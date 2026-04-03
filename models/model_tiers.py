"""Model tier definitions and task classification for cost-effective routing."""

import re

from models.pricing import PRICING_PER_MILLION

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


def classify_task(description: str) -> str:
    """Classify a task description into a model tier using keyword matching.

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
