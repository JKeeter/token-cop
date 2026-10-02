"""Thin client for the Token Cop AgentCore *harness* twin.

The harness is the managed, config-only chassis for Token Cop: AWS runs the
Strands agent loop, we supply the model, system prompt, and a Gateway that
fronts the same 13 tools the container runtime uses in-process.

This module wraps ``bedrock-agentcore:InvokeHarness`` so that callers
(``mcp_server.py`` with ``TOKEN_COP_BACKEND=harness``, ``scripts/harness_demo.py``)
get a plain text answer plus the token-usage metadata the harness streams back.

Only ``boto3`` is required at import time. ``agent.agent.build_system_prompt``
is imported lazily so this module can be used from a process that does not
have Strands installed.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
SSM_HARNESS_ARN = "/token-cop/harness-arn"

# Default the harness restricts itself to: only the Token Cop gateway tools.
# The built-in ``shell`` + ``file_operations`` tools cost ~900 input tokens
# per model call and are irrelevant to usage analytics.
GATEWAY_TOOL_NAME = "token-cop-gw"
TOOLS_TARGET_NAME = "token-cop-tools"
DEFAULT_ALLOWED_TOOLS = [f"@{GATEWAY_TOOL_NAME}/{TOOLS_TARGET_NAME}___*"]

# Usage counters summed across every ``metadata`` event in one invocation.
USAGE_COUNTERS = (
    "inputTokens",
    "outputTokens",
    "totalTokens",
    "cacheReadInputTokens",
    "cacheWriteInputTokens",
)

_ssm_cache: dict[str, str] = {}


def new_session_id() -> str:
    """Return a runtimeSessionId that satisfies the >=33 char requirement."""
    return str(uuid.uuid4())  # 36 chars


def get_harness_arn() -> str:
    """Harness ARN from ``TOKEN_COP_HARNESS_ARN`` or SSM ``/token-cop/harness-arn``."""
    env = os.environ.get("TOKEN_COP_HARNESS_ARN")
    if env:
        return env
    if SSM_HARNESS_ARN not in _ssm_cache:
        ssm = boto3.client("ssm", region_name=REGION)
        _ssm_cache[SSM_HARNESS_ARN] = ssm.get_parameter(Name=SSM_HARNESS_ARN)["Parameter"]["Value"]
    return _ssm_cache[SSM_HARNESS_ARN]


def system_prompt_blocks(today: date | None = None) -> list[dict]:
    """Render Token Cop's system prompt as harness ``systemPrompt`` blocks."""
    from agent.agent import build_system_prompt

    return [{"text": build_system_prompt(today)}]


@dataclass
class HarnessResult:
    text: str
    stop_reason: str | None = None
    # inputTokens/outputTokens/totalTokens/cacheRead*/cacheWrite*, SUMMED over
    # every model call in the invocation (one ``metadata`` event per call).
    usage: dict = field(default_factory=dict)
    latency_ms: int | None = None  # latencyMs of the last model call
    total_latency_ms: int = 0  # sum of latencyMs across all model calls
    model_calls: int = 0  # number of ``metadata`` events seen
    tool_calls: list[str] = field(default_factory=list)  # tool names the agent invoked, in order
    transcript: str = ""  # every assistant text block, incl. "Let me check..." narration
    wall_ms: int = 0
    session_id: str = ""
    fabrication_warning: str | None = None  # set when figures appear without any tool call

    @property
    def input_tokens(self) -> int:
        return int(self.usage.get("inputTokens", 0))

    @property
    def output_tokens(self) -> int:
        return int(self.usage.get("outputTokens", 0))

    def usage_footer(self) -> str:
        """One-line usage summary; Token Cop reporting on itself."""
        parts = [
            f"in={self.input_tokens:,}",
            f"out={self.output_tokens:,}",
        ]
        cr = int(self.usage.get("cacheReadInputTokens", 0) or 0)
        if cr:
            parts.append(f"cache_read={cr:,}")
        if self.model_calls > 1:
            parts.append(f"calls={self.model_calls}")
        if self.tool_calls:
            parts.append(f"tools={','.join(self.tool_calls)}")
        if self.stop_reason and self.stop_reason != "end_turn":
            parts.append(f"stop={self.stop_reason}")
        if self.fabrication_warning:
            parts.append("UNVERIFIED")
        return "harness usage: " + " ".join(parts)


def invoke(
    prompt: str,
    *,
    session_id: str | None = None,
    harness_arn: str | None = None,
    system_prompt: list[dict] | None = None,
    model_id: str | None = None,
    allowed_tools: list[str] | None = None,
    max_tokens: int | None = None,
    max_iterations: int | None = None,
    timeout_seconds: int | None = None,
    qualifier: str | None = None,
    client=None,
    on_text=None,
) -> HarnessResult:
    """Invoke the harness and collect the streamed response.

    Args:
        prompt: User message.
        session_id: Reuse to continue a conversation (defaults to a fresh UUID).
        harness_arn: Defaults to env/SSM lookup.
        system_prompt: Harness ``systemPrompt`` blocks. Defaults to Token Cop's
            prompt rendered for today. Pass ``[]`` to use the harness default.
        model_id: Per-invocation Bedrock model override (no redeploy).
        allowed_tools: Per-invocation ``allowedTools``. Defaults to the Token Cop
            gateway tools only. Pass ``["*"]`` to include the built-in shell/file tools.
        max_tokens / max_iterations / timeout_seconds: Per-invocation caps.
        qualifier: Named harness endpoint (e.g. ``PROD``). Omit for ``DEFAULT``.
        client: Pre-built ``bedrock-agentcore`` client (tests).
        on_text: Optional callback receiving assistant text deltas as they stream
            (includes intermediate narration, not just the final answer).
    """
    client = client or boto3.client("bedrock-agentcore", region_name=REGION)
    session_id = session_id or new_session_id()

    kwargs: dict = {
        "harnessArn": harness_arn or get_harness_arn(),
        "runtimeSessionId": session_id,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
    }
    if system_prompt is None:
        system_prompt = system_prompt_blocks()
    if system_prompt:
        kwargs["systemPrompt"] = system_prompt
    if model_id:
        kwargs["model"] = {"bedrockModelConfig": {"modelId": model_id}}
    if allowed_tools is None:
        allowed_tools = DEFAULT_ALLOWED_TOOLS
    if allowed_tools:
        kwargs["allowedTools"] = allowed_tools
    if max_tokens is not None:
        kwargs["maxTokens"] = max_tokens
    if max_iterations is not None:
        kwargs["maxIterations"] = max_iterations
    if timeout_seconds is not None:
        kwargs["timeoutSeconds"] = timeout_seconds
    if qualifier:
        kwargs["qualifier"] = qualifier

    started = time.monotonic()
    response = client.invoke_harness(**kwargs)
    result = _consume_stream(response["stream"], on_text=on_text)
    result.wall_ms = int((time.monotonic() - started) * 1000)
    result.session_id = session_id
    result.fabrication_warning = detect_fabrication(result.text, result.tool_calls)
    return result


def _consume_stream(stream, on_text=None) -> HarnessResult:
    """Fold InvokeHarness stream events into a HarnessResult.

    One invocation streams several messages: ``assistant`` (text and/or
    ``toolUse`` blocks, ``stopReason=tool_use``), ``user`` (``toolResult``,
    ``stopReason=tool_result``), then ``assistant`` again, until a terminal
    stopReason (``end_turn`` or a cap such as ``max_output_tokens_exceeded``).

    Text handling:
      * Only ``assistant`` text is kept; tool results are never echoed.
      * ``text`` is the text of the *last assistant message that had any*, so
        narration like "Let me check..." before a tool call is dropped from
        the answer. If no assistant message produced text, ``text`` is empty.
      * ``transcript`` is every assistant text block, in order, messages
        separated by blank lines — for the demo / debugging.
      * ``on_text`` receives every assistant delta as it streams.

    Usage handling: a ``metadata`` event is emitted once per model call, so
    the token counters are SUMMED across events (``usage``), ``latency_ms`` is
    the last call's latency and ``total_latency_ms`` the sum.

    Raises ``RuntimeError`` on ``runtimeClientError``/``internalServerException``
    and ``ValueError`` on ``validationException``.
    """
    tool_calls: list[str] = []
    stop_reason = None
    usage: dict = {}
    latency_ms = None
    total_latency_ms = 0
    model_calls = 0

    current_role = "assistant"
    current_msg: list[str] = []  # text of the assistant message being streamed
    messages: list[str] = []  # completed assistant messages (may be empty strings)

    def _flush_message():
        if current_role == "assistant":
            messages.append("".join(current_msg).strip())
        current_msg.clear()

    for event in stream:
        if "messageStart" in event:
            _flush_message()
            current_role = event["messageStart"].get("role", "assistant")
        elif "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                name = start["toolUse"].get("name", "?")
                tool_calls.append(name.split("___")[-1])
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta and current_role == "assistant":
                current_msg.append(delta["text"])
                if on_text:
                    on_text(delta["text"])
        elif "messageStop" in event:
            # Several messages stream per invocation (tool turns). The last
            # stopReason is the one that matters.
            stop_reason = event["messageStop"].get("stopReason", stop_reason)
        elif "metadata" in event:
            md = event["metadata"]
            model_calls += 1
            for key, val in (md.get("usage") or {}).items():
                if key in USAGE_COUNTERS:
                    usage[key] = usage.get(key, 0) + int(val or 0)
                else:
                    usage[key] = val
            lat = (md.get("metrics") or {}).get("latencyMs")
            if lat is not None:
                latency_ms = lat
                total_latency_ms += int(lat)
        elif "runtimeClientError" in event:
            raise RuntimeError(f"harness error: {event['runtimeClientError'].get('message')}")
        elif "validationException" in event:
            raise ValueError(f"harness validation error: {event['validationException'].get('message')}")
        elif "internalServerException" in event:
            raise RuntimeError(f"harness internal error: {event['internalServerException'].get('message')}")

    _flush_message()

    with_text = [m for m in messages if m]
    return HarnessResult(
        text=with_text[-1] if with_text else "",
        stop_reason=stop_reason,
        usage=usage,
        latency_ms=latency_ms,
        total_latency_ms=total_latency_ms,
        model_calls=model_calls,
        tool_calls=tool_calls,
        transcript="\n\n".join(with_text),
    )


# Markers of a model "role-playing" a tool call in plain text. Observed live
# (2026-09-11): with zero tools bound (Cedar deny on the gateway), Claude Sonnet 4
# emitted a fake <invoke>/<result> transcript with invented dollar figures, even
# with an explicit anti-fabrication rule in the system prompt. Prompt rules do
# not reliably stop this, so the client checks the answer against the trace.
_FAKE_TOOL_MARKERS = ("<invoke", "<function_calls", "<tool_call", "<result>", "<tool_result")
_FIGURE_RE = re.compile(r"(\$\s?\d[\d,]*(\.\d+)?)|(\b\d{1,3}(,\d{3})+\b\s*(tokens?|requests?|invocations?))", re.I)


def detect_fabrication(text: str, tool_calls: list[str]) -> str | None:
    """Return a warning if the answer contains usage figures but no tool ran.

    Token Cop's contract is that every figure comes from a tool result. If the
    trace shows zero tool calls and the answer still contains dollar amounts,
    comma-grouped token/request counts, or a hand-written tool transcript, the
    figures cannot be real. Returns ``None`` when the answer looks honest.
    """
    if tool_calls:
        return None
    lowered = text.lower()
    if any(m in lowered for m in _FAKE_TOOL_MARKERS):
        return "answer contains a hand-written tool transcript; no tool was actually called"
    if _FIGURE_RE.search(text):
        return "answer contains usage figures but no tool was called; treat every number as invented"
    return None


def estimate_cost_usd(model_id: str, usage: dict) -> float:
    """Estimate USD for a harness usage block using models/pricing.py."""
    from models.normalization import normalize_model_name
    from models.pricing import estimate_cost

    return estimate_cost(
        normalize_model_name(model_id),
        int(usage.get("inputTokens", 0)),
        int(usage.get("outputTokens", 0)),
        int(usage.get("cacheReadInputTokens", 0) or 0),
        int(usage.get("cacheWriteInputTokens", 0) or 0),
    )


if __name__ == "__main__":  # quick manual smoke test
    import sys

    q = " ".join(sys.argv[1:]) or "What did we spend on Bedrock in the last 7 days, by model?"
    res = invoke(q, on_text=lambda t: print(t, end="", flush=True))
    print("\n\n" + res.usage_footer(), f"wall={res.wall_ms}ms")
    print(json.dumps(res.usage))
