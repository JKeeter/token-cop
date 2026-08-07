"""Model tier definitions and task classification for cost-effective routing."""

import re
from dataclasses import dataclass, field

from models.pricing import PRICING_PER_MILLION


@dataclass
class TierResult:
    """Classification result: tier, confidence 0-1, and signal breakdown."""
    tier: str
    confidence: float
    signals: dict = field(default_factory=dict)

TIERS = {
    "reasoning": {
        "description": "Complex analysis, architecture, multi-step planning, subtle debugging",
        "models": ["claude-opus-4.6", "claude-opus-4", "o1"],
        "cost_range": "$15-75/M tokens",
        "signals": [
            "architect", "design", "analyze complex", "trade-off", "debug subtle",
            "plan", "evaluate", "compare approaches", "reason about",
        ],
    },
    "execution": {
        "description": "Code generation, data processing, standard implementation tasks",
        "models": ["claude-sonnet-4.6", "claude-sonnet-4", "gpt-4o"],
        "cost_range": "$2.50-15/M tokens",
        "signals": [
            "implement", "write code", "generate", "convert", "process",
            "build", "create", "refactor", "migrate",
        ],
    },
    "polish": {
        "description": "Formatting, summarizing, simple Q&A, proofreading",
        "models": ["claude-haiku-4.5", "gpt-4o-mini", "amazon-nova-lite"],
        "cost_range": "$0.06-4/M tokens",
        "signals": [
            "format", "summarize", "proofread", "clean up", "rename",
            "translate", "reword", "fix typo", "lint",
        ],
    },
}

# Precompile signal patterns for each tier
_TIER_PATTERNS = {
    tier: re.compile("|".join(re.escape(s) for s in info["signals"]), re.IGNORECASE)
    for tier, info in TIERS.items()
}

# Map model names to their tier
_MODEL_TO_TIER = {}
for _tier, _info in TIERS.items():
    for _model in _info["models"]:
        _MODEL_TO_TIER[_model] = _tier


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


def _structural_scores(
    input_tokens: int,
    has_code: bool,
    message_count: int,
    reasoning_corroborated: bool = False,
    execution_corroborated: bool = False,
) -> dict:
    """Additive structural nudges. All zero when signals are absent.

    Size and message-count signals only AMPLIFY an existing text signal — on
    cache-heavy traffic a large effective context is the norm, not evidence of
    reasoning work, so a size nudge must not create a verdict on its own. The
    polish suppression from a huge prompt is unconditional (a 50K-token prompt
    genuinely isn't a polish task), and has_code is a standalone signal.
    """
    s = {"reasoning": 0.0, "execution": 0.0, "polish": 0.0}
    if input_tokens > 50_000:
        if reasoning_corroborated:
            s["reasoning"] += 2.0
        s["polish"] -= 3.0
    elif input_tokens > 20_000:
        if reasoning_corroborated:
            s["reasoning"] += 1.0
        if execution_corroborated:
            s["execution"] += 1.0
    if has_code:
        s["execution"] += 1.5
        s["polish"] -= 1.0
    if message_count > 10 and reasoning_corroborated:
        s["reasoning"] += 1.0
    return s


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
    reasoning_corroborated = scores["reasoning"] > 0
    execution_corroborated = scores["execution"] > 0
    structural = _structural_scores(
        input_tokens, has_code, message_count,
        reasoning_corroborated=reasoning_corroborated,
        execution_corroborated=execution_corroborated,
    )
    for tier in scores:
        scores[tier] += structural[tier]
    return _finalize(scores)


def _classify_task_legacy(description: str) -> str:
    """Legacy classifier using regex pattern matching on signal counts.

    This is the original implementation and is preserved for backward compatibility
    until the new weighted text scoring classifier is fully integrated.

    Returns one of: "reasoning", "execution", "polish".
    Defaults to "execution" if no signals match.
    """
    description_lower = description.lower()

    # Count signal matches per tier
    scores = {}
    for tier, pattern in _TIER_PATTERNS.items():
        scores[tier] = len(pattern.findall(description_lower))

    best_tier = max(scores, key=scores.get)
    if scores[best_tier] == 0:
        return "execution"  # sensible default
    return best_tier


def get_model_tier(model_name: str) -> str | None:
    """Return the tier for a known model, or None."""
    return _MODEL_TO_TIER.get(model_name)


def get_cost_comparison(task_tier: str) -> dict:
    """Show concrete cost differences between tiers using real pricing data.

    Returns a dict with per-tier cost info, highlighting the recommended tier.
    """
    result = {}
    for tier, info in TIERS.items():
        tier_costs = []
        for model in info["models"]:
            pricing = PRICING_PER_MILLION.get(model)
            if pricing:
                tier_costs.append({
                    "model": model,
                    "input_per_million": pricing["input"],
                    "output_per_million": pricing["output"],
                })
        avg_input = sum(c["input_per_million"] for c in tier_costs) / len(tier_costs) if tier_costs else 0
        avg_output = sum(c["output_per_million"] for c in tier_costs) / len(tier_costs) if tier_costs else 0
        result[tier] = {
            "description": info["description"],
            "cost_range": info["cost_range"],
            "models": tier_costs,
            "avg_input_per_million": round(avg_input, 2),
            "avg_output_per_million": round(avg_output, 2),
            "is_recommended": tier == task_tier,
        }
    return result
