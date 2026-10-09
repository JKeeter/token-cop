"""Tests for the weekly report's "Principals Over Budget" source selection.

The enforcement subsystem is the system of record for budgets. When it is
provisioned the report must use it (default budget applies to everyone,
per-principal overrides win); the legacy TOKEN_COP_PRINCIPAL_BUDGETS env var
is only a fallback for installs without enforcement.
"""
import json
import os
import unittest
from unittest.mock import patch

for _k in ("OPENROUTER_API_KEY", "OPENAI_ADMIN_API_KEY", "ANTHROPIC_ADMIN_API_KEY"):
    os.environ.setdefault(_k, "test-placeholder")

from scripts import generate_report as report  # noqa: E402

A, B, C = "arn:aws:iam::<ACCT>:user/alice", "arn:aws:iam::<ACCT>:role/bob", "arn:aws:iam::<ACCT>:user/carol"
GROUPS = [
    {"principal": A, "cost": 60.0, "percentage": 18.75},
    {"principal": B, "cost": 250.0, "percentage": 78.1},
    {"principal": C, "cost": 10.0, "percentage": 3.1},
]
ENFORCEMENT_CFG = {"table": "t", "default_usd": 200.0, "overrides": {A: 50.0}}


class OverBudgetSourceTests(unittest.TestCase):
    def test_enforcement_table_wins_over_env_var(self):
        env = {"TOKEN_COP_PRINCIPAL_BUDGETS": json.dumps({A: 1000, B: 1000, C: 1000})}
        with patch.dict(os.environ, env), \
             patch("tools.enforcement.load_budget_config", return_value=ENFORCEMENT_CFG):
            over = report._compute_over_budget(GROUPS)
        self.assertEqual(
            [(r["principal"], r["budget_usd"], r["overage_usd"]) for r in over],
            [(B, 200.0, 50.0), (A, 50.0, 10.0)],   # default applies to B; override to A; sorted by overage
        )

    def test_env_var_used_when_enforcement_not_provisioned(self):
        env = {"TOKEN_COP_PRINCIPAL_BUDGETS": json.dumps({A: 30})}
        with patch.dict(os.environ, env), \
             patch("tools.enforcement.load_budget_config", return_value=None):
            over = report._compute_over_budget(GROUPS)
        # No default budget in legacy mode, so B (no entry) is not flagged.
        self.assertEqual([(r["principal"], r["budget_usd"], r["overage_usd"]) for r in over], [(A, 30.0, 30.0)])

    def test_enforcement_errors_fall_back_to_env_var(self):
        env = {"TOKEN_COP_PRINCIPAL_BUDGETS": json.dumps({A: 30})}
        with patch.dict(os.environ, env), \
             patch("tools.enforcement.load_budget_config", side_effect=RuntimeError("ssm down")):
            over = report._compute_over_budget(GROUPS)
        self.assertEqual([r["principal"] for r in over], [A])

    def test_no_budgets_anywhere_yields_empty(self):
        env = {k: v for k, v in os.environ.items() if k != "TOKEN_COP_PRINCIPAL_BUDGETS"}
        with patch.dict(os.environ, env, clear=True), \
             patch("tools.enforcement.load_budget_config", return_value=None):
            self.assertEqual(report._compute_over_budget(GROUPS), [])

    def test_budget_source_is_reported(self):
        with patch("tools.enforcement.load_budget_config", return_value=ENFORCEMENT_CFG):
            self.assertEqual(report._load_principal_budgets()["source"], "enforcement")
        with patch("tools.enforcement.load_budget_config", return_value=None), \
             patch.dict(os.environ, {"TOKEN_COP_PRINCIPAL_BUDGETS": json.dumps({A: 1})}):
            self.assertEqual(report._load_principal_budgets()["source"], "env")


if __name__ == "__main__":
    unittest.main()
