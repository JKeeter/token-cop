"""Cost Explorer-backed attribution breakdown for AWS Bedrock spend.

Groups Bedrock cost by IAM principal, cost-allocation tag, usage type, or
linked account. Consumes the CUR 2.0 attribution data enabled via
``scripts/enable_cur_attribution.py``.

Required IAM permissions (attach to the runtime / gateway role):
    - ce:GetCostAndUsage
    - ce:GetDimensionValues
    - ce:GetTags

If the CUR 2.0 IAM-principal export hasn't been enabled, the principal
grouping will either return empty groups or raise ValidationException.
The tool surfaces a ``caveats`` field in that case pointing users at
``docs/cost-attribution.md`` for setup.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError
from strands import tool

from agent.tracing import get_tracer
from models.normalization import normalize_principal_arn
from utils.dates import parse_date

logger = logging.getLogger(__name__)

_BEDROCK_SERVICE = "Amazon Bedrock"
_DOCS_REF = "See docs/cost-attribution.md for setup instructions."


@tool
def attribution_breakdown(
    dimension: str = "principal",
    start_date: str = "",
    end_date: str = "",
) -> str:
    """Break down AWS Bedrock cost by IAM principal, tag, usage type, or account.

    Queries AWS Cost Explorer filtered to ``SERVICE = Amazon Bedrock`` and
    groups the results by the requested dimension. Requires CUR 2.0
    attribution data (IAM principal / cost-allocation tags) to be enabled
    in the payer account — see ``scripts/enable_cur_attribution.py``.

    Args:
        dimension: One of:
            * ``principal`` — group by IAM principal ARN.
            * ``tag:<key>`` — group by a cost-allocation tag (e.g. ``tag:team``).
              The tag must be activated in Billing.
            * ``usage_type`` — group by Bedrock usage type
              (input/output/cache-read/cache-write tokens per model).
            * ``account`` — group by linked AWS account.
        start_date: YYYY-MM-DD (parsed via dateutil). Defaults to 30 days ago.
        end_date: YYYY-MM-DD (parsed via dateutil). Defaults to today.

    Returns:
        JSON string of the form::

            {
              "period": {"start": "...", "end": "..."},
              "dimension": "principal",
              "groups": [
                {"key": "arn:aws:iam::<acct>:user/alice",
                 "cost_usd": 12.34, "usage_quantity": 123456.0}
              ],
              "total_cost_usd": 12.34,
              "caveats": ["..."]
            }
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(
        "tool.attribution_breakdown",
        attributes={"token_cop.attribution.dimension": dimension},
    ):
        return _attribution_breakdown_impl(dimension, start_date, end_date)


def _attribution_breakdown_impl(dimension: str, start_date: str, end_date: str) -> str:
    now = datetime.now(timezone.utc)
    try:
        start = parse_date(start_date) if start_date else now - timedelta(days=30)
        end = parse_date(end_date) if end_date else now
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    # Cost Explorer expects YYYY-MM-DD strings and end is exclusive.
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")
    if start_str == end_str:
        # Bedrock CE requires an exclusive end — widen by one day.
        end_str = (end + timedelta(days=1)).strftime("%Y-%m-%d")

    group_by, caveats = _build_group_by(dimension)
    if group_by is None:
        return json.dumps({
            "error": (
                f"Unknown dimension '{dimension}'. Use one of: "
                "principal, tag:<key>, usage_type, account."
            ),
        })

    ce = boto3.client("ce")
    try:
        response = ce.get_cost_and_usage(
            TimePeriod={"Start": start_str, "End": end_str},
            Granularity="MONTHLY" if (end - start).days >= 28 else "DAILY",
            Metrics=["UnblendedCost", "UsageQuantity"],
            Filter={
                "Dimensions": {
                    "Key": "SERVICE",
                    "Values": [_BEDROCK_SERVICE],
                }
            },
            GroupBy=[group_by],
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("AccessDeniedException", "AccessDenied"):
            return json.dumps({
                "error": (
                    "AccessDeniedException calling Cost Explorer. Attach "
                    "ce:GetCostAndUsage, ce:GetDimensionValues, ce:GetTags "
                    "to the runtime role. " + _DOCS_REF
                ),
                "dimension": dimension,
            })
        if code in ("ValidationException", "DataUnavailableException"):
            return json.dumps({
                "error": (
                    f"Cost Explorer rejected the request for dimension "
                    f"'{dimension}': {exc.response['Error'].get('Message', '')}. "
                    "This usually means CUR 2.0 attribution data isn't enabled. "
                    + _DOCS_REF
                ),
                "dimension": dimension,
                "caveats": caveats,
            })
        raise

    groups = _aggregate_groups(response, dimension)
    total_cost = round(sum(g["cost_usd"] for g in groups), 2)

    if not groups and dimension == "principal":
        caveats.append(
            "No IAM-principal groups returned. If you enabled CUR 2.0 "
            "IAM-principal attribution recently, allow up to 24h for data "
            "to populate. " + _DOCS_REF
        )

    result = {
        "period": {"start": start_str, "end": end_str},
        "dimension": dimension,
        "groups": groups,
        "total_cost_usd": total_cost,
        "caveats": caveats,
    }
    return json.dumps(result, indent=2)


def _build_group_by(dimension: str):
    """Translate our dimension string into a Cost Explorer GroupBy clause.

    Returns ``(group_by_dict, caveats_list)`` or ``(None, [])`` on invalid input.
    """
    caveats: list[str] = []
    if dimension == "principal":
        # CUR 2.0 surfaces IAM principal ARNs via the cost-allocation tag
        # ``aws:PrincipalArn`` (added automatically once principal attribution
        # is enabled on the data export). There is no ``IAM_PRINCIPAL``
        # Cost Explorer dimension as of 2026-04; we use the tag path.
        caveats.append(
            "Principal attribution requires the `aws:PrincipalArn` cost-"
            "allocation tag to be activated. " + _DOCS_REF
        )
        return {"Type": "TAG", "Key": "aws:PrincipalArn"}, caveats
    if dimension.startswith("tag:"):
        tag_key = dimension.split(":", 1)[1].strip()
        if not tag_key:
            return None, []
        return {"Type": "TAG", "Key": tag_key}, caveats
    if dimension == "usage_type":
        return {"Type": "DIMENSION", "Key": "USAGE_TYPE"}, caveats
    if dimension == "account":
        return {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"}, caveats
    return None, []


def _aggregate_groups(response: dict, dimension: str) -> list[dict]:
    """Collapse Cost Explorer's per-period groups into a single ranked list."""
    agg: dict[str, dict] = {}
    for period in response.get("ResultsByTime", []):
        for group in period.get("Groups", []):
            key = _extract_group_key(group, dimension)
            bucket = agg.setdefault(key, {"cost_usd": 0.0, "usage_quantity": 0.0})
            metrics = group.get("Metrics", {})
            cost = metrics.get("UnblendedCost", {}).get("Amount", "0")
            qty = metrics.get("UsageQuantity", {}).get("Amount", "0")
            try:
                bucket["cost_usd"] += float(cost)
            except (TypeError, ValueError):
                pass
            try:
                bucket["usage_quantity"] += float(qty)
            except (TypeError, ValueError):
                pass
    out = [
        {
            "key": key,
            "cost_usd": round(v["cost_usd"], 4),
            "usage_quantity": round(v["usage_quantity"], 2),
        }
        for key, v in agg.items()
    ]
    out.sort(key=lambda g: g["cost_usd"], reverse=True)
    return out


def _extract_group_key(group: dict, dimension: str) -> str:
    """Turn a CE group's ``Keys`` list into a display string.

    CE returns tag groups as ``"<tag_key>$<value>"``; strip the prefix for
    readability. For principal ARNs, scrub the account ID so committed
    sample outputs don't leak it.
    """
    keys = group.get("Keys") or [""]
    raw = keys[0] if keys else ""
    if "$" in raw:
        raw = raw.split("$", 1)[1]
    if not raw:
        raw = "(untagged)"
    if dimension == "principal":
        return normalize_principal_arn(raw)
    return raw
