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
]


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
        cases.append(Case(
            session_id=str(uuid.uuid4()),
            input=REGRESSION_INPUTS[i],
            expected_output=REGRESSION_EXPECTED[i],
            name=f"case_{i}",
        ))

    print(f"Running {len(cases)} test cases...\n")

    def task_fn(case):
        """Run the agent and return output + trajectory for evaluation."""
        # Clear previous spans
        if hasattr(telemetry, '_in_memory_exporter') and telemetry._in_memory_exporter:
            telemetry._in_memory_exporter.clear()

        agent = create_agent()
        response = agent(case.input)
        text = scrub_response(_extract_response(agent, response))

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


def _run_manual(cases, indices, evaluator):
    """Fallback: run each case individually without the Experiment wrapper."""
    from agent.agent import create_agent
    from agent.app import _extract_response
    from agent.guardrails import scrub_response

    results = []
    for i, (case, idx) in enumerate(zip(cases, indices)):
        query = REGRESSION_INPUTS[idx]
        print(f"  Case {idx}: {query[:60]}...")

        agent = create_agent()
        response = agent(query)
        text = scrub_response(_extract_response(agent, response))

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
