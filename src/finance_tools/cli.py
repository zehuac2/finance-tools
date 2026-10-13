"""The finance-tools command-line interface."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from finance_tools.rebalance import (
    compute_plan,
    parse_composition,
    parse_holdings,
    render_summary,
)


def buying_power(value: str) -> Decimal:
    """Parse a finite, nonnegative cash amount in whole cents."""
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("buying power must be a number") from exc

    if not amount.is_finite() or amount < 0:
        raise argparse.ArgumentTypeError("buying power must be finite and nonnegative")

    if amount != amount.quantize(Decimal("0.01")):
        raise argparse.ArgumentTypeError("buying power must be in whole cents")

    return amount


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="finance-tools", description="Command-line tools for portfolio planning."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    rebalance = commands.add_parser(
        "rebalance", help="Compute a buy-only plan from target and holdings CSV files."
    )
    rebalance.add_argument("--targets", type=Path, required=True, help="Target allocation CSV.")
    rebalance.add_argument("--holdings", type=Path, required=True, help="Quantity and price CSV.")
    rebalance.add_argument("--buying-power", type=buying_power, required=True, help="Available cash.")
    rebalance.add_argument("--json", action="store_true", help="Print the plan as JSON.")
    args = parser.parse_args(argv)

    try:
        targets = parse_composition(args.targets)
        positions, prices = parse_holdings(args.holdings)
        plan = compute_plan(targets, args.buying_power, positions, prices)
    except (OSError, ValueError) as exc:
        rebalance.error(str(exc))

    summary = render_summary(plan)
    if args.json:
        print(json.dumps({**plan, "summary": summary}, indent=2, allow_nan=False))
    else:
        print(summary)

    return 0
