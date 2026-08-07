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

<div align="center">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 640" font-family="-apple-system, Segoe UI, Roboto, sans-serif" font-size="13" role="img" aria-label="Reactive budget enforcement flow: Bedrock invocations are logged, metered per principal in DynamoDB, and once a principal is over budget an IAM deny policy is attached so their NEXT request is blocked by IAM. A monthly schedule resets the block.">
  <rect x="0" y="0" width="900" height="640" fill="#ffffff"/>
  <defs>
    <marker id="ea" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L7,3 L0,6 Z" fill="#334155"/>
    </marker>
    <marker id="ea-red" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L7,3 L0,6 Z" fill="#dc2626"/>
    </marker>
  </defs>

  <!-- Client -->
  <rect x="30" y="40" width="180" height="70" rx="8" fill="#eef2ff" stroke="#4f46e5" stroke-width="1.5"/>
  <text x="120" y="66" text-anchor="middle" font-weight="700" fill="#3730a3">Client</text>
  <text x="120" y="86" text-anchor="middle" fill="#1e293b" font-size="12">Claude Code / SDK</text>
  <text x="120" y="102" text-anchor="middle" fill="#64748b" font-size="11">dev's IAM creds (SigV4)</text>

  <!-- Bedrock -->
  <rect x="30" y="160" width="180" height="70" rx="8" fill="#fef3f2" stroke="#e11d48" stroke-width="1.5"/>
  <text x="120" y="186" text-anchor="middle" font-weight="700" fill="#9f1239">Bedrock</text>
  <text x="120" y="206" text-anchor="middle" fill="#1e293b" font-size="12">InvokeModel / Converse</text>
  <text x="120" y="222" text-anchor="middle" fill="#64748b" font-size="11">request COMPLETES + is billed</text>

  <!-- IAM gate -->
  <rect x="30" y="300" width="180" height="86" rx="8" fill="#fffbeb" stroke="#d97706" stroke-width="2"/>
  <text x="120" y="326" text-anchor="middle" font-weight="700" fill="#92400e">IAM authorization</text>
  <text x="120" y="346" text-anchor="middle" fill="#1e293b" font-size="11">checked on EVERY call</text>
  <text x="120" y="364" text-anchor="middle" fill="#b45309" font-size="11">deny policy attached?</text>
  <text x="120" y="379" text-anchor="middle" fill="#dc2626" font-size="11" font-weight="600">→ AccessDenied</text>

  <!-- CloudWatch Logs -->
  <rect x="330" y="160" width="200" height="70" rx="8" fill="#ecfeff" stroke="#0891b2" stroke-width="1.5"/>
  <text x="430" y="186" text-anchor="middle" font-weight="700" fill="#155e75">CloudWatch Logs</text>
  <text x="430" y="206" text-anchor="middle" fill="#1e293b" font-size="12">/aws/bedrock/invocations</text>
  <text x="430" y="222" text-anchor="middle" fill="#64748b" font-size="11">subscription filter</text>

  <!-- Meter Lambda -->
  <rect x="330" y="290" width="200" height="86" rx="8" fill="#f0fdf4" stroke="#16a34a" stroke-width="1.5"/>
  <text x="430" y="314" text-anchor="middle" font-weight="700" fill="#166534">Meter Lambda</text>
  <text x="430" y="333" text-anchor="middle" fill="#1e293b" font-size="11">parse identity.arn, tokens, model</text>
  <text x="430" y="349" text-anchor="middle" fill="#1e293b" font-size="11">compute cost (inline pricing)</text>
  <text x="430" y="365" text-anchor="middle" fill="#1e293b" font-size="11">atomic ADD cost_usd</text>

  <!-- DynamoDB -->
  <rect x="330" y="430" width="200" height="86" rx="8" fill="#f5f3ff" stroke="#7c3aed" stroke-width="1.5"/>
  <text x="430" y="454" text-anchor="middle" font-weight="700" fill="#5b21b6">DynamoDB usage table</text>
  <text x="430" y="473" text-anchor="middle" fill="#1e293b" font-size="11">PK principal_arn</text>
  <text x="430" y="489" text-anchor="middle" fill="#1e293b" font-size="11">SK "YYYY-MM" | "budget" | "denied"</text>
  <text x="430" y="505" text-anchor="middle" fill="#64748b" font-size="11">returns new running cost</text>

  <!-- Decision -->
  <polygon points="640,333 720,373 640,413 560,373" fill="#fef9c3" stroke="#ca8a04" stroke-width="1.5"/>
  <text x="640" y="368" text-anchor="middle" fill="#713f12" font-size="11">cost &gt; budget</text>
  <text x="640" y="383" text-anchor="middle" fill="#713f12" font-size="11">&amp; not denied?</text>

  <!-- Attach deny -->
  <rect x="740" y="330" width="140" height="86" rx="8" fill="#fef2f2" stroke="#dc2626" stroke-width="2"/>
  <text x="810" y="352" text-anchor="middle" font-weight="700" fill="#991b1b" font-size="12">Attach deny</text>
  <text x="810" y="370" text-anchor="middle" fill="#1e293b" font-size="11">iam.Attach</text>
  <text x="810" y="385" text-anchor="middle" fill="#1e293b" font-size="11">User/RolePolicy</text>
  <text x="810" y="402" text-anchor="middle" fill="#64748b" font-size="10">+ write "denied"</text>

  <!-- Reset Lambda -->
  <rect x="740" y="470" width="140" height="86" rx="8" fill="#f1f5f9" stroke="#64748b" stroke-width="1.5"/>
  <text x="810" y="494" text-anchor="middle" font-weight="700" fill="#334155" font-size="12">Reset Lambda</text>
  <text x="810" y="512" text-anchor="middle" fill="#1e293b" font-size="11">EventBridge</text>
  <text x="810" y="527" text-anchor="middle" fill="#1e293b" font-size="11">cron 1st 00:05 UTC</text>
  <text x="810" y="544" text-anchor="middle" fill="#64748b" font-size="10">detach + clear markers</text>

  <!-- Arrows: client -> bedrock -->
  <line x1="120" y1="110" x2="120" y2="158" stroke="#334155" stroke-width="1.8" marker-end="url(#ea)"/>
  <!-- IAM gate governs the client's calls (dashed, the enforcement point) -->
  <line x1="120" y1="300" x2="120" y2="232" stroke="#dc2626" stroke-width="1.6" stroke-dasharray="5,4" marker-end="url(#ea-red)"/>
  <text x="150" y="268" text-anchor="start" fill="#dc2626" font-size="10">blocks NEXT call</text>

  <!-- bedrock -> CW logs -->
  <line x1="210" y1="195" x2="328" y2="195" stroke="#334155" stroke-width="1.8" marker-end="url(#ea)"/>
  <text x="269" y="186" text-anchor="middle" fill="#64748b" font-size="10">logged</text>
  <!-- CW logs -> meter -->
  <line x1="430" y1="230" x2="430" y2="288" stroke="#334155" stroke-width="1.8" marker-end="url(#ea)"/>
  <!-- meter <-> ddb -->
  <line x1="430" y1="376" x2="430" y2="428" stroke="#334155" stroke-width="1.8" marker-end="url(#ea)"/>
  <!-- ddb -> decision (running cost) -->
  <line x1="530" y1="460" x2="600" y2="400" stroke="#334155" stroke-width="1.6" marker-end="url(#ea)"/>
  <!-- meter -> decision -->
  <line x1="530" y1="345" x2="576" y2="357" stroke="#334155" stroke-width="1.6" marker-end="url(#ea)"/>
  <!-- decision -> attach (yes) -->
  <line x1="720" y1="373" x2="738" y2="373" stroke="#dc2626" stroke-width="1.8" marker-end="url(#ea-red)"/>
  <text x="729" y="364" text-anchor="middle" fill="#dc2626" font-size="10">yes</text>
  <!-- attach -> IAM (the deny lands on the principal) -->
  <path d="M740,395 C400,600 240,560 130,388" fill="none" stroke="#dc2626" stroke-width="1.6" stroke-dasharray="5,4" marker-end="url(#ea-red)"/>
  <text x="430" y="575" text-anchor="middle" fill="#dc2626" font-size="10">deny policy now on principal → IAM rejects future Bedrock calls</text>
  <!-- reset -> attach (undo) -->
  <line x1="810" y1="470" x2="810" y2="418" stroke="#64748b" stroke-width="1.6" stroke-dasharray="4,4" marker-end="url(#ea)"/>
  <text x="880" y="445" text-anchor="end" fill="#64748b" font-size="10">monthly undo</text>

  <!-- Lag callout -->
  <rect x="560" y="150" width="320" height="52" rx="6" fill="#fffbeb" stroke="#d97706" stroke-width="1.2"/>
  <text x="720" y="170" text-anchor="middle" fill="#92400e" font-size="11" font-weight="600">~1–5 min lag: log delivery → subscription →</text>
  <text x="720" y="187" text-anchor="middle" fill="#92400e" font-size="11">Lambda → IAM propagation. Soft cap, not real-time.</text>
</svg>
</div>

**Reading the diagram — the key property:** the request that crosses the budget
**completes and is billed** (solid path, top-left). Enforcement is reactive: the
invocation is logged, metered into DynamoDB, and only if the running cost is now over
budget does the meter attach the deny policy. That policy blocks the principal's
**next** Bedrock call at the **IAM authorization layer** (dashed red) — Token Cop is
never in the request path. The `~1–5 min` pipeline lag makes this a **soft cap that
catches up**, not a hard real-time gate; a burst within that window gets through. The
`"denied"` marker short-circuits repeat IAM calls, and the monthly reset Lambda
detaches the policy to reopen access.

Compact ASCII view:

```
 Client (dev IAM creds)                          [1] request happens
     │ SigV4
     ▼
 Bedrock InvokeModel ── completes & is BILLED ──► CloudWatch Logs
     ▲                                                  │ subscription (~1–5 min)
     │ [4] IAM denies NEXT call                         ▼
 IAM authorization ◄───┐                        Meter Lambda
   (deny policy?)       │                          │ atomic ADD cost_usd
                        │                          ▼
                        │                     DynamoDB usage  ── running cost ─┐
                        │                     PK principal_arn                 │
                        │  [3] attach deny                                     ▼
                        └────────────────────  cost > budget & not denied?  ──┘
                           iam.Attach*Policy         │ yes
                           (+ "denied" marker)       ▼
                                              [reset Lambda: 1st of month → detach]
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
