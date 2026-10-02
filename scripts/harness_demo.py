"""Dual-chassis demo: Token Cop on AgentCore Runtime vs the AgentCore *harness* twin.

Six acts, each framed for a CTO audience: why it matters, do the thing, show a
compact result table. Mirrors the style of ``scripts/eval_demo.py``.

Usage:
    python -m scripts.harness_demo                 # all six acts, pause between them
    python -m scripts.harness_demo --act 3         # one act
    python -m scripts.harness_demo --skip-acts 5,6 # everything except policy + versioning
    python -m scripts.harness_demo --no-pause      # non-interactive
    python -m scripts.harness_demo --json          # append machine-readable summary (slides)
    python -m scripts.harness_demo --polish-model us.amazon.nova-lite-v1:0

Prerequisites:
    1. Runtime deployed (``agentcore deploy``) and its ARN in SSM ``/token-cop/agent-arn``.
    2. Harness twin provisioned: ``python -m scripts.setup_harness --enable``
       (writes ``/token-cop/harness-arn`` and ``/token-cop/harness-id`` to SSM).
    3. Cedar policies provisioned: ``python -m scripts.setup_policies`` (LOG_ONLY).
    4. Act 6's export escape hatch needs the npm CLI at ``/opt/homebrew/bin/agentcore``
       (the venv's ``agentcore`` is the Python starter toolkit and cannot export harnesses).
       If it is missing the act prints the command instead of failing.

Acts:
    1. Same question, two chassis   - runtime (container) vs harness (config)
    2. Trim the harness             - allowedTools removes ~900 tokens/call of built-in schemas
    3. Act on Token Cop's advice    - recommend_model says "polish" -> rerun on Haiku/Nova
    4. Hard caps                    - maxTokens / maxIterations enforced inside the loop
    5. One policy plane             - the same Cedar forbid denies the harness's tool calls
    6. Ship, roll back, escape hatch - versions, PROD endpoint, export to Strands code

Every act catches its own exceptions, prints ``ERROR: ...`` and lets the next act run.
Nothing here creates durable AWS resources except Act 6 (a new harness version and the
``PROD`` endpoint, both intended to persist) and Act 5's temporary forbid policy, which
is always deleted in a ``finally``.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REGION = os.environ.get("AWS_REGION", "us-east-1")
REPO_ROOT = Path(__file__).resolve().parent.parent

# Sonnet 4 on Bedrock -- the same id as agent.agent.MODEL_ID. Duplicated so this
# module imports without Strands; ``resolve_model_id`` prefers the live value.
SONNET_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"
POLISH_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
POLISH_MODEL_CHOICES = [POLISH_MODEL_ID, "us.amazon.nova-lite-v1:0"]

SSM_HARNESS_ID = "/token-cop/harness-id"
NPM_AGENTCORE = "/opt/homebrew/bin/agentcore"
EXPORT_DIR = "/tmp/token-cop-harness-export"

# AWS docs: the built-in ``shell`` + ``file_operations`` tools add roughly this
# many input tokens of schema to every model call when they are exposed.
BUILTIN_TOOL_OVERHEAD_TOKENS = 900

SPEND_PROMPT = "What did we spend on Bedrock in the last 7 days, by model?"
CLASSIFY_PROMPT = (
    "Use recommend_model to classify this task and tell me the tier: "
    "'fix typos and tidy the formatting of this budget summary'"
)
POLISH_PROMPT = (
    "Reformat this as a tidy markdown table: bedrock $12.30, openai $4.10, openrouter $0.90"
)

OBSERVABILITY_URL = (
    "https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1"
    "#gen-ai-observability/agent-core"
)

ANSWER_TRUNCATE = 1200

# Accumulates every measured number for --json.
SUMMARY: dict = {"acts": {}}
PAUSE = True

# ---------------------------------------------------------------------------
# Pure helpers (unit-tested in tests/test_harness_demo.py)
# ---------------------------------------------------------------------------


def fmt_usd(value) -> str:
    """Format a dollar figure; sub-cent values keep 4 decimals so deltas stay visible."""
    if value is None:
        return "n/a"
    value = float(value)
    if value == 0:
        return "$0.00"
    if abs(value) < 0.01:
        return f"${value:.4f}"
    return f"${value:,.2f}"


def fmt_int(value) -> str:
    """Thousands-separated integer, or ``n/a`` for None."""
    if value is None:
        return "n/a"
    return f"{int(value):,}"


def pct_delta(before, after) -> float | None:
    """Percent change from ``before`` to ``after``; None when before is 0/None."""
    if before is None or after is None:
        return None
    before = float(before)
    if before == 0:
        return None
    return round((float(after) - before) / before * 100.0, 1)


def fmt_pct(value) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def truncate(text: str, limit: int = ANSWER_TRUNCATE) -> str:
    """Cut ``text`` at ``limit`` chars with an ellipsis marker showing what was dropped."""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n... [{len(text) - limit:,} more chars]"


def compare_rows(headers: list[str], rows: list[list], indent: str = "  ") -> str:
    """Render a simple aligned text table. Every cell is str()-ed."""
    cells = [[str(c) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, c in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(c))
            else:
                widths.append(len(c))
    def line(vals):
        return indent + " | ".join(v.ljust(widths[i]) for i, v in enumerate(vals))
    out = [line(headers), indent + "-+-".join("-" * w for w in widths)]
    out.extend(line(r) for r in cells)
    return "\n".join(out)


def model_calls(tool_calls: list) -> int:
    """Approximate number of model calls in one harness turn: one per tool + final."""
    return len(tool_calls or []) + 1


def per_call_overhead(delta_tokens: int, calls: int) -> float:
    """Input-token delta spread across model calls (never divides by zero)."""
    return round(delta_tokens / max(1, calls), 1)


def parse_skip_acts(value: str | None) -> set[int]:
    """``"5,6"`` -> {5, 6}; tolerates spaces and blanks; rejects non-integers."""
    if not value:
        return set()
    out = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        out.add(int(part))
    return out


def harness_id_from_arn(arn: str) -> str:
    """``arn:...:harness/abc-123`` -> ``abc-123`` (``/`` suffixes like versions dropped)."""
    tail = arn.rsplit("harness/", 1)[-1]
    return tail.split("/", 1)[0].split(":", 1)[0]


def endpoint_rows(endpoints: list[dict]) -> list[list[str]]:
    """Rows for the Act 6 endpoint table from ``list_harness_endpoints`` items."""
    rows = []
    for ep in sorted(endpoints, key=lambda e: e.get("endpointName", "")):
        rows.append([
            ep.get("endpointName", "?"),
            str(ep.get("liveVersion", "?")),
            str(ep.get("targetVersion", ep.get("liveVersion", "?"))),
            ep.get("status", "?"),
        ])
    return rows


def diff_excerpt(ours: str, theirs: str, ours_name: str, theirs_name: str, lines: int = 40) -> str:
    """First ``lines`` lines of a unified diff between two source strings."""
    diff = difflib.unified_diff(
        ours.splitlines(), theirs.splitlines(), fromfile=ours_name, tofile=theirs_name, lineterm="",
    )
    out = []
    for i, line in enumerate(diff):
        if i >= lines:
            out.append(f"... (diff truncated at {lines} lines)")
            break
        out.append(line)
    return "\n".join(out)


def find_exported_python(export_dir: str) -> Path | None:
    """Prefer an ``agent.py`` in the export tree, else the first ``*.py``."""
    root = Path(export_dir)
    if not root.is_dir():
        return None
    candidates = sorted(root.rglob("agent.py")) or sorted(root.rglob("*.py"))
    return candidates[0] if candidates else None


def resolve_model_id(cli_value: str | None) -> str:
    """CLI override > agent.agent.MODEL_ID > SONNET_MODEL_ID."""
    if cli_value:
        return cli_value
    try:
        from agent.agent import MODEL_ID
        return MODEL_ID
    except Exception:
        return SONNET_MODEL_ID


def record(act: int, **numbers) -> None:
    SUMMARY["acts"].setdefault(str(act), {}).update(numbers)


# ---------------------------------------------------------------------------
# Presentation helpers (same shape as scripts/eval_demo.py)
# ---------------------------------------------------------------------------


def header(title, act=None):
    width = 70
    print()
    print("=" * width)
    print(f"  Act {act}: {title}" if act else f"  {title}")
    print("=" * width)
    print()


def why(text: str) -> None:
    print("  WHY IT MATTERS")
    for line in _wrap(text, 66):
        print(f"    {line}")
    print()


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        lines.append(cur)
    return lines


def cli_hint(cmd):
    print("  CLI equivalent:")
    print(f"    {cmd}")
    print()


def wait_for_enter(msg="Press Enter to continue..."):
    if not PAUSE:
        return
    try:
        input(f"\n  {msg}")
    except (EOFError, KeyboardInterrupt):
        print()


def show_answer(label: str, text: str) -> None:
    print(f"  {label}:")
    for line in truncate(text).splitlines() or [""]:
        print(f"    {line}")
    print()


def _harness():
    from agent import harness_client
    return harness_client


def _harness_usage_row(label: str, res, model_id: str) -> list:
    hc = _harness()
    return [
        label,
        fmt_int(res.wall_ms),
        ",".join(res.tool_calls) or "-",
        f"{fmt_int(res.input_tokens)}/{fmt_int(res.output_tokens)}",
        fmt_usd(hc.estimate_cost_usd(model_id, res.usage)),
    ]


def _invoke_runtime(prompt: str) -> str:
    """Runtime (container) side of Act 1. Reuses mcp_server._call_direct when importable."""
    try:
        import mcp_server
        return mcp_server._call_direct(prompt)
    except ImportError:
        pass
    # Minimal copy of mcp_server._call_direct for environments without the mcp package.
    import boto3
    ssm = boto3.client("ssm", region_name=REGION)
    arn = os.environ.get("TOKEN_COP_AGENT_ARN") or ssm.get_parameter(
        Name="/token-cop/agent-arn", WithDecryption=True
    )["Parameter"]["Value"]
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    response = client.invoke_agent_runtime(agentRuntimeArn=arn, payload=json.dumps({"prompt": prompt}))
    chunks = []
    for event in response.get("body", response.get("output", [])):
        if "chunk" in event:
            data = event["chunk"]
            if "bytes" in data:
                chunks.append(data["bytes"].decode("utf-8"))
            elif "text" in data:
                chunks.append(data["text"])
        elif isinstance(event, bytes):
            chunks.append(event.decode("utf-8"))
    if not chunks:
        body = response.get("body")
        if body and hasattr(body, "read"):
            raw = body.read()
            chunks.append(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
    return "".join(chunks) if chunks else "No response received from Token Cop agent."


# ---------------------------------------------------------------------------
# Act 1: Same question, two chassis
# ---------------------------------------------------------------------------


def act1(model_id: str, **_):
    header("Same question, two chassis", act=1)
    why(
        "Token Cop today is a hand-built Strands agent in a container on AgentCore "
        "Runtime. The harness is the same agent declared as configuration: model, "
        "prompt, tools, limits. If both chassis answer the same question with the "
        "same tools, the team can pick per workload -- and the harness reports its "
        "own token bill on every call, which the container never did."
    )
    hc = _harness()
    print(f"  Prompt: {SPEND_PROMPT!r}")
    print()

    print("  [runtime]  invoke_agent_runtime ...", flush=True)
    t0 = time.monotonic()
    runtime_text = _invoke_runtime(SPEND_PROMPT)
    runtime_ms = int((time.monotonic() - t0) * 1000)
    print(f"  [runtime]  {runtime_ms:,} ms")

    print("  [harness]  invoke_harness ...", flush=True)
    res = hc.invoke(SPEND_PROMPT, session_id=hc.new_session_id())
    print(f"  [harness]  {res.wall_ms:,} ms  {res.usage_footer()}")
    print()

    harness_cost = hc.estimate_cost_usd(model_id, res.usage)
    print(compare_rows(
        ["chassis", "wall ms", "tools called", "in/out tokens", "est. $"],
        [
            ["runtime (container)", fmt_int(runtime_ms), "n/a (in-process)", "n/a, see CloudWatch", "n/a"],
            _harness_usage_row("harness (config)", res, model_id),
        ],
    ))
    print()
    show_answer("Runtime answer", runtime_text)
    show_answer("Harness answer", getattr(res, "transcript", res.text))
    print("  Harness traces (GenAI Observability -> Harnesses tab):")
    print(f"    {OBSERVABILITY_URL}")
    print(f"    session: {res.session_id}")
    record(
        1,
        prompt=SPEND_PROMPT,
        runtime_wall_ms=runtime_ms,
        harness_wall_ms=res.wall_ms,
        harness_latency_ms=res.latency_ms,
        harness_tool_calls=res.tool_calls,
        harness_usage=res.usage,
        harness_est_usd=harness_cost,
        harness_session_id=res.session_id,
        harness_stop_reason=res.stop_reason,
    )


# ---------------------------------------------------------------------------
# Act 2: Trim the harness
# ---------------------------------------------------------------------------


def act2(model_id: str, **_):
    header("Trim the harness", act=2)
    why(
        "Every tool the loop exposes costs input tokens on every model call, whether "
        "or not it is used. The harness ships built-in shell and file tools that "
        f"add about {BUILTIN_TOOL_OVERHEAD_TOKENS} tokens per call. allowedTools is a "
        "one-line config change that removes them. Token Cop measures this as its "
        "'context overhead' dimension; the harness lets you fix it without a redeploy."
    )
    hc = _harness()
    print(f"  Prompt (both runs, fresh sessions): {SPEND_PROMPT!r}")
    print()

    print("  [all tools]      allowedTools=['*'] ...", flush=True)
    res_all = hc.invoke(SPEND_PROMPT, session_id=hc.new_session_id(), allowed_tools=["*"])
    print(f"  [all tools]      {res_all.usage_footer()}")
    print(f"  [gateway only]   allowedTools={hc.DEFAULT_ALLOWED_TOOLS} ...", flush=True)
    res_gw = hc.invoke(SPEND_PROMPT, session_id=hc.new_session_id())
    print(f"  [gateway only]   {res_gw.usage_footer()}")
    print()

    delta = res_all.input_tokens - res_gw.input_tokens
    calls = model_calls(res_all.tool_calls)
    overhead = per_call_overhead(delta, calls)
    cost_all = hc.estimate_cost_usd(model_id, res_all.usage)
    cost_gw = hc.estimate_cost_usd(model_id, res_gw.usage)

    print(compare_rows(
        ["allowedTools", "wall ms", "tools called", "in/out tokens", "est. $"],
        [
            _harness_usage_row("* (built-ins exposed)", res_all, model_id),
            _harness_usage_row("gateway tools only", res_gw, model_id),
        ],
    ))
    print()
    print(f"  Input-token delta:          {fmt_int(delta)}  ({fmt_pct(pct_delta(res_all.input_tokens, res_gw.input_tokens))})")
    print(f"  Model calls (tools+1):      {calls}")
    print(f"  Overhead per model call:    ~{overhead:,.0f} tokens  (AWS docs: ~{BUILTIN_TOOL_OVERHEAD_TOKENS} for shell+file_operations)")
    print(f"  Cost delta this turn:       {fmt_usd(cost_all - cost_gw)}")
    record(
        2,
        all_tools_usage=res_all.usage,
        gateway_only_usage=res_gw.usage,
        input_token_delta=delta,
        model_calls=calls,
        overhead_per_call=overhead,
        all_tools_est_usd=cost_all,
        gateway_only_est_usd=cost_gw,
        pct_delta_input=pct_delta(res_all.input_tokens, res_gw.input_tokens),
    )


# ---------------------------------------------------------------------------
# Act 3: Act on Token Cop's advice
# ---------------------------------------------------------------------------


def act3(model_id: str, polish_model: str = POLISH_MODEL_ID, **_):
    header("Act on Token Cop's advice", act=3)
    why(
        "Token Cop's recommend_model tool tells you which tier a task deserves. On the "
        "container, acting on that advice means a code change and a CodeBuild cycle. "
        "On the harness, model is a per-invocation parameter: the agent recommends "
        "'polish', and the very next call runs on a polish-tier model. Same tools, "
        "same prompt, a fraction of the price."
    )
    hc = _harness()

    print("  Step A -- ask the harness for a tier recommendation")
    print(f"    Prompt: {CLASSIFY_PROMPT!r}")
    rec = hc.invoke(CLASSIFY_PROMPT, session_id=hc.new_session_id())
    print(f"    {rec.usage_footer()}")
    show_answer("Recommendation", getattr(rec, "transcript", rec.text))

    print("  Step B -- run the polish task on both tiers (fresh session each)")
    print(f"    Prompt: {POLISH_PROMPT!r}")
    print(f"    [{model_id}] ...", flush=True)
    res_big = hc.invoke(POLISH_PROMPT, session_id=hc.new_session_id())
    print(f"    {res_big.usage_footer()}")
    print(f"    [{polish_model}] ...", flush=True)
    res_small = hc.invoke(POLISH_PROMPT, session_id=hc.new_session_id(), model_id=polish_model)
    print(f"    {res_small.usage_footer()}")
    print()

    cost_big = hc.estimate_cost_usd(model_id, res_big.usage)
    cost_small = hc.estimate_cost_usd(polish_model, res_small.usage)
    delta_pct = pct_delta(cost_big, cost_small)
    print(compare_rows(
        ["model", "in/out tokens", "wall ms", "est. $", "delta %"],
        [
            [model_id, f"{fmt_int(res_big.input_tokens)}/{fmt_int(res_big.output_tokens)}",
             fmt_int(res_big.wall_ms), fmt_usd(cost_big), "baseline"],
            [polish_model, f"{fmt_int(res_small.input_tokens)}/{fmt_int(res_small.output_tokens)}",
             fmt_int(res_small.wall_ms), fmt_usd(cost_small), fmt_pct(delta_pct)],
        ],
    ))
    print()
    show_answer(f"Polish-tier answer ({polish_model})", getattr(res_small, "transcript", res_small.text))
    print("  The model swap is a per-call parameter -- it also works mid-session,")
    print("  so a long conversation can drop to a cheaper tier for its last mile.")
    record(
        3,
        recommendation_text=truncate(getattr(rec, "transcript", rec.text), 600),
        recommendation_tool_calls=rec.tool_calls,
        baseline_model=model_id,
        baseline_usage=res_big.usage,
        baseline_est_usd=cost_big,
        polish_model=polish_model,
        polish_usage=res_small.usage,
        polish_est_usd=cost_small,
        cost_delta_pct=delta_pct,
    )


# ---------------------------------------------------------------------------
# Act 4: Hard caps
# ---------------------------------------------------------------------------


def act4(model_id: str, **_):
    header("Hard caps", act=4)
    why(
        "Token Cop's budget enforcement works at the IAM layer: a principal that "
        "overspends loses access next month. The harness adds enforcement inside the "
        "loop, on every call: maxTokens stops the answer, maxIterations stops the "
        "tool loop, timeoutSeconds stops the clock. Token Cop measures; the harness "
        "enforces. Together they bound the worst case, not just the average."
    )
    hc = _harness()

    print(f"  [maxTokens=100]      {SPEND_PROMPT!r} ...", flush=True)
    res_tok = hc.invoke(SPEND_PROMPT, session_id=hc.new_session_id(), max_tokens=100)
    print(f"  [maxTokens=100]      stop_reason={res_tok.stop_reason}  {res_tok.usage_footer()}")
    show_answer("Truncated answer", getattr(res_tok, "transcript", res_tok.text))

    print(f"  [maxIterations=1]    {SPEND_PROMPT!r} ...", flush=True)
    res_it = hc.invoke(SPEND_PROMPT, session_id=hc.new_session_id(), max_iterations=1)
    print(f"  [maxIterations=1]    stop_reason={res_it.stop_reason}  {res_it.usage_footer()}")
    show_answer("Iteration-capped answer", getattr(res_it, "transcript", res_it.text))

    print(compare_rows(
        ["cap", "stop_reason", "tools called", "in/out tokens", "est. $"],
        [
            ["maxTokens=100", res_tok.stop_reason or "?", ",".join(res_tok.tool_calls) or "-",
             f"{fmt_int(res_tok.input_tokens)}/{fmt_int(res_tok.output_tokens)}",
             fmt_usd(hc.estimate_cost_usd(model_id, res_tok.usage))],
            ["maxIterations=1", res_it.stop_reason or "?", ",".join(res_it.tool_calls) or "-",
             f"{fmt_int(res_it.input_tokens)}/{fmt_int(res_it.output_tokens)}",
             fmt_usd(hc.estimate_cost_usd(model_id, res_it.usage))],
        ],
    ))
    print()
    print("  Expected: max_output_tokens_exceeded and max_iterations_exceeded.")
    print("  Note: the harness checks the output-token budget BETWEEN iterations, so the cap must sit")
    print("        below the first turn's output (~130 tokens here) to stop the loop before the tool result.")
    record(
        4,
        max_tokens_stop_reason=res_tok.stop_reason,
        max_tokens_usage=res_tok.usage,
        max_iterations_stop_reason=res_it.stop_reason,
        max_iterations_usage=res_it.usage,
        max_iterations_tool_calls=res_it.tool_calls,
    )


# ---------------------------------------------------------------------------
# Act 5: One policy plane
# ---------------------------------------------------------------------------


def act5(model_id: str, **_):
    header("One policy plane", act=5)
    why(
        "Governance should be a property of the tool plane, not of whichever agent "
        "chassis is calling it. Both chassis reach Token Cop's tools through the same "
        "MCP Gateway, so the same Cedar policy engine gates both. Below, one forbid "
        "statement in ENFORCE mode denies the Claude Code path (tools/list) and the "
        "harness path (a live tool call) at the same time -- then we roll it back. "
        "Watch the denied harness answer closely: a model with zero tools bound will "
        "improvise a tool transcript with invented numbers, and a prompt rule alone does "
        "not stop it. Token Cop's client-side guard compares the answer to the trace "
        "(no tool call + figures = UNVERIFIED), which is what makes a deny safe to ship."
    )
    from scripts import setup_policies as sp
    from bedrock_agentcore_starter_toolkit.operations.gateway import GatewayClient
    from bedrock_agentcore_starter_toolkit.operations.policy import PolicyClient

    for name in ("bedrock_agentcore.policy", "bedrock_agentcore.gateway", "botocore", "urllib3"):
        import logging
        logging.getLogger(name).setLevel(logging.ERROR)
    sp.log.setLevel(logging.ERROR)

    policy_client = PolicyClient(region_name=REGION)
    gateway_client = GatewayClient(region_name=REGION)
    engine = sp.find_engine(policy_client)
    if not engine:
        raise RuntimeError("no Cedar policy engine found; run `python -m scripts.setup_policies` first")
    engine_id, engine_arn = engine["policyEngineId"], engine["policyEngineArn"]

    gw = gateway_client.client.get_gateway(gatewayIdentifier=sp.GATEWAY_ID)
    original_mode = (gw.get("policyEngineConfiguration") or {}).get("mode", "LOG_ONLY")
    print(f"  Gateway: {sp.GATEWAY_ID}   engine: {engine_id}   mode: {original_mode}")
    print()

    hc = _harness()
    before = sp.call_gateway("tools/list")
    print(f"  Before  tools/list -> {truncate(before, 300)}")

    forbid_id = None
    denied_gateway = denied_harness_text = denied_fab = None
    try:
        print()
        print("  Flip gateway to ENFORCE and add a blanket forbid:")
        print(f"    Cedar: {sp.FORBID_ALL_STATEMENT}")
        cli_hint("python -m scripts.setup_policies --mode ENFORCE   # then add the forbid policy")
        sp.associate_gateway(gateway_client, engine_arn, "ENFORCE")
        forbid = policy_client.create_or_get_policy(
            policy_engine_id=engine_id,
            name="token_cop_forbid_all",
            definition={"cedar": {"statement": sp.FORBID_ALL_STATEMENT}},
            description="harness_demo: temporary blanket deny",
            validation_mode="IGNORE_ALL_FINDINGS",
        )
        forbid_id = forbid["policyId"]
        print(f"  Forbid policy: {forbid_id} ({forbid.get('status')})")

        denied_gateway = sp.call_gateway("tools/list")
        print(f"  Denied  tools/list -> {truncate(denied_gateway, 300)}")
        print()
        print(f"  [harness, denied]  {SPEND_PROMPT!r} ...", flush=True)
        res_denied = hc.invoke(SPEND_PROMPT, session_id=hc.new_session_id())
        denied_harness_text = getattr(res_denied, "transcript", res_denied.text)
        denied_fab = res_denied.fabrication_warning
        print(f"  [harness, denied]  {res_denied.usage_footer()}")
        if res_denied.fabrication_warning:
            print(f"  [harness, denied]  GUARD: {res_denied.fabrication_warning}")
        else:
            print("  [harness, denied]  GUARD: no fabrication detected (model declined honestly)")
        show_answer("Harness answer while denied", denied_harness_text)
        record(
            5,
            denied_harness_usage=res_denied.usage,
            denied_harness_tool_calls=res_denied.tool_calls,
            denied_harness_stop_reason=res_denied.stop_reason,
            denied_harness_fabrication_warning=res_denied.fabrication_warning,
        )
    finally:
        print("  Restoring: delete forbid policy, gateway back to", original_mode)
        if forbid_id:
            try:
                policy_client.delete_policy(engine_id, forbid_id)
                policy_client._wait_for_policy_deleted(engine_id, forbid_id)
            except Exception as e:  # keep going: mode restore matters more
                print(f"  WARNING: could not delete forbid policy {forbid_id}: {e}")
        try:
            sp.associate_gateway(gateway_client, engine_arn, original_mode)
        except Exception as e:
            print(f"  WARNING: could not restore gateway mode {original_mode}: {e}")

    after = sp.call_gateway("tools/list")
    print(f"  After   tools/list -> {truncate(after, 300)}")
    print()
    print(compare_rows(
        ["phase", "gateway tools/list", "harness"],
        [
            ["before", before.split(" - ")[0], "allowed"],
            ["forbid + ENFORCE", (denied_gateway or "?").split(" - ")[0],
             "0 tools bound; answer flagged UNVERIFIED" if denied_fab else "0 tools bound; declined honestly"],
            ["restored", after.split(" - ")[0], "allowed"],
        ],
    ))
    record(
        5,
        original_mode=original_mode,
        gateway_before=before,
        gateway_denied=denied_gateway,
        gateway_after=after,
        denied_harness_text=truncate(denied_harness_text or "", 600),
    )


# ---------------------------------------------------------------------------
# Act 6: Ship, roll back, escape hatch
# ---------------------------------------------------------------------------


def _control():
    import boto3
    return boto3.client("bedrock-agentcore-control", region_name=REGION)


def _harness_id(hc) -> str:
    import boto3
    try:
        ssm = boto3.client("ssm", region_name=REGION)
        return ssm.get_parameter(Name=SSM_HARNESS_ID)["Parameter"]["Value"]
    except Exception:
        return harness_id_from_arn(hc.get_harness_arn())


def _run_setup_harness(*flags: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "scripts.setup_harness", *flags]
    print(f"  $ {' '.join(cmd[2:])}")
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=600)
    for line in (proc.stdout or "").strip().splitlines()[-8:]:
        print(f"    {line}")
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-5:]
        raise RuntimeError(f"setup_harness {' '.join(flags)} failed ({proc.returncode}): " + " | ".join(tail))
    return proc


def _print_endpoints(control, harness_id: str) -> list[dict]:
    eps = control.list_harness_endpoints(harnessId=harness_id).get("endpoints", [])
    print(compare_rows(["endpoint", "live", "target", "status"], endpoint_rows(eps)))
    return eps


def act6(model_id: str, **_):
    header("Ship, roll back, escape hatch", act=6)
    why(
        "Prompt and config changes on the harness create immutable versions. Named "
        "endpoints pin traffic: DEFAULT follows the latest, PROD stays where you put "
        "it, and rollback is one API call, not a rebuild. And when configuration is "
        "no longer enough, `agentcore export harness` hands back the Strands code we "
        "already run -- the same platform, no lock-in cliff."
    )
    hc = _harness()
    control = _control()
    harness_id = _harness_id(hc)
    harness_arn = hc.get_harness_arn()

    current = control.get_harness(harnessId=harness_id)["harness"]
    v_current = int(current.get("harnessVersion"))
    print(f"  Harness: {harness_id}   current version: {v_current}   status: {current.get('status')}")
    print()

    print(f"  Step 1 -- publish a new version (prompt tweak) via setup_harness --update-prompt")
    _run_setup_harness("--update-prompt")
    versions = control.list_harness_versions(harnessId=harness_id).get("harnessVersions", [])
    v_new = max(int(v.get("harnessVersion", 0)) for v in versions) if versions else v_current
    print(f"  Versions: {sorted(int(v.get('harnessVersion', 0)) for v in versions)}  (new: {v_new})")
    print()

    print(f"  Step 2 -- pin PROD to version {v_current} while DEFAULT advances")
    _run_setup_harness("--endpoint", "PROD", "--version", str(v_current))
    eps_pinned = _print_endpoints(control, harness_id)
    print()

    print("  Step 3 -- invoke through the PROD endpoint (qualifier='PROD')")
    res_prod = hc.invoke(SPEND_PROMPT, session_id=hc.new_session_id(), qualifier="PROD")
    print(f"  {res_prod.usage_footer()}  wall={res_prod.wall_ms:,}ms")
    show_answer("PROD answer", getattr(res_prod, "transcript", res_prod.text))

    print(f"  Step 4 -- promote PROD to {v_new}, then roll back to {v_current}")
    _run_setup_harness("--endpoint", "PROD", "--version", str(v_new))
    _print_endpoints(control, harness_id)
    print()
    _run_setup_harness("--endpoint", "PROD", "--version", str(v_current))
    eps_final = _print_endpoints(control, harness_id)
    print()

    export = _export_escape_hatch(harness_arn)
    record(
        6,
        harness_id=harness_id,
        version_before=v_current,
        version_after_update=v_new,
        endpoints_after_pin=eps_pinned,
        endpoints_final=eps_final,
        prod_usage=res_prod.usage,
        prod_est_usd=hc.estimate_cost_usd(model_id, res_prod.usage),
        prod_tool_calls=res_prod.tool_calls,
        export=export,
    )


def _export_escape_hatch(harness_arn: str) -> dict:
    """Run the npm CLI export, guarded; diff generated agent code against ours."""
    print("  Step 5 -- escape hatch: export the harness to Strands code")
    cmd = [NPM_AGENTCORE, "export", "harness", "--arn", harness_arn, "--output", EXPORT_DIR]
    printable = " ".join(cmd)
    result = {"command": printable, "ran": False, "ok": False, "generated_file": None}
    if not os.path.exists(NPM_AGENTCORE):
        print(f"    npm CLI not found at {NPM_AGENTCORE}; run this outside the venv:")
        print(f"      {printable}")
        result["note"] = "npm agentcore CLI missing"
        return result
    try:
        help_out = subprocess.run([NPM_AGENTCORE, "--help"], capture_output=True, text=True, timeout=60)
        has_export = "export" in (help_out.stdout + help_out.stderr)
    except (OSError, subprocess.TimeoutExpired):
        has_export = True  # let the real run decide
    if not has_export:
        print(f"    installed npm CLI ({NPM_AGENTCORE}) predates `agentcore export` (GA, June 2026).")
        print("    upgrade with:  npm install -g @aws/agentcore@latest   then run:")
        print(f"      {printable}")
        result["note"] = "npm agentcore CLI lacks the export command; upgrade required"
        return result
    shutil.rmtree(EXPORT_DIR, ignore_errors=True)
    print(f"    $ {printable}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"    export did not run ({e}); the command above is the escape hatch.")
        result["note"] = str(e)
        return result
    result["ran"] = True
    if proc.returncode != 0:
        tail = ((proc.stderr or proc.stdout) or "").strip().splitlines()[-3:]
        print(f"    export failed (exit {proc.returncode}): {' | '.join(tail)}")
        print("    (the harness->code path exists; this CLI build just did not complete it here)")
        result["note"] = " | ".join(tail)
        return result
    result["ok"] = True
    generated = find_exported_python(EXPORT_DIR)
    if not generated:
        print(f"    exported to {EXPORT_DIR} but no .py file found to diff")
        return result
    result["generated_file"] = str(generated)
    ours = (REPO_ROOT / "agent" / "agent.py").read_text(errors="replace")
    theirs = generated.read_text(errors="replace")
    print(f"    generated: {generated}")
    print("    diff (first 40 lines) -- config graduates to code:")
    for line in diff_excerpt(ours, theirs, "agent/agent.py", str(generated.relative_to(EXPORT_DIR))).splitlines():
        print(f"      {line}")
    return result


# ---------------------------------------------------------------------------
# Registry + main
# ---------------------------------------------------------------------------

ACTS = {
    1: ("Same question, two chassis", act1),
    2: ("Trim the harness", act2),
    3: ("Act on Token Cop's advice", act3),
    4: ("Hard caps", act4),
    5: ("One policy plane", act5),
    6: ("Ship, roll back, escape hatch", act6),
}


def select_acts(act: int | None, skip: set[int]) -> list[int]:
    """Acts to run: a single ``--act`` wins; otherwise 1..6 minus ``--skip-acts``."""
    if act is not None:
        return [act]
    return [n for n in sorted(ACTS) if n not in skip]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Token Cop dual-chassis demo: AgentCore Runtime vs harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(f"  {n}. {title}" for n, (title, _) in ACTS.items()),
    )
    p.add_argument("--all", action="store_true", help="Run acts 1-6 (default)")
    p.add_argument("--act", type=int, choices=sorted(ACTS), help="Run one act only")
    p.add_argument("--skip-acts", default="", help="Comma-separated act numbers to skip, e.g. 5,6")
    p.add_argument("--no-pause", action="store_true", help="Do not wait for Enter between acts")
    p.add_argument("--model", default=None, help=f"Baseline Bedrock model id (default: agent.agent.MODEL_ID = {SONNET_MODEL_ID})")
    p.add_argument("--polish-model", default=POLISH_MODEL_ID, choices=POLISH_MODEL_CHOICES,
                   help="Polish-tier model for Act 3")
    p.add_argument("--json", action="store_true", help="Print a JSON summary of every measured number at the end")
    return p


def run_act(n: int, **kwargs) -> bool:
    title, fn = ACTS[n]
    started = time.monotonic()
    try:
        fn(**kwargs)
        ok = True
    except Exception as e:  # never let one act sink the demo
        print(f"\n  ERROR: act {n} ({title}) failed: {type(e).__name__}: {e}")
        record(n, error=f"{type(e).__name__}: {e}")
        ok = False
    record(n, title=title, ok=ok, act_wall_ms=int((time.monotonic() - started) * 1000))
    return ok


def main(argv: list[str] | None = None) -> int:
    global PAUSE
    args = build_parser().parse_args(argv)
    PAUSE = not args.no_pause
    skip = parse_skip_acts(args.skip_acts)
    acts = select_acts(args.act, skip)
    model_id = resolve_model_id(args.model)

    SUMMARY.update({
        "date": date.today().isoformat(),
        "region": REGION,
        "model_id": model_id,
        "polish_model_id": args.polish_model,
        "acts_requested": acts,
    })

    if args.act is None:
        header("Token Cop: one agent, two chassis")
        print("  Same 13 tools, same Cedar policies, same IAM attribution.")
        print("  Runtime = our Strands code in a container. Harness = the same agent as config.")
        print(f"  Baseline model: {model_id}    polish model: {args.polish_model}")
        if skip:
            print(f"  Skipping acts: {sorted(skip)}")

    for i, n in enumerate(acts):
        run_act(n, model_id=model_id, polish_model=args.polish_model)
        if i < len(acts) - 1:
            wait_for_enter()

    failed = [n for n in acts if not SUMMARY["acts"].get(str(n), {}).get("ok")]
    print()
    print("=" * 70)
    print(f"  Done. {len(acts) - len(failed)}/{len(acts)} acts succeeded" + (f"; failed: {failed}" if failed else ""))
    print("=" * 70)

    if args.json:
        print()
        print("--- JSON SUMMARY ---")
        print(json.dumps(SUMMARY, indent=2, default=str))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
