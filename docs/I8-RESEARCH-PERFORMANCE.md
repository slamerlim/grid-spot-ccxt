# I8 offline follow-up — realized statistics and funded portfolio benchmarks

Later extension: `I8-INDEPENDENT-LOT-REFERENCE.md` adds a research-only comparison
against independently closed lots. Evidence below remains scoped to this original
performance increment.

Base: `553216de26ae2c5d181ca989307918fedd824a0f`, branch
`i8/public-paper-hour`. This is continued authorized offline development under
PRD §9. Tests use invented funds, histories and temporary mock ledgers. No new
exchange observation, account access, order transport or deployment is included.

## Realized statistics

The ordinary replay already accumulated positive and negative net realized PnL
per hypothetical SELL fill. It now serializes `gross_wins_quote` and
`gross_losses_quote` alongside its existing realized/unrealized PnL and profit
factor. Gross here means sums before netting winners against losers, **not**
profits before fees. Purchase cost and actual modeled fees are included in the
ledger's realized amounts. Unrealized inventory losses remain separate and can
outweigh realized wins.

Portfolio replay adds `posted_performance`, with aggregate and per-allocation
results. It streams the temporary ledger's realized journal and unique stored
fills, checks realized totals against final states, and refuses unlike realized
currencies. Its fill/SELL counts, turnover and fees describe committed postings.
The existing root `fills`, `fees_quote` and `turnover_quote` describe the synthetic
venue. These can differ while reconciliation is pending; the new report does
not promote a venue hint or status acknowledgement into recorded earnings.
Duplicate intake and compacted state cannot erase or multiply durable history.

Profit factor is the sum of positive net realized postings divided by the
absolute sum of negative postings. It is null if losses are zero or fewer than
two distinct SELL orders have posted fills. `filled_sell_orders` makes that
minimum explicit. Multiple partial fills of one order do not increase this
count. Orders may still be incomplete or correlated; two orders are a declared
reporting minimum, not evidence of statistical adequacy or a profitable edge.
All calculations use the application's controlled Decimal context.

## Portfolio benchmark ownership and valuation

`backtest --portfolio ...` now includes `benchmarks` with schema
`grid-portfolio-benchmarks-v1`, without an additional flag. Scenario comparisons
retain the benchmarks within every complete member result. Benchmark funds are
independent counterfactual copies; they never enter the financial writer or
produce executor orders, fills or transfers.

Both references begin with the strategy's complete owned capital and the same
pre-trading cycle NAV, including each allocation, fee-token balances and all
unallocated pools. Pool balances deduct allocated amounts exactly once:

- Cash retains the initial currencies without trading. If the supplied capital
  includes non-reporting tokens, this is a no-trade reference, not an assertion
  that all funds were converted to the reporting currency.
- Buy/hold uses the existing funded benchmark entry rule independently for each
  allocation: spend its quote above the configured quote reserve at the first
  completed close plus assumed spread, round the price up, include the modeled
  entry fee, and obey market amount/cost bounds. Grid minimum-lot sizing and grid
  position caps do not define this all-in reference. Entry has no modeled queue,
  latency or participation guarantee. A minimum/budget failure retains cash and
  records a reason; it never enlarges a lot or borrows. Unallocated pools stay
  uninvested and empty conversion sinks stay empty.

Subsequent marks use the existing portfolio valuation function with the same
synthetic book depth, fee rules, exit-order budgets, direct FX capacities and
dust treatment as strategy NAV. The larger reference position may exceed those
constraints even when the small grid position can be valued. Such a benchmark
mark and return are null with `portfolio_valuation_constraints`, never zero or
a stale prior value. Entry is an idealized funded reference; a known exit mark
does not establish that its entry or liquidation could actually execute.

Each retained interval includes its benchmark marks. The final benchmark ends
at the strategy's actual simulation endpoint: cycle completion or unfinished
EOF. Later history is still checksum-validated but cannot change its ending
mark. `entry_ms`, `last.end_ms_exclusive`, initial owned NAV, per-allocation entry
holdings/fees, unallocated pools, reporting currency and assumptions are recorded.
Returns divide net marked benchmark NAV by the original owned baseline, so entry
fees/spread and estimated exit costs are not omitted or rebased away. There is
no forced benchmark sale, automatic next cycle or annualization.

## Examples and evidence

In the fixed one-allocation example, 1,000 quote capital triggers at 1,120 and
actually completes at 1,050. At that same endpoint cash is 1,000 and modeled
buy/hold is 1,494.85101. Changing only later synthetic prices changes source
manifests but leaves these completed metrics unchanged.

A two-allocation exit fixture separately checks a +50 winner and -20 loser:
gross wins 50, gross losses 20, realized PnL 30 and profit factor 2.5 across two
filled SELL orders. A losing partial-exit fixture checks that two fills of one
order leave profit factor undefined. These loss studies explicitly permit a
75% deviation from their synthetic trigger marks; this is test configuration,
not a recommended or default trading limit. Existing tighter price guards remain
in force for other configurations and can prevent an exit.

Tests also cover CLI serialization and matching timestamps, unallocated cash and
foreign-token ownership, BASE/QUOTE/TOKEN entry and exit fees, unavailable depth
or valuation order budget, a minimum/budget entry refusal, duplicate fills,
compaction, unlike currencies and inconsistent realized totals. Existing repeat
and low ambient Decimal precision tests exercise the added output as well.
Ruff lint/format, strict mypy (164 source/test files), Bandit over application
and scripts, lock consistency, the staged secret scan with its unchanged
baseline, and the dependency advisory audit passed. The audit reported no known
vulnerabilities; it cannot establish absence of undisclosed vulnerabilities.
All 76 application modules in the wheel match the final tree byte for byte.
The four imported specifications and attachments still match their original
manifest hashes; dependency manifests and the secret baseline are unchanged.

Both complete test suites passed with frozen application source SHA-256
`5f84d760bc8a893e2ea695c45cc8305f0b1e134cf6b9a5ca95e0542cc86bfc84`.
Python 3.12.16 passed **935 tests in 521.74 seconds**; Python 3.13.18 passed
**935 tests in 548.50 seconds**. Both completion receipts confirm the application
source remained unchanged. Durable logs and completion
receipts are in `/tmp/grid-i8-performance-final-checks/`, with stems
`3.12-1793825405027098488` and `3.13-1793825405027483173`. Package artifacts are
under `/tmp/grid-i8-performance-dist/`. The final source archive is checked
against all application/test Python files, documentation and dependency
manifests; no generated financial/history artifacts are included. Temporary
logs and packages are not committed evidence. Hosted CI remains pending under the known unchanged account
lock; publish with `[skip ci]`.

## Compatibility, resources and remaining work

The engine schema names remain `grid-research-v1` and
`grid-portfolio-research-v1`. Fields are additive; descriptions and result hashes
change. Consumers should accept the new fields, retain original reports and
compare runs with the same source revision. No financial migration, dependency
change, trading policy or actual order sequence is introduced. Reverting this
increment removes the extra reporting; existing operator source compatibility
checks still apply.

The benchmark keeps two bounded copies of the allocation roster and values them
at every simulated bar; the ledger scan streams rows and retains at most its
bounded order-ID set plus aggregate results. Existing input, interval, venue,
temporary-database and report limits remain. These add CPU work and are not OS
resource quotas or evidence of responsiveness under concurrent trading load.

Independent-lot reference, walk-forward/regime/fee/survivorship studies,
portfolio per-allocation unrealized/exposure/drawdown reporting, historical FX/rule changes,
multi-cycle replay, constrained research/fill contention and the complete
storage benchmark matrix remain open. The observed 48-hour acceptance gate,
intended-first-venue account evidence, I9 and deployment remain separately gated.
