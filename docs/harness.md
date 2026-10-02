# Token Cop on AgentCore Harness (the "twin" chassis)

Token Cop runs on two chassis that share one governed tool plane:

| | **Runtime** (standard) | **Harness** (managed) |
|---|---|---|
| Agent loop | Our Strands code in an ARM64 container (`agent/app.py`) | AWS-managed Strands loop, declared as configuration |
| Deploy | `agentcore deploy` (CodeBuild → ECR → Runtime) | `python -m scripts.setup_harness --enable` (one API call) |
| Tools | 13 `@tool` functions in-process | Same 13 tools, hosted in Lambda behind the MCP Gateway |
| System prompt | Rendered in `create_agent()` | Same template, rendered by the caller per invocation |
| Memory | Tool-driven snapshots to AgentCore Memory | Same (via gateway tools); conversation memory disabled for parity |
| Tracing | Hand-rolled ADOT in `agent/tracing.py` | Automatic (CloudWatch GenAI Observability → **Harnesses** tab) |
| Output scrubbing | `guardrails.scrub_response` server-side | Same function, applied client-side in `mcp_server.py` |
| Governance | Cedar policies on the gateway | **Same Cedar policies** (the harness calls the same gateway) |
| Cost controls | IAM budget enforcement (opt-in) | Plus loop-level `maxTokens` / `maxIterations` / `timeoutSeconds` |

The harness does not replace the runtime. It is a second, config-only way to run the
same agent so that model, prompt, and tool changes become config edits instead of
container rebuilds, and so the two can be compared live.

## Architecture

```
Claude Code /tokcop ─► mcp_server.py ─TOKEN_COP_BACKEND─┬─ gateway (default) ─► Gateway ─► Lambda ─► Runtime agent (container)
                                                        ├─ direct            ─► Runtime agent (SigV4)
                                                        └─ harness           ─► InvokeHarness ─► managed Strands loop
                                                                                       │  agentcore_gateway tool
                                                                                       │  (OAuth client_credentials via Cognito)
                                                                                       ▼
                             token-cop-gateway-7q9nodpeem ─► target token-cop-tools ─► Lambda token-cop-tools (13 tools)
                             (Cedar policy engine gates both chassis)
```

- The gateway keeps Cognito JWT inbound auth, so the Claude Code path is unchanged.
- The harness authenticates to the gateway with an **AgentCore Identity OAuth2 credential
  provider** (`token-cop-cognito`) that performs the Cognito `client_credentials` flow with
  the same client id/secret already in SSM. No new identity surface.
- `allowedTools` on the harness is `@token-cop-gw/token-cop-tools___*`, which excludes the
  pre-existing `token-cop-target___token_cop` tool (so the harness never calls the runtime
  agent recursively) and the built-in `shell` / `file_operations` tools (~900 input tokens
  per model call that a usage-analytics agent does not need).
- The Gateway tool schema is **generated from the Strands `tool_spec` of each tool** at
  setup time (`--emit-tool-schema` prints it). The two chassis cannot drift.

## Setup

Prerequisites: the runtime is deployed, the MCP gateway and Cognito client exist
(`docs/mcp-gateway.md`), `boto3>=1.43` (harness APIs), `uv` on PATH (builds the arm64 Lambda
bundle; falls back to `pip`).

```bash
source .venv/bin/activate

python -m scripts.setup_harness --dry-run --enable   # preview
python -m scripts.setup_harness --enable             # provision (idempotent)
python -m scripts.setup_harness --status             # inspect
python -m scripts.setup_harness --emit-tool-schema   # review generated Gateway tool schema
python -m scripts.setup_harness --update-prompt      # re-render prompt → new immutable harness version
python -m scripts.setup_harness --endpoint PROD --version 1   # pin a named endpoint
python -m scripts.setup_harness --teardown           # remove everything the script created
```

Resources created (all named `token-cop-tools*` / `token-cop-harness*` / `token_cop_harness`):

| Resource | Name | Purpose |
|---|---|---|
| Lambda | `token-cop-tools` (arm64, py3.13, 1024 MB, 300 s) | Hosts the 13 tools; dispatches on `bedrockAgentCoreToolName` |
| IAM role | `token-cop-tools-lambda-role` | CloudWatch, S3 logs, Cost Explorer, DynamoDB, IAM deny-policy attach, AgentCore Memory, SSM |
| Gateway target | `token-cop-tools` on `token-cop-gateway-7q9nodpeem` | Exposes tools as `token-cop-tools___<name>` |
| OAuth2 credential provider | `token-cop-cognito` | Harness → gateway auth (Cognito client_credentials) |
| IAM role | `token-cop-harness-role` | Harness execution role (Bedrock invoke, logs, X-Ray, token vault) |
| Harness | `token_cop_harness` | The managed agent |
| SSM | `/token-cop/harness-arn`, `/token-cop/harness-id` | Consumed by `mcp_server.py` and the demo |

Untouched by the script: the gateway itself, Cognito, the existing `token-cop-target`,
the runtime, and the Cedar policy engine.

### IAM summary

Harness execution role (from the AWS sample policy, plus the OAuth gateway hop):

| Action | Resource |
|---|---|
| `bedrock:InvokeModel*` | foundation models, inference profiles |
| `ecr-public:GetAuthorizationToken`, `sts:GetServiceBearerToken` | `*` (managed image pull) |
| `xray:Put*`, `xray:GetSampling*`, `logs:*` on `/aws/bedrock-agentcore/runtimes/*`, `cloudwatch:PutMetricData` (ns `bedrock-agentcore`) | observability |
| `bedrock-agentcore:GetWorkloadAccessToken*` | workload identity `harness_token_cop_harness-*` |
| `bedrock-agentcore:GetResourceOauth2Token`, `secretsmanager:GetSecretValue` | token vault / `token-cop-cognito` provider |
| `bedrock-agentcore:InvokeGateway` | the gateway |

Caller of `InvokeHarness` needs `bedrock-agentcore:InvokeHarness` **and**
`bedrock-agentcore:InvokeAgentRuntime` on the harness ARN (harness is a Runtime underneath).

## Switching chassis

`mcp_server.py` supports three backends via `TOKEN_COP_BACKEND`:

| Value | Path | Notes |
|---|---|---|
| `gateway` (default) | Gateway → Lambda → Runtime | Cognito JWT, Cedar-governed |
| `direct` | Runtime via SigV4 | Bypasses gateway |
| `harness` | `InvokeHarness` | One `runtimeSessionId` per MCP process; usage footer appended |

Set it in `.mcp.json` (`"env": {"TOKEN_COP_BACKEND": "harness"}`) and restart the MCP
server, or pass `backend="harness"` on a single `token_cop` tool call. `/tokcop` is unchanged.

Other knobs on the harness path:

- `TOKEN_COP_HARNESS_ARN` overrides the SSM lookup.
- `TOKEN_COP_HARNESS_FRESH_SESSION=1` starts a new conversation per call.
- The Token Cop system prompt is re-rendered with today's date on every call (the harness
  has no server-side templating).

Programmatic use: `agent/harness_client.py` → `invoke(prompt, model_id=..., allowed_tools=...,
max_tokens=..., qualifier="PROD")` returns text plus `usage` (input/output/cache tokens),
`stop_reason`, `tool_calls`, and wall time.

## Demo

Talk track for presenting this to a CTO: `docs/harness-demo-talk-track.md`.

```bash
python -m scripts.harness_demo            # all six acts, pauses between them
python -m scripts.harness_demo --act 3    # one act
python -m scripts.harness_demo --no-pause --json > demo-numbers.json
```

| Act | What it shows | CTO takeaway |
|---|---|---|
| 1 Same question, two chassis | Identical prompt on runtime and harness; wall time, tools called, harness token usage and estimated cost; link to the Harnesses observability tab | Same governed tools, two ways to run the loop |
| 2 Trim the harness | Built-in shell/file tools on vs off; input-token delta per model call | Context hygiene is a config flag |
| 3 Act on Token Cop's advice | `recommend_model` says polish tier → rerun on Haiku via per-invocation `model` override; cost delta | Right-sizing without a redeploy |
| 4 Hard caps | `maxTokens=100` and `maxIterations=1` stop reasons (the output budget is checked between iterations, so the cap must be below the first turn's output to stop the loop) | Budget enforcement inside the loop |
| 5 One policy plane | Cedar ENFORCE denies the harness's tool calls; restore LOG_ONLY | Governance lives in the tool plane, not the chassis |
| 6 Ship, roll back, escape hatch | New version via prompt update, `PROD` endpoint pinned/rolled, `agentcore export harness` diffed against our code | Immutable versions; no lock-in cliff |

## Findings from the first live run (2026-09-11)

- **Parity holds.** Both chassis answered the 7-day spend question with the same `bedrock_usage`
  data. Harness wall time was roughly half the container's on this question.
- **Built-in tools cost real tokens.** Exposing `shell`/`file_operations` added ~1,300 input tokens
  over two model calls (~650 per call) for an agent that never used them. `allowedTools` removes it.
- **Model override pays off.** The polish task cost 67% less on Haiku 4.5 than on Sonnet 4 with an
  identical table as output, via a per-invocation `model` parameter and no redeploy.
- **`maxTokens` is checked between iterations.** A 200-token cap did not stop a two-turn answer of
  735 output tokens because the loop only compares the budget before starting the next model
  call. Set the cap below the first turn's output (100 here) to demonstrate a stop.
- **Denial without visibility makes the model improvise.** With Cedar in ENFORCE mode and a
  blanket forbid, the gateway returned zero tools and Claude Sonnet 4 wrote a *hand-written*
  `<invoke>`/`<result>` transcript with invented dollar figures. An explicit anti-fabrication
  rule in the system prompt did not prevent it. The fix is client-side: `harness_client`
  compares the answer with the trace and flags "figures but no tool call" as `UNVERIFIED`;
  `mcp_server.py` prepends a warning. This is worth showing a CTO: governance needs the deny to
  be visible in the answer, not just in the log.
- **`update_harness` wraps nullable fields.** `memory` (and `authorizerConfiguration`,
  `environmentArtifact`) must be sent as `{"optionalValue": {...}}` on update; create takes
  them bare.
- **Export needs a current npm CLI.** The `agentcore export harness` command shipped at GA;
  older CLI builds print the top-level usage instead. `npm install -g @aws/agentcore@latest`.

## Trade-offs

What the harness gives up relative to the runtime, and how the twin handles it:

- **No hooks, no custom loop, no in-process Python tools.** Tools must be gateway, remote
  MCP, browser, code interpreter, or client-side inline functions. → Lambda target.
- **`_extract_response` safety net and `tracing.py` session-id injection are not used.**
  The stream carries the final assistant message; the harness stamps `session.id` natively.
- **Output scrubbing moves client-side** (`mcp_server.py`). Bedrock Guardrails can be added
  through `bedrockModelConfig.additionalParams.guardrailConfig` if server-side filtering is
  required.
- **Lambda re-hosting.** Cold starts add latency on the first tool call.
  `analyze_invocation_logs` samples up to 300 S3 objects; the Lambda timeout is 300 s, but
  the gateway's per-tool timeout should be verified before demoing that tool on the harness.
- **SigV4 inbound gives no per-user identity propagation to tools** (AWS lists it as planned).
  Use the Cognito `customJWTAuthorizer` on the harness if per-user scoping is needed.
- **Two CLIs named `agentcore`.** `.venv/bin/agentcore` is the Python starter toolkit used by
  `agentcore deploy` / `agentcore eval`. `/opt/homebrew/bin/agentcore` is the npm CLI that
  knows about harnesses (`agentcore export harness`). Inside the venv the Python one wins;
  this repo drives the harness with boto3 in `scripts/setup_harness.py` on purpose.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `OperationNotFoundError: CreateHarness` | boto3 too old; `uv pip install --python .venv/bin/python "boto3>=1.43"` |
| `Unable to parse config file: ~/.aws/config` | Duplicate `[profile ...]` section; every CLI/boto3 call fails until fixed |
| Harness `CREATE_FAILED` | `--status` prints `failureReason`; usually the execution role trust policy or a missing token-vault permission |
| Harness answers "I don't have access to tools" | Gateway auth: check the OAuth2 provider status, the `GetResourceOauth2Token` grant, and Cedar mode (`scripts/setup_policies.py --status`) |
| `stop=max_output_tokens_exceeded` in the usage footer | Per-invocation `maxTokens` cap hit; raise it or narrow the question |
| Gateway target `FAILED` | Tool schema rejected; run `--emit-tool-schema` and check for keys other than `type/description/properties/required/items` |
| Answer flagged `UNVERIFIED` / warning banner | No tool ran but the answer has figures: the harness had zero gateway tools (Cedar deny, OAuth provider failure, or an `allowedTools` pattern that matches nothing). Fix access; do not trust the numbers |
| `ParamValidationError: Unknown parameter in memory: "disabled"` on update | `update_harness` needs `{"optionalValue": {...}}` wrappers; use `--update-prompt`, which handles it |
