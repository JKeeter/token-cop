# Token Cop

Cross-platform LLM token usage tracker deployed on AWS Bedrock AgentCore. An AI agent that monitors token consumption and costs across Bedrock, OpenRouter, and OpenAI, exposed as an MCP tool for Claude Code.

## Features

### Usage Tracking
- **Multi-provider tracking** - Bedrock (CloudWatch), OpenRouter (REST API), OpenAI (Admin API)
- **Cost estimation** - Per-model pricing lookup with automatic model name normalization
- **Budget tracking** - Burn rate calculations and monthly projections
- **Usage snapshots** - Persist and semantically search past usage via AgentCore Memory
- **Cross-provider aggregation** - Unified view across all providers

### Smart Token Management (v2)
- **Heavy file ingestion** - Auto-converts PDF, DOCX, PPTX, XLSX to markdown/CSV before Claude reads them (10-100x token savings)
- **Smart model router** - Classifies tasks into reasoning/execution/polish tiers and recommends the most cost-effective model
- **Token audit** - Scores usage across 6 dimensions (document ingestion, model mix, cache utilization, cost concentration, efficiency trend, savings opportunities) with A-F grades
- **Context audit** - Inspects Claude Code environment for bloat: CLAUDE.md weight, MCP servers, skill/plugin tax, pruning recommendations
- **Team dashboard** - Streamlit app with org-wide spend overview, per-model efficiency analysis, and optimization recommendations
- **Weekly reports** - Markdown/JSON reports for Slack/email with efficiency grades and top recommendations

### Infrastructure
- **MCP Gateway** - HTTPS endpoint with Cognito JWT authentication
- **Cedar policies** - Access control via AgentCore Policy Engine
- **Observability** - OTEL tracing with AgentCore evaluations
- **Claude Code hooks** - PreToolUse hook intercepts binary file reads

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
| `recommend_model` | Classify a task and recommend the best model tier |
| `token_audit` | Score usage efficiency across 6 dimensions (A-F grade) |
| `context_audit` | Inspect Claude Code environment for context bloat (local only) |

## Project Structure

```
token-cop/
├── agent/
│   ├── app.py              # AgentCore entrypoint (BedrockAgentCoreApp)
│   ├── agent.py            # Strands Agent with system prompt + efficiency advisor
│   ├── config.py           # Configuration loading
│   ├── guardrails.py       # Output scrubbing for API keys/secrets
│   └── tracing.py          # OTEL tracing + ADOT configurator
├── tools/
│   ├── bedrock_usage.py    # AWS Bedrock CloudWatch metrics
│   ├── openrouter_usage.py # OpenRouter REST API
│   ├── openai_usage.py     # OpenAI Admin API
│   ├── aggregate.py        # Cross-provider rollup
│   ├── budget.py           # Burn rate + projection
│   ├── memory_tools.py     # save_snapshot + search_history
│   ├── model_router.py     # Smart model tier recommendations
│   ├── audit.py            # Token efficiency audit (6 dimensions)
│   └── context_audit.py    # Claude Code environment bloat detection
├── models/
│   ├── schemas.py          # TokenUsageRecord dataclass
│   ├── pricing.py          # Per-model cost lookup table
│   ├── normalization.py    # Model name aliases
│   └── model_tiers.py      # Reasoning/execution/polish tier definitions
├── dashboard/
│   ├── app.py              # Streamlit dashboard entry point
│   ├── auth.py             # Password auth (Cognito-upgradeable)
│   ├── data.py             # Data aggregation layer
│   └── views/
│       ├── overview.py     # Team spend, grade, model mix charts
│       ├── per_user.py     # Per-model efficiency analysis
│       └── recommendations.py  # Prioritized optimization advice
├── memory/
│   └── store.py            # AgentCore Memory helpers
├── scripts/
│   ├── local_test.py       # Local REPL for testing
│   ├── convert_heavy_file.py  # Document → markdown/CSV converter
│   ├── check_heavy_file.py    # Claude Code hook helper
│   ├── generate_report.py     # Weekly efficiency report generator
│   ├── setup_policies.py      # Cedar policy demos
│   ├── eval_demo.py           # Interactive evaluation demo
│   └── eval_regression.py     # CI regression suite
├── skills/
│   ├── heavy-file-ingestion/SKILL.md  # Document conversion skill
│   └── token-audit/SKILL.md           # /tokcop-audit skill
├── evaluators/
│   └── token_cop_evaluators.json
├── docs/
│   ├── mcp-gateway.md      # MCP Gateway architecture
│   ├── policies.md         # Cedar policy documentation
│   └── evaluations.md      # Evaluation framework docs
├── mcp_server.py           # MCP server for Claude Code (+ context audit)
├── .claude/settings.json   # Hook: intercept binary file reads
├── Dockerfile              # Container build
├── requirements.txt        # Python dependencies
└── .bedrock_agentcore.yaml # AgentCore deployment config
```

## Smart Token Management

*More tokens is FINE — they need to be SMART tokens.*

### Document Conversion

Convert binary documents to markdown/CSV before Claude reads them:

```bash
# Convert a single file
python scripts/convert_heavy_file.py report.pdf

# Specify output directory
python scripts/convert_heavy_file.py deck.pptx --output-dir ./converted

# Choose conversion strategy
python scripts/convert_heavy_file.py data.xlsx --prefer native
```

Supported formats: PDF, DOCX, PPTX, XLSX. Output goes to `<filename>.converted/` with:
- Converted artifacts (`.md` or `.csv`)
- `index.json` — metadata, compression ratio, quality flags
- `index.md` — human-readable summary with preview

The Claude Code hook (`.claude/settings.json`) automatically intercepts binary file reads and prompts conversion.

### Model Router

Ask Token Cop which model tier fits your task:

```
/tokcop Should I use Opus or Sonnet to reformat this JSON?
```

Three tiers: **Reasoning** (Opus — architecture, debugging), **Execution** (Sonnet — code gen, data processing), **Polish** (Haiku — formatting, summarizing).

### Token Audit

Run a comprehensive efficiency audit:

```
/tokcop Run a token audit for the last 7 days
```

Scores 6 dimensions: document ingestion, model mix, cache utilization, cost concentration, efficiency trend, and top savings opportunity. Returns an A-F grade with prioritized recommendations.

Schedule weekly audits in Claude Code: `/loop 1w /tokcop-audit`

### Context Audit

Inspect your Claude Code environment for bloat:

```
/tokcop-audit
```

Reports on CLAUDE.md weight, MCP server inventory, skill/plugin tax, and recommends pruning.

### Team Dashboard

```bash
# Launch the dashboard
streamlit run dashboard/app.py

# Generate a weekly report for Slack/email
python -m scripts.generate_report --period weekly --format markdown

# Monthly JSON report
python -m scripts.generate_report --period monthly --format json
```

The dashboard provides: org-wide spend overview, per-model efficiency analysis, and prioritized optimization recommendations. Set `TOKEN_COP_DASHBOARD_PASSWORD` env var for access control (default: `tokencop`).

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
