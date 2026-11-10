# Module 1 ten-market historical backtest report

**INCOMPLETE — interim artifact snapshot, 2026-11-10 15:54 UTC.**
Three of ten individual full histories have completed execution and independent
accounting checks. One has a recorded attempt without a final receipt; six are
unstarted. All twelve common-period portfolio comparisons remain NOT RUN.
This report will be updated from completed receipts; missing returns are never
zero. The ongoing study is not a completed deliverable.

All numerical results are **UNVERIFIED SOURCE / ASSUMPTION-BASED**. Strict
historical venue performance is **NOT COMPUTABLE**: historical instrument
identity, collection provenance, fees and market rules are not certified.
OHLC observations do not prove executions, liquidity or queue priority.

## Reproducible artifacts and scope

Repository branch: `i8/public-paper-hour`. The executed research implementation
was published as `81c336626193583873589a7bafc48137af4a86f8`. The copied study
driver, child driver, binary, configuration and cohort are bound to that
publication by the retained `publication_binding.json`. The interim reporting
delta has separate focused validation; it does not change the executed model.

- [Frozen selection manifest](../research/runs/module1-20261110/selection_manifest.json)
- [Frozen revision-4 experiment](../research/runs/module1-20261110/experiment_config.json)
- [Explicit assumptions](../research/runs/module1-20261110/assumptions.md)
- [Strategy/research contract](MODULE1_RESEARCH_SPEC_v1.md)
- [Engine reuse decision](CPP_BACKTEST_AUDIT.md)
- [Build, parity and verification evidence](CPP_BACKTEST_VERIFICATION.md)
- [Per-allocation results](../research/runs/module1-20261110/results.csv),
  [funded portfolio results](../research/runs/module1-20261110/portfolio_results.csv),
  [exchange results](../research/runs/module1-20261110/exchange_results.csv),
  [cycle events](../research/runs/module1-20261110/cycle_events.csv), and
  [machine-readable summary](../research/runs/module1-20261110/summary.json).

Full execution artifacts are outside Git:
`/opt/grid-runs/module1-20261110/full-study-v4-notices/`.
The immutable interim table directory is
`/opt/grid-runs/module1-20261110/tables-interim-20261110T1554Z/`.
Its summary records CSV hashes and the external directory containing the
retained daily NAV curve. Committed compact tables are exact copies of that
snapshot. They are not a continuously updating view of the running study.

The Python live domain, executor, exchange gateway, ledger/schema, portfolio
and operations packages and dependency manifests remain unchanged from starting
revision `cd48dd01734d2b84ad13651e907e72fc437a0cbc`. The C++20 executable is an
offline research component. No credentials, exchange requests, real/sandbox
orders, deployment or Module 2 work is involved. `/opt/datasets/` is read-only.

## Frozen markets and coverage

All ten sources passed complete structural/hash verification before replay.
The table gives inclusive source timestamps in UTC, not an assertion of
verified historical listing dates. Each source has zero structural rejections.
The cohort contains ten distinct bases across four target venues, not all seven.

| Venue | Market | First UTC | Last UTC | Actual rows | Missing minutes inside source endpoints |
|---|---|---|---|---:|---:|
| OKX | 1INCH/USDT | 2022-08-04 12:05 | 2026-11-04 02:11 | 2,235,727 | 0 |
| HTX | MINA/USDT | 2022-08-04 12:05 | 2026-11-04 02:09 | 2,235,725 | 0 |
| Gate | WPAY/USDT | 2024-10-08 12:00 | 2026-11-04 02:08 | 1,080,076 | 9,413 |
| KuCoin | AKT/USDT | 2022-08-04 12:05 | 2026-11-04 02:10 | 2,235,726 | 0 |
| OKX | AGLD/USDT | 2022-08-04 12:05 | 2026-11-04 02:11 | 2,235,727 | 0 |
| HTX | XTZ/USDT | 2022-08-04 12:05 | 2026-11-04 02:09 | 2,235,725 | 0 |
| Gate | BGSC/USDT | 2024-12-08 11:00 | 2026-11-03 20:08 | 999,391 | 1,958 |
| KuCoin | OP/USDT | 2022-08-04 12:05 | 2026-11-04 02:10 | 2,235,725 | 1 |
| OKX | APE/USDT | 2022-08-04 12:05 | 2026-11-04 02:11 | 2,235,727 | 0 |
| HTX | ELA/USDT | 2022-08-04 12:05 | 2026-11-04 02:09 | 2,235,723 | 2 |

Total full-source records: **19,965,272**, occupying **479,166,528 bytes**.
The common portfolio interval is **2024-12-08 11:00 UTC to 2026-11-03 20:09
UTC, end exclusive**: 695.38125 calendar days and 10,005,677 actual market rows.
It includes 478 shared complete UTC days, but only **66 consecutive complete
UTC days**. All ten sources are present in 993,572 minutes; at least one is
missing in 7,777. The longest continuous common interval is 95,988 minutes.
This is not a gap-free one-year study; missing periods are not joined or filled.

Selection used frozen coverage/quality rules before strategy returns. The first
sparse candidate roster was rejected before returns and remains preserved.
Coverage selection can still introduce survivorship bias. Flat-price source
rows range from 3.81% to 65.67%; upstream carry-forward and actual inactivity
cannot be distinguished. Exact source hashes, gaps, rejected candidates and
selection-rule provenance are retained in the manifest and its referenced audit.

## Declared experiment

Each individual history receives 1,000 USDT. Each aligned portfolio case receives
10,000 USDT, divided into ten non-overlapping 1,000-USDT allocations in separately
funded venue accounts. No transfers, loans or external contributions occur.

Primary settings are 1% geometric spacing, one minimum executable purchase at
each admitted level, a 100-USDT quote reserve, 900-USDT commitment allowance,
100 accumulated lots, three pending lower buys for `ladder_multi` versus one
for `ladder_one`, one basket SELL, and 0.01 USDT minimum net basket profit.
The minimum net profit is an absolute quote amount, not a 1% return promise.
Synthetic rules use a 5-USDT minimum notional, price tick 1e-8, amount tick 1e-4
and 0.1% quote fees on each side. These are assumptions, not venue facts.

The passive seed uses the completed close rounded down. Synthetic spread is
0.1%; new orders and cancellation finality wait one bar. Eligible pre-existing
orders receive full hypothetical fills under the selected OHLC convention.
No creation-bar, missing-bar or retroactive chain of fills is allowed.
Partial execution and cancel races have deterministic fixtures; their historical
frequency cannot be reconstructed from these files.

Global +10% net NAV is enabled; automatic symbol/exchange TP is disabled in the
primary matrix. Separate fixtures test optional tiers and global > exchange >
symbol precedence. Exits remain LIMIT-only, with a fixed 5% trigger-price guard,
one initial order plus three reprices, 60-minute order TTLs and a 240-minute
overall deadline including cancellation. Reconciled completed cycles restart on
a later bar with continuing capital and identities; funds are never reset.

Raw NAV values all owned base at the decoded completed close. Estimated net
liquidation NAV uses valid sellable quantity, the rounded-down close proxy and
forecast exit fees. Unsellable residuals remain owned but contribute zero
estimated proceeds. Missing required current marks produce unknown NAV and
cannot trigger TP. No forced sale occurs at the end of a history.

## Completed individual histories

All completed rows below use the control `seed_min_limit + ladder_multi` and
`strict_skip_ambiguous`. Returns use original funded capital and are cumulative,
not annualized. Different source horizons must not be treated as matched trials.
PASS denotes completed execution and accounting validation, not profitability.

| Market | Status | Initial USDT | Raw ending NAV | Net ending NAV | Net return | Max observed net drawdown | Net buy/hold return |
|---|---|---:|---:|---:|---:|---:|---:|
| OKX 1INCH | PASS | 1,000 | 1,004.844546 | 1,004.513237 | +0.451324% | 19.911460% | -80.988778% |
| HTX MINA | PASS | 1,000 | 658.689686 | 658.635443 | -34.136456% | 42.087020% | -83.809729% |
| Gate WPAY | PASS | 1,000 | 1,007.559246 | 1,007.559246 | +0.755925% | 1.598700% | +2,018.996258% |
| KuCoin AKT | INCOMPLETE | — | — | — | — | — | — |
| OKX AGLD | NOT_RUN | — | — | — | — | — | — |
| HTX XTZ | NOT_RUN | — | — | — | — | — | — |
| Gate BGSC | NOT_RUN | — | — | — | — | — | — |
| KuCoin OP | NOT_RUN | — | — | — | — | — | — |
| OKX APE | NOT_RUN | — | — | — | — | — | — |
| HTX ELA | NOT_RUN | — | — | — | — | — | — |

INCOMPLETE means an attempt is recorded without a final receipt. It does not
establish process liveness. Dash cells are unavailable, not zero.

| Market | Cash | Remaining acquisition cost | Realized PnL | Net unrealized PnL | Estimated open-inventory proceeds | Actual modeled fees | Turnover |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1INCH | 673.540555 | 500.500697 | +174.041252 | -169.528015 | 330.972681 | 39.91362193 | 39,913.595143 |
| MINA | 604.456192 | 500.502731 | +104.958923 | -446.323480 | 54.179251 | 23.97860183 | 23,978.585596 |
| WPAY | 1,007.559246 | 0 | +7.559246 | 0 | 0 | 1.68925081 | 1,689.249380 |

Actual posted fees are already included in cash/cost/PnL; do not subtract them
again. Independently checked identities are cash + position cost - realized PnL
= original funding, and realized + net unrealized = net NAV - original funding.
Reserved quote is a subset of cash, not a second asset or a deduction from NAV.

| Market | BUY / SELL fills | Ordinary completed basket SELLs | Global exit SELLs | Open / unknown orders | Reserved quote | Mean observed cost utilization | Longest observed net underwater span | Longest observed lot hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1INCH | 4,020 / 1,125 | 1,124 | 1 | 1 / 0 | 0 | 19.425180% | 294.122917 days | 335.988194 days |
| MINA | 2,435 / 748 | 747 | 1 | 1 / 0 | 0 | 23.807221% | 666.401389 days | 688.073611 days |
| WPAY | 168 / 34 | 34 | 0 | 1 / 0 | 5.005006089 | 0.114687% | 0.889583 days | 11.992361 days |

All three have zero modeled unsellable residuals and zero unknown orders.
Zero residual does not mean zero inventory: 1INCH and MINA still own sellable
base. WPAY ends with no inventory and one funded seed BUY waiting. Its inventory
was positive on only about 1.52% of observed bars; the retained summary does not
establish the cause or duration of every inactive period. Planner-notice maps
in the CSV are emission counts, not time spent inactive.

Both 1INCH and MINA recorded one completed global exit and later restarted.
For 1INCH, net trigger NAV was 1,100.214080443132; four minutes later the exit
completed at 1,099.844219316910, or +9.984422% rather than +10%. Restart was one
minute later. Subsequent open losses reduced the whole-history gain to +0.451324%.
MINA's first exit completed at 1,100.099426169811, but its later history ended
below original funding. A profitable completed cycle does not protect the next.
Exact timestamps, baseline changes and outcomes are in `cycle_events.csv`.

All three have zero realized losing SELLs, so profit factor is undefined/null.
MINA demonstrates why this cannot be described as an undefeated profitable
strategy: positive realized sales coexist with a 34.14% total funded loss.

Buy/hold is a counterfactual purchase of the maximum funded amount after the
same 100-USDT reserve, using the declared spread/fees, followed by holding.
It is not an observed or simulated LIMIT fill. Approximately 90% initial
buy/hold exposure differs materially from the grid's variable exposure.
Matching initial funds alone does not make this an exposure-matched comparison.
The cash benchmark is 1,000 nominal USDT without yield or modeled custody/depeg
risk. WPAY's large benchmark gain is an outcome of this unverified source and
counterfactual, not certified asset or executable investment performance.

Risk is sampled at observed minute closes. Missing minutes and intrabar extremes
are not covered. WPAY has 9,413 missing minutes and 3,610 modeled gap refreshes;
zero unknown samples at observed timestamps does not erase those missing periods.
Gaps and unknown valuations break underwater-duration continuity; the longest
observed span is not a guaranteed maximum calendar time to recovery.

## Required common-period comparative matrix

All cases below retain the same ten markets, 10,000-USDT initial funding, costs,
dates, one basket SELL and TP switches. The two touch conventions are alternative
assumptions, not rigorous optimistic/pessimistic profit bounds.

| Trial | First entry | Pending lower buys | OHLC convention | Status | Net return / drawdown |
|---|---|---|---|---|---|
| seed_multi_strict | Seed | Up to 3 | Strict skip ambiguous | NOT_RUN | — |
| seed_multi_buy | Seed | Up to 3 | Touch BUY priority | NOT_RUN | — |
| seed_multi_sell | Seed | Up to 3 | Touch SELL priority | NOT_RUN | — |
| seed_one_strict | Seed | 1 | Strict skip ambiguous | NOT_RUN | — |
| seed_one_buy | Seed | 1 | Touch BUY priority | NOT_RUN | — |
| seed_one_sell | Seed | 1 | Touch SELL priority | NOT_RUN | — |
| lower_multi_strict | One lower first order | Up to 3 | Strict skip ambiguous | NOT_RUN | — |
| lower_multi_buy | One lower first order | Up to 3 | Touch BUY priority | NOT_RUN | — |
| lower_multi_sell | One lower first order | Up to 3 | Touch SELL priority | NOT_RUN | — |
| lower_one_strict | One lower first order | 1 | Strict skip ambiguous | NOT_RUN | — |
| lower_one_buy | One lower first order | 1 | Touch BUY priority | NOT_RUN | — |
| lower_one_sell | One lower first order | 1 | Touch SELL priority | NOT_RUN | — |

No winning variant can be selected from this incomplete matrix. Individual
histories cannot substitute for these jointly funded chronological runs.

## Retrospective monthly descriptions

The predeclared monthly convention was applied to the first three retained daily
NAV curves. It produced 130 observed monthly spans: 43 rising, 55 falling,
29 flat and 3 unclassified. Results and script/input hashes are outside Git at
`/opt/grid-runs/module1-20261110/monthly-interim-20261110T1554Z/`.

| Market | Retrospective direction | Classified spans | Median net endpoint return | Minimum | Maximum |
|---|---|---:|---:|---:|---:|
| 1INCH | Rising | 17 | +1.5227% | 0.0000% | +17.2333% |
| 1INCH | Falling | 24 | -2.0551% | -9.3709% | +0.8399% |
| 1INCH | Flat | 10 | +0.3555% | -2.0676% | +4.5061% |
| MINA | Rising | 14 | +1.2658% | 0.0000% | +10.9131% |
| MINA | Falling | 28 | -0.8462% | -11.3631% | +1.9318% |
| MINA | Flat | 9 | 0.0000% | -0.7809% | +0.9531% |
| WPAY | Rising | 12 | 0.0000% | 0.0000% | +0.7559% |
| WPAY | Falling | 3 | 0.0000% | 0.0000% | 0.0000% |
| WPAY | Flat | 10 | 0.0000% | 0.0000% | 0.0000% |

All three unclassified spans are the final partial November 2026 interval.
Independent rational-arithmetic review matched all 130 rows and 3,869 retained
NAV points against source results. WPAY's many zero returns reflect its low
modeled participation; they are not evidence of a profitable defensive policy.

Direction uses the funded buy/hold raw NAV change between each UTC month's first
and last retained observations: above +5% rising, below -5% falling, otherwise
flat; spans below 20 days or with unavailable endpoints are unclassified.
This is retrospective description, not a live signal, validated regime model
or strategy-selection rule. Endpoints do not cover complete calendar months;
intervening intervals are omitted, so these returns are not compounded or
annualized. Capital was not reset monthly. Missing intraday source observations
remain missing even when all retained daily endpoints are present.

## Measured execution and reproducibility

GCC 13.3.0, C++20, CMake 3.31.6, OpenSSL 3 exact integer/rational primitives;
portable Release build without fast-math or native-CPU flags. Each child has a
512-MiB address-space ceiling, six-hour timeout and 32-MiB output-file ceiling.
Input uses bounded 98,304-byte source buffers; runs are sequential.

| Completed run | Processed rows | Wall seconds | Rows/second | Peak child RSS KiB | Result bytes |
|---|---:|---:|---:|---:|---:|
| full_okx_1INCH | 2,235,727 | 554.083439 | 4,035.0 | 11,852 | 305,928 |
| full_htx_MINA | 2,235,725 | 502.651547 | 4,447.9 | 12,804 | 306,500 |
| full_gate_WPAY | 1,080,076 | 147.458172 | 7,324.6 | 13,756 | 155,252 |

These end-to-end receipts include startup, source validation and reporting.
They are warm-cache observations with zero reported kernel input blocks, not
cold-device throughput. Different activity and histories explain why rows/s
is not a universal engine speed. CPU/wall ratios, exact commands, result hashes
and source identity checks remain in the individual receipts and CSV.

The registered 4,096-bar C++ profile improved from about 3.708 to 1.040 seconds
after a measured exact-GCD optimization; the final diagnostic-output build took
about 1.062 seconds. Repeated financial results matched the Python reference.
The narrower predecoded Python workload took about 0.57 seconds, so no claim
that C++ beats Python follows. Full-source scans are separately reported and
must not be substituted for financial replay throughput.

Executed full-study command (already running; do not duplicate its directory):

```sh
/opt/grid-tools/module1-py312/bin/python -m scripts.module1_study --config research/runs/module1-20261110/experiment_config.json --binary /opt/grid-builds/module1/release/grid_backtest --output /opt/grid-runs/module1-20261110/full-study-v4-notices --group all --execute --timeout-seconds 21600
```

Interim table and monthly analysis commands:

```sh
/opt/grid-tools/module1-py312/bin/python -m scripts.module1_report --study /opt/grid-runs/module1-20261110/full-study-v4-notices --output /opt/grid-runs/module1-20261110/tables-interim-20261110T1554Z
/opt/grid-tools/module1-py312/bin/python /opt/grid-runs/module1-20261110/analysis-monthly.py --input /opt/grid-runs/module1-20261110/tables-interim-20261110T1554Z/nav_curve.csv --output /opt/grid-runs/module1-20261110/monthly-interim-20261110T1554Z
```

Outputs are exclusive. Reproduction must use a new output directory. The report
command validates copied config/cohort/binary/input/result hashes and independently
checks funded returns, allocation identities and aggregate accounting. It does
not execute a strategy. Source files and all prior attempts remain untouched.

The earlier `full-study-v4-gcd` attempt was interrupted before any full case
completed because planner notices were omitted from output. Its failed receipt,
interruption reason and unexecuted trials remain preserved. The replacement adds
diagnostic output and retains identical financial parameters. The original
failure is not hidden or counted as a completed history.

## Completion audit and next actions

| Requirement | Current evidence |
|---|---|
| Preserve live core and raw source files | Verified unchanged protected code; source identity/hash checks retained |
| Versioned research contract and frozen ten-market selection | Published before comparative returns |
| C++ implementation and Python financial/lifecycle parity | Implemented; finite differential and synthetic integration gates pass |
| Debug, Release, sanitizer and full Python regression checks | Recorded in verification document; later report deltas separately checked |
| Full replay of every selected source | 3/10 completed in this snapshot; INCOMPLETE |
| Twelve matched funded portfolio cases | 0/12 completed; NOT_RUN |
| Actual accounting, metrics, benchmarks and reports | Available for three cases; remaining financial cells unavailable |
| Retrospective rising/falling/flat descriptions | First three histories processed; portfolio descriptions pending |
| One-year ten-market exploratory objective | NOT MET yet; aligned runs incomplete and calendar coverage contains gaps |
| Gap-free shared one-year data | NOT MET by this cohort; 66 consecutive complete UTC days |
| One-year dozens-of-symbols objective | NOT YET TESTED; ten markets cannot satisfy it |
| Historically executable profit / live readiness | NOT PROVEN; outside OHLC evidence and current authorization |

Continue the existing bounded driver through the remaining full histories and
twelve comparisons. Preserve failures and negative outcomes. Then regenerate
the compact tables and monthly analysis, complete the portfolio/exchange
discussion, verify artifacts against all original requirements and publish the
final report. Do not change the cohort or tune parameters using these returns.

After the mandatory matrix, the highest-value follow-ups are separately declared
fee/execution sensitivity and, if justified, one controlled reentry comparison.
Representative held-out periods, historical rule provenance and later execution
evidence remain necessary before claiming robustness. Optional split SELL,
broader universe expansion and automatic regime selection are not needed to
finish this experiment and have not been introduced. GitHub remains Free-only;
local validation does not relabel the known hosted-CI lock as a pass.
