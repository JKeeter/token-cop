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


class ConfidenceAndUnknownTests(unittest.TestCase):
    def test_no_signal_returns_unknown(self):
        # "ok do it" / "yes continue" -> no keywords -> unknown, not execution.
        result = classify_task("yes, continue")
        self.assertEqual(result.tier, "unknown")
        self.assertEqual(result.confidence, 0.0)

    def test_low_margin_flags_low_confidence(self):
        # One weak reasoning word vs one weak polish word -> near tie.
        result = classify_task("analyze and summarize")
        self.assertLess(result.confidence, 0.5)
        self.assertIn(result.tier, ("reasoning", "polish"))

    def test_signals_dict_exposes_per_tier_scores(self):
        result = classify_task("implement the feature")
        self.assertIn("execution", result.signals)
        self.assertGreater(result.signals["execution"], 0.0)


class StructuralSignalTests(unittest.TestCase):
    def test_huge_input_overrides_polish_keywords(self):
        # "summarize" is polish, but a 90K-token prompt is not polish work.
        result = classify_task("summarize", input_tokens=90_000)
        self.assertNotEqual(result.tier, "polish")

    def test_code_presence_favors_execution(self):
        result = classify_task("take a look", has_code=True, input_tokens=25_000)
        self.assertEqual(result.tier, "execution")

    def test_structural_absent_is_text_only(self):
        # No structural kwargs -> identical to a pure-text call.
        a = classify_task("implement the parser")
        b = classify_task("implement the parser", input_tokens=0,
                           has_code=False, message_count=0)
        self.assertEqual(a.tier, b.tier)
        self.assertEqual(a.confidence, b.confidence)
