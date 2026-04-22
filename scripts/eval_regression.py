"""Regression test suite for Token Cop using Strands Evals + AgentCore Evaluators.

Runs Token Cop locally, captures OTel spans in-memory, evaluates each response
with AgentCore evaluators. Designed for CI or pre-deployment validation.

Usage:
    python -m scripts.eval_regression
    python -m scripts.eval_regression --cases 0,1,5
    python -m scripts.eval_regression --evaluator Builtin.Correctness
"""
import argparse
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from strands_evals import Case, Experiment
    from strands_evals.telemetry import StrandsEvalsTelemetry
except ImportError:
    print("ERROR: strands-agents-evals is not installed.")
    print("Install it with: uv pip install strands-agents-evals")
    sys.exit(1)

from dotenv import load_dotenv
load_dotenv()

REGION = "us-east-1"

# ---------------------------------------------------------------------------
# Test dataset
# ---------------------------------------------------------------------------

REGRESSION_INPUTS = [
    "What's my Bedrock usage today?",
    "Show OpenRouter spending this week",
    "Give me a summary of all providers usage",
    "Am I on track for a $500 monthly budget?",
    "Which model costs the most?",
    "How many input vs output tokens did I use on Bedrock today?",
    "Save my current usage for later comparison",
    "Compare Bedrock vs OpenRouter costs for this week",
    "Which IAM role spent the most on Bedrock last week?",
    "What is an application inference profile ARN and why does it matter for cost?",
]

REGRESSION_EXPECTED = [
    "Numerical token counts and costs for Bedrock models",
    "OpenRouter spend breakdown with dollar amounts",
    "Aggregated cross-provider summary with per-provider table",
    "Budget analysis with burn rate, projection, and on-track status",
    "Ranked model list with costs in USD",
    "Token split showing input and output token counts",
    "Confirmation that snapshot was saved to memory",
    "Side-by-side provider cost comparison with dollar amounts",
    "IAM role ARN (arn:aws:iam or arn:aws:sts) plus a dollar figure for the top Bedrock spender",
    "Explanation covering cost attribution AND tagging/routing for application inference profile ARNs",
]

# Case names for human-readable output. When omitted the index is used.
REGRESSION_NAMES = [
    "bedrock_today",
    "openrouter_week",
    "all_providers_summary",
    "monthly_budget_check",
    "most_expensive_model",
    "bedrock_io_split",
    "save_snapshot",
    "bedrock_vs_openrouter",
    "attribution_dimension",
    "inference_profile_awareness",
]


# ---------------------------------------------------------------------------
# Regex-based evaluators for the attribution regression cases.
# These complement the LLM-judge evaluators — attribution prompts need
# deterministic structural checks (does the response actually contain an
# ARN?) rather than a helpfulness rating. Returns a score in [0.0, 1.0].
# ---------------------------------------------------------------------------

_IAM_ARN_RE = re.compile(r"arn:aws:(?:iam|sts):", re.IGNORECASE)
_DOLLAR_FIGURE_RE = re.compile(r"\$\s*\d")
_COST_ATTRIBUTION_RE = re.compile(r"cost[-\s]?attribut|attribut[a-z]*\s+cost", re.IGNORECASE)
_TAG_OR_ROUTING_RE = re.compile(r"\b(tag|tagging|routing|route|inference profile)\b", re.IGNORECASE)

# Graceful-degradation phrases: when attribution isn't enabled, the agent
# should acknowledge it rather than hallucinating an ARN.
_DEGRADATION_RE = re.compile(
    r"(not (?:yet )?(?:enabled|available|configured|activated|set up)"
    r"|attribution (?:is )?(?:not|unavailable)"
    r"|enable_cur_attribution"
    r"|cost[-\s]?attribution (?:setup|disabled)"
    r"|run\s+`?scripts/enable_cur_attribution)",
    re.IGNORECASE,
)


def score_attribution_dimension(text: str) -> float:
    """Score the `Which IAM role spent the most on Bedrock last week?` case.

    Full credit (1.0) requires BOTH an IAM/STS ARN substring AND a dollar
    figure. Partial credit (0.5) for either signal alone, OR for a graceful
    acknowledgment that attribution isn't enabled in this account (matches
    the task spec's "graceful degradation" requirement). 0.0 otherwise.
    """
    body = text or ""
    has_arn = bool(_IAM_ARN_RE.search(body))
    has_dollar = bool(_DOLLAR_FIGURE_RE.search(body))
    if has_arn and has_dollar:
        return 1.0
    if has_arn or has_dollar:
        return 0.5
    if _DEGRADATION_RE.search(body):
        # Agent correctly recognizes attribution isn't enabled rather than
        # fabricating a principal — partial credit per the task spec.
        return 0.5
    return 0.0


def score_inference_profile_awareness(text: str) -> float:
    """Score the inference-profile conceptual-awareness case.

    Full credit (1.0) requires mentioning BOTH cost attribution AND
    tagging/routing. Partial credit (0.5) for either. 0.0 otherwise.
    """
    mentions_cost_attr = bool(_COST_ATTRIBUTION_RE.search(text or ""))
    mentions_tag = bool(_TAG_OR_ROUTING_RE.search(text or ""))
    if mentions_cost_attr and mentions_tag:
        return 1.0
    if mentions_cost_attr or mentions_tag:
        return 0.5
    return 0.0


# Map case index -> regex scorer. Cases not in the map are scored via the
# main LLM-judge evaluator only.
REGEX_SCORERS = {
    8: ("attribution_dimension", score_attribution_dimension),
    9: ("inference_profile_awareness", score_inference_profile_awareness),
}


def run_regression(case_indices=None, evaluator_id="Builtin.Helpfulness"):
    from bedrock_agentcore.evaluation import create_strands_evaluator
    from agent.agent import create_agent
    from agent.app import _extract_response
    from agent.guardrails import scrub_response

    print(f"Evaluator: {evaluator_id}")
    print(f"Region:    {REGION}")
    print()

    evaluator = create_strands_evaluator(evaluator_id, region=REGION)

    # Setup telemetry for span capture
    telemetry = StrandsEvalsTelemetry()
    telemetry.setup_in_memory_exporter()

    # Select cases
    indices = case_indices if case_indices is not None else list(range(len(REGRESSION_INPUTS)))
    cases = []
    for i in indices:
        case_name = REGRESSION_NAMES[i] if i < len(REGRESSION_NAMES) else f"case_{i}"
        cases.append(Case(
            session_id=str(uuid.uuid4()),
            input=REGRESSION_INPUTS[i],
            expected_output=REGRESSION_EXPECTED[i],
            name=case_name,
        ))

    print(f"Running {len(cases)} test cases...\n")

    # Capture final agent text per case for the regex pass below. Keyed by
    # case index so we can correlate with REGEX_SCORERS after the main run.
    captured_outputs: dict[int, str] = {}
    case_to_index = {id(c): idx for c, idx in zip(cases, indices)}

    def task_fn(case):
        """Run the agent and return output + trajectory for evaluation."""
        # Clear previous spans
        if hasattr(telemetry, '_in_memory_exporter') and telemetry._in_memory_exporter:
            telemetry._in_memory_exporter.clear()

        agent = create_agent()
        response = agent(case.input)
        text = scrub_response(_extract_response(agent, response))

        # Stash the output for the post-run regex evaluator pass
        idx = case_to_index.get(id(case))
        if idx is not None:
            captured_outputs[idx] = text

        # Collect spans
        spans = []
        if hasattr(telemetry, '_in_memory_exporter') and telemetry._in_memory_exporter:
            spans = list(telemetry._in_memory_exporter.get_finished_spans())

        return {"output": text, "trajectory": spans}

    # Run experiment
    experiment = Experiment(cases=cases, evaluators=[evaluator])

    try:
        reports = experiment.run_evaluations(task_fn)
    except Exception as e:
        print(f"Experiment failed: {e}")
        print()
        print("Falling back to manual evaluation...")
        print()
        _run_manual(cases, indices, evaluator)
        return

    # Print reports
    for report in reports:
        print(f"Evaluator: {report.evaluator_id if hasattr(report, 'evaluator_id') else evaluator_id}")
        if hasattr(report, 'scores') and report.scores:
            for i, (case, score) in enumerate(zip(cases, report.scores)):
                passed = score >= 0.7
                status = "PASS" if passed else "FAIL"
                idx = indices[i]
                print(f"  [{status}] ({score:.2f}) Case {idx}: {REGRESSION_INPUTS[idx][:55]}...")
            avg = sum(report.scores) / len(report.scores)
            passed_count = sum(1 for s in report.scores if s >= 0.7)
            print()
            print(f"  {passed_count}/{len(cases)} PASS | Average score: {avg:.2f}")
        else:
            print(f"  Report: {report}")
    print()

    # Regex-based evaluator pass for attribution regression cases.
    # These use deterministic structural checks (ARN substring, dollar
    # figure, tagging/routing keywords) to complement the LLM-judge scores.
    # Partial credit means a degraded response is reported as PARTIAL rather
    # than a hard FAIL so "attribution not yet enabled" doesn't break CI.
    _run_regex_pass(captured_outputs, indices)


def _run_regex_pass(captured_outputs: dict, indices: list) -> None:
    """Score attribution cases with regex evaluators and print results."""
    applicable = [
        (idx, REGEX_SCORERS[idx]) for idx in indices if idx in REGEX_SCORERS
    ]
    if not applicable:
        return

    print("Regex Evaluators (attribution cases):")
    for idx, (name, scorer) in applicable:
        text = captured_outputs.get(idx, "")
        score = scorer(text)
        if score >= 1.0:
            status = "PASS"
        elif score > 0.0:
            status = "PARTIAL"
        else:
            status = "FAIL"
        print(
            f"  [{status}] ({score:.2f}) Case {idx} [{name}]: "
            f"{REGRESSION_INPUTS[idx][:55]}..."
        )
    print()


def _run_manual(cases, indices, evaluator):
    """Fallback: run each case individually without the Experiment wrapper."""
    from agent.agent import create_agent
    from agent.app import _extract_response
    from agent.guardrails import scrub_response

    results = []
    captured_outputs: dict[int, str] = {}
    for i, (case, idx) in enumerate(zip(cases, indices)):
        query = REGRESSION_INPUTS[idx]
        print(f"  Case {idx}: {query[:60]}...")

        agent = create_agent()
        response = agent(query)
        text = scrub_response(_extract_response(agent, response))
        captured_outputs[idx] = text

        auto_saved = any(
            "save_snapshot" in str(block.get("toolUse", {}).get("name", ""))
            for msg in agent.messages
            for block in msg.get("content", [])
            if isinstance(block, dict)
        )

        has_data = len(text) > 100
        status = "PASS" if has_data else "FAIL"
        print(f"    {status} | len={len(text)} | auto_saved={auto_saved}")
        if not has_data:
            print(f"    Response: {text[:150]}")
        print()

        results.append({"status": status, "has_data": has_data, "auto_saved": auto_saved})

    total = len(results)
    passed = sum(1 for r in results if r["has_data"])
    auto_saves = sum(1 for r in results if r["auto_saved"])

    print("=" * 60)
    print(f"  Results: {passed}/{total} PASS")
    print(f"  Auto-saves triggered: {auto_saves}/{total}")
    print("=" * 60)

    # Regex evaluator pass (attribution cases) — same as the main path.
    _run_regex_pass(captured_outputs, indices)


def main():
    parser = argparse.ArgumentParser(description="Token Cop Regression Suite")
    parser.add_argument(
        "--cases", help="Comma-separated case indices (e.g., 0,1,5)"
    )
    parser.add_argument(
        "--evaluator", default="Builtin.Helpfulness",
        help="Evaluator ID (default: Builtin.Helpfulness)"
    )
    args = parser.parse_args()

    case_indices = None
    if args.cases:
        case_indices = [int(x) for x in args.cases.split(",")]

    print()
    print("Token Cop Regression Suite")
    print("-" * 40)
    print()

    run_regression(case_indices=case_indices, evaluator_id=args.evaluator)


if __name__ == "__main__":
    main()
