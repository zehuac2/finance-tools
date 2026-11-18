"""Hand-computed examples for named rebalance scenarios."""

from __future__ import annotations

import unittest
from decimal import ROUND_HALF_EVEN, Decimal

from finance_tools.rebalance import compute_plan
from support import PUBLISHED_POSITIONS, PUBLISHED_PRICES, PUBLISHED_TARGETS


def _cents(amount: float) -> int:
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


class PlanExampleTests(unittest.TestCase):
    def assert_plan_invariants(self, plan: dict, outside: tuple[str, ...] = ()) -> None:
        target_symbols = [target.symbol for target in PUBLISHED_TARGETS]
        order_symbols = [order["symbol"] for order in plan["orders"]]
        self.assertEqual(
            order_symbols,
            [symbol for symbol in target_symbols if symbol in order_symbols],
        )
        self.assertTrue(set(order_symbols).isdisjoint(outside))

        spent = sum(_cents(order["est_cost"]) for order in plan["orders"])
        self.assertLessEqual(spent, _cents(plan["buying_power"]))
        self.assertEqual(
            _cents(plan["allocated"]) + _cents(plan["unallocated"]),
            _cents(plan["buying_power"]),
        )

    def plan(self, cash: str, positions: dict, prices: dict) -> dict:
        return compute_plan(list(PUBLISHED_TARGETS), Decimal(cash), positions, prices)

    def test_overweight_voo_is_not_bought(self):
        """Ten VOO shares are $5400 of a $5500 portfolio, above the 50% target.

        The $100 of cash splits into $50 for UBER and $50 for TSLA. Neither
        buys a whole share, and VOO gets no order.
        """

        plan = self.plan("100", {"VOO": Decimal(10)}, dict(PUBLISHED_PRICES))
        self.assertEqual(plan["total_portfolio_value"], 5500)
        self.assertEqual(plan["buying_power"], 100)
        self.assertEqual(plan["orders"], [])
        self.assertEqual(plan["skipped"], [])
        self.assertEqual(plan["allocated"], 0)
        self.assertEqual(plan["unallocated"], 100)
        self.assert_plan_invariants(plan)

    def test_one_dollar_cash_buys_nothing(self):
        """$1 on an empty account stays unallocated.

        The 50/25/25 split is $0.50, $0.25, and $0.25, each below one share
        and below the $1 minimum.
        """

        plan = self.plan("1", {}, dict(PUBLISHED_PRICES))
        self.assertEqual(plan["total_portfolio_value"], 1)
        self.assertEqual(plan["buying_power"], 1)
        self.assertEqual(plan["orders"], [])
        self.assertEqual(plan["skipped"], [])
        self.assertEqual(plan["allocated"], 0)
        self.assertEqual(plan["unallocated"], 1)
        self.assert_plan_invariants(plan)

    def test_zero_cash_buys_nothing(self):
        """No buying power produces no orders. The VOO share is still valued."""

        plan = self.plan("0", dict(PUBLISHED_POSITIONS), dict(PUBLISHED_PRICES))
        self.assertEqual(plan["total_portfolio_value"], 540)
        self.assertEqual(plan["buying_power"], 0)
        self.assertEqual(plan["orders"], [])
        self.assertEqual(plan["skipped"], [])
        self.assertEqual(plan["allocated"], 0)
        self.assertEqual(plan["unallocated"], 0)
        self.assert_plan_invariants(plan)

    def test_missing_price_is_skipped_in_target_order(self):
        """A target with no quote is skipped with reason 'no price'.

        With only TSLA unpriced, the held portfolio still buys 5 UBER. With
        VOO and TSLA both unpriced, the skips come out as VOO then TSLA.
        """

        priced = {symbol: price for symbol, price in PUBLISHED_PRICES.items() if symbol != "TSLA"}
        plan = self.plan("1000", dict(PUBLISHED_POSITIONS), priced)
        self.assertEqual(plan["total_portfolio_value"], 1540)
        self.assertEqual(plan["buying_power"], 1000)
        self.assertEqual(plan["allocated"], 350)
        self.assertEqual(plan["unallocated"], 650)
        self.assertEqual([order["symbol"] for order in plan["orders"]], ["UBER"])
        self.assertEqual([order["quantity"] for order in plan["orders"]], [5])
        self.assertEqual([order["est_cost"] for order in plan["orders"]], [350])
        self.assertEqual(plan["skipped"], [{"symbol": "TSLA", "reason": "no price"}])
        self.assert_plan_invariants(plan)

        plan = self.plan("1000", {}, {"UBER": Decimal("70")})
        self.assertEqual(plan["total_portfolio_value"], 1000)
        self.assertEqual(plan["buying_power"], 1000)
        self.assertEqual(plan["allocated"], 210)
        self.assertEqual(plan["unallocated"], 790)
        self.assertEqual([order["symbol"] for order in plan["orders"]], ["UBER"])
        self.assertEqual([order["quantity"] for order in plan["orders"]], [3])
        self.assertEqual([order["est_cost"] for order in plan["orders"]], [210])
        self.assertEqual(
            plan["skipped"],
            [
                {"symbol": "VOO", "reason": "no price"},
                {"symbol": "TSLA", "reason": "no price"},
            ],
        )
        self.assert_plan_invariants(plan)

    def test_fresh_account_with_fifty_dollar_prices(self):
        """A fresh account with $1000 and every share priced at $50.

        Buys follow the 50/25/25 weights: 10 VOO, 5 UBER, and 5 TSLA, in
        that target order.
        """

        prices = {symbol: Decimal(50) for symbol in PUBLISHED_PRICES}
        plan = self.plan("1000", {}, prices)
        self.assertEqual(plan["total_portfolio_value"], 1000)
        self.assertEqual(plan["buying_power"], 1000)
        self.assertEqual(plan["allocated"], 1000)
        self.assertEqual(plan["unallocated"], 0)
        self.assertEqual(plan["skipped"], [])
        self.assertEqual([order["symbol"] for order in plan["orders"]], ["VOO", "UBER", "TSLA"])
        self.assertEqual([order["quantity"] for order in plan["orders"]], [10, 5, 5])
        self.assertEqual([order["est_cost"] for order in plan["orders"]], [500, 250, 250])
        self.assert_plan_invariants(plan)

    def test_outside_holding_counts_toward_value_and_gets_no_order(self):
        """OTHER, 2 shares at $100, is outside the target list.

        Portfolio value is 1740, including that $200. OTHER is not ordered.
        UBER and TSLA still buy 5 and 1 shares.
        """

        positions = {**PUBLISHED_POSITIONS, "OTHER": Decimal(2)}
        prices = {**PUBLISHED_PRICES, "OTHER": Decimal(100)}
        plan = self.plan("1000", positions, prices)
        self.assertEqual(plan["total_portfolio_value"], 1740)
        self.assertEqual(plan["buying_power"], 1000)
        self.assertEqual(plan["allocated"], 600)
        self.assertEqual(plan["unallocated"], 400)
        self.assertEqual(plan["skipped"], [])
        self.assertEqual([order["symbol"] for order in plan["orders"]], ["UBER", "TSLA"])
        self.assertEqual([order["quantity"] for order in plan["orders"]], [5, 1])
        self.assertEqual([order["est_cost"] for order in plan["orders"]], [350, 250])
        self.assert_plan_invariants(plan, outside=("OTHER",))


if __name__ == "__main__":
    unittest.main()
