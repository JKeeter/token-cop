import unittest

from models.normalization import normalize_model_name
from models.pricing import estimate_cost


class PricingNormalizationTests(unittest.TestCase):
    def test_bedrock_opus_47_uses_current_pricing(self):
        for model_id in (
            "anthropic.claude-opus-4-7",
            "us.anthropic.claude-opus-4-7",
            "eu.anthropic.claude-opus-4-7",
            "jp.anthropic.claude-opus-4-7",
            "au.anthropic.claude-opus-4-7",
            "global.anthropic.claude-opus-4-7",
        ):
            with self.subTest(model_id=model_id):
                model = normalize_model_name(model_id)

                self.assertEqual(model, "claude-opus-4.7")
                self.assertEqual(
                    estimate_cost(
                        model,
                        input_tokens=1_000_000,
                        output_tokens=1_000_000,
                        cache_read_tokens=1_000_000,
                        cache_write_tokens=1_000_000,
                    ),
                    36.75,
                )

    def test_bedrock_opus_46_uses_current_pricing(self):
        model = normalize_model_name("us.anthropic.claude-opus-4-6-v1")

        self.assertEqual(model, "claude-opus-4.6")
        self.assertEqual(
            estimate_cost(
                model,
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                cache_read_tokens=1_000_000,
                cache_write_tokens=1_000_000,
            ),
            36.75,
        )

    def test_haiku_45_uses_current_pricing(self):
        model = normalize_model_name("global.anthropic.claude-haiku-4-5-20251001-v1:0")

        self.assertEqual(model, "claude-haiku-4.5")
        self.assertEqual(
            estimate_cost(
                model,
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                cache_read_tokens=1_000_000,
                cache_write_tokens=1_000_000,
            ),
            7.35,
        )


if __name__ == "__main__":
    unittest.main()
