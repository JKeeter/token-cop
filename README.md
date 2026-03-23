# Token Cop

Cross-platform LLM token usage tracker deployed on AWS Bedrock AgentCore. An AI agent that monitors token consumption and costs across Bedrock, OpenRouter, and OpenAI, exposed as an MCP tool for Claude Code.

## Features

- **Multi-provider tracking** - Bedrock (CloudWatch), OpenRouter (REST API), OpenAI (Admin API)
- **Cost estimation** - Per-model pricing lookup with automatic model name normalization
- **Budget tracking** - Burn rate calculations and monthly projections
- **Usage snapshots** - Persist and semantically search past usage via AgentCore Memory
- **Cross-provider aggregation** - Unified view across all providers
- **MCP Gateway** - HTTPS endpoint with Cognito JWT authentication
- **Cedar policies** - Access control via AgentCore Policy Engine
- **Observability** - OTEL tracing with AgentCore evaluations

## Architecture

```
Claude Code ──► MCP Server (stdio) ──► MCP Gateway (HTTPS/JWT) ──► Lambda ──► AgentCore Runtime ──► Strands Agent
                                                                                                       │
                                                                                    ┌──────────────────┼──────────────────┐
                                                                                    ▼                  ▼                  ▼
                                                                              CloudWatch         OpenRouter API      OpenAI Admin API
                                                                            (AWS/Bedrock)
```

## Prerequisites

- AWS account with Bedrock AgentCore access
- AWS CLI configured with appropriate permissions
- Python 3.13+
- API keys for enabled providers (OpenRouter, OpenAI — optional)

### AWS Account ID Git Filter

This project uses a git clean/smudge filter to keep the AWS account ID out of version control. Files contain `<REPLACE-WITH-YOUR-AWS-ACCOUNT>` as a placeholder. Two options:

1. **Automatic** — Run the global filter setup (stores your account ID in SSM, auto-replaces on checkout):
   ```bash
   # One-time SSM setup
   aws ssm put-parameter --name /global/aws-account-id --type SecureString --value "YOUR_ACCOUNT_ID"
   # Install the filter
   bash ~/.git-filters/setup.sh
   ```
2. **Manual** — Replace `<REPLACE-WITH-YOUR-AWS-ACCOUNT>` with your 12-digit AWS account ID in:
   - `.bedrock_agentcore.yaml`
   - `mcp_server.py`
   - `scripts/setup_policies.py`

## Setup

```bash
# Clone
git clone <repo-url> && cd token-cop

# Set up git filter (or manually replace account IDs — see above)
bash ~/.git-filters/setup.sh
git checkout -- .bedrock_agentcore.yaml mcp_server.py scripts/setup_policies.py

# Virtual environment
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure API keys in SSM (optional — only for providers you use)
aws ssm put-parameter --name /token-cop/openrouter-api-key --type SecureString --value "sk-or-..."
aws ssm put-parameter --name /token-cop/openai-api-key --type SecureString --value "sk-..."

# Deploy
agentcore deploy
```

## Claude Code Integration

The MCP server is configured in the project's `.mcp.json`. Use it via:

- **Slash command**: `/tokcop What is my Bedrock usage this week?`
- **Natural language**: Ask Claude to use the `token_cop` tool

### Sample Questions

| Question | What it does |
|----------|-------------|
| "What is my Bedrock usage this week?" | Queries CloudWatch for recent Bedrock metrics |
| "Show all provider usage for the last 30 days" | Aggregates across Bedrock, OpenRouter, OpenAI |
| "Am I on track for a $500 monthly budget?" | Calculates burn rate and projects end-of-month spend |
| "Which model costs the most?" | Breaks down costs by model across providers |
| "Compare Bedrock vs OpenRouter costs" | Side-by-side provider comparison |
| "Save my current usage for later" | Persists a snapshot to AgentCore Memory |
| "What was my usage trend last month?" | Searches historical snapshots |

## Tools

| Tool | Description |
|------|-------------|
| `bedrock_usage` | CloudWatch metrics from the AWS/Bedrock namespace |
| `openrouter_usage` | Usage data from the OpenRouter REST API |
| `openai_usage` | Usage data from the OpenAI Admin API |
| `aggregate_usage` | Cross-provider rollup and comparison |
| `check_budget` | Burn rate calculation and monthly projection |
| `save_snapshot` | Persist current usage to AgentCore Memory |
| `search_history` | Semantic search over past usage snapshots |

## Project Structure

```
token-cop/
├── agent/
│   ├── app.py              # AgentCore entrypoint (BedrockAgentCoreApp)
│   ├── agent.py            # Strands Agent with system prompt
│   ├── config.py           # Configuration loading
│   ├── guardrails.py       # Output scrubbing for API keys/secrets
│   └── tracing.py          # OTEL tracing + ADOT configurator
├── tools/
│   ├── bedrock_usage.py    # AWS Bedrock CloudWatch metrics
│   ├── openrouter_usage.py # OpenRouter REST API
│   ├── openai_usage.py     # OpenAI Admin API
│   ├── aggregate.py        # Cross-provider rollup
│   ├── budget.py           # Burn rate + projection
│   └── memory_tools.py     # save_snapshot + search_history
├── models/
│   ├── schemas.py          # TokenUsageRecord dataclass
│   ├── pricing.py          # Per-model cost lookup table
│   └── normalization.py    # Model name aliases
├── memory/
│   └── store.py            # AgentCore Memory helpers
├── scripts/
│   ├── local_test.py       # Local REPL for testing
│   ├── setup_policies.py   # Cedar policy demos
│   ├── eval_demo.py        # Interactive evaluation demo
│   └── eval_regression.py  # CI regression suite
├── evaluators/
│   └── token_cop_evaluators.json
├── docs/
│   ├── mcp-gateway.md      # MCP Gateway architecture
│   ├── policies.md         # Cedar policy documentation
│   └── evaluations.md      # Evaluation framework docs
├── mcp_server.py           # MCP server for Claude Code
├── Dockerfile              # Container build
├── requirements.txt        # Python dependencies
└── .bedrock_agentcore.yaml # AgentCore deployment config
```

## Local Development

```bash
source .venv/bin/activate

# Interactive REPL
python -m scripts.local_test

# With trace output
OTEL_TRACES_EXPORTER=console python -m scripts.local_test

# AgentCore dev server
agentcore dev
```

## MCP Gateway

The agent is exposed via an MCP Gateway with Cognito JWT authentication. The `mcp_server.py` stdio server handles token refresh automatically.

Set `TOKEN_COP_BACKEND=direct` to bypass the gateway and call the AgentCore Runtime directly via boto3/IAM.

See [docs/mcp-gateway.md](docs/mcp-gateway.md) for the full architecture.

## Policies

Cedar policies control access to the MCP Gateway via the AgentCore Policy Engine. Includes demo policies for permit-all, client-restricted, and forbid scenarios.

```bash
python -m scripts.setup_policies              # Create policies (LOG_ONLY)
python -m scripts.setup_policies --demo       # Interactive walkthrough
python -m scripts.setup_policies --generate   # AI policy generation
python -m scripts.setup_policies --teardown   # Clean up
```

See [docs/policies.md](docs/policies.md) for details.

## Evaluations

AgentCore Evaluations assess agent response quality using built-in and custom evaluators.

```bash
python -m scripts.eval_demo                   # Interactive 5-act demo
python -m scripts.eval_regression             # CI regression suite (8 test cases)
python -m scripts.eval_demo --reset           # Clean slate between demos
```

See [docs/evaluations.md](docs/evaluations.md) for details.
