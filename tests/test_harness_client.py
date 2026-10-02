"""Tests for agent/harness_client.py — InvokeHarness stream folding + request shape.

The stream consumer has to cope with several messages per invocation
(assistant → tool_use, user → tool_result, assistant → end_turn) and one
``metadata`` event per model call. These tests pin down: which text becomes
the answer, that usage counters are summed (not overwritten), that tool
names are recorded, and that error events raise.
"""

import unittest

from agent.harness_client import (
    DEFAULT_ALLOWED_TOOLS,
    HarnessResult,
    _consume_stream,
    estimate_cost_usd,
    invoke,
)


# --- synthetic event builders --------------------------------------------

def _start(role):
    return {"messageStart": {"role": role}}


def _text(t, idx=0):
    return {"contentBlockDelta": {"contentBlockIndex": idx, "delta": {"text": t}}}


def _tool_use(name, idx=1):
    return {"contentBlockStart": {"contentBlockIndex": idx,
                                  "start": {"toolUse": {"toolUseId": "tu-1", "name": name}}}}


def _tool_result(idx=0):
    return {"contentBlockDelta": {"contentBlockIndex": idx,
                                  "delta": {"toolResult": {"content": [{"text": "{\"usd\": 5}"}]}}}}


def _stop(reason):
    return {"messageStop": {"stopReason": reason}}


def _meta(inp, out, latency=100, **extra):
    usage = {"inputTokens": inp, "outputTokens": out, "totalTokens": inp + out}
    usage.update(extra)
    return {"metadata": {"usage": usage, "metrics": {"latencyMs": latency}}}


class ConsumeStreamTests(unittest.TestCase):

    def test_simple_end_turn(self):
        events = [
            _start("assistant"), _text("Hello "), _text("world"),
            _stop("end_turn"), _meta(10, 5, latency=42),
        ]
        res = _consume_stream(events)
        self.assertEqual(res.text, "Hello world")
        self.assertEqual(res.transcript, "Hello world")
        self.assertEqual(res.stop_reason, "end_turn")
        self.assertEqual(res.usage["inputTokens"], 10)
        self.assertEqual(res.usage["outputTokens"], 5)
        self.assertEqual(res.usage["totalTokens"], 15)
        self.assertEqual(res.latency_ms, 42)
        self.assertEqual(res.total_latency_ms, 42)
        self.assertEqual(res.model_calls, 1)
        self.assertEqual(res.tool_calls, [])

    def test_tool_turn_keeps_last_assistant_text_and_sums_usage(self):
        events = [
            _start("assistant"), _text("Let me check."),
            _tool_use("token-cop-tools___bedrock_usage"),
            _stop("tool_use"), _meta(100, 20, latency=300),
            _start("user"), _tool_result(), _stop("tool_result"),
            _start("assistant"), _text("You spent $5."),
            _stop("end_turn"), _meta(200, 30, latency=500),
        ]
        res = _consume_stream(events)
        self.assertEqual(res.text, "You spent $5.")
        self.assertIn("Let me check.", res.transcript)
        self.assertIn("You spent $5.", res.transcript)
        self.assertNotIn("usd", res.text)  # tool result never echoed
        self.assertEqual(res.tool_calls, ["bedrock_usage"])
        self.assertEqual(res.usage["inputTokens"], 300)
        self.assertEqual(res.usage["outputTokens"], 50)
        self.assertEqual(res.usage["totalTokens"], 350)
        self.assertEqual(res.model_calls, 2)
        self.assertEqual(res.latency_ms, 500)
        self.assertEqual(res.total_latency_ms, 800)
        self.assertEqual(res.stop_reason, "end_turn")
        self.assertIn("calls=2", res.usage_footer())
        self.assertIn("tools=bedrock_usage", res.usage_footer())

    def test_final_assistant_message_without_text_falls_back_to_earlier_text(self):
        # max_iterations hit right after a tool_use with no closing prose:
        # the answer should still show the narration we did get.
        events = [
            _start("assistant"), _text("Checking Bedrock..."),
            _tool_use("token-cop-tools___bedrock_usage"),
            _stop("tool_use"), _meta(50, 10),
            _start("user"), _tool_result(), _stop("tool_result"),
            _start("assistant"), _tool_use("token-cop-tools___check_budget"),
            _stop("max_iterations_exceeded"), _meta(60, 5),
        ]
        res = _consume_stream(events)
        self.assertEqual(res.text, "Checking Bedrock...")
        self.assertEqual(res.stop_reason, "max_iterations_exceeded")
        self.assertEqual(res.tool_calls, ["bedrock_usage", "check_budget"])

    def test_hard_cap_stop_reason(self):
        events = [
            _start("assistant"), _text("Partial"),
            _stop("max_output_tokens_exceeded"), _meta(20, 300),
        ]
        res = _consume_stream(events)
        self.assertEqual(res.text, "Partial")
        self.assertEqual(res.stop_reason, "max_output_tokens_exceeded")
        self.assertIn("stop=max_output_tokens_exceeded", res.usage_footer())

    def test_cache_counters_summed(self):
        events = [
            _start("assistant"), _stop("tool_use"),
            _meta(10, 1, cacheReadInputTokens=1000, cacheWriteInputTokens=0),
            _start("assistant"), _text("done"), _stop("end_turn"),
            _meta(10, 1, cacheReadInputTokens=2000, cacheWriteInputTokens=5),
        ]
        res = _consume_stream(events)
        self.assertEqual(res.usage["cacheReadInputTokens"], 3000)
        self.assertEqual(res.usage["cacheWriteInputTokens"], 5)
        self.assertIn("cache_read=3,000", res.usage_footer())

    def test_on_text_receives_every_assistant_delta(self):
        seen = []
        events = [
            _start("assistant"), _text("a"), _stop("tool_use"),
            _start("user"), _tool_result(), _stop("tool_result"),
            _start("assistant"), _text("b"), _stop("end_turn"),
        ]
        _consume_stream(events, on_text=seen.append)
        self.assertEqual(seen, ["a", "b"])

    def test_runtime_client_error_raises(self):
        events = [_start("assistant"), {"runtimeClientError": {"message": "boom"}}]
        with self.assertRaises(RuntimeError) as ctx:
            _consume_stream(events)
        self.assertIn("boom", str(ctx.exception))

    def test_validation_exception_raises_value_error(self):
        events = [{"validationException": {"message": "bad arn"}}]
        with self.assertRaises(ValueError) as ctx:
            _consume_stream(events)
        self.assertIn("bad arn", str(ctx.exception))

    def test_internal_server_exception_raises(self):
        events = [{"internalServerException": {"message": "oops"}}]
        with self.assertRaises(RuntimeError):
            _consume_stream(events)

    def test_empty_stream(self):
        res = _consume_stream([])
        self.assertEqual(res.text, "")
        self.assertEqual(res.transcript, "")
        self.assertIsNone(res.stop_reason)
        self.assertEqual(res.usage, {})
        self.assertEqual(res.usage_footer(), "harness usage: in=0 out=0")


class _FakeClient:
    def __init__(self, events):
        self.events = events
        self.kwargs = None

    def invoke_harness(self, **kw):
        self.kwargs = kw
        return {"stream": iter(self.events)}


class InvokeTests(unittest.TestCase):

    EVENTS = [_start("assistant"), _text("ok"), _stop("end_turn"), _meta(3, 1)]

    def test_request_shape_defaults(self):
        client = _FakeClient(self.EVENTS)
        res = invoke("hi", harness_arn="arn:test", system_prompt=[{"text": "x"}], client=client)
        kw = client.kwargs
        self.assertEqual(kw["harnessArn"], "arn:test")
        self.assertGreaterEqual(len(kw["runtimeSessionId"]), 33)
        self.assertEqual(kw["messages"], [{"role": "user", "content": [{"text": "hi"}]}])
        self.assertEqual(kw["systemPrompt"], [{"text": "x"}])
        self.assertEqual(kw["allowedTools"], DEFAULT_ALLOWED_TOOLS)
        for absent in ("model", "maxTokens", "maxIterations", "timeoutSeconds", "qualifier"):
            self.assertNotIn(absent, kw)
        self.assertIsInstance(res, HarnessResult)
        self.assertEqual(res.text, "ok")
        self.assertEqual(res.session_id, kw["runtimeSessionId"])
        self.assertGreaterEqual(res.wall_ms, 0)

    def test_request_shape_overrides(self):
        client = _FakeClient(self.EVENTS)
        res = invoke(
            "hi", harness_arn="arn:test", system_prompt=[], client=client,
            session_id="s" * 40, model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            allowed_tools=["*"], max_tokens=300, max_iterations=2, timeout_seconds=30,
            qualifier="PROD",
        )
        kw = client.kwargs
        self.assertEqual(kw["runtimeSessionId"], "s" * 40)
        self.assertEqual(res.session_id, "s" * 40)
        self.assertNotIn("systemPrompt", kw)  # [] means "use harness default"
        self.assertEqual(kw["model"], {"bedrockModelConfig": {"modelId": "us.anthropic.claude-haiku-4-5-20251001-v1:0"}})
        self.assertEqual(kw["allowedTools"], ["*"])
        self.assertEqual(kw["maxTokens"], 300)
        self.assertEqual(kw["maxIterations"], 2)
        self.assertEqual(kw["timeoutSeconds"], 30)
        self.assertEqual(kw["qualifier"], "PROD")

    def test_invoke_surfaces_stream_error(self):
        client = _FakeClient([{"runtimeClientError": {"message": "denied"}}])
        with self.assertRaises(RuntimeError):
            invoke("hi", harness_arn="arn:test", system_prompt=[], client=client)


class EstimateCostTests(unittest.TestCase):

    def test_known_model_positive(self):
        cost = estimate_cost_usd(
            "us.anthropic.claude-sonnet-4-20250514-v1:0",
            {"inputTokens": 1_000_000, "outputTokens": 0},
        )
        self.assertIsInstance(cost, float)
        self.assertAlmostEqual(cost, 3.00, places=2)  # $3/M input for Sonnet 4

    def test_cache_read_counted(self):
        base = estimate_cost_usd("us.anthropic.claude-sonnet-4-20250514-v1:0",
                                 {"inputTokens": 0, "outputTokens": 0})
        cached = estimate_cost_usd("us.anthropic.claude-sonnet-4-20250514-v1:0",
                                   {"inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 1_000_000})
        self.assertEqual(base, 0.0)
        self.assertGreater(cached, 0.0)


if __name__ == "__main__":
    unittest.main()


class FabricationGuardTests(unittest.TestCase):
    """Client-side guard: figures without any tool call are unverified."""

    def test_clean_answer_with_tool_call_is_not_flagged(self):
        from agent.harness_client import detect_fabrication
        self.assertIsNone(detect_fabrication("You spent $5.10 on 1,234 tokens.", ["bedrock_usage"]))

    def test_no_figures_no_tools_is_not_flagged(self):
        from agent.harness_client import detect_fabrication
        self.assertIsNone(detect_fabrication("I cannot reach my usage tools right now.", []))

    def test_dollar_figures_without_tools_are_flagged(self):
        from agent.harness_client import detect_fabrication
        warning = detect_fabrication("Total estimated cost: $24.73 across 2,847,650 tokens.", [])
        self.assertIsNotNone(warning)
        self.assertIn("no tool was called", warning)

    def test_hand_written_tool_transcript_is_flagged(self):
        from agent.harness_client import detect_fabrication
        fake = '<invoke name="bedrock_usage">\n<parameter name="start_date">2026-09-05</parameter>\n</invoke>\n<result>{}</result>'
        warning = detect_fabrication(fake, [])
        self.assertIn("hand-written tool transcript", warning)

    def test_usage_footer_marks_unverified(self):
        from agent.harness_client import HarnessResult
        res = HarnessResult(text="$1.00", usage={"inputTokens": 1, "outputTokens": 1}, fabrication_warning="x")
        self.assertIn("UNVERIFIED", res.usage_footer())
