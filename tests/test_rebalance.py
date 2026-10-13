"""Allocation and CSV regressions adapted from the original rebalance tests."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from finance_tools.cli import main
from finance_tools.rebalance import Target, compute_plan, parse_composition, parse_holdings


ROOT = Path(__file__).resolve().parents[1]
TARGETS = ROOT / "examples" / "targets.csv"
HOLDINGS = ROOT / "examples" / "holdings.csv"


class RebalanceTests(unittest.TestCase):
    def setUp(self):
        self.targets = parse_composition(TARGETS)
        self.positions, self.prices = parse_holdings(HOLDINGS)

    def plan(self, cash="1000", positions=None, prices=None):
        return compute_plan(
            self.targets,
            Decimal(cash),
            self.positions if positions is None else positions,
            self.prices if prices is None else prices,
        )

    def test_original_happy_path(self):
        plan = self.plan()
        self.assertEqual(plan["total_portfolio_value"], 1540)
        self.assertEqual(plan["allocated"], 830)
        self.assertEqual(plan["unallocated"], 170)
        self.assertEqual(plan["orders"][0]["dollar_amount"], "230.00")
        self.assertEqual(plan["orders"][1]["quantity"], 5)
        self.assertEqual(plan["orders"][2]["quantity"], 1)

    def test_overweight_position_is_never_sold(self):
        plan = self.plan("100", positions={"VOO": Decimal(10)})
        self.assertNotIn("VOO", [order["symbol"] for order in plan["orders"]])
        self.assertTrue(all(order["est_cost"] > 0 for order in plan["orders"]))

    def test_whole_shares_and_minimum_order_leave_cash(self):
        plan = self.plan("1", positions={})
        self.assertEqual(plan["orders"], [])
        self.assertEqual(plan["unallocated"], 1)

    def test_missing_price_is_skipped(self):
        prices = {key: price for key, price in self.prices.items() if key != "TSLA"}
        plan = self.plan(prices=prices)
        self.assertEqual(plan["skipped"], [{"symbol": "TSLA", "reason": "no price"}])

    def test_fresh_account_tracks_weights(self):
        plan = self.plan(positions={}, prices=dict.fromkeys(self.prices, Decimal(50)))
        self.assertEqual([order["est_cost"] for order in plan["orders"]], [500, 250, 250])

    def test_zero_cash(self):
        plan = self.plan("0")
        self.assertEqual(plan["orders"], [])
        self.assertEqual(plan["allocated"], 0)

    def test_rounding_does_not_overspend(self):
        targets = [
            Target("A", "", "", True, Decimal("50")),
            Target("B", "", "", True, Decimal("50")),
        ]
        plan = compute_plan(targets, Decimal("3.03"), {}, {"A": Decimal(1), "B": Decimal(1)})
        self.assertEqual([order["dollar_amount"] for order in plan["orders"]], ["1.52", "1.51"])
        self.assertEqual(plan["allocated"], 3.03)
        self.assertEqual(plan["unallocated"], 0)

    def test_outside_holdings_contribute_to_total_value(self):
        positions = {**self.positions, "OTHER": Decimal(2)}
        prices = {**self.prices, "OTHER": Decimal(100)}
        plan = self.plan(positions=positions, prices=prices)
        self.assertEqual(plan["total_portfolio_value"], 1740)
        self.assertNotIn("OTHER", [order["symbol"] for order in plan["orders"]])


class CsvTests(unittest.TestCase):
    def parse(self, text, parser):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text(text, encoding="utf-8")
            return parser(path)

    def test_whitespace_bom_and_symbol_normalization(self):
        positions, prices = self.parse(
            "\ufeff Symbol , Quantity , Price \n voo , 1.5 , 540 \n", parse_holdings
        )
        self.assertEqual(positions, {"VOO": Decimal("1.5")})
        self.assertEqual(prices, {"VOO": Decimal(540)})

    def test_invalid_holdings(self):
        cases = [
            ("Symbol,Quantity\nVOO,1\n", "missing columns"),
            ("Symbol,Quantity,Price\nVOO,1,540\nvoo,0,540\n", "duplicate Symbol"),
            ("Symbol,Quantity,Price\n,1,540\n", "empty"),
            ("Symbol,Quantity,Price\nVOO,-1,540\n", "nonnegative"),
            ("Symbol,Quantity,Price\nVOO,NaN,540\n", "finite"),
            ("Symbol,Quantity,Price\nVOO,1,Infinity\n", "finite"),
            ("Symbol,Quantity,Price\nVOO,1,\n", "positive Price"),
            ("Symbol,Quantity,Price\nVOO,1\n", "number of fields"),
            ("Symbol,Quantity,Price\nVOO,1,540,extra\n", "number of fields"),
            ("Symbol,Quantity,Price,Price\nVOO,1,540,540\n", "duplicate column"),
        ]

        for text, message in cases:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, message):
                self.parse(text, parse_holdings)

    def test_invalid_targets(self):
        header = "Symbol,Name,Group,Fractional Shares,Target Percentage\n"
        cases = [
            ("VOO,Vanguard,Core,TRUE,90\n", "sum to 100"),
            ("VOO,Vanguard,Core,maybe,100\n", "TRUE/FALSE"),
            ("VOO,Vanguard,Core,TRUE,NaN\n", "finite"),
            ("VOO,Vanguard,Core,TRUE,-100\n", "nonnegative"),
            ("VOO,Vanguard,Core,TRUE,101\n", "at most 100"),
        ]

        for row, message in cases:
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, message):
                self.parse(header + row, parse_composition)

    def test_empty_holdings_and_blank_unheld_price(self):
        self.assertEqual(self.parse("Symbol,Quantity,Price\n", parse_holdings), ({}, {}))
        positions, prices = self.parse("Symbol,Quantity,Price\nVOO,0,\n", parse_holdings)
        self.assertEqual(positions["VOO"], 0)
        self.assertEqual(prices["VOO"], 0)


class CliTests(unittest.TestCase):
    def args(self):
        return [
            "rebalance", "--targets", str(TARGETS), "--holdings", str(HOLDINGS),
            "--buying-power", "1000",
        ]

    def test_json_and_text_output(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(self.args() + ["--json"]), 0)

        plan = json.loads(output.getvalue())
        self.assertEqual(plan["allocated"], 830)
        self.assertIn("Core ETF", plan["summary"])

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(self.args()), 0)

        self.assertEqual(output.getvalue().strip(), plan["summary"].strip())

    def test_invalid_cash_and_missing_file_have_clean_errors(self):
        for value in ["-1", "NaN", "Infinity", "1.001", "bad"]:
            args = self.args()
            args[-1] = value

            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(args)

                self.assertEqual(raised.exception.code, 2)

        args = self.args()
        args[2] = str(ROOT / "does-not-exist.csv")
        output = io.StringIO()

        with contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as raised:
            main(args)

        self.assertEqual(raised.exception.code, 2)
        self.assertNotIn("Traceback", output.getvalue())


if __name__ == "__main__":
    unittest.main()
