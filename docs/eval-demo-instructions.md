# Evaluations Demo: Presenter Instructions

A guide for presenting the AgentCore Evaluations demo. Covers the storyline,
what to say during each act, what to show on screen, and how to handle questions.

**Total runtime**: ~8-10 minutes (plus Q&A)

## Before the Demo

### 30 minutes before

```bash
source .venv/bin/activate

# Reset to clean slate
python -m scripts.eval_demo --reset

# Generate fresh session data (if needed)
# Run 3-5 queries through the gateway:
python3 -c "
from mcp_server import _call_via_gateway
queries = [
    'What is my Bedrock usage today?',
    'Am I on track for a \$500 monthly budget?',
    'Compare Bedrock vs OpenRouter costs this week',
]
for q in queries:
    print(f'Query: {q}')
    result = _call_via_gateway(q)
    print(f'  Response length: {len(result)} chars')
    print()
"

# Wait 3-5 min for CloudWatch span ingestion, then verify:
agentcore eval run -e Builtin.Helpfulness
# Should show "Evaluating session: <ID>" with results
```

### Right before

- Terminal open, venv activated, in project directory
- Font size large enough for audience to read
- No sensitive credentials visible in terminal history

---

## The Storyline

**One sentence**: "We deployed an agent, it silently broke in production, and
AgentCore Evaluations would have caught it automatically."

**Narrative arc**:
1. We built Token Cop — an agent that tracks LLM spending
2. It worked great in dev, we deployed it
3. In production, it silently started returning empty responses
4. We caught it manually after 50+ bad responses
5. AgentCore Evaluations has built-in and custom evaluators that would have flagged this immediately
6. You can set up continuous monitoring so this never happens again

---

## Act 1: "The Bug We Caught Manually"

**Duration**: ~2 minutes
**Command**: `python -m scripts.eval_demo --act 1`

### What to say

> "Let me tell you about a real bug we hit. Token Cop is a Strands Agent
> deployed on Bedrock AgentCore. It queries CloudWatch, OpenRouter, and OpenAI
> APIs to tell you how many tokens you've used and what it costs."
>
> "After we deployed it, we started getting responses like this..."
>
> [Point to the "Before fix" box on screen]
>
> "Empty strings. Or just 'Your data has been saved.' The agent was actually
> fetching the data correctly — but then it was calling save_snapshot as its
> last action, and the Strands framework's str(response) only returns text
> from the last message. So the actual data got buried."
>
> "This went undetected through 50+ invocations. Nobody noticed because the
> agent wasn't crashing — it was just silently returning the wrong thing."
>
> "Now, what if we'd had evaluations running? Let's find out."

### What to show

- The before/after boxes the script prints
- Emphasize: the agent wasn't failing, it was silently misbehaving
- This is the gap that evaluations fill — catching quality issues, not crashes

### Audience questions to anticipate

- **"How did you eventually catch it?"** — Manual testing. Someone ran a query and noticed the empty response. Then we ran 10 more and saw the pattern.
- **"Couldn't you have caught this with unit tests?"** — The bug was in how the framework serialized the response, not in the business logic. The tools worked fine. The data was there. It was the *last mile* that broke.

---

## Act 2: "Built-in Evaluators"

**Duration**: ~2-3 minutes
**Command**: `python -m scripts.eval_demo --act 2`

### What to say

> "AgentCore comes with 13 built-in evaluators. Let me run four of them
> against a real session from our deployed agent."
>
> [Script discovers session and starts evaluating]
>
> "We're running Helpfulness, ResponseRelevance, GoalSuccessRate, and
> ToolSelectionAccuracy. These are LLM-as-judge evaluators that look at the
> actual conversation traces stored in CloudWatch."
>
> [Point to CLI equivalent printed on screen]
>
> "You can do this from the CLI too — one command. And this works against
> any agent deployed on AgentCore, not just Token Cop."

### What to show

- The session ID being auto-discovered
- The 4 evaluator names and what each measures
- The CLI equivalent command (emphasize simplicity)
- Scores and explanations — expect scores like Helpfulness=0.83, Correctness=1.0
- The detailed LLM-judge explanations analyzing response quality

### Key talking points

- **13 built-in evaluators** across response quality, safety, and component-level metrics
- **Three levels**: SESSION (whole conversation), TRACE (single turn), TOOL_CALL (individual tool invocations)
- **No code changes needed** — evaluators work on CloudWatch traces automatically
- The CLI command is the same for any AgentCore agent
- **Rich explanations** — the judge doesn't just score, it explains WHY

---

## Act 3: "Custom Evaluators"

**Duration**: ~2 minutes
**Command**: `python -m scripts.eval_demo --act 3`

### What to say

> "Built-in evaluators are great for general quality, but every agent has
> domain-specific requirements. Token Cop's #1 requirement is: the response
> must contain actual numbers — token counts, dollar amounts, tables."
>
> "So let's create a custom evaluator for exactly that."
>
> [Script creates token_cop_data_completeness]
>
> "This evaluator uses LLM-as-judge with custom instructions. It scores 0
> if the response is just a save confirmation, and 1 if it has real data.
> If we'd had this running when the bug hit, every single response would
> have scored 0 — instant detection."
>
> [Script creates token_cop_cost_formatting]
>
> "We also create one for formatting compliance — are costs shown as $X.XX?
> Are large numbers using commas? These are the rules in our system prompt,
> and now we can enforce them automatically."

### What to show

- The evaluator names being created
- Point out that configs are in `evaluators/token_cop_evaluators.json` — version-controlled, repeatable
- The CLI equivalent command
- Emphasize: `{context}` and `{assistant_turn}` placeholders let the judge
  see the full conversation

### Key talking points

- **Custom evaluators are LLM-as-judge** — you write natural language instructions
- **Template placeholders** (`{context}`, `{assistant_turn}`, `{actual_trajectory}`) give the judge access to conversation data
- **Rating scales** define what scores mean — can be binary (0/1) or granular (0-6)
- **Model config** lets you choose which model acts as judge
- Evaluator definitions are JSON, live in source control, created via SDK or CLI

---

## Act 4: "Online Evaluation"

**Duration**: ~1-2 minutes
**Command**: `python -m scripts.eval_demo --act 4`

### What to say

> "Running evaluations manually is useful, but the real power is continuous
> monitoring. Let's set up online evaluation."
>
> [Script creates the config]
>
> "We're sampling 100% of invocations — every query gets evaluated by
> Helpfulness, ResponseRelevance, and our custom data_completeness evaluator.
> In production you'd set this to 1-10%, but for the demo we want to catch
> everything."
>
> "With this running, the save_snapshot bug would have been caught within
> the first few invocations. The data_completeness evaluator would score 0
> on every empty response, and you'd see it immediately in CloudWatch."

### What to show

- The config name, agent, sampling rate, and evaluator list
- The CLI equivalent (one command to set up continuous monitoring)
- If the config already exists (re-running), show the status instead

### Key talking points

- **Sampling rate** — 100% for dev, 1-10% for production (cost control)
- **CloudWatch integration** — results appear in CloudWatch metrics dashboard
- **Multiple evaluators** — mix built-in and custom in one config
- **Auto-creates IAM role** — the SDK handles the execution role setup
- This is the "set and forget" path — catches regressions without manual testing

---

## Act 5: "Summary & Cleanup"

**Duration**: ~1 minute
**Command**: `python -m scripts.eval_demo --act 5`

### What to say

> "Let me recap what we showed:"
>
> [Point to the summary table]
>
> "We went from a silent production bug, to on-demand evaluation with
> built-in evaluators, to custom domain-specific evaluators, to continuous
> monitoring — all in about 8 minutes. And all of these work with any
> Strands Agent on AgentCore."
>
> "Here are the CLI commands you'll use most often."
>
> [Point to CLI reference]

### Cleanup decision

- If demoing again soon: say N to cleanup
- If done for the day: say Y or run `--reset` later
- If audience wants to explore: keep resources up and share CLI commands

---

## Bonus: Regression Suite

**Duration**: ~2 minutes (optional, great for technical audiences)
**Command**: `python -m scripts.eval_regression --cases 0,3,4`

### When to show

- Technical audience asking "how does this work in CI?"
- If on-demand evaluation returned no results (span format issue)
- If someone asks about testing before deployment

### What to say

> "For pre-deployment validation, we have a regression suite that runs the
> agent locally, captures OTEL spans in-memory, and evaluates each response
> with AgentCore evaluators."
>
> [Run with 3 cases to keep it quick]
>
> "Each case gets a Helpfulness score. The save confirmation bug would score
> well below the 0.7 threshold — it would FAIL immediately. Our current
> agent scores 0.94 average."

### What to show

- Cases running with real Bedrock calls
- PASS/FAIL status with scores
- The summary line: "3/3 PASS | Average score: 0.94"

---

## Handling Questions

### "How much does evaluation cost?"

Each evaluation makes an LLM call (the judge). Cost depends on the model
you choose and how much conversation context it evaluates. For Sonnet as
the judge, expect ~$0.01-0.05 per evaluation. Online evaluation at 1%
sampling on a high-traffic agent costs pennies per day.

### "Can I use a different judge model?"

Yes. The `modelConfig` in the evaluator definition specifies which model
acts as judge. You can use any model available in your Bedrock account.

### "Does this work with agents not built on Strands?"

The on-demand and online evaluation works with any agent deployed on
AgentCore that emits OTEL traces. The regression suite specifically uses
the Strands evals integration but could be adapted for other frameworks.

### "Can I fail a deployment based on evaluation scores?"

Yes — the regression suite returns pass/fail results that can be checked
in CI. Set the threshold via `test_pass_score` on the evaluator
(default 0.7). Non-zero exit code on failure is straightforward to add.

### "What about latency? Do evaluations slow down the agent?"

On-demand and online evaluations run asynchronously — they don't add
latency to the agent's response. The regression suite runs sequentially
but that's expected for a test suite.

### "Can I see evaluation results in a dashboard?"

Yes. Online evaluation results go to CloudWatch metrics. The AgentCore
GenAI Observability Dashboard shows evaluation scores alongside latency,
error rates, and token usage.

---

## Quick Reference

```bash
# Full demo
python -m scripts.eval_demo

# Single act
python -m scripts.eval_demo --act 2

# Reset between demos
python -m scripts.eval_demo --reset

# Regression suite (quick, 3 cases)
python -m scripts.eval_regression --cases 0,3,4

# Regression suite (full, 8 cases)
python -m scripts.eval_regression

# List what was created
agentcore eval evaluator list
agentcore eval online list
```
