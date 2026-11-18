"""Property tests for the rebalance command."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

from finance_tools.cli import buying_power, main
from finance_tools.rebalance import compute_plan, render_summary
from support import (
    FRESH,
    HOLDINGS,
    OUTSIDE,
    PUBLISHED,
    PUBLISHED_POSITIONS,
    PUBLISHED_PRICES,
    PUBLISHED_TARGETS,
    TARGETS,
    ZERO_CASH,
    Portfolio,
    portfolios,
)

@st.composite
def valid_cash_text(draw: st.DrawFn) -> str:
    cents = draw(st.integers(0, 1_000_000))
    whole, frac = divmod(cents, 100)
    choices = [f"{whole}.{frac:02d}", f"{whole}.{frac:02d}00"]
    if frac == 0:
        choices.extend([str(whole), f"{whole}."])
    if frac % 10 == 0:
        choices.append(f"{whole}.{frac // 10}")
    return draw(st.sampled_from(choices))


@st.composite
def invalid_cash(draw: st.DrawFn) -> str:
    kind = draw(st.sampled_from(["negative", "nonfinite", "subcent", "text"]))
    if kind == "negative":
        cents = draw(st.integers(1, 1_000_000))
        whole, frac = divmod(cents, 100)
        return draw(st.sampled_from([f"-{whole}.{frac:02d}", f"-{cents}"]))
    if kind == "nonfinite":
        return draw(st.sampled_from(["NaN", "nan", "Infinity", "-Infinity", "inf", "+inf", "sNaN"]))
    if kind == "subcent":
        cents = draw(st.integers(0, 100_000))
        milli = draw(st.integers(1, 9))
        whole, frac = divmod(cents, 100)
        return f"{whole}.{frac:02d}{milli}"
    return draw(st.sampled_from(["bad", "cash", "1.00.00", "--1", "None", ""]))


class CliPropertyTests(unittest.TestCase):
    def invoke(self, args: list[str]):
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(args)
        except SystemExit as exc:
            return exc, stdout.getvalue(), stderr.getvalue()
        return code, stdout.getvalue(), stderr.getvalue()

    def write_inputs(self, directory: Path, case: Portfolio) -> tuple[Path, Path]:
        targets_path = directory / "targets.csv"
        holdings_path = directory / "holdings.csv"
        target_header = ["Symbol", "Name", "Group", "Fractional Shares", "Target Percentage"]
        with targets_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=target_header, lineterminator="\n")
            writer.writeheader()
            for target in case.targets:
                writer.writerow(
                    {
                        "Symbol": target.symbol,
                        "Name": target.name,
                        "Group": target.group,
                        "Fractional Shares": "TRUE" if target.fractional else "FALSE",
                        "Target Percentage": format(target.target_pct, "f"),
                    }
                )

        holding_header = ["Symbol", "Quantity", "Price"]
        with holdings_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=holding_header, lineterminator="\n")
            writer.writeheader()
            symbols = list(dict.fromkeys([*case.position_map(), *case.price_map()]))
            positions = case.position_map()
            prices = case.price_map()
            for symbol in symbols:
                writer.writerow(
                    {
                        "Symbol": symbol,
                        "Quantity": format(positions.get(symbol, Decimal(0)), "f"),
                        "Price": format(prices.get(symbol, Decimal(0)), "f"),
                    }
                )
        return targets_path, holdings_path

    @given(case=portfolios())
    @example(case=PUBLISHED)
    @example(case=FRESH)
    @example(case=ZERO_CASH)
    @example(case=OUTSIDE)
    def test_json_and_text_agree_with_the_plan(self, case: Portfolio):
        with tempfile.TemporaryDirectory() as directory:
            targets_path, holdings_path = self.write_inputs(Path(directory), case)
            base = [
                "rebalance",
                "--targets",
                str(targets_path),
                "--holdings",
                str(holdings_path),
                "--buying-power",
                format(case.cash, "f"),
            ]
            json_code, json_out, json_err = self.invoke([*base, "--json"])
            text_code, text_out, text_err = self.invoke(base)

        self.assertEqual(json_code, 0)
        self.assertEqual(text_code, 0)
        self.assertEqual(json_err, "")
        self.assertEqual(text_err, "")
        plan = compute_plan(list(case.targets), case.cash, case.position_map(), case.price_map())
        payload = json.loads(json_out)
        summary = payload.pop("summary")
        self.assertEqual(payload, plan)
        self.assertEqual(summary, render_summary(plan))
        self.assertEqual(text_out, summary + "\n")
        self.assertNotIn("dollar_amount", json_out)
        self.assertNotIn("dollar_amount", text_out)
        for order in plan["orders"]:
            self.assertIn(f"buy {order['quantity']} sh", summary)

    @given(cash_text=valid_cash_text())
    @example(cash_text="1000")
    @example(cash_text="0")
    @example(cash_text="0.10")
    @example(cash_text="1000.00")
    def test_whole_cent_buying_power_is_accepted(self, cash_text: str):
        code, stdout, stderr = self.invoke(
            [
                "rebalance",
                "--targets",
                str(TARGETS),
                "--holdings",
                str(HOLDINGS),
                "--buying-power",
                cash_text,
                "--json",
            ]
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        cash = buying_power(cash_text)
        plan = compute_plan(
            list(PUBLISHED_TARGETS),
            cash,
            dict(PUBLISHED_POSITIONS),
            dict(PUBLISHED_PRICES),
        )
        payload.pop("summary")
        self.assertEqual(payload, plan)

    @given(case=invalid_cash())
    @example(case="-1")
    @example(case="NaN")
    @example(case="Infinity")
    @example(case="1.001")
    @example(case="bad")
    def test_invalid_buying_power_exits_cleanly(self, case: str):
        code, _stdout, stderr = self.invoke(
            [
                "rebalance",
                "--targets",
                str(TARGETS),
                "--holdings",
                str(HOLDINGS),
                "--buying-power",
                case,
            ]
        )
        self.assertIsInstance(code, SystemExit)
        self.assertEqual(code.code, 2)
        self.assertNotIn("Traceback", stderr)

    @given(which=st.sampled_from(["targets", "holdings"]))
    @example(which="targets")
    def test_missing_input_exits_cleanly(self, which: str):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "does-not-exist.csv")
            targets = missing if which == "targets" else str(TARGETS)
            holdings = missing if which == "holdings" else str(HOLDINGS)
            code, _stdout, stderr = self.invoke(
                [
                    "rebalance",
                    "--targets",
                    targets,
                    "--holdings",
                    holdings,
                    "--buying-power",
                    "1000",
                ]
            )

        self.assertIsInstance(code, SystemExit)
        self.assertEqual(code.code, 2)
        self.assertNotIn("Traceback", stderr)


if __name__ == "__main__":
    unittest.main()
