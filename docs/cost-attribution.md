# Bedrock Cost Attribution

## Overview

On **April 17, 2026**, AWS launched **granular cost attribution for
Amazon Bedrock**. Every inference request now records the IAM principal
that made the call, and any tags on that principal flow through to the
bill. This lets teams answer questions that used to require
home-grown accounting: *who is spending what, on which model, for which
project?*

Token Cop integrates with this feature. Once you enable it in the payer
account, Token Cop's `attribution_breakdown` tool queries AWS Cost
Explorer and slices Bedrock spend by IAM principal, cost-allocation
tag, usage type, or linked account — no S3/Athena pipeline required.

**Feature surface in AWS:**

- `line_item_iam_principal` column in CUR 2.0 exports (IAM ARN that
  made the Bedrock call)
- `iamPrincipal/<tag>` cost-allocation tags — any tag on the IAM
  identity is copied into billing records
- Per-principal / per-role / per-team / per-session granularity across
  `InvokeModel`, `Converse`, and Chat Completions APIs

Reference: [Using IAM principal for cost allocation](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/iam-principal-cost-allocation.html)
and [IAM principal attribution](https://docs.aws.amazon.com/bedrock/latest/userguide/cost-mgmt-iam-principal-tracking.html).

## How to Enable

One helper script does the whole setup. Run it in the **payer account**
(the account that owns the consolidated bill):

```bash
source .venv/bin/activate

# Dry run first - prints planned actions, changes nothing
python -m scripts.enable_cur_attribution \
    --dry-run --bucket my-billing-bucket

# Real run
python -m scripts.enable_cur_attribution \
    --bucket my-billing-bucket \
    --prefix token-cop-cur
```

The script is idempotent and performs three steps:

1. **CUR 2.0 data export.** If one already exists, it's updated with
   `INCLUDE_IAM_PRINCIPAL_DATA=TRUE`. If not, a new export named
   `token-cop-cur-2-export` is created against the supplied S3 bucket.
2. **Cost-allocation tag activation.** Every `iamPrincipal/*` tag that
   already shows up in your account is flipped to `Active` via
   `ce:UpdateCostAllocationTagsStatus`.
3. **Summary printout.** The current state of the export and activated
   tags is printed for verification.

After the real run:

- **Wait 24–48 hours.** Tag activation is eventually consistent, and
  the first CUR 2.0 delivery can take up to 24 hours. This is a normal
  AWS Billing cadence, not a Token Cop limitation.
- **Confirm in AWS Billing → Cost allocation tags.** `iamPrincipal/*`
  entries should be listed as `Active`.
- **Tag your IAM identities.** Apply tags like `team=ml-research`,
  `project=chatbot-v2`, or `environment=prod` on each IAM user / role
  that calls Bedrock. The tags flow into CUR + Cost Explorer the next
  billing cycle.

Inspect state any time:

```bash
python -m scripts.enable_cur_attribution --status
```

If you only want to re-sync tag activation (export already configured
manually):

```bash
python -m scripts.enable_cur_attribution --tags-only
```

## What Token Cop Supports

Once attribution data is flowing, these Token Cop surfaces light up:

| Surface | What it does |
|---------|--------------|
| `attribution_breakdown` tool | Groups Bedrock cost by `principal`, `tag:<key>`, `usage_type`, or `account` via Cost Explorer |
| `analyze_invocation_logs` tool | Now extracts `iam_principal` + `inference_profile` per invocation from S3 logs |
| `check_budget` tool | Accepts `principal=` or `tag_filter=` for scoped burn-rate checks |
| Streamlit **Per-User** tab | Real IAM-principal grouping (no more `session_id` heuristics) |
| Weekly report (`generate_report.py`) | Top-N principal breakdown + per-team rollup |
| Eval regression suite | New test cases that assert attribution-shaped answers |

### Example prompts

With attribution enabled, Token Cop can answer:

```text
/tokcop break down Bedrock spend by IAM principal last 7 days
/tokcop how much did team=ml-research spend on Claude Sonnet this month?
/tokcop is user alice on track for her $200 monthly budget?
/tokcop which role is the top Bedrock spender?
```

## CUR 2.0 vs Cost Explorer — Why We Chose Cost Explorer

Two paths exist to consume the new attribution data:

| Path | Latency | Setup | Querying | Why Token Cop uses it |
|------|---------|-------|----------|----------------------|
| **Cost Explorer** (`ce:GetCostAndUsage`) | ~24h lag | Zero beyond tag activation | `GroupBy=TAG` / `DIMENSION` | Simple, low-ceremony, works from the agent runtime |
| **CUR 2.0 parquet + Athena/Glue** | ~24h lag | S3 bucket + Athena workgroup + schema | SQL | Deferred — too much infra for a baseline integration |

**Trade-off we accepted:** Cost Explorer caps out at ~14 months of
history and has granularity limits (`HOURLY` for recent ranges,
`DAILY`/`MONTHLY` for longer ranges). For sub-hourly attribution or
large historical joins, operators can point `attribution_breakdown` at
a CUR 2.0 parquet reader in a future iteration — the schema fields are
already wired up.

## Gateway / Multi-Tenant Caveat

If your team runs a **shared gateway** in front of Bedrock (one IAM
role that serves many end-users), IAM-principal attribution out of the
box will only show the gateway's role. AWS recommends two patterns for
attributing further:

1. **Session tags via `sts:AssumeRole`.** The gateway assumes a
   per-request role and sets session tags (e.g. `username=alice`,
   `team=ml`). Those tags flow into CUR via the `iamPrincipal/*` tag
   prefix just like static identity tags.
2. **Per-user IAM identities.** Give each caller their own IAM user or
   role and let the inference call run under those credentials.

**Known limits to plan around:**

- **STS AssumeRole rate limit**: ~500 calls/sec per role per account.
  A high-QPS gateway should cache assumed credentials (they're valid
  up to 1 hour) rather than assuming per request.
- **Session-tag immutability**: once a set of session tags is attached
  via `AssumeRole`, you cannot change them for the duration of those
  temporary credentials. Plan your tag schema up front.
- **Temporary-credential TTL**: 15 min – 12 hr (default 1 hr). Rotate
  before expiry.

**Token Cop itself is observability-only.** It reads usage data — it
does not proxy Bedrock calls and is not a gateway. If your architecture
needs a shared gateway, build it separately and ensure the AssumeRole
pattern above is applied; Token Cop will read the resulting
attribution tags out of Cost Explorer automatically.

## IAM Permissions Summary

Attach these to the **runtime role** (AgentCore execution role) that
runs Token Cop:

| Permission | Used by | Purpose |
|------------|---------|---------|
| `ce:GetCostAndUsage` | `attribution_breakdown` | Core Cost Explorer query |
| `ce:GetDimensionValues` | `attribution_breakdown` | Populate valid `usage_type`/`account` keys |
| `ce:GetTags` | `attribution_breakdown` | Enumerate tag keys for `tag:<key>` dimension |
| `cloudwatch:GetMetricData` | `bedrock_usage` | Existing — CloudWatch Bedrock metrics |
| `s3:ListBucket` + `s3:GetObject` | `analyze_invocation_logs` | Existing — Bedrock invocation log reader |

Additional permissions required **only when running the setup script**
(attach to the user or role executing `enable_cur_attribution.py`, not
the agent runtime):

| Permission | Used by | Purpose |
|------------|---------|---------|
| `bcm-data-exports:ListExports` | setup script | Find existing CUR 2.0 exports |
| `bcm-data-exports:GetExport` | setup script | Inspect existing export config |
| `bcm-data-exports:CreateExport` | setup script | Create new CUR 2.0 export |
| `bcm-data-exports:UpdateExport` | setup script | Add `INCLUDE_IAM_PRINCIPAL_DATA=TRUE` |
| `ce:UpdateCostAllocationTagsStatus` | setup script | Activate `iamPrincipal/*` tags |
| `ce:ListCostAllocationTags` | setup script | Discover which tags to activate |
| `sts:GetCallerIdentity` | setup script | Display account in logs |
| `iam:ListAccountAliases` | setup script | Optional — pretty-prints account name |
| `s3:PutBucketPolicy` | operator | One-time — allow `billingreports.amazonaws.com` to write to the CUR bucket |

## Troubleshooting

### No tag data appears in Cost Explorer

- **Expected for 24–48 hours after enabling.** Tag activation is
  eventually consistent and the first CUR 2.0 delivery lags a full
  billing cycle.
- Check `python -m scripts.enable_cur_attribution --status`. The
  `iamPrincipal/*` tags should be present and `Active`.
- Verify at least one IAM identity actually has tags applied. Untagged
  principals still show up via `line_item_iam_principal` but do not
  populate tag-based groupings.

### `AccessDeniedException` when calling `attribution_breakdown`

The runtime role is missing Cost Explorer permissions. Attach
`ce:GetCostAndUsage`, `ce:GetDimensionValues`, and `ce:GetTags`.

### `ValidationException: Grouping by TAG 'aws:PrincipalArn' is not supported`

CUR 2.0 IAM principal data is not yet enabled, or the activation has
not propagated. Re-run `enable_cur_attribution.py` and wait 24 hours.

### `Linked account doesn't have access to cost allocation tags`

You're running the setup script in a **member account** of an AWS
Organization. Cost allocation tags are managed at the **payer account**
only. Log in to the payer account and re-run there. The script itself
will still exit cleanly and continue with the CUR 2.0 steps.

### CUR 2.0 export fails to deliver to S3

Most common causes:

- S3 bucket policy is missing the
  `billingreports.amazonaws.com` grant. See the AWS
  [CUR 2.0 bucket policy docs](https://docs.aws.amazon.com/cur/latest/userguide/cur-s3.html).
- Bucket is in a different region than the export. Use `--prefix` to
  customize the S3 path, but keep the bucket in the export region.

### Principal ARNs in output look scrubbed

Intentional. `tools/attribution.py` routes principal ARNs through
`models/normalization.normalize_principal_arn`, which hides the
account ID so committed sample responses and regression fixtures don't
leak it. The underlying Cost Explorer API returns the full ARN.

### Gateway-fronted traffic all attributes to one role

Expected — the gateway is the IAM principal. Add session tags via
`AssumeRole` (see *Gateway / Multi-Tenant Caveat* above) to get
per-user attribution.

## Related Docs

- [`docs/policies.md`](policies.md) — Cedar policies at the gateway
- [`docs/evaluations.md`](evaluations.md) — Evaluating attribution answers
- [`docs/mcp-gateway.md`](mcp-gateway.md) — Gateway architecture
- [AWS blog: Granular cost attribution for Bedrock (2026-04-17)](https://aws.amazon.com/blogs/aws-cost-management/)
