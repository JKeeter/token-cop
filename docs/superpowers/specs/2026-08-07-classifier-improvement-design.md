# LLM-Free Task Classifier Improvement — Design

- **Status:** Draft for review
- **Date:** 2026-08-07
- **Scope:** Rework the deterministic task classifier (`classify_task`) and the
  Bedrock invocation-log parser that feeds it. Design + implementation intent.
- **Constraint:** No LLM in the classification path. Until the Bifrost gateway
  add-on exists (see `2026-07-07-bifrost-gateway-addon-design.md`), classification
  must remain a pure, deterministic heuristic.

---

## 1. Summary

`classify_task` (in `models/model_tiers.py`) maps a task into one of three model
tiers — `reasoning` / `execution` / `polish` — using keyword frequency matching.
It has four known weaknesses:

1. **Silent default to `execution`.** Any text with zero keyword matches falls to
   `execution` with no signal that the classifier had no evidence.
2. **Keyword ties & equal weighting.** Every signal counts as 1; a task hitting one
   strong reasoning phrase and three weak polish words misclassifies.
3. **Ignores structural signals.** A 90K-token prompt with code is almost never
   `polish`, regardless of keywords.
4. **Last-message / no-context blindness.** In the log path, only the final user
   message is classified.

This design reworks the classifier into a **weighted additive scoring model with a
confidence score and an explicit `unknown` tier** (Approach 1). Structural signals
(input size, code presence, message count) are optional inputs, so one function
serves both consumers.

### Critical prerequisite discovered during design (Layer A)

Validation against real logs (bucket configured via `BEDROCK_LOG_BUCKET` /
SSM `/token-cop/bedrock-log-bucket`, referenced here as `<BEDROCK_LOG_BUCKET>`)
revealed the current parser does not actually feed the classifier on this data:

- **Token counts are read from the wrong level.** `_parse_record` reads top-level
  `record["inputTokenCount"]` (which is `null`); the real value is nested at
  `record["input"]["inputTokenCount"]`. All input-size signals are currently zero.
- **Message text is offloaded.** Records carry `input.inputBodyS3Path` (a pointer to
  a separate `*_input.json.gz`), not inline `messages`. The parser looks for inline
  `input_body["messages"]`, finds none, so `user_message_text` is empty and
  `classify_task` is never called.
- **Path shape differs.** Real keys are
  `AWSLogs/{account}/BedrockModelInvocationLogs/{region}/YYYY/MM/DD/HH/…` — the
  `{region}` segment is not handled by `_list_log_objects`.

Improving classifier *logic* is moot on this data until the parser feeds it real
inputs. So the work is two layers:

- **Layer A — parser fidelity** (prerequisite): nested token counts, region-aware
  listing, capped fetch of offloaded bodies.
- **Layer B — classifier rework** (the requested work): weighted scoring, confidence,
  `unknown`, structural signals.

---

## 2. Architecture & Boundaries

Three files change. The classifier stays pure and I/O-free; all S3/parsing lives in
the tool.

### 2.1 `models/model_tiers.py` — classifier core (Layer B)

New signature and return type:

```python
classify_task(
    text: str,
    *,
    input_tokens: int = 0,
    has_code: bool = False,
    message_count: int = 0,
) -> TierResult
```

`TierResult` is a small dataclass:

```python
@dataclass
class TierResult:
    tier: str            # "reasoning" | "execution" | "polish" | "unknown"
    confidence: float    # 0.0–1.0
    signals: dict        # per-tier scores + which signals fired (transparency)
```

Weighted signal tables, structural-signal rules, and the scoring/tie-break logic are
module-level constants + pure functions here. No I/O.

**Backward compatibility:** `classify_task` previously returned a bare string. Both
call sites are updated to consume `TierResult`. The legacy implementation is kept as
`_classify_task_legacy` (bare-string return) solely for A/B comparison during tuning;
removed once tuning settles.

### 2.2 `tools/invocation_logs.py` — Layer A + wiring

- Read nested `input.inputTokenCount` / `input.outputTokenCount`.
- Region-aware prefix in `_list_log_objects` (fall back to existing patterns).
- Capped fetch of `inputBodyS3Path`: fetch the offloaded body for at most
  `BODY_FETCH_CAP` sampled entries; extract messages/text; derive `has_code` and
  `message_count`.
- Pass structural signals into `classify_task`; store `tier` + `confidence` on the
  entry.
- Mismatch dimension skips `unknown` / low-confidence entries.

### 2.3 `models/schemas.py`

`InvocationLogEntry` gains `classification_confidence: float = 0.0`.
(`classified_tier` already exists.)

**Invariant:** the classifier is pure; structural signals are optional inputs.
`recommend_model` calls it text-only (structural kwargs default → contribute nothing),
so its behavior stays text-driven but with the improved weighting.

---

## 3. Scoring Model

Each tier accumulates a weighted score from text + structural signals. The winner and
its margin over the runner-up determine tier and confidence.

### 3.1 Text signals (weighted)

The three signal lists are retained but weighted; strong phrases outscore weak single
words. Signals stay within their own tier's tally, so tier totals are compared, not
raw counts.

Starting weights (tuned on real logs — see §4):

| Tier | Strong (2.0) | Weak (1.0) |
|---|---|---|
| reasoning | "architect", "design the system", "debug subtle", "trade-off" | "analyze", "plan", "evaluate", "compare approaches", "reason about" |
| execution | "implement", "refactor", "migrate" | "write code", "generate", "convert", "process", "build", "create" |
| polish | "fix typo", "proofread" | "format", "summarize", "clean up", "rename", "translate", "reword", "lint" |

### 3.2 Structural signals (additive; only when supplied)

| Signal | Effect |
|---|---|
| `input_tokens > 50_000` | +2.0 reasoning, −3.0 polish |
| `input_tokens > 20_000` | +1.0 reasoning, +1.0 execution |
| `has_code` | +1.5 execution, −1.0 polish |
| `message_count > 10` | +1.0 reasoning |

Absent structural kwargs contribute nothing (the `recommend_model` path).

### 3.3 Winner & confidence

Structural signals can drive a tier's raw score negative (e.g. polish −3.0 on a huge
prompt). Before computing the winner, each tier's score is floored at 0
(`max(0, score)`), so negatives act only to *suppress* a tier, never to distort the
`total` or the margin ratio.

- `tier = argmax(floored_scores)`; `total = sum of floored tier scores`.
- `confidence = winner_score / total` (winner's share of evidence), gated by:
  - **`winner_score < MIN_SCORE`** (default 1.0) → `tier = "unknown"`, confidence 0.
    (Fixes silent-default: no evidence → say so.)
  - **`(winner − runner_up) / total < MARGIN`** (default 0.15) → return the tier but
    flag low confidence.
- `signals` returns per-tier scores + fired signals for transparency.

### 3.4 Consumer behavior on confidence

- **Mismatch dimension:** skip entries where `tier == "unknown"` or
  `confidence < CONF_THRESHOLD` (default 0.5). Excluded from `classified_count`
  entirely, so low-confidence guesses never inflate mismatch rate or savings.
- **`recommend_model`:** surfaces confidence in output but still recommends.

All defaults (weights, 50K/20K cutoffs, MIN_SCORE, MARGIN, CONF_THRESHOLD) are
starting values, tuned empirically per §4.

---

## 4. Validation & Tuning

Chosen strategy: **validate/tune against real logs, then freeze a fixture for
regression.**

### 4.1 Comparison script — `scripts/compare_classifier.py` (new)

- Reads real entries from the configured log bucket (`<BEDROCK_LOG_BUCKET>`, never
  hard-coded — resolved from `BEDROCK_LOG_BUCKET` env / SSM, or a `--bucket` arg) via
  the fixed parser (with capped body fetches). Takes an explicit date/prefix arg (real
  data is April 2026, outside a default 7-day window).
- Runs both `_classify_task_legacy` and the new `classify_task` over the same entries.
- Outputs: tier distribution old vs. new, count of flipped classifications, new
  `unknown`/low-confidence rate, and a sample of flipped cases with their text +
  structural signals for eyeball review.
- This is the tuning loop: run → inspect flips → adjust weights → re-run.

### 4.2 Frozen regression fixture

From the same run, capture ~20–30 anonymized real entries (scrub `identity.arn` /
account IDs via `normalize_principal_arn`) into `tests/fixtures/`. Provides a
repeatable pass/fail guard with real data and no live S3 in the suite.

### 4.3 Data flow (per sampled log object)

```
list objects (region-aware prefix) → sample → parse record
  → read nested input.inputTokenCount
  → if inputBodyS3Path and under BODY_FETCH_CAP: GET body, extract messages/text
  → derive has_code, message_count, last user message
  → classify_task(text, input_tokens=, has_code=, message_count=) → TierResult
  → store tier + confidence on InvocationLogEntry
mismatch dimension: skip unknown/low-confidence; scale savings (unchanged)
```

---

## 5. Testing (stdlib `unittest`)

1. **Classifier unit tests** — one per failure mode: zero-match text → `unknown`;
   weighted tie resolves correctly; 90K-token input overrides polish keywords;
   structural-absent path still works text-only.
2. **Confidence gating** — low-confidence entry excluded from mismatch
   `classified_count`.
3. **Parser fidelity** — a captured real-shape fixture (nested token counts +
   `inputBodyS3Path`) parses correctly.
4. **Frozen-fixture regression** — the ~20–30 real entries classify to a committed
   baseline; drift fails.
5. **Legacy comparison** — new classifier differs from legacy on ≥ N fixture cases
   (proves change), and `recommend_model`'s behavior is preserved for clear-cut cases.

---

## 6. Error Handling

- Body-fetch failure → fall back to inline/system-prompt text; never crash the audit.
- `BODY_FETCH_CAP` bounds S3 cost/latency.
- Unknown region segment → fall back to existing prefix patterns.
- `total == 0` (no signals at all) → `unknown`, confidence 0.

---

## 7. Out of Scope

- Any LLM-based classification (belongs to the Bifrost gateway design).
- Changing the tier definitions themselves (reasoning/execution/polish stay).
- Fetching offloaded bodies for *every* entry (capped only).
- Fixing the audit's default date window (the comparison script takes an explicit
  date arg; a general fix is separate).

## 8. Resolved Decisions

- **`BODY_FETCH_CAP` starting value: 300** (matches the default `sample_size`, so a
  default audit can recover bodies for the whole sample; tunable if S3 cost warrants).
- **`_classify_task_legacy` is deleted after tuning** — it exists only for the A/B
  comparison during the tuning pass, then removed along with the comparison script's
  dependency on it.
- **The log bucket name is never hard-coded** in code, tests, or committed docs —
  always resolved from `BEDROCK_LOG_BUCKET` env / SSM `/token-cop/bedrock-log-bucket`
  or an explicit `--bucket` argument. Fixtures scrub it.
