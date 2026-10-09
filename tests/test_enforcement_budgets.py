"""Tests for tools/enforcement.load_budget_config — the single budget source
shared by the meter, the agent tools, and the weekly report."""
import os
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

for _k in ("OPENROUTER_API_KEY", "OPENAI_ADMIN_API_KEY", "ANTHROPIC_ADMIN_API_KEY"):
    os.environ.setdefault(_k, "test-placeholder")

from models.normalization import normalize_principal_arn  # noqa: E402
from tools import enforcement  # noqa: E402

ALICE = "arn:aws:iam::123456789012:user/alice"
BOB = "arn:aws:iam::123456789012:role/bob"


class LoadBudgetConfigTests(unittest.TestCase):
    def test_returns_none_when_not_provisioned(self):
        with patch.object(enforcement, "_config", return_value=None), \
             patch.object(enforcement, "boto3") as boto3_mock:
            self.assertIsNone(enforcement.load_budget_config())
        boto3_mock.resource.assert_not_called()

    def test_reads_default_and_normalized_overrides_across_pages(self):
        table = MagicMock()
        table.scan.side_effect = [
            {"Items": [{"principal_arn": ALICE, "sk": "budget", "monthly_usd": Decimal("50")}],
             "LastEvaluatedKey": {"principal_arn": ALICE, "sk": "budget"}},
            {"Items": [{"principal_arn": BOB, "sk": "budget", "monthly_usd": Decimal("0")}]},
        ]
        with patch.object(enforcement, "_config", return_value=("token-cop-enforcement-usage", 200.0)), \
             patch.object(enforcement, "boto3") as boto3_mock:
            boto3_mock.resource.return_value.Table.return_value = table
            cfg = enforcement.load_budget_config()

        self.assertEqual(cfg["table"], "token-cop-enforcement-usage")
        self.assertEqual(cfg["default_usd"], 200.0)
        # Keys are scrubbed the same way attribution_breakdown scrubs its groups,
        # so the report can join on them; values are plain floats.
        self.assertEqual(cfg["overrides"], {
            normalize_principal_arn(ALICE): 50.0,
            normalize_principal_arn(BOB): 0.0,
        })
        self.assertEqual(table.scan.call_count, 2)
        self.assertIn("ExclusiveStartKey", table.scan.call_args_list[1].kwargs)
        for call in table.scan.call_args_list:
            self.assertEqual(call.kwargs["ExpressionAttributeValues"], {":sk": "budget"})


if __name__ == "__main__":
    unittest.main()
