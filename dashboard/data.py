"""Data aggregation layer for the Token Cop dashboard.

Fetches usage data from all providers by calling the existing tool functions
directly (not via MCP gateway). Handles Strands @tool return format and
provider failures gracefully.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from models.model_tiers import get_model_tier, TIERS
from models.pricing import PRICING_PER_MILLION

logger = logging.getLogger(__name__)


def _parse_tool_result(raw) -> dict | None:
    """Parse the result from a Strands @tool-decorated function.

    Handles both direct dict results and tool_result wrapper format.
    Returns parsed JSON dict, or None on failure.
    """
    try:
        if isinstance(raw, dict) and "content" in raw:
            text = "".join(
                block.get("text", "")
                for block in raw["content"]
                if isinstance(block, dict)
            )
        else:
            text = str(raw)
        data = json.loads(text)
        if "error" in data:
            logger.warning("Provider returned error: %s", data.get("error"))
            return None
        return data
    except (json.JSONDecodeError, TypeError, AttributeError) as exc:
        logger.warning("Failed to parse tool result: %s", exc)
        return None


def fetch_all_provider_data(start_date: str, end_date: str) -> dict:
    """Call all 3 provider tools and return combined data.

    Returns a dict with keys: providers (list of parsed results),
    combined_models (list of model records), totals, and metadata.
    Handles individual provider failures gracefully.
    """
    from tools.bedrock_usage import bedrock_usage
    from tools.openai_usage import openai_usage
    from tools.openrouter_usage import openrouter_usage

    providers = []
    errors = []

    for name, fn in [("bedrock", bedrock_usage), ("openai", openai_usage),
                      ("openrouter", openrouter_usage)]:
        try:
            raw = fn(start_date=start_date, end_date=end_date)
            parsed = _parse_tool_result(raw)
            if parsed:
                providers.append(parsed)
            else:
                errors.append(name)
        except Exception as exc:
            logger.warning("Provider %s failed: %s", name, exc)
            errors.append(name)

    # Combine all model records
    combined_models = []
    for pdata in providers:
        for m in pdata.get("by_model", []):
            m.setdefault("provider", pdata.get("provider", "unknown"))
            combined_models.append(m)

    # Compute totals
    total_input = sum(m.get("input_tokens", 0) for m in combined_models)
    total_output = sum(m.get("output_tokens", 0) for m in combined_models)
    total_cache_read = sum(m.get("cache_read_tokens", 0) for m in combined_models)
    total_cache_write = sum(m.get("cache_write_tokens", 0) for m in combined_models)
    total_cost = sum(m.get("estimated_cost_usd", 0) for m in combined_models)
    total_requests = sum(m.get("requests", 0) for m in combined_models)

    # Include OpenRouter aggregate spend if it has no by_model breakdown
    openrouter_spend = 0.0
    for pdata in providers:
        if pdata.get("provider") == "openrouter" and not pdata.get("by_model"):
            spend = pdata.get("spend", {})
            openrouter_spend = spend.get("weekly_usd", 0)

    return {
        "providers": providers,
        "combined_models": combined_models,
        "errors": errors,
        "totals": {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_read_tokens": total_cache_read,
            "cache_write_tokens": total_cache_write,
            "total_cost_usd": round(total_cost + openrouter_spend, 2),
            "total_requests": total_requests,
        },
        "start_date": start_date,
        "end_date": end_date,
    }


def fetch_audit_data(days: int = 7) -> dict | None:
    """Call token_audit and return parsed audit scores.

    Returns parsed audit dict, or None on failure.
    """
    from tools.audit import token_audit

    try:
        raw = token_audit(days=days)
        return _parse_tool_result(raw)
    except Exception as exc:
        logger.warning("Audit tool failed: %s", exc)
        return None


def compute_smart_token_score(data: dict) -> float:
    """Compute a composite 'Smart Token Score' (0-100).

    Formula: (useful_output_tokens / total_input_tokens) * cache_bonus * 100
    Where cache_bonus = 1 + (cache_read_ratio * 0.5)

    Higher score = more efficient use of tokens.
    """
    totals = data.get("totals", {})
    total_input = totals.get("input_tokens", 0)
    total_output = totals.get("output_tokens", 0)
    cache_read = totals.get("cache_read_tokens", 0)

    if total_input == 0:
        return 0.0

    # Base ratio: how many output tokens per input token
    output_ratio = min(total_output / total_input, 1.0)

    # Cache bonus: reward for using cached tokens
    cache_ratio = cache_read / total_input if total_input > 0 else 0
    cache_bonus = 1.0 + (cache_ratio * 0.5)

    # Model mix bonus: reward for using cheaper models where appropriate
    models = data.get("combined_models", [])
    total_reqs = totals.get("total_requests", 0)
    polish_reqs = sum(
        m.get("requests", 0) for m in models
        if get_model_tier(m.get("model", "")) == "polish"
    )
    mix_bonus = 1.0 + (polish_reqs / total_reqs * 0.3) if total_reqs > 0 else 1.0

    score = output_ratio * cache_bonus * mix_bonus * 100
    return round(min(score, 100.0), 1)


def get_model_mix(data: dict) -> list[dict]:
    """Return per-model breakdown with cost percentages.

    Returns list of dicts sorted by cost descending:
    [{model, provider, requests, cost, input_tokens, output_tokens,
      cache_read_tokens, percentage, tier}]
    """
    models = data.get("combined_models", [])
    total_cost = data.get("totals", {}).get("total_cost_usd", 0)

    result = []
    for m in models:
        cost = m.get("estimated_cost_usd", 0)
        pct = (cost / total_cost * 100) if total_cost > 0 else 0
        tier = get_model_tier(m.get("model", "")) or "unknown"
        result.append({
            "model": m.get("model", "unknown"),
            "provider": m.get("provider", "unknown"),
            "requests": m.get("requests", 0),
            "cost": round(cost, 2),
            "input_tokens": m.get("input_tokens", 0),
            "output_tokens": m.get("output_tokens", 0),
            "cache_read_tokens": m.get("cache_read_tokens", 0),
            "cache_write_tokens": m.get("cache_write_tokens", 0),
            "percentage": round(pct, 1),
            "tier": tier,
        })

    return sorted(result, key=lambda x: x["cost"], reverse=True)


def get_cost_trend(data_current: dict, data_previous: dict) -> dict:
    """Compare current and previous period costs.

    Returns dict with current/previous totals and change percentages.
    """
    curr_totals = data_current.get("totals", {})
    prev_totals = data_previous.get("totals", {})

    curr_cost = curr_totals.get("total_cost_usd", 0)
    prev_cost = prev_totals.get("total_cost_usd", 0)
    curr_requests = curr_totals.get("total_requests", 0)
    prev_requests = prev_totals.get("total_requests", 0)

    cost_change_pct = (
        ((curr_cost - prev_cost) / prev_cost * 100) if prev_cost > 0 else 0
    )
    curr_cpr = curr_cost / curr_requests if curr_requests > 0 else 0
    prev_cpr = prev_cost / prev_requests if prev_requests > 0 else 0
    cpr_change_pct = (
        ((curr_cpr - prev_cpr) / prev_cpr * 100) if prev_cpr > 0 else 0
    )

    return {
        "current_cost": round(curr_cost, 2),
        "previous_cost": round(prev_cost, 2),
        "cost_change_pct": round(cost_change_pct, 1),
        "current_requests": curr_requests,
        "previous_requests": prev_requests,
        "current_cost_per_request": round(curr_cpr, 4),
        "previous_cost_per_request": round(prev_cpr, 4),
        "cpr_change_pct": round(cpr_change_pct, 1),
    }
