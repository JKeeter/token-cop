import importlib.util
import os
import pathlib
import unittest
from unittest import mock

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "lambda" / "token_meter.py"
_spec = importlib.util.spec_from_file_location("token_meter", _PATH)
token_meter = importlib.util.module_from_spec(_spec)
with mock.patch.dict(os.environ, {
    "TABLE_NAME": "test-table",
    "DENY_POLICY_ARN": "arn:aws:iam::000000000000:policy/test",
    "AWS_DEFAULT_REGION": "us-east-1",
}):
    _spec.loader.exec_module(token_meter)


class TokenMeterPricingTests(unittest.TestCase):
    def test_normalizes_every_cross_region_prefix(self):
        for model_id in (
            "anthropic.claude-opus-4-7",
            "us.anthropic.claude-opus-4-7",
            "eu.anthropic.claude-opus-4-7",
            "jp.anthropic.claude-opus-4-7",
            "au.anthropic.claude-opus-4-7",
            "apac.anthropic.claude-opus-4-7",
            "global.anthropic.claude-opus-4-7",
        ):
            with self.subTest(model_id=model_id):
                self.assertEqual(token_meter._normalize_model(model_id), "claude-opus-4.7")

    def test_dated_ids_match_pricing_keys(self):
        self.assertEqual(
            token_meter._normalize_model("us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
            "claude-sonnet-4.5",
        )
        self.assertEqual(
            token_meter._normalize_model("global.anthropic.claude-sonnet-4-20250514-v1:0"),
            "claude-sonnet-4",
        )

    def test_claude_5_era_models_are_priced(self):
        cases = (
            ("global.anthropic.claude-fable-5", 60.00),
            ("us.anthropic.claude-fable-5-1", 60.00),
            ("us.anthropic.claude-opus-4-8", 30.00),
            ("global.anthropic.claude-opus-5", 30.00),
            ("us.anthropic.claude-opus-5-5", 24.00),
            ("us.anthropic.claude-sonnet-5-5", 12.00),
        )
        for model_id, expected in cases:
            with self.subTest(model_id=model_id):
                self.assertAlmostEqual(
                    token_meter._cost(model_id, 1_000_000, 1_000_000), expected
                )


    def test_cache_tokens_are_priced(self):
        # Fable 5.1: cache read $0.25/M, cache write $12.50/M
        self.assertAlmostEqual(
            token_meter._cost("us.anthropic.claude-fable-5-1", 0, 0, 1_000_000, 1_000_000),
            12.75,
        )


class TokenMeterRecordTests(unittest.TestCase):
    def _process(self, record):
        ddb = mock.MagicMock()
        ddb.update_item.return_value = {"Attributes": {"cost_usd": 0}}
        with mock.patch.object(token_meter, "ddb", ddb), \
                mock.patch.object(token_meter, "_get_budget", return_value=1e9):
            token_meter._process_record(record)
        return ddb.update_item.call_args.kwargs["ExpressionAttributeValues"]

    def test_reads_nested_schema_with_cache_tokens(self):
        values = self._process({
            "identity": {"arn": "arn:aws:iam::000000000000:user/alice"},
            "modelId": "global.anthropic.claude-opus-4-8",
            "timestamp": "2026-09-15T00:00:00Z",
            "input": {
                "inputTokenCount": 1_000_000,
                "cacheReadInputTokenCount": 2_000_000,
                "cacheWriteInputTokenCount": 1_000_000,
            },
            "output": {"outputTokenCount": 1_000_000},
        })
        self.assertEqual(values[":i"], 1_000_000)
        self.assertEqual(values[":o"], 1_000_000)
        self.assertEqual(values[":cr"], 2_000_000)
        self.assertEqual(values[":cw"], 1_000_000)
        # 5 + 25 + 2*0.50 + 6.25
        self.assertAlmostEqual(float(values[":c"]), 37.25)

    def test_cache_only_call_is_still_metered(self):
        values = self._process({
            "identity": {"arn": "arn:aws:iam::000000000000:user/alice"},
            "modelId": "us.anthropic.claude-fable-5-1",
            "timestamp": "2026-09-15T00:00:00Z",
            "input": {"inputTokenCount": 0, "cacheReadInputTokenCount": 4_000_000},
            "output": {"outputTokenCount": 0},
        })
        self.assertAlmostEqual(float(values[":c"]), 1.00)

    def test_legacy_top_level_counts_still_work(self):
        values = self._process({
            "identity": {"arn": "arn:aws:iam::000000000000:user/alice"},
            "modelId": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "timestamp": "2026-09-15T00:00:00Z",
            "inputTokenCount": 1_000_000,
            "outputTokenCount": 1_000_000,
        })
        self.assertAlmostEqual(float(values[":c"]), 18.00)
        self.assertEqual(values[":cr"], 0)


if __name__ == "__main__":
    unittest.main()
