# MCP Gateway Architecture

## Overview

Token Cop is exposed as a secured MCP server via AWS Bedrock AgentCore's MCP Gateway.
The gateway provides a standards-compliant MCP endpoint over HTTPS, authenticated with
Cognito JWT tokens. No anonymous access is possible.

## Request Flow

```
Claude Code ──stdio──> mcp_server.py ──JWT/HTTPS──> MCP Gateway ──Lambda──> AgentCore Runtime
     │                      │                            │                        │
     │                      │                            │                        └─ Strands Agent
     │                      │                            │                           (agent/app.py)
     │                      │                            │
     │                      │                            └─ Validates JWT, routes
     │                      │                               to target by tool name
     │                      │
     │                      └─ Fetches/caches Cognito token,
     │                         sends MCP JSON-RPC over HTTPS
     │
     └─ Claude Code runs mcp_server.py as a
        stdio MCP server (configured in settings.json)
```

### Alternate: Direct Mode

```
Claude Code ──stdio──> mcp_server.py ──SigV4/boto3──> AgentCore Runtime
```

Set `TOKEN_COP_BACKEND=direct` to skip the gateway and call the runtime directly via IAM.

## Token Refresh

Cognito tokens from the `client_credentials` OAuth flow expire after 1 hour (3600s).
Rather than using a cron job or scheduled task, token refresh is handled in-process
by `mcp_server.py`.

### How it works

1. On the first `/tokcop` call, `mcp_server.py` has no cached token.
2. `_get_access_token()` fetches Cognito client credentials from SSM Parameter Store.
3. It POSTs to the Cognito token endpoint with `grant_type=client_credentials`.
4. The returned `access_token` and `expires_in` are cached in module-level globals.
5. On subsequent calls, the cached token is returned if `now < expires_at - 60s`.
6. When the token is within 60 seconds of expiry, the next call triggers a fresh fetch.

### Why not a cron?

- Claude Code's MCP server config (`settings.json`) has static headers — you can't
  inject a dynamic Bearer token.
- A cron writing to `settings.json` would require restarting the MCP server to pick
  up the new token, which kills the Claude Code session.
- In-process refresh is invisible to Claude Code. The stdio MCP server stays alive
  for the entire session, so the token cache persists across calls. Refresh happens
  transparently on the ~1 hour boundary.

### Token cache lifecycle

```
Session start ─── First /tokcop call ─── Token fetched (TTL: 3600s)
                                              │
                  Subsequent calls ─────────── Cache hit (fast path)
                                              │
                  ~59 minutes later ────────── Cache miss → refresh
                                              │
                  Continues ───────────────── New token cached
                                              │
Session end ──── mcp_server.py exits ──────── Cache discarded
```

Each new Claude Code session starts fresh — no stale tokens persist on disk.

## Security Layers

| Layer | Mechanism | Protects against |
|-------|-----------|-----------------|
| MCP Gateway | Cognito JWT (Bearer token) | Unauthenticated access |
| Cognito | `client_credentials` flow, admin-only user creation | Token forgery, self-registration |
| Lambda → Runtime | IAM policy scoped to specific agent ARN | Lateral movement |
| SSM Parameter Store | `SecureString` encryption | Credential exposure |
| Claude Code → MCP server | stdio (local process, no network) | Network interception |

## AWS Resources

| Resource | Identifier |
|----------|-----------|
| MCP Gateway | `token-cop-gateway-7q9nodpeem` |
| Gateway URL | `https://token-cop-gateway-7q9nodpeem.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp` |
| Gateway Target | `token-cop-target` (ID: `FHE3QYCIBG`) |
| Lambda | `token-cop-gateway-handler` |
| Lambda Role | `token-cop-gateway-lambda-role` |
| Gateway Role | `AgentCoreGatewayExecutionRole` |
| Cognito User Pool | `us-east-1_hYAk8mbYH` |
| Cognito Domain | `agentcore-d4673f36` |
| Cognito Client ID | `29ksjo8k257ev205bvh4fjn4gg` |
| Token Endpoint | `https://agentcore-d4673f36.auth.us-east-1.amazoncognito.com/oauth2/token` |
| OAuth Scope | `token-cop-gateway/invoke` |

### SSM Parameters

| Parameter | Type |
|-----------|------|
| `/token-cop/gateway-client-id` | SecureString |
| `/token-cop/gateway-client-secret` | SecureString |
| `/token-cop/gateway-url` | String |
| `/token-cop/gateway-token-endpoint` | String |

## Calling the Gateway from Other Clients

Any MCP client that supports Streamable HTTP can connect to the gateway URL.
First obtain a token:

```bash
curl -X POST "$TOKEN_ENDPOINT" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=$CLIENT_ID&client_secret=$CLIENT_SECRET&scope=token-cop-gateway/invoke"
```

Then call the MCP endpoint:

```bash
curl -X POST "$GATEWAY_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "token_cop",
      "arguments": {"prompt": "What is my Bedrock usage this week?"}
    }
  }'
```
