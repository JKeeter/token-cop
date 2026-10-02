"""Tests for mcp_server.py backend selection and the harness call path.

``mcp_server`` imports the ``mcp`` package at module import; these tests are
skipped cleanly when it is not installed in the venv.
"""

import unittest
from unittest import mock

try:
    import mcp_server
except ImportError as exc:  # pragma: no cover - env without `mcp`
    mcp_server = None
    _IMPORT_ERROR = exc


@unittest.skipIf(mcp_server is None, "mcp package not installed")
class SelectBackendTests(unittest.TestCase):

    def test_env_default_gateway(self):
        self.assertEqual(mcp_server._select_backend("", default="gateway"), "gateway")

    def test_env_harness(self):
        self.assertEqual(mcp_server._select_backend("", default="harness"), "harness")

    def test_env_direct_and_unknown_fall_back_to_direct(self):
        self.assertEqual(mcp_server._select_backend("", default="direct"), "direct")
        self.assertEqual(mcp_server._select_backend("", default="bogus"), "direct")
        self.assertEqual(mcp_server._select_backend("", default=""), "direct")

    def test_override_wins(self):
        self.assertEqual(mcp_server._select_backend("harness", default="gateway"), "harness")
        self.assertEqual(mcp_server._select_backend("gateway", default="harness"), "gateway")
        self.assertEqual(mcp_server._select_backend("direct", default="gateway"), "direct")

    def test_override_normalised(self):
        self.assertEqual(mcp_server._select_backend(" Harness ", default="gateway"), "harness")

    def test_uses_module_backend_when_no_default(self):
        with mock.patch.object(mcp_server, "BACKEND", "harness"):
            self.assertEqual(mcp_server._select_backend(), "harness")

    def test_dispatch_table_covers_all_backends(self):
        self.assertEqual(set(mcp_server._BACKEND_CALLS), set(mcp_server.BACKENDS))


@unittest.skipIf(mcp_server is None, "mcp package not installed")
class HarnessCallTests(unittest.TestCase):

    def setUp(self):
        mcp_server._harness_session_id = None

    def tearDown(self):
        mcp_server._harness_session_id = None

    def _result(self, text="You spent $5.", **kw):
        from agent.harness_client import HarnessResult
        return HarnessResult(text=text, stop_reason="end_turn",
                             usage={"inputTokens": 300, "outputTokens": 50}, **kw)

    def test_session_reused_across_calls(self):
        with mock.patch("agent.harness_client.invoke", return_value=self._result()) as inv, \
             mock.patch.dict("os.environ", {"TOKEN_COP_HARNESS_FRESH_SESSION": ""}):
            mcp_server._call_via_harness("q1")
            mcp_server._call_via_harness("q2")
        sids = [c.kwargs["session_id"] for c in inv.call_args_list]
        self.assertEqual(len(sids), 2)
        self.assertEqual(sids[0], sids[1])
        self.assertGreaterEqual(len(sids[0]), 33)

    def test_fresh_session_env(self):
        with mock.patch("agent.harness_client.invoke", return_value=self._result()) as inv, \
             mock.patch.dict("os.environ", {"TOKEN_COP_HARNESS_FRESH_SESSION": "1"}):
            mcp_server._call_via_harness("q1")
            mcp_server._call_via_harness("q2")
        sids = [c.kwargs["session_id"] for c in inv.call_args_list]
        self.assertNotEqual(sids[0], sids[1])

    def test_footer_appended_and_text_scrubbed(self):
        leaked = "Key is sk-ant-api03-" + "A" * 95 + " ok."
        with mock.patch("agent.harness_client.invoke", return_value=self._result(text=leaked)):
            out = mcp_server._call_via_harness("q")
        self.assertNotIn("sk-ant-api03-" + "A" * 95, out)
        self.assertTrue(out.endswith("_harness usage: in=300 out=50_"))

    def test_runtime_error_becomes_message(self):
        with mock.patch("agent.harness_client.invoke", side_effect=RuntimeError("harness error: denied")):
            out = mcp_server._call_via_harness("q")
        self.assertTrue(out.startswith("Harness error: "))
        self.assertIn("denied", out)

    def test_value_error_becomes_message(self):
        with mock.patch("agent.harness_client.invoke", side_effect=ValueError("bad")):
            self.assertTrue(mcp_server._call_via_harness("q").startswith("Harness error: "))

    def test_empty_text_gets_placeholder(self):
        with mock.patch("agent.harness_client.invoke", return_value=self._result(text="")):
            out = mcp_server._call_via_harness("q")
        self.assertIn("No response received from Token Cop harness.", out)

    def test_token_cop_tool_dispatches_override(self):
        with mock.patch.object(mcp_server, "_BACKEND_CALLS",
                               {"gateway": lambda p: "G", "harness": lambda p: "H", "direct": lambda p: "D"}):
            self.assertEqual(mcp_server.token_cop("x", backend="harness"), "H")
            with mock.patch.object(mcp_server, "BACKEND", "gateway"):
                self.assertEqual(mcp_server.token_cop("x"), "G")
            with mock.patch.object(mcp_server, "BACKEND", "whatever"):
                self.assertEqual(mcp_server.token_cop("x"), "D")


if __name__ == "__main__":
    unittest.main()
