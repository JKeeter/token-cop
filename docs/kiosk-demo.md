# Kiosk Demo

A self-running, ~5-minute visual demo of Token Cop — a standalone React/Vite
single-page app in `kiosk/`, modeled on the AgentCoreKiosk ("Ledgerly") booth
kiosk: a declarative scene playlist with time-coded captions, RECORDED/SLIDE
badges, watchdog budgets, pause-on-touch, and an attract loop with a QR code.

## Running it

```bash
cd kiosk
npm install
npm run dev          # http://localhost:5173/?kiosk=1
```

Booth / presentation mode:

```bash
npm run build && npm run preview
open -na "Google Chrome" --args --kiosk "http://localhost:4173/?kiosk=1"
```

| Control | Effect |
|---|---|
| `?kiosk=1` / `?kiosk=0` | enable / disable kiosk mode (persists in localStorage) |
| `?kioskFast=1` / `=0` | 5% speed (full loop in ~15s, for smoke tests) / normal |
| ← / → (PageUp/PageDown) | jump to previous / next scene (presenter mode) |
| Space | manual pause / resume (holds indefinitely) |
| Esc Esc (within 1.5s) | exit kiosk mode |
| any touch / scroll / other key | pause 60s, then auto-resume |

The scene index persists, so a reload resumes mid-playlist. Repeated scene
failures escalate retry → reload → a full-screen "attendant needed" card
(scene errors ring-buffer in localStorage `tokencop.kiosk.log`).

## The playlist (~284s)

Dwells are tuned to the narration clips (`kiosk/narration.txt`): each scene
outlasts its spoken paragraph by ~2s, so there's no dead air between scenes.
If you re-record narration, re-check dwells against the new clip lengths.

| # | Scene | Badge | Dwell | Content |
|---|-------|-------|------:|---------|
| 1 | attract | — | 20s | TOKEN COP lockup + repo QR |
| 2 | hook-uber | SLIDE | 25s | deck slide: 2026 AI-cost-crisis headlines (Ken Burns) |
| 3 | cost-anatomy | SLIDE | 16.5s | deck slide: $700 of every $1,000 is inference |
| 4 | snowball | SLIDE | 23s | native animated token counters (3,000 → 6,600/turn, 18,800 total; cache-aware kicker: re-reads bill at ~10%, still 64% of real spend) |
| 5 | arch-mcp | SLIDE | 43s | native AWS-icon diagram: /tokcop → MCP server → Cognito → Gateway → Lambda → Runtime → 13 tools, 6 highlight steps |
| 6 | replay-spend | RECORDED | 30s | real capture: 7-day Bedrock spend by model |
| 7 | replay-audit | RECORDED | 30s | real capture: full token audit |
| 8 | replay-recommend | RECORDED | 26s | real capture: model recommendation (polish tier) |
| 9 | arch-platform | SLIDE | 37s | native diagram: AgentCore platform + budget-enforcement loop |
| 10 | dashboards | RECORDED | 19s | Streamlit dashboard screenshot montage |
| 11 | closing | — | 14s | mantra + QR |

Debug override: set localStorage `tokencop.kiosk.playlist` to e.g.
`[{"id":"replay-spend"},{"id":"arch-mcp","maxMs":20000}]` to pin a subset.

## Refreshing the recorded MCP captures

The three terminal-replay scenes play **genuine** `token_cop` MCP exchanges
stored in `kiosk/public/captures/*.json`. Before an event, re-record them from
a Claude Code session in this repo (the `mcp__token-cop__token_cop` tool):

1. `What did we spend on Bedrock in the last 7 days, by model?` → `spend-by-model.json`
2. `Run a token audit for the last 7 days.` → `token-audit.json`
3. `We need to summarize 500 support tickets daily — which model should we use?` → `recommend-model.json`

Paste the verbatim response into `responseMarkdown`, update `capturedAt`, and
run `npm run captures:check`. Streaming speed auto-fits long responses to the
scene budget, so no trimming is needed. If a caption cites a number (e.g. the
$188 total), update it in `src/kiosk/playlist.ts` to match the new capture.

## Refreshing the slide/image assets

Preferred (no extra software): open the deck in PowerPoint, **File → Export →
PNG** (exports every slide to a folder), copy the cost-crisis-headlines slide
to `kiosk/public/slides/hook-uber.png` and the "Anatomy of Agentic AI Cost"
slide to `cost-anatomy.png`, then `sips --resampleWidth 1920` both. PowerPoint
**cannot be scripted** for this — its sandbox rejects AppleScript save targets
(`sandbox_extension_issue_file`), so the export is a manual step.

```bash
bash kiosk/scripts/export-slides.sh   # always refreshes dash/QR copies;
                                      # renders slides only if LibreOffice +
                                      # poppler happen to be installed
```

Caveats for the automated path (hard-won):

- 32 of the deck's 62 slides are hidden, so **PDF page numbers ≠ pptx slide
  numbers**; the page mapping in the script was verified visually.
- LibreOffice silently produces nothing when a stale profile lock exists; the
  script uses a throwaway `-env:UserInstallation` profile to avoid this.

## AWS icons

`kiosk/src/kiosk/overlay/aws/` holds official AWS Architecture Icons (48px
family) — most vendored from AgentCoreKiosk, four extracted from the official
Icon-package (see `aws/README.md`). The architecture scenes
(`ArchMcpFlow.tsx`, `ArchPlatform.tsx`) map caption steps to lit nodes via
`HOT_BY_STEP`, same pattern as Ledgerly's `ArchitectureSlide`.

## Numbers cited in captions

- $188 7-day spend, 88% cache hit rate — from `captures/spend-by-model.json` / `token-audit.json`
- ~$0.08/day vs ~$22.50/day, 95% cheaper — from `captures/recommend-model.json`
- 11.9s harness vs 24.6s container — from `docs/harness-demo-talk-track.md` (2026-09-11 run)
- 3,000 / 4,000 / 5,200 / 6,600 / 18,800 tokens — from the deck's snowball slide
- cache re-reads billed at ~10%, cached context 64% of real spend — Bedrock/Anthropic prompt-caching pricing + the cost breakdown in `captures/token-audit.json` (~$120 cache ops of $188 total)
