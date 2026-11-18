"""Property tests for buy-only allocation."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal

from hypothesis import example, given
from hypothesis import strategies as st

from finance_tools.rebalance import Target, compute_plan, render_summary
from support import (
    CASH_ONE,
    DROP_ZERO_SHARE,
    FLOOR_UBER,
    FLOOR_UBER_HELD,
    FRACTIONAL_PAIR,
    FRESH,
    HUNDRED,
    MISSING_PRICE,
    ONE,
    OUTSIDE,
    OVERWEIGHT,
    PUBLISHED,
    ZERO_CASH,
    Portfolio,
    after_buys,
    allocation_math,
    order_line,
    portfolio_value,
    portfolios,
    purchases,
    reference_plan,
    round_money,
    single_asset_portfolios,
    spent,
)

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



if __name__ == "__main__":
    unittest.main()
