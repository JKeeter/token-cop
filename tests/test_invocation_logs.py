"""Tests for tools/invocation_logs.py analysis dimensions.

Focus: the savings-projection math (model-task mismatch and caching) and the
context-overhead ratio clamp. These dimensions read real prompt payloads and
report dollar/percent figures, so their arithmetic needs to be exact.
"""

import unittest

from models.schemas import InvocationLogEntry
from tools.invocation_logs import (
    _analyze_caching_opportunities,
    _analyze_context_overhead,
    _analyze_model_task_mismatch,
)


def _entry(**kwargs) -> InvocationLogEntry:
    """Build an InvocationLogEntry with sensible defaults for the required fields."""
    base = {
        "model_id": "us.anthropic.claude-opus-4-6-v1",
        "normalized_model": "claude-opus-4.6",
        "timestamp": "2026-07-01T00:00:00Z",
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


if __name__ == "__main__":
    unittest.main()
