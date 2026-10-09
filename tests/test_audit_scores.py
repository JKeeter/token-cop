"""Tests for tools/audit.py dimension scorers."""
import os
import unittest

for _k in ("OPENROUTER_API_KEY", "OPENAI_ADMIN_API_KEY", "ANTHROPIC_ADMIN_API_KEY"):
    os.environ.setdefault(_k, "test-placeholder")

from tools.audit import _score_cache_utilization  # noqa: E402


class CacheUtilizationScoreTests(unittest.TestCase):
    def test_heavy_cache_traffic_reports_a_real_percentage(self):
        score, detail = _score_cache_utilization(100, 7942, 0)
        self.assertEqual(score, 10)
        self.assertIn("99% cache hit rate", detail)
        self.assertNotIn("7942", detail)

    def test_cache_writes_dilute_the_hit_rate(self):
        # 100 uncached + 100 written + 100 read: one third of input came from cache.
        score, detail = _score_cache_utilization(100, 100, 100)
        self.assertIn("33% cache hit rate", detail)
        self.assertEqual(score, 9)  # 8 + int((33 - 20) / 10)

    def test_no_cache_is_the_floor(self):
        score, detail = _score_cache_utilization(1000, 0, 0)
        self.assertEqual(score, 1)
        self.assertIn("Only 0.0% cache hit rate", detail)

    def test_no_input_data(self):
        self.assertEqual(_score_cache_utilization(0, 0, 0), (5, "No input token data"))


if __name__ == "__main__":
    unittest.main()
