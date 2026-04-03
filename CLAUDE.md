# Token Cop

Cross-platform LLM token usage tracker deployed on AWS Bedrock AgentCore.

## Project
- Framework: Strands Agents with BedrockAgentCoreApp
- Model: Claude Sonnet 4 on Bedrock
- Virtual env: `.venv/` (Python 3.13)
- Memory: AgentCore Memory (`TokenCopMemory-oGHHvc2vSN`)

## Architecture
- `agent/app.py` - AgentCore entrypoint (BedrockAgentCoreApp)
- `agent/agent.py` - Strands Agent with system prompt, 11 tools, and efficiency advisor
- `agent/tracing.py` - OTEL tracing + ADOT configurator for AgentCore span export
- `agent/guardrails.py` - Output scrubbing for API keys/secrets
- `tools/` - One file per provider, each exports a @tool-decorated function
- `models/` - Data schemas, pricing table, model name normalization
- `memory/store.py` - AgentCore Memory helpers (store/retrieve snapshots)

## Tools
- `bedrock_usage` - CloudWatch metrics (AWS/Bedrock namespace)
- `openrouter_usage` - OpenRouter REST API
- `openai_usage` - OpenAI Admin API
- `aggregate_usage` - Cross-provider rollup
- `save_snapshot` - Persist to AgentCore Memory
- `search_history` - Semantic search over past snapshots
- `check_budget` - Burn rate + projection
- `recommend_model` - Classify task → reasoning/execution/polish tier recommendation
- `token_audit` - Score usage efficiency across 6 dimensions (A-F grade), auto-calls invocation log analysis when S3 configured
- `analyze_invocation_logs` - Deep analysis of Bedrock S3 invocation logs: 7 dimensions (prompt bloat, model-task mismatch, caching, I/O ratio, system prompt weight, response waste, context overhead)
- `context_audit` - Inspect Claude Code environment for context bloat (local MCP tool)

## Smart Token Management (v2)
- `scripts/convert_heavy_file.py` - Converts PDF/DOCX/PPTX/XLSX → markdown/CSV (10-100x savings)
- `scripts/check_heavy_file.py` - PreToolUse hook helper, blocks binary file reads
- `.claude/settings.json` - Hook wiring for automatic binary file interception
- `skills/heavy-file-ingestion/SKILL.md` - Document conversion skill
- `skills/token-audit/SKILL.md` - `/tokcop-audit` skill (usage + context audit)
- `models/model_tiers.py` - Reasoning/execution/polish tier definitions + task classifier
- `dashboard/` - Streamlit team dashboard (overview, per-model, recommendations)
- `scripts/generate_report.py` - Weekly markdown/JSON report for Slack/email
- System prompt includes Token Efficiency Advisor (6 commandments)
- Mantra: "More tokens is FINE — they need to be SMART tokens"

## Invocation Log Analysis
- Requires Bedrock model invocation logging enabled to S3
- Config: `BEDROCK_LOG_BUCKET` env var or SSM `/token-cop/bedrock-log-bucket`
- Config: `BEDROCK_LOG_PREFIX` env var or SSM `/token-cop/bedrock-log-prefix` (default: `AWSLogs`)
- S3 path pattern: `{prefix}/{accountId}/BedrockModelInvocationLogs/YYYY/MM/DD/`
- Logs are gzipped JSON files with batches of invocation records
- Sampling: stratified random across days, default 300 entries max
- 7 dimensions: prompt bloat, model-task mismatch, caching opportunities, I/O ratio, system prompt weight, response waste, context overhead
- Context overhead dimension measures actual MCP tool schemas, skills, plugins, CLAUDE.md in system prompts (complements context_audit static estimates)
- IAM needs: `s3:ListBucket` + `s3:GetObject` on the log bucket

## Patterns
- Tools return JSON strings (Strands convention)
- All providers normalize to `TokenUsageRecord` dataclass
- Model names normalized via `models/normalization.py` aliases
- Costs estimated via `models/pricing.py` lookup table
- Today's date injected into system prompt (LLM doesn't know current date)
- Date parsing uses dateutil for robustness (LLM may pass non-YYYY-MM-DD)
- Each tool wrapped in OTEL span for latency/error tracking
- Output scrubbed for API key patterns before reaching user
- Response extraction via `_extract_response()` walks conversation history (safety net for save_snapshot)
- ADOT configured manually in `tracing.py` (platform overrides Dockerfile CMD, skips opentelemetry-instrument)

## MCP Gateway
- Gateway URL: `https://token-cop-gateway-7q9nodpeem.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp`
- Gateway ID: `token-cop-gateway-7q9nodpeem`
- Target: `token-cop-target` (Lambda `token-cop-gateway-handler` → AgentCore Runtime)
- Auth: Cognito JWT (`client_credentials` flow), credentials in SSM `/token-cop/gateway-*`
- Cognito User Pool: `us-east-1_hYAk8mbYH`, Domain: `agentcore-d4673f36`
- Token refresh: handled in-process by `mcp_server.py`, see `docs/mcp-gateway.md`
- `mcp_server.py` - Stdio MCP server for Claude Code, default backend=gateway (JWT/HTTPS)
- Set `TOKEN_COP_BACKEND=direct` to bypass gateway and call runtime via boto3/IAM
- `/tokcop <question>` - Claude Code skill to query token usage

## Policies
- Policy Engine: `token_cop_policy_engine` (created by `scripts/setup_policies.py`)
- 3 demo Cedar policies: permit-all, cognito-client-only, forbid-demo
- Gateway mode: LOG_ONLY (default), switchable to ENFORCE
- AI generation demo: `python -m scripts.setup_policies --generate`
- Teardown: `python -m scripts.setup_policies --teardown`
- Docs: `docs/policies.md`

## Evaluations
- Demo: `python -m scripts.eval_demo` (5-act interactive demo)
- Regression: `python -m scripts.eval_regression` (CI-oriented, 8 test cases)
- Custom evaluators: `evaluators/token_cop_evaluators.json` (data_completeness, cost_formatting)
- Reset: `python -m scripts.eval_demo --reset` (clean slate between demos)
- Docs: `docs/evaluations.md`

## Git Filter
- AWS account ID scrubbed from git via clean/smudge filter
- `.gitattributes` applies `filter=aws-account` to 3 files
- Placeholder: `<REPLACE-WITH-YOUR-AWS-ACCOUNT>`
- Global setup: `bash ~/.git-filters/setup.sh`
- `/aws-filter` command auto-detects and configures for any project

## Running
- Local: `source .venv/bin/activate && python -m scripts.local_test`
- Local with trace output: `OTEL_TRACES_EXPORTER=console python -m scripts.local_test`
- AgentCore dev: `agentcore dev`
- Deploy: `agentcore deploy`
