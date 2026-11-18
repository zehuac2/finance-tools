"""Shared portfolios, oracles, and strategies for the rebalance tests."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from hypothesis import strategies as st

from finance_tools.rebalance import Target, parse_composition, parse_holdings

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
