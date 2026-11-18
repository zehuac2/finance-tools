"""Property tests for the rebalance command."""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from hypothesis import example, given
from hypothesis import strategies as st

from finance_tools.cli import buying_power, main
from finance_tools.rebalance import compute_plan
from support import (
    HOLDINGS,
    PUBLISHED_POSITIONS,
    PUBLISHED_PRICES,
    PUBLISHED_TARGETS,
    TARGETS,
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


if __name__ == "__main__":
    unittest.main()
