import json
import os
from datetime import datetime, timedelta, timezone

import requests
from strands import tool

from agent.tracing import get_tracer
from models.normalization import normalize_model_name
from models.pricing import estimate_cost
from models.schemas import TokenUsageRecord


OPENAI_BASE = "https://api.openai.com/v1/organization"


@tool
def openai_usage(start_date: str = "", end_date: str = "") -> str:
    """Get OpenAI API token usage from the Organization Usage API.

    Queries the OpenAI admin API for completions usage broken down by model,
    and the costs endpoint for actual billed amounts.

    Args:
        start_date: Start date in YYYY-MM-DD format. Defaults to 7 days ago.
        end_date: End date in YYYY-MM-DD format. Defaults to today.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("tool.openai_usage", attributes={"token_cop.provider": "openai"}):
        return _openai_usage_impl(start_date, end_date)


def _openai_usage_impl(start_date: str, end_date: str) -> str:
    api_key = os.environ.get("OPENAI_ADMIN_API_KEY")
    org_id = os.environ.get("OPENAI_ORG_ID")
    if not api_key:
        return json.dumps({
            "provider": "openai",
            "error": "OPENAI_ADMIN_API_KEY not set. Please configure it in .env",
        })

    headers = {"Authorization": f"Bearer {api_key}"}
    if org_id:
        headers["OpenAI-Organization"] = org_id

    now = datetime.now(timezone.utc)
    start = _parse_date(start_date) if start_date else now - timedelta(days=7)
    end = _parse_date(end_date, end_of_day=True) if end_date else now

    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    # Fetch completions usage grouped by model
    records = _fetch_completions_usage(headers, start_ts, end_ts)

    # Fetch costs for actual billed amounts
    total_billed = _fetch_costs(headers, start_ts, end_ts)

    total_input = sum(r.input_tokens for r in records)
    total_output = sum(r.output_tokens for r in records)
    total_cache_read = sum(r.cache_read_tokens for r in records)
    total_estimated = sum(r.estimated_cost_usd for r in records)
    total_requests = sum(r.request_count for r in records)

    result = {
        "provider": "openai",
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_read_tokens": total_cache_read,
        "total_cache_write_tokens": 0,
        "total_tokens": total_input + total_output + total_cache_read,
        "total_requests": total_requests,
        "total_estimated_cost_usd": round(total_estimated, 2),
        "total_billed_cost_usd": round(total_billed, 2) if total_billed else "not available",
        "by_model": [
            {
                "model": r.model,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cache_read_tokens": r.cache_read_tokens,
                "cache_write_tokens": 0,
                "total_tokens": r.total_tokens,
                "requests": r.request_count,
                "estimated_cost_usd": r.estimated_cost_usd,
            }
            for r in sorted(records, key=lambda r: r.total_tokens, reverse=True)
        ],
    }
    return json.dumps(result, indent=2)


def _parse_date(date_str: str, end_of_day: bool = False) -> datetime:
    """Parse a date string. Raises ValueError on failure."""
    from utils.dates import parse_date
    return parse_date(date_str, end_of_day=end_of_day)


def _fetch_completions_usage(
    headers: dict, start_ts: int, end_ts: int
) -> list[TokenUsageRecord]:
    """Fetch completions usage from OpenAI, paginating through all buckets."""
    model_usage: dict[str, dict] = {}
    page = None

    while True:
        params: dict = {
            "start_time": start_ts,
            "end_time": end_ts,
            "limit": 7,
            "group_by": ["model"],
        }
        if page:
            params["page"] = page

        try:
            resp = requests.get(
                f"{OPENAI_BASE}/usage/completions",
                headers=headers,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException:
            break

        for bucket in data.get("data", []):
            for result in bucket.get("results", []):
                raw_model = result.get("model", "unknown")
                normalized = normalize_model_name(raw_model)
                inp = result.get("input_tokens", 0)
                out = result.get("output_tokens", 0)
                cached = result.get("input_cached_tokens", 0)
                reqs = result.get("num_model_requests", 0)

                if normalized not in model_usage:
                    model_usage[normalized] = {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                        "request_count": 0,
                    }
                model_usage[normalized]["input_tokens"] += inp
                model_usage[normalized]["output_tokens"] += out
                model_usage[normalized]["cache_read_tokens"] += cached
                model_usage[normalized]["request_count"] += reqs

        if not data.get("has_more"):
            break
        page = data.get("next_page")

    records = []
    for model, usage in model_usage.items():
        inp, out = usage["input_tokens"], usage["output_tokens"]
        if inp == 0 and out == 0:
            continue
        cost = estimate_cost(model, inp, out, cache_read_tokens=usage["cache_read_tokens"])
        records.append(TokenUsageRecord(
            provider="openai",
            model=model,
            date="aggregate",
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=usage["cache_read_tokens"],
            request_count=usage["request_count"],
            estimated_cost_usd=round(cost, 4),
        ))

    return records


def _fetch_costs(headers: dict, start_ts: int, end_ts: int) -> float:
    """Fetch actual billed costs from OpenAI costs endpoint."""
    total = 0.0
    page = None

    while True:
        params: dict = {"start_time": start_ts, "end_time": end_ts, "limit": 7}
        if page:
            params["page"] = page

        try:
            resp = requests.get(
                f"{OPENAI_BASE}/costs",
                headers=headers,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException:
            break

        for bucket in data.get("data", []):
            for result in bucket.get("results", []):
                # Cost is in cents
                amount = result.get("amount", {})
                total += amount.get("value", 0)

        if not data.get("has_more"):
            break
        page = data.get("next_page")

    return total / 100.0 if total else 0.0
