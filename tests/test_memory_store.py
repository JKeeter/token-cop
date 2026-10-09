"""Tests for memory/store.py memory-ID resolution.

Token Cop's snapshots need the memory with SEMANTIC + USER_PREFERENCE
strategies (TokenCopMemory). The AgentCore starter toolkit injects its own
BEDROCK_AGENTCORE_MEMORY_ID for a strategy-less STM memory it provisions;
that one must never be used silently.
"""
import logging
import os
import unittest
from unittest.mock import patch

from memory import store


class ResolveMemoryIdTests(unittest.TestCase):
    def _env(self, **overrides):
        env = {k: v for k, v in os.environ.items()
               if k not in ("AGENTCORE_MEMORY_ID", "BEDROCK_AGENTCORE_MEMORY_ID")}
        env.update(overrides)
        return patch.dict(os.environ, env, clear=True)

    def test_defaults_to_token_cop_memory(self):
        with self._env():
            self.assertEqual(store.resolve_memory_id(), store.DEFAULT_MEMORY_ID)
        self.assertEqual(store.DEFAULT_MEMORY_ID, "TokenCopMemory-oGHHvc2vSN")

    def test_explicit_override_wins(self):
        with self._env(AGENTCORE_MEMORY_ID="CustomMemory-abc123"):
            self.assertEqual(store.resolve_memory_id(), "CustomMemory-abc123")

    def test_toolkit_memory_is_ignored_with_warning(self):
        with self._env(BEDROCK_AGENTCORE_MEMORY_ID="token_cop_mem-hpzWit5smt"):
            with self.assertLogs(store.logger, level=logging.WARNING) as logs:
                self.assertEqual(store.resolve_memory_id(), store.DEFAULT_MEMORY_ID)
        joined = "\n".join(logs.output)
        self.assertIn("token_cop_mem-hpzWit5smt", joined)
        self.assertIn(store.DEFAULT_MEMORY_ID, joined)

    def test_no_warning_when_toolkit_memory_matches(self):
        with self._env(BEDROCK_AGENTCORE_MEMORY_ID=store.DEFAULT_MEMORY_ID):
            with self.assertNoLogs(store.logger, level=logging.WARNING):
                self.assertEqual(store.resolve_memory_id(), store.DEFAULT_MEMORY_ID)


if __name__ == "__main__":
    unittest.main()
