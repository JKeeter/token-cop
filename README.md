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
- **Invocation log analysis** - Deep analysis of Bedrock S3 invocation logs across 7 dimensions: prompt bloat, model-task mismatch, caching opportunities, I/O ratio, system prompt weight, response waste, and context overhead detection (MCP tools, skills, plugins)
- **Context audit** - Inspects Claude Code environment for bloat: CLAUDE.md weight, MCP servers, skill/plugin tax, pruning recommendations
- **Team dashboard** - Streamlit app with org-wide spend overview, per-model efficiency analysis, and optimization recommendations
- **Weekly reports** - Markdown/JSON reports for Slack/email with efficiency grades and top recommendations
- **Granular cost attribution** - Per-IAM-principal, per-team, and per-project Bedrock cost breakdowns via Cost Explorer (consumes the [April 17, 2026 AWS Bedrock cost attribution feature](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/iam-principal-cost-allocation.html)); one-shot setup with `scripts/enable_cur_attribution.py`
- **Budget enforcement (opt-in)** - Hard-cap monthly Bedrock spend per IAM principal with ~1-5 min lag. CloudWatch Logs subscription meters every invocation into DynamoDB; an over-budget principal is blocked by attaching a managed deny policy until the next monthly reset. Setup: `scripts/setup_enforcement.py --enable`

### Infrastructure
- **MCP Gateway** - HTTPS endpoint with Cognito JWT authentication
- **Cedar policies** - Access control via AgentCore Policy Engine
- **Observability** - OTEL tracing with AgentCore evaluations
- **Harness twin** - The same agent as a config-only AgentCore *harness*, sharing the gateway and Cedar policies with the container runtime; switch with `TOKEN_COP_BACKEND=harness`
- **Claude Code hooks** - PreToolUse hook intercepts binary file reads

## Architecture

```
Claude Code ──► MCP Server (stdio) ──► MCP Gateway (HTTPS/JWT) ──► Lambda ──► AgentCore Runtime ──► Strands Agent
                     │                        │                                                          │
                     │ TOKEN_COP_BACKEND=     │ target token-cop-tools ──► Lambda (13 tools)             │
                     │ harness                │        ▲                                                 │
                     └──► AgentCore Harness ──┘ (managed Strands loop, OAuth via Cognito)                │
                                                                                     ┌──────────────────┼──────────────────┐
                                                                                     ▼                  ▼                  ▼
                                                                               CloudWatch         OpenRouter API      OpenAI Admin API
                                                                             (AWS/Bedrock)
```

Two chassis, one governed tool plane: the container runtime runs our Strands loop in-process;
the harness runs an AWS-managed Strands loop from configuration and reaches the same 13 tools
through the same gateway (and the same Cedar policies). See [docs/harness.md](docs/harness.md).

## Prerequisites

- AWS account with Bedrock AgentCore access
- AWS CLI configured with appropriate permissions
- Python 3.13+
- API keys for enabled providers (OpenRouter, OpenAI — optional)

## Setup

```bash
# Clone
git clone <repo-url> && cd token-cop

# Virtual environment
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Replace the <REPLACE-WITH-YOUR-AWS-ACCOUNT> placeholder with your 12-digit
# account ID in .bedrock_agentcore.yaml, mcp_server.py, scripts/setup_policies.py.
# (Optional: automate this with a git filter — see "AWS Account ID Git Filter" below.)

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

## Kiosk Demo

A self-running ~5-minute visual demo (React/Vite SPA in `kiosk/`): attract
loop with QR, deck slides with Ken Burns, animated AWS-icon architecture
scenes, and replays of **real recorded** `token_cop` MCP exchanges in a
Claude Code-style terminal.

```bash
cd kiosk && npm install && npm run dev     # http://localhost:5173/?kiosk=1
```

Presenter keys: ← / → jump scenes, Space pauses, Esc Esc exits; any touch
pauses 60s. Full runbook (booth launch, capture refresh, slide re-export):
[docs/kiosk-demo.md](docs/kiosk-demo.md).

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
| `analyze_invocation_logs` | Deep analysis of Bedrock S3 invocation logs (7 dimensions) |
| `attribution_breakdown` | Group Bedrock cost by IAM principal / tag / usage type / account via Cost Explorer |
| `context_audit` | Inspect Claude Code environment for context bloat (local only) |
| `enforcement_status` | Report enforcement state — global, or per-principal current spend / budget / denied flag |
| `set_principal_budget` | Override the default monthly Bedrock budget for a specific IAM principal |
| `list_denied_principals` | List principals currently blocked by Token Cop enforcement |

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
│   ├── invocation_logs.py  # Bedrock S3 invocation log analysis (7 dimensions)
│   ├── attribution.py      # Cost Explorer attribution breakdown (principal / tag / usage type)
│   ├── context_audit.py    # Claude Code environment bloat detection
│   └── enforcement.py      # Budget enforcement: status, set budget, list denied (opt-in)
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
│   ├── enable_cur_attribution.py  # One-shot CUR 2.0 + IAM principal attribution setup
│   ├── setup_enforcement.py   # Budget enforcement provisioning (opt-in)
│   ├── lambda/
│   │   ├── token_meter.py     # Per-invocation meter + deny attacher Lambda
│   │   └── budget_reset.py    # Monthly reset Lambda (detach + clear markers)
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
│   ├── evaluations.md      # Evaluation framework docs
│   ├── cost-attribution.md # IAM principal / CUR 2.0 cost attribution guide
│   └── enforcement.md      # Budget enforcement architecture, setup, caveats
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

Three tiers: **Reasoning** (Opus — architecture, debugging), **Execution** (Sonnet — code gen, data processing), **Polish** (Haiku — formatting, summarizing). When a task is too ambiguous to classify, the advisor returns low confidence rather than guessing — see [Task Classification](#task-classification).

### Token Audit

Run a comprehensive efficiency audit:

```
/tokcop Run a token audit for the last 7 days
```

Scores 6 dimensions: document ingestion, model mix, cache utilization, cost concentration, efficiency trend, and top savings opportunity. Returns an A-F grade with prioritized recommendations.

Schedule weekly audits in Claude Code: `/loop 1w /tokcop-audit`

### Invocation Log Analysis

Analyze actual Bedrock request/response payloads from S3 invocation logs:

```
/tokcop Analyze my invocation logs for the last 5 days
```

Requires Bedrock model invocation logging to S3. Configure:
```bash
# Set the S3 bucket where Bedrock invocation logs land
aws ssm put-parameter --name /token-cop/bedrock-log-bucket --type String --value "your-log-bucket"

# Optional: set custom prefix (default: AWSLogs)
aws ssm put-parameter --name /token-cop/bedrock-log-prefix --type String --value "custom/prefix"
```

Analyzes 7 dimensions: prompt bloat, model-task mismatch, caching opportunities, I/O ratio, system prompt weight, response waste, and context overhead (MCP tools, skills, plugins in system prompts). Automatically included in `token_audit` when S3 bucket is configured.

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

## Cost Attribution

Break down AWS Bedrock spend by IAM principal, team tag, usage type, or linked account. Consumes the April 17, 2026 AWS granular cost attribution feature (IAM principal column in CUR 2.0 + `iamPrincipal/*` cost-allocation tags).

```bash
# One-time setup (in the payer account)
python -m scripts.enable_cur_attribution --bucket my-billing-bucket

# Inspect state
python -m scripts.enable_cur_attribution --status
```

Then ask Token Cop:

```
/tokcop break down Bedrock spend by IAM principal last 7 days
/tokcop how much did team=ml-research spend this month?
```

Tag activation and the first CUR 2.0 delivery each take 24–48 hours. Token Cop queries Cost Explorer (no Athena/Glue pipeline required).

See [docs/cost-attribution.md](docs/cost-attribution.md) for the full setup, IAM permissions table, multi-tenant gateway guidance, and troubleshooting.

## Budget Enforcement (opt-in)

Hard-cap monthly Bedrock spend per IAM principal. Token Cop core works without this — it's an opt-in module.

A CloudWatch Logs subscription on the Bedrock invocation log group forwards each call to a meter Lambda, which atomically increments per-principal usage in DynamoDB. When a principal crosses their monthly budget, the meter attaches a `TokenCopBedrockBudgetDeny` managed policy to that user/role; subsequent `bedrock:InvokeModel*` and `bedrock:Converse*` calls return `AccessDenied`. An EventBridge schedule (1st of month, 00:05 UTC) detaches the deny policy from every blocked principal.

```bash
# Preview what will be created
python -m scripts.setup_enforcement --dry-run --enable

# Provision (default budget: $200/principal/month)
python -m scripts.setup_enforcement --enable

# With a custom default budget and log group
python -m scripts.setup_enforcement --enable \
    --default-budget-usd 500 \
    --log-group /aws/bedrock/invocations

# Inspect / tear down
python -m scripts.setup_enforcement --status
python -m scripts.setup_enforcement --teardown
```

Then drive it through Token Cop:

```
/tokcop what's the enforcement status for arn:aws:iam::...user/alice?
/tokcop set alice's monthly Bedrock budget to $300
/tokcop who's currently blocked by enforcement?
```

Caveats: 1–5 minute lag (a determined user can burn ~$10–50 past the cap on expensive models); assumed-role principals meter per role, not per session (shared role = shared budget); no self-service unblock flow.

See [docs/enforcement.md](docs/enforcement.md) for the architecture, IAM permissions, and manual unblock recipe.

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

## Harness Twin (managed AgentCore harness)

The same agent also runs as an [AgentCore harness](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness.html):
no container, the loop is AWS-managed, and model/prompt/tools/limits are configuration. The 13
tools are hosted in a Lambda behind the existing gateway, so both chassis obey the same Cedar policies.

```bash
python -m scripts.setup_harness --dry-run --enable   # preview
python -m scripts.setup_harness --enable             # provision (idempotent)
python -m scripts.setup_harness --status
TOKEN_COP_BACKEND=harness python mcp_server.py       # or set it in .mcp.json env

python -m scripts.harness_demo                       # 6-act CTO demo (runtime vs harness)
python -m scripts.setup_harness --teardown
```

See [docs/harness.md](docs/harness.md) for architecture, IAM, trade-offs, and the demo runbook.

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

## Task Classification

The invocation-log analysis and the `recommend_model` advisor share one deterministic (no-LLM) classifier, `classify_task` in `models/model_tiers.py`. It returns a `TierResult` (`tier`, `confidence`, `signals`) where `tier` is `reasoning`, `execution`, `polish`, or `unknown`.

- **Weighted keyword scoring** with structural signals (input size, code presence, message count) as optional inputs — the log path supplies them, `recommend_model` omits them.
- **Confidence gate:** when evidence is weak or ambiguous the classifier returns `unknown` rather than guessing. The model-task-mismatch dimension excludes `unknown`/low-confidence entries so its findings stay trustworthy.
- **Cache-aware sizing:** on cached traffic `inputTokenCount` is only the uncached delta, so classification uses effective size (`input + cacheRead + cacheWrite`). Size and message-count signals only *amplify* a text signal — a large cached context alone is the norm, not evidence of reasoning.
- **Tuning:** `scripts/compare_classifier.py` reports the tier distribution over real logs and can freeze an anonymized regression fixture (`--freeze-fixture`). Bucket is resolved from `--bucket`/`BEDROCK_LOG_BUCKET`/SSM, never hard-coded.

## AWS Account ID Git Filter

Repo-hygiene convenience, not required to run the project. A git clean/smudge filter keeps the AWS account ID out of version control; files carry `<REPLACE-WITH-YOUR-AWS-ACCOUNT>` as a placeholder. To automate replacement on checkout:

```bash
aws ssm put-parameter --name /global/aws-account-id --type SecureString --value "YOUR_ACCOUNT_ID"
bash ~/.git-filters/setup.sh
git checkout -- .bedrock_agentcore.yaml mcp_server.py scripts/setup_policies.py
```

Without the filter, just replace the placeholder manually (see Setup).

See [docs/evaluations.md](docs/evaluations.md) for details.
