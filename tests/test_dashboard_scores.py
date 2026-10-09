"""Tests for dashboard/data.py composite scores."""
import unittest

from dashboard.data import compute_smart_token_score


def _data(input_tokens, output_tokens, cache_read=0):
    return {
        "totals": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "total_requests": 0,
        },
        "combined_models": [],
    }


class SmartTokenScoreTests(unittest.TestCase):
    def test_no_cache_baseline(self):
        self.assertEqual(compute_smart_token_score(_data(100, 50)), 50.0)

    def test_cache_bonus_is_bounded_on_heavy_cache_traffic(self):
        # output_ratio 0.5; cache ratio 7942/8042 = 0.9876 -> bonus 1.4938 -> 74.7.
        # The old uncached-denominator formula gave a 40x bonus and pinned at 100.
        self.assertEqual(compute_smart_token_score(_data(100, 50, cache_read=7942)), 74.7)

    def test_zero_input(self):
        self.assertEqual(compute_smart_token_score(_data(0, 0)), 0.0)


if __name__ == "__main__":
    unittest.main()
