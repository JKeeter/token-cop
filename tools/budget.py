import json

from strands import tool

from agent.tracing import get_tracer


@tool
def check_budget(
    current_spend_usd: float,
    budget_usd: float,
    days_elapsed: int,
    days_in_period: int = 30,
    principal: str = "",
    tag_filter: str = "",
) -> str:
    """Check current spending against a budget and project end-of-period spend.

    Use this after getting usage data to evaluate budget status. Calculates
    daily burn rate, projected spend, and whether the user is on track.

    Can operate at three scopes:
    - global (default): `current_spend_usd` is total organization spend
    - principal: pass `principal="arn:aws:iam::...user/alice"` along with the
      principal-scoped `current_spend_usd` (computed by the caller, e.g. via
      `attribution_breakdown`). Output labels the check as principal-scoped.
    - tag: pass `tag_filter="team=ml-research"` along with the tag-scoped
      `current_spend_usd`. Output labels the check as tag-scoped.

    This tool does NOT call Cost Explorer itself — it is a pure burn-rate
    calculator. The caller provides the already-scoped spend number.

    Args:
        current_spend_usd: Amount spent so far in the period (scoped per args below).
        budget_usd: Budget limit for the period.
        days_elapsed: Number of days elapsed in the budget period.
        days_in_period: Total days in the budget period (default 30).
        principal: Optional IAM principal ARN; scopes the output to that principal.
        tag_filter: Optional tag filter in `key=value` form (e.g. `team=ml-research`).
    """
    tracer = get_tracer()
    scope = "global"
    if principal:
        scope = "principal"
    elif tag_filter:
        scope = "tag"
    attributes = {"token_cop.budget.scope": scope}
    if principal:
        attributes["token_cop.budget.principal"] = principal
    if tag_filter:
        attributes["token_cop.budget.tag_filter"] = tag_filter
    with tracer.start_as_current_span("tool.check_budget", attributes=attributes):
        return _check_budget_impl(
            current_spend_usd=current_spend_usd,
            budget_usd=budget_usd,
            days_elapsed=days_elapsed,
            days_in_period=days_in_period,
            principal=principal,
            tag_filter=tag_filter,
            scope=scope,
        )


def _check_budget_impl(
    current_spend_usd: float,
    budget_usd: float,
    days_elapsed: int,
    days_in_period: int,
    principal: str,
    tag_filter: str,
    scope: str,
) -> str:
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
        "scope": scope,
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
    if principal:
        result["principal"] = principal
    if tag_filter:
        result["tag_filter"] = tag_filter
    return json.dumps(result, indent=2)
