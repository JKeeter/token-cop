# Kiosk Demo — Talk Track

Narration for the ~5-minute kiosk loop (`docs/kiosk-demo.md`), written to be
spoken at a relaxed ~150 words/minute so each scene's lines fit inside its
dwell with air to spare. Numbers match the recorded captures and committed
docs — if you re-record captures, re-check the spend/audit lines.

For TTS, the narration text alone (no headers/cues) is in
`kiosk/narration.txt` — one paragraph per scene, blank-line separated, numbers
written out as words so the voice doesn't garble "$188.36" or "/tokcop".

If presenting live: run with sound off, let the captions carry the detail, and
speak these lines over each scene. Space holds a scene if a question comes up;
← → move at your pace.

| # | Scene (dwell) | On screen | Say |
|---|---|---|---|
| 1 | attract (20s) | TOKEN COP lockup + QR | This is Token Cop — a cost cop for AI agents, running on Amazon Bedrock AgentCore. Every token your agents spend, it sees, audits, and — if you want — enforces. Here's five minutes on how it works. |
| 2 | hook (25s) | 2026 cost-crisis headlines | These are real twenty-twenty-six headlines. Inference is draining enterprise AI budgets, and overspending has become a meme. The pattern repeats everywhere: adoption soars first, and only then does anyone ask — which user, which model, which workload actually spent the money? Usually, nobody can answer. |
| 3 | anatomy (16.5s) | cost-anatomy slide | Break down an agent's monthly bill and the story is simple: roughly seventy percent is LLM inference. Not compute, not storage — tokens. That is the line Token Cop polices. |
| 4 | snowball (23s) | animated turn bars → 18,800 | And tokens compound. Every chat turn re-sends the whole history, so turn one costs three thousand tokens and turn four costs sixty-six hundred. Prompt caching discounts those re-reads by ninety percent — yet at this volume, cached context was still sixty-four percent of our real bill. |
| 5 | arch-mcp (43s) | skill-path diagram, 6 steps | So how does it work? A developer asks a question — slash tokcop — right inside Claude Code. A local MCP server signs the call with Cognito: a real JWT, not a prompt claim. The request crosses the AgentCore MCP Gateway to its Lambda target, and lands on AgentCore Runtime, where a Strands agent runs Claude on Bedrock. From there it reaches thirteen tools — CloudWatch, Cost Explorer, S3 invocation logs, provider APIs, AgentCore Memory. The answer comes back scrubbed of secrets. Seconds, not spreadsheets. |
| 6 | replay-spend (30s) | recorded terminal replay | What you're watching now is a real exchange, recorded from the live tool — not a mock-up. The question: what did we spend on Bedrock in the last seven days, by model? Token Cop pulls CloudWatch metrics and prices every model from its own pricing table. One hundred eighty-eight dollars across six models, an eighty-eight percent cache hit rate — and it calls out that caching saved about a hundred and forty-six dollars. |
| 7 | replay-audit (30s) | recorded audit replay | Next, a full token audit — also recorded live. Six efficiency dimensions: cache utilization, model mix, context efficiency, output ratios, and more. It grades real usage and hands back the receipts — here, an exceptional cache strategy, but most of the spend sitting on premium models, with a concrete recommendation to shift mid-tier work down a tier. |
| 8 | replay-recommend (26s) | recorded recommendation | Here's where it earns its badge. Summarize five hundred support tickets daily — which model? Token Cop classifies the task: that's polish-tier work, not frontier-model work. Nova Lite at about eight cents a day, instead of twenty-two fifty on a reasoning model. Same job, ninety-five percent cheaper. That's a smart token. |
| 9 | arch-platform (37s) | platform diagram, 5 steps | One agent, a whole platform. AgentCore Runtime at the center, with Memory and the MCP Gateway beside it. Cognito identity and Cedar policies gate every tool call — permit, forbid, audit. OpenTelemetry spans flow to X-Ray and CloudWatch, so every token and every tool call is traced. A harness twin runs the same agent as pure config. And if someone blows their budget, a meter Lambda flips an IAM deny. That is enforcement, not just a dashboard. |
| 10 | dashboards (19s) | Streamlit montage | For the team, there's a dashboard: live spend, model mix, cache utilization. Recommendations rank the cheapest safe model for each workload, and efficiency scores show whether each tier is earning its cost — all from the same data the agent uses. |
| 11 | closing (14s) | mantra + QR | The mantra: more tokens is fine — they need to be smart tokens. Scan the QR, clone the repo, and put a cop on your token beat. Thanks for watching. |

## Timing notes

- Start each scene's lines about a beat after the scene appears; the attract
  and closing scenes have the most slack.
- Scene 5 (arch-mcp) is the densest — its six sentences land roughly one per
  highlight step; if you fall behind, drop "Seconds, not spreadsheets."
- Scenes 6–8 stream for most of their dwell; speak over the streaming, don't
  wait for it to finish.
- Scene 4's cache line is deliberate: cache reads bill at ~10% of the input
  rate (not free), and in the recorded 7-day audit cache operations were still
  ~64% of total spend — the snowball shrinks in price, not in volume.
- Total narration ≈ 570 words ≈ 4¼ minutes of speech inside the ~4¾-minute
  loop; dwells are tuned so each scene ends ~2s after its paragraph.
