"""Property tests for buy-only allocation."""

from __future__ import annotations

import unittest
from decimal import Decimal

from hypothesis import example, given

from finance_tools.rebalance import Target, compute_plan
from support import (
    DROP_ZERO_SHARE,
    FLOOR_UBER,
    FLOOR_UBER_HELD,
    FRACTIONAL_PAIR,
    PUBLISHED,
    Portfolio,
    portfolios,
    purchases,
    round_money,
    single_asset_portfolios,
)

class PlanPropertyTests(unittest.TestCase):
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
