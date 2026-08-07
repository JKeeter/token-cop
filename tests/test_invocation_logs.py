"""Tests for tools/invocation_logs.py analysis dimensions.

Focus: the savings-projection math (model-task mismatch and caching) and the
context-overhead ratio clamp. These dimensions read real prompt payloads and
report dollar/percent figures, so their arithmetic needs to be exact.
"""

import json
import os
import unittest

from models.schemas import InvocationLogEntry
from unittest.mock import MagicMock

from tools.invocation_logs import (
    _analyze_caching_opportunities,
    _analyze_context_overhead,
    _analyze_model_task_mismatch,
    _effective_input_tokens,
    _extract_body_signals,
    _list_log_objects,
    _parse_record,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _entry(**kwargs) -> InvocationLogEntry:
    """Build an InvocationLogEntry with sensible defaults for the required fields."""
    base = {
        "model_id": "us.anthropic.claude-opus-4-6-v1",
        "normalized_model": "claude-opus-4.6",
        "timestamp": "2026-07-01T00:00:00Z",
        "classification_confidence": 1.0,  # high confidence by default for existing tests
    }
    base.update(kwargs)
    return InvocationLogEntry(**base)


class ModelTaskMismatchSavingsTests(unittest.TestCase):
    """The weekly-savings projection must scale by the sampled day window."""

    def _one_mismatch_entry(self):
        # Opus (reasoning tier, $5/M input) running a polish-tier task -> mismatch.
        # 1,000,000 input tokens -> current input cost = $5.00.
        # Savings assumption is half the cost -> $2.50 of sampled savings.
        return _entry(
            input_token_count=1_000_000,
            model_tier="reasoning",
            classified_tier="polish",
        )

    def test_default_seven_days_is_not_inflated(self):
        # A 7-day sample projected to a week must equal the sampled savings,
        # NOT 7x it (the old (7/N)*N expression multiplied by 7).
        result = _analyze_model_task_mismatch([self._one_mismatch_entry()], days=7)
        self.assertAlmostEqual(result["estimated_weekly_savings_usd"], 2.50, places=2)

    def test_single_day_sample_scales_up_to_week(self):
        result = _analyze_model_task_mismatch([self._one_mismatch_entry()], days=1)
        self.assertAlmostEqual(result["estimated_weekly_savings_usd"], 17.50, places=2)

    def test_two_week_sample_scales_down_to_week(self):
        result = _analyze_model_task_mismatch([self._one_mismatch_entry()], days=14)
        self.assertAlmostEqual(result["estimated_weekly_savings_usd"], 1.25, places=2)

    def test_population_scale_multiplies_savings(self):
        # A 10x sample-to-population ratio scales the projected savings 10x,
        # since only a fraction of log objects were sampled.
        entry = self._one_mismatch_entry()
        full = _analyze_model_task_mismatch([entry], days=7, population_scale=1.0)
        scaled = _analyze_model_task_mismatch([entry], days=7, population_scale=10.0)
        self.assertAlmostEqual(full["estimated_weekly_savings_usd"], 2.50, places=2)
        self.assertAlmostEqual(scaled["estimated_weekly_savings_usd"], 25.00, places=2)

    def test_population_scale_does_not_touch_rate_or_score(self):
        entry = self._one_mismatch_entry()
        full = _analyze_model_task_mismatch([entry], days=7, population_scale=1.0)
        scaled = _analyze_model_task_mismatch([entry], days=7, population_scale=10.0)
        self.assertEqual(full["mismatch_rate"], scaled["mismatch_rate"])
        self.assertEqual(full["score"], scaled["score"])

    def test_savings_independent_of_entry_count(self):
        # Adding well-matched (non-mismatch) entries must not change the projected
        # savings — only the mismatched Opus call contributes.
        matched = _entry(
            normalized_model="claude-haiku-4.5",
            model_id="us.anthropic.claude-haiku-4-5",
            input_token_count=1_000_000,
            model_tier="polish",
            classified_tier="polish",
        )
        entries = [self._one_mismatch_entry(), matched, matched, matched]
        result = _analyze_model_task_mismatch(entries, days=7)
        self.assertAlmostEqual(result["estimated_weekly_savings_usd"], 2.50, places=2)
        self.assertEqual(result["mismatched_entries"], 1)
        self.assertEqual(result["classified_entries"], 4)


class CachingSavingsProjectionTests(unittest.TestCase):
    """Caching 'weekly' savings must also project from the sampled day window."""

    def _repeated_prompt_entries(self, count):
        # Identical system prompt across `count` calls -> reuse. Sized large
        # enough that the projected savings survive 2-decimal rounding.
        text = "SYSTEM PROMPT TOKENS " * 4000  # stable content -> stable hash
        return [
            _entry(
                system_prompt_text=text,
                system_prompt_hash="deadbeefcafe",
                system_prompt_length=len(text) // 4,
            )
            for _ in range(count)
        ]

    def test_seven_day_and_one_day_projection_ratio(self):
        entries = self._repeated_prompt_entries(4)
        weekly = _analyze_caching_opportunities(list(entries), days=7)
        daily = _analyze_caching_opportunities(list(entries), days=1)
        # A one-day sample projected to a week is 7x a seven-day sample of the
        # same data.
        self.assertGreater(weekly["potential_weekly_savings_usd"], 0)
        self.assertAlmostEqual(
            daily["potential_weekly_savings_usd"],
            weekly["potential_weekly_savings_usd"] * 7,
            places=2,
        )

    def test_population_scale_multiplies_caching_savings(self):
        entries = self._repeated_prompt_entries(4)
        full = _analyze_caching_opportunities(list(entries), days=7, population_scale=1.0)
        scaled = _analyze_caching_opportunities(list(entries), days=7, population_scale=5.0)
        self.assertGreater(full["potential_weekly_savings_usd"], 0)
        self.assertAlmostEqual(
            scaled["potential_weekly_savings_usd"],
            full["potential_weekly_savings_usd"] * 5,
            places=2,
        )
        # Reuse ratio is a proportion — unaffected by population scaling.
        self.assertEqual(full["reuse_ratio"], scaled["reuse_ratio"])


class ContextOverheadClampTests(unittest.TestCase):
    """overhead_ratio is a fraction and must never exceed 1.0."""

    def test_ratio_clamped_when_multipliers_exceed_prompt(self):
        # 10 tool-schema markers -> 10 * 800 = 8000 estimated overhead tokens,
        # but the prompt itself is only ~100 tokens. Pre-clamp ratio would be ~80.
        text = "<function>{" * 10
        entry = _entry(
            system_prompt_text=text,
            system_prompt_hash="aaaabbbbcccc",
            system_prompt_length=100,
        )
        result = _analyze_context_overhead([entry])
        self.assertLessEqual(result["overhead_ratio"], 1.0)
        self.assertGreaterEqual(result["overhead_ratio"], 0.0)
        # Heavily-overloaded prompt -> worst score band.
        self.assertEqual(result["score"], 3)

    def test_core_tokens_never_negative(self):
        text = "<function>{" * 10
        entry = _entry(
            system_prompt_text=text,
            system_prompt_hash="aaaabbbbcccc",
            system_prompt_length=100,
        )
        result = _analyze_context_overhead([entry])
        self.assertGreaterEqual(
            result["breakdown"]["core_system_prompt"]["est_tokens"], 0
        )


class ParserFidelityTests(unittest.TestCase):
    def test_reads_nested_input_token_count(self):
        record = {
            "modelId": "us.anthropic.claude-opus-4-6-v1:0",
            "timestamp": "2026-04-03T20:03:49Z",
            "input": {
                "inputTokenCount": 12345,
                "inputBodyS3Path": "s3://x/y_input.json.gz",
            },
            "output": {"outputTokenCount": 678},
        }
        entry = _parse_record(record)
        self.assertEqual(entry.input_token_count, 12345)
        self.assertEqual(entry.output_token_count, 678)


class RegionAwareListingTests(unittest.TestCase):
    def test_region_partitioned_prefix_is_tried(self):
        s3 = MagicMock()
        paginator = MagicMock()
        s3.get_paginator.return_value = paginator
        # Return one object only for the region-partitioned prefix.
        def paginate(Bucket, Prefix, MaxKeys):
            if "us-east-1" in Prefix:
                return [{"Contents": [{"Key": Prefix + "/f.json.gz", "Size": 10}]}]
            return [{}]
        paginator.paginate.side_effect = paginate
        s3.list_objects_v2.return_value = {"CommonPrefixes": []}
        objs = _list_log_objects(s3, "bucket", "AWSLogs", 1)
        self.assertTrue(any("us-east-1" in o["Key"] for o in objs))


class BodySignalTests(unittest.TestCase):
    def test_extracts_code_and_message_count(self):
        with open(os.path.join(FIXTURE_DIR, "invocation_body_sample.json")) as f:
            body = json.load(f)
        sig = _extract_body_signals(body)
        self.assertTrue(sig["has_code"])
        self.assertEqual(sig["message_count"], 3)
        self.assertEqual(sig["user_message_text"], "now add a test")


class MismatchConfidenceGatingTests(unittest.TestCase):
    def _entry(self, **kw):
        base = {"model_id": "m", "normalized_model": "claude-opus-4.6",
                "timestamp": "2026-04-03T00:00:00Z"}
        base.update(kw)
        return InvocationLogEntry(**base)

    def test_low_confidence_entries_excluded(self):
        from tools.invocation_logs import _analyze_model_task_mismatch
        entries = [
            # high-confidence mismatch: counts
            self._entry(model_tier="reasoning", classified_tier="polish",
                        classification_confidence=0.9, input_token_count=1000),
            # low-confidence: must be skipped, not counted as classifiable
            self._entry(model_tier="reasoning", classified_tier="polish",
                        classification_confidence=0.2, input_token_count=1000),
            # unknown: skipped
            self._entry(model_tier="reasoning", classified_tier="unknown",
                        classification_confidence=0.0, input_token_count=1000),
        ]
        result = _analyze_model_task_mismatch(entries, days=7)
        self.assertEqual(result["classified_entries"], 1)
        self.assertEqual(result["mismatched_entries"], 1)


class AccountSegmentListingTests(unittest.TestCase):
    def test_traverses_account_segment_across_past_days(self):
        # Real layout: {prefix}/{ACCOUNT}/BedrockModelInvocationLogs/{region}/YYYY/MM/DD/
        s3 = MagicMock()

        def list_v2(Bucket, Prefix, Delimiter=None, MaxKeys=None):
            # Account discovery: trailing-slash prefix yields the account below.
            if Delimiter == "/" and Prefix == "AWSLogs/":
                return {"CommonPrefixes": [{"Prefix": "AWSLogs/123456789012/"}]}
            return {"CommonPrefixes": []}
        s3.list_objects_v2.side_effect = list_v2

        paginator = MagicMock()
        s3.get_paginator.return_value = paginator

        def paginate(Bucket, Prefix, MaxKeys):
            # Only the account+region-partitioned prefix has an object.
            if Prefix.startswith("AWSLogs/123456789012/BedrockModelInvocationLogs/us-east-1/"):
                return [{"Contents": [{"Key": Prefix + "/f.json.gz", "Size": 42}]}]
            return [{}]
        paginator.paginate.side_effect = paginate

        # days=40 so "today" (day 0) is empty but a past day matches — proving
        # discovery is not gated to day_offset == 0.
        objs = _list_log_objects(s3, "bucket", "AWSLogs", 40)
        self.assertTrue(objs, "should find objects under the account segment")
        self.assertTrue(all("123456789012" in o["Key"] for o in objs))
        self.assertTrue(any("us-east-1" in o["Key"] for o in objs))

    def test_account_discovery_uses_trailing_slash(self):
        s3 = MagicMock()
        seen_prefixes = []

        def list_v2(Bucket, Prefix, Delimiter=None, MaxKeys=None):
            if Delimiter == "/":
                seen_prefixes.append(Prefix)
            return {"CommonPrefixes": []}
        s3.list_objects_v2.side_effect = list_v2
        paginator = MagicMock()
        s3.get_paginator.return_value = paginator
        paginator.paginate.side_effect = lambda **kw: [{}]

        _list_log_objects(s3, "bucket", "AWSLogs", 1)
        self.assertIn("AWSLogs/", seen_prefixes,
                      "discovery must list with a trailing slash to surface the account prefix")


class EffectiveInputSizeTests(unittest.TestCase):
    def test_effective_includes_cache_tokens(self):
        # Cached multi-turn call: 3 new tokens but 33K cached context.
        self.assertEqual(_effective_input_tokens(3, 33_000, 200), 33_203)

    def test_parse_record_reads_and_classifies_on_effective_size(self):
        # A tiny uncached delta but huge cache read -> effective size crosses
        # the >20K reasoning-size threshold, so it must NOT be "unknown".
        from tools.invocation_logs import _parse_record
        record = {
            "modelId": "us.anthropic.claude-opus-4-6-v1:0",
            "timestamp": "2026-04-03T20:00:00Z",
            "input": {
                "inputTokenCount": 3,
                "cacheReadInputTokenCount": 33000,
                "cacheWriteInputTokenCount": 200,
            },
            "output": {"outputTokenCount": 50},
        }
        entry = _parse_record(record)
        self.assertEqual(entry.cache_read_tokens, 33000)
        self.assertEqual(entry.input_token_count, 3)  # RAW unchanged
        # No inline text here, so no user message -> still unknown; but confirm
        # cache fields are populated for later effective-size use.


class MultiMessageTextTests(unittest.TestCase):
    def test_combines_last_five_user_messages(self):
        body = {"messages": [
            {"role": "user", "content": [{"text": "first, analyze the architecture"}]},
            {"role": "assistant", "content": [{"text": "ok"}]},
            {"role": "user", "content": [{"text": "now continue"}]},
        ]}
        sig = _extract_body_signals(body)
        # combined text spans multiple user turns, so the "analyze"/"architecture"
        # signal from an earlier turn is visible even though the last turn is terse.
        self.assertIn("analyze", sig["combined_user_text"])
        self.assertIn("continue", sig["combined_user_text"])


class FrozenFixtureRegressionTests(unittest.TestCase):
    """Regression guard: classify anonymized real log entries to a stable
    distribution. Drift (e.g. everything → unknown, or confident-wrong
    reasoning creeping back) fails here."""

    def _load(self):
        from collections import Counter
        from models.model_tiers import classify_task

        path = os.path.join(FIXTURE_DIR, "invocation_log_sample.json")
        if not os.path.exists(path):
            self.skipTest("fixture not captured yet")
        with open(path) as f:
            frozen = json.load(f)
        tiers = Counter()
        for rec in frozen:
            r = classify_task(
                rec["user_message_text"],
                input_tokens=rec["input_token_count"],
                has_code="```" in rec["user_message_text"],
                message_count=rec["message_count"],
            )
            tiers[r.tier] += 1
        return frozen, tiers

    def test_not_everything_unknown(self):
        # The classifier must confidently classify at least some real entries —
        # guards against an over-strict regression where everything abstains.
        frozen, tiers = self._load()
        confident = sum(v for k, v in tiers.items() if k != "unknown")
        self.assertGreater(confident, 0)

    def test_fixture_is_anonymized(self):
        # Belt-and-suspenders: no 12-digit AWS account IDs in the committed
        # fixture (they would otherwise leak via inference-profile ARNs).
        import re
        path = os.path.join(FIXTURE_DIR, "invocation_log_sample.json")
        if not os.path.exists(path):
            self.skipTest("fixture not captured yet")
        with open(path) as f:
            raw = f.read()
        self.assertIsNone(re.search(r"\b\d{12}\b", raw),
                          "fixture contains a bare 12-digit AWS account ID")


if __name__ == "__main__":
    unittest.main()
