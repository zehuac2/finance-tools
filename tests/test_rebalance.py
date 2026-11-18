"""Property tests for buy-only rebalancing.

The invariants follow the allocation rules: portfolio value is cash plus every
holding, cash is split in proportion to positive target deficits, and each buy
is the greatest whole-share quantity that stays within that dollar allocation
and the cash still available. Orders below one share or one dollar are omitted
and are not redistributed.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from finance_tools.cli import buying_power, main
from finance_tools.rebalance import (
    Target,
    compute_plan,
    parse_composition,
    parse_holdings,
    render_summary,
)


def _install_hypothesis_profile() -> None:
    """Use a deterministic profile so CI does not depend on Hypothesis' seed.

    The built-in ``ci`` profile already derandomizes and disables the deadline
    when Hypothesis notices a CI environment. Re-registering it pins those
    choices, and a bounded example count, in this suite. ``HYPOTHESIS_PROFILE``
    still selects another registered profile when it is set.
    """

    settings.register_profile(
        "ci",
        derandomize=True,
        deadline=None,
        max_examples=100,
        database=None,
        print_blob=True,
        suppress_health_check=[HealthCheck.too_slow],
    )
    settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


_install_hypothesis_profile()

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ROOT / "examples" / "targets.csv"
HOLDINGS = ROOT / "examples" / "holdings.csv"
CENT = Decimal("0.01")
HUNDRED = Decimal(100)
ONE = Decimal(1)
NAME_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 -'&.,"
SYMBOL_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def round_money(value: Decimal) -> float:
    return float(value.quantize(CENT, rounding=ROUND_HALF_EVEN))


def portfolio_value(
    cash: Decimal, positions: dict[str, Decimal], prices: dict[str, Decimal]
) -> Decimal:
    return cash + sum(
        (quantity * prices.get(symbol, Decimal(0)) for symbol, quantity in positions.items()),
        Decimal(0),
    )


def allocation_math(
    targets: list[Target] | tuple[Target, ...],
    cash: Decimal,
    positions: dict[str, Decimal],
    prices: dict[str, Decimal],
) -> tuple[Decimal, list[Decimal], list[Decimal], list[Decimal]]:
    """Dollar deficits and proportional allocations, before share rounding."""

    total = portfolio_value(cash, positions, prices)
    held_values: list[Decimal] = []
    deficits: list[Decimal] = []
    for target in targets:
        held = positions.get(target.symbol, Decimal(0)) * prices.get(target.symbol, Decimal(0))
        held_values.append(held)
        deficits.append(max(Decimal(0), total * target.target_pct / HUNDRED - held))

    total_deficit = sum(deficits, Decimal(0))
    ratio = min(ONE, cash / total_deficit) if total_deficit else Decimal(0)
    allocations = [deficit * ratio for deficit in deficits]
    return total, held_values, deficits, allocations


def reference_plan(
    targets: list[Target] | tuple[Target, ...],
    cash: Decimal,
    positions: dict[str, Decimal],
    prices: dict[str, Decimal],
) -> dict:
    """A direct reading of the buy-only rule, used as an independent oracle."""

    total, held_values, deficits, allocations = allocation_math(targets, cash, positions, prices)
    orders = []
    skipped = []
    remaining = cash

    for target, held, deficit, allocation in zip(targets, held_values, deficits, allocations):
        if deficit <= 0:
            continue

        price = prices.get(target.symbol, Decimal(0))
        if price <= 0:
            skipped.append({"symbol": target.symbol, "reason": "no price"})
            continue

        affordable = min(allocation, remaining)
        quantity = int(affordable // price)
        amount = quantity * price
        if quantity < 1 or amount < 1:
            continue

        orders.append(
            {
                "symbol": target.symbol,
                "group": target.group,
                "current_pct": round_money(held / total * HUNDRED) if total else 0.0,
                "target_pct": float(target.target_pct),
                "fractional": target.fractional,
                "quantity": quantity,
                "est_cost": round_money(amount),
            }
        )
        remaining -= amount

    return {
        "total_portfolio_value": round_money(total),
        "buying_power": round_money(cash),
        "allocated": round_money(cash - remaining),
        "unallocated": round_money(remaining),
        "orders": orders,
        "skipped": skipped,
    }


def order_line(order: dict) -> str:
    unit = f"{order['quantity']} sh (~${order['est_cost']:.2f})"
    current = f"{order['current_pct']:.1f}".rjust(5)
    target = f"{order['target_pct']:g}".rjust(3)
    return f"  {order['symbol']:<6} {current}% → {target}%   buy {unit}"


def purchases(plan: dict) -> tuple:
    """Economic fields. Names, groups, and the fractional flag are presentation."""

    return (
        plan["total_portfolio_value"],
        plan["buying_power"],
        plan["allocated"],
        plan["unallocated"],
        tuple(
            (
                order["symbol"],
                order["quantity"],
                order["est_cost"],
                order["current_pct"],
                order["target_pct"],
            )
            for order in plan["orders"]
        ),
        tuple((row["symbol"], row["reason"]) for row in plan["skipped"]),
    )


def spent(plan: dict, prices: dict[str, Decimal]) -> Decimal:
    return sum(
        (Decimal(order["quantity"]) * prices[order["symbol"]] for order in plan["orders"]),
        Decimal(0),
    )


def after_buys(positions: dict[str, Decimal], plan: dict) -> dict[str, Decimal]:
    updated = dict(positions)
    for order in plan["orders"]:
        symbol = order["symbol"]
        updated[symbol] = updated.get(symbol, Decimal(0)) + Decimal(order["quantity"])
    return updated


@dataclass(frozen=True)
class Portfolio:
    targets: tuple[Target, ...]
    cash: Decimal
    positions: tuple[tuple[str, Decimal], ...]
    prices: tuple[tuple[str, Decimal], ...]

    def position_map(self) -> dict[str, Decimal]:
        return dict(self.positions)

    def price_map(self) -> dict[str, Decimal]:
        return dict(self.prices)


def portfolio(targets, cash, positions, prices) -> Portfolio:
    if isinstance(positions, dict):
        positions = tuple(positions.items())
    if isinstance(prices, dict):
        prices = tuple(prices.items())
    return Portfolio(tuple(targets), Decimal(cash), tuple(positions), tuple(prices))


PUBLISHED_TARGETS = tuple(parse_composition(TARGETS))
PUBLISHED_POSITIONS, PUBLISHED_PRICES = parse_holdings(HOLDINGS)
PUBLISHED = portfolio(PUBLISHED_TARGETS, "1000", PUBLISHED_POSITIONS, PUBLISHED_PRICES)
OVERWEIGHT = portfolio(PUBLISHED_TARGETS, "100", {"VOO": Decimal(10)}, PUBLISHED_PRICES)
CASH_ONE = portfolio(PUBLISHED_TARGETS, "1", {}, PUBLISHED_PRICES)
ZERO_CASH = portfolio(PUBLISHED_TARGETS, "0", PUBLISHED_POSITIONS, PUBLISHED_PRICES)
MISSING_PRICE = portfolio(
    PUBLISHED_TARGETS,
    "1000",
    PUBLISHED_POSITIONS,
    {symbol: price for symbol, price in PUBLISHED_PRICES.items() if symbol != "TSLA"},
)
FRESH = portfolio(
    PUBLISHED_TARGETS,
    "1000",
    {},
    {symbol: Decimal(50) for symbol in PUBLISHED_PRICES},
)
OUTSIDE = portfolio(
    PUBLISHED_TARGETS,
    "1000",
    {**PUBLISHED_POSITIONS, "OTHER": Decimal(2)},
    {**PUBLISHED_PRICES, "OTHER": Decimal(100)},
)
FLOOR_UBER = portfolio(
    [Target("UBER", "", "Bets", False, HUNDRED)],
    "200",
    {},
    {"UBER": Decimal("70")},
)
FLOOR_UBER_HELD = portfolio(
    [Target("UBER", "", "Bets", False, HUNDRED)],
    "200",
    {"UBER": Decimal(5)},
    {"UBER": Decimal("70")},
)
FRACTIONAL_PAIR = portfolio(
    [
        Target("A", "", "Core", True, Decimal(50)),
        Target("B", "", "Core", True, Decimal(50)),
    ],
    "10",
    {},
    {"A": Decimal(3), "B": Decimal(3)},
)
DROP_ZERO_SHARE = portfolio(
    [
        Target("A", "", "Core", True, Decimal(50)),
        Target("B", "", "Bets", False, Decimal(50)),
    ],
    "100",
    {},
    {"A": Decimal(80), "B": Decimal(10)},
)


def _cents(minimum: int, maximum: int):
    return st.integers(minimum, maximum).map(lambda cents: Decimal(cents) / HUNDRED)


def _units(minimum: int, maximum: int, scale: int):
    return st.integers(minimum, maximum).map(lambda units: Decimal(units) / Decimal(scale))


@st.composite
def percentage_partition(draw: st.DrawFn, count: int) -> list[Decimal]:
    if count == 1:
        return [HUNDRED]

    cuts = sorted(
        draw(
            st.lists(
                st.integers(0, 10_000),
                min_size=count - 1,
                max_size=count - 1,
            )
        )
    )
    bounds = [0, *cuts, 10_000]
    return [Decimal(bounds[index + 1] - bounds[index]) / HUNDRED for index in range(count)]


@st.composite
def portfolios(draw: st.DrawFn) -> Portfolio:
    target_count = draw(st.integers(1, 4))
    outside_count = draw(st.integers(0, 2))
    symbols = draw(
        st.lists(
            st.text(alphabet=SYMBOL_ALPHABET, min_size=1, max_size=5),
            min_size=target_count + outside_count,
            max_size=target_count + outside_count,
            unique=True,
        )
    )
    weights = draw(percentage_partition(target_count))
    targets = []
    for symbol, weight in zip(symbols[:target_count], weights):
        name = draw(st.text(alphabet=NAME_ALPHABET, max_size=16)).strip()
        group = draw(st.text(alphabet=NAME_ALPHABET, max_size=12)).strip()
        targets.append(Target(symbol, name, group, draw(st.booleans()), weight))

    positions: dict[str, Decimal] = {}
    prices: dict[str, Decimal] = {}
    for symbol in symbols[:target_count]:
        quantity = draw(_units(0, 100_000, 1000))
        positions[symbol] = quantity
        if quantity > 0:
            prices[symbol] = draw(_units(1, 2_000_000, 10_000))
        elif draw(st.integers(0, 4)) == 0:
            prices[symbol] = Decimal(0)
        else:
            prices[symbol] = draw(_units(1, 2_000_000, 10_000))

    for symbol in symbols[target_count:]:
        positions[symbol] = draw(_units(1, 100_000, 1000))
        prices[symbol] = draw(_units(1, 2_000_000, 10_000))

    return Portfolio(
        tuple(targets),
        draw(_cents(0, 5_000_000)),
        tuple(positions.items()),
        tuple(prices.items()),
    )


@st.composite
def single_asset_portfolios(draw: st.DrawFn) -> Portfolio:
    symbol = draw(st.text(alphabet=SYMBOL_ALPHABET, min_size=1, max_size=5))
    quantity = draw(_units(0, 100_000, 1000))
    if quantity == 0 and draw(st.integers(0, 4)) == 0:
        price = Decimal(0)
    else:
        price = draw(_units(1, 2_000_000, 10_000))
    return portfolio(
        [Target(symbol, "", "Core", draw(st.booleans()), HUNDRED)],
        draw(_cents(0, 5_000_000)),
        {symbol: quantity},
        {symbol: price},
    )


@dataclass(frozen=True)
class HoldingsCase:
    text: str
    positions: tuple[tuple[str, Decimal], ...]
    prices: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class TargetsCase:
    text: str
    targets: tuple[Target, ...]


@dataclass(frozen=True)
class InvalidCsv:
    text: str
    message: str


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


@st.composite
def invalid_holdings(draw: st.DrawFn) -> InvalidCsv:
    kind = draw(
        st.sampled_from(
            [
                "missing_column",
                "duplicate_symbol",
                "empty_symbol",
                "negative",
                "nonfinite",
                "held_without_price",
                "short_row",
                "long_row",
                "duplicate_header",
                "not_a_number",
                "broken_csv",
            ]
        )
    )
    symbol = draw(st.text(alphabet=SYMBOL_ALPHABET, min_size=1, max_size=4))

    if kind == "missing_column":
        column = draw(st.sampled_from(["Symbol", "Quantity", "Price"]))
        headers = [name for name in ("Symbol", "Quantity", "Price") if name != column]
        return InvalidCsv(f"{','.join(headers)}\n{symbol},1\n", "missing columns")

    if kind == "duplicate_symbol":
        return InvalidCsv(
            f"Symbol,Quantity,Price\n{symbol},1,10\n{_mix_case(draw, symbol)},0,10\n",
            "duplicate Symbol",
        )

    if kind == "empty_symbol":
        blank = draw(st.sampled_from(["", " ", "  "]))
        return InvalidCsv(f"Symbol,Quantity,Price\n{blank},1,10\n", "empty")

    if kind == "negative":
        amount = format(Decimal(draw(st.integers(1, 10_000))) / HUNDRED, "f")
        if draw(st.booleans()):
            return InvalidCsv(
                f"Symbol,Quantity,Price\n{symbol},-{amount},10\n",
                "Quantity must be finite and nonnegative",
            )
        return InvalidCsv(
            f"Symbol,Quantity,Price\n{symbol},1,-{amount}\n",
            "Price must be finite and nonnegative",
        )

    if kind == "nonfinite":
        token = draw(st.sampled_from(["NaN", "Infinity", "-Infinity", "inf", "+inf", "sNaN"]))
        if draw(st.booleans()):
            return InvalidCsv(f"Symbol,Quantity,Price\n{symbol},{token},10\n", "finite")
        return InvalidCsv(f"Symbol,Quantity,Price\n{symbol},1,{token}\n", "finite")

    if kind == "held_without_price":
        price = draw(st.sampled_from(["", "0", "0.00"]))
        return InvalidCsv(f"Symbol,Quantity,Price\n{symbol},1,{price}\n", "positive Price")

    if kind == "short_row":
        return InvalidCsv(f"Symbol,Quantity,Price\n{symbol},1\n", "number of fields")

    if kind == "long_row":
        return InvalidCsv(f"Symbol,Quantity,Price\n{symbol},1,10,extra\n", "number of fields")

    if kind == "duplicate_header":
        header = draw(
            st.sampled_from(
                ["Symbol,Quantity,Price,Price", "Symbol,Quantity,Price, Price"]
            )
        )
        return InvalidCsv(f"{header}\n{symbol},1,10,10\n", "duplicate column")

    if kind == "not_a_number":
        return InvalidCsv(f"Symbol,Quantity,Price\n{symbol},abc,10\n", "must be a number")

    return InvalidCsv(f'Symbol,Quantity,Price\n"{symbol},1,10\n', "invalid CSV")


@st.composite
def invalid_targets(draw: st.DrawFn) -> InvalidCsv:
    kind = draw(
        st.sampled_from(
            [
                "bad_sum",
                "bad_flag",
                "nonfinite",
                "negative",
                "above_100",
                "duplicate_symbol",
                "empty_symbol",
                "missing_column",
                "duplicate_header",
                "not_a_number",
            ]
        )
    )
    header = "Symbol,Name,Group,Fractional Shares,Target Percentage"
    symbol = draw(st.text(alphabet=SYMBOL_ALPHABET, min_size=1, max_size=4))

    if kind == "bad_sum":
        percentage = draw(st.sampled_from(["0", "90", "99.98", "50"]))
        return InvalidCsv(f"{header}\n{symbol},Name,Core,TRUE,{percentage}\n", "sum to 100")

    if kind == "bad_flag":
        flag = draw(st.sampled_from(["maybe", "1", "TRUEE", "nope", ""]))
        return InvalidCsv(f"{header}\n{symbol},Name,Core,{flag},100\n", "TRUE/FALSE")

    if kind == "nonfinite":
        token = draw(st.sampled_from(["NaN", "Infinity", "-Infinity", "inf"]))
        return InvalidCsv(f"{header}\n{symbol},Name,Core,TRUE,{token}\n", "finite")

    if kind == "negative":
        percentage = draw(st.sampled_from(["-1", "-0.01", "-100"]))
        return InvalidCsv(f"{header}\n{symbol},Name,Core,TRUE,{percentage}\n", "nonnegative")

    if kind == "above_100":
        percentage = draw(st.sampled_from(["100.01", "101", "150"]))
        return InvalidCsv(f"{header}\n{symbol},Name,Core,TRUE,{percentage}\n", "at most 100")

    if kind == "duplicate_symbol":
        other = _mix_case(draw, symbol)
        return InvalidCsv(
            f"{header}\n{symbol},Name,Core,TRUE,40\n{other},Name,Core,FALSE,60\n",
            "duplicate Symbol",
        )

    if kind == "empty_symbol":
        return InvalidCsv(f"{header}\n ,Name,Core,TRUE,100\n", "empty")

    if kind == "missing_column":
        column = draw(
            st.sampled_from(["Symbol", "Name", "Group", "Fractional Shares", "Target Percentage"])
        )
        headers = [name for name in header.split(",") if name != column]
        return InvalidCsv(f"{','.join(headers)}\n{symbol},Name,Core,TRUE,100\n", "missing columns")

    if kind == "duplicate_header":
        return InvalidCsv(f"{header},Name\n{symbol},Name,Core,TRUE,100,Extra\n", "duplicate column")

    return InvalidCsv(f"{header}\n{symbol},Name,Core,TRUE,abc\n", "must be a number")


class ParseHelper(unittest.TestCase):
    def parse(self, text: str, parser):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text(text, encoding="utf-8")
            return parser(path)


class HypothesisProfileTests(unittest.TestCase):
    def test_ci_profile_is_deterministic(self):
        profile = settings.get_profile("ci")
        self.assertTrue(profile.derandomize)
        self.assertIsNone(profile.deadline)
        self.assertEqual(profile.max_examples, 100)
        self.assertIn(HealthCheck.too_slow, profile.suppress_health_check)


class PlanPropertyTests(unittest.TestCase):
    maxDiff = None

    @given(case=portfolios())
    @example(case=PUBLISHED)
    @example(case=OVERWEIGHT)
    @example(case=CASH_ONE)
    @example(case=ZERO_CASH)
    @example(case=MISSING_PRICE)
    @example(case=FRESH)
    @example(case=OUTSIDE)
    @example(case=FLOOR_UBER)
    @example(case=FRACTIONAL_PAIR)
    @example(case=DROP_ZERO_SHARE)
    def test_plan_matches_deficit_rule(self, case: Portfolio):
        positions = case.position_map()
        prices = case.price_map()
        before_positions = dict(positions)
        before_prices = dict(prices)
        plan = compute_plan(list(case.targets), case.cash, positions, prices)

        self.assertEqual(positions, before_positions)
        self.assertEqual(prices, before_prices)
        self.assertEqual(
            plan,
            reference_plan(case.targets, case.cash, positions, prices),
        )
        self.assertEqual(
            plan,
            compute_plan(list(case.targets), case.cash, dict(positions), dict(prices)),
        )

        total, _held_values, deficits, allocations = allocation_math(
            case.targets, case.cash, positions, prices
        )
        self.assertEqual(plan["total_portfolio_value"], round_money(total))
        self.assertEqual(plan["buying_power"], round_money(case.cash))
        order_symbols = [order["symbol"] for order in plan["orders"]]
        target_order = [target.symbol for target in case.targets]
        outside = set(positions).difference(target_order)
        self.assertEqual(order_symbols, sorted(order_symbols, key=target_order.index))
        self.assertTrue(set(order_symbols).isdisjoint(outside))
        self.assertNotIn("dollar_amount", json.dumps(plan))

        raw_spent = spent(plan, prices)
        self.assertGreaterEqual(case.cash - raw_spent, 0)
        self.assertEqual(plan["allocated"], round_money(raw_spent))
        self.assertEqual(plan["unallocated"], round_money(case.cash - raw_spent))
        self.assertEqual(
            portfolio_value(case.cash, positions, prices),
            portfolio_value(case.cash - raw_spent, after_buys(positions, plan), prices),
        )

        if case.cash == 0:
            self.assertEqual(plan["orders"], [])
            self.assertEqual(plan["allocated"], 0)
            self.assertEqual(plan["unallocated"], 0)

        targets_by_symbol = {target.symbol: target for target in case.targets}
        for order in plan["orders"]:
            target = targets_by_symbol[order["symbol"]]
            price = prices[order["symbol"]]
            cost = Decimal(order["quantity"]) * price
            held = positions.get(order["symbol"], Decimal(0)) * price
            self.assertIsInstance(order["quantity"], int)
            self.assertGreaterEqual(order["quantity"], 1)
            self.assertGreaterEqual(cost, ONE)
            self.assertLessEqual(cost, case.cash)
            self.assertEqual(order["est_cost"], round_money(cost))
            self.assertGreater(order["est_cost"], 0)
            self.assertEqual(order["group"], target.group)
            self.assertEqual(order["fractional"], target.fractional)
            self.assertEqual(order["target_pct"], float(target.target_pct))
            self.assertEqual(
                order["current_pct"],
                round_money(held / total * HUNDRED) if total else 0.0,
            )
            self.assertGreaterEqual(order["current_pct"], 0)
            self.assertLessEqual(order["current_pct"], 100)
            self.assertNotIn("dollar_amount", order)

        self.assertEqual(
            plan["skipped"],
            [
                {"symbol": target.symbol, "reason": "no price"}
                for target, deficit in zip(case.targets, deficits)
                if deficit > 0 and prices.get(target.symbol, Decimal(0)) <= 0
            ],
        )
        ordered = set(order_symbols)
        for target, deficit, allocation in zip(case.targets, deficits, allocations):
            if target.symbol in ordered:
                self.assertGreater(deficit, 0)
                quantity = next(
                    order["quantity"]
                    for order in plan["orders"]
                    if order["symbol"] == target.symbol
                )
                self.assertLessEqual(Decimal(quantity) * prices[target.symbol], allocation)

        summary = render_summary(plan)
        lines = summary.splitlines()
        self.assertEqual(lines[0], f"Total portfolio value: ${plan['total_portfolio_value']:.2f}")
        self.assertEqual(lines[1], f"Buying power:          ${plan['buying_power']:.2f}")
        self.assertEqual(lines[2], f"To be allocated:       ${plan['allocated']:.2f}")
        self.assertEqual(lines[3], f"Left as cash:          ${plan['unallocated']:.2f}")
        self.assertNotIn("dollar_amount", summary)
        for order in plan["orders"]:
            line = order_line(order)
            self.assertIn(line, lines)
            if order["group"]:
                self.assertLess(lines.index(order["group"]), lines.index(line))
        self.assertEqual("Skipped:" in lines, bool(plan["skipped"]))
        for row in plan["skipped"]:
            self.assertIn(f"  {row['symbol']} — {row['reason']}", lines)

        groups = []
        for order in plan["orders"]:
            if order["group"] and order["group"] not in groups:
                groups.append(order["group"])
        group_lines = [lines.index(group) for group in groups]
        self.assertEqual(group_lines, sorted(group_lines))

    @given(case=single_asset_portfolios())
    @example(case=FLOOR_UBER)
    @example(case=FLOOR_UBER_HELD)
    def test_single_asset_buys_floor_of_cash_over_price(self, case: Portfolio):
        target = case.targets[0]
        price = case.price_map().get(target.symbol, Decimal(0))
        plan = compute_plan(list(case.targets), case.cash, case.position_map(), case.price_map())

        if price <= 0:
            self.assertEqual(plan["orders"], [])
            if case.cash > 0:
                self.assertEqual(plan["skipped"], [{"symbol": target.symbol, "reason": "no price"}])
            else:
                self.assertEqual(plan["skipped"], [])
            return

        quantity = int(case.cash // price)
        amount = quantity * price
        if quantity < 1 or amount < 1:
            self.assertEqual(plan["orders"], [])
            self.assertEqual(plan["skipped"], [])
            self.assertEqual(plan["allocated"], 0)
            self.assertEqual(plan["unallocated"], round_money(case.cash))
            return

        self.assertEqual(len(plan["orders"]), 1)
        self.assertEqual(plan["orders"][0]["symbol"], target.symbol)
        self.assertEqual(plan["orders"][0]["quantity"], quantity)
        self.assertEqual(plan["orders"][0]["est_cost"], round_money(amount))
        self.assertEqual(plan["allocated"], round_money(amount))
        self.assertEqual(plan["unallocated"], round_money(case.cash - amount))
        self.assertEqual(plan["skipped"], [])

    @given(case=portfolios(), factor=st.integers(2, 10))
    @example(case=PUBLISHED, factor=2)
    @example(case=FRACTIONAL_PAIR, factor=10)
    @example(case=DROP_ZERO_SHARE, factor=3)
    def test_scaling_prices_and_cash_preserves_share_counts(self, case: Portfolio, factor: int):
        positions = case.position_map()
        prices = case.price_map()
        original = compute_plan(list(case.targets), case.cash, positions, prices)
        scaled = compute_plan(
            list(case.targets),
            case.cash * factor,
            positions,
            {symbol: price * factor for symbol, price in prices.items()},
        )
        original_quantities = {order["symbol"]: order["quantity"] for order in original["orders"]}
        scaled_quantities = {order["symbol"]: order["quantity"] for order in scaled["orders"]}

        for symbol, quantity in original_quantities.items():
            self.assertEqual(scaled_quantities[symbol], quantity)
        for symbol, quantity in scaled_quantities.items():
            self.assertGreaterEqual(quantity, original_quantities.get(symbol, 0))

    @given(case=portfolios(), extra_cents=st.integers(1, 500_000))
    @example(case=PUBLISHED, extra_cents=100)
    @example(case=OVERWEIGHT, extra_cents=1)
    @example(case=CASH_ONE, extra_cents=50)
    def test_more_cash_never_reduces_share_counts(self, case: Portfolio, extra_cents: int):
        positions = case.position_map()
        prices = case.price_map()
        smaller = compute_plan(list(case.targets), case.cash, positions, prices)
        larger = compute_plan(
            list(case.targets),
            case.cash + Decimal(extra_cents) / HUNDRED,
            positions,
            prices,
        )
        smaller_quantities = {order["symbol"]: order["quantity"] for order in smaller["orders"]}
        larger_quantities = {order["symbol"]: order["quantity"] for order in larger["orders"]}

        for symbol, quantity in smaller_quantities.items():
            self.assertGreaterEqual(larger_quantities.get(symbol, 0), quantity)
        self.assertGreaterEqual(spent(larger, prices), spent(smaller, prices))

    @given(case=portfolios())
    @example(case=PUBLISHED)
    @example(case=FRACTIONAL_PAIR)
    @example(case=DROP_ZERO_SHARE)
    def test_names_groups_and_fractional_flag_do_not_change_buys(self, case: Portfolio):
        flipped = tuple(
            Target(
                target.symbol,
                f"{target.name} revised",
                "Alt" if target.group != "Alt" else "Core",
                not target.fractional,
                target.target_pct,
            )
            for target in case.targets
        )
        positions = case.position_map()
        prices = case.price_map()
        original = compute_plan(list(case.targets), case.cash, positions, prices)
        revised = compute_plan(list(flipped), case.cash, positions, prices)

        self.assertEqual(purchases(original), purchases(revised))
        revised_targets = {target.symbol: target for target in flipped}
        for order in revised["orders"]:
            self.assertEqual(order["fractional"], revised_targets[order["symbol"]].fractional)
            self.assertEqual(order["group"], revised_targets[order["symbol"]].group)
            self.assertIsInstance(order["quantity"], int)


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

    @given(case=invalid_holdings())
    @example(case=InvalidCsv("Symbol,Quantity\nVOO,1\n", "missing columns"))
    @example(case=InvalidCsv("Symbol,Quantity,Price\nVOO,1,540\nvoo,0,540\n", "duplicate Symbol"))
    @example(case=InvalidCsv("Symbol,Quantity,Price\n,1,540\n", "empty"))
    @example(case=InvalidCsv("Symbol,Quantity,Price\nVOO,-1,540\n", "nonnegative"))
    @example(case=InvalidCsv("Symbol,Quantity,Price\nVOO,NaN,540\n", "finite"))
    @example(case=InvalidCsv("Symbol,Quantity,Price\nVOO,1,Infinity\n", "finite"))
    @example(case=InvalidCsv("Symbol,Quantity,Price\nVOO,1,\n", "positive Price"))
    @example(case=InvalidCsv("Symbol,Quantity,Price\nVOO,1\n", "number of fields"))
    @example(case=InvalidCsv("Symbol,Quantity,Price\nVOO,1,540,extra\n", "number of fields"))
    @example(case=InvalidCsv("Symbol,Quantity,Price,Price\nVOO,1,540,540\n", "duplicate column"))
    def test_invalid_holdings_are_rejected(self, case: InvalidCsv):
        with self.assertRaisesRegex(ValueError, case.message):
            self.parse(case.text, parse_holdings)

    @given(case=invalid_targets())
    @example(
        case=InvalidCsv(
            "Symbol,Name,Group,Fractional Shares,Target Percentage\nVOO,Vanguard,Core,TRUE,90\n",
            "sum to 100",
        )
    )
    @example(
        case=InvalidCsv(
            "Symbol,Name,Group,Fractional Shares,Target Percentage\nVOO,Vanguard,Core,maybe,100\n",
            "TRUE/FALSE",
        )
    )
    @example(
        case=InvalidCsv(
            "Symbol,Name,Group,Fractional Shares,Target Percentage\nVOO,Vanguard,Core,TRUE,NaN\n",
            "finite",
        )
    )
    @example(
        case=InvalidCsv(
            "Symbol,Name,Group,Fractional Shares,Target Percentage\nVOO,Vanguard,Core,TRUE,-100\n",
            "nonnegative",
        )
    )
    @example(
        case=InvalidCsv(
            "Symbol,Name,Group,Fractional Shares,Target Percentage\nVOO,Vanguard,Core,TRUE,101\n",
            "at most 100",
        )
    )
    def test_invalid_targets_are_rejected(self, case: InvalidCsv):
        with self.assertRaisesRegex(ValueError, case.message):
            self.parse(case.text, parse_composition)

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


class CliPropertyTests(ParseHelper):
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


class PublishedExampleTests(unittest.TestCase):
    def test_published_example_portfolio(self):
        """Lock the sample CSVs to their known order list.

        examples/targets.csv and examples/holdings.csv with $1000 of buying
        power are the portfolio shown in the README. This checks those dollar
        totals and the UBER and TSLA share counts directly.
        """

        plan = compute_plan(
            list(PUBLISHED_TARGETS),
            Decimal("1000"),
            dict(PUBLISHED_POSITIONS),
            dict(PUBLISHED_PRICES),
        )
        self.assertEqual(plan["total_portfolio_value"], 1540)
        self.assertEqual(plan["allocated"], 600)
        self.assertEqual(plan["unallocated"], 400)
        self.assertEqual([order["symbol"] for order in plan["orders"]], ["UBER", "TSLA"])
        self.assertEqual([order["quantity"] for order in plan["orders"]], [5, 1])
        self.assertEqual([order["est_cost"] for order in plan["orders"]], [350, 250])
        self.assertTrue(all("dollar_amount" not in order for order in plan["orders"]))

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "rebalance",
                    "--targets",
                    str(TARGETS),
                    "--holdings",
                    str(HOLDINGS),
                    "--buying-power",
                    "1000",
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["allocated"], 600)
        self.assertEqual(payload["orders"][0]["quantity"], 5)
        self.assertIn("buy 5 sh", payload["summary"])
        self.assertNotIn("dollar_amount", payload["summary"])

        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            self.assertEqual(
                main(
                    [
                        "rebalance",
                        "--targets",
                        str(TARGETS),
                        "--holdings",
                        str(HOLDINGS),
                        "--buying-power",
                        "1000",
                    ]
                ),
                0,
            )
        self.assertEqual(text.getvalue().strip(), payload["summary"].strip())


if __name__ == "__main__":
    unittest.main()
