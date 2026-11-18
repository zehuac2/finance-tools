"""Known-value regression for the published sample portfolio."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from decimal import Decimal

from finance_tools.cli import main
from finance_tools.rebalance import compute_plan
from support import (
    HOLDINGS,
    PUBLISHED_POSITIONS,
    PUBLISHED_PRICES,
    PUBLISHED_TARGETS,
    TARGETS,
)

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
