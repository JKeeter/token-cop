"""Tests for scripts/lambda/tool_dispatch.py — the Gateway Lambda target handler.

Uses tools that need no AWS (recommend_model, check_budget, aggregate_usage) and
monkeypatches the secrets loader so nothing touches SSM.
"""
import importlib.util
import json
import logging
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Keep agent.config from calling SSM at import time (PROVIDERS evaluates get_secret).
for _k in ("OPENROUTER_API_KEY", "OPENAI_ADMIN_API_KEY", "ANTHROPIC_ADMIN_API_KEY"):
    os.environ.setdefault(_k, "test-placeholder")

# scripts/lambda is not a package (it is zipped with the handler at the root),
# so load the module by path exactly as Lambda would import it.
_SRC = Path(__file__).resolve().parent.parent / "scripts" / "lambda" / "tool_dispatch.py"
_spec = importlib.util.spec_from_file_location("tool_dispatch", _SRC)
tool_dispatch = importlib.util.module_from_spec(_spec)
sys.modules["tool_dispatch"] = tool_dispatch
_spec.loader.exec_module(tool_dispatch)


def _ctx(tool_name: str):
    """Fake Lambda context carrying the gateway's tool-name client-context key."""
    return SimpleNamespace(
        client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": tool_name}),
    )


class ToolDispatchTests(unittest.TestCase):
    def setUp(self):
        tool_dispatch._secrets_loaded = False
        tool_dispatch.log.setLevel(logging.CRITICAL)  # expected error paths; keep test output clean
        self._patch = patch.object(tool_dispatch, "_load_secrets")
        self.load_secrets = self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_registry_matches_agent_tool_list(self):
        from agent.agent import TOKEN_COP_TOOLS
        self.assertEqual(set(tool_dispatch.REGISTRY), {t.tool_name for t in TOKEN_COP_TOOLS})
        self.assertEqual(len(tool_dispatch.REGISTRY), 13)

    def test_dispatches_prefixed_tool_name_from_client_context(self):
        out = tool_dispatch.handler({"task_description": "fix typo"}, _ctx("token-cop-tools___recommend_model"))
        self.assertIsInstance(out, str)
        data = json.loads(out)
        self.assertEqual(data["recommended_tier"], "polish")
        self.load_secrets.assert_called_once()

    def test_secrets_loaded_once_per_container(self):
        ctx = _ctx("token-cop-tools___recommend_model")
        tool_dispatch.handler({"task_description": "fix typo"}, ctx)
        tool_dispatch.handler({"task_description": "design the architecture"}, ctx)
        self.load_secrets.assert_called_once()

    def test_bare_tool_name_without_prefix(self):
        out = tool_dispatch.handler(
            {"current_spend_usd": 50, "budget_usd": 100, "days_elapsed": 10},
            _ctx("check_budget"),
        )
        data = json.loads(out)
        self.assertNotIn("error", data)
        self.assertEqual(data["daily_burn_rate_usd"], 5.0)

    def test_local_fallback_tool_name_in_event(self):
        out = tool_dispatch.handler(
            {"__tool_name__": "recommend_model", "task_description": "proofread"}, context=None
        )
        data = json.loads(out)
        self.assertIn("recommended_tier", data)

    def test_unknown_tool_returns_error_json(self):
        out = tool_dispatch.handler({}, _ctx("token-cop-tools___nope"))
        data = json.loads(out)
        self.assertEqual(data, {"error": "unknown tool nope", "tool": "nope"})

    def test_missing_tool_name_returns_error_json(self):
        out = tool_dispatch.handler({"x": 1}, context=None)
        self.assertIn("missing tool name", json.loads(out)["error"])

    def test_tool_exception_becomes_error_json(self):
        # aggregate_usage requires provider_results -> TypeError from the call
        # (missing required kwarg) must surface as JSON, not raise.
        out = tool_dispatch.handler({}, _ctx("token-cop-tools___aggregate_usage"))
        data = json.loads(out)
        self.assertIn("error", data)
        self.assertEqual(data["tool"], "aggregate_usage")

    def test_raising_tool_is_caught(self):
        def boom(**kwargs):
            raise RuntimeError("kaboom")
        with patch.dict(tool_dispatch.REGISTRY, {"boom": boom}):
            out = tool_dispatch.handler({"a": 1}, _ctx("token-cop-tools___boom"))
        self.assertEqual(json.loads(out), {"error": "kaboom", "tool": "boom"})

    def test_non_dict_event_is_rejected(self):
        out = tool_dispatch.handler(["not", "a", "dict"], _ctx("token-cop-tools___recommend_model"))
        self.assertIn("event must be an object", json.loads(out)["error"])


class ToTextTests(unittest.TestCase):
    def test_string_passthrough(self):
        self.assertEqual(tool_dispatch._to_text('{"a":1}'), '{"a":1}')

    def test_content_blocks_joined(self):
        result = {"status": "success", "content": [{"text": "one"}, {"json": {"b": 2}}]}
        self.assertEqual(tool_dispatch._to_text(result), 'one\n{"b": 2}')

    def test_plain_dict_serialized(self):
        self.assertEqual(json.loads(tool_dispatch._to_text({"k": [1, 2]})), {"k": [1, 2]})

    def test_none_is_empty(self):
        self.assertEqual(tool_dispatch._to_text(None), "")


if __name__ == "__main__":
    unittest.main()
