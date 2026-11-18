"""Property tests for target and holdings CSV parsing."""

from __future__ import annotations

import csv
import io
import tempfile
import unittest
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

from finance_tools.rebalance import Target, parse_composition, parse_holdings
from support import CENT, HUNDRED, NAME_ALPHABET, SYMBOL_ALPHABET, _units, percentage_partition

@dataclass(frozen=True)
class HoldingsCase:
    text: str
    positions: tuple[tuple[str, Decimal], ...]
    prices: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class TargetsCase:
    text: str
    targets: tuple[Target, ...]


def _mix_case(draw: st.DrawFn, text: str) -> str:
    return "".join(character.lower() if draw(st.booleans()) else character for character in text)


def _pad(draw: st.DrawFn, text: str) -> str:
    return f"{' ' * draw(st.integers(0, 2))}{text}{' ' * draw(st.integers(0, 2))}"


def _csv_text(headers: list[str], rows: list[dict[str, str]], bom: bool) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    text = buffer.getvalue()
    return f"\ufeff{text}" if bom else text


def _flag_text(draw: st.DrawFn, fractional: bool) -> str:
    word = draw(st.sampled_from(("true", "yes") if fractional else ("false", "no")))
    return _mix_case(draw, word)


@st.composite
def holdings_cases(draw: st.DrawFn) -> HoldingsCase:
    count = draw(st.integers(0, 6))
    symbols = draw(
        st.lists(
            st.text(alphabet=SYMBOL_ALPHABET, min_size=1, max_size=5),
            min_size=count,
            max_size=count,
            unique=True,
        )
    )
    positions: dict[str, Decimal] = {}
    prices: dict[str, Decimal] = {}
    rows = []
    column_order = draw(st.permutations(["Symbol", "Quantity", "Price"]))
    headers = [_pad(draw, header) for header in column_order]

    for symbol in symbols:
        quantity = draw(_units(0, 100_000, 1000))
        if quantity > 0:
            price = draw(_units(1, 2_000_000, 10_000))
            price_text = format(price, "f")
        else:
            price = Decimal(0) if draw(st.booleans()) else draw(_units(1, 2_000_000, 10_000))
            price_text = "" if price == 0 and draw(st.booleans()) else format(price, "f")

        positions[symbol] = quantity
        prices[symbol] = price
        raw = {
            "Symbol": _pad(draw, _mix_case(draw, symbol)),
            "Quantity": _pad(draw, format(quantity, "f")),
            "Price": _pad(draw, price_text),
        }
        rows.append({header: raw[column] for header, column in zip(headers, column_order)})

    return HoldingsCase(
        _csv_text(headers, rows, draw(st.booleans())),
        tuple(positions.items()),
        tuple(prices.items()),
    )


@st.composite
def targets_cases(draw: st.DrawFn) -> TargetsCase:
    count = draw(st.integers(1, 5))
    symbols = draw(
        st.lists(
            st.text(alphabet=SYMBOL_ALPHABET, min_size=1, max_size=5),
            min_size=count,
            max_size=count,
            unique=True,
        )
    )
    weights = draw(percentage_partition(count))
    column_order = draw(
        st.permutations(["Symbol", "Name", "Group", "Fractional Shares", "Target Percentage"])
    )
    headers = [_pad(draw, header) for header in column_order]
    targets = []
    rows = []

    for symbol, weight in zip(symbols, weights):
        name = draw(st.text(alphabet=NAME_ALPHABET, max_size=16)).strip()
        group = draw(st.text(alphabet=NAME_ALPHABET, max_size=12)).strip()
        fractional = draw(st.booleans())
        targets.append(Target(symbol, name, group, fractional, weight))
        raw = {
            "Symbol": _pad(draw, _mix_case(draw, symbol)),
            "Name": _pad(draw, name),
            "Group": _pad(draw, group),
            "Fractional Shares": _pad(draw, _flag_text(draw, fractional)),
            "Target Percentage": _pad(draw, format(weight, "f")),
        }
        rows.append({header: raw[column] for header, column in zip(headers, column_order)})

    return TargetsCase(_csv_text(headers, rows, draw(st.booleans())), tuple(targets))


class ParseHelper(unittest.TestCase):
    def parse(self, text: str, parser):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text(text, encoding="utf-8")
            return parser(path)


class ParserPropertyTests(ParseHelper):
    @given(case=holdings_cases())
    @example(
        case=HoldingsCase(
            "\ufeff Symbol , Quantity , Price \n voo , 1.5 , 540 \n",
            (("VOO", Decimal("1.5")),),
            (("VOO", Decimal(540)),),
        )
    )
    @example(case=HoldingsCase("Symbol,Quantity,Price\n", (), ()))
    @example(
        case=HoldingsCase(
            "Symbol,Quantity,Price\nVOO,0,\n",
            (("VOO", Decimal(0)),),
            (("VOO", Decimal(0)),),
        )
    )
    def test_holdings_round_trip(self, case: HoldingsCase):
        positions, prices = self.parse(case.text, parse_holdings)
        self.assertEqual(positions, dict(case.positions))
        self.assertEqual(prices, dict(case.prices))
        again_positions, again_prices = self.parse(case.text, parse_holdings)
        self.assertEqual(again_positions, positions)
        self.assertEqual(again_prices, prices)

    @given(case=targets_cases())
    @example(
        case=TargetsCase(
            "Symbol,Name,Group,Fractional Shares,Target Percentage\n"
            "VOO,Vanguard,Core,Yes,60\n"
            "UBER,Uber,Bets,No,40\n",
            (
                Target("VOO", "Vanguard", "Core", True, Decimal(60)),
                Target("UBER", "Uber", "Bets", False, Decimal(40)),
            ),
        )
    )
    @example(
        case=TargetsCase(
            "Symbol,Name,Group,Fractional Shares,Target Percentage\nAAA,, ,YES,100\n",
            (Target("AAA", "", "", True, HUNDRED),),
        )
    )
    @example(
        case=TargetsCase(
            "Symbol,Name,Group,Fractional Shares,Target Percentage\n"
            "AAA,A,Core,FALSE,0\n"
            "BBB,B,Core,true,100\n",
            (
                Target("AAA", "A", "Core", False, Decimal(0)),
                Target("BBB", "B", "Core", True, HUNDRED),
            ),
        )
    )
    def test_targets_round_trip(self, case: TargetsCase):
        self.assertEqual(tuple(self.parse(case.text, parse_composition)), case.targets)
        self.assertEqual(
            tuple(self.parse(case.text, parse_composition)),
            tuple(self.parse(case.text, parse_composition)),
        )

    @given(shift_thousandths=st.integers(-200, 200))
    @example(shift_thousandths=0)
    @example(shift_thousandths=10)
    @example(shift_thousandths=-10)
    @example(shift_thousandths=11)
    @example(shift_thousandths=-11)
    def test_target_percentages_must_sum_to_within_one_cent(self, shift_thousandths: int):
        shift = Decimal(shift_thousandths) / Decimal(1000)
        first = Decimal(40)
        second = Decimal(60) + shift
        text = (
            "Symbol,Name,Group,Fractional Shares,Target Percentage\n"
            f"AAA,A,Core,TRUE,{first}\n"
            f"BBB,B,Core,FALSE,{second}\n"
        )

        if abs(shift) <= CENT:
            targets = self.parse(text, parse_composition)
            self.assertEqual([target.target_pct for target in targets], [first, second])
        else:
            with self.assertRaisesRegex(ValueError, "sum to 100"):
                self.parse(text, parse_composition)



if __name__ == "__main__":
    unittest.main()
