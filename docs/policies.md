# AgentCore Policies

## Overview

AgentCore Policies provide real-time, deterministic authorization over tool calls
at the MCP Gateway level. They use the [Cedar](https://www.cedarpolicy.com/) policy
language to define who can do what through the gateway.

**Key properties:**
- **Default-deny**: nothing is allowed unless a `permit` policy explicitly allows it
- **Forbid overrides permit**: a `forbid` policy always wins
- **Deterministic**: Cedar evaluates the same way every time (no ML/probabilistic behavior)
- **Complementary to guardrails**: `agent/guardrails.py` scrubs sensitive data from
  output (defense-in-depth); policies control who can call tools in the first place

## Cedar Policy Language

A Cedar policy has three parts:

```cedar
permit|forbid (
    principal,                          -- who (caller identity)
    action,                             -- what (e.g. InvokeTool)
    resource is AgentCore::Gateway      -- must specify resource type
) when {
    context has <attr> && context.<attr> == "value"
};
```

**AgentCore requirements:**
- `resource` must be constrained to `AgentCore::Gateway` (wildcards rejected)
- `context` attributes need a `has` guard before access (Cedar type safety)

**What Cedar can inspect**: caller identity, action type, resource metadata, and
context attributes set by the gateway (e.g. `context.clientId`, `context.toolName`).

**What Cedar cannot inspect**: request payload content (e.g. the prompt text).
For content-level controls, use Bedrock Guardrails or application-level checks.

## Demo Policies

### 1. Permit All (baseline)

```cedar
permit(principal, action, resource is AgentCore::Gateway);
```

Allows all authenticated callers to invoke any tool on the gateway. Since Cedar is
default-deny, you need at least one `permit` policy for anything to work. Use this
during development and testing. The `resource is AgentCore::Gateway` constraint is
required -- AgentCore rejects wildcard resources.

### 2. Cognito Client Only

```cedar
permit(principal, action, resource is AgentCore::Gateway)
when { context has clientId && context.clientId == "29ksjo8k257ev205bvh4fjn4gg" };
```

Only allows calls where the JWT `client_id` claim matches the known Token Cop
Cognito client. The `context has clientId` guard is required by Cedar's type system
before accessing an attribute on untyped context. In production, this ensures only
your authorized application can call the gateway.

### 3. Forbid Demo

```cedar
forbid(principal, action, resource is AgentCore::Gateway)
when { context has toolName && context.toolName == "dangerous_tool" };
```

Blocks invocations of a hypothetical `dangerous_tool`. Demonstrates that `forbid`
always overrides `permit` in Cedar -- even if Policy 1 permits everything, this
policy still blocks the specific tool.

## Gateway Association

Policies are evaluated by a **Policy Engine** (a container for policies) that is
attached to a gateway. The gateway has two enforcement modes:

| Mode | Behavior |
|------|----------|
| `LOG_ONLY` | Evaluates policies and logs decisions, but allows all requests through |
| `ENFORCE` | Blocks unauthorized requests with an error |

Start with `LOG_ONLY` to audit what would be allowed/denied before switching to `ENFORCE`.

### Switching modes

```bash
# Switch to ENFORCE
python -m scripts.setup_policies --mode ENFORCE

# Or via CLI
agentcore policy ...  # (use update_gateway_policy_engine via SDK)
```

## AI-Powered Policy Generation

AgentCore can generate Cedar policies from natural language:

```bash
python -m scripts.setup_policies --generate
```

This calls `generate_policy()` which:
1. Sends a natural language description to the generation service
2. Polls until generation completes
3. Returns one or more Cedar policy statements

You can then review the generated Cedar and create a real policy from it using
`create_policy_from_generation_asset()`.

## Architecture

```
Client ──JWT──> MCP Gateway ──> Policy Engine (Cedar eval) ──> Tool Target
                    │                   │
                    │                   ├─ permit-all
                    │                   ├─ cognito-client-only
                    │                   └─ forbid-demo
                    │
                    └─ LOG_ONLY: log decision, allow request
                       ENFORCE:  log decision, block if denied
```

## CLI Quick Reference

```bash
# Policy engines
agentcore policy create-policy-engine --name <name> --region us-east-1
agentcore policy list-policy-engines --region us-east-1
agentcore policy get-policy-engine -e <engine-id> --region us-east-1
agentcore policy delete-policy-engine -e <engine-id> --region us-east-1

# Policies
agentcore policy create-policy -e <engine-id> -n <name> \
    --definition '{"cedar":{"statement":"permit(principal, action, resource);"}}'
agentcore policy list-policies -e <engine-id> --region us-east-1
agentcore policy get-policy -e <engine-id> -p <policy-id> --region us-east-1
agentcore policy delete-policy -e <engine-id> -p <policy-id> --region us-east-1

# AI generation
agentcore policy start-policy-generation -e <engine-id> -n <name> \
    --resource-arn <gateway-arn> --content "Allow refunds under $1000"
agentcore policy get-policy-generation -e <engine-id> -g <generation-id>
agentcore policy list-policy-generation-assets -e <engine-id> -g <generation-id>
```

## Running the Demo Script

```bash
source .venv/bin/activate

# Full setup: engine + 3 policies + gateway association (LOG_ONLY)
python -m scripts.setup_policies

# Check current state
python -m scripts.setup_policies --status

# AI generation demo
python -m scripts.setup_policies --generate

# Switch to ENFORCE mode
python -m scripts.setup_policies --mode ENFORCE

# Clean up all resources
python -m scripts.setup_policies --teardown
```
