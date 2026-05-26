"""Tools to inspect and configure Token Cop budget enforcement.

The enforcement subsystem is OPT-IN — provisioned via
`scripts/setup_enforcement.py --enable`. These tools degrade gracefully
when enforcement isn't configured (returns `enabled: false` rather than
raising), so Token Cop core is unaffected if enforcement is off.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError
from strands import tool

from agent.config import AWS_REGION
from agent.tracing import get_tracer
from models.normalization import normalize_principal_arn

# SSM keys are owned by setup_enforcement.py and reflect provisioned state.
SSM_TABLE = "/token-cop/enforcement-table"
SSM_DEFAULT_BUDGET = "/token-cop/enforcement-default-budget-usd"


def _decimal_to_float(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _decimal_to_float(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decimal_to_float(v) for v in value]
    return value


def _config() -> tuple[str, float] | None:
    """Resolve (table_name, default_budget_usd). None if not provisioned."""
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    try:
        table = ssm.get_parameter(Name=SSM_TABLE)["Parameter"]["Value"]
    except ClientError:
        return None
    try:
        default = float(ssm.get_parameter(Name=SSM_DEFAULT_BUDGET)["Parameter"]["Value"])
    except ClientError:
        default = 200.0
    return table, default


def _disabled_response() -> str:
    return json.dumps({
        "enabled": False,
        "message": (
            "Token Cop budget enforcement is not provisioned. "
            "Run `python -m scripts.setup_enforcement --enable` to opt in."
        ),
    })


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


@tool
def enforcement_status(principal: str = "") -> str:
    """Report the current Token Cop enforcement state.

    With no arguments, returns global status (whether the system is
    provisioned and the default monthly budget). With `principal=<arn>`,
    also returns that principal's current-month usage, budget, and
    whether they're currently denied.

    Args:
        principal: Optional IAM principal ARN to look up.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(
        "tool.enforcement_status",
        attributes={"token_cop.enforcement.principal": principal or "global"},
    ):
        cfg = _config()
        if not cfg:
            return _disabled_response()
        table_name, default_budget = cfg

        result: dict = {
            "enabled": True,
            "table": table_name,
            "default_budget_usd": default_budget,
            "month": _current_month(),
        }

        if principal:
            ddb = boto3.resource("dynamodb", region_name=AWS_REGION).Table(table_name)
            usage = ddb.get_item(Key={"principal_arn": principal, "sk": _current_month()}).get("Item")
            budget_item = ddb.get_item(Key={"principal_arn": principal, "sk": "budget"}).get("Item")
            denied_item = ddb.get_item(Key={"principal_arn": principal, "sk": "denied"}).get("Item")

            budget_usd = float(budget_item["monthly_usd"]) if budget_item else default_budget
            cost_usd = float(usage.get("cost_usd", 0)) if usage else 0.0
            result["principal"] = {
                "arn": normalize_principal_arn(principal),
                "current_month_cost_usd": round(cost_usd, 4),
                "budget_usd": budget_usd,
                "pct_used": round(cost_usd / budget_usd * 100, 1) if budget_usd > 0 else 0,
                "tokens_in": int(usage.get("tokens_in", 0)) if usage else 0,
                "tokens_out": int(usage.get("tokens_out", 0)) if usage else 0,
                "calls": int(usage.get("calls", 0)) if usage else 0,
                "denied": denied_item is not None,
            }

        return json.dumps(_decimal_to_float(result), indent=2)


@tool
def set_principal_budget(principal: str, monthly_usd: float) -> str:
    """Set a per-principal monthly Bedrock budget override.

    Without an override, principals fall back to the default budget set
    at provisioning time (`scripts/setup_enforcement.py --default-budget-usd`).

    Args:
        principal: IAM principal ARN (e.g. `arn:aws:iam::123:user/alice`).
        monthly_usd: Monthly budget cap in USD. Pass 0 to effectively block all calls
            from this principal until the next month.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(
        "tool.set_principal_budget",
        attributes={
            "token_cop.enforcement.principal": principal,
            "token_cop.enforcement.budget_usd": monthly_usd,
        },
    ):
        cfg = _config()
        if not cfg:
            return _disabled_response()
        table_name, _ = cfg

        if not principal or not principal.startswith("arn:"):
            return json.dumps({"error": "principal must be a valid IAM ARN"})
        if monthly_usd < 0:
            return json.dumps({"error": "monthly_usd must be >= 0"})

        ddb = boto3.resource("dynamodb", region_name=AWS_REGION).Table(table_name)
        ddb.put_item(Item={
            "principal_arn": principal,
            "sk": "budget",
            "monthly_usd": Decimal(str(monthly_usd)),
            "set_at": datetime.now(timezone.utc).isoformat(),
        })

        return json.dumps({
            "enabled": True,
            "principal": normalize_principal_arn(principal),
            "monthly_usd": monthly_usd,
            "status": "updated",
        }, indent=2)


@tool
def list_denied_principals() -> str:
    """List IAM principals currently blocked by Token Cop enforcement.

    Returns each denied principal with the cost-at-deny snapshot. The
    monthly reset job (1st of month, 00:05 UTC) clears these
    automatically; no manual action needed unless you want to unblock
    someone early.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("tool.list_denied_principals"):
        cfg = _config()
        if not cfg:
            return _disabled_response()
        table_name, _ = cfg

        ddb = boto3.resource("dynamodb", region_name=AWS_REGION).Table(table_name)
        denied = []
        last_key = None
        while True:
            scan_kwargs = {
                "FilterExpression": "sk = :sk",
                "ExpressionAttributeValues": {":sk": "denied"},
            }
            if last_key:
                scan_kwargs["ExclusiveStartKey"] = last_key
            resp = ddb.scan(**scan_kwargs)
            for item in resp.get("Items", []):
                denied.append({
                    "principal_arn": normalize_principal_arn(item["principal_arn"]),
                    "month": item.get("month", ""),
                    "cost_at_deny_usd": float(item.get("cost_at_deny_usd", 0)),
                })
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break

        return json.dumps({
            "enabled": True,
            "denied_count": len(denied),
            "denied": denied,
        }, indent=2)
