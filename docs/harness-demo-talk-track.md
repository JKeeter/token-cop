# Token Cop on AgentCore Harness — CTO demo talk track

**Runtime:** 20–25 minutes with pauses. 12 minutes if you use `--no-pause` and talk over it.
**Audience:** CTO / VP Eng / platform lead. They care about velocity, governance, cost, and lock-in.
**Driver command:** `python -m scripts.harness_demo` (pauses between acts) or `--act N` for one act.

Numbers below are from the first live run on 2026-09-11. Your run will differ; the shape of the
story will not. Where a number is quoted, say "on our last run" and read the live value.

---

## Pre-flight (5 minutes before)

```bash
source .venv/bin/activate
python -m scripts.setup_harness --status          # everything OK, harness READY
python -m scripts.setup_policies --status         # policy engine attached, LOG_ONLY
aws sts get-caller-identity                       # right account
```

Open in a browser tab: CloudWatch → GenAI Observability → Harnesses
(`https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#gen-ai-observability/agent-core`).

Have `docs/harness.md` open for the architecture diagram if someone asks "where does this run?"

---

## Opening (90 seconds, before any command)

> "Token Cop is an agent that audits our LLM spend. It has been running for six months as a
> Strands agent we wrote, in a container we build, on AgentCore Runtime. That works. But every
> prompt tweak, every model swap, every new tool is a container rebuild through CodeBuild.
>
> In June, AWS shipped the AgentCore *harness*: the agent loop as a managed service. You declare
> model, prompt, tools, and limits. AWS runs the loop. No container.
>
> The question I wanted answered, and the one I think you care about: **can our real agent run
> that way, does it stay governed, and what does it cost us to find out?** So we built the twin.
> Same agent, same tools, same policies, two chassis. Today I'll run them side by side."

Show the diagram from `docs/harness.md`. Point at the one thing that matters:

> "Both chassis reach the same 13 tools through the same MCP Gateway. That gateway is where our
> Cedar policies live. Governance is a property of the tool plane, not of whichever loop is
> calling it. Hold onto that; it is the punchline of Act 5."

---

## Act 1 — Same question, two chassis (3 min)

**Command:** `python -m scripts.harness_demo --act 1`

**While it runs (about 40 seconds):**

> "One prompt: what did we spend on Bedrock in the last seven days, by model. The left column is
> our container. The right is the harness. Neither has been told about the other."

**When the table appears:**

| Talking point | What to point at |
|---|---|
| Same data | Both tables show the same models and dollar figures. Same tool, same CloudWatch query. |
| Harness reports its own bill | `in/out tokens` and `est. $` columns are filled only for the harness. The stream carries per-call token usage; the container path never gave us that without digging in CloudWatch. |
| Wall time | Last run: container 24.6 s, harness 11.9 s. Don't oversell this; it's one question, one run. Say "comparable, and the harness had no cold-start disadvantage." |

> "Nothing about the tools changed. The 13 Python functions we wrote now sit in a Lambda behind
> the gateway. The schema the harness sees is *generated from our Strands tool definitions*, so
> the two chassis cannot drift. That was the whole migration."

Click the Observability link. Show the session in the Harnesses tab. Say: "automatic. We deleted
our hand-rolled tracing code on this path."

---

## Act 2 — Trim the harness (2 min)

**Command:** `python -m scripts.harness_demo --act 2`

> "Every tool a loop exposes costs input tokens on every model call, used or not. The harness
> ships built-in shell and file tools. Token Cop is a usage-analytics agent; it never needs a
> shell. Watch what removing them does."

**When the table appears:**

> "Same question twice. With built-ins exposed: about 10,700 input tokens. Restricted to our
> gateway tools: about 9,400. That's roughly 650 tokens per model call for tools we never touch,
> which lines up with AWS's own figure of about 900."

**The CTO point:**

> "This is Token Cop's 'context overhead' audit dimension, applied to itself. On the container,
> fixing this is a code change. On the harness, it's one config field: `allowedTools`."

---

## Act 3 — Act on Token Cop's own advice (4 min)

**Command:** `python -m scripts.harness_demo --act 3`

> "Token Cop has a tool called `recommend_model`. Give it a task, it tells you which tier to run
> on: reasoning, execution, or polish. The advice has always been easy to give and hard to act on,
> because the model is baked into the deployment."

**Step A appears (classification):**

> "Task: fix typos and tidy a budget summary. Verdict: polish tier. Nova Lite, GPT-4o mini,
> Haiku."

**Step B appears (the two runs):**

> "Now we act on it. Same polish task, same session shape, two invocations. First on Sonnet 4,
> our default. Then on Haiku 4.5, passed as a *per-call parameter*. No redeploy, no new version,
> nothing changed on the harness resource."

Read the table. Last run: **67% cheaper on Haiku, identical markdown table as output.**

> "And it works mid-conversation. A long session can plan on a strong model and drop to a cheap
> one for the last mile, without rebuilding the conversation. Token Cop measures. The harness lets
> you act on the measurement in the same breath."

---

## Act 4 — Hard caps (2 min)

**Command:** `python -m scripts.harness_demo --act 4`

> "Our existing budget enforcement works at the IAM layer: a principal that overspends loses
> Bedrock access next month. That bounds the month. It doesn't bound a single runaway call. The
> harness adds caps inside the loop."

**When results appear:**

| Cap | Stop reason | Say |
|---|---|---|
| `maxTokens=100` | `max_output_tokens_exceeded` | "Output budget hit after the first turn; the loop stopped before spending more." |
| `maxIterations=1` | `max_iterations_exceeded` | "One tool call allowed, then stop. A runaway agent can't loop." |

**One honest detail worth saying out loud** (it builds credibility):

> "A note from testing: the token budget is checked *between* iterations, not mid-generation. A
> 200-token cap did not stop a two-turn answer that produced 735 tokens, because the second turn
> was already running. Set caps below the first turn's expected output if you want a hard stop.
> That's the kind of thing you only learn by running it."

---

## Act 5 — One policy plane (5 min) — the punchline

**Command:** `python -m scripts.harness_demo --act 5`

> "Earlier I said governance lives in the tool plane. Let's prove it. Our gateway has a Cedar
> policy engine attached in log-only mode. I'm going to flip it to enforce and add a blanket
> forbid. Then I'll hit both chassis."

**Before / denied lines appear:**

> "Before: the gateway lists 15 tools. After the forbid: zero. Our Claude Code integration is now
> denied. Now the harness asks the same spend question."

**Now slow down. This is the moment.**

The denied harness answer will show a fake tool transcript with invented dollar figures, and the
line above it will read `UNVERIFIED` with `GUARD: answer contains a hand-written tool transcript`.

> "Look at what the model did. It had zero tools bound, and it *wrote a tool call and a result by
> hand*, with made-up numbers. It looks exactly like a real answer. We put an explicit rule in the
> system prompt, 'never invent figures, never write example tool calls,' and it did it anyway.
>
> This is the finding I most want you to take away. **A policy deny that the model can't see is a
> hallucination generator.** The deny worked; the gateway blocked the call. But nothing told the
> model, so it improvised.
>
> Token Cop's answer is a client-side guard: it compares the answer to the trace. No tool call
> plus dollar figures equals *unverified*, and the MCP response gets a warning banner. Cedar
> enforces; the guard makes the enforcement visible in the answer. You need both."

**Restore line appears:**

> "Policy deleted, gateway back to log-only, both chassis allowed again. Same policy, same
> engine, both loops. Governance did not care which chassis was calling."

If asked "why not just make the model refuse?": "We tried. Prompt rules are advisory. The trace
is ground truth."

---

## Act 6 — Ship, roll back, escape hatch (4 min)

**Command:** `python -m scripts.harness_demo --act 6`

> "Last act is about operating this thing. Three questions a CTO asks about any managed service:
> how do I ship a change, how do I undo it, and how do I leave."

**Step 1–2 (versions, PROD endpoint):**

> "Updating the prompt created version N+1. Every update is an immutable version. `DEFAULT`
> follows the latest. I pin a `PROD` endpoint to the previous version. Two endpoints, two versions,
> one harness."

**Step 3 (invoke PROD):**

> "Callers pass a qualifier. PROD answers on the old version while DEFAULT is already on the new
> one. That's a canary for free."

**Step 4 (promote, roll back):**

> "Promote PROD forward. Roll it back. Each is one API call, seconds, no rebuild. Compare that to
> a container rollback through CodeBuild and ECR."

**Step 5 (export):**

If the export runs, show the diff against `agent/agent.py`:

> "And when configuration stops being enough — we need a hook, a custom loop, a library the
> harness doesn't expose — `agentcore export harness` hands back Strands code. That is the
> framework we already run. Start declarative, graduate to code when you earn the complexity.
> There is no lock-in cliff."

If the export prints the CLI-upgrade note instead, say exactly that:

> "This machine's CLI predates the export command; the path exists and the docs cover it. I'd
> rather show you a real limitation than fake a diff."

---

## Close (2 min)

> "So, the answer to the question I opened with.
>
> **Can our real agent run on the harness?** Yes. Two days of work, and the container is untouched.
> Both chassis are live right now.
>
> **Does it stay governed?** Yes, because governance was never in the agent. It's in the gateway,
> and both loops go through it. Act 5 also taught us that a deny must be visible to the model or
> you get confident fiction; we fixed that with a guard that compares answers to traces.
>
> **What did it cost to find out?** No harness fee. We pay for the underlying compute and tokens
> we already pay for. The Lambda and gateway target are pennies.
>
> **Where would I use which?** Harness for anything that is a straight tool-calling loop and
> benefits from fast iteration, model swaps, caps, and canary endpoints. Runtime when we need
> hooks, custom orchestration, or a framework the harness doesn't support. And the export command
> means the choice is reversible."

Leave this on screen:

```
python -m scripts.setup_harness --status
```

> "Everything you saw is reproducible from the repo: `setup_harness --enable` builds the twin,
> `harness_demo` runs the six acts, `--teardown` removes it."

---

## Anticipated questions

**"Is this production-ready or a demo toy?"**
The harness is GA across most AgentCore regions. What we built in two days is a real deployment:
IAM roles scoped per the AWS sample policy, OAuth between harness and gateway through AgentCore
Identity, immutable versions, named endpoints. The parts I'd harden before production are the
Lambda's S3 permission (currently broad) and pinning the Lambda's Strands version to match the
container's.

**"What can't the harness do?"**
No hooks, no custom loop, no in-process tools, no framework choice. If you need those, export to
code and run on Runtime. Same platform, same observability, same gateway.

**"Where do the model's tokens go? Is our data leaving AWS?"**
Same Bedrock models, same region, same account as the container. The harness is a Runtime
underneath; CloudTrail records it as one.

**"Why not just use the harness for everything?"**
We might. Today the runtime carries our custom tracing and a response-extraction safety net that
the harness makes redundant. If the harness keeps parity for a quarter, the container becomes the
special case, not the default.

**"The model made up numbers. Isn't that disqualifying?"**
It's the most useful thing we learned. Any agent with tools denied will do this; we only saw it
because we tested the deny path. The guard turns it from a silent failure into a flagged one. I'd
rather ship the agent that knows when it's guessing.

**"What about per-user attribution through the harness?"**
SigV4 callers don't propagate per-user identity to tools yet (AWS says it's coming). The harness
supports a Cognito JWT authorizer if we need user-scoped access today.

---

## Backup: if AWS is slow or the demo breaks

- `--no-pause --json > demo-numbers.json` before the meeting captures every number; keep the
  file open as a fallback.
- Act 5 always restores LOG_ONLY in a `finally`. If it fails mid-way, run
  `python -m scripts.setup_policies --status` on screen and show the mode.
- If the harness is UPDATING (someone ran `--update-prompt`), wait for READY in `--status`;
  invocations queue behind updates.
- The single-command fallback that shows the whole story in one line:
  `python -m agent.harness_client "What did we spend on Bedrock in the last 7 days, by model?"`
