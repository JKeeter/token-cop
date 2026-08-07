import unittest

from models.model_tiers import TierResult, classify_task


class WeightedTextScoringTests(unittest.TestCase):
    def test_strong_reasoning_phrase_beats_weak_polish_words(self):
        # "architect" is a strong (2.0) reasoning signal; a lone weak polish
        # word must not outweigh it.
        result = classify_task("architect the system and format the output")
        self.assertIsInstance(result, TierResult)
        self.assertEqual(result.tier, "reasoning")
        self.assertGreater(result.confidence, 0.0)

    def test_clear_polish_task(self):
        result = classify_task("fix typo and proofread this paragraph")
        self.assertEqual(result.tier, "polish")

    def test_clear_execution_task(self):
        result = classify_task("implement and refactor the parser module")
        self.assertEqual(result.tier, "execution")
