import json

from strands import tool


@tool
def check_budget(
    current_spend_usd: float,
    budget_usd: float,
    days_elapsed: int,
    days_in_period: int = 30,
) -> str:
    """Check current spending against a budget and project end-of-period spend.

    Use this after getting usage data to evaluate budget status. Calculates
    daily burn rate, projected spend, and whether the user is on track.

    Args:
        current_spend_usd: Amount spent so far in the period.
        budget_usd: Budget limit for the period.
        days_elapsed: Number of days elapsed in the budget period.
        days_in_period: Total days in the budget period (default 30).
    """
    if days_elapsed <= 0:
        return json.dumps({"error": "days_elapsed must be positive"})

    daily_rate = current_spend_usd / days_elapsed
    projected_spend = daily_rate * days_in_period
    remaining_budget = budget_usd - current_spend_usd
    days_remaining = days_in_period - days_elapsed

    if days_remaining > 0 and remaining_budget > 0:
        safe_daily_rate = remaining_budget / days_remaining
    else:
        safe_daily_rate = 0

    on_track = projected_spend <= budget_usd
    pct_used = (current_spend_usd / budget_usd * 100) if budget_usd > 0 else 0
    pct_period = (days_elapsed / days_in_period * 100) if days_in_period > 0 else 0

    result = {
        "budget_usd": budget_usd,
        "current_spend_usd": round(current_spend_usd, 2),
        "remaining_budget_usd": round(remaining_budget, 2),
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "daily_burn_rate_usd": round(daily_rate, 2),
        "projected_spend_usd": round(projected_spend, 2),
        "on_track": on_track,
        "budget_pct_used": round(pct_used, 1),
        "period_pct_elapsed": round(pct_period, 1),
        "safe_daily_rate_usd": round(safe_daily_rate, 2),
        "status": "UNDER_BUDGET" if on_track else "OVER_BUDGET",
    }
    return json.dumps(result, indent=2)
