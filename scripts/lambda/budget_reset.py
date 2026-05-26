"""Token Cop monthly reset Lambda.

Triggered by an EventBridge schedule on the 1st of each month at 00:05 UTC.
Scans the usage table for "denied" markers, detaches the BedrockBudgetDeny
managed policy from each principal, then deletes the marker. Usage rows
are kept for historical reporting — Token Cop can read them via
tools/enforcement.py.
"""
import logging
import os

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

TABLE_NAME = os.environ["TABLE_NAME"]
DENY_POLICY_ARN = os.environ["DENY_POLICY_ARN"]

ddb = boto3.resource("dynamodb").Table(TABLE_NAME)
iam = boto3.client("iam")


def _principal_role_or_user(arn: str) -> tuple[str, str] | None:
    if not arn or not arn.startswith("arn:"):
        return None
    parts = arn.split(":")
    if len(parts) < 6:
        return None
    resource = parts[5]
    if resource.startswith("user/"):
        return ("user", resource.split("/", 1)[1])
    if resource.startswith("assumed-role/"):
        segments = resource.split("/")
        if len(segments) >= 2:
            return ("role", segments[1])
    if resource.startswith("role/"):
        return ("role", resource.split("/", 1)[1])
    return None


def _detach(arn: str) -> bool:
    target = _principal_role_or_user(arn)
    if not target:
        log.warning("Skipping unrecognized principal ARN: %s", arn)
        return False
    kind, name = target
    try:
        if kind == "user":
            iam.detach_user_policy(UserName=name, PolicyArn=DENY_POLICY_ARN)
        else:
            iam.detach_role_policy(RoleName=name, PolicyArn=DENY_POLICY_ARN)
        log.info("Detached deny policy from %s/%s", kind, name)
        return True
    except iam.exceptions.NoSuchEntityException:
        # Policy was already detached — safe; we still want to clear the marker.
        return True
    except Exception:
        log.exception("Failed to detach deny policy from %s/%s", kind, name)
        return False


def handler(_event, _context):
    """Scan for denied markers and clear them."""
    detached = 0
    skipped = 0

    paginator_kwargs = {
        "FilterExpression": "sk = :sk",
        "ExpressionAttributeValues": {":sk": "denied"},
        "ProjectionExpression": "principal_arn",
    }

    last_evaluated = None
    while True:
        scan = dict(paginator_kwargs)
        if last_evaluated:
            scan["ExclusiveStartKey"] = last_evaluated
        resp = ddb.scan(**scan)

        for item in resp.get("Items", []):
            arn = item["principal_arn"]
            if _detach(arn):
                ddb.delete_item(Key={"principal_arn": arn, "sk": "denied"})
                detached += 1
            else:
                skipped += 1

        last_evaluated = resp.get("LastEvaluatedKey")
        if not last_evaluated:
            break

    log.info("Monthly reset complete: detached=%d skipped=%d", detached, skipped)
    return {"detached": detached, "skipped": skipped}
