# AgentCore Evaluations Demo

## Overview

This demo showcases AgentCore Evaluations using Token Cop, a real agent that
tracks LLM token usage across AWS Bedrock, OpenRouter, and OpenAI.

**Narrative**: We deployed Token Cop and discovered a production bug — the agent
was auto-calling `save_snapshot` after every query, causing responses to return
empty strings or save confirmations instead of actual usage data. This went
undetected through 50+ invocations until caught manually. AgentCore Evaluations
would have flagged it automatically within the first few calls.

**The demo has 5 acts:**

| Act | Title | What it shows |
|-----|-------|---------------|
| 1 | The Bug | Motivating story — before/after the fix |
| 2 | Built-in Evaluators | On-demand evaluation with Helpfulness, ResponseRelevance, GoalSuccessRate, ToolSelectionAccuracy |
| 3 | Custom Evaluators | Domain-specific evaluators: data_completeness, cost_formatting |
| 4 | Online Evaluation | Continuous monitoring setup at 100% sampling |
| 5 | Summary & Cleanup | Recap + CLI reference + optional cleanup |

## Prerequisites

- AWS credentials configured (`aws configure` or env vars)
- Token Cop deployed to AgentCore (`agentcore deploy`)
- Virtual environment activated: `source .venv/bin/activate`
- Recent session data in CloudWatch (run a few `/tokcop` queries first)

For the regression suite only:
```bash
uv pip install strands-agents-evals
```

## Quick Start

```bash
source .venv/bin/activate

# Run the full interactive demo
python -m scripts.eval_demo

# Or run a specific act
python -m scripts.eval_demo --act 2

# Run the regression suite
python -m scripts.eval_regression
```

## Running the Demo

### Full Demo

```bash
python -m scripts.eval_demo
```

Walks through all 5 acts with pauses between each. Press Enter to advance.
Takes ~5 minutes total (most time spent waiting for evaluation API calls).

### Specific Act

```bash
python -m scripts.eval_demo --act 2
```

Useful for re-showing a particular section without running the full demo.

### Custom Session

```bash
python -m scripts.eval_demo --session-id <SESSION_ID>
```

Pin a specific session for consistent results. By default, the demo
auto-discovers the most recent session from CloudWatch.

To find available sessions:
```bash
agentcore eval run  # shows latest session in output
```

### Skip Cleanup

```bash
python -m scripts.eval_demo --no-cleanup
```

Keeps custom evaluators and online config after the demo ends. Useful when
you want to inspect them in the console afterward.

## Resetting Between Demos

### Quick Reset

```bash
python -m scripts.eval_demo --reset
```

Deletes:
- Custom evaluators (`token_cop_data_completeness`, `token_cop_cost_formatting`)
- Online evaluation config (`token_cop_quality_monitor`)

Safe to run multiple times. Idempotent — reports "not found" if already deleted.

### Manual Reset

```bash
# List custom evaluators
agentcore eval evaluator list

# Delete specific evaluator
agentcore eval evaluator delete --evaluator-id <ID> --force

# List online configs
agentcore eval online list

# Delete online config
agentcore eval online delete --config-id <ID> --force
```

### Generating Fresh Session Data

If CloudWatch sessions have expired or you want fresh data:

```bash
# Run 3-5 queries to generate sessions
# (from Claude Code, or via the MCP tool)
/tokcop What is my Bedrock usage today?
/tokcop Am I on track for a $500 monthly budget?
/tokcop Compare Bedrock vs OpenRouter costs this week

# Wait 3-5 minutes for CloudWatch span ingestion
# Then run the demo
python -m scripts.eval_demo
```

## Running the Regression Suite

The regression suite runs Token Cop locally (not through the gateway), captures
OTel spans in-memory, and evaluates each response with AgentCore evaluators.

### Install

```bash
uv pip install strands-agents-evals
```

### Run

```bash
# Full suite (8 test cases)
python -m scripts.eval_regression

# Specific cases only
python -m scripts.eval_regression --cases 0,1,5

# Use a different evaluator
python -m scripts.eval_regression --evaluator Builtin.Correctness
```

### Interpreting Results

Each case shows:
- **PASS/FAIL** — based on the evaluator's threshold (default 0.7)
- **score** — 0.0 to 1.0 quality score
- **len** — response text length (short responses may indicate bugs)
- **auto_saved** — whether the agent called save_snapshot (should be False
  except for case 6 which explicitly asks to save)

Example output:
```
  Case 0: What's my Bedrock usage today?...
    PASS | score=0.95 | len=916 | auto_saved=False

  Case 6: Save my current usage for later comparison...
    PASS | score=0.80 | len=150 | auto_saved=True
```

## Custom Evaluator Reference

Custom evaluator definitions are in `evaluators/token_cop_evaluators.json`.

### token_cop_data_completeness (TRACE level)

**Purpose**: Catches the save_snapshot bug. Checks that the agent's response
contains actual numerical data (token counts, costs, tables) rather than just
save confirmations or empty responses.

| Score | Meaning |
|-------|---------|
| 1.0 | Response contains specific numerical data answering the question |
| 0.5 | Response has some data but is incomplete or vague |
| 0.0 | No data — just a save confirmation, empty, or generic follow-up |

### token_cop_cost_formatting (TRACE level)

**Purpose**: Validates that costs follow the formatting rules in the system prompt.

| Score | Meaning |
|-------|---------|
| 1.0 | All costs formatted as $X.XX with commas, labeled as estimated |
| 0.5 | Costs present but formatting inconsistent |
| 0.0 | No costs shown when expected, or wrong format |

### Adding New Evaluators

1. Add a new entry to `evaluators/token_cop_evaluators.json`
2. Run `python -m scripts.eval_demo --act 3` to create it
3. It will be picked up automatically on next demo run

## CLI Command Reference

```bash
# List all evaluators (built-in + custom)
agentcore eval evaluator list

# Get evaluator details
agentcore eval evaluator get --evaluator-id <ID>

# Create custom evaluator from JSON
agentcore eval evaluator create --name NAME --config config.json --level TRACE

# Run on-demand evaluation
agentcore eval run -s <SESSION_ID> -e Builtin.Helpfulness -e Builtin.Correctness

# Run with output file
agentcore eval run -s <SESSION_ID> -e Builtin.Helpfulness -o results.json

# Create online (continuous) evaluation
agentcore eval online create \
    -a token_cop \
    -n my-monitor \
    --sampling-rate 100 \
    -e Builtin.Helpfulness

# List/manage online configs
agentcore eval online list
agentcore eval online get --config-id <ID>
agentcore eval online delete --config-id <ID> --force
```

## OTEL Span Export (Deployed Agents)

For on-demand evaluation of deployed sessions, Strands GenAI spans must
reach the `aws/spans` CloudWatch log group. This requires:

1. **CloudWatch Transaction Search** enabled (X-Ray writes spans to `aws/spans`)
2. **`aws-opentelemetry-distro`** in requirements.txt
3. **Manual ADOT configuration** in `agent/tracing.py`

### Why manual configuration?

The AgentCore platform sets OTEL env vars (`OTEL_PYTHON_DISTRO=aws_distro`,
`OTEL_PYTHON_CONFIGURATOR=aws_configurator`) but **overrides the Dockerfile
CMD**, so `opentelemetry-instrument` never runs. The platform also omits
`OTEL_TRACES_EXPORTER` and `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`.

`tracing.py` detects this state and manually:
- Sets `OTEL_TRACES_EXPORTER=otlp`
- Sets `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://xray.{region}.amazonaws.com/v1/traces`
  (the `/v1/traces` path is required for the ADOT regex to select `OTLPAwsSpanExporter` with SigV4)
- Sets `AGENT_OBSERVABILITY_ENABLED=false` to prevent the LLO handler from
  stripping GenAI events out of spans (the evaluation API needs them)
- Runs `AwsOpenTelemetryConfigurator().configure()` to set up the TracerProvider
- Adds `_SessionIdSpanProcessor` to inject `session.id` from `BedrockAgentCoreContext`
  into every span (required for evaluation API's CloudWatch query)

### Resulting pipeline

```
Strands Agent → GenAI spans (chat, execute_tool, events)
  → OTLPAwsSpanExporter (SigV4 signed)
  → X-Ray OTLP endpoint
  → CloudWatch Transaction Search
  → aws/spans log group
  → Evaluation API queries by session.id
```

### Verified results

Online evaluation produces scores across all 9 built-in evaluators.
Example scores for a Bedrock usage query:

| Evaluator | Score | Label |
|-----------|-------|-------|
| Correctness | 1.0 | Perfectly Correct |
| ResponseRelevance | 1.0 | Completely Yes |
| Faithfulness | 1.0 | Completely Yes |
| GoalSuccessRate | 1.0 | Yes |
| Helpfulness | 0.83 | Very Helpful |

Both on-demand (`agentcore eval run`) and online evaluation produce scores.
Results are visible in the CloudWatch GenAI Observability dashboard and
written to the output log group configured in the online evaluation config.

## Troubleshooting

### "No sessions found"

CloudWatch spans take 3-5 minutes to appear after invocations.
Run some `/tokcop` queries, wait, then retry.

Or specify a known session:
```bash
python -m scripts.eval_demo --session-id <SESSION_ID>
```

### "Permission denied" or IAM errors

The demo needs these permissions:
- `bedrock-agentcore:*Evaluator*` — create/list/delete evaluators
- `bedrock-agentcore:*OnlineEvaluationConfig*` — manage online configs
- `logs:StartQuery`, `logs:GetQueryResults` — CloudWatch Logs Insights
- `iam:CreateRole`, `iam:AttachRolePolicy` — for online eval execution role

### "Evaluator already exists"

Run reset first:
```bash
python -m scripts.eval_demo --reset
python -m scripts.eval_demo
```

### Evaluation scores seem wrong

Scores depend on what's in the session being evaluated. If you're evaluating
a session from before the bug fix, expect low scores (that's the point). For
post-fix sessions, scores should be high.

### Regression suite is slow

Each case creates a fresh agent and calls real Bedrock APIs. The full suite
(8 cases) takes ~2-5 minutes. Use `--cases` to run a subset:
```bash
python -m scripts.eval_regression --cases 0,3,4
```
