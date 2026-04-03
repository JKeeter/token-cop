"""MCP Server for Token Cop - exposes the deployed AgentCore agent as a tool in Claude Code.

Supports two backends (set TOKEN_COP_BACKEND env var):
  - "gateway" (default): Calls the MCP Gateway with Cognito JWT auth
  - "direct": Calls the AgentCore Runtime directly via boto3/IAM
"""
import json
import os
import time
import urllib.parse
import urllib.request

import boto3
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("token-cop")

REGION = "us-east-1"
COGNITO_SCOPE = "token-cop-gateway/invoke"

BACKEND = os.environ.get("TOKEN_COP_BACKEND", "gateway")

# Token cache
_token: str | None = None
_token_expires_at: float = 0

# SSM-loaded config cache
_ssm_config: dict[str, str] = {}


def _get_ssm_param(name: str) -> str:
    """Load a parameter from SSM, caching the result."""
    if name in _ssm_config:
        return _ssm_config[name]
    ssm = boto3.client("ssm", region_name=REGION)
    value = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    _ssm_config[name] = value
    return value


def _get_gateway_url() -> str:
    """Load gateway URL from env or SSM."""
    return os.environ.get("TOKEN_COP_GATEWAY_URL") or _get_ssm_param("/token-cop/gateway-url")


def _get_token_endpoint() -> str:
    """Load Cognito token endpoint from env or SSM."""
    return os.environ.get("TOKEN_COP_TOKEN_ENDPOINT") or _get_ssm_param("/token-cop/gateway-token-endpoint")


def _get_agent_arn() -> str:
    """Load agent ARN from env or SSM."""
    return os.environ.get("TOKEN_COP_AGENT_ARN") or _get_ssm_param("/token-cop/agent-arn")


def _get_cognito_credentials() -> tuple[str, str]:
    """Load Cognito client credentials from SSM Parameter Store."""
    client_id = _get_ssm_param("/token-cop/gateway-client-id")
    client_secret = _get_ssm_param("/token-cop/gateway-client-secret")
    return client_id, client_secret


def _get_access_token() -> str:
    """Get a valid Cognito access token, refreshing if expired."""
    global _token, _token_expires_at

    # Return cached token if still valid (with 60s buffer)
    if _token and time.time() < _token_expires_at - 60:
        return _token

    client_id, client_secret = _get_cognito_credentials()

    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": COGNITO_SCOPE,
    }).encode()

    req = urllib.request.Request(
        _get_token_endpoint(),
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        token_data = json.loads(resp.read())

    _token = token_data["access_token"]
    _token_expires_at = time.time() + token_data.get("expires_in", 3600)
    return _token


def _call_via_gateway(prompt: str) -> str:
    """Call the agent through the MCP Gateway (JWT-secured HTTPS)."""
    token = _get_access_token()

    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "token-cop-target___token_cop",
            "arguments": {"prompt": prompt},
        },
    }).encode()

    req = urllib.request.Request(
        _get_gateway_url(),
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read())

    # Extract result from MCP response
    if "result" in result:
        content = result["result"]
        if isinstance(content, dict) and "content" in content:
            parts = content["content"]
            return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        return json.dumps(content)
    if "error" in result:
        return f"Gateway error: {result['error']}"
    return json.dumps(result)


def _call_direct(prompt: str) -> str:
    """Call the AgentCore Runtime directly via boto3/IAM."""
    client = boto3.client("bedrock-agentcore", region_name=REGION)

    response = client.invoke_agent_runtime(
        agentRuntimeArn=_get_agent_arn(),
        payload=json.dumps({"prompt": prompt}),
    )

    chunks = []
    for event in response.get("body", response.get("output", [])):
        if "chunk" in event:
            chunk_data = event["chunk"]
            if "bytes" in chunk_data:
                chunks.append(chunk_data["bytes"].decode("utf-8"))
            elif "text" in chunk_data:
                chunks.append(chunk_data["text"])
        elif isinstance(event, bytes):
            chunks.append(event.decode("utf-8"))

    if not chunks:
        body = response.get("body")
        if body and hasattr(body, "read"):
            raw = body.read()
            chunks.append(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
        elif isinstance(body, str):
            chunks.append(body)

    return "".join(chunks) if chunks else "No response received from Token Cop agent."


@mcp.tool()
def token_cop_context_audit(project_dir: str = ".") -> str:
    """Audit your Claude Code environment for context bloat.

    Inspects CLAUDE.md files, MCP servers, skills, and plugins to find
    wasted tokens in your session context. Returns a report with scores
    and pruning recommendations.

    Args:
        project_dir: Project root to audit (default: current directory).
    """
    from tools.context_audit import context_audit

    result = context_audit(project_dir)
    # Strands tools may return a tool_result dict
    if isinstance(result, dict) and "content" in result:
        return "".join(
            block.get("text", "") for block in result["content"]
            if isinstance(block, dict)
        )
    return str(result)


@mcp.tool()
def token_cop(prompt: str) -> str:
    """Query Token Cop for LLM token usage across AWS Bedrock, OpenRouter, and OpenAI.

    Ask about token usage, costs, budgets, and trends across providers.

    Examples:
        - "What is my Bedrock usage this week?"
        - "Show all provider usage for the last 30 days"
        - "Am I on track for a $500 monthly budget?"
        - "Which model costs the most?"

    Args:
        prompt: Your question about token usage.
    """
    if BACKEND == "gateway":
        return _call_via_gateway(prompt)
    return _call_direct(prompt)


if __name__ == "__main__":
    mcp.run(transport="stdio")
