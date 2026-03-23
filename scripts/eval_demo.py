"""AgentCore Evaluations Demo for Token Cop.

A progressive 5-act demo showing how AgentCore Evaluations catches quality
issues in deployed agents. Uses real session data from Token Cop.

Usage:
    python -m scripts.eval_demo              # Full demo (all 5 acts)
    python -m scripts.eval_demo --act 2      # Run specific act
    python -m scripts.eval_demo --session-id X  # Use specific session
    python -m scripts.eval_demo --no-cleanup    # Skip cleanup prompt
    python -m scripts.eval_demo --reset         # Delete custom evaluators & online configs
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REGION = "us-east-1"
AGENT_ID = "token_cop-Mu4TjQBJoH"
EVALUATORS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "evaluators",
    "token_cop_evaluators.json",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def header(title, act=None):
    width = 70
    print()
    print("=" * width)
    if act:
        print(f"  Act {act}: {title}")
    else:
        print(f"  {title}")
    print("=" * width)
    print()


def cli_hint(cmd):
    print(f"  CLI equivalent:")
    print(f"    {cmd}")
    print()


def wait_for_enter(msg="Press Enter to continue..."):
    try:
        input(f"\n  {msg}")
    except (EOFError, KeyboardInterrupt):
        print()


def print_result(result):
    evaluator = result.get("evaluatorId") or result.get("evaluator_id", "?")
    score = result.get("value")
    explanation = result.get("explanation", "")
    error = result.get("error")

    if error:
        print(f"  {evaluator}: ERROR - {error}")
        return

    score_str = f"{score:.2f}" if score is not None else "N/A"
    print(f"  {evaluator}: score={score_str}")
    if explanation:
        # Wrap long explanations
        lines = explanation.split("\n")
        for line in lines[:4]:
            print(f"    {line[:100]}")
        if len(lines) > 4:
            print(f"    ... ({len(lines) - 4} more lines)")
    print()


# ---------------------------------------------------------------------------
# Control plane helpers
# ---------------------------------------------------------------------------

def get_control_plane():
    from bedrock_agentcore_starter_toolkit.operations.evaluation.control_plane_client import (
        EvaluationControlPlaneClient,
    )
    return EvaluationControlPlaneClient(region_name=REGION)


def get_processor():
    from bedrock_agentcore_starter_toolkit.operations.evaluation.on_demand_processor import (
        EvaluationProcessor,
    )
    from bedrock_agentcore_starter_toolkit.operations.evaluation.data_plane_client import (
        EvaluationDataPlaneClient,
    )
    dp = EvaluationDataPlaneClient(region_name=REGION)
    cp = get_control_plane()
    return EvaluationProcessor(data_plane_client=dp, control_plane_client=cp)


def find_custom_evaluator(cp, name):
    """Return evaluator ID if a custom evaluator with the given name exists."""
    resp = cp.list_evaluators(max_results=100)
    for ev in resp.get("evaluators", []):
        if ev.get("evaluatorName") == name:
            return ev.get("evaluatorId")
    return None


def find_online_config(cp, name):
    """Return config ID if an online eval config with the given name exists."""
    try:
        resp = cp.list_online_evaluation_configs(max_results=50)
    except Exception:
        resp = cp.client.list_online_evaluation_configs(maxResults=50)
    for cfg in resp.get("onlineEvaluationConfigs", []):
        if cfg.get("onlineEvaluationConfigName") == name:
            return cfg.get("onlineEvaluationConfigId")
    return None


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------

def discover_sessions(days=7):
    """Find recent session IDs from CloudWatch via the evaluation processor."""
    processor = get_processor()
    try:
        session_id = processor.get_latest_session(agent_id=AGENT_ID, region=REGION)
        if session_id:
            return [session_id]
    except Exception as e:
        print(f"  Warning: session discovery failed: {e}")
    return []


# ---------------------------------------------------------------------------
# Act 1: The Bug
# ---------------------------------------------------------------------------

def act1():
    header("The Bug We Caught Manually", act=1)

    print("  Token Cop is a Strands Agent deployed on Bedrock AgentCore that tracks")
    print("  LLM token usage across AWS Bedrock, OpenRouter, and OpenAI.")
    print()
    print("  After deployment, we noticed something wrong: queries were returning")
    print("  empty responses or just 'Snapshot saved' instead of actual data.")
    print()
    print("  THE BUG: The system prompt told the agent to call save_snapshot after")
    print("  every query. The Strands AgentResult.__str__() only returns text from")
    print("  the LAST message -- so when save_snapshot was the last tool call,")
    print("  the response was just a save confirmation or empty string.")
    print()

    print("  Before fix (what users saw):")
    print("  ┌─────────────────────────────────────────────────────────────┐")
    print('  │  User: "What is my Bedrock usage today?"                   │')
    print('  │  Agent: ""                                                 │')
    print("  │                                                            │")
    print('  │  User: "Show OpenRouter costs this week"                   │')
    print('  │  Agent: "Your usage data has been saved for trend analysis"│')
    print("  └─────────────────────────────────────────────────────────────┘")
    print()

    print("  After fix (what users see now):")
    print("  ┌─────────────────────────────────────────────────────────────┐")
    print('  │  User: "What is my Bedrock usage today?"                   │')
    print("  │  Agent: 865,742 tokens | $16.58 | 3-model breakdown table  │")
    print("  └─────────────────────────────────────────────────────────────┘")
    print()

    print("  This bug went undetected through 50+ invocations.")
    print()
    print("  >> AgentCore Evaluations would have caught this automatically. <<")
    print("  >> Let's see how.                                              <<")


# ---------------------------------------------------------------------------
# Act 2: Built-in Evaluators
# ---------------------------------------------------------------------------

def act2(session_id=None):
    header("Built-in Evaluators", act=2)

    # Discover session
    if not session_id:
        print("  Discovering recent sessions from CloudWatch...")
        sessions = discover_sessions()
        if not sessions:
            print("  ERROR: No sessions found. Run some /tokcop queries first,")
            print("  wait 3-5 minutes for span ingestion, then re-run.")
            return None
        session_id = sessions[0]

    print(f"  Using session: {session_id}")
    print()

    evaluators = [
        "Builtin.Helpfulness",
        "Builtin.ResponseRelevance",
        "Builtin.GoalSuccessRate",
        "Builtin.ToolSelectionAccuracy",
    ]

    print(f"  Running {len(evaluators)} built-in evaluators:")
    for e in evaluators:
        print(f"    - {e}")
    print()

    cli_hint(
        f"agentcore eval run -s {session_id} "
        + " ".join(f"-e {e}" for e in evaluators)
    )

    # Run via SDK
    processor = get_processor()

    print("  Evaluating (this may take 30-60 seconds)...")
    print()

    try:
        results = processor.evaluate_session(
            session_id=session_id,
            evaluators=evaluators,
            agent_id=AGENT_ID,
            region=REGION,
        )
        if results.results:
            print("  Results:")
            print("  " + "-" * 60)
            for r in results.results:
                print_result(r.to_dict() if hasattr(r, "to_dict") else vars(r))
        else:
            print("  No evaluation results returned.")
            print("  Session spans may not have been ingested yet.")
            print("  Wait a few minutes and try again.")
    except Exception as e:
        print(f"  Evaluation failed: {e}")

    return session_id


# ---------------------------------------------------------------------------
# Act 3: Custom Evaluators
# ---------------------------------------------------------------------------

def act3(session_id=None):
    header("Custom Evaluators", act=3)

    if not session_id:
        sessions = discover_sessions()
        if not sessions:
            print("  ERROR: No sessions found.")
            return
        session_id = sessions[0]

    # Load evaluator definitions
    with open(EVALUATORS_FILE) as f:
        eval_defs = json.load(f)["evaluators"]

    cp = get_control_plane()
    created_ids = []

    for defn in eval_defs:
        name = defn["name"]
        print(f"  Custom evaluator: {name}")

        # Check if already exists
        existing_id = find_custom_evaluator(cp, name)
        if existing_id:
            print(f"    Already exists: {existing_id} (reusing)")
            created_ids.append(existing_id)
        else:
            print(f"    Creating...")
            try:
                resp = cp.create_evaluator(
                    name=name,
                    config=defn["config"],
                    level=defn["level"],
                    description=defn["description"],
                )
                eid = resp.get("evaluatorId", "?")
                print(f"    Created: {eid}")
                created_ids.append(eid)
            except Exception as e:
                print(f"    Failed to create: {e}")
        print()

    if not created_ids:
        print("  No custom evaluators available to run.")
        return

    cli_hint(
        f"agentcore eval run -s {session_id} "
        + " ".join(f"-e {eid}" for eid in created_ids)
    )

    # Run custom evaluators
    processor = get_processor()

    print("  Running custom evaluators against session...")
    print()

    try:
        results = processor.evaluate_session(
            session_id=session_id,
            evaluators=created_ids,
            agent_id=AGENT_ID,
            region=REGION,
        )
        if results.results:
            print("  Results:")
            print("  " + "-" * 60)
            for r in results.results:
                print_result(r.to_dict() if hasattr(r, "to_dict") else vars(r))
        else:
            print("  No results yet. Session spans may need a few minutes to ingest.")
    except Exception as e:
        print(f"  Evaluation failed: {e}")

    print()
    print("  The data_completeness evaluator is the key one: it would have")
    print("  scored 0.0 on every response that just said 'Snapshot saved'")
    print("  without actual data -- catching the bug automatically.")


# ---------------------------------------------------------------------------
# Act 4: Online Evaluation
# ---------------------------------------------------------------------------

def act4():
    header("Online Evaluation (Continuous Monitoring)", act=4)

    config_name = "token_cop_quality_monitor"
    cp = get_control_plane()

    # Check if already exists
    existing = find_online_config(cp, config_name)
    if existing:
        print(f"  Online evaluation config already exists: {existing}")
        print("  Showing current status...")
        print()
        try:
            cfg = cp.client.get_online_evaluation_config(
                onlineEvaluationConfigId=existing
            )
            print(f"    Name:          {cfg.get('onlineEvaluationConfigName', '?')}")
            print(f"    Status:        {cfg.get('status', '?')}")
            print(f"    Sampling Rate: {cfg.get('samplingRate', '?')}%")
            evaluators = cfg.get("evaluatorList", [])
            print(f"    Evaluators:    {len(evaluators)}")
            for e in evaluators:
                print(f"      - {e}")
        except Exception as e:
            print(f"    Could not fetch details: {e}")
        print()
        return

    # Collect evaluator IDs (builtin + any custom ones)
    evaluator_ids = [
        "Builtin.Helpfulness",
        "Builtin.ResponseRelevance",
    ]

    # Try to include custom data_completeness evaluator
    custom_id = find_custom_evaluator(cp, "token_cop_data_completeness")
    if custom_id:
        evaluator_ids.append(custom_id)

    print(f"  Creating online evaluation config: {config_name}")
    print(f"    Agent:         {AGENT_ID}")
    print(f"    Sampling Rate: 100% (demo)")
    print(f"    Evaluators:    {len(evaluator_ids)}")
    for e in evaluator_ids:
        print(f"      - {e}")
    print()

    cli_hint(
        f"agentcore eval online create "
        f"-a token_cop -n {config_name} --sampling-rate 100 "
        + " ".join(f"-e {e}" for e in evaluator_ids)
    )

    try:
        resp = cp.create_online_evaluation_config(
            config_name=config_name,
            agent_id=AGENT_ID,
            sampling_rate=100.0,
            evaluator_list=evaluator_ids,
            config_description="Continuous quality monitoring for Token Cop demo",
        )
        config_id = resp.get("onlineEvaluationConfigId", "?")
        print(f"  Created: {config_id}")
        print()
        print("  With this running at 100% sampling, every invocation is evaluated.")
        print("  The data_completeness evaluator would have flagged the save_snapshot")
        print("  bug within the first few calls -- scoring 0.0 on empty responses.")
    except Exception as e:
        print(f"  Failed to create online config: {e}")
        print("  This may require additional IAM permissions.")


# ---------------------------------------------------------------------------
# Act 5: Summary & Cleanup
# ---------------------------------------------------------------------------

def act5(no_cleanup=False):
    header("Summary", act=5)

    print("  What we demonstrated:")
    print("  ┌──────────────────────────────────────────────────────────────┐")
    print("  │ 1. Real bug story    - save_snapshot swallowing responses   │")
    print("  │ 2. Built-in evals    - Helpfulness, Relevance, GoalSuccess │")
    print("  │ 3. Custom evaluators - data_completeness, cost_formatting  │")
    print("  │ 4. Online evaluation - continuous monitoring at 100%       │")
    print("  └──────────────────────────────────────────────────────────────┘")
    print()

    print("  CLI Reference:")
    print("    agentcore eval evaluator list              # List all evaluators")
    print("    agentcore eval run -e Builtin.Helpfulness  # On-demand evaluation")
    print("    agentcore eval online list                 # List online configs")
    print("    agentcore eval online create ...           # Set up continuous eval")
    print()

    if no_cleanup:
        print("  Skipping cleanup (--no-cleanup).")
        return

    try:
        answer = input("  Clean up demo resources? (custom evaluators + online config) [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if answer.strip().lower() == "y":
        do_reset()


# ---------------------------------------------------------------------------
# Reset / Cleanup
# ---------------------------------------------------------------------------

def do_reset():
    """Delete custom evaluators and online configs created by the demo."""
    print()
    print("  Cleaning up demo resources...")
    cp = get_control_plane()

    # Delete online config FIRST (evaluators are locked while in use)
    config_name = "token_cop_quality_monitor"
    cid = find_online_config(cp, config_name)
    if cid:
        try:
            cp.delete_online_evaluation_config(config_id=cid)
            print(f"    Deleted online config: {config_name} ({cid})")
        except Exception as e:
            print(f"    Failed to delete {config_name}: {e}")
    else:
        print(f"    Online config not found: {config_name} (already deleted)")

    # Then delete custom evaluators
    with open(EVALUATORS_FILE) as f:
        eval_defs = json.load(f)["evaluators"]

    for defn in eval_defs:
        eid = find_custom_evaluator(cp, defn["name"])
        if eid:
            try:
                cp.delete_evaluator(evaluator_id=eid)
                print(f"    Deleted evaluator: {defn['name']} ({eid})")
            except Exception as e:
                print(f"    Failed to delete {defn['name']}: {e}")
        else:
            print(f"    Evaluator not found: {defn['name']} (already deleted)")

    print()
    print("  Reset complete. Ready for a fresh demo run.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AgentCore Evaluations Demo")
    parser.add_argument("--act", type=int, choices=[1, 2, 3, 4, 5], help="Run specific act only")
    parser.add_argument("--session-id", help="Use specific session ID")
    parser.add_argument("--no-cleanup", action="store_true", help="Skip cleanup prompt")
    parser.add_argument("--reset", action="store_true", help="Delete demo resources and exit")
    args = parser.parse_args()

    if args.reset:
        header("Reset Demo Resources")
        do_reset()
        return

    session_id = args.session_id

    if args.act:
        if args.act == 1:
            act1()
        elif args.act == 2:
            act2(session_id)
        elif args.act == 3:
            act3(session_id)
        elif args.act == 4:
            act4()
        elif args.act == 5:
            act5(args.no_cleanup)
        return

    # Full demo
    header("AgentCore Evaluations Demo for Token Cop")
    print("  This demo walks through 5 acts showing how AgentCore Evaluations")
    print("  catches quality issues in deployed agents, using a real bug story.")
    print()

    act1()
    wait_for_enter()

    session_id = act2(session_id)
    wait_for_enter()

    act3(session_id)
    wait_for_enter()

    act4()
    wait_for_enter()

    act5(args.no_cleanup)


if __name__ == "__main__":
    main()
