import json
from datetime import datetime, timedelta, timezone

import boto3
from strands import tool

from agent.tracing import get_tracer
from models.normalization import normalize_model_name
from models.pricing import estimate_cost
from models.schemas import TokenUsageRecord


@tool
def bedrock_usage(start_date: str = "", end_date: str = "", model_filter: str = "") -> str:
    """Get AWS Bedrock token usage from CloudWatch metrics.

    Queries CloudWatch for Bedrock model invocation metrics including
    input tokens, output tokens, and invocation count. Returns usage
    broken down by model.

    Args:
        start_date: Start date in YYYY-MM-DD format. Defaults to 7 days ago.
        end_date: End date in YYYY-MM-DD format. Defaults to today.
        model_filter: Optional model ID substring to filter by (e.g. 'claude', 'nova').
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("tool.bedrock_usage", attributes={"token_cop.provider": "bedrock"}):
        return _bedrock_usage_impl(start_date, end_date, model_filter)


def _bedrock_usage_impl(start_date: str, end_date: str, model_filter: str) -> str:
    now = datetime.now(timezone.utc)
    start = _parse_date(start_date) if start_date else now - timedelta(days=7)
    end = _parse_date(end_date, end_of_day=True) if end_date else now

    cw = boto3.client("cloudwatch")

    # List all Bedrock models that have reported metrics in the period
    model_ids = _discover_bedrock_models(cw, start, end)

    if model_filter:
        model_ids = [m for m in model_ids if model_filter.lower() in m.lower()]

    if not model_ids:
        return json.dumps({
            "provider": "bedrock",
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
            "message": "No Bedrock model usage found in this period.",
            "records": [],
        })

    # Aggregate by normalized model name (us. and global. prefixes merge)
    aggregated: dict[str, dict] = {}
    for model_id in model_ids:
        input_tokens, output_tokens, invocations, cache_read, cache_write = _get_model_metrics(
            cw, model_id, start, end
        )
        normalized = normalize_model_name(model_id)
        if normalized not in aggregated:
            aggregated[normalized] = {
                "input_tokens": 0, "output_tokens": 0, "invocations": 0,
                "cache_read_tokens": 0, "cache_write_tokens": 0,
            }
        aggregated[normalized]["input_tokens"] += input_tokens
        aggregated[normalized]["output_tokens"] += output_tokens
        aggregated[normalized]["invocations"] += invocations
        aggregated[normalized]["cache_read_tokens"] += cache_read
        aggregated[normalized]["cache_write_tokens"] += cache_write

    date_range = f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"
    records = []
    for model, usage in aggregated.items():
        inp, out = usage["input_tokens"], usage["output_tokens"]
        cache_read, cache_write = usage["cache_read_tokens"], usage["cache_write_tokens"]
        if inp == 0 and out == 0:
            continue  # Skip models with no usage in the period
        cost = estimate_cost(model, inp, out, cache_read_tokens=cache_read, cache_write_tokens=cache_write)
        records.append(TokenUsageRecord(
            provider="bedrock",
            model=model,
            date=date_range,
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            request_count=usage["invocations"],
            estimated_cost_usd=cost,
        ))

    total_input = sum(r.input_tokens for r in records)
    total_output = sum(r.output_tokens for r in records)
    total_cache_read = sum(r.cache_read_tokens for r in records)
    total_cache_write = sum(r.cache_write_tokens for r in records)
    total_cost = sum(r.estimated_cost_usd for r in records)
    total_requests = sum(r.request_count for r in records)

    result = {
        "provider": "bedrock",
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_input + total_output + total_cache_read + total_cache_write,
        "total_cache_read_tokens": total_cache_read,
        "total_cache_write_tokens": total_cache_write,
        "total_requests": total_requests,
        "total_estimated_cost_usd": round(total_cost, 2),
        "by_model": [
            {
                "model": r.model,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "total_tokens": r.total_tokens,
                "cache_read_tokens": r.cache_read_tokens,
                "cache_write_tokens": r.cache_write_tokens,
                "requests": r.request_count,
                "estimated_cost_usd": r.estimated_cost_usd,
            }
            for r in sorted(records, key=lambda r: r.total_tokens, reverse=True)
        ],
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
            # Last resort: treat as relative like "30 days ago"
            return datetime.now(timezone.utc) - timedelta(days=30)

    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt


def _discover_bedrock_models(cw, start: datetime, end: datetime) -> list[str]:
    """Find all Bedrock model IDs that have metrics in the time range.

    Only collects metrics where ModelId is the sole dimension to avoid
    double-counting from multi-dimension metrics (e.g. ContextWindow+ModelId).
    """
    model_ids = set()
    paginator = cw.get_paginator("list_metrics")
    for page in paginator.paginate(
        Namespace="AWS/Bedrock",
        MetricName="InputTokenCount",
    ):
        for metric in page["Metrics"]:
            dims = metric["Dimensions"]
            # Only single-dimension ModelId metrics (avoids double-counting)
            if len(dims) == 1 and dims[0]["Name"] == "ModelId":
                model_ids.add(dims[0]["Value"])
    return sorted(model_ids)


def _get_model_metrics(
    cw, model_id: str, start: datetime, end: datetime
) -> tuple[int, int, int, int, int]:
    """Get aggregated token counts and invocation count for a model."""
    response = cw.get_metric_data(
        MetricDataQueries=[
            {
                "Id": "input_tokens",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/Bedrock",
                        "MetricName": "InputTokenCount",
                        "Dimensions": [{"Name": "ModelId", "Value": model_id}],
                    },
                    "Period": 86400,  # 1 day
                    "Stat": "Sum",
                },
            },
            {
                "Id": "output_tokens",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/Bedrock",
                        "MetricName": "OutputTokenCount",
                        "Dimensions": [{"Name": "ModelId", "Value": model_id}],
                    },
                    "Period": 86400,
                    "Stat": "Sum",
                },
            },
            {
                "Id": "invocations",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/Bedrock",
                        "MetricName": "Invocations",
                        "Dimensions": [{"Name": "ModelId", "Value": model_id}],
                    },
                    "Period": 86400,
                    "Stat": "Sum",
                },
            },
            {
                "Id": "cache_read_tokens",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/Bedrock",
                        "MetricName": "CacheReadInputTokenCount",
                        "Dimensions": [{"Name": "ModelId", "Value": model_id}],
                    },
                    "Period": 86400,
                    "Stat": "Sum",
                },
            },
            {
                "Id": "cache_write_tokens",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/Bedrock",
                        "MetricName": "CacheWriteInputTokenCount",
                        "Dimensions": [{"Name": "ModelId", "Value": model_id}],
                    },
                    "Period": 86400,
                    "Stat": "Sum",
                },
            },
        ],
        StartTime=start,
        EndTime=end,
    )

    input_tokens = 0
    output_tokens = 0
    invocations = 0
    cache_read = 0
    cache_write = 0
    for result in response["MetricDataResults"]:
        total = int(sum(result["Values"]))
        if result["Id"] == "input_tokens":
            input_tokens = total
        elif result["Id"] == "output_tokens":
            output_tokens = total
        elif result["Id"] == "invocations":
            invocations = total
        elif result["Id"] == "cache_read_tokens":
            cache_read = total
        elif result["Id"] == "cache_write_tokens":
            cache_write = total

    return input_tokens, output_tokens, invocations, cache_read, cache_write
