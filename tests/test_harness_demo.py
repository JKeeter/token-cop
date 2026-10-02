"""Pure-function tests for scripts/harness_demo.py.

No AWS calls: only formatting/compare helpers, the act registry, and argparse.
"""
import contextlib
import io
import unittest

from scripts import harness_demo as hd


class FormattingTests(unittest.TestCase):
    def test_fmt_usd_cents_and_subcent(self):
        self.assertEqual(hd.fmt_usd(12.3), "$12.30")
        self.assertEqual(hd.fmt_usd(1234.5), "$1,234.50")
        self.assertEqual(hd.fmt_usd(0.0042), "$0.0042")
        self.assertEqual(hd.fmt_usd(0), "$0.00")
        self.assertEqual(hd.fmt_usd(None), "n/a")
        self.assertEqual(hd.fmt_usd(-0.5), "$-0.50")

    def test_fmt_int(self):
        self.assertEqual(hd.fmt_int(0), "0")
        self.assertEqual(hd.fmt_int(1234567), "1,234,567")
        self.assertEqual(hd.fmt_int(12.9), "12")
        self.assertEqual(hd.fmt_int(None), "n/a")

    def test_pct_delta(self):
        self.assertEqual(hd.pct_delta(100, 50), -50.0)
        self.assertEqual(hd.pct_delta(50, 100), 100.0)
        self.assertEqual(hd.pct_delta(3, 4), 33.3)
        self.assertIsNone(hd.pct_delta(0, 10))
        self.assertIsNone(hd.pct_delta(None, 10))
        self.assertIsNone(hd.pct_delta(10, None))

    def test_fmt_pct(self):
        self.assertEqual(hd.fmt_pct(-50.0), "-50.0%")
        self.assertEqual(hd.fmt_pct(12.34), "+12.3%")
        self.assertEqual(hd.fmt_pct(0.0), "0.0%")
        self.assertEqual(hd.fmt_pct(None), "n/a")

    def test_truncate(self):
        self.assertEqual(hd.truncate("short", 10), "short")
        self.assertEqual(hd.truncate("", 10), "")
        self.assertEqual(hd.truncate(None, 10), "")
        out = hd.truncate("a" * 1500, 1200)
        self.assertTrue(out.startswith("a" * 1200))
        self.assertIn("300 more chars", out)
        # Trailing whitespace at the cut is stripped before the marker.
        self.assertEqual(hd.truncate("abc   def", 5), "abc\n... [4 more chars]")

    def test_truncate_default_limit(self):
        self.assertEqual(hd.truncate("x" * hd.ANSWER_TRUNCATE), "x" * hd.ANSWER_TRUNCATE)
        self.assertIn("more chars", hd.truncate("x" * (hd.ANSWER_TRUNCATE + 1)))


class TableTests(unittest.TestCase):
    def test_compare_rows_aligns_columns(self):
        table = hd.compare_rows(["a", "bbb"], [["1", "2"], ["333", "4"]], indent="")
        lines = table.splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0], "a   | bbb")
        self.assertEqual(lines[1], "----+----")
        self.assertEqual(lines[2], "1   | 2  ")
        self.assertEqual(lines[3], "333 | 4  ")

    def test_compare_rows_stringifies_and_indents(self):
        table = hd.compare_rows(["n"], [[42]])
        self.assertTrue(all(line.startswith("  ") for line in table.splitlines()))
        self.assertIn("42", table)

    def test_compare_rows_empty(self):
        table = hd.compare_rows(["x", "y"], [], indent="")
        self.assertEqual(table.splitlines(), ["x | y", "--+--"])

    def test_endpoint_rows_sorted_with_target_fallback(self):
        eps = [
            {"endpointName": "PROD", "liveVersion": "1", "targetVersion": "2", "status": "UPDATING"},
            {"endpointName": "DEFAULT", "liveVersion": "2", "status": "READY"},
        ]
        rows = hd.endpoint_rows(eps)
        self.assertEqual(rows[0], ["DEFAULT", "2", "2", "READY"])
        self.assertEqual(rows[1], ["PROD", "1", "2", "UPDATING"])

    def test_endpoint_rows_missing_fields(self):
        self.assertEqual(hd.endpoint_rows([{}]), [["?", "?", "?", "?"]])


class OverheadMathTests(unittest.TestCase):
    def test_model_calls_is_tools_plus_one(self):
        self.assertEqual(hd.model_calls([]), 1)
        self.assertEqual(hd.model_calls(None), 1)
        self.assertEqual(hd.model_calls(["bedrock_usage", "aggregate_usage"]), 3)

    def test_per_call_overhead_never_divides_by_zero(self):
        self.assertEqual(hd.per_call_overhead(2700, 3), 900.0)
        self.assertEqual(hd.per_call_overhead(2700, 0), 2700.0)
        self.assertEqual(hd.per_call_overhead(-100, 2), -50.0)
        self.assertEqual(hd.per_call_overhead(1000, 3), 333.3)


class ParsingTests(unittest.TestCase):
    def test_parse_skip_acts(self):
        self.assertEqual(hd.parse_skip_acts("5,6"), {5, 6})
        self.assertEqual(hd.parse_skip_acts(" 1, 3 ,,"), {1, 3})
        self.assertEqual(hd.parse_skip_acts(""), set())
        self.assertEqual(hd.parse_skip_acts(None), set())
        with self.assertRaises(ValueError):
            hd.parse_skip_acts("5,x")

    def test_harness_id_from_arn(self):
        arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:harness/token_cop_harness-AbC123"
        self.assertEqual(hd.harness_id_from_arn(arn), "token_cop_harness-AbC123")
        self.assertEqual(hd.harness_id_from_arn(arn + "/2"), "token_cop_harness-AbC123")
        self.assertEqual(hd.harness_id_from_arn("plain-id"), "plain-id")

    def test_diff_excerpt_truncates(self):
        ours = "\n".join(f"line {i}" for i in range(100))
        theirs = "\n".join(f"LINE {i}" for i in range(100))
        out = hd.diff_excerpt(ours, theirs, "ours.py", "theirs.py", lines=10)
        lines = out.splitlines()
        self.assertEqual(lines[0], "--- ours.py")
        self.assertEqual(lines[1], "+++ theirs.py")
        self.assertEqual(len(lines), 11)
        self.assertIn("truncated at 10 lines", lines[-1])

    def test_diff_excerpt_identical_is_empty(self):
        self.assertEqual(hd.diff_excerpt("same", "same", "a", "b"), "")

    def test_find_exported_python_missing_dir(self):
        self.assertIsNone(hd.find_exported_python("/nonexistent/path/for/harness/demo"))

    def test_resolve_model_id_prefers_cli(self):
        self.assertEqual(hd.resolve_model_id("us.amazon.nova-lite-v1:0"), "us.amazon.nova-lite-v1:0")
        # Without override we get the Sonnet id (live constant or module fallback).
        self.assertIn("sonnet", hd.resolve_model_id(None))


class RegistryTests(unittest.TestCase):
    def test_six_acts_registered_in_order(self):
        self.assertEqual(sorted(hd.ACTS), [1, 2, 3, 4, 5, 6])
        for n, (title, fn) in hd.ACTS.items():
            self.assertTrue(title)
            self.assertTrue(callable(fn))
            self.assertEqual(fn.__name__, f"act{n}")

    def test_select_acts(self):
        self.assertEqual(hd.select_acts(None, set()), [1, 2, 3, 4, 5, 6])
        self.assertEqual(hd.select_acts(None, {5, 6}), [1, 2, 3, 4])
        self.assertEqual(hd.select_acts(3, {3}), [3])  # explicit --act wins over skip

    def test_polish_model_constants(self):
        self.assertIn(hd.POLISH_MODEL_ID, hd.POLISH_MODEL_CHOICES)
        self.assertIn("us.amazon.nova-lite-v1:0", hd.POLISH_MODEL_CHOICES)
        self.assertTrue(hd.POLISH_MODEL_ID.startswith("us.anthropic.claude-haiku"))

    def test_record_merges_into_summary(self):
        hd.SUMMARY["acts"].pop("99", None)
        hd.record(99, a=1)
        hd.record(99, b=2)
        self.assertEqual(hd.SUMMARY["acts"]["99"], {"a": 1, "b": 2})
        hd.SUMMARY["acts"].pop("99")


class ArgparseTests(unittest.TestCase):
    def test_defaults(self):
        args = hd.build_parser().parse_args([])
        self.assertIsNone(args.act)
        self.assertEqual(args.skip_acts, "")
        self.assertFalse(args.no_pause)
        self.assertFalse(args.json)
        self.assertIsNone(args.model)
        self.assertEqual(args.polish_model, hd.POLISH_MODEL_ID)

    def test_flags(self):
        args = hd.build_parser().parse_args([
            "--act", "4", "--skip-acts", "5,6", "--no-pause", "--json",
            "--model", "us.anthropic.claude-sonnet-4-20250514-v1:0",
            "--polish-model", "us.amazon.nova-lite-v1:0",
        ])
        self.assertEqual(args.act, 4)
        self.assertEqual(hd.parse_skip_acts(args.skip_acts), {5, 6})
        self.assertTrue(args.no_pause and args.json)
        self.assertEqual(args.polish_model, "us.amazon.nova-lite-v1:0")

    def test_invalid_act_and_polish_model_rejected(self):
        parser = hd.build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--act", "7"])
            with self.assertRaises(SystemExit):
                parser.parse_args(["--polish-model", "gpt-4o"])

    def test_help_lists_every_act(self):
        text = hd.build_parser().format_help()
        for n, (title, _) in hd.ACTS.items():
            self.assertIn(f"{n}. {title}", text)


if __name__ == "__main__":
    unittest.main()
