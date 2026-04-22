"""Enable CUR 2.0 + IAM-principal cost attribution for Token Cop.

One-shot, idempotent helper that prepares AWS Billing so Token Cop's
`attribution_breakdown` tool can pull per-principal / per-tag Bedrock
spend from Cost Explorer.

It performs three independent steps:

1. Ensure a CUR 2.0 data export exists with the
   `INCLUDE_IAM_PRINCIPAL_DATA = TRUE` table property. If an existing
   export is found with the property missing, it is updated in place.
   Otherwise a new export named `token-cop-cur-2-export` is created.
2. Activate any `iamPrincipal/*` cost allocation tags that appear in the
   account via `ce.update_cost_allocation_tags_status`. Tag activation
   is eventually consistent and can take 24-48 hours to surface.
3. Print the principal attribution summary so the operator knows what
   to expect next.

Usage:
    source .venv/bin/activate

    # Dry run - print planned actions, change nothing
    python -m scripts.enable_cur_attribution \\
        --dry-run --bucket token-cop-cur-example

    # Real run - create export + activate tags
    python -m scripts.enable_cur_attribution \\
        --bucket my-billing-bucket --prefix token-cop-cur

    # Activation only (skip CUR 2.0 setup, assumes export exists)
    python -m scripts.enable_cur_attribution --tags-only

    # Inspect current state
    python -m scripts.enable_cur_attribution --status

IAM permissions required to run:
    bcm-data-exports:ListExports, GetExport, CreateExport, UpdateExport
    ce:UpdateCostAllocationTagsStatus, ce:ListCostAllocationTags
    sts:GetCallerIdentity
    iam:GetAccountAlias (optional, for display)
"""
import argparse
import logging
import sys

import boto3
from botocore.exceptions import ClientError

from agent.config import AWS_REGION

DEFAULT_EXPORT_NAME = "token-cop-cur-2-export"
DEFAULT_S3_PREFIX = "token-cop-cur"

# CUR 2.0 table property that populates the `line_item_iam_principal`
# column with the IAM ARN that made the Bedrock call. This is the
# critical switch for per-principal attribution.
IAM_PRINCIPAL_PROPERTY = "INCLUDE_IAM_PRINCIPAL_DATA"
CUR_TABLE_NAME = "COST_AND_USAGE_REPORT"

# Canonical CUR 2.0 query: SELECT *. create_export/update_export accept
# a simple `SELECT * FROM COST_AND_USAGE_REPORT` statement.
CUR_QUERY_STATEMENT = f"SELECT * FROM {CUR_TABLE_NAME}"

# Cost allocation tag key prefix that AWS emits for IAM principal tags
# once INCLUDE_IAM_PRINCIPAL_DATA is active and tags exist on the
# principal. We activate every tag key under this prefix.
IAM_PRINCIPAL_TAG_PREFIX = "iamPrincipal/"

log = logging.getLogger("enable_cur_attribution")


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    for noisy in ("botocore", "urllib3", "boto3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------- Account helpers --------------------------------------------------


def account_id() -> str:
    return boto3.client("sts", region_name=AWS_REGION).get_caller_identity()["Account"]


def account_display(acct: str) -> str:
    """Return "alias (acct)" if an IAM account alias exists, else just acct."""
    try:
        iam = boto3.client("iam", region_name=AWS_REGION)
        aliases = iam.list_account_aliases().get("AccountAliases", [])
        if aliases:
            return f"{aliases[0]} ({acct})"
    except ClientError:
        pass
    return acct


# ---------- CUR 2.0 export helpers -------------------------------------------


def list_exports(client) -> list[dict]:
    """Return full export objects for every data export in the account."""
    exports: list[dict] = []
    paginator = client.get_paginator("list_exports")
    for page in paginator.paginate():
        for ref in page.get("Exports", []):
            # ListExports returns light refs; fetch the full record so we
            # can inspect TableConfigurations.
            try:
                full = client.get_export(ExportArn=ref["ExportArn"])["Export"]
                exports.append(full)
            except ClientError as exc:
                log.warning("Could not fetch export %s: %s", ref.get("ExportArn"), exc)
    return exports


def has_iam_principal_property(export: dict) -> bool:
    """Return True if the export's CUR 2.0 table has INCLUDE_IAM_PRINCIPAL_DATA=TRUE."""
    query = export.get("DataQuery", {}) or {}
    configs = query.get("TableConfigurations", {}) or {}
    cur_props = configs.get(CUR_TABLE_NAME, {}) or {}
    # API values are case-insensitive strings ("TRUE" / "FALSE")
    return str(cur_props.get(IAM_PRINCIPAL_PROPERTY, "")).upper() == "TRUE"


def find_cur2_export(exports: list[dict]) -> dict | None:
    """Return the first export whose query targets COST_AND_USAGE_REPORT."""
    for exp in exports:
        stmt = (exp.get("DataQuery", {}) or {}).get("QueryStatement", "")
        if CUR_TABLE_NAME in stmt.upper():
            return exp
    return None


def build_table_configurations(existing: dict | None) -> dict:
    """Merge INCLUDE_IAM_PRINCIPAL_DATA=TRUE into any existing CUR 2.0 config.

    We preserve whatever other table properties the caller had set
    (TIME_GRANULARITY, INCLUDE_RESOURCES, etc.) so this helper is safe
    to run against a pre-existing export.
    """
    existing = existing or {}
    cur_props = dict(existing.get(CUR_TABLE_NAME, {}) or {})
    cur_props[IAM_PRINCIPAL_PROPERTY] = "TRUE"
    # Sensible defaults when we're creating fresh
    cur_props.setdefault("TIME_GRANULARITY", "HOURLY")
    cur_props.setdefault("INCLUDE_RESOURCES", "TRUE")
    cur_props.setdefault("INCLUDE_SPLIT_COST_ALLOCATION_DATA", "FALSE")
    return {CUR_TABLE_NAME: cur_props}


def build_export_body(name: str, bucket: str, prefix: str, region: str,
                      existing: dict | None = None) -> dict:
    """Build the Export body for create_export / update_export."""
    existing_query = (existing or {}).get("DataQuery", {}) or {}
    existing_tables = existing_query.get("TableConfigurations")

    return {
        "Name": name,
        "Description": "Token Cop: Bedrock IAM principal + tag cost attribution",
        "DataQuery": {
            "QueryStatement": CUR_QUERY_STATEMENT,
            "TableConfigurations": build_table_configurations(existing_tables),
        },
        "DestinationConfigurations": {
            "S3Destination": {
                "S3Bucket": bucket,
                "S3Prefix": prefix,
                "S3Region": region,
                "S3OutputConfigurations": {
                    "OutputType": "CUSTOM",
                    "Format": "PARQUET",
                    "Compression": "PARQUET",
                    "Overwrite": "OVERWRITE_REPORT",
                },
            }
        },
        "RefreshCadence": {"Frequency": "SYNCHRONOUS"},
    }


def ensure_cur2_export(client, bucket: str | None, prefix: str,
                      export_name: str, dry_run: bool) -> str | None:
    """Ensure a CUR 2.0 export exists with INCLUDE_IAM_PRINCIPAL_DATA=TRUE.

    Returns the export ARN (or a placeholder string in dry-run mode).
    """
    existing_exports = list_exports(client)
    existing = find_cur2_export(existing_exports)

    if existing:
        arn = existing["ExportArn"]
        name = existing.get("Name", "?")
        if has_iam_principal_property(existing):
            log.info(
                "CUR 2.0 export '%s' already has %s=TRUE - nothing to do",
                name, IAM_PRINCIPAL_PROPERTY,
            )
            return arn

        log.info(
            "CUR 2.0 export '%s' found but missing %s=TRUE - will update in place",
            name, IAM_PRINCIPAL_PROPERTY,
        )
        body = build_export_body(
            name=name,
            bucket=(existing.get("DestinationConfigurations", {})
                   .get("S3Destination", {}).get("S3Bucket", bucket or "")),
            prefix=(existing.get("DestinationConfigurations", {})
                   .get("S3Destination", {}).get("S3Prefix", prefix)),
            region=(existing.get("DestinationConfigurations", {})
                   .get("S3Destination", {}).get("S3Region", AWS_REGION)),
            existing=existing,
        )
        if dry_run:
            log.info("[dry-run] update_export(ExportArn=%s, ...)", arn)
            return arn

        client.update_export(ExportArn=arn, Export=body)
        log.info("Export updated: %s", arn)
        return arn

    # No CUR 2.0 export exists - create one
    if not bucket:
        log.error(
            "No existing CUR 2.0 export found and --bucket was not supplied. "
            "Re-run with --bucket <name> to create one."
        )
        sys.exit(2)

    log.info(
        "No CUR 2.0 export found - will create '%s' -> s3://%s/%s",
        export_name, bucket, prefix,
    )
    body = build_export_body(
        name=export_name,
        bucket=bucket,
        prefix=prefix,
        region=AWS_REGION,
    )
    if dry_run:
        log.info("[dry-run] create_export(Export=%s)", export_name)
        log.info(
            "[dry-run]   TableConfigurations: %s",
            body["DataQuery"]["TableConfigurations"],
        )
        return None

    resp = client.create_export(Export=body)
    arn = resp["ExportArn"]
    log.info("Export created: %s", arn)
    log.info(
        "Note: first CUR 2.0 delivery can take up to 24 hours. "
        "Ensure the S3 bucket policy grants billingreports.amazonaws.com "
        "PutObject permission (see https://docs.aws.amazon.com/cur/latest/userguide/)."
    )
    return arn


# ---------- Cost allocation tag activation -----------------------------------


def list_iam_principal_tags(ce_client) -> list[dict]:
    """List cost allocation tags whose key starts with iamPrincipal/."""
    results: list[dict] = []
    next_token: str | None = None
    while True:
        kwargs = {"MaxResults": 100}
        if next_token:
            kwargs["NextToken"] = next_token
        try:
            resp = ce_client.list_cost_allocation_tags(**kwargs)
        except ClientError as exc:
            log.warning("list_cost_allocation_tags failed: %s", exc)
            return results
        for tag in resp.get("CostAllocationTags", []):
            if tag.get("TagKey", "").startswith(IAM_PRINCIPAL_TAG_PREFIX):
                results.append(tag)
        next_token = resp.get("NextToken")
        if not next_token:
            break
    return results


def activate_iam_principal_tags(ce_client, dry_run: bool) -> int:
    """Flip every iamPrincipal/* tag to Active. Returns count activated.

    Cost Explorer caps each update_cost_allocation_tags_status call at
    20 entries, so we batch.
    """
    tags = list_iam_principal_tags(ce_client)
    if not tags:
        log.warning(
            "No %s* cost allocation tags found yet. "
            "Tags appear after IAM principals are tagged AND Bedrock usage "
            "has flowed through billing. This is normal for a fresh setup - "
            "re-run this helper in 24-48 hours.",
            IAM_PRINCIPAL_TAG_PREFIX,
        )
        return 0

    pending = [t for t in tags if t.get("Status") != "Active"]
    if not pending:
        log.info(
            "All %d %s* tags are already Active",
            len(tags), IAM_PRINCIPAL_TAG_PREFIX,
        )
        return 0

    log.info(
        "Activating %d %s* cost allocation tag(s)",
        len(pending), IAM_PRINCIPAL_TAG_PREFIX,
    )
    activated = 0
    batch_size = 20
    for i in range(0, len(pending), batch_size):
        batch = pending[i:i + batch_size]
        entries = [{"TagKey": t["TagKey"], "Status": "Active"} for t in batch]
        for entry in entries:
            log.info("  -> %s", entry["TagKey"])
        if dry_run:
            log.info("[dry-run] update_cost_allocation_tags_status(%d entries)", len(entries))
            activated += len(entries)
            continue
        try:
            resp = ce_client.update_cost_allocation_tags_status(
                CostAllocationTagsStatus=entries,
            )
        except ClientError as exc:
            log.error("update_cost_allocation_tags_status failed: %s", exc)
            continue
        errors = resp.get("Errors", []) or []
        if errors:
            for err in errors:
                log.warning(
                    "  FAILED %s: %s (%s)",
                    err.get("TagKey"), err.get("Message"), err.get("Code"),
                )
        activated += len(entries) - len(errors)

    if activated:
        log.warning(
            "Tag activation is eventually consistent - new values may take "
            "24-48 hours to appear in Cost Explorer / CUR 2.0."
        )
    return activated


# ---------- Commands ---------------------------------------------------------


def show_status(exports_client, ce_client):
    acct = account_id()
    print(f"\nAccount: {account_display(acct)}")
    print(f"Region:  {AWS_REGION}\n")

    exports = list_exports(exports_client)
    cur = find_cur2_export(exports)
    if not cur:
        print("CUR 2.0 export: NOT FOUND")
        print("  -> run: python -m scripts.enable_cur_attribution --bucket <name>")
    else:
        dest = cur.get("DestinationConfigurations", {}).get("S3Destination", {})
        flag = "YES" if has_iam_principal_property(cur) else "NO"
        print(f"CUR 2.0 export: {cur.get('Name')}")
        print(f"  ARN:                     {cur.get('ExportArn')}")
        print(f"  Destination:             s3://{dest.get('S3Bucket')}/{dest.get('S3Prefix')}")
        print(f"  IAM principal enabled:   {flag}")

    tags = list_iam_principal_tags(ce_client)
    print(f"\niamPrincipal/* cost allocation tags: {len(tags)}")
    active = [t for t in tags if t.get("Status") == "Active"]
    inactive = [t for t in tags if t.get("Status") != "Active"]
    if tags:
        print(f"  Active:   {len(active)}")
        print(f"  Inactive: {len(inactive)}")
        for t in tags[:10]:
            print(f"    - {t.get('TagKey')} [{t.get('Status')}]")
        if len(tags) > 10:
            print(f"    ... and {len(tags) - 10} more")
    else:
        print("  (none yet - this is normal before the first billing cycle)")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Enable CUR 2.0 IAM principal attribution for Token Cop",
    )
    parser.add_argument(
        "--bucket",
        help="S3 bucket that receives CUR 2.0 deliveries "
             "(required when no export exists yet)",
    )
    parser.add_argument(
        "--prefix", default=DEFAULT_S3_PREFIX,
        help=f"S3 path prefix (default: {DEFAULT_S3_PREFIX})",
    )
    parser.add_argument(
        "--export-name", default=DEFAULT_EXPORT_NAME,
        help=f"Name for a newly-created export (default: {DEFAULT_EXPORT_NAME})",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Print current attribution state and exit",
    )
    parser.add_argument(
        "--tags-only", action="store_true",
        help="Skip CUR 2.0 setup, only activate iamPrincipal/* tags",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print planned actions without making changes",
    )
    args = parser.parse_args()

    setup_logging()

    # Cost Explorer is a global service but boto3 requires a region.
    # The `ce` and `bcm-data-exports` endpoints are both in us-east-1.
    exports_client = boto3.client("bcm-data-exports", region_name="us-east-1")
    ce_client = boto3.client("ce", region_name="us-east-1")

    if args.status:
        show_status(exports_client, ce_client)
        return

    acct = account_id()
    log.info("Target account: %s (%s)", account_display(acct), AWS_REGION)
    if args.dry_run:
        log.info("DRY RUN - no AWS resources will be modified")

    try:
        if not args.tags_only:
            ensure_cur2_export(
                client=exports_client,
                bucket=args.bucket,
                prefix=args.prefix,
                export_name=args.export_name,
                dry_run=args.dry_run,
            )

        activated = activate_iam_principal_tags(ce_client, dry_run=args.dry_run)
    except ClientError as exc:
        log.error("AWS error: %s", exc)
        sys.exit(1)

    log.info("Setup complete.")
    log.info("Next steps:")
    log.info("  - wait 24-48h for tag activation + first CUR 2.0 delivery")
    log.info("  - run: python -m scripts.enable_cur_attribution --status")
    log.info("  - ask Token Cop: 'break down Bedrock spend by IAM principal last 7 days'")
    if activated and not args.dry_run:
        log.info("  - activated %d new tag(s) this run", activated)


if __name__ == "__main__":
    main()
