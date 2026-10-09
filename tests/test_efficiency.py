"""Tests for models/efficiency.py — cache-aware token ratios.

Provider ``input_tokens`` (CloudWatch InputTokenCount, invocation-log
inputTokenCount) is only the UNCACHED delta. Any ratio that divides cache reads
by it alone can exceed 100% by orders of magnitude on cached traffic.
"""
import unittest

from models.efficiency import cache_hit_ratio, effective_input_tokens


class EffectiveInputTests(unittest.TestCase):
    def test_sums_uncached_and_cached_input(self):
        self.assertEqual(effective_input_tokens(100, 50, 25), 175)

    def test_defaults_to_uncached_only(self):
        self.assertEqual(effective_input_tokens(100), 100)


class CacheHitRatioTests(unittest.TestCase):
    def test_heavy_cache_traffic_stays_below_one(self):
        # The live audit reported "7942% cache hit rate" from exactly this shape.
        ratio = cache_hit_ratio(100, 7942, 0)
        self.assertAlmostEqual(ratio, 7942 / 8042, places=6)
        self.assertLessEqual(ratio, 1.0)

    def test_cache_writes_count_as_input_not_hits(self):
        self.assertAlmostEqual(cache_hit_ratio(100, 100, 100), 1 / 3, places=6)

    def test_zero_safe(self):
        self.assertEqual(cache_hit_ratio(0, 0, 0), 0.0)

    def test_no_cache_is_zero(self):
        self.assertEqual(cache_hit_ratio(1000, 0, 0), 0.0)


if __name__ == "__main__":
    unittest.main()
