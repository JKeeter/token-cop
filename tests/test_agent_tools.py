"""Tests for agent/agent.py tool registration.

Guards the invariant that every tool the system prompt tells the model to call
is actually registered in TOKEN_COP_TOOLS. The prompt and the tool list live in
the same file but nothing else ties them together; token_audit was referenced
by the prompt for months without being registered.
"""
import os
import re
import unittest
from pathlib import Path

# Keep agent.config from calling SSM at import time (PROVIDERS evaluates get_secret).
for _k in ("OPENROUTER_API_KEY", "OPENAI_ADMIN_API_KEY", "ANTHROPIC_ADMIN_API_KEY"):
    os.environ.setdefault(_k, "test-placeholder")

from agent.agent import TOKEN_COP_TOOLS, build_system_prompt  # noqa: E402

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
_TOOL_DEF = re.compile(r"^@tool\s*\n\s*def\s+(\w+)\s*\(", re.MULTILINE)
_IDENTIFIER = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")


def _all_tool_function_names() -> set[str]:
    """Every @tool-decorated function defined under tools/, by static scan (no imports)."""
    names: set[str] = set()
    for src in _TOOLS_DIR.glob("*.py"):
        names.update(_TOOL_DEF.findall(src.read_text()))
    return names


class AgentToolRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.registered = {t.tool_name for t in TOKEN_COP_TOOLS}

    def test_token_audit_is_registered(self):
        self.assertIn("token_audit", self.registered)

    def test_every_tool_named_in_system_prompt_is_registered(self):
        prompt_identifiers = set(_IDENTIFIER.findall(build_system_prompt()))
        referenced_tools = prompt_identifiers & _all_tool_function_names()
        self.assertTrue(referenced_tools, "sanity: prompt should reference at least one tool")
        missing = referenced_tools - self.registered
        self.assertEqual(
            missing, set(),
            f"system prompt tells the model to call tools that are not in TOKEN_COP_TOOLS: {sorted(missing)}",
        )


if __name__ == "__main__":
    unittest.main()
