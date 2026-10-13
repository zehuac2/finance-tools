"""Buy-only planning adapted from my_agent_workspace.rebalance.

Distribute cash proportionally to target deficits, with whole-share sizing and
a $1 minimum order. CSV parsing and arithmetic use the standard library.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_EVEN
from pathlib import Path


CENT = Decimal("0.01")
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class Target:
    symbol: str
    name: str
    group: str
    fractional: bool
    target_pct: Decimal


def _number(value: str, label: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a number") from exc

    if not number.is_finite() or number < 0:
        raise ValueError(f"{label} must be finite and nonnegative")

    return number


def _read_csv(path: Path | str, required: set[str]) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        headers = [header.strip() for header in reader.fieldnames or []]

        if len(headers) != len(set(headers)):
            raise ValueError(f"{path}: duplicate column headers")

        missing = required - set(headers)
        if missing:
            raise ValueError(f"{path}: missing columns: {', '.join(sorted(missing))}")

        reader.fieldnames = headers
        rows = []
        symbols = set()

        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{path}: row {reader.line_num} has the wrong number of fields")

            row = {key: value.strip() for key, value in row.items()}
            symbol = row["Symbol"].upper()

            if not symbol or symbol in symbols:
                raise ValueError(f"{path}: row {reader.line_num} has an empty or duplicate Symbol")

            row["Symbol"] = symbol
            symbols.add(symbol)
            rows.append(row)

    return rows


def parse_composition(source: Path | str) -> list[Target]:
    """Read the target CSV using the original composition column names."""
    required = {"Symbol", "Name", "Group", "Fractional Shares", "Target Percentage"}

    try:
        rows = _read_csv(source, required)
    except csv.Error as exc:
        raise ValueError(f"{source}: invalid CSV: {exc}") from exc

    targets = []
    for row in rows:
        flag = row["Fractional Shares"].lower()
        if flag not in {"true", "yes", "false", "no"}:
            raise ValueError(f"{row['Symbol']}: Fractional Shares must be TRUE/FALSE or YES/NO")

        percentage = _number(row["Target Percentage"], f"{row['Symbol']}: Target Percentage")
        if percentage > HUNDRED:
            raise ValueError(f"{row['Symbol']}: Target Percentage must be at most 100")

        targets.append(
            Target(
                symbol=row["Symbol"],
                name=row["Name"],
                group=row["Group"],
                fractional=flag in {"true", "yes"},
                target_pct=percentage,
            )
        )

    total = sum((target.target_pct for target in targets), Decimal(0))
    if abs(total - HUNDRED) > CENT:
        raise ValueError(f"Target percentages must sum to 100, got {total}.")

    return targets


def parse_holdings(source: Path | str) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """Read quantities and prices; blank or zero prices represent missing quotes."""
    try:
        rows = _read_csv(source, {"Symbol", "Quantity", "Price"})
    except csv.Error as exc:
        raise ValueError(f"{source}: invalid CSV: {exc}") from exc

    positions = {}
    prices = {}

    for row in rows:
        symbol = row["Symbol"]
        quantity = _number(row["Quantity"], f"{symbol}: Quantity")
        price = _number(row["Price"] or "0", f"{symbol}: Price")

        if quantity > 0 and price <= 0:
            raise ValueError(f"{symbol}: a positive Price is required to value held shares")

        positions[symbol] = quantity
        prices[symbol] = price

    return positions, prices


def _round2(value: Decimal) -> float:
    return float(value.quantize(CENT, rounding=ROUND_HALF_EVEN))


def compute_plan(
    targets: list[Target],
    buying_power: Decimal,
    positions: dict[str, Decimal],
    prices: dict[str, Decimal],
) -> dict:
    """Compute the original proportional-deficit plan without exceeding cash.

    Holdings outside the target CSV contribute to portfolio value, but receive
    no orders. Missing quotes for unheld targets produce skipped entries.
    """
    total_value = buying_power + sum(
        (quantity * prices.get(symbol, Decimal(0)) for symbol, quantity in positions.items()),
        Decimal(0),
    )
    held_values = [
        positions.get(target.symbol, Decimal(0)) * prices.get(target.symbol, Decimal(0))
        for target in targets
    ]
    deficits = [
        max(Decimal(0), total_value * target.target_pct / HUNDRED - held)
        for target, held in zip(targets, held_values)
    ]
    total_deficit = sum(deficits, Decimal(0))
    ratio = min(Decimal(1), buying_power / total_deficit) if total_deficit else Decimal(0)

    orders = []
    skipped = []
    remaining = buying_power

    for target, held, deficit in zip(targets, held_values, deficits):
        if deficit <= 0:
            continue

        price = prices.get(target.symbol, Decimal(0))
        if price <= 0:
            skipped.append({"symbol": target.symbol, "reason": "no price"})
            continue

        allocation = deficit * ratio
        base = {
            "symbol": target.symbol,
            "group": target.group,
            "current_pct": _round2(held / total_value * HUNDRED) if total_value else 0.0,
            "target_pct": float(target.target_pct),
            "fractional": target.fractional,
        }

        if target.fractional:
            amount = min(
                allocation.quantize(CENT, rounding=ROUND_HALF_EVEN),
                remaining.quantize(CENT, rounding=ROUND_DOWN),
            )
            if amount < 1:
                continue

            order = {**base, "dollar_amount": f"{amount:.2f}", "est_cost": _round2(amount)}
        else:
            quantity = int(min(allocation, remaining) / price)
            amount = quantity * price
            if quantity < 1 or amount < 1:
                continue

            order = {**base, "quantity": quantity, "est_cost": _round2(amount)}

        orders.append(order)
        remaining -= amount

    return {
        "total_portfolio_value": _round2(total_value),
        "buying_power": _round2(buying_power),
        "allocated": _round2(buying_power - remaining),
        "unallocated": _round2(remaining),
        "orders": orders,
        "skipped": skipped,
    }


def render_summary(plan: dict) -> str:
    """Render the original summary with groups in CSV order."""
    lines = [
        f"Total portfolio value: ${plan['total_portfolio_value']:.2f}",
        f"Buying power:          ${plan['buying_power']:.2f}",
        f"To be allocated:       ${plan['allocated']:.2f}",
        f"Left as cash:          ${plan['unallocated']:.2f}",
        "",
    ]

    groups: dict[str, list[dict]] = {}
    for order in plan["orders"]:
        groups.setdefault(order["group"], []).append(order)

    for group, orders in groups.items():
        lines.append(group)

        for order in orders:
            if order["fractional"]:
                unit = f"${order['dollar_amount']}"
            else:
                unit = f"{order['quantity']} sh (~${order['est_cost']:.2f})"

            current = f"{order['current_pct']:.1f}".rjust(5)
            target = f"{order['target_pct']:g}".rjust(3)
            lines.append(f"  {order['symbol']:<6} {current}% → {target}%   buy {unit}")

    if plan["skipped"]:
        lines.extend(["", "Skipped:"])

        for skipped in plan["skipped"]:
            lines.append(f"  {skipped['symbol']} — {skipped['reason']}")

    return "\n".join(lines)
