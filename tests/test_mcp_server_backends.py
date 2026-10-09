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


@unittest.skipIf(mcp_server is None, "mcp package not installed")
class DecodeAgentPayloadTests(unittest.TestCase):
    """The runtime JSON-encodes the entrypoint's return value, and the gateway
    handler Lambda wraps THAT in {"result": ...}. The decoder must peel every
    layer so Claude Code sees markdown, not JSON-in-JSON."""

    def test_plain_markdown_passes_through(self):
        self.assertEqual(mcp_server._decode_agent_payload("## Hi\n\nbody"), "## Hi\n\nbody")

    def test_json_string_literal_is_decoded(self):
        import json
        self.assertEqual(mcp_server._decode_agent_payload(json.dumps("## Hi\n\nbody")), "## Hi\n\nbody")

    def test_gateway_handler_wrapper_is_fully_unwrapped(self):
        import json
        wrapped = json.dumps({"result": json.dumps("## Token Audit\n\n**Grade A**")})
        self.assertEqual(mcp_server._decode_agent_payload(wrapped), "## Token Audit\n\n**Grade A**")

    def test_dict_without_known_keys_is_returned_as_json(self):
        import json
        self.assertEqual(mcp_server._decode_agent_payload('{"foo": 1}'), json.dumps({"foo": 1}))


@unittest.skipIf(mcp_server is None, "mcp package not installed")
class GatewayCallTests(unittest.TestCase):
    def _mcp_response(self, text):
        import io, json
        body = json.dumps({"jsonrpc": "2.0", "id": 1,
                           "result": {"content": [{"type": "text", "text": text}]}}).encode()
        resp = mock.MagicMock()
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp

    def test_gateway_text_is_unwrapped_to_markdown(self):
        import json
        nested = json.dumps({"result": json.dumps("## Token Audit\n\n**Grade A**")})
        with mock.patch.object(mcp_server, "_get_access_token", return_value="jwt"), \
             mock.patch.object(mcp_server, "_get_gateway_url", return_value="https://gw.example/mcp"), \
             mock.patch.object(mcp_server.urllib.request, "urlopen", return_value=self._mcp_response(nested)):
            out = mcp_server._call_via_gateway("audit please")
        self.assertEqual(out, "## Token Audit\n\n**Grade A**")

    def test_gateway_error_is_surfaced(self):
        import json
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}}).encode()
        resp = mock.MagicMock(); resp.read.return_value = body
        resp.__enter__.return_value = resp; resp.__exit__.return_value = False
        with mock.patch.object(mcp_server, "_get_access_token", return_value="jwt"), \
             mock.patch.object(mcp_server, "_get_gateway_url", return_value="https://gw.example/mcp"), \
             mock.patch.object(mcp_server.urllib.request, "urlopen", return_value=resp):
            out = mcp_server._call_via_gateway("q")
        self.assertTrue(out.startswith("Gateway error:"))
