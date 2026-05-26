"""Set up Token Cop budget enforcement (option 3).

Enforcement is OPT-IN. Token Cop core works without it. This script
idempotently provisions the AWS resources that read Bedrock invocation
logs in near-real-time and attach a BedrockBudgetDeny managed policy to
any IAM principal that crosses its monthly budget.

Resources created (all named with the `token-cop-enforcement-` prefix):
  - DynamoDB table  : token-cop-enforcement-usage
  - IAM policy      : TokenCopBedrockBudgetDeny  (attached to over-budget principals)
  - IAM role        : token-cop-enforcement-meter-role
  - Lambda          : token-cop-enforcement-meter
  - IAM role        : token-cop-enforcement-reset-role
  - Lambda          : token-cop-enforcement-reset
  - Subscription filter on the Bedrock invocation log group
  - EventBridge rule: monthly reset (1st of month, 00:05 UTC)
  - SSM parameters  : /token-cop/enforcement-* (consumed by tools/enforcement.py)

Usage:
    source .venv/bin/activate
    python -m scripts.setup_enforcement --status
    python -m scripts.setup_enforcement --enable --log-group /aws/bedrock/invocations
    python -m scripts.setup_enforcement --enable --default-budget-usd 200
    python -m scripts.setup_enforcement --teardown
    python -m scripts.setup_enforcement --dry-run --enable

The --enable flag is the explicit opt-in. Without it (and without the
TOKEN_COP_ENFORCEMENT_ENABLED env var), the script prints status only.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "us-east-1")

# Resource names — all prefixed so teardown is unambiguous.
PREFIX = "token-cop-enforcement"
TABLE_NAME = f"{PREFIX}-usage"
DENY_POLICY_NAME = "TokenCopBedrockBudgetDeny"
METER_FN = f"{PREFIX}-meter"
METER_ROLE = f"{PREFIX}-meter-role"
RESET_FN = f"{PREFIX}-reset"
RESET_ROLE = f"{PREFIX}-reset-role"
SUB_FILTER_NAME = f"{PREFIX}-subscription"
RESET_RULE = f"{PREFIX}-monthly-reset"

# SSM parameter names — read by tools/enforcement.py at runtime.
SSM_PREFIX = "/token-cop"
SSM_TABLE = f"{SSM_PREFIX}/enforcement-table"
SSM_DENY_POLICY_ARN = f"{SSM_PREFIX}/enforcement-deny-policy-arn"
SSM_DEFAULT_BUDGET = f"{SSM_PREFIX}/enforcement-default-budget-usd"
SSM_LOG_GROUP = f"{SSM_PREFIX}/enforcement-log-group"

DEFAULT_LOG_GROUP = "/aws/bedrock/invocations"
DEFAULT_BUDGET_USD = 200.0

DENY_POLICY_DOCUMENT = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "TokenCopBudgetDeny",
            "Effect": "Deny",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:Converse",
                "bedrock:ConverseStream",
            ],
            "Resource": "*",
        }
    ],
}

LAMBDA_ASSUME_ROLE = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}

log = logging.getLogger("setup_enforcement")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def account_id(sts) -> str:
    return sts.get_caller_identity()["Account"]


def _exists(call, *not_found_codes: str):
    """Run an AWS call; return None if NotFound, else the response."""
    try:
        return call()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in not_found_codes:
            return None
        raise


# ---------------------------------------------------------------------------
# DynamoDB
# ---------------------------------------------------------------------------

def ensure_table(ddb, dry_run: bool) -> str:
    """Create the usage table if missing. Returns the table ARN.

    Schema:
        principal_arn (HASH)  - normalized IAM principal ARN, or "_budget" item
        sk (RANGE)            - "YYYY-MM" for usage rows, "budget" for budgets,
                                "denied" for active deny markers
    """
    desc = _exists(
        lambda: ddb.describe_table(TableName=TABLE_NAME),
        "ResourceNotFoundException",
    )
    if desc:
        log.info("DynamoDB table exists: %s", TABLE_NAME)
        return desc["Table"]["TableArn"]

    if dry_run:
        log.info("[dry-run] Would create DynamoDB table: %s", TABLE_NAME)
        return f"arn:aws:dynamodb:{REGION}:DRY-RUN:table/{TABLE_NAME}"

    log.info("Creating DynamoDB table: %s", TABLE_NAME)
    ddb.create_table(
        TableName=TABLE_NAME,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "principal_arn", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "principal_arn", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        Tags=[{"Key": "ManagedBy", "Value": "token-cop-enforcement"}],
    )
    waiter = ddb.get_waiter("table_exists")
    waiter.wait(TableName=TABLE_NAME)
    desc = ddb.describe_table(TableName=TABLE_NAME)
    log.info("Table ready: %s", desc["Table"]["TableArn"])
    return desc["Table"]["TableArn"]


# ---------------------------------------------------------------------------
# IAM policies + roles
# ---------------------------------------------------------------------------

def ensure_deny_policy(iam, acct: str, dry_run: bool) -> str:
    arn = f"arn:aws:iam::{acct}:policy/{DENY_POLICY_NAME}"
    existing = _exists(
        lambda: iam.get_policy(PolicyArn=arn),
        "NoSuchEntity",
    )
    if existing:
        log.info("Deny policy exists: %s", arn)
        return arn

    if dry_run:
        log.info("[dry-run] Would create deny policy: %s", arn)
        return arn

    log.info("Creating deny policy: %s", DENY_POLICY_NAME)
    resp = iam.create_policy(
        PolicyName=DENY_POLICY_NAME,
        PolicyDocument=json.dumps(DENY_POLICY_DOCUMENT),
        Description="Attached by Token Cop when an IAM principal exceeds their monthly Bedrock budget.",
    )
    return resp["Policy"]["Arn"]


def ensure_role(iam, role_name: str, inline_policy: dict, dry_run: bool) -> str:
    existing = _exists(
        lambda: iam.get_role(RoleName=role_name),
        "NoSuchEntity",
    )
    if existing:
        arn = existing["Role"]["Arn"]
        if not dry_run:
            iam.put_role_policy(
                RoleName=role_name,
                PolicyName=f"{role_name}-inline",
                PolicyDocument=json.dumps(inline_policy),
            )
        log.info("IAM role exists: %s (inline policy refreshed)", role_name)
        return arn

    if dry_run:
        log.info("[dry-run] Would create IAM role: %s", role_name)
        return f"arn:aws:iam::DRY-RUN:role/{role_name}"

    log.info("Creating IAM role: %s", role_name)
    resp = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(LAMBDA_ASSUME_ROLE),
    )
    iam.attach_role_policy(
        RoleName=role_name,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{role_name}-inline",
        PolicyDocument=json.dumps(inline_policy),
    )
    # IAM role propagation for Lambda assumeRole: ~10s in practice.
    time.sleep(10)
    return resp["Role"]["Arn"]


def meter_role_inline_policy(table_arn: str, deny_policy_arn: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["dynamodb:UpdateItem", "dynamodb:GetItem", "dynamodb:PutItem"],
                "Resource": table_arn,
            },
            {
                "Effect": "Allow",
                "Action": [
                    "iam:AttachUserPolicy",
                    "iam:AttachRolePolicy",
                    "iam:ListAttachedUserPolicies",
                    "iam:ListAttachedRolePolicies",
                ],
                # Restricted to the deny policy itself via aws:PolicyARN condition
                # below — but AttachUserPolicy needs Resource: user ARN, so we
                # leave the user/role resource open and gate the policy.
                "Resource": "*",
                "Condition": {
                    "ArnEquals": {"iam:PolicyARN": deny_policy_arn},
                },
            },
        ],
    }


def reset_role_inline_policy(table_arn: str, deny_policy_arn: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["dynamodb:Scan", "dynamodb:Query", "dynamodb:DeleteItem"],
                "Resource": table_arn,
            },
            {
                "Effect": "Allow",
                "Action": [
                    "iam:DetachUserPolicy",
                    "iam:DetachRolePolicy",
                    "iam:ListAttachedUserPolicies",
                    "iam:ListAttachedRolePolicies",
                ],
                "Resource": "*",
                "Condition": {
                    "ArnEquals": {"iam:PolicyARN": deny_policy_arn},
                },
            },
        ],
    }


# ---------------------------------------------------------------------------
# Lambda packaging
# ---------------------------------------------------------------------------

LAMBDA_DIR = Path(__file__).parent / "lambda"


def _zip_lambda(src_file: str) -> bytes:
    """Pack a single-file Lambda into a zip with handler at module root."""
    src = LAMBDA_DIR / src_file
    if not src.exists():
        raise FileNotFoundError(f"Lambda source not found: {src}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(src, arcname=src.name)
    return buf.getvalue()


def ensure_lambda(
    lam, fn_name: str, role_arn: str, src_file: str, env: dict, dry_run: bool
) -> str:
    if dry_run:
        log.info("[dry-run] Would deploy Lambda %s with env %s", fn_name, sorted(env))
        return f"arn:aws:lambda:{REGION}:DRY-RUN:function:{fn_name}"

    package = _zip_lambda(src_file)
    handler = f"{Path(src_file).stem}.handler"

    existing = _exists(
        lambda: lam.get_function(FunctionName=fn_name),
        "ResourceNotFoundException",
    )
    if existing:
        log.info("Updating Lambda code: %s", fn_name)
        lam.update_function_code(FunctionName=fn_name, ZipFile=package)
        # Wait for code update before config update (otherwise concurrent
        # update error from Lambda).
        waiter = lam.get_waiter("function_updated")
        waiter.wait(FunctionName=fn_name)
        lam.update_function_configuration(
            FunctionName=fn_name,
            Role=role_arn,
            Handler=handler,
            Environment={"Variables": env},
            Timeout=60,
        )
        return existing["Configuration"]["FunctionArn"]

    log.info("Creating Lambda: %s", fn_name)
    resp = lam.create_function(
        FunctionName=fn_name,
        Runtime="python3.13",
        Role=role_arn,
        Handler=handler,
        Code={"ZipFile": package},
        Environment={"Variables": env},
        Timeout=60,
        Tags={"ManagedBy": "token-cop-enforcement"},
    )
    return resp["FunctionArn"]


# ---------------------------------------------------------------------------
# CloudWatch Logs subscription
# ---------------------------------------------------------------------------

def ensure_subscription_filter(logs, lam, log_group: str, fn_arn: str, fn_name: str, dry_run: bool):
    if dry_run:
        log.info("[dry-run] Would subscribe %s -> %s", log_group, fn_name)
        return

    # Allow CloudWatch Logs to invoke the meter Lambda. Idempotent: the
    # add_permission call fails with ResourceConflictException if the
    # statement ID already exists, which is fine.
    sid = f"{fn_name}-cwlogs-invoke"
    try:
        lam.add_permission(
            FunctionName=fn_name,
            StatementId=sid,
            Action="lambda:InvokeFunction",
            Principal="logs.amazonaws.com",
            SourceArn=f"arn:aws:logs:{REGION}:{fn_arn.split(':')[4]}:log-group:{log_group}:*",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceConflictException":
            raise

    log.info("Subscribing %s to log group %s", fn_name, log_group)
    logs.put_subscription_filter(
        logGroupName=log_group,
        filterName=SUB_FILTER_NAME,
        filterPattern="",  # match all records
        destinationArn=fn_arn,
    )


# ---------------------------------------------------------------------------
# EventBridge monthly reset
# ---------------------------------------------------------------------------

def ensure_monthly_reset(events, lam, fn_arn: str, fn_name: str, dry_run: bool):
    if dry_run:
        log.info("[dry-run] Would create monthly EventBridge schedule: %s", RESET_RULE)
        return

    log.info("Creating EventBridge rule: %s", RESET_RULE)
    events.put_rule(
        Name=RESET_RULE,
        # Cron in EventBridge: minute hour day-of-month month day-of-week year
        ScheduleExpression="cron(5 0 1 * ? *)",  # 00:05 UTC on the 1st
        State="ENABLED",
        Description="Monthly Token Cop budget reset — detaches deny policies.",
    )

    sid = f"{fn_name}-events-invoke"
    try:
        lam.add_permission(
            FunctionName=fn_name,
            StatementId=sid,
            Action="lambda:InvokeFunction",
            Principal="events.amazonaws.com",
            SourceArn=f"arn:aws:events:{REGION}:{fn_arn.split(':')[4]}:rule/{RESET_RULE}",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceConflictException":
            raise

    events.put_targets(
        Rule=RESET_RULE,
        Targets=[{"Id": "token-cop-reset", "Arn": fn_arn}],
    )


# ---------------------------------------------------------------------------
# SSM
# ---------------------------------------------------------------------------

def write_ssm(ssm, name: str, value: str, dry_run: bool):
    if dry_run:
        log.info("[dry-run] Would write SSM %s=%s", name, value)
        return
    ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)


# ---------------------------------------------------------------------------
# Status / teardown
# ---------------------------------------------------------------------------

def show_status(ddb, iam, lam, logs, events, ssm, acct: str):
    print("\nToken Cop Enforcement — Status")
    print("=" * 60)

    desc = _exists(lambda: ddb.describe_table(TableName=TABLE_NAME), "ResourceNotFoundException")
    print(f"  DynamoDB table   : {'OK ' + TABLE_NAME if desc else 'missing'}")

    arn = f"arn:aws:iam::{acct}:policy/{DENY_POLICY_NAME}"
    pol = _exists(lambda: iam.get_policy(PolicyArn=arn), "NoSuchEntity")
    print(f"  Deny policy      : {'OK ' + arn if pol else 'missing'}")

    for fn in (METER_FN, RESET_FN):
        f = _exists(lambda fn=fn: lam.get_function(FunctionName=fn), "ResourceNotFoundException")
        print(f"  Lambda {fn:35} : {'OK' if f else 'missing'}")

    log_group = ""
    p = _exists(lambda: ssm.get_parameter(Name=SSM_LOG_GROUP), "ParameterNotFound")
    if p:
        log_group = p["Parameter"]["Value"]
        filters = logs.describe_subscription_filters(logGroupName=log_group).get("subscriptionFilters", [])
        ours = [f for f in filters if f.get("filterName") == SUB_FILTER_NAME]
        print(f"  Subscription     : {'OK on ' + log_group if ours else 'missing on ' + log_group}")
    else:
        print("  Subscription     : log group SSM not set")

    rule = _exists(lambda: events.describe_rule(Name=RESET_RULE), "ResourceNotFoundException")
    print(f"  Monthly reset    : {'OK ' + rule['ScheduleExpression'] if rule else 'missing'}")

    p = _exists(lambda: ssm.get_parameter(Name=SSM_DEFAULT_BUDGET), "ParameterNotFound")
    print(f"  Default budget   : ${p['Parameter']['Value']}/mo" if p else "  Default budget   : not set (enforcement disabled)")

    print()


def teardown(ddb, iam, lam, logs, events, ssm, acct: str):
    log.info("Tearing down enforcement resources")

    # Subscription filter
    p = _exists(lambda: ssm.get_parameter(Name=SSM_LOG_GROUP), "ParameterNotFound")
    if p:
        try:
            logs.delete_subscription_filter(
                logGroupName=p["Parameter"]["Value"], filterName=SUB_FILTER_NAME
            )
            log.info("Deleted subscription filter on %s", p["Parameter"]["Value"])
        except ClientError as exc:
            log.warning("Subscription filter cleanup: %s", exc)

    # EventBridge rule + targets
    try:
        events.remove_targets(Rule=RESET_RULE, Ids=["token-cop-reset"])
    except ClientError:
        pass
    try:
        events.delete_rule(Name=RESET_RULE)
        log.info("Deleted EventBridge rule: %s", RESET_RULE)
    except ClientError as exc:
        log.warning("EventBridge rule cleanup: %s", exc)

    # Lambdas
    for fn in (METER_FN, RESET_FN):
        try:
            lam.delete_function(FunctionName=fn)
            log.info("Deleted Lambda: %s", fn)
        except ClientError:
            pass

    # IAM roles (detach managed policies first)
    for role_name in (METER_ROLE, RESET_ROLE):
        try:
            for ap in iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", []):
                iam.detach_role_policy(RoleName=role_name, PolicyArn=ap["PolicyArn"])
            for ip in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
                iam.delete_role_policy(RoleName=role_name, PolicyName=ip)
            iam.delete_role(RoleName=role_name)
            log.info("Deleted IAM role: %s", role_name)
        except ClientError:
            pass

    # Deny policy — refuse to delete if still attached, since that would
    # silently unblock denied users mid-month.
    arn = f"arn:aws:iam::{acct}:policy/{DENY_POLICY_NAME}"
    try:
        attached = iam.list_entities_for_policy(PolicyArn=arn)
        in_use = (
            attached.get("PolicyUsers")
            or attached.get("PolicyRoles")
            or attached.get("PolicyGroups")
        )
        if in_use:
            log.warning(
                "Deny policy %s is still attached to %d entities — leaving in place. "
                "Detach manually or run --reset-denied first.",
                arn, sum(len(v) for v in attached.values() if isinstance(v, list)),
            )
        else:
            iam.delete_policy(PolicyArn=arn)
            log.info("Deleted deny policy: %s", arn)
    except ClientError:
        pass

    # DynamoDB
    try:
        ddb.delete_table(TableName=TABLE_NAME)
        log.info("Deleted DynamoDB table: %s", TABLE_NAME)
    except ClientError:
        pass

    # SSM
    for name in (SSM_TABLE, SSM_DENY_POLICY_ARN, SSM_DEFAULT_BUDGET, SSM_LOG_GROUP):
        try:
            ssm.delete_parameter(Name=name)
        except ClientError:
            pass

    log.info("Teardown complete")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Token Cop budget enforcement setup")
    parser.add_argument("--enable", action="store_true",
                        help="Explicit opt-in to provision resources")
    parser.add_argument("--status", action="store_true", help="Show current state")
    parser.add_argument("--teardown", action="store_true", help="Remove all enforcement resources")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without making changes")
    parser.add_argument("--log-group", default=DEFAULT_LOG_GROUP,
                        help=f"Bedrock invocation log group (default: {DEFAULT_LOG_GROUP})")
    parser.add_argument("--default-budget-usd", type=float, default=DEFAULT_BUDGET_USD,
                        help=f"Default monthly budget per principal (default: {DEFAULT_BUDGET_USD})")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)

    sts = boto3.client("sts", region_name=REGION)
    ddb = boto3.client("dynamodb", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    logs = boto3.client("logs", region_name=REGION)
    events = boto3.client("events", region_name=REGION)
    ssm = boto3.client("ssm", region_name=REGION)
    acct = account_id(sts)

    if args.status:
        show_status(ddb, iam, lam, logs, events, ssm, acct)
        return

    if args.teardown:
        teardown(ddb, iam, lam, logs, events, ssm, acct)
        return

    # Opt-in gate. Both flag and env var work; either is sufficient.
    enabled = args.enable or os.environ.get("TOKEN_COP_ENFORCEMENT_ENABLED", "").lower() in ("1", "true", "yes")
    if not enabled:
        print("Enforcement is OPT-IN. Pass --enable or set TOKEN_COP_ENFORCEMENT_ENABLED=1.")
        print("Showing current status:\n")
        show_status(ddb, iam, lam, logs, events, ssm, acct)
        return

    log.info("Provisioning Token Cop enforcement (account=%s region=%s)", acct, REGION)

    table_arn = ensure_table(ddb, args.dry_run)
    deny_arn = ensure_deny_policy(iam, acct, args.dry_run)
    meter_role_arn = ensure_role(
        iam, METER_ROLE, meter_role_inline_policy(table_arn, deny_arn), args.dry_run,
    )
    reset_role_arn = ensure_role(
        iam, RESET_ROLE, reset_role_inline_policy(table_arn, deny_arn), args.dry_run,
    )

    meter_env = {
        "TABLE_NAME": TABLE_NAME,
        "DENY_POLICY_ARN": deny_arn,
        "DEFAULT_BUDGET_USD": str(args.default_budget_usd),
    }
    reset_env = {"TABLE_NAME": TABLE_NAME, "DENY_POLICY_ARN": deny_arn}

    meter_arn = ensure_lambda(lam, METER_FN, meter_role_arn, "token_meter.py", meter_env, args.dry_run)
    reset_arn = ensure_lambda(lam, RESET_FN, reset_role_arn, "budget_reset.py", reset_env, args.dry_run)

    ensure_subscription_filter(logs, lam, args.log_group, meter_arn, METER_FN, args.dry_run)
    ensure_monthly_reset(events, lam, reset_arn, RESET_FN, args.dry_run)

    write_ssm(ssm, SSM_TABLE, TABLE_NAME, args.dry_run)
    write_ssm(ssm, SSM_DENY_POLICY_ARN, deny_arn, args.dry_run)
    write_ssm(ssm, SSM_DEFAULT_BUDGET, str(args.default_budget_usd), args.dry_run)
    write_ssm(ssm, SSM_LOG_GROUP, args.log_group, args.dry_run)

    log.info("Enforcement provisioned. Default budget: $%.2f/mo", args.default_budget_usd)
    log.info("Override per-principal: aws dynamodb put-item --table-name %s ...", TABLE_NAME)


if __name__ == "__main__":
    main()
