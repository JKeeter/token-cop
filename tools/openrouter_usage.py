import json
import os
from datetime import datetime, timedelta, timezone

import requests
from strands import tool

from agent.tracing import get_tracer


OPENROUTER_BASE = "https://openrouter.ai/api/v1"


@tool
def openrouter_usage(start_date: str = "", end_date: str = "") -> str:
    """Get OpenRouter usage and spend data.

    Queries the OpenRouter API for current spend totals (daily, weekly, monthly,
    all-time) and credit balance. With a standard API key, returns aggregate
    spend; a management key would provide per-model breakdowns.

    Args:
        start_date: Start date in YYYY-MM-DD format. Defaults to 7 days ago.
        end_date: End date in YYYY-MM-DD format. Defaults to today.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("tool.openrouter_usage", attributes={"token_cop.provider": "openrouter"}):
        return _openrouter_usage_impl(start_date, end_date)


def _openrouter_usage_impl(start_date: str, end_date: str) -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return json.dumps({
            "provider": "openrouter",
            "error": "OPENROUTER_API_KEY not set. Please configure it in .env",
        })

    headers = {"Authorization": f"Bearer {api_key}"}

    now = datetime.now(timezone.utc)
    start = _parse_date(start_date) if start_date else now - timedelta(days=7)
    end = _parse_date(end_date, end_of_day=True) if end_date else now

    try:
        resp = requests.get(
            f"{OPENROUTER_BASE}/auth/key", headers=headers, timeout=10
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
    except requests.RequestException as e:
        return json.dumps({
            "provider": "openrouter",
            "error": f"Failed to query OpenRouter API: {e}",
        })

    # Extract spend data - OpenRouter tracks both credit-based and BYOK usage
    credit_usage = data.get("usage", 0)
    byok_usage = data.get("byok_usage", 0)
    total_spend = credit_usage + byok_usage

    credit_daily = data.get("usage_daily", 0)
    byok_daily = data.get("byok_usage_daily", 0)
    credit_weekly = data.get("usage_weekly", 0)
    byok_weekly = data.get("byok_usage_weekly", 0)
    credit_monthly = data.get("usage_monthly", 0)
    byok_monthly = data.get("byok_usage_monthly", 0)

    limit = data.get("limit")
    balance = round(limit - credit_usage, 4) if limit is not None else "no limit set"
    is_free_tier = data.get("is_free_tier", False)
    is_management_key = data.get("is_management_key", False)

    result = {
        "provider": "openrouter",
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "key_type": "management" if is_management_key else "standard",
        "is_free_tier": is_free_tier,
        "credit_balance_usd": balance,
        "spend": {
            "all_time_usd": round(total_spend, 4),
            "daily_usd": round(credit_daily + byok_daily, 4),
            "weekly_usd": round(credit_weekly + byok_weekly, 4),
            "monthly_usd": round(credit_monthly + byok_monthly, 4),
        },
        "breakdown": {
            "credits_all_time_usd": round(credit_usage, 4),
            "byok_all_time_usd": round(byok_usage, 4),
        },
        "note": (
            "Per-model breakdown requires a management API key. "
            "Current key is a standard API key providing aggregate spend only."
            if not is_management_key
            else "Management key detected. Per-model analytics available."
        ),
    }
    return json.dumps(result, indent=2)


def _parse_date(date_str: str, end_of_day: bool = False) -> datetime:
    """Parse a date string, handling multiple formats the LLM might use."""
    from dateutil import parser as dateutil_parser

    date_str = date_str.strip()
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            dt = dateutil_parser.parse(date_str).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc) - timedelta(days=30)

    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt
