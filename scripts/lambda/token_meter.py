"""Token Cop budget meter Lambda.

Triggered by a CloudWatch Logs subscription filter on the Bedrock model
invocation log group. For each record:

  1. Extract IAM principal ARN, model id, input/output token counts.
  2. Atomically increment the per-principal monthly usage row in DynamoDB.
  3. Resolve the principal's budget (per-principal item, falling back to
     the DEFAULT_BUDGET_USD env var).
  4. If running cost has crossed the budget AND the principal is not
     already marked denied, attach the BedrockBudgetDeny managed policy
     and write a "denied" marker so we don't repeat the API call on
     every subsequent invocation.

Self-contained — no Token Cop package import. Pricing table is duplicated
here to keep the Lambda zero-dependency.
"""
import base64
import gzip
import io
import json
import logging
import os
from decimal import Decimal

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

TABLE_NAME = os.environ["TABLE_NAME"]
DENY_POLICY_ARN = os.environ["DENY_POLICY_ARN"]
DEFAULT_BUDGET_USD = float(os.environ.get("DEFAULT_BUDGET_USD", "200"))

ddb = boto3.resource("dynamodb").Table(TABLE_NAME)
iam = boto3.client("iam")

# Pricing per 1M tokens, mirroring models/pricing.py. Kept inline so the
# Lambda has no Token Cop package dependency. Update both when prices change.
PRICING = {
    "claude-opus-4.7": (5.00, 25.00),
    "claude-opus-4.6": (5.00, 25.00),
    "claude-sonnet-4.6": (3.00, 15.00),
    "claude-sonnet-4.5": (3.00, 15.00),
    "claude-haiku-4.5": (1.00, 5.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-3.5-sonnet": (3.00, 15.00),
    "claude-3.5-haiku": (0.80, 4.00),
    "claude-3-opus": (15.00, 75.00),
    "amazon-nova-pro": (0.80, 3.20),
    "amazon-nova-lite": (0.06, 0.24),
    "amazon-nova-micro": (0.035, 0.14),
    "llama-3.1-405b": (5.32, 16.00),
    "llama-3.1-70b": (0.72, 0.72),
    "llama-3.1-8b": (0.22, 0.22),
}
DEFAULT_PRICING = (1.00, 3.00)


def _normalize_model(model_id: str) -> str:
    """Match models/normalization.py's tail-based heuristic."""
    if not model_id:
        return ""
    m = model_id.lower()
    # Strip Bedrock cross-region prefix and provider segment.
    for prefix in ("us.", "eu.", "apac."):
        if m.startswith(prefix):
            m = m[len(prefix):]
            break
    if "." in m:
        m = m.split(".", 1)[1]
    # Trim version/date suffix like "-20250514-v1:0"
    for sep in ("-2024", "-2025", "-2026", "-v1", "-v2"):
        idx = m.find(sep)
        if idx != -1:
            m = m[:idx]
            break
    return m


def _cost(model_id: str, in_tok: int, out_tok: int) -> float:
    pricing = PRICING.get(_normalize_model(model_id), DEFAULT_PRICING)
    return (in_tok * pricing[0] + out_tok * pricing[1]) / 1_000_000


def _month_key(timestamp: str) -> str:
    """Bedrock timestamps are ISO 8601 — first 7 chars are YYYY-MM."""
    if timestamp and len(timestamp) >= 7:
        return timestamp[:7]
    # Fallback to current month if timestamp missing/malformed.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _principal_role_or_user(arn: str) -> tuple[str, str] | None:
    """Return (kind, name) where kind is 'user' or 'role', or None.

    Examples:
        arn:aws:iam::123:user/alice              -> ("user", "alice")
        arn:aws:sts::123:assumed-role/Dev/alice  -> ("role", "Dev")
    """
    if not arn or not arn.startswith("arn:"):
        return None
    parts = arn.split(":")
    if len(parts) < 6:
        return None
    resource = parts[5]
    if resource.startswith("user/"):
        return ("user", resource.split("/", 1)[1])
    if resource.startswith("assumed-role/"):
        # assumed-role/<RoleName>/<session-name>
        segments = resource.split("/")
        if len(segments) >= 2:
            return ("role", segments[1])
    if resource.startswith("role/"):
        return ("role", resource.split("/", 1)[1])
    return None


def _attach_deny(arn: str) -> bool:
    target = _principal_role_or_user(arn)
    if not target:
        log.warning("Cannot attach deny — unrecognized principal ARN: %s", arn)
        return False
    kind, name = target
    try:
        if kind == "user":
            iam.attach_user_policy(UserName=name, PolicyArn=DENY_POLICY_ARN)
        else:
            iam.attach_role_policy(RoleName=name, PolicyArn=DENY_POLICY_ARN)
        log.info("Attached deny policy to %s/%s", kind, name)
        return True
    except Exception:
        log.exception("Failed to attach deny policy to %s/%s", kind, name)
        return False


def _get_budget(principal_arn: str) -> float:
    resp = ddb.get_item(Key={"principal_arn": principal_arn, "sk": "budget"})
    item = resp.get("Item")
    if item and "monthly_usd" in item:
        return float(item["monthly_usd"])
    return DEFAULT_BUDGET_USD


def _mark_denied(principal_arn: str, month: str, cost_at_deny: float):
    ddb.put_item(Item={
        "principal_arn": principal_arn,
        "sk": "denied",
        "month": month,
        "cost_at_deny_usd": Decimal(str(round(cost_at_deny, 4))),
    })


def _is_denied(principal_arn: str) -> bool:
    resp = ddb.get_item(Key={"principal_arn": principal_arn, "sk": "denied"})
    return "Item" in resp


def _process_record(record: dict):
    identity = record.get("identity") or {}
    arn = identity.get("arn") if isinstance(identity, dict) else None
    if not arn:
        return  # No principal → skip silently. Token Cop reports attribution coverage separately.

    in_tok = int(record.get("inputTokenCount") or 0)
    out_tok = int(record.get("outputTokenCount") or 0)
    if in_tok == 0 and out_tok == 0:
        return

    model = record.get("modelId") or ""
    cost = _cost(model, in_tok, out_tok)
    month = _month_key(record.get("timestamp", ""))

    # Atomic increment. Returns the new totals so we can budget-check
    # without a second read.
    resp = ddb.update_item(
        Key={"principal_arn": arn, "sk": month},
        UpdateExpression=(
            "ADD tokens_in :i, tokens_out :o, cost_usd :c, calls :one"
        ),
        ExpressionAttributeValues={
            ":i": in_tok,
            ":o": out_tok,
            ":c": Decimal(str(round(cost, 6))),
            ":one": 1,
        },
        ReturnValues="UPDATED_NEW",
    )
    new_cost = float(resp["Attributes"].get("cost_usd", 0))

    budget = _get_budget(arn)
    if new_cost <= budget:
        return

    # Over budget. Skip if already denied this month — attach is idempotent
    # but the IAM API call costs latency on every record otherwise.
    if _is_denied(arn):
        return

    if _attach_deny(arn):
        _mark_denied(arn, month, new_cost)


def _decode_cwl_event(event: dict) -> list[dict]:
    """CloudWatch Logs subscription events arrive base64+gzip wrapped."""
    payload = event.get("awslogs", {}).get("data")
    if not payload:
        return []
    raw = gzip.decompress(base64.b64decode(payload))
    parsed = json.loads(raw)
    records = []
    for log_event in parsed.get("logEvents", []):
        message = log_event.get("message")
        if not message:
            continue
        try:
            records.append(json.loads(message))
        except json.JSONDecodeError:
            log.warning("Skipping non-JSON log event")
    return records


def handler(event, _context):
    records = _decode_cwl_event(event)
    for record in records:
        try:
            _process_record(record)
        except Exception:
            # One bad record must not poison the batch — Bedrock log schemas
            # vary, and we'd rather drop a metric than retry forever.
            log.exception("Failed to process record")
    return {"processed": len(records)}
