"""Pure-function tests for scripts/setup_harness.py (no AWS calls).

Covers the Strands tool_spec -> Gateway ToolDefinition conversion, the IAM
policy builders, the harness config, and the Lambda package layout.
"""
import io
import json
import os
import unittest
import zipfile

for _k in ("OPENROUTER_API_KEY", "OPENAI_ADMIN_API_KEY", "ANTHROPIC_ADMIN_API_KEY"):
    os.environ.setdefault(_k, "test-placeholder")

from agent.agent import MODEL_ID, TOKEN_COP_TOOLS  # noqa: E402
from agent.harness_client import DEFAULT_ALLOWED_TOOLS, GATEWAY_TOOL_NAME  # noqa: E402
from scripts import setup_harness as sh  # noqa: E402

ACCT = "123456789012"


def _walk_schema(schema, path="inputSchema"):
    """Yield (path, dict) for every SchemaDefinition node."""
    yield path, schema
    for k, v in schema.get("properties", {}).items():
        yield from _walk_schema(v, f"{path}.properties.{k}")
    if "items" in schema:
        yield from _walk_schema(schema["items"], f"{path}.items")


class GatewayToolSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = sh.gateway_tool_schema()
        cls.by_name = {t["name"]: t for t in cls.schema}

    def test_every_tool_converts(self):
        self.assertEqual(len(self.schema), len(TOKEN_COP_TOOLS))
        self.assertEqual(set(self.by_name), {t.tool_name for t in TOKEN_COP_TOOLS})

    def test_tool_definition_keys(self):
        for t in self.schema:
            with self.subTest(tool=t["name"]):
                self.assertEqual(set(t), {"name", "description", "inputSchema"})
                self.assertTrue(t["description"])
                self.assertLessEqual(len(t["description"]), sh.TOOL_DESCRIPTION_MAX)

    def test_only_allowed_schema_keys_and_types(self):
        for t in self.schema:
            for path, node in _walk_schema(t["inputSchema"]):
                with self.subTest(tool=t["name"], path=path):
                    self.assertTrue(set(node) <= sh.SCHEMA_ALLOWED_KEYS, f"extra keys: {set(node) - sh.SCHEMA_ALLOWED_KEYS}")
                    self.assertIn(node["type"], sh.SCHEMA_ALLOWED_TYPES)
                    self.assertNotIn("default", node)

    def test_root_is_object(self):
        for t in self.schema:
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_required_preserved(self):
        self.assertEqual(self.by_name["recommend_model"]["inputSchema"]["required"], ["task_description"])
        self.assertEqual(
            self.by_name["check_budget"]["inputSchema"]["required"],
            ["current_spend_usd", "budget_usd", "days_elapsed"],
        )
        self.assertNotIn("required", self.by_name["list_denied_principals"]["inputSchema"])

    def test_array_items_preserved(self):
        arrays = [
            (t["name"], k, p) for t in self.schema
            for k, p in t["inputSchema"].get("properties", {}).items() if p["type"] == "array"
        ]
        self.assertTrue(arrays, "expected at least one array-typed tool argument")
        for name, key, prop in arrays:
            with self.subTest(tool=name, arg=key):
                self.assertIn("items", prop)
                self.assertIn(prop["items"]["type"], sh.SCHEMA_ALLOWED_TYPES)

    def test_source_specs_had_defaults_that_were_stripped(self):
        # Guard against the test passing vacuously: the Strands specs DO carry
        # ``default`` keys, and the conversion must remove them.
        raw_defaults = sum(
            1 for t in TOKEN_COP_TOOLS
            for p in t.tool_spec["inputSchema"]["json"].get("properties", {}).values() if "default" in p
        )
        self.assertGreater(raw_defaults, 0)

    def test_descriptions_preserved(self):
        self.assertIn("description", self.by_name["recommend_model"]["inputSchema"]["properties"]["task_description"])

    def test_json_serializable(self):
        json.dumps(self.schema)

    def test_clean_schema_edge_cases(self):
        cleaned = sh._clean_schema({
            "type": ["string", "null"], "default": "x", "enum": ["a"], "description": "d",
        })
        self.assertEqual(cleaned, {"type": "string", "description": "d"})
        self.assertEqual(sh._clean_schema({"properties": {"a": {"type": "integer"}}})["type"], "object")
        self.assertEqual(sh._clean_schema({"items": {"type": "string"}})["type"], "array")
        with self.assertRaises(ValueError):
            sh._clean_schema({"type": "null"})

    def test_long_description_truncated(self):
        class FakeTool:
            tool_name = "long"
            tool_spec = {"name": "long", "description": "x" * 5000,
                         "inputSchema": {"json": {"type": "object", "properties": {}}}}
        out = sh.strands_spec_to_gateway_tool(FakeTool())
        self.assertLessEqual(len(out["description"]), sh.TOOL_DESCRIPTION_MAX)


class PolicyBuilderTests(unittest.TestCase):
    def _assert_policy(self, doc):
        self.assertEqual(doc["Version"], "2012-10-17")
        json.dumps(doc)
        for s in doc["Statement"]:
            self.assertEqual(s["Effect"], "Allow")
            self.assertIn("Action", s)
            self.assertIn("Resource", s)
        self.assertNotIn("<acct>", json.dumps(doc))

    def test_tools_lambda_policy(self):
        doc = sh.tools_lambda_policy(ACCT)
        self._assert_policy(doc)
        text = json.dumps(doc)
        self.assertIn(f"arn:aws:ssm:us-east-1:{ACCT}:parameter/token-cop/*", text)
        self.assertIn(f"arn:aws:dynamodb:us-east-1:{ACCT}:table/token-cop-enforcement-usage", text)
        self.assertIn(f"arn:aws:iam::{ACCT}:policy/TokenCopBedrockBudgetDeny", text)
        self.assertIn(f"arn:aws:bedrock-agentcore:us-east-1:{ACCT}:memory/", text)
        actions = {a for s in doc["Statement"] for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])}
        # One action per boto3 call the tools actually make.
        for needed in (
            "cloudwatch:GetMetricData", "cloudwatch:ListMetrics", "ssm:GetParameter", "kms:Decrypt",
            "s3:GetObject", "s3:ListBucket", "ce:GetCostAndUsage", "dynamodb:GetItem", "dynamodb:PutItem",
            "dynamodb:Scan", "iam:AttachUserPolicy", "iam:DetachRolePolicy", "bedrock-agentcore:CreateEvent",
            "bedrock-agentcore:ListEvents", "bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:GetMemory",
        ):
            self.assertIn(needed, actions)
        sids = [s["Sid"] for s in doc["Statement"]]
        self.assertEqual(len(sids), len(set(sids)))

    def test_harness_role_policy(self):
        gw = f"arn:aws:bedrock-agentcore:us-east-1:{ACCT}:gateway/token-cop-gateway-7q9nodpeem"
        prov = f"arn:aws:bedrock-agentcore:us-east-1:{ACCT}:token-vault/default/oauth2credentialprovider/token-cop-cognito"
        doc = sh.harness_role_policy(ACCT, gw, prov)
        self._assert_policy(doc)
        text = json.dumps(doc)
        self.assertIn(gw, text)
        self.assertIn(prov, text)
        self.assertIn("bedrock-agentcore:GetResourceOauth2Token", text)
        self.assertIn(f"secret:bedrock-agentcore-identity!default/oauth2/token-cop-cognito-*", text)
        self.assertIn("workload-identity/harness_token_cop_harness-*", text)
        self.assertIn("bedrock:InvokeModelWithResponseStream", text)
        oauth = next(s for s in doc["Statement"] if s["Sid"] == "GatewayOAuthToken")
        self.assertEqual(len(oauth["Resource"]), len(set(oauth["Resource"])))  # provider arn deduped

    def test_harness_role_policy_keeps_distinct_provider_arn(self):
        doc = sh.harness_role_policy(ACCT, "arn:gw", "arn:aws:bedrock-agentcore:us-east-1:%s:token-vault/other" % ACCT)
        oauth = next(s for s in doc["Statement"] if s["Sid"] == "GatewayOAuthToken")
        self.assertIn("arn:aws:bedrock-agentcore:us-east-1:%s:token-vault/other" % ACCT, oauth["Resource"])
        self.assertIn(sh.provider_resource_arn(ACCT), oauth["Resource"])

    def test_trust_policies(self):
        lam = sh.lambda_trust_policy()
        self.assertEqual(lam["Statement"][0]["Principal"]["Service"], "lambda.amazonaws.com")
        har = sh.harness_trust_policy(ACCT)
        self.assertEqual(har["Statement"][0]["Principal"]["Service"], "bedrock-agentcore.amazonaws.com")
        self.assertEqual(har["Statement"][0]["Condition"]["StringEquals"]["aws:SourceAccount"], ACCT)

    def test_gateway_invoke_policy(self):
        fn = f"arn:aws:lambda:us-east-1:{ACCT}:function:token-cop-tools"
        doc = sh.gateway_invoke_policy(fn)
        self._assert_policy(doc)
        self.assertEqual(doc["Statement"][0]["Resource"], [fn])


class HarnessConfigTests(unittest.TestCase):
    def test_config_shape(self):
        cfg = sh.harness_config("arn:role", "arn:gw", "arn:prov")
        json.dumps(cfg)
        self.assertEqual(cfg["executionRoleArn"], "arn:role")
        self.assertEqual(cfg["model"], {"bedrockModelConfig": {"modelId": MODEL_ID}})
        self.assertEqual(len(cfg["systemPrompt"]), 1)
        self.assertIn("Token Cop", cfg["systemPrompt"][0]["text"])
        tool = cfg["tools"][0]
        self.assertEqual(tool["type"], "agentcore_gateway")
        self.assertEqual(tool["name"], GATEWAY_TOOL_NAME)
        oauth = tool["config"]["agentCoreGateway"]["outboundAuth"]["oauth"]
        self.assertEqual(oauth, {"providerArn": "arn:prov", "scopes": ["token-cop-gateway/invoke"],
                                 "grantType": "CLIENT_CREDENTIALS"})
        self.assertEqual(cfg["allowedTools"], DEFAULT_ALLOWED_TOOLS)
        self.assertEqual(cfg["memory"], {"disabled": {}})
        self.assertEqual(cfg["truncation"]["strategy"], "sliding_window")
        self.assertEqual(set(cfg) - set(sh._HARNESS_RW_FIELDS), {"systemPrompt"})

    def test_harness_name_matches_api_pattern(self):
        import re
        self.assertRegex(sh.HARNESS_NAME, r"^[a-zA-Z][a-zA-Z0-9_]{0,39}$")
        self.assertRegex(sh.TOOLS_TARGET_NAME, r"^([0-9a-zA-Z][-]?){1,100}$")

    def test_oauth_provider_config(self):
        cfg = sh.oauth_provider_config("cid", "sec")
        custom = cfg["customOauth2ProviderConfig"]
        self.assertEqual(custom["clientId"], "cid")
        self.assertEqual(custom["clientSecret"], "sec")
        self.assertEqual(custom["clientAuthenticationMethod"], "CLIENT_SECRET_POST")
        self.assertIn("us-east-1_hYAk8mbYH/.well-known/openid-configuration", custom["oauthDiscovery"]["discoveryUrl"])

    def test_allowed_tools_exclude_runtime_recursion(self):
        # The harness must never call the runtime agent through token-cop-target.
        for pattern in DEFAULT_ALLOWED_TOOLS:
            self.assertNotIn("token-cop-target", pattern)
            self.assertIn("token-cop-tools___", pattern)


class LambdaPackageTests(unittest.TestCase):
    def test_package_layout_without_deps(self):
        package = sh.build_lambda_package(install_deps=False)
        names = set(zipfile.ZipFile(io.BytesIO(package)).namelist())
        self.assertIn("tool_dispatch.py", names)
        for f in sh.PACKAGE_AGENT_FILES:
            self.assertIn(f"agent/{f}", names)
        for d in sh.PACKAGE_DIRS:
            self.assertIn(f"{d}/__init__.py", names)
        self.assertIn("tools/bedrock_usage.py", names)
        self.assertIn("utils/dates.py", names)  # tools import utils.dates
        self.assertFalse([n for n in names if "__pycache__" in n or n.endswith(".pyc")])
        self.assertNotIn("agent/app.py", names)  # runtime entrypoint stays out

    def test_package_is_deterministic(self):
        a = sh.build_lambda_package(install_deps=False)
        b = sh.build_lambda_package(install_deps=False)
        self.assertEqual(sh._code_sha256(a), sh._code_sha256(b))

    def test_code_sha256_is_base64(self):
        import base64
        digest = sh._code_sha256(b"abc")
        self.assertEqual(len(base64.b64decode(digest)), 32)


if __name__ == "__main__":
    unittest.main()
