---
name: tokcop-audit
description: Run a combined token efficiency + context environment audit
---

# Token Cop Audit

Run a comprehensive audit of both your LLM token usage efficiency AND your Claude Code environment context overhead.

**Mantra: "More tokens is FINE — are they SMART tokens?"**

## Steps

1. **Context audit** — Run the `token_cop_context_audit` MCP tool with the current project directory to inspect the local Claude Code environment (CLAUDE.md sizes, MCP server overhead, skill count, duplication).

2. **Usage audit** — Call the `token_cop` MCP tool with the prompt: "Run a token audit for the last 7 days"

3. **Unified dashboard** — Merge both reports and present findings as a single dashboard:

   ```
   ## Token Cop Audit Report

   ### Usage Efficiency (remote)
   Overall Grade: [grade] ([score]/10)
   - Document Ingestion: [score] — [detail]
   - Model Mix: [score] — [detail]
   - Cache Utilization: [score] — [detail]
   - Cost Concentration: [score] — [detail]
   - Efficiency Trend: [score] — [detail]
   - Top Savings: [action] (~$[amount]/week)

   ### Environment Context (local)
   Context Grade: [grade]
   Total context overhead: ~[tokens] tokens per session
   - Global CLAUDE.md: [tokens] tokens
   - Project CLAUDE.md: [tokens] tokens
   - MCP servers: [count] servers, ~[tokens] tokens
   - Skills: [count] loaded, ~[tokens] tokens

   ### Top Recommendations
   1. [highest impact recommendation]
   2. [next recommendation]
   3. ...
   ```

4. Close with the mantra reminder: **"More tokens is FINE — but they need to be SMART tokens."**

5. Suggest scheduling recurring audits: run `/loop 1w /tokcop-audit` for automatic weekly Monday runs.
