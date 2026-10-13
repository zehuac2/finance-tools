# finance-tools

A Python CLI for portfolio planning. Its first subcommand, `rebalance`, adapts
the buy-only logic from `my_agent_workspace.rebalance` to separate CSV inputs.
It computes a plan; it does not place trades or fetch live prices.

## Install from GitHub

After pushing this project to GitHub, replace `OWNER` below with the repository
owner:

```sh
uv tool install git+https://github.com/OWNER/finance-tools.git
finance-tools --help
```

Or run without a persistent installation:

```sh
uvx --from git+https://github.com/OWNER/finance-tools.git finance-tools rebalance \
  --targets targets.csv \
  --holdings holdings.csv \
  --buying-power 1000
```

These Git sources are supported by [uv's tool commands](https://docs.astral.sh/uv/guides/tools/).
Python 3.10 or later is required. There are no runtime dependencies.

## Rebalance

```sh
finance-tools rebalance \
  --targets targets.csv \
  --holdings holdings.csv \
  --buying-power 1000
```

Add `--json` for structured output containing `orders`, `skipped`, a `summary`,
and portfolio/cash totals. Fractional orders have a two-decimal `dollar_amount`
string; whole-share orders have an integer `quantity`. Both include `est_cost`,
`current_pct`, `target_pct`, `symbol`, `group`, and `fractional`.

### Targets CSV

The column names match the existing composition CSV:

```csv
Symbol,Name,Group,Fractional Shares,Target Percentage
VOO,Vanguard S&P 500,Core ETF,TRUE,50
UBER,Uber,Bets,FALSE,25
TSLA,Tesla,Bets,FALSE,25
```

All five headers are required; `Name` and `Group` values may be blank.
Percentages must be finite, between 0 and 100, and sum to 100 within 0.01.
Fractional flags accept `TRUE`/`FALSE` or `YES`/`NO`, case-insensitively.

### Holdings CSV

```csv
Symbol,Quantity,Price
VOO,1,540
UBER,0,70
TSLA,0,250
```

All three headers are required. Quantities are shares held and prices are per
share, using the same currency as `--buying-power`. Include zero-quantity rows
to supply prices for securities you do not currently own. For a fresh account,
use zero quantities for all targets.

Symbols are normalized to uppercase; surrounding whitespace is stripped.
Duplicate or empty symbols, malformed CSV rows, negative numbers, and nonfinite
numbers are rejected. A blank or zero price for an unheld target causes it to
be skipped; held shares require a positive price so the portfolio can be valued.
Omitted target symbols are treated as unheld with no quote. An empty holdings
file with the header is valid, but supplies no prices.

### Allocation behavior

Total portfolio value includes all holdings plus available cash, including
holdings outside the target CSV. Only target symbols receive buy orders.
Available cash is distributed proportionally to positive target deficits;
positions already at or above target receive no orders. Whole-share quantities
are rounded down, and orders below $1 are omitted, matching the original logic.
Cash reserved for missing quotes or unaffordable shares remains unallocated;
leftovers are not redistributed.

Calculations use decimal arithmetic. Fractional dollar orders are rounded to
cents and capped by remaining cash, avoiding the original rounding overspend.
Cash must be nonnegative and in whole cents. Costs and totals are estimates,
displayed to two decimals; holdings CSV prices may have greater precision.

## Local development

```sh
uv sync
uv run finance-tools rebalance \
  --targets examples/targets.csv \
  --holdings examples/holdings.csv \
  --buying-power 1000

uv run python -m unittest discover -s tests -v
uv build
```
