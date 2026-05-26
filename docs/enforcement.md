# Token Cop — Bedrock Budget Enforcement (Option 3)

Hard-cap monthly Bedrock spend per IAM principal. Near-real-time (1–5 min
latency). Built on top of the cost attribution feature already used by
Token Cop's reporting tools.

This is an **opt-in** module. Token Cop core works without it.

## What it does

1. A CloudWatch Logs subscription on the Bedrock invocation log group
   forwards every invocation record to a meter Lambda.
2. The meter atomically increments per-principal monthly usage in
   DynamoDB and computes running cost.
3. When a principal crosses their monthly budget, the meter attaches the
   `TokenCopBedrockBudgetDeny` managed policy to that user/role.
   Subsequent `bedrock:InvokeModel*` and `bedrock:Converse*` calls
   return `AccessDenied`.
4. On the 1st of each month at 00:05 UTC, an EventBridge schedule
   triggers a reset Lambda that detaches the deny policy from every
   blocked principal and clears the deny markers.

Architecture diagram:

```
Claude Code (dev's IAM creds)
        │  SigV4
        ▼
  Bedrock InvokeModel ─────► CloudWatch Logs (/aws/bedrock/invocations)
                                       │
                                       ▼ subscription filter
                            token-cop-enforcement-meter (Lambda)
                                       │
                                       ▼
                            DynamoDB: token-cop-enforcement-usage
                                  PK: principal_arn
                                  SK: "YYYY-MM" | "budget" | "denied"
                                       │
                          if cost_usd > budget AND not already denied
                                       │
                                       ▼
                            iam.AttachUser/RolePolicy(TokenCopBedrockBudgetDeny)
```

## Prerequisites

1. Bedrock model invocation logging enabled and writing to **CloudWatch
   Logs** (not just S3). The default group is `/aws/bedrock/invocations`,
   override with `--log-group`. Each record must contain `identity.arn`
   — this is on by default after the April 17, 2026 granular cost
   attribution feature.
2. The IAM principal running the setup script must be able to create
   DynamoDB tables, IAM policies/roles, Lambda functions, EventBridge
   rules, CloudWatch Logs subscription filters, and SSM parameters.
3. Per-principal IAM identity in the logs. Multi-tenant gateway setups
   need session tags — see `docs/cost-attribution.md`.

## Setup

```bash
source .venv/bin/activate

# 1. Preview what will be created.
python -m scripts.setup_enforcement --dry-run --enable

# 2. Provision (default budget: $200/principal/month).
python -m scripts.setup_enforcement --enable

# 3. Or with a custom default budget and log group.
python -m scripts.setup_enforcement --enable \
    --default-budget-usd 500 \
    --log-group /aws/bedrock/invocations

# 4. Inspect.
python -m scripts.setup_enforcement --status
```

The script is idempotent — re-running with `--enable` updates Lambda
code and refreshes role inline policies but keeps DDB data intact.

## Usage

Use the agent tools (added to Token Cop automatically):

- `enforcement_status()` — global state.
- `enforcement_status(principal="arn:aws:iam::...user/alice")` —
  per-principal current spend, budget, and denied flag.
- `set_principal_budget(principal, monthly_usd)` — override the default
  budget for a specific principal.
- `list_denied_principals()` — currently blocked principals.

Or query DynamoDB directly:

```bash
aws dynamodb get-item \
  --table-name token-cop-enforcement-usage \
  --key '{"principal_arn":{"S":"arn:aws:iam::...user/alice"},"sk":{"S":"2026-05"}}'
```

## Caveats

- **Lag**: 1–5 minutes between an invocation and the deny attachment.
  A determined user can burn ~$10–50 past the cap depending on model.
  For a true zero-overage hard cap, use a proxy in front of Bedrock
  (option 4 in `<notes>.md`).
- **IAM consistency**: deny policy attachment may take ~30 seconds to
  propagate.
- **Assumed roles**: principals that arrive as
  `arn:aws:sts::ACCT:assumed-role/RoleName/session-name` are metered
  per role, not per session. If multiple users share a role, the budget
  is shared. Use distinct roles or session tags for per-user budgets.
- **No partial unblock UI**: there is no self-service raise flow. To
  unblock a principal mid-month, manually detach the deny policy:

  ```bash
  aws iam detach-user-policy \
    --user-name alice \
    --policy-arn arn:aws:iam::ACCT:policy/TokenCopBedrockBudgetDeny
  aws dynamodb delete-item \
    --table-name token-cop-enforcement-usage \
    --key '{"principal_arn":{"S":"arn:aws:iam::...user/alice"},"sk":{"S":"denied"}}'
  ```

## Teardown

```bash
python -m scripts.setup_enforcement --teardown
```

Refuses to delete the deny policy if it's still attached anywhere — run
the manual detach commands above first or wait for the next monthly
reset.

## Files

- `scripts/setup_enforcement.py` — provisioning + status + teardown
- `scripts/lambda/token_meter.py` — per-invocation meter + deny attacher
- `scripts/lambda/budget_reset.py` — monthly detach + clear markers
- `tools/enforcement.py` — agent-facing tools (status, set budget, list denied)
