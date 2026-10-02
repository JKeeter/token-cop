# Token Cop

Cross-platform LLM token usage tracker deployed on AWS Bedrock AgentCore.

## Project
- Framework: Strands Agents with BedrockAgentCoreApp
- Model: Claude Sonnet 4 on Bedrock
- Virtual env: `.venv/` (Python 3.13)
- Memory: AgentCore Memory (`TokenCopMemory-oGHHvc2vSN`)

## Architecture
- `agent/app.py` - AgentCore entrypoint (BedrockAgentCoreApp)
- `agent/agent.py` - Strands Agent with system prompt, 13 tools (`TOKEN_COP_TOOLS`), `build_system_prompt(today)`, and efficiency advisor
- `agent/harness_client.py` - `InvokeHarness` streaming client (text + token usage + stop reason) for the harness twin
- `agent/tracing.py` - OTEL tracing + ADOT configurator for AgentCore span export
- `agent/guardrails.py` - Output scrubbing for API keys/secrets
- `tools/` - One file per provider, each exports a @tool-decorated function
- `models/` - Data schemas, pricing table, model name normalization
- `memory/store.py` - AgentCore Memory helpers (store/retrieve snapshots)
- `kiosk/` - React/Vite 5-minute kiosk demo (self-looping scene playlist; replays real recorded MCP captures, deck-slide PNGs, native AWS-icon architecture scenes)

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
- `attribution_breakdown` - Break down Bedrock cost via Cost Explorer by IAM principal, cost-allocation tag, usage type, or linked account
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
- S3 path pattern: `{prefix}/{accountId}/BedrockModelInvocationLogs/{region}/YYYY/MM/DD/HH/` (account + region segments; lister discovers account prefix via trailing-slash `CommonPrefixes` once, applies across all days)
- Logs are gzipped JSON files with batches of invocation records; token counts nest under `input.inputTokenCount`/`output.outputTokenCount` (top-level fallback for legacy)
- Large prompts are offloaded to `input.inputBodyS3Path`; fetched (capped at `BODY_FETCH_CAP=300`) to recover message text + structural signals
- Sampling: stratified random across days, default 300 entries max; dollar figures scaled to population via `population_scale = total_objects / sampled_objects`
- 7 dimensions: prompt bloat, model-task mismatch, caching opportunities, I/O ratio, system prompt weight, response waste, context overhead
- Context overhead dimension measures actual MCP tool schemas, skills, plugins, CLAUDE.md in system prompts (complements context_audit static estimates)
- Model-task mismatch uses `classify_task` (weighted keyword scoring + structural signals) with a confidence gate: `unknown`/low-confidence (`< CONF_THRESHOLD=0.5`) entries are excluded, not guessed
- Classification uses EFFECTIVE input size (`inputTokenCount + cacheRead + cacheWrite`) — raw `inputTokenCount` is only the uncached delta on cached traffic; size + message_count nudges only AMPLIFY a text signal (never create a verdict alone), since large cached context is the norm
- Classifier tuned against real logs via `scripts/compare_classifier.py`; regression fixture `tests/fixtures/invocation_log_sample.json` (anonymized — account IDs scrubbed from ARNs)
- Log records now carry `iam_principal` + `inference_profile` fields extracted by `tools/invocation_logs.py` (post-April 2026)
- IAM needs: `s3:ListBucket` + `s3:GetObject` on the log bucket

## Cost Attribution
- Consumes the April 17, 2026 AWS Bedrock granular cost attribution feature (IAM principal + `iamPrincipal/*` tags in CUR 2.0)
- Tool: `attribution_breakdown` — dimensions: `principal`, `tag:<key>`, `usage_type`, `account`
- Data source: AWS Cost Explorer (`ce:GetCostAndUsage`); CUR 2.0 parquet reader deferred
- Principal grouping uses the `aws:PrincipalArn` tag path in CE (no native `IAM_PRINCIPAL` dimension as of 2026-04)
- One-shot setup: `python -m scripts.enable_cur_attribution --bucket <s3-bucket>` creates/updates a CUR 2.0 export with `INCLUDE_IAM_PRINCIPAL_DATA=TRUE` and activates every `iamPrincipal/*` cost-allocation tag
- Setup is idempotent — safe to re-run; `--status` reports current state, `--tags-only` skips export step, `--dry-run` previews actions
- Tag activation + first CUR 2.0 delivery each take 24–48h (AWS Billing consistency lag); script warns about this
- Runtime IAM needs: `ce:GetCostAndUsage`, `ce:GetDimensionValues`, `ce:GetTags`
- Setup-script IAM needs: `bcm-data-exports:*`, `ce:UpdateCostAllocationTagsStatus`, `ce:ListCostAllocationTags`
- Principal ARNs in tool output are scrubbed via `models/normalization.normalize_principal_arn` so account IDs don't leak
- `check_budget` accepts `principal=` or `tag_filter=` for scoped burn-rate checks; dashboard `views/per_user.py` groups by real IAM principal
- Multi-tenant gateway caveat: attribute further via STS `AssumeRole` session tags (500/s rate limit, 1h credential TTL)
- Docs: `docs/cost-attribution.md` — full setup, IAM tables, troubleshooting

## Patterns
- Tools return JSON strings (Strands convention)
- All providers normalize to `TokenUsageRecord` dataclass
- Model names normalized via `models/normalization.py` aliases
- Costs estimated via `models/pricing.py` lookup table
- `classify_task` (`models/model_tiers.py`) returns a `TierResult` (tier/confidence/signals); tier may be `"unknown"`. Structural signals (input size, code, message count) are optional kwargs — supplied by the invocation-log path, omitted by `recommend_model`
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
- Set `TOKEN_COP_BACKEND=direct` to bypass gateway and call runtime via boto3/IAM; `TOKEN_COP_BACKEND=harness` to call the managed harness twin
- `/tokcop <question>` - Claude Code skill to query token usage

## Harness Twin (managed AgentCore harness, opt-in)
- Same agent as a config-only AgentCore harness (`token_cop_harness`); the container runtime stays untouched
- Provisioned via `python -m scripts.setup_harness --enable` (idempotent; `--status/--dry-run/--teardown/--emit-tool-schema/--update-prompt/--endpoint NAME --version N`)
- Tools: the 13 `TOKEN_COP_TOOLS` re-hosted in Lambda `token-cop-tools` (arm64, deps bundled via `uv`) as gateway target `token-cop-tools` on the existing gateway → tools appear as `token-cop-tools___<name>`; handler `scripts/lambda/tool_dispatch.py` dispatches on `bedrockAgentCoreToolName`
- Gateway tool schema is GENERATED from each Strands `tool_spec` (strip `default`; gateway SchemaDefinition allows only type/description/properties/required/items) — the two chassis cannot drift
- Harness → gateway auth: OAuth2 credential provider `token-cop-cognito` (Cognito client_credentials, scope `token-cop-gateway/invoke`); `allowedTools=["@token-cop-gw/token-cop-tools___*"]` excludes the recursive `token_cop` tool and the ~900-token built-in shell/file tools
- Harness has NO prompt templating: callers pass `systemPrompt=[{"text": build_system_prompt()}]` per invoke; no hooks, no custom loop, no in-process tools; scrubbing happens client-side in `mcp_server.py`
- `mcp_server.py` backend `TOKEN_COP_BACKEND=harness` (one `runtimeSessionId` per MCP process; usage footer appended); `token_cop(prompt, backend=...)` overrides per call
- Demo: `python -m scripts.harness_demo` (6 acts: two chassis, trim built-ins, act on `recommend_model` via `model` override, hard caps, Cedar ENFORCE on both, versions/endpoints/export)
- SSM keys: `/token-cop/harness-arn`, `/token-cop/harness-id`
- Needs `boto3>=1.43` (harness APIs). Two `agentcore` CLIs exist: `.venv/bin/agentcore` (Python toolkit: deploy/eval) vs `/opt/homebrew/bin/agentcore` (npm: `export harness`); this repo uses boto3 for harness ops
- Docs: `docs/harness.md`; CTO talk track: `docs/harness-demo-talk-track.md`

## Budget Enforcement (Option 3, opt-in)
- Provisioned via `python -m scripts.setup_enforcement --enable` (idempotent)
- Hard-caps monthly Bedrock spend per IAM principal with ~1-5 min lag
- Resources (all `token-cop-enforcement-*` prefix): DynamoDB usage table, `TokenCopBedrockBudgetDeny` managed policy, meter Lambda, reset Lambda, EventBridge monthly schedule, CloudWatch Logs subscription on `/aws/bedrock/invocations`
- Meter Lambda parses CWL subscription events, increments `cost_usd` in DDB atomically, attaches deny policy on first overage
- Reset Lambda runs `cron(5 0 1 * ? *)` (1st of month 00:05 UTC) — detaches deny policy and clears `denied` markers
- DDB schema: PK=`principal_arn`, SK=`YYYY-MM` (usage row) | `budget` (per-principal override) | `denied` (active block marker)
- Tools: `enforcement_status`, `set_principal_budget`, `list_denied_principals` (graceful "not enabled" when SSM keys absent)
- SSM keys: `/token-cop/enforcement-{table,deny-policy-arn,default-budget-usd,log-group}`
- Lambda pricing table is duplicated inline in `scripts/lambda/token_meter.py` to keep zero-dependency — update both when prices change
- Assumed-role principals meter per-role not per-session (shared role = shared budget)
- Docs: `docs/enforcement.md`

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

## Kiosk Demo
- `kiosk/` — standalone React 18 + Vite + Tailwind SPA, ~5-min self-looping demo (11 scenes, ~284s; dwells tuned to the narration clip lengths in `kiosk/narration.txt`); pattern ported from AgentCoreKiosk's engine (playlist/watchdog/pause/Esc-Esc + presenter keys ←/→/Space)
- Run: `cd kiosk && npm run dev` → `?kiosk=1`; smoke: `&kioskFast=1` (5% speed); build gate: `npm run captures:check && tsc && vite build`
- Replay scenes play GENUINE recorded `token_cop` MCP exchanges from `kiosk/public/captures/*.json` — refresh via the live MCP tool before events (procedure + caption-number coupling in `docs/kiosk-demo.md`)
- Slide PNGs rendered via LibreOffice headless (`kiosk/scripts/export-slides.sh`); PowerPoint AppleScript export is sandbox-blocked; PDF page numbers ≠ pptx slide numbers (32 hidden slides)
- AWS icons: official 48px set in `kiosk/src/kiosk/overlay/aws/`; arch scenes sync node highlights to caption steps via `HOT_BY_STEP`

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
