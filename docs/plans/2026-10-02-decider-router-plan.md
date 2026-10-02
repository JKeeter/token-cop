# Token Cop Router: Strands Decider 2B for conversation-boundary model routing

> **Status (2026-10-02): research complete, plan written, NOTHING EXECUTED.** Copied from
> `~/.claude/plans/aws-recently-released-a-frolicking-neumann.md` (plan-mode session, 2026-10-02). Parked by the user to
> pick up later. No repo files, AWS resources, or settings were changed for this plan (the only
> changes made in the same session were the unrelated pricing/meter fixes, committed as `58d8829`).
>
> **To resume:** re-read this file top to bottom (facts were verified 2026-10-02 and may drift —
> re-check `strands-decider` PyPI version, Claude Code version, and Bedrock profile IDs first), then
> start at **Phase 0** (`models/model_tiers.py` tier table + the `<system-reminder>` text bug).
> **Needs your explicit OK before it happens:** (1) `brew install --cask session-manager-plugin`
> (missing locally; required for the EC2+SSM path); (2) launching the EC2 g6.xlarge Decider box
> ($0.8048/h, stop when idle); (3) ~$60 of Opus 5.5 judge labelling; (4) adding
> `ANTHROPIC_DEFAULT_HAIKU_MODEL` to `~/.claude/settings.json` (Phase 0.3).
> Decisions already taken with the user are recorded under **Context** below.

## Context

Token Cop picks a model tier (reasoning / execution / polish) for a prompt with a keyword
heuristic (`/Users/jkeeter/projects/token-cop/models/model_tiers.py::classify_task`). It feeds the
advisory `recommend_model` tool and the invocation-log "model-task mismatch" audit. On the only
real-traffic fixture it marks 67% of requests `unknown` and every confident verdict is a substring
false positive. The user asked whether AWS's new Strands Decider 2B could replace it, and whether
it could sit inline on every Claude Code / LLM call "without impacting latency much".

Decisions taken with the user (2026-10-02): build the **Claude Code gateway first**; host the
Decider **on AWS with a kill switch that reverts everything to plain passthrough**; label the eval
set with an **LLM judge + ~50 human spot-checks**; route **only at conversation boundaries**
(subagent spawns; never the main thread).

Every external claim below was fetched 2026-10-02 from the named primary source. The release
post-dates my training data, so nothing about the Decider comes from memory.

---

## Part 1: What was verified

### The feature is real, experimental, and not an SDK or Bedrock feature
| Claim | Source |
|---|---|
| "Introducing Strands Decider 2B" blog, 2026-10-01, Marc Brooker / Mike Chambers / Fabio Nonato de Paula, released under **strands-labs** | strandsagents.com/blog/introducing-strands-decider (HTTP 200) |
| `github.com/strands-labs/strands-decider`, created 2026-09-29, Apache-2.0 | GitHub API |
| Weights `StrandsAgents/strands-decider-2B-hobson-v19` (HF, Apache-2.0); LoRA r16 on `Qwen/Qwen3.5-2B-Base` + ~1M-param pointer head; `hobson_config.json`: `max_length 4096`, `num_slots 24`, bf16, per-kind calibration temperatures | HF API + raw config |
| `pip install strands-decider` **0.1.0** (sole release, 2026-10-01), Python ≥3.10; deps `torch>=2.7, transformers>=5.15,<6, peft>=0.21, accelerate, fastapi, uvicorn…`; **no dependency on strands-agents** | PyPI JSON, repo `pyproject.toml` |
| Not in `strands-agents` 1.57.2 (zero "decider" references); not on Bedrock (no matching model/profile IDs in us-east-1); no Ollama build; hosted version "remains to be seen" | wheel inspection, `aws bedrock list-*`, ollama.com, The New Stack |
| Independent coverage: VentureBeat, The New Stack, thelettertwo, TechTalkThai | fetched |

Unreproducible: the blog's "3rd of 33 in the 2B class" on JevBench. Today's leaderboard
(benchmarkheaven v1.5.4) shows `decider-2b` rank 26/106 overall, 2nd of ~7 ≈2B systems
(Intelligence 42.3, Calibration 71.5). Treat the blog number as point-in-time.

### How it works (repo source: `schema.py`, `infer.py`, `prompting.py`, `server.py`)
- Cannot generate text. Answers typed questions about a `state`: `noul` → `{"noul": P(true)}`;
  `choice` → `{"choice", "probabilities": {opt: p}, "confidence"}`; `score` → `{"score", "legend",
  "probabilities", "confidence"}`. Options/criteria are **supplied per request** (no retraining).
- Prompt layout: `<state>…</state><question type=…>…<options>1. name — desc…</options></question><answer>`.
- Window 4096 tokens: `_fit` reserves up to 75% for the question, **right-truncates the state
  silently**, front-truncates the question. Prefix sharing: N questions cost `state + N×question`.
- HTTP: `strands-decider serve <ckpt> --port 8000 --device cuda|mps|cpu`; `POST /v1/systemone`
  `{"state", "model", "questions": {id: {"type","instructions","criteria"}}}` →
  `{"model","answers","usage":{"input_tokens","output_tokens"},"latency_ms"}`; `GET /health`.
  Binds 127.0.0.1, **no auth**, concurrency "not verified".
- In-process: `strands_decider.infer.load_engine(ckpt, device=…)` → `SystemOneEngine.ask(state, questions)`.
  `StrandsDeciderModel.load` pulls the ~4.5 GB base weights from HF Hub (unpinned revision).

### Measured performance (repo `evaluation/results.md`, `docs/inference.md`)
| Hardware | Latency | Memory |
|---|---|---|
| RTX 3090 | median **115 ms**, p95 299 ms | ~3.5 GiB |
| Apple M3 Pro, MPS | 153 ms warm (<300 tok), 310 ms first request of a new length; JevBench median 234 ms, **p95 2.6 s**; 1,024 tok → 1,029 ms | 5.4 GB |
| **CPU fp32** | 256 tok → **3,252 ms**; 1,024 tok → **5,424 ms** | **8.3 GB** |

Accuracy: JevBench 167/231 (72.3%); internal held-out short tasks **64.1%**. HF card: calibration
fitted on held-out short classification only, "**measure on your own traffic before you trust a
threshold**"; on unseen tasks `choice` is useful behind a confidence gate, `score` is not. **No
published model-routing accuracy.**

### Claude Code facts (code.claude.com docs; local `claude --version` = 2.1.287)
- **Hooks cannot change the model.** All 33 events checked; `UserPromptSubmit` can add context or
  block only; `PreModelSwitch` can veto, not initiate. `ANTHROPIC_BASE_URL` "changes where requests
  are sent, not which model answers them."
- **Only seam: an LLM gateway.** User's setup is Bedrock Invoke API (`CLAUDE_CODE_USE_BEDROCK=1`,
  also `CLAUDE_CODE_USE_MANTLE=1`, `ANTHROPIC_MODEL=global.anthropic.claude-fable-5`, settings
  `model: us.anthropic.claude-fable-5-1`). Gateway variables: `ANTHROPIC_BEDROCK_BASE_URL`,
  `CLAUDE_CODE_SKIP_BEDROCK_AUTH=1`. Requests: `POST /model/{id}/invoke-with-response-stream`
  (+ `/invoke`, optional `/count-tokens`), startup `GET /inference-profiles?type=SYSTEM_DEFINED`
  (may be rejected). Response `application/vnd.amazon.eventstream` must be relayed **byte-for-byte,
  unbuffered, content-type intact**. Body fields `anthropic_version`/`anthropic_beta` forwarded
  unchanged. Routing only among Claude models is supported.
- **Hint headers** (v2.1.273+, need `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1` on Bedrock):
  `x-claude-code-request-class` ∈ main|subagent|workflow|compaction|auxiliary,
  `x-claude-code-agent-type` (Explore|Plan|general-purpose|custom|teammate|fork),
  `x-claude-code-prompt-id` (random UUID per user prompt, v2.1.283+); always
  `x-claude-code-session-id`, `x-claude-code-agent-id`, `x-claude-code-parent-agent-id`.
  Bedrock invocation logs do **not** contain these; only a gateway sees them.
- **Why per-call switching on the main thread is a net loss even at 0 ms classifier latency:**
  prompt caches are model-scoped (a cached prefix is that model's computed state; Anthropic's API
  reference calls caches model-scoped). September traffic was **92.3% cache reads** (718M/778M
  tokens). One switch at 150K context cold-starts the cache: ≈ 150K × ($12.50 − $0.25)/M ≈
  **$1.84 on Fable 5.1**. Thinking blocks are bound to the producing model: a switch triggers a
  `bound to a different conversation` 400, after which Claude Code strips thinking blocks for the
  rest of the conversation.
- **Zero-code levers that already exist**: `ANTHROPIC_DEFAULT_HAIKU_MODEL` moves background tasks
  (titles, summaries) off the primary model — today they run on **Fable** because a primary model is
  set; `CLAUDE_CODE_SUBAGENT_MODEL` sets a blanket default model for subagents.
- Bedrock profiles ACTIVE in us-east-1 (both `us.` and `global.`): fable-5, fable-5-1, opus-5-5,
  opus-5, opus-4-8, sonnet-5-5, sonnet-5, sonnet-4-5-20250929-v1:0, haiku-4-5-20251001-v1:0.

### Strands SDK facts (separate from the Decider)
`strands-agents` has `ModelRouter` (python/v1.52.0, PR #3474), `ClassifierStrategy` (one extra LLM
call, v1.54.0), `RoutingStrategy` protocol (`async select(context) -> RoutingCandidate | None`),
`RoutingContext(messages, system_prompt, tool_specs, candidates, invocation_state, attempts)`.
Token Cop has **1.30.0 installed** (no `strands.models.routing`, no `strands.interventions`).

### Repo baseline (Explore reports, read-only; 158 tests pass)
- Heuristic on `/Users/jkeeter/projects/token-cop/tests/fixtures/invocation_log_sample.json` (24
  real, anonymized Claude Code records): `{unknown: 16, execution: 8}`; all 8 confident verdicts fire
  on scaffolding (`TaskCreate`→"create", "implementation"→"implement"); every human request → unknown.
- `_extract_body_signals` classifies the **first** text block of the user message, which on Claude
  Code traffic is usually a `<system-reminder>`, not the task.
- **No ground-truth labels exist**; `scripts/compare_classifier.py` reports distribution only.
- `TIERS` covers 9 models; **20 priced models incl. every Claude 5-era ID have no tier**, so all
  current premium traffic is skipped by the mismatch audit.
- The only per-call model override today is `agent/harness_client.py:160-161` (`model_id`), unused by
  `mcp_server.py:218`. `agent/app.py:57-61` builds the agent per request from `prompt` only.
- Tools Lambda zip guard 45 MB (`scripts/setup_harness.py:111`) → torch can never ship there.
- Local specs (gitignored) `docs/superpowers/specs/2026-07-07-bifrost-gateway-addon-design.md` and
  `…/2026-08-07-classifier-improvement-design.md` say "no per-request classification / no LLM in
  the classification path"; the rejection assumed a *network hop to Python*, never a sidecar.

### Invocation-log inventory (read-only measurement, 2026-10-02)
- Bucket `<bedrock-log-bucket>` (name in SSM `/token-cop/bedrock-log-bucket`): **15,278 objects / 3.07 GB, 2026-04-03 → 2026-10-02**: 6,662 log
  batch files + 8,616 offloaded full-prompt bodies (`…/data/<uuid>_input.json.gz`). Monthly objects:
  Apr 1,387 · May 273 · Jun 24 · Jul 130 · Aug 1,068 · **Sep 10,728** · Oct(2 d) 1,663.
- Keys live under `invocation-logs/AWSLogs/<acct>/BedrockModelInvocationLogs/us-east-1/…`; Token
  Cop's fallback prefix is `AWSLogs` and SSM `/token-cop/bedrock-log-prefix` is unset → confirm the
  runtime's `BEDROCK_LOG_PREFIX` is `invocation-logs/AWSLogs`.
- Stratified sample via the production pipeline (`_list_log_objects/_sample_objects/_parse_log_entries`,
  253 files → 669 records, 2.64 records/file → **≈17,600 invocations in six months**). Bodies were
  recovered for ~207 records; **10 of those were conversation boundaries (message_count == 1, ≈5%)**;
  the rest are mid-loop tool-result turns. Extrapolated: **≈500–1,000 boundary prompts** and
  ≈2–3k text-bearing user turns in six months. Boundary models seen: opus-4.6 ×4, haiku-4.5 ×3,
  opus-4.7 ×2, fable-5 ×1. Exact counts require the full extraction in Phase 3.1.

### Decider training facts (repo `training/`, `src/strands_decider/{train,data/format,data/recipes}.py`)
- Row format (`Example`, JSONL): `kind` (noul|choice|score), `state`, `instructions`,
  `options: [[name, description], …]`, `label: int` (index; noul order is `[false, true]`),
  `task`, `weight=1.0`, `instruction_variants=[]`. Only hard labels; teacher distributions are a
  separate positional file `{"i": row, "probs": […]}`. `write_jsonl/read_jsonl/load_examples/
  split_examples(val_fraction, group_by_task)` exist.
- Config (`training/configs/train.yaml`): `base_model Qwen/Qwen3.5-2B-Base`, LoRA r16/α32 on 12
  target modules, `num_slots 24`, `max_length 4096`, 1 epoch, micro-batch 8 × accum 4, lr 1e-4,
  `shuffle_options: true`, `train_files` list (≈100k rows + 12.9k multistep), `teacher_file`
  (positional soft targets), `kl_frozen_weight 0.3`. Defaults "sized for a single 24 GB card".
- Full recipe: `training/recipe.sh all` ≈ **11 h on one RTX 3090**, 1 h 10 min on 8×H100; Linux/WSL2
  only (Triton kernels). Reproduction bar: JevBench public 157–177/231.
- `init_from: <checkpoint>` continues from a trained checkpoint **but freezes the torso and attaches a
  fresh `SlotHead`** (the earlier, weaker head) — so it is head-only retraining, not continued LoRA.
- `strands-decider calibrate <ckpt> --data <file.jsonl> [--split all]` re-fits one temperature per
  primitive (minimising ECE) and writes them into the checkpoint — this is how the confidence gate is
  made valid "on your own traffic".

---

## Part 2: Feasibility verdict

| User's premise | Verdict |
|---|---|
| Strands Decider 2B exists | **Yes** (v0.1.0, experimental, strands-labs) |
| "Much faster than a standard LLM" | **True on GPU/MPS** (115–300 ms vs 0.5–1.5 s for a Haiku classifier); **false on CPU** (3–5 s). Rules out Lambda, the AgentCore container, and any CPU host |
| "Wouldn't impact latency much if put inline for all Claude Code calls" | **Only at conversation boundaries.** Per-call switching on the main thread is dominated by cache rebuilds and thinking-block loss, not by classifier latency. Hooks can't do it at all; a gateway can |
| "Much better classifier than the heuristics" | **Plausible, unmeasured.** The heuristic bar is very low, but the Decider has no routing accuracy figure and its authors say to measure on your traffic. Adoption is gated on a labelled eval |
| Hosting economics | A dedicated GPU costs **$588/mo (EC2 g6.xlarge, $0.8048/h)** or **$822/mo (SageMaker ml.g6.xlarge, $1.1267/h)** 24×7, against ≈$1,169/mo of September Bedrock spend of which the main thread (untouched) is the majority. It only pays if run business-hours/stopped when idle, or shared by a team. Shadow mode measures the addressable pool before anything goes live |
| "Use the S3 invocation logs to train the classifier before go-live" | **Yes, as the data source; no, not as labels.** Six months of complete prompts (and responses + token usage) exist, but the model that served each request is what you *chose*, not what was *needed*, so every row still needs a tier label (judge + human). Volume is the constraint: ≈500–1,000 boundary prompts and ≈2–3k text-bearing turns. That is enough to **evaluate and recalibrate** the released model on your traffic (cheap, required) and to **domain-adapt** it by mixing your rows into the published recipe (full retrain, ≈11 h GPU), but too thin to train a 2B model on alone. The plan does both, gated on measured results, and accumulates more boundary prompts from the proxy for later retrains |

**What this feature will and won't save (honest framing):** the blunt levers
`ANTHROPIC_DEFAULT_HAIKU_MODEL` + `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` capture most subagent/background
savings with zero code. The gateway + Decider adds: (a) visibility into request classes Bedrock logs
cannot see; (b) per-task tiering of subagents (keep Fable for a Plan agent that needs it, drop an
Explore agent to Haiku) instead of one blanket model; (c) an enforcement point for Token Cop; (d) a
pluggable classifier that fixes the audit's `unknown` problem. Shadow mode compares the Decider
against the blanket setting so the decision is data-driven.

---

## Part 3: Architecture

```
scripts/claude-routed  (exports CLAUDE_CODE_USE_BEDROCK=1, CLAUDE_CODE_SKIP_BEDROCK_AUTH=1,
                        CLAUDE_CODE_GATEWAY_HINT_HEADERS=1, ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:8787,
                        ANTHROPIC_DEFAULT_HAIKU_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0; falls back to plain `claude` if proxy unhealthy)
   │
   ▼  POST /model/{id}/invoke-with-response-stream | /invoke | /count-tokens ; GET /inference-profiles*
router/app.py  (Starlette + uvicorn, 127.0.0.1:8787, body bytes never re-serialized)
   ├─ request_parse: hint headers, unquoted model id, is_conversation_boundary(body), task text (system-reminders stripped)
   ├─ policy.pre_decide → passthrough | pinned | ask-decider
   ├─ decider_client (httpx → http://127.0.0.1:8000/v1/systemone through an SSM port-forward; 600 ms budget; circuit breaker) ─ fail-open
   ├─ policy.finalize → served model (downgrade only, ≤1 tier at launch, pinned per agent-id for its lifetime)
   ├─ upstream: rewrite URL path only, SigV4 with the DEVELOPER's own credentials (service "bedrock"), relay bytes via aiter_raw
   ├─ observe: UsageObserver tees eventstream → token usage; DecisionLog JSONL; optional CloudWatch TokenCop/Router
   └─ config: env > SSM (/token-cop/router-mode off|shadow|live, /token-cop/router-policy JSON, /token-cop/decider-url), 60 s TTL

EC2 g6.xlarge "token-cop-decider" (DLAMI, no inbound ports, SSM-managed; systemd `strands-decider serve … --device cuda --port 8000`;
                                   weights on EBS, HF_HUB_OFFLINE=1 after first download; stop/start = kill switch)
```

Why a **local per-developer proxy**: it signs Bedrock calls with the developer's own identity, so
Token Cop's per-IAM-principal attribution (CUR `iamPrincipal/*`, `attribution_breakdown`) keeps
working. A central gateway signing with one role would erase it.

Why **EC2 + SSM port-forward** over SageMaker: $0.8048/h vs $1.1267/h; stop/start in ~1–2 min
with weights kept on EBS; no public port, TLS cert, or ALB (SSM tunnel is IAM-authenticated and
encrypted); no Docker image or 5 GB S3 artifact to build. Trade-off: each developer needs the AWS
`session-manager-plugin` (**not installed here; brew cask, needs your OK**) and an auto-reconnecting
tunnel. **Alternative kept documented**: SageMaker real-time endpoint (BYOC shim `/ping`+`/invocations`
on 8080, IAM `InvokeEndpoint`, delete/recreate as kill switch) if a shared team endpoint is wanted.

Invariants (go in CLAUDE.md): never switch `main`/`workflow`/`compaction`; decide only when the
agent-id is unseen **and** the body has zero `assistant` messages; downgrade only; fail-open on any
Decider problem; body bytes are never re-serialized; pins are per agent-id for the agent's lifetime.

---

## Part 4: Implementation plan

Conventions: tests first (stdlib `unittest`; run `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m
unittest discover -s tests`, baseline 158 OK); install with `uv pip install --python .venv/bin/python`;
tools return JSON strings; fixtures scrubbed with `models/normalization.normalize_principal_arn` +
the 12-digit regex from `scripts/compare_classifier.py`; new package `router/` keeps `__init__.py`
import-light. No torch locally, ever.

### Phase 0: Foundations (no user-visible behavior change)
**0.1 Tier table + rank.** Modify `/Users/jkeeter/projects/token-cop/models/model_tiers.py`: add
reasoning `claude-fable-5.1, claude-fable-5, claude-opus-5.5, claude-opus-5, claude-opus-4.8,
claude-opus-4.7`; execution `claude-sonnet-5.5, claude-sonnet-5, claude-sonnet-4.5`; polish
`claude-haiku-4.5, claude-3.5-haiku` (keep existing). Replace hard-coded `cost_range` strings with
`_cost_range(models) -> str` computed from `PRICING_PER_MILLION`. Add `TIER_RANK = {"polish":0,
"execution":1,"reasoning":2}` + `tier_rank(tier)`; import it in `tools/model_router.py` and
`tools/invocation_logs.py::_analyze_model_task_mismatch` (replacing local dicts).
Tests first in `/Users/jkeeter/projects/token-cop/tests/test_model_tiers.py::TierTableTests`:
every `TIERS` model has a pricing row; `get_model_tier("claude-fable-5.1") == "reasoning"`;
`normalize_model_name("us.anthropic.claude-opus-5-5")` resolves to a tier.

**0.2 Task-text helpers + the system-reminder bug.** Create
`/Users/jkeeter/projects/token-cop/utils/prompt_text.py` (pure; `utils/` already ships in the tools
Lambda): `strip_system_reminders(text) -> str`, `first_user_task_text(messages, max_chars=8000) -> str`
(first user message; skip blocks empty after stripping; join; bound), `has_code(text) -> bool` (move
`_looks_like_code`, keep alias). Modify `tools/invocation_logs.py::_extract_body_signals` to use it.
Tests first: `/Users/jkeeter/projects/token-cop/tests/test_prompt_text.py`; new fixture
`/Users/jkeeter/projects/token-cop/tests/fixtures/claude_code_subagent_body.json` (user message whose
first block is a `<system-reminder>`, second the task); extend `tests/test_invocation_logs.py::BodySignalTests`.

**0.3 Zero-code baseline (settings only, your call when):** add
`ANTHROPIC_DEFAULT_HAIKU_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0` to `~/.claude/settings.json`
`env` (background tasks off Fable). Record the before/after in `router.report` once Phase 2 exists.

### Phase 1: Passthrough proxy (mode `off`)
**1.1 `/Users/jkeeter/projects/token-cop/router/request_parse.py`** (pure): `HINT_HEADERS`,
`@dataclass(frozen=True) RequestHints`, `parse_hints(headers)`, `model_id_from_path(raw)`
(`urllib.parse.unquote`; ARNs untouched), `is_conversation_boundary(body) -> bool` (no `assistant`
role), `@dataclass(frozen=True) RouteInput(request_class, agent_type, agent_id, parent_agent_id,
session_id, prompt_id, requested_model, is_boundary, message_count, has_code, task_text, tool_count)`,
`build_route_input(hints, model_id, body)`. Tests: `tests/test_router_request_parse.py`.

**1.2 `/Users/jkeeter/projects/token-cop/router/upstream.py`**: `upstream_url(kind, region,
model_id=None, query="")` for `stream|invoke|count_tokens|profiles|profile` (runtime host
`bedrock-runtime.{region}.amazonaws.com`, control host `bedrock.{region}.amazonaws.com`, model id
`quote(id, safe="")`); `sign(method, url, body: bytes, headers, credentials, region,
service="bedrock", now=None) -> dict` via `botocore.auth.SigV4Auth` + `AWSRequest` (sign **after**
the path rewrite; body untouched); `relay_headers()` (drop hop-by-hop + content-length; keep
`content-type`, `x-amzn-*`); `class BedrockUpstream(region, client: httpx.AsyncClient,
credential_provider)` with `async send(...)` (`stream=True`, `httpx.Timeout(connect=10, read=330,
write=60, pool=10)`); credentials fetched per request from botocore's refreshable chain (SSO expiry).
Tests: `tests/test_router_upstream.py` (fixed creds + `now` → deterministic `Authorization`,
canonical path contains `%3A`, control-plane host for profiles, body identity).

**1.3 App, config, log.** `/Users/jkeeter/projects/token-cop/router/config.py` (`RouterSettings`
from `TOKEN_COP_ROUTER_*` env; `PolicyLoader` env > SSM, 60 s TTL, last-good on SSM failure, default
`mode="off"`; register keys in `agent/config.py::SSM_PARAMS`).
`/Users/jkeeter/projects/token-cop/router/observe.py` (`DecisionRecord` dataclass — ids, class,
agent type, requested/served/would_serve model, action, tier, confidence, frontier_p,
decider_latency_ms, fallback_reason, is_boundary, message_count, task_chars, task_sha8,
upstream_status, upstream_ms, usage, est_cost_requested/served via `models/pricing.estimate_cost`;
`DecisionLog(path)` JSONL, **never prompt text**).
`/Users/jkeeter/projects/token-cop/router/app.py`: `create_app(settings, upstream, decider=None,
policy_loader, pins, log, metrics=None) -> Starlette`; routes for the five Bedrock paths +
`GET /_router/health`, `GET /_router/stats`; `handle_invoke` reads body once, builds `RouteInput`,
decides (Phase 1: always passthrough), signs, relays with `StreamingResponse(aiter_raw(), status,
relay_headers)`, closes upstream on client disconnect, forwards error bodies/status unmodified,
502 only for proxy-side failures. `/Users/jkeeter/projects/token-cop/router/__main__.py`
(`uvicorn.run(host="127.0.0.1", http="h11", timeout_keep_alive=600)`, no middleware/gzip).
`/Users/jkeeter/projects/token-cop/scripts/router_up.sh` (idempotent start, pidfile, health check;
also opens/maintains the SSM tunnel in Phase 2) and `/Users/jkeeter/projects/token-cop/scripts/claude-routed`
(exports the env vars, verifies health, `exec claude "$@"`; on unhealthy proxy warns and runs plain
`claude`). Pin `httpx>=0.28, starlette>=0.52, uvicorn>=0.42` in `requirements.txt` (already installed).
Tests: `tests/test_router_app.py` with `starlette.testclient.TestClient` + `httpx.MockTransport`
upstream emitting multi-chunk `application/vnd.amazon.eventstream` bytes: byte-identical body,
content-type/status preserved, model id unchanged in `off`, 400/429 bodies verbatim,
`GET /inference-profiles?type=SYSTEM_DEFINED` to control host, one JSONL line per request.

**Phase 1 gate:** one working day through the proxy in `off`: streaming, tool use, thinking,
`cacheReadInputTokenCount > 0` on repeated turns, invocation logs still show **your** `identity.arn`.
Spike item: confirm Claude Code accepts `http://127.0.0.1:8787` as `ANTHROPIC_BEDROCK_BASE_URL`.

### Phase 2: Decider on EC2, policy, shadow mode
**2.1 `/Users/jkeeter/projects/token-cop/router/policy.py`** (pure): `RouterPolicy(mode="off",
gate=0.8, frontier_veto=0.3, max_downgrade=1, decider_budget_ms=600, pin_ttl_s=21600,
compaction="passthrough", auxiliary="passthrough", eligible_agent_types=("Explore","Plan",
"general-purpose","custom"), tier_models={"execution": "anthropic.claude-sonnet-5-5",
"polish": "anthropic.claude-haiku-4-5-20251001-v1:0"})` + `from_dict/to_dict`;
`DeciderAnswer(tier, probabilities, confidence, frontier_p, latency_ms, error)`;
`RouteDecision(requested_model, served_model, would_serve, action, tier, confidence, reason)`;
`PinStore(ttl_s, clock)`; `requested_tier(model_id)` (via `normalize_model_name` + `get_model_tier`);
`target_model(tier, requested_model, policy)` (preserves `us.`/`global.` prefix; `None` unless
strictly lower rank, never for application-profile ARNs or non-Claude targets);
`pre_decide(inp, policy, pins)`: main/workflow/no header → passthrough; compaction → policy;
auxiliary → passthrough (background tasks are handled by `ANTHROPIC_DEFAULT_HAIKU_MODEL`, Claude
Code's own supported mechanism; `x-claude-code-prompt-id` is a per-prompt UUID and cannot be
allow-listed); pinned agent → pinned model; `subagent` ∧ `is_boundary` ∧ eligible type ∧ known
requested tier above polish → ask Decider; else passthrough.
`finalize(inp, policy, pins, answer)`: error → passthrough with reason; **two-key rule**: downgrade
only if `choice.confidence ≥ gate` **and** `frontier_needed.noul ≤ frontier_veto`; clamp to
`max_downgrade`; pin the served model (also on passthrough). `apply_mode(decision, mode)`: `shadow`
serves requested, records `would_serve`. Tests: `tests/test_router_policy.py` (one per rule; TTL
with injected clock; prefix preservation; ARN passthrough; shadow vs live).

**2.2 `/Users/jkeeter/projects/token-cop/router/decider_prompts.py`** (`TASK_TEXT_MAX_CHARS=8000`;
`build_state(inp)` puts agent type, requested tier, tool count, has_code, then the task — most
informative first because `_fit` right-truncates; `build_routing_questions()` →
`{"frontier_needed": noul(...criteria true/false...), "tier": choice(... {reasoning, execution,
polish} with "minimum sufficient tier" criteria)}`) and
`/Users/jkeeter/projects/token-cop/router/decider_client.py` (`DeciderConfig(url, budget_ms=600,
breaker_failures=3, breaker_open_s=60)`; `parse_systemone_response(payload, latency_ms) ->
DeciderAnswer` reading `answers.tier.{choice,probabilities,confidence}` and
`answers.frontier_needed.noul` (field names verified in `schema.py`); `DeciderClient.ask()` never
raises, `asyncio.Semaphore(2)`, `wait_for(budget)`, circuit breaker, `available()`).
Tests: `tests/test_decider_client.py` with `httpx.MockTransport` (happy path, malformed → error,
timeout, breaker opens after 3 and closes after `breaker_open_s`).

**2.3 `/Users/jkeeter/projects/token-cop/scripts/setup_decider.py`** (idempotent, mirrors
`setup_enforcement.py`/`setup_harness.py`): `--enable` (IAM role + instance profile with
`AmazonSSMManagedInstanceCore`; security group with **no inbound rules**; latest GPU DLAMI resolved
via its public SSM parameter at provision time — verify the exact parameter path; g6.xlarge;
100 GB gp3; user-data: venv, `pip install strands-decider==0.1.0`, `huggingface-cli download` of
`StrandsAgents/strands-decider-2B-hobson-v19` and `Qwen/Qwen3.5-2B-Base` **pinned to the revision in
`provenance.json`**, systemd unit `strands-decider serve … --device cuda --port 8000` with
`HF_HOME=/opt/decider/hf`, `HF_HUB_OFFLINE=1`), `--status`, `--stop`/`--start` (EC2
stop/start = kill switch, weights persist), `--terminate`, `--set-mode off|shadow|live`,
`--policy-file`, `--schedule business-hours|none` (EventBridge Scheduler universal targets
`ec2:StartInstances/StopInstances`, no Lambda), `--dry-run`. Pure, unit-tested builders:
`instance_trust_policy()`, `user_data(...)`, `run_instances_kwargs(...)`, `default_router_policy()`.
SSM keys: `/token-cop/decider-instance-id`, `/token-cop/decider-url` (default
`http://127.0.0.1:8000`), `/token-cop/router-mode`, `/token-cop/router-policy`.
`router_up.sh` gains the tunnel: `aws ssm start-session --target <id> --document-name
AWS-StartPortForwardingSession --parameters '{"portNumber":["8000"],"localPortNumber":["8000"]}'`
in the background with auto-reconnect (SSM sessions idle-timeout; default 20 min). Requires
`session-manager-plugin` locally (**ask before `brew install --cask session-manager-plugin`**).
Tests: `tests/test_setup_decider.py` (policy docs, kwargs, SSM names, `--dry-run` makes no `run_instances`).

**2.4 Shadow wiring + usage observer.** Modify `router/app.py`: when `mode != "off"` and
`pre_decide` returns None → Decider → `finalize` → `apply_mode`; rewrite the path only in `live`.
Add `UsageObserver` to `router/observe.py` (tees chunks into `botocore.eventstream.EventStreamBuffer`,
extracts `amazon-bedrock-invocationMetrics`/`message_delta.usage`; never alters bytes, never raises)
and `MetricsSink` (`PutMetricData` to `TokenCop/Router`, batched 60 s, opt-in
`TOKEN_COP_ROUTER_METRICS=1`: Requests, Downgrades, Fallbacks, DeciderLatencyMs, DeciderErrors by
class/mode/reason). Create `/Users/jkeeter/projects/token-cop/router/report.py`
(`python -m router.report --since 7d [--json] [--endpoint-hourly-usd 0.8048]`: counts by
class/agent type/action, coverage at gate, fallback reasons, Decider p50/p95, projected (shadow) or
realized (live) savings, break-even hours, and the Decider-vs-blanket-`CLAUDE_CODE_SUBAGENT_MODEL`
comparison). Tests: extend `tests/test_router_app.py` (shadow logs `would_serve` but serves requested;
live rewrites; timeout → passthrough + reason; pin reused on turn 2), `tests/test_router_observe.py`
(synthetic eventstream via botocore framing; report math).

**Phase 2 gate:** 5 working days in `shadow`: zero proxy-caused upstream errors; Decider p95 ≤ 500 ms
measured from the proxy (through the tunnel); fallback rate ≤ 10%; `router.report` shows subagent
spend, would-route rate, and projected savings vs endpoint cost.

### Phase 3: Train on your logs — dataset, labels, calibration, fine-tune, acceptance gate
The S3 invocation logs are the training *and* evaluation source. Everything with prompt text stays
local (`tests/fixtures/local/`, gitignored) or in your S3; only labels and a redacted 30-row sample
are committed.

**3.1 Full extraction — `/Users/jkeeter/projects/token-cop/scripts/build_routing_dataset.py`**:
walk the **whole bucket** (not a sample) with `_list_log_objects(days=200)`, parse every record with
`_parse_record`, fetch every offloaded body with `_fetch_body` (own loop, bypass `BODY_FETCH_CAP`,
thread pool 8), and the matching `_output.json.gz` where present. Keep every request whose latest
user turn has text (reminders stripped via `utils/prompt_text`). Fields: `case_id` (sha256 of task
text), `is_boundary` (zero `assistant` messages), `task_text` (≤8000 chars, `agent.guardrails` +
ARN/12-digit scrub), `system_prompt_snippet` (600 chars) + `system_prompt_hash`, `tool_count`,
`effective_input_tokens`, cache read/write, `output_tokens` (includes thinking tokens — a difficulty
signal), `response_snippet` (first 1,500 chars of the model's reply, for the judge), `model_id`,
`requested_tier`, `has_code`, `message_count`, `day`, `principal_hash`, `group_key`
(day + principal + system_prompt_hash — a conversation proxy for leakage-safe splits; logs carry no
session id). Output `/Users/jkeeter/projects/token-cop/tests/fixtures/local/routing_dataset.jsonl`
+ a `--stats` summary (counts by boundary/model/month). `--commit-sample 30 --truncate 300` →
committed redacted `/Users/jkeeter/projects/token-cop/tests/fixtures/routing_eval_sample.jsonl`.
Expected: ≈2–3k text-bearing rows, ≈500–1,000 boundaries (the script reports the real numbers).
Tests: schema, stable `case_id`, no 12-digit IDs, boundary flag, group key, on the fixture bodies.

**3.2 Labels — `/Users/jkeeter/projects/token-cop/scripts/label_routing_dataset.py`**: judge =
Opus 5.5 (`us.anthropic.claude-opus-5-5`, `bedrock-runtime.converse`), rubric "minimum sufficient
tier" (definitions + 3 examples per tier) shown **the prompt, the frontier model's actual response
snippet, and its output-token count** — richer than prompt-only judging; strict JSON
`{"tier","confidence","rationale"}`; `--resume`; cost ≈ 3k rows × ~$0.02 ≈ **$60**. Labels committed
to `/Users/jkeeter/projects/token-cop/tests/fixtures/routing_eval_labels.jsonl` (ids + labels only).
`--human-sample 60 --human-out docs/router-human-review.csv` stratified over **boundary rows** by
judge tier; `--merge-human` overrides and reports agreement. Optional calibration check (≤100 single-
turn rows, ≈$5): replay the prompt on Sonnet 5.5 and have the judge compare outputs pairwise — the
only outcome-based label available; multi-turn agentic runs cannot be replayed faithfully.
Tests: parsing (strict/fenced/malformed → unknown), seeded stratified sampling.

**3.3 Decider rows + splits — `/Users/jkeeter/projects/token-cop/router/training_data.py`** (pure):
`to_examples(case, label) -> list[dict]` emitting `Example` rows with **the same `build_state` and
question text the proxy uses at runtime** (train/serve parity): a `choice` row
(`options=[["reasoning",…],["execution",…],["polish",…]]`, `label` = tier index,
`task="tokencop_tier"`) and a `noul` row (`frontier_needed`, label 1 iff tier == reasoning,
`task="tokencop_frontier"`); `weight` 2.0 for boundary rows. `split_by_group(rows, group_key) ->
train/calib/test` (≈70/15/15 by `group_key`, test = boundary-heavy and contains all human-checked
rows). Writes `tests/fixtures/local/decider_{train,calib,test}.jsonl`. Tests: parity with
`decider_prompts.build_state`, label mapping, no group leakage across splits.

**3.4 Zero-shot baseline + recalibration (always, before go-live)**: on the EC2 box
`strands-decider calibrate <v19 ckpt> --data decider_calib.jsonl --split all` → `v19-tc` checkpoint
with temperatures fitted to your traffic; `scripts/eval_classifier.py --decider-url … --gate-sweep`
on `decider_test.jsonl` for `heuristic`, `decider-v19`, `decider-v19-tc`, `haiku`. This step is
required: the released calibration is explicitly valid only on the authors' held-out tasks.

**3.5 Fine-tune on your rows (before go-live if 3.4 fails the gate; otherwise as v2 — `--force` to
run regardless)** — `/Users/jkeeter/projects/token-cop/scripts/train_decider.py` wrapping the
published recipe on the EC2 GPU box (stop serving for the duration): `training/recipe.sh build fetch
multistep generated adequacy catchall` (public corpus, ≈100k rows) + append `decider_train.jsonl`
to `train_files` with `weight` up-weighting (and `instruction_variants` from 3–4 rubric phrasings);
`strands-decider train --config configs/tokencop.yaml` (copy of `train.yaml`, `output_dir
checkpoints/hobson-2b-tc-v1`); `calibrate` on `decider_calib.jsonl`; eval on `decider_test.jsonl`
**and** JevBench public (must stay within the 157–177 reproduction band — guards against forgetting);
package (`python -m strands_decider.hf_export export`) → `s3://…/token-cop/decider/tc-v1/` →
systemd restart with the new checkpoint; SSM `/token-cop/decider-version`. Compute ≈ 11 h on a
3090-class GPU — **measure on the L4 (g6.xlarge) first; use g6e.xlarge ($1.861/h) if >2×
slower** → ≈ $10–25 per run. Also run the cheap `init_from` head-only variant as an experiment, noting
it yields the weaker `SlotHead`. Pick the best checkpoint by test metrics; record all in `docs/router.md`.

**3.6 Keep growing the dataset**: proxy opt-in `TOKEN_COP_ROUTER_CAPTURE_BOUNDARIES=1` appends
boundary task text (scrubbed) to `~/.token-cop/router/boundaries.jsonl` (local, never committed) so
future retrains have thousands of rows; `scripts/build_routing_dataset.py --since <date>` merges new
S3 logs. Cadence: re-label + recalibrate monthly, retrain when ≥1,000 new labelled rows.

**3.7 Metrics + acceptance — `/Users/jkeeter/projects/token-cop/router/eval_metrics.py`** (pure:
confusion, per-tier P/R, coverage at gate, downgrade precision, harm rate = label above served,
cache-aware `projected_savings`, `check_acceptance`) and
`/Users/jkeeter/projects/token-cop/scripts/eval_classifier.py` (backends `heuristic`, `decider`
via `--decider-url`, `haiku` standalone prompt; `--gate-sweep 0.5:0.95:0.05`; markdown + JSON;
`--check` exits non-zero). **Acceptance to go live** (constants, tune after first run): downgrade
precision ≥ 0.90 at the chosen gate on `decider_test`; coverage ≥ 40% of boundary cases; harm rate
≤ 5%; Decider p95 ≤ 500 ms from the proxy; projected monthly savings ≥ 1.5× endpoint monthly cost
(or schedule the endpoint). Tests: metric math on a synthetic 12-case set.

### Phase 4: Live
`python -m scripts.setup_decider --set-mode live` with the chosen gate in `/token-cop/router-policy`,
`max_downgrade=1` (reasoning → execution only). Daily `router.report` for a week; upstream 400s
containing "bound to a different conversation" must be zero. Then consider `max_downgrade=2` and
`--schedule business-hours` from the break-even numbers. Add local MCP tool
`token_cop_router_report(since="7d")` to `/Users/jkeeter/projects/token-cop/mcp_server.py`
(delegates to `router.report`; test in `tests/test_mcp_server_backends.py`).

### Phase 5: Pluggable classifier for Token Cop internals
Create `/Users/jkeeter/projects/token-cop/router/classifier.py`: `Classifier` protocol
(`classify(text, *, input_tokens=0, has_code=False, message_count=0) -> TierResult`),
`HeuristicClassifier` (wraps `classify_task`), `DeciderClassifier(url, gate=0.8, fallback=Heuristic)`
(sync httpx, 2 s timeout; `probabilities → signals`, `choice → tier`, below gate → `unknown`; any
exception → fallback), `HybridClassifier` (Decider only when heuristic is `unknown`/below
`CONF_THRESHOLD`), `get_classifier()` from `TOKEN_COP_CLASSIFIER=heuristic|decider|hybrid`
(default heuristic → existing suites unchanged) + `DECIDER_URL` via `get_secret`. Wire into
`tools/invocation_logs.py` (`_parse_record`, `_enrich_entry`; `DECIDER_CLASSIFY_CAP=100` per run —
the server serializes inference) and `tools/model_router.py::_recommend_model_impl`; extend
`scripts/compare_classifier.py --classifier`. Scope note: the AgentCore runtime container cannot
reach the EC2 Decider without VPC plumbing — internals use the Decider in **local/batch** contexts
(audit runs from a developer machine, `mcp_server.py` harness path can pass `model_id=` at
`mcp_server.py:218`); runtime stays heuristic until a reachable endpoint exists. `strands-agents`
≥1.54 upgrade + `DeciderStrategy(RoutingStrategy)` for the agent's own model: separate later step.
Tests: `tests/test_task_classifier.py`.

### Phase 6: Docs (project rule: part of the feature commit)
Create `/Users/jkeeter/projects/token-cop/docs/router.md` (architecture, setup incl. plugin
prerequisite, env vars, SSM keys, IAM tables, shadow→live runbook, kill switches, troubleshooting:
thinking-block/cache rules, eventstream content-type, SSM tunnel timeouts). Update
`/Users/jkeeter/projects/token-cop/CLAUDE.md` (new "Token Cop Router (opt-in)" section with the
invariants), `/Users/jkeeter/projects/token-cop/README.md`,
`/Users/jkeeter/projects/token-cop/docs/cost-attribution.md` (revise "does not proxy Bedrock calls"),
`.env.example`, `.gitignore` (`tests/fixtures/local/`). Revise the two local specs with a dated
"Revised" header (per-request classification now at conversation boundaries; "no LLM in the
classification path" applies to the heuristic backend only). Save a memory note on the cache/
thinking-block routing invariants.

---

## Part 5: AWS resources, IAM, costs

| Who | Needs |
|---|---|
| Developer running the proxy | `bedrock:InvokeModel*` on requested **and** target profiles; `bedrock:ListInferenceProfiles`, `GetInferenceProfile`, `CountTokens`; `ssm:StartSession` on the instance + document `AWS-StartPortForwardingSession`, `ssm:TerminateSession`; `ssm:GetParameter(s)` on `/token-cop/router-*`, `/token-cop/decider-*`; `ec2:DescribeInstances`; optional `cloudwatch:PutMetricData` (namespace `TokenCop/Router`) |
| `setup_decider.py` operator | `ec2:RunInstances/Start/Stop/Terminate/Describe*/CreateSecurityGroup/CreateTags`; `iam:CreateRole/PutRolePolicy/AttachRolePolicy/CreateInstanceProfile/AddRoleToInstanceProfile/PassRole`; `ssm:PutParameter/GetParameter/DeleteParameter/GetParameters` (DLAMI public parameter); `scheduler:*Schedule` + an EventBridge Scheduler role with `ec2:StartInstances/StopInstances` |
| Instance role | `AmazonSSMManagedInstanceCore`; `logs:*` on its log group (optional) |
| Eval runner | `bedrock:InvokeModel` on Opus 5.5 + Haiku 4.5; `s3:GetObject/ListBucket` on the invocation-log bucket |

Costs (verified via Pricing API, us-east-1 on-demand, 2026-09): EC2 g6.xlarge **$0.8048/h** →
24×7 ≈ $588/mo, business hours (~240 h) ≈ $193/mo; EBS 100 GB gp3 ≈ $8/mo; (alternative SageMaker
ml.g6.xlarge $1.1267/h ≈ $822/mo, ml.g5.xlarge $1.408/h). One-off: judge labelling ≈ **$60**
(≈3k rows × ~3.5K in @ $4/M + 200 out @ $20/M), optional replay check ≈ $5, fine-tune compute ≈
**$10–25 per run** (≈11 h on the g6.xlarge, or g6e.xlarge $1.861/h if the L4 is slow), S3 GETs for
the full extraction (≈15k objects) < $1. Savings per routed Fable 5.1→Sonnet 5.5 token: input $10→$2,
output $50→$10, cache write $12.50→$2.50, cache read $0.25→$0.20. Illustrative downgraded subagent
conversation (200K cache write, 2M cache read, 50K uncached, 20K output) saves ≈ $3.30 → ≈180 such
conversations/month cover a 24×7 instance. Shadow mode produces the real number.

---

## Part 6: Rollout, kill switches, risks

Rollout: Phase 1 `off` (1 day) → Phase 2 `shadow` (5 days) → Phase 3 gate → Phase 4 `live`
subagents only (`max_downgrade=1`) → widen → Phase 5 internals.

Kill switches (fastest first): (1) `TOKEN_COP_ROUTER_MODE=off` env or
`setup_decider --set-mode off` (SSM; all proxies within 60 s) — relay continues, no Decider calls;
(2) `setup_decider --stop` (EC2 stop, ≈$0 compute) — circuit opens, passthrough, `Fallbacks` rises;
(3) `--terminate`; (4) run plain `claude` (no base URL). Pins are set only at boundaries, so a mode
flip never switches an in-flight conversation.

| Risk | Mitigation |
|---|---|
| 4096-token window, silent right-truncation | ≤8000-char task first in state; ~250-token questions; log `task_chars`; eval uses the same truncation |
| Thinking blocks bound to model | Decide only when body has zero assistant messages; pin served model per agent-id incl. passthrough; `fork` never routed; alert on "bound to a different conversation" |
| Cache cold start | Never touch main/compaction; boundaries only; pins for the agent's life |
| Eventstream relay | `aiter_raw`, no middleware, `read=330 s`, content-type verbatim, close upstream on disconnect; multi-chunk MockTransport tests |
| SigV4 with rewritten path | Sign after rewrite; `%3A` in canonical path; body untouched; unit test |
| SSM tunnel idle-timeout / plugin missing | Auto-reconnect in `router_up.sh`; health check; passthrough while down; plugin install needs your approval |
| Decider accuracy unproven / concurrency unverified | Two-key gate; shadow first; eval gate; one worker + `Semaphore(2)` + 600 ms budget; fail-open |
| GPU idle cost | `router.report` break-even; `--schedule business-hours`; `--stop` |
| Base weights unpinned upstream | Pin HF revision from `provenance.json`; `HF_HUB_OFFLINE=1` |
| Target model unavailable | Startup preflight `GetInferenceProfile` on targets; disable that downgrade on failure; ARNs always passthrough |
| Old Claude Code (no hint headers) | No class header → passthrough |
| Eval privacy / judge bias | Cases stay local & scrubbed; only labels + 30 redacted rows committed; 50 human spot-checks with agreement reported |

---

## Part 7: Verification

1. Unit: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests` (158 → +new).
2. Integration (offline): `tests/test_router_app.py` fakes Bedrock (multi-chunk eventstream, 400/429)
   and the Decider (SystemOne answers, timeouts): byte identity, path rewrite only in `live`, pins,
   fallbacks, JSONL lines.
3. Live smoke, mode `off`: `scripts/router_up.sh`; `curl -s -X POST
   http://127.0.0.1:8787/model/us.anthropic.claude-haiku-4-5-20251001-v1%3A0/invoke -H
   'content-type: application/json' -d '{"anthropic_version":"bedrock-2023-05-31","max_tokens":20,
   "messages":[{"role":"user","content":"ping"}]}'`; streaming path with `curl -N … | xxd | head`
   (binary frames, `content-type: application/vnd.amazon.eventstream`); `scripts/claude-routed -p
   "say hi"`; then a subagent spawn (`-p "Use the Explore agent to find where classify_task is
   defined"`) → a `subagent` boundary line in `~/.token-cop/router/decisions.jsonl`.
4. Shadow/live: same spawn in `shadow` shows `would_serve`; in `live` shows `served_model` =
   Sonnet 5.5 and later turns of that agent-id `action=pinned`; no retry errors in the transcript.
5. Attribution: CloudWatch Logs Insights on `/aws/bedrock/invocations`: `filter modelId like
   /sonnet-5-5/ | fields @timestamp, identity.arn, modelId` → **your** ARN, never a shared role;
   `cacheReadInputTokenCount > 0` on turn 2 of the routed subagent.
6. Decider: `setup_decider --status` (running, `/health` 200 through the tunnel); p95 ≤ 500 ms in
   `router.report`; `--stop` → `Fallbacks{reason=circuit_open}` increments, Claude Code unaffected;
   `--start` → routing resumes within 60 s.
7. Eval: `scripts/eval_classifier.py --check` passes the acceptance constants before `--set-mode live`.

## Verify at implementation time
DLAMI public SSM parameter path and torch≥2.7 availability on the AMI; Decider latency on an L4
(g6) vs the published 3090 number; SSM tunnel added latency; Claude Code accepting an `http://`
loopback base URL; exact `provenance.json` base-model revision; whether `strands-decider serve`
honors `HF_HUB_OFFLINE=1` on first load (else download once with network, then flip); the real
boundary-row count from the full extraction (the 5% figure rests on a 10-row sample); whether the
runtime's `BEDROCK_LOG_PREFIX` is `invocation-logs/AWSLogs`; recipe `build/fetch` dataset downloads
succeed on the EC2 box (public HF datasets) and the L4 training time; whether `_output.json.gz`
bodies exist for most requests (needed for response-aware judging).
