"""MCP Server for Token Cop - exposes the deployed AgentCore agent as a tool in Claude Code.

Supports three backends (set TOKEN_COP_BACKEND env var):
  - "gateway" (default): Calls the MCP Gateway with Cognito JWT auth
  - "harness": Calls the managed AgentCore *harness* twin via InvokeHarness
    (boto3/IAM). One runtimeSessionId is kept per MCP server process so
    consecutive /tokcop calls share conversation context; set
    TOKEN_COP_HARNESS_FRESH_SESSION=1 for a new session on every call.
  - anything else (e.g. "direct"): Calls the AgentCore Runtime directly via boto3/IAM

The ``token_cop`` tool also accepts a per-call ``backend`` override.
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
BACKENDS = ("gateway", "harness", "direct")

# Token cache
_token: str | None = None
_token_expires_at: float = 0

# SSM-loaded config cache
_ssm_config: dict[str, str] = {}

# Harness conversation session (one per MCP server process, created lazily)
_harness_session_id: str | None = None


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
    """Call the AgentCore Runtime directly via boto3/IAM.

    ``invoke_agent_runtime`` returns the agent's payload under the ``response``
    key (a streaming body). BedrockAgentCoreApp JSON-encodes the entrypoint's
    return value, so a plain-string answer arrives as a JSON string; SSE
    streaming responses arrive as ``data: ...`` lines.
    """
    import uuid

    client = boto3.client("bedrock-agentcore", region_name=REGION)

    response = client.invoke_agent_runtime(
        agentRuntimeArn=_get_agent_arn(),
        runtimeSessionId=str(uuid.uuid4()),
        payload=json.dumps({"prompt": prompt}),
    )

    body = response.get("response")
    content_type = response.get("contentType", "") or ""
    if body is None:
        return "No response received from Token Cop agent."

    if "text/event-stream" in content_type:
        chunks = []
        for line in body.iter_lines():
            if not line:
                continue
            text = line.decode("utf-8") if isinstance(line, bytes) else str(line)
            if text.startswith("data: "):
                text = text[len("data: "):]
            chunks.append(_decode_agent_payload(text))
        return "".join(chunks) or "No response received from Token Cop agent."

    raw = body.read()
    text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    return _decode_agent_payload(text) or "No response received from Token Cop agent."


def _decode_agent_payload(text: str) -> str:
    """Unwrap the JSON the runtime wraps around the entrypoint's return value."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return text
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("result", "response", "output", "text"):
            if isinstance(data.get(key), str):
                return data[key]
    return json.dumps(data)


def _harness_session() -> str:
    """Return the per-process harness session id (or a fresh one if requested)."""
    global _harness_session_id
    from agent.harness_client import new_session_id

    if os.environ.get("TOKEN_COP_HARNESS_FRESH_SESSION") == "1":
        return new_session_id()
    if _harness_session_id is None:
        _harness_session_id = new_session_id()
    return _harness_session_id


def _call_via_harness(prompt: str) -> str:
    """Call the managed AgentCore harness twin via InvokeHarness (boto3/IAM).

    The runtime container scrubs its own output server-side; the harness has
    no hook for that, so the answer is scrubbed here before it reaches Claude
    Code. A one-line usage footer is appended: Token Cop reporting on itself.
    """
    # Lazy so the gateway/direct paths never import agent code (Strands etc.).
    from agent.guardrails import scrub_response
    from agent.harness_client import invoke

    try:
        result = invoke(prompt, session_id=_harness_session())
    except (RuntimeError, ValueError) as exc:
        return f"Harness error: {exc}"

    text = scrub_response(result.text) or "No response received from Token Cop harness."
    if result.fabrication_warning:
        text = (
            "**WARNING — unverified answer.** " + result.fabrication_warning + ". "
            "Token Cop only trusts figures that come from a tool result; check gateway "
            "access and Cedar policy mode (`python -m scripts.setup_policies --status`).\n\n" + text
        )
    return text + "\n\n_" + result.usage_footer() + "_"


def _select_backend(override: str = "", default: str | None = None) -> str:
    """Resolve which backend to use.

    ``override`` (per-call argument) wins when non-empty; otherwise the
    module-level ``BACKEND`` (from ``TOKEN_COP_BACKEND``). ``gateway`` and
    ``harness`` are matched exactly (case-insensitive, whitespace-trimmed);
    anything else falls back to ``direct`` — preserving the historical
    behaviour where any unrecognised value meant the direct runtime path.
    """
    name = (override or (default if default is not None else BACKEND) or "").strip().lower()
    if name in ("gateway", "harness"):
        return name
    return "direct"


_BACKEND_CALLS = {
    "gateway": _call_via_gateway,
    "harness": _call_via_harness,
    "direct": _call_direct,
}


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
def token_cop(prompt: str, backend: str = "") -> str:
    """Query Token Cop for LLM token usage across AWS Bedrock, OpenRouter, and OpenAI.

    Ask about token usage, costs, budgets, and trends across providers.

    Examples:
        - "What is my Bedrock usage this week?"
        - "Show all provider usage for the last 30 days"
        - "Am I on track for a $500 monthly budget?"
        - "Which model costs the most?"

    Args:
        prompt: Your question about token usage.
        backend: Optional per-call override: "gateway" (MCP Gateway + JWT),
            "harness" (managed AgentCore harness twin), or "direct"
            (AgentCore Runtime via IAM). Empty uses the TOKEN_COP_BACKEND
            env default (gateway).
    """
    return _BACKEND_CALLS[_select_backend(backend)](prompt)


if __name__ == "__main__":
    mcp.run(transport="stdio")
