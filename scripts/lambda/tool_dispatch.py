"""Gateway Lambda target that fronts the 13 Token Cop tools for the harness twin.

The AgentCore *harness* runs a managed Strands loop with no in-process Python
tools, so the same ``@tool`` functions the container runtime calls directly
are re-hosted here behind the existing MCP Gateway (target ``token-cop-tools``).

Dispatch contract (AgentCore Gateway -> Lambda target):
  - The tool name arrives in the Lambda *client context*:
    ``context.client_context.custom["bedrockAgentCoreToolName"]`` formatted as
    ``<target-name>___<tool_name>`` (e.g. ``token-cop-tools___bedrock_usage``).
  - ``event`` is the tool's argument dict exactly as the model produced it.
  - The return value is handed back to the model as the tool result. Every
    Token Cop tool returns a JSON string, so we return that string unchanged.

Registry: built from ``agent.agent.TOKEN_COP_TOOLS`` — the same list the
runtime chassis uses — so a tool added there is automatically exposed here
(the gateway schema is regenerated from ``tool_spec`` by
``scripts/setup_harness.py``).

Local / test invocation: pass ``context=None`` and ``event["__tool_name__"]``.

Packaged with deps by ``scripts.setup_harness.build_lambda_package`` and
deployed as Lambda ``token-cop-tools`` (python3.13, arm64).
"""
from __future__ import annotations

import json
import logging
import traceback

from agent.agent import TOKEN_COP_TOOLS

log = logging.getLogger("token_cop.tool_dispatch")
log.setLevel(logging.INFO)

TOOL_NAME_KEY = "bedrockAgentCoreToolName"
LOCAL_TOOL_NAME_KEY = "__tool_name__"
NAME_SEPARATOR = "___"

# tool_name -> DecoratedFunctionTool (callable: tool(**kwargs) -> str)
REGISTRY = {t.tool_name: t for t in TOKEN_COP_TOOLS}

_secrets_loaded = False


def _load_secrets():
    """Pull SSM ``/token-cop/*`` secrets into os.environ once per container.

    Deferred to first invocation (not import) so unit tests can import this
    module — and monkeypatch this function — without touching SSM.
    """
    from agent.config import load_all_secrets

    load_all_secrets()


def _ensure_secrets():
    global _secrets_loaded
    if _secrets_loaded:
        return
    _load_secrets()
    _secrets_loaded = True


def resolve_tool_name(event, context) -> str | None:
    """Return the bare tool name from the Lambda client context (or local fallback)."""
    raw = None
    client_ctx = getattr(context, "client_context", None) if context is not None else None
    custom = getattr(client_ctx, "custom", None) if client_ctx is not None else None
    if isinstance(custom, dict):
        raw = custom.get(TOOL_NAME_KEY)
    if not raw and isinstance(event, dict):
        raw = event.get(LOCAL_TOOL_NAME_KEY)
    if not raw:
        return None
    # "token-cop-tools___bedrock_usage" -> "bedrock_usage"; tolerate a bare name.
    return raw.rsplit(NAME_SEPARATOR, 1)[-1]


def _to_text(result) -> str:
    """Normalize a tool return value to the string the gateway hands the model.

    Strands tools in this repo return JSON strings. Be tolerant of the
    ToolResult-style dict (``{"content": [{"text": ...}, ...]}``) and of plain
    dicts/lists.
    """
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and "content" in result:
        blocks = result.get("content") or []
        texts = []
        for block in blocks:
            if isinstance(block, dict) and "text" in block:
                texts.append(str(block["text"]))
            elif isinstance(block, dict) and "json" in block:
                texts.append(json.dumps(block["json"]))
            else:
                texts.append(str(block))
        return "\n".join(texts)
    if result is None:
        return ""
    try:
        return json.dumps(result)
    except (TypeError, ValueError):
        return str(result)


def _error(message: str, tool: str | None = None) -> str:
    payload = {"error": message}
    if tool:
        payload["tool"] = tool
    return json.dumps(payload)


def handler(event, context=None):
    """Lambda entrypoint. Returns the tool's JSON string (or an ``{"error": ...}`` JSON string)."""
    tool_name = resolve_tool_name(event, context)
    if not tool_name:
        log.error("No tool name in client context or event")
        return _error(f"missing tool name ({TOOL_NAME_KEY})")

    tool = REGISTRY.get(tool_name)
    if tool is None:
        log.error("Unknown tool requested: %s", tool_name)
        return _error(f"unknown tool {tool_name}", tool_name)

    if not isinstance(event, dict):
        return _error(f"event must be an object of tool arguments, got {type(event).__name__}", tool_name)
    args = {k: v for k, v in event.items() if k != LOCAL_TOOL_NAME_KEY}

    try:
        _ensure_secrets()
        log.info("Dispatching %s(%s)", tool_name, ", ".join(sorted(args)))
        return _to_text(tool(**args))
    except Exception as exc:  # noqa: BLE001 - surface as a tool error, never crash the loop
        log.error("Tool %s failed: %s\n%s", tool_name, exc, traceback.format_exc())
        return _error(str(exc), tool_name)
