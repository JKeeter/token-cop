# LLM-Free Task Classifier Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the deterministic `classify_task` into a weighted-scoring classifier with a confidence score and an explicit `unknown` tier, and fix the Bedrock invocation-log parser so it actually feeds the classifier real token counts and message text.

**Architecture:** Layer B (classifier core in `models/model_tiers.py`) stays pure and I/O-free — text keywords are weighted, structural signals (input size, code presence, message count) are optional keyword args, and a confidence/`unknown` gate replaces the silent `execution` default. Layer A (parser fidelity in `tools/invocation_logs.py`) reads nested token counts, lists region-partitioned log paths, and fetches offloaded prompt bodies (capped) so the classifier receives real inputs. Validation is a comparison script run against real logs plus a frozen anonymized fixture as a regression guard.

**Tech Stack:** Python 3.13, stdlib `unittest` (NOT pytest), `boto3` for S3, existing `dataclasses` schemas. Run tests with `python -m unittest`.

## Global Constraints

- **No LLM in the classification path.** Deterministic heuristics only.
- **Log bucket name is never hard-coded** in code, tests, or committed docs — always resolved from `BEDROCK_LOG_BUCKET` env / SSM `/token-cop/bedrock-log-bucket`, or an explicit `--bucket` arg. Fixtures scrub bucket names, account IDs, and principal ARNs (use `models.normalization.normalize_principal_arn`).
- **`BODY_FETCH_CAP = 300`** (matches default `sample_size`).
- **Test runner is stdlib `unittest`** — every run command is `python -m unittest ...`, never `pytest`.
- **Tools return JSON strings** (Strands convention) — do not change tool return contracts.
- **`_classify_task_legacy` is temporary** — kept only for the tuning comparison, deleted in the final task.
- **Confidence defaults:** `MIN_SCORE = 1.0`, `MARGIN = 0.15`, `CONF_THRESHOLD = 0.5`. These are starting values; the tuning task may adjust the module constants (not the logic).

---

## File Structure

- `models/model_tiers.py` — **modify.** Add `TierResult` dataclass, weighted signal tables, structural-signal rules, scoring/confidence logic. `classify_task` gets new signature + return type. Keep `_classify_task_legacy` (bare-string) until Task 8.
- `tools/model_router.py` — **modify.** `recommend_model` consumes `TierResult` (text-only call), surfaces confidence.
- `tools/invocation_logs.py` — **modify.** Nested token counts; region-aware `_list_log_objects`; capped body fetch; structural signals into `classify_task`; store confidence; mismatch dimension skips `unknown`/low-confidence.
- `models/schemas.py` — **modify.** `InvocationLogEntry` gains `classification_confidence: float = 0.0`.
- `scripts/compare_classifier.py` — **create.** Old-vs-new comparison over real logs; fixture capture helper.
- `tests/test_model_tiers.py` — **create.** Classifier unit tests.
- `tests/test_invocation_logs.py` — **modify.** Parser fidelity + confidence-gating tests (extends existing file).
- `tests/fixtures/invocation_log_sample.json` — **create (in Task 7).** Frozen anonymized real entries.
- `tests/fixtures/invocation_body_sample.json` — **create (in Task 4).** Offloaded-body shape fixture.

---

### Task 1: `TierResult` dataclass + weighted text scoring

**Files:**
- Modify: `models/model_tiers.py`
- Test: `tests/test_model_tiers.py` (create)

**Interfaces:**
- Produces: `TierResult` dataclass with fields `tier: str`, `confidence: float`, `signals: dict`. `classify_task(text: str, *, input_tokens: int = 0, has_code: bool = False, message_count: int = 0) -> TierResult`. Constants `MIN_SCORE`, `MARGIN` at module level. `_classify_task_legacy(description: str) -> str` (the current logic, renamed & preserved).

- [ ] **Step 1: Preserve the current classifier as legacy**

In `models/model_tiers.py`, rename the existing `classify_task` function body to `_classify_task_legacy` (keep it returning a bare string exactly as today). Leave `get_model_tier`, `get_cost_comparison`, `TIERS` untouched.

- [ ] **Step 2: Write the failing test for weighted text scoring**

Create `tests/test_model_tiers.py`:

```python
import unittest

from models.model_tiers import TierResult, classify_task


class WeightedTextScoringTests(unittest.TestCase):
    def test_strong_reasoning_phrase_beats_weak_polish_words(self):
        # "architect" is a strong (2.0) reasoning signal; a lone weak polish
        # word must not outweigh it.
        result = classify_task("architect the system and format the output")
        self.assertIsInstance(result, TierResult)
        self.assertEqual(result.tier, "reasoning")
        self.assertGreater(result.confidence, 0.0)

    def test_clear_polish_task(self):
        result = classify_task("fix typo and proofread this paragraph")
        self.assertEqual(result.tier, "polish")

    def test_clear_execution_task(self):
        result = classify_task("implement and refactor the parser module")
        self.assertEqual(result.tier, "execution")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m unittest tests.test_model_tiers -v`
Expected: FAIL — `ImportError: cannot import name 'TierResult'`.

- [ ] **Step 4: Implement `TierResult` + weighted text scoring**

Add near the top of `models/model_tiers.py` (after imports):

```python
from dataclasses import dataclass, field


@dataclass
class TierResult:
    """Classification result: tier, confidence 0-1, and signal breakdown."""
    tier: str
    confidence: float
    signals: dict = field(default_factory=dict)


MIN_SCORE = 1.0   # below this total for the winner -> "unknown"
MARGIN = 0.15     # winner must lead runner-up by this share of total

# Weighted text signals: (phrase, weight). Strong phrases = 2.0, weak = 1.0.
_WEIGHTED_SIGNALS = {
    "reasoning": [
        ("architect", 2.0), ("design the system", 2.0), ("debug subtle", 2.0),
        ("trade-off", 2.0), ("analyze", 1.0), ("plan", 1.0), ("evaluate", 1.0),
        ("compare approaches", 1.0), ("reason about", 1.0),
    ],
    "execution": [
        ("implement", 2.0), ("refactor", 2.0), ("migrate", 2.0),
        ("write code", 1.0), ("generate", 1.0), ("convert", 1.0),
        ("process", 1.0), ("build", 1.0), ("create", 1.0),
    ],
    "polish": [
        ("fix typo", 2.0), ("proofread", 2.0), ("format", 1.0),
        ("summarize", 1.0), ("clean up", 1.0), ("rename", 1.0),
        ("translate", 1.0), ("reword", 1.0), ("lint", 1.0),
    ],
}


def _text_scores(text: str) -> dict:
    """Sum weighted signal matches per tier from the text."""
    low = text.lower()
    scores = {"reasoning": 0.0, "execution": 0.0, "polish": 0.0}
    for tier, signals in _WEIGHTED_SIGNALS.items():
        for phrase, weight in signals:
            if phrase in low:
                scores[tier] += weight
    return scores


def classify_task(
    text: str,
    *,
    input_tokens: int = 0,
    has_code: bool = False,
    message_count: int = 0,
) -> TierResult:
    """Classify a task into a model tier with a confidence score.

    Structural signals (input_tokens, has_code, message_count) are optional;
    when omitted the result is text-driven. Returns tier "unknown" when there
    is insufficient signal rather than silently defaulting.
    """
    scores = _text_scores(text)
    return _finalize(scores)


def _finalize(scores: dict) -> TierResult:
    """Pick the winner, apply MIN_SCORE / MARGIN gates, compute confidence."""
    floored = {t: max(0.0, s) for t, s in scores.items()}
    total = sum(floored.values())
    if total <= 0:
        return TierResult("unknown", 0.0, floored)

    ranked = sorted(floored.items(), key=lambda kv: kv[1], reverse=True)
    winner, winner_score = ranked[0]
    runner_score = ranked[1][1] if len(ranked) > 1 else 0.0

    if winner_score < MIN_SCORE:
        return TierResult("unknown", 0.0, floored)

    confidence = winner_score / total
    # Low margin still returns the tier, but confidence reflects the closeness.
    if (winner_score - runner_score) / total < MARGIN:
        confidence = min(confidence, 0.49)  # flag as low-confidence
    return TierResult(winner, round(confidence, 3), floored)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest tests.test_model_tiers -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add models/model_tiers.py tests/test_model_tiers.py
git commit -m "feat(classifier): weighted text scoring + TierResult with confidence"
```

---

### Task 2: `unknown` tier + confidence gating (the four failure modes)

**Files:**
- Modify: `models/model_tiers.py` (extends Task 1; no new functions)
- Test: `tests/test_model_tiers.py`

**Interfaces:**
- Consumes: `classify_task`, `TierResult`, `MIN_SCORE`, `MARGIN` from Task 1.
- Produces: no new symbols — locks the `unknown` and low-confidence behavior.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_model_tiers.py`:

```python
class ConfidenceAndUnknownTests(unittest.TestCase):
    def test_no_signal_returns_unknown(self):
        # "ok do it" / "yes continue" -> no keywords -> unknown, not execution.
        result = classify_task("yes, continue")
        self.assertEqual(result.tier, "unknown")
        self.assertEqual(result.confidence, 0.0)

    def test_low_margin_flags_low_confidence(self):
        # One weak reasoning word vs one weak polish word -> near tie.
        result = classify_task("analyze and summarize")
        self.assertLess(result.confidence, 0.5)
        self.assertIn(result.tier, ("reasoning", "polish"))

    def test_signals_dict_exposes_per_tier_scores(self):
        result = classify_task("implement the feature")
        self.assertIn("execution", result.signals)
        self.assertGreater(result.signals["execution"], 0.0)
```

- [ ] **Step 2: Run tests to verify they fail or pass**

Run: `python -m unittest tests.test_model_tiers.ConfidenceAndUnknownTests -v`
Expected: PASS already if Task 1's `_finalize` is correct. If `test_no_signal_returns_unknown` FAILS, the `total <= 0` guard is missing — fix in `_finalize`.

- [ ] **Step 3: Confirm no code change needed (or fix `_finalize`)**

The Task 1 `_finalize` already implements this. If any test failed, the only allowed change is correcting the `total <= 0` or `MARGIN` branch in `_finalize`. Do NOT add execution fallback.

- [ ] **Step 4: Run the whole classifier suite**

Run: `python -m unittest tests.test_model_tiers -v`
Expected: PASS (6 tests total).

- [ ] **Step 5: Commit**

```bash
git add models/model_tiers.py tests/test_model_tiers.py
git commit -m "test(classifier): lock unknown-tier and low-confidence behavior"
```

---

### Task 3: Structural signals in the classifier

**Files:**
- Modify: `models/model_tiers.py`
- Test: `tests/test_model_tiers.py`

**Interfaces:**
- Consumes: `classify_task`, `_text_scores`, `_finalize` from Task 1.
- Produces: `_structural_scores(input_tokens, has_code, message_count) -> dict` merged into `classify_task`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_model_tiers.py`:

```python
class StructuralSignalTests(unittest.TestCase):
    def test_huge_input_overrides_polish_keywords(self):
        # "summarize" is polish, but a 90K-token prompt is not polish work.
        result = classify_task("summarize", input_tokens=90_000)
        self.assertNotEqual(result.tier, "polish")

    def test_code_presence_favors_execution(self):
        result = classify_task("take a look", has_code=True, input_tokens=25_000)
        self.assertEqual(result.tier, "execution")

    def test_structural_absent_is_text_only(self):
        # No structural kwargs -> identical to a pure-text call.
        a = classify_task("implement the parser")
        b = classify_task("implement the parser", input_tokens=0,
                           has_code=False, message_count=0)
        self.assertEqual(a.tier, b.tier)
        self.assertEqual(a.confidence, b.confidence)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_model_tiers.StructuralSignalTests -v`
Expected: FAIL — `test_huge_input_overrides_polish_keywords` returns "polish".

- [ ] **Step 3: Implement structural scoring**

Add to `models/model_tiers.py`:

```python
def _structural_scores(input_tokens: int, has_code: bool, message_count: int) -> dict:
    """Additive structural nudges. All zero when signals are absent."""
    s = {"reasoning": 0.0, "execution": 0.0, "polish": 0.0}
    if input_tokens > 50_000:
        s["reasoning"] += 2.0
        s["polish"] -= 3.0
    elif input_tokens > 20_000:
        s["reasoning"] += 1.0
        s["execution"] += 1.0
    if has_code:
        s["execution"] += 1.5
        s["polish"] -= 1.0
    if message_count > 10:
        s["reasoning"] += 1.0
    return s
```

Then update `classify_task` to merge them:

```python
def classify_task(text, *, input_tokens=0, has_code=False, message_count=0):
    scores = _text_scores(text)
    structural = _structural_scores(input_tokens, has_code, message_count)
    for tier in scores:
        scores[tier] += structural[tier]
    return _finalize(scores)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_model_tiers -v`
Expected: PASS (9 tests total).

- [ ] **Step 5: Commit**

```bash
git add models/model_tiers.py tests/test_model_tiers.py
git commit -m "feat(classifier): structural signals (input size, code, turns)"
```

---

### Task 4: Update `recommend_model` to consume `TierResult`

**Files:**
- Modify: `tools/model_router.py:31` (`_recommend_model_impl`)
- Test: `tests/test_model_tiers.py` (add an integration check) — or extend as noted

**Interfaces:**
- Consumes: `classify_task` returning `TierResult` (Tasks 1-3).
- Produces: `recommend_model` output dict gains a `"confidence"` key; `recommended_tier` derives from `TierResult.tier`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_model_tiers.py`:

```python
import json
from tools.model_router import _recommend_model_impl


class RecommendModelIntegrationTests(unittest.TestCase):
    def test_recommend_surfaces_confidence(self):
        out = json.loads(_recommend_model_impl("implement the parser", ""))
        self.assertEqual(out["recommended_tier"], "execution")
        self.assertIn("confidence", out)

    def test_recommend_unknown_defaults_gracefully(self):
        # No-signal text -> unknown -> recommend_model must still return a tier
        # (fall back to execution for the advisory, but report low confidence).
        out = json.loads(_recommend_model_impl("yes continue", ""))
        self.assertIn(out["recommended_tier"], ("execution", "unknown"))
        self.assertEqual(out["confidence"], 0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_model_tiers.RecommendModelIntegrationTests -v`
Expected: FAIL — current code does `recommended_tier = classify_task(...)` expecting a string; now it's a `TierResult`, so `TIERS[recommended_tier]` raises `TypeError`/`KeyError`.

- [ ] **Step 3: Update `_recommend_model_impl`**

In `tools/model_router.py`, change the top of `_recommend_model_impl` (currently line 31):

```python
def _recommend_model_impl(task_description: str, current_model: str) -> str:
    result = classify_task(task_description)
    # "unknown" has no tier definition; fall back to execution for the
    # advisory recommendation, but preserve the (zero) confidence signal.
    recommended_tier = result.tier if result.tier in TIERS else "execution"
    tier_info = TIERS[recommended_tier]
```

Then add `"confidence": result.confidence,` to the `result` dict that the function builds (the dict currently starting with `"recommended_tier"`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_model_tiers -v`
Expected: PASS (11 tests total).

- [ ] **Step 5: Commit**

```bash
git add tools/model_router.py tests/test_model_tiers.py
git commit -m "feat(recommend_model): consume TierResult, surface confidence"
```

---

### Task 5: Parser fidelity — nested token counts + region-aware listing

**Files:**
- Modify: `tools/invocation_logs.py` (`_parse_record` ~line 258; `_list_log_objects` ~line 137)
- Modify: `models/schemas.py` (`InvocationLogEntry`)
- Test: `tests/test_invocation_logs.py` (extend existing file)

**Interfaces:**
- Consumes: `InvocationLogEntry` (schema), `_parse_record`, `_list_log_objects`.
- Produces: `_parse_record` reads `record["input"]["inputTokenCount"]`; `InvocationLogEntry` gains `classification_confidence: float = 0.0`.

- [ ] **Step 1: Add the schema field**

In `models/schemas.py`, add to `InvocationLogEntry` (after `model_tier`):

```python
    classification_confidence: float = 0.0
```

- [ ] **Step 2: Write the failing test for nested token counts**

Add to `tests/test_invocation_logs.py`:

```python
from tools.invocation_logs import _parse_record


class ParserFidelityTests(unittest.TestCase):
    def test_reads_nested_input_token_count(self):
        record = {
            "modelId": "us.anthropic.claude-opus-4-6-v1:0",
            "timestamp": "2026-04-03T20:03:49Z",
            "input": {
                "inputTokenCount": 12345,
                "inputBodyS3Path": "s3://x/y_input.json.gz",
            },
            "output": {"outputTokenCount": 678},
        }
        entry = _parse_record(record)
        self.assertEqual(entry.input_token_count, 12345)
        self.assertEqual(entry.output_token_count, 678)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m unittest tests.test_invocation_logs.ParserFidelityTests -v`
Expected: FAIL — `input_token_count` is 0 (reads top-level, which is absent).

- [ ] **Step 4: Fix token-count reads in `_parse_record`**

In `tools/invocation_logs.py`, replace lines 258-259:

```python
    input_body = record.get("input", {})
    if isinstance(input_body, str):
        try:
            input_body = json.loads(input_body)
        except (json.JSONDecodeError, TypeError):
            input_body = {}

    output_body = record.get("output", {})
    if isinstance(output_body, str):
        try:
            output_body = json.loads(output_body)
        except (json.JSONDecodeError, TypeError):
            output_body = {}

    # Token counts: prefer nested input/output bodies (current Bedrock schema),
    # fall back to legacy top-level keys.
    input_tokens = (
        (input_body.get("inputTokenCount") if isinstance(input_body, dict) else None)
        or record.get("inputTokenCount", 0)
        or 0
    )
    output_tokens = (
        (output_body.get("outputTokenCount") if isinstance(output_body, dict) else None)
        or record.get("outputTokenCount", 0)
        or 0
    )
```

Then DELETE the now-duplicated `input_body = record.get("input", {})` block that followed (original lines 266-271), since it's moved above.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest tests.test_invocation_logs.ParserFidelityTests -v`
Expected: PASS.

- [ ] **Step 6: Write the failing test for region-aware listing**

Add to `tests/test_invocation_logs.py`:

```python
from unittest.mock import MagicMock
from tools.invocation_logs import _list_log_objects


class RegionAwareListingTests(unittest.TestCase):
    def test_region_partitioned_prefix_is_tried(self):
        s3 = MagicMock()
        paginator = MagicMock()
        s3.get_paginator.return_value = paginator
        # Return one object only for the region-partitioned prefix.
        def paginate(Bucket, Prefix, MaxKeys):
            if "us-east-1" in Prefix:
                return [{"Contents": [{"Key": Prefix + "/f.json.gz", "Size": 10}]}]
            return [{}]
        paginator.paginate.side_effect = paginate
        s3.list_objects_v2.return_value = {"CommonPrefixes": []}
        objs = _list_log_objects(s3, "bucket", "AWSLogs", 1)
        self.assertTrue(any("us-east-1" in o["Key"] for o in objs))
```

- [ ] **Step 7: Run test to verify it fails**

Run: `python -m unittest tests.test_invocation_logs.RegionAwareListingTests -v`
Expected: FAIL — current prefixes don't include a region segment.

- [ ] **Step 8: Add region-aware prefixes in `_list_log_objects`**

In `tools/invocation_logs.py`, in the `for pfx in [...]` list (around line 150), add region-partitioned patterns. Replace the list with:

```python
        # Common Bedrock CWL/S3 export prefix patterns. Region-partitioned
        # paths (…/BedrockModelInvocationLogs/{region}/YYYY/MM/DD/) are the
        # current default; keep the older region-less patterns as fallback.
        candidate_prefixes = []
        for region in ("us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"):
            candidate_prefixes.append(
                f"{prefix}/BedrockModelInvocationLogs/{region}/{date_suffix}"
            )
        candidate_prefixes.append(f"{prefix}/BedrockModelInvocationLogs/{date_suffix}")
        candidate_prefixes.append(f"{prefix}/{date_suffix}")
        for pfx in candidate_prefixes:
```

Leave the account-ID discovery block below it intact, but add region handling there too by changing its inner prefix to iterate regions:

```python
        if not objects and day_offset == 0:
            try:
                resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter="/", MaxKeys=10)
                for cp in resp.get("CommonPrefixes", []):
                    acct_prefix = cp["Prefix"]
                    for region in ("us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"):
                        pfx = f"{acct_prefix}BedrockModelInvocationLogs/{region}/{date_suffix}"
                        for page in paginator.paginate(Bucket=bucket, Prefix=pfx, MaxKeys=1000):
                            for obj in page.get("Contents", []):
                                objects.append({
                                    "Key": obj["Key"],
                                    "Size": obj.get("Size", 0),
                                    "date": day.strftime("%Y-%m-%d"),
                                })
            except Exception as exc:
                logger.debug("S3 account prefix discovery failed: %s", exc)
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `python -m unittest tests.test_invocation_logs -v`
Expected: PASS (all prior + 2 new).

- [ ] **Step 10: Commit**

```bash
git add tools/invocation_logs.py models/schemas.py tests/test_invocation_logs.py
git commit -m "fix(invocation-logs): read nested token counts, region-aware listing"
```

---

### Task 6: Capped body fetch + structural-signal derivation

**Files:**
- Modify: `tools/invocation_logs.py` (`_parse_log_entries` ~line 209; new helpers)
- Test: `tests/test_invocation_logs.py`
- Create: `tests/fixtures/invocation_body_sample.json`

**Interfaces:**
- Consumes: `_parse_record`, S3 client from `_parse_log_entries`.
- Produces: `BODY_FETCH_CAP = 300` constant; `_extract_body_signals(body: dict) -> dict` returning `{"user_message_text", "message_count", "has_code"}`; `_fetch_body(s3, bucket, s3_path) -> dict | None`.

- [ ] **Step 1: Create the body fixture**

Create `tests/fixtures/invocation_body_sample.json` (the offloaded body shape — Converse format):

```json
{
  "messages": [
    {"role": "user", "content": [{"text": "Please refactor this:\n```python\ndef f(): pass\n```"}]},
    {"role": "assistant", "content": [{"text": "Done."}]},
    {"role": "user", "content": [{"text": "now add a test"}]}
  ],
  "system": [{"text": "You are a coding assistant."}]
}
```

- [ ] **Step 2: Write the failing test for body-signal extraction**

Add to `tests/test_invocation_logs.py`:

```python
import os
from tools.invocation_logs import _extract_body_signals

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


class BodySignalTests(unittest.TestCase):
    def test_extracts_code_and_message_count(self):
        with open(os.path.join(FIXTURE_DIR, "invocation_body_sample.json")) as f:
            body = json.load(f)
        sig = _extract_body_signals(body)
        self.assertTrue(sig["has_code"])
        self.assertEqual(sig["message_count"], 3)
        self.assertEqual(sig["user_message_text"], "now add a test")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m unittest tests.test_invocation_logs.BodySignalTests -v`
Expected: FAIL — `_extract_body_signals` not defined.

- [ ] **Step 4: Implement body-signal extraction**

Add to `tools/invocation_logs.py` (near the top, add `BODY_FETCH_CAP = 300` beside the other limits at line 26-28):

```python
BODY_FETCH_CAP = 300  # max offloaded bodies to fetch per analysis run


def _extract_body_signals(body: dict) -> dict:
    """Derive classification signals from an offloaded request body."""
    text = ""
    message_count = 0
    has_code = False
    messages = body.get("messages", []) if isinstance(body, dict) else []
    if isinstance(messages, list):
        message_count = len(messages)
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and "text" in block:
                            text = block["text"][:_USER_MESSAGE_MAX]
                            break
                elif isinstance(content, str):
                    text = content[:_USER_MESSAGE_MAX]
                break
    # Code detection across all message text (fenced blocks or common tokens).
    blob = json.dumps(body)[:20000]
    if "```" in blob or "def " in blob or "function " in blob or "import " in blob:
        has_code = True
    return {"user_message_text": text, "message_count": message_count, "has_code": has_code}


def _fetch_body(s3, bucket: str, s3_path: str) -> dict | None:
    """Fetch and parse an offloaded inputBodyS3Path object. None on failure."""
    if not s3_path:
        return None
    key = s3_path
    if s3_path.startswith("s3://"):
        # s3://bucket/key -> key
        parts = s3_path[5:].split("/", 1)
        key = parts[1] if len(parts) == 2 else ""
    if not key:
        return None
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        raw = resp["Body"].read()
        if key.endswith(".gz") or key.endswith(".gzip"):
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.debug("Body fetch failed for %s: %s", key, exc)
        return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest tests.test_invocation_logs.BodySignalTests -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tools/invocation_logs.py tests/test_invocation_logs.py tests/fixtures/invocation_body_sample.json
git commit -m "feat(invocation-logs): capped offloaded-body fetch + signal extraction"
```

---

### Task 7: Wire structural signals + confidence into parsing and the mismatch dimension

**Files:**
- Modify: `tools/invocation_logs.py` (`_parse_record` classify call ~line 316; `_parse_log_entries` ~line 209; `_analyze_model_task_mismatch` ~line 407)
- Test: `tests/test_invocation_logs.py`

**Interfaces:**
- Consumes: `classify_task -> TierResult`, `_extract_body_signals`, `_fetch_body`, `BODY_FETCH_CAP`, `InvocationLogEntry.classification_confidence`.
- Produces: mismatch dimension excludes `unknown`/low-confidence entries from `classified_count`.

- [ ] **Step 1: Update `_parse_record` to store tier + confidence**

In `tools/invocation_logs.py`, replace the classify block (currently lines 313-316):

```python
    model_tier = get_model_tier(normalized) or ""
    classified_tier = ""
    classification_confidence = 0.0
    if user_message_text:
        result = classify_task(
            user_message_text,
            input_tokens=input_tokens,
            has_code=_looks_like_code(user_message_text),
            message_count=message_count,
        )
        classified_tier = result.tier
        classification_confidence = result.confidence
```

Add a tiny helper near `_extract_body_signals`:

```python
def _looks_like_code(text: str) -> bool:
    return "```" in text or "def " in text or "import " in text or "function " in text
```

Then add `classification_confidence=classification_confidence,` to the `InvocationLogEntry(...)` constructor call at the end of `_parse_record`.

- [ ] **Step 2: Fetch bodies during parsing (capped) and re-derive signals**

In `_parse_log_entries` (line 209), after an entry is parsed, when the record had an offloaded body and we're under the cap, fetch it and enrich. Modify the loop:

```python
def _parse_log_entries(s3, bucket: str, objects: list[dict]) -> list[InvocationLogEntry]:
    entries = []
    bodies_fetched = 0
    for obj in objects:
        try:
            resp = s3.get_object(Bucket=bucket, Key=obj["Key"])
            body = resp["Body"].read()
            if obj["Key"].endswith(".gz") or obj["Key"].endswith(".gzip"):
                body = gzip.decompress(body)
            text = body.decode("utf-8", errors="replace")
            for line in text.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    entry = _parse_record(record)
                    if not entry:
                        continue
                    # If the prompt was offloaded and we have budget, fetch it
                    # to recover real message text + structural signals.
                    body_path = _offloaded_body_path(record)
                    if body_path and not entry.user_message_text and bodies_fetched < BODY_FETCH_CAP:
                        fetched = _fetch_body(s3, bucket, body_path)
                        bodies_fetched += 1
                        if fetched:
                            _enrich_entry(entry, fetched)
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
        except Exception as exc:
            logger.warning("Failed to parse S3 object %s: %s", obj["Key"], exc)
            continue
    return entries
```

Add the two helpers:

```python
def _offloaded_body_path(record: dict) -> str:
    inp = record.get("input", {})
    if isinstance(inp, dict):
        return inp.get("inputBodyS3Path", "") or ""
    return ""


def _enrich_entry(entry: InvocationLogEntry, body: dict) -> None:
    """Re-classify an entry using signals recovered from the offloaded body."""
    sig = _extract_body_signals(body)
    if not sig["user_message_text"]:
        return
    entry.user_message_text = sig["user_message_text"]
    entry.message_count = sig["message_count"] or entry.message_count
    result = classify_task(
        sig["user_message_text"],
        input_tokens=entry.input_token_count,
        has_code=sig["has_code"],
        message_count=sig["message_count"],
    )
    entry.classified_tier = result.tier
    entry.classification_confidence = result.confidence
```

- [ ] **Step 3: Write the failing test for confidence gating in mismatch**

Add to `tests/test_invocation_logs.py`:

```python
class MismatchConfidenceGatingTests(unittest.TestCase):
    def _entry(self, **kw):
        base = {"model_id": "m", "normalized_model": "claude-opus-4.6",
                "timestamp": "2026-04-03T00:00:00Z"}
        base.update(kw)
        return InvocationLogEntry(**base)

    def test_low_confidence_entries_excluded(self):
        from tools.invocation_logs import _analyze_model_task_mismatch
        entries = [
            # high-confidence mismatch: counts
            self._entry(model_tier="reasoning", classified_tier="polish",
                        classification_confidence=0.9, input_token_count=1000),
            # low-confidence: must be skipped, not counted as classifiable
            self._entry(model_tier="reasoning", classified_tier="polish",
                        classification_confidence=0.2, input_token_count=1000),
            # unknown: skipped
            self._entry(model_tier="reasoning", classified_tier="unknown",
                        classification_confidence=0.0, input_token_count=1000),
        ]
        result = _analyze_model_task_mismatch(entries, days=7)
        self.assertEqual(result["classified_entries"], 1)
        self.assertEqual(result["mismatched_entries"], 1)
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m unittest tests.test_invocation_logs.MismatchConfidenceGatingTests -v`
Expected: FAIL — all 3 entries counted (`classified_entries == 3`).

- [ ] **Step 5: Add the confidence gate in `_analyze_model_task_mismatch`**

In `tools/invocation_logs.py`, add a module constant near the others:

```python
CONF_THRESHOLD = 0.5  # mismatch dimension ignores classifications below this
```

Then in `_analyze_model_task_mismatch`, change the loop guard (currently `if not e.model_tier or not e.classified_tier: continue`):

```python
    for e in entries:
        if not e.model_tier or not e.classified_tier:
            continue
        if e.classified_tier == "unknown" or e.classification_confidence < CONF_THRESHOLD:
            continue  # insufficient classifier confidence — don't guess
        classified_count += 1
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m unittest tests.test_invocation_logs -v`
Expected: PASS (all prior + new gating test).

- [ ] **Step 7: Commit**

```bash
git add tools/invocation_logs.py tests/test_invocation_logs.py
git commit -m "feat(invocation-logs): wire structural signals + confidence gating"
```

---

### Task 8: Comparison script, real-log tuning, frozen fixture, delete legacy

**Files:**
- Create: `scripts/compare_classifier.py`
- Create: `tests/fixtures/invocation_log_sample.json` (from a real run, anonymized)
- Modify: `tests/test_invocation_logs.py` (frozen-fixture regression)
- Modify: `models/model_tiers.py` (delete `_classify_task_legacy` at the end)
- Modify: `CLAUDE.md` (docs)

**Interfaces:**
- Consumes: everything above; `_classify_task_legacy` (deleted at the end).
- Produces: `scripts/compare_classifier.py` CLI; frozen regression test.

- [ ] **Step 1: Write the comparison script**

Create `scripts/compare_classifier.py`:

```python
"""Compare the legacy vs. reworked task classifier over real Bedrock logs.

Bucket is resolved from --bucket, else BEDROCK_LOG_BUCKET / SSM. Never
hard-coded. Prints tier-distribution deltas, flip count, unknown rate, and a
sample of flipped cases for eyeball review; can freeze an anonymized fixture.
"""
import argparse
import json
from collections import Counter

import boto3

from agent.config import AWS_REGION, get_secret
from models.model_tiers import classify_task, _classify_task_legacy
from models.normalization import normalize_principal_arn
from tools.invocation_logs import (
    _list_log_objects, _sample_objects, _parse_log_entries,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", default=get_secret("BEDROCK_LOG_BUCKET"))
    ap.add_argument("--prefix", default=get_secret("BEDROCK_LOG_PREFIX") or "AWSLogs")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--freeze-fixture", metavar="PATH",
                    help="write anonymized sampled entries to PATH")
    args = ap.parse_args()
    if not args.bucket:
        raise SystemExit("No bucket: pass --bucket or set BEDROCK_LOG_BUCKET")

    s3 = boto3.client("s3", region_name=AWS_REGION)
    objs = _list_log_objects(s3, args.bucket, args.prefix, args.days)
    sampled = _sample_objects(objs, args.sample)
    entries = _parse_log_entries(s3, args.bucket, sampled)

    legacy_dist, new_dist, flips, flipped_examples = Counter(), Counter(), 0, []
    for e in entries:
        if not e.user_message_text:
            continue
        old = _classify_task_legacy(e.user_message_text)
        new = classify_task(
            e.user_message_text,
            input_tokens=e.input_token_count,
            has_code="```" in e.user_message_text,
            message_count=e.message_count,
        )
        legacy_dist[old] += 1
        new_dist[new.tier] += 1
        if old != new.tier:
            flips += 1
            if len(flipped_examples) < 15:
                flipped_examples.append({
                    "text": e.user_message_text[:120],
                    "input_tokens": e.input_token_count,
                    "old": old, "new": new.tier, "conf": new.confidence,
                })

    print("entries:", len(entries))
    print("legacy distribution:", dict(legacy_dist))
    print("new distribution:   ", dict(new_dist))
    print("flips:", flips)
    print("unknown rate:", round(new_dist.get("unknown", 0) / max(1, sum(new_dist.values())), 3))
    print("--- flipped examples ---")
    for ex in flipped_examples:
        print(json.dumps(ex))

    if args.freeze_fixture:
        frozen = [{
            "model_id": e.model_id,
            "normalized_model": e.normalized_model,
            "input_token_count": e.input_token_count,
            "output_token_count": e.output_token_count,
            "user_message_text": e.user_message_text,
            "message_count": e.message_count,
            "model_tier": e.model_tier,
            "iam_principal": normalize_principal_arn(e.iam_principal or ""),
        } for e in entries if e.user_message_text][:30]
        with open(args.freeze_fixture, "w") as f:
            json.dump(frozen, f, indent=2)
        print(f"wrote {len(frozen)} anonymized entries to {args.freeze_fixture}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the comparison against real logs (tuning loop)**

Run (April 2026 data — use `--days` large enough or point via prefix):

```bash
source .venv/bin/activate
python -m scripts.compare_classifier --days 200 --sample 300
```

Inspect: are the flips sensible (polish→execution on code, →reasoning on huge input)? Is the unknown rate reasonable (<40%)? If weights need adjusting, edit the constants in `models/model_tiers.py` (`_WEIGHTED_SIGNALS`, `_structural_scores`, `MIN_SCORE`, `MARGIN`) and re-run. Do NOT change logic — only constants.

- [ ] **Step 3: Freeze the anonymized fixture**

```bash
python -m scripts.compare_classifier --days 200 --sample 300 --freeze-fixture tests/fixtures/invocation_log_sample.json
```

Verify the file contains NO account IDs or un-scrubbed ARNs:

```bash
grep -E '[0-9]{12}' tests/fixtures/invocation_log_sample.json && echo "LEAK — fix" || echo "clean"
```

- [ ] **Step 4: Write the frozen-fixture regression test**

Add to `tests/test_invocation_logs.py`:

```python
class FrozenFixtureRegressionTests(unittest.TestCase):
    def test_classifier_distribution_is_stable(self):
        path = os.path.join(FIXTURE_DIR, "invocation_log_sample.json")
        if not os.path.exists(path):
            self.skipTest("fixture not captured yet")
        with open(path) as f:
            frozen = json.load(f)
        tiers = Counter()
        for rec in frozen:
            r = classify_task(
                rec["user_message_text"],
                input_tokens=rec["input_token_count"],
                has_code="```" in rec["user_message_text"],
                message_count=rec["message_count"],
            )
            tiers[r.tier] += 1
        # Guard: at least one confident classification and not everything unknown.
        self.assertLess(tiers.get("unknown", 0), len(frozen),
                        "every real entry classified as unknown — classifier too strict")
        self.assertGreater(sum(v for k, v in tiers.items() if k != "unknown"), 0)
```

Add `from collections import Counter` to the test file imports.

- [ ] **Step 5: Run the full suite**

Run: `python -m unittest discover -s tests -v`
Expected: PASS (all classifier + invocation-log + pricing tests).

- [ ] **Step 6: Delete the legacy classifier**

Now that tuning is done and the comparison captured, remove `_classify_task_legacy` from `models/model_tiers.py`, and change `scripts/compare_classifier.py` to stop importing it (the script's comparison purpose is served; either delete the script or leave it importing only `classify_task` for future distribution checks — leave it, removing the `_classify_task_legacy` import and the `old`/`flips` logic).

- [ ] **Step 7: Run the suite again to confirm nothing depended on legacy**

Run: `python -m unittest discover -s tests -v`
Expected: PASS.

- [ ] **Step 8: Update CLAUDE.md**

In `CLAUDE.md`, under "Invocation Log Analysis", update the model-task-mismatch bullet to note: classifier now uses weighted scoring + structural signals (input size, code, turn count) with a confidence gate (`CONF_THRESHOLD`); `unknown`/low-confidence entries are excluded from mismatch stats; offloaded prompt bodies (`inputBodyS3Path`) are fetched up to `BODY_FETCH_CAP=300`. Under "Patterns", note `classify_task` returns a `TierResult` (tier/confidence/signals).

- [ ] **Step 9: Commit**

```bash
git add scripts/compare_classifier.py tests/fixtures/invocation_log_sample.json tests/test_invocation_logs.py models/model_tiers.py CLAUDE.md
git commit -m "feat(classifier): comparison script, frozen fixture, remove legacy"
```

---

## Self-Review

**Spec coverage:**
- §2.1 TierResult + weighted core → Tasks 1-3 ✓
- §2.2 parser fidelity (nested tokens, region, capped body fetch) → Tasks 5, 6 ✓
- §2.3 schema field → Task 5 ✓
- §3 scoring model (text weights, structural, confidence/unknown, score floor) → Tasks 1, 3 ✓
- §3.4 consumer behavior (mismatch skip, recommend_model surface) → Tasks 4, 7 ✓
- §4 comparison script + frozen fixture → Task 8 ✓
- §5 testing (all 5 categories) → distributed across tasks ✓
- §6 error handling (body fetch fallback, cap, region fallback, total==0) → Tasks 3, 5, 6 ✓
- §8 resolved decisions (cap=300, delete legacy, no hard-coded bucket) → Tasks 6, 8, global constraints ✓

**Placeholder scan:** No TBD/TODO; all code steps have real code. ✓

**Type consistency:** `TierResult(tier, confidence, signals)` used identically across Tasks 1-8; `classify_task(text, *, input_tokens, has_code, message_count) -> TierResult` consistent; `classification_confidence` field name matches between schema (Task 5) and usage (Task 7). ✓
