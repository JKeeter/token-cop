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


import json
from tools.model_router import _recommend_model_impl


class RecommendModelIntegrationTests(unittest.TestCase):
    def test_recommend_surfaces_confidence(self):
        out = json.loads(_recommend_model_impl("implement the parser", ""))
        self.assertEqual(out["recommended_tier"], "execution")
        self.assertIn("confidence", out)

    def test_recommend_unknown_defaults_gracefully(self):
        # No-signal text -> unknown -> recommend_model must still return a tier
        # (fall back to execution for the advisory, but report low confidence).
        out = json.loads(_recommend_model_impl("yes continue", ""))
        self.assertIn(out["recommended_tier"], ("execution", "unknown"))
        self.assertEqual(out["confidence"], 0.0)


class MessageCountCorroborationTests(unittest.TestCase):
    def test_long_convo_no_text_signal_is_not_reasoning(self):
        # Deep in a long conversation but the turn itself has no task signal.
        # message_count alone must NOT manufacture a confident reasoning verdict.
        result = classify_task("install the tavily skills", message_count=25)
        self.assertNotEqual(result.tier, "reasoning")

    def test_long_convo_with_reasoning_text_stays_reasoning(self):
        # Corroborated: a reasoning text signal is present, so message_count
        # may reinforce reasoning.
        result = classify_task("analyze the trade-off here", message_count=25)
        self.assertEqual(result.tier, "reasoning")

    def test_large_input_alone_does_not_promote_reasoning(self):
        # Large input is the norm on cache-heavy traffic; without a text signal
        # it must NOT manufacture a reasoning verdict. (Reverses the earlier
        # assumption that input size self-corroborates — see Task 3c.)
        result = classify_task("continue", message_count=25, input_tokens=60_000)
        self.assertEqual(result.tier, "unknown")
        self.assertEqual(result.confidence, 0.0)

    def test_message_count_alone_yields_unknown(self):
        # No text, no size — only a long conversation. Honest unknown, not
        # confident reasoning.
        result = classify_task("ok thanks", message_count=25)
        self.assertEqual(result.tier, "unknown")
        self.assertEqual(result.confidence, 0.0)


class SizeCorroborationTests(unittest.TestCase):
    def test_large_input_no_text_is_unknown(self):
        # "Tool loaded." with 135K cached context -> no text signal -> unknown,
        # NOT confident reasoning.
        result = classify_task("Tool loaded.", input_tokens=135_000)
        self.assertEqual(result.tier, "unknown")

    def test_large_input_with_reasoning_text_amplifies(self):
        # Text signal present -> size legitimately reinforces reasoning.
        result = classify_task("analyze this", input_tokens=60_000)
        self.assertEqual(result.tier, "reasoning")

    def test_large_input_with_execution_text_amplifies(self):
        result = classify_task("implement this", input_tokens=25_000)
        self.assertEqual(result.tier, "execution")

    def test_huge_input_still_suppresses_polish(self):
        # polish suppression stays unconditional: "summarize" + 90K -> not polish.
        result = classify_task("summarize", input_tokens=90_000)
        self.assertNotEqual(result.tier, "polish")

    def test_has_code_still_works_without_text(self):
        # has_code is a standalone signal, not gated by corroboration.
        result = classify_task("take a look", has_code=True, input_tokens=25_000)
        self.assertEqual(result.tier, "execution")
