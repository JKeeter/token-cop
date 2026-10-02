"""Tests for the Cost Explorer attribution breakdown service-filter logic.

Third-party models (Anthropic Claude) bill through AWS Marketplace under the
model provider's service name, not "Amazon Bedrock" — verified live 2026-10-02
(CE under the single-service filter saw $0.02 of a $510 CloudWatch week).
These tests pin the dynamic service discovery that closes that gap.
"""
import json
import unittest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from tools.attribution import _attribution_breakdown_impl, _bedrock_service_values


def _dimension_page(values, token=None):
    page = {"DimensionValues": [{"Value": v} for v in values]}
    if token:
        page["NextPageToken"] = token
    return page


def _cost_response():
    return {
        "ResultsByTime": [
            {
                "Groups": [
                    {
                        "Keys": ["USW2-ClaudeFable5-output-tokens"],
                        "Metrics": {
                            "UnblendedCost": {"Amount": "12.5"},
                            "UsageQuantity": {"Amount": "100"},
                        },
                    }
                ]
            }
        ]
    }


class BedrockServiceDiscoveryTests(unittest.TestCase):
    def test_collects_bedrock_and_provider_services(self):
        ce = MagicMock()
        ce.get_dimension_values.return_value = _dimension_page(
            [
                "AWS Lambda",
                "Amazon Bedrock",
                "Amazon Bedrock AgentCore",
                "Amazon Bedrock Service",
                "Claude Sonnet 5.5 (Amazon Bedrock Edition)",
            ]
        )
        services, caveats = _bedrock_service_values(ce, "2026-09-01", "2026-10-01")
        self.assertEqual(
            services,
            [
                "Amazon Bedrock",
                "Amazon Bedrock AgentCore",
                "Amazon Bedrock Service",
                "Claude Sonnet 5.5 (Amazon Bedrock Edition)",
            ],
        )
        self.assertEqual(caveats, [])

    def test_warns_when_no_provider_service_present(self):
        ce = MagicMock()
        ce.get_dimension_values.return_value = _dimension_page(
            ["Amazon Bedrock", "Amazon Bedrock AgentCore", "AWS Lambda"]
        )
        services, caveats = _bedrock_service_values(ce, "2026-09-01", "2026-10-01")
        self.assertIn("Amazon Bedrock", services)
        self.assertEqual(len(caveats), 1)
        self.assertIn("Marketplace", caveats[0])
        self.assertIn("bedrock_usage", caveats[0])

    def test_follows_pagination(self):
        ce = MagicMock()
        ce.get_dimension_values.side_effect = [
            _dimension_page(["Amazon Bedrock"], token="page2"),
            _dimension_page(["Anthropic"]),
        ]
        services, _ = _bedrock_service_values(ce, "2026-09-01", "2026-10-01")
        self.assertEqual(services, ["Amazon Bedrock", "Anthropic"])
        self.assertEqual(ce.get_dimension_values.call_count, 2)
        self.assertEqual(
            ce.get_dimension_values.call_args_list[1].kwargs["NextPageToken"], "page2"
        )

    def test_always_includes_amazon_bedrock(self):
        ce = MagicMock()
        ce.get_dimension_values.return_value = _dimension_page(["Anthropic"])
        services, _ = _bedrock_service_values(ce, "2026-09-01", "2026-10-01")
        self.assertIn("Amazon Bedrock", services)

    def test_falls_back_on_client_error(self):
        ce = MagicMock()
        ce.get_dimension_values.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied"}}, "GetDimensionValues"
        )
        services, caveats = _bedrock_service_values(ce, "2026-09-01", "2026-10-01")
        self.assertEqual(services, ["Amazon Bedrock"])
        self.assertEqual(len(caveats), 1)
        self.assertIn("may be missing", caveats[0])


class AttributionBreakdownFilterTests(unittest.TestCase):
    @patch("tools.attribution.boto3")
    def test_cost_filter_uses_discovered_services(self, mock_boto3):
        ce = MagicMock()
        mock_boto3.client.return_value = ce
        ce.get_dimension_values.return_value = _dimension_page(
            ["Amazon Bedrock", "Claude Fable 5 (Amazon Bedrock Edition)"]
        )
        ce.get_cost_and_usage.return_value = _cost_response()

        out = json.loads(
            _attribution_breakdown_impl("usage_type", "2026-09-25", "2026-10-02")
        )

        sent_filter = ce.get_cost_and_usage.call_args.kwargs["Filter"]
        self.assertEqual(
            sent_filter["Dimensions"]["Values"],
            ["Amazon Bedrock", "Claude Fable 5 (Amazon Bedrock Edition)"],
        )
        self.assertEqual(
            out["services_included"],
            ["Amazon Bedrock", "Claude Fable 5 (Amazon Bedrock Edition)"],
        )
        self.assertEqual(out["total_cost_usd"], 12.5)
        self.assertEqual(out["caveats"], [])

    @patch("tools.attribution.boto3")
    def test_missing_provider_caveat_reaches_result(self, mock_boto3):
        ce = MagicMock()
        mock_boto3.client.return_value = ce
        ce.get_dimension_values.return_value = _dimension_page(["Amazon Bedrock"])
        ce.get_cost_and_usage.return_value = _cost_response()

        out = json.loads(
            _attribution_breakdown_impl("usage_type", "2026-09-25", "2026-10-02")
        )
        self.assertTrue(any("Marketplace" in c for c in out["caveats"]))


if __name__ == "__main__":
    unittest.main()
