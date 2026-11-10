# Module 1 ten-market historical backtest report

**INCOMPLETE — second matched portfolio snapshot, 2026-11-10 17:48 UTC.**
All ten individual full histories completed execution and independent accounting
checks, covering every one of their 19,965,272 source records. Seven ended
positive and three negative under the declared model. The first common-period
portfolio case, `seed_multi_strict`, also completed: its 10,000 USDT ended at
9,856.393285 USDT estimated net liquidation NAV, a **1.436067% loss**, with
11.371658% maximum observed net drawdown. The second case, `seed_multi_buy`,
ended at 9,861.972690 USDT, a **1.380273% loss**, with 11.382277% observed
net drawdown. Two of twelve portfolio cases are complete; the third has an
attempt without a final receipt and nine are unstarted.
This snapshot is not the completed comparative study or evidence of profitability.

All numerical results are **UNVERIFIED SOURCE / ASSUMPTION-BASED**. Strict
historical venue performance is **NOT COMPUTABLE**: historical instrument
identity, collection provenance, fees and market rules are not certified.
OHLC observations do not prove executions, liquidity or queue priority.

## Reproducible artifacts and scope

Repository branch: `i8/public-paper-hour`. The executed research implementation
was published as `81c336626193583873589a7bafc48137af4a86f8`. The copied study
driver, child driver, binary, configuration and cohort are bound to that
publication by the retained `publication_binding.json`. Reporting/guard-test
changes were published as
`53a692be8f9a71c38f4060954006e371b69a1a30`, with separate focused validation;
they do not change the executed model.

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
The immutable table directory for this snapshot is
`/opt/grid-runs/module1-20261110/tables-portfolio2-20261110T1748Z/`.
Its summary records CSV hashes and the external directory containing the
retained daily NAV curve. Committed compact tables are exact copies of that
snapshot. They are not a continuously updating view of the running study.
The previous full-individual snapshot remains preserved at
`/opt/grid-runs/module1-20261110/tables-full10-20261110T1641Z/`.
Its ten individual results are unchanged in the newer extraction. The first
portfolio snapshot also remains preserved at
`/opt/grid-runs/module1-20261110/tables-portfolio1-20261110T1715Z/`; all eleven
previously completed results are unchanged in this second-portfolio extraction.

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

Here, calendar alignment means identical funding/start/end boundaries and a
chronological merge of the actual observations. It does not assert a complete
observation for every market in every minute. The 695-day shared calendar
interval supplies the exploratory duration target; uninterrupted complete-year
coverage is a separate failed data-quality gate. The frozen matrix therefore
retains this interval and its explicit gaps. A shorter gap-free interval would
answer a different coverage question and cannot replace the declared study.

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

All ten full histories completed with PASS execution/accounting receipts and
zero rejected source records. All use the control `seed_min_limit + ladder_multi`
and `strict_skip_ambiguous`, with 1,000 USDT funded separately in each run.
Returns are cumulative on original funding, not annualized. These different
source horizons must not be summed into a matched portfolio return. In each
individual run the global trigger covers its one funded allocation; the
jointly funded ten-market comparisons are separate; their first two completed
cases are described below and the remaining ten are unfinished in this snapshot.

Seven histories ended positive and three negative. XTZ had the largest positive
whole-funded return; BGSC had the largest loss. This is a description of the
frozen exploratory cohort, not a rule for selecting assets or evidence of future
profitability. PASS denotes execution and accounting validation, not profit.

| Market | Initial USDT | Raw ending NAV | Net ending NAV | Net return | Max observed net drawdown | Net buy/hold return | Delta vs buy/hold (pp) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OKX 1INCH | 1,000 | 1,004.844546 | 1,004.513237 | +0.451324% | 19.911460% | -80.988778% | +81.440102 |
| HTX MINA | 1,000 | 658.689686 | 658.635443 | -34.136456% | 42.087020% | -83.809729% | +49.673274 |
| Gate WPAY | 1,000 | 1,007.559246 | 1,007.559246 | +0.755925% | 1.598700% | +2,018.996258% | -2,018.240334 |
| KuCoin AKT | 1,000 | 1,088.595488 | 1,088.595488 | +8.859549% | 9.528914% | +48.557797% | -39.698249 |
| OKX AGLD | 1,000 | 1,248.637844 | 1,248.590362 | +24.859036% | 18.793868% | -54.727638% | +79.586674 |
| HTX XTZ | 1,000 | 1,319.956629 | 1,319.956629 | +31.995663% | 11.002105% | -77.662486% | +109.658149 |
| Gate BGSC | 1,000 | 508.014672 | 508.006156 | -49.199384% | 49.208848% | -89.103671% | +39.904287 |
| KuCoin OP | 1,000 | 1,124.007177 | 1,124.007177 | +12.400718% | 19.838572% | -85.184528% | +97.585246 |
| OKX APE | 1,000 | 574.367741 | 574.347214 | -42.565279% | 46.036667% | -88.692752% | +46.127473 |
| HTX ELA | 1,000 | 1,250.875200 | 1,250.875200 | +25.087520% | 12.378647% | -66.304135% | +91.391655 |

Amounts below retain open inventory; no terminal liquidation was forced.

| Market | Cash | Remaining acquisition cost | Realized PnL | Net unrealized PnL | Estimated open-inventory proceeds | Posted modeled fees | Turnover |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1INCH | 673.540555 | 500.500697 | +174.041252 | -169.528015 | 330.972681 | 39.91362193 | 39,913.595143 |
| MINA | 604.456192 | 500.502731 | +104.958923 | -446.323480 | 54.179251 | 23.97860183 | 23,978.585596 |
| WPAY | 1,007.559246 | 0 | +7.559246 | 0 | 0 | 1.68925081 | 1,689.249380 |
| AKT | 1,088.595488 | 0 | +88.595488 | 0 | 0 | 19.79846563 | 19,798.452791 |
| AGLD | 1,201.157096 | 50.050091 | +251.207187 | -2.616826 | 47.433265 | 55.38681784 | 55,386.781344 |
| XTZ | 1,319.956629 | 0 | +319.956629 | 0 | 0 | 71.51198018 | 71,511.933290 |
| BGSC | 499.499866 | 500.500134 | 0 | -491.993844 | 8.506290 | 0.50000100 | 500.000133 |
| OP | 1,124.007177 | 0 | +124.007177 | 0 | 0 | 27.71194984 | 27,711.932312 |
| APE | 553.842575 | 500.513790 | +54.356365 | -480.009151 | 20.504639 | 12.64707215 | 12,647.063871 |
| ELA | 1,250.875200 | 0 | +250.875200 | 0 | 0 | 56.10760358 | 56,107.568785 |

Posted fees are already included in raw NAV, cash, cost and PnL; do not subtract
them again. Raw versus net NAV differs by estimated remaining liquidation costs
and residual valuation. Independently checked identities are cash + position
cost - realized PnL = original funding, and realized + net unrealized = net NAV
- original funding. Reserved quote is a subset of cash, not a second asset or
a deduction from NAV. Exact unrounded values are retained in the CSVs.

| Market | BUY / SELL fills | Ordinary completed basket SELLs | Exit SELLs | Confirmed cancellations | Open / unknown orders | Reserved quote | Completed global cycles |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1INCH | 4,020 / 1,125 | 1,124 | 1 | 6,069 | 1 / 0 | 0 | 1 |
| MINA | 2,435 / 748 | 747 | 1 | 3,776 | 1 / 0 | 0 | 1 |
| WPAY | 168 / 34 | 34 | 0 | 190 | 1 / 0 | 5.005006089000 | 0 |
| AKT | 1,969 / 538 | 538 | 0 | 2,808 | 1 / 0 | 5.005025300052 | 0 |
| AGLD | 5,513 / 1,620 | 1,619 | 1 | 8,448 | 4 / 0 | 15.015016333017 | 2 |
| XTZ | 7,112 / 2,125 | 2,124 | 1 | 10,323 | 1 / 0 | 5.005003200000 | 2 |
| BGSC | 100 / 0 | 0 | 0 | 33 | 1 / 0 | 0 | 0 |
| OP | 2,756 / 655 | 655 | 0 | 3,896 | 1 / 0 | 5.005001797389 | 1 |
| APE | 1,308 / 353 | 353 | 0 | 1,958 | 1 / 0 | 0 | 0 |
| ELA | 5,580 / 1,240 | 1,239 | 1 | 7,106 | 1 / 0 | 5.005022220000 | 2 |

Open orders are terminal working/unfilled orders, distinct from confirmed
cancellations. Under this frozen full-fill convention, placements equal BUY
fills + SELL fills + confirmed cancellations + terminal open orders in every
individual run; this identity must not be generalized to partial-fill counts.
Ordinary completed basket SELLs also differ from allocation round advances,
which include portfolio-cycle restarts.

| Market | Mean observed cost utilization | Maximum cost / original funding | Observed bars with inventory | Longest observed net underwater span | Longest observed lot hold |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1INCH | 19.425180% | 50.050070% | 82.861011% | 294.122917 days | 335.988194 days |
| MINA | 23.807221% | 50.050273% | 69.204442% | 666.401389 days | 688.073611 days |
| WPAY | 0.114687% | 12.512506% | 1.524152% | 0.889583 days | 11.992361 days |
| AKT | 1.710727% | 34.534617% | 17.071546% | 37.228472 days | 37.227778 days |
| AGLD | 8.167931% | 50.050215% | 56.333041% | 126.076389 days | 128.931944 days |
| XTZ | 5.674838% | 37.537904% | 73.234991% | 72.881944 days | 73.022917 days |
| BGSC | 50.048327% | 50.050013% | 99.999900% | 101.413194 days | 695.379861 days |
| OP | 7.121023% | 50.050100% | 34.927909% | 603.906944 days | 105.252083 days |
| APE | 41.470465% | 50.051379% | 95.331541% | 1,286.979167 days | 1,295.848611 days |
| ELA | 6.669492% | 40.040443% | 61.640373% | 119.861806 days | 119.862500 days |

Utilization is acquisition cost divided by original funding, sampled at each
source's observed bars. It is not continuous-time market exposure or the amount
at risk at every instant. Holding times include unsold lots. Calendar gaps and
unknown marks break underwater streaks, so the longest observed streak is not
a guaranteed maximum calendar time to recovery. OP ended profitable after a
603.91-day observed underwater period; APE's corresponding span was 1,286.98 days.

BGSC bought 100 minimum lots and sold none. Its 100-lot allowance stopped more
buying while 499.499866 USDT remained in cash. Inventory acquired for 500.500134
USDT was worth an estimated 8.506290 net at the end, a loss of about 98.30% of
its cost. Its oldest lot remained owned for about 695.38 days. The funding/lot
caps restricted purchases but did not create a recovery or loss-cutting policy.

APE completed 353 profitable ordinary basket SELLs, yet ended down 42.57% on
all funded capital: its 480.009151 USDT net unrealized loss overwhelmed
54.356365 USDT realized profit. The remaining position lost about 95.90% of its
acquisition cost. MINA similarly ended down 34.14% despite positive realized PnL
and an earlier completed +10% cycle. Realized grid gains alone are insufficient
performance evidence.

Every individual run has zero realized losing sales and undefined/null profit
factor. The reported gross realized profit/loss fields sum positive/negative
realized sale PnL after posted acquisition/sale costs; they are not fee-free
returns. For these runs gross realized profit equals the realized PnL column
and gross realized loss is zero, including BGSC's zero sales. No infinite or
risk-free profit-factor conclusion follows.

All ten have zero modeled unsellable residuals and zero unknown orders.
1INCH, MINA, AGLD, BGSC and APE still own sellable base. WPAY, AKT, XTZ, OP and
ELA end in cash with one funded seed BUY waiting. WPAY held inventory on only
about 1.52% of observed bars. The retained summaries cannot explain every
inactive period or waiting-order duration. Planner-notice maps count emitted
diagnostics, not time spent inactive.

All triggered individual global cycles completed and restarted; no global exit
is terminally blocked in this snapshot. This does not prove the joint portfolio
can exit. For 1INCH, trigger NAV was 1,100.214080443132; four minutes later the
exit completed at 1,099.844219316910, or +9.984422% rather than +10%. Restart
was a minute later, and subsequent open losses reduced the full-history gain
to +0.451324%. MINA's first exit completed at 1,100.099426169811 before its
later losses. A profitable completed cycle does not protect the next.

Cycle counts need not equal exit-SELL counts: AGLD, XTZ and ELA each completed
two global cycles with only one portfolio-exit SELL, and OP completed one with
none. Some thresholds were reached already in cash. Completed-cycle baselines
compound: XTZ's active baseline is 1,210.933347983045, making its next +10%
threshold 1,332.0266827813495 despite a +31.995663% cumulative return. Exact
trigger/completion/restart timestamps, NAV and baseline changes are in
`cycle_events.csv`; current cycle states and scoped exit diagnostics are in
`summary.json`.

Buy/hold is a counterfactual maximum funded purchase after the same 100-USDT
reserve, using the declared spread/fees, followed by holding. It is not an
observed or simulated LIMIT fill. Approximately 90% initial buy/hold exposure
differs materially from the grid's variable exposure. Matching initial funds
alone does not make this an exposure-matched comparison. The cash benchmark
is 1,000 nominal USDT without yield or modeled custody/depeg risk; the grid's
net-return column is also its percentage-point delta versus that benchmark.
WPAY's large buy/hold gain remains an unverified-source counterfactual.

Risk is sampled at observed minute closes, omitting intrabar extremes. All
individual runs have known NAV at observed samples, but WPAY has 9,413 missing
minutes, BGSC 1,958, OP one and ELA two. Source gaps remain missing even when
observed NAV samples are known. Gap refreshes are synthetic planning events,
not recovered executions or filled observations.

## Required common-period comparative matrix

All cases below retain the same ten markets, 10,000-USDT initial funding, costs,
dates, one basket SELL and TP switches. The two touch conventions are alternative
assumptions, not rigorous optimistic/pessimistic profit bounds.

| Trial | First entry | Pending lower buys | OHLC convention | Status | Net return / drawdown |
|---|---|---|---|---|---|
| seed_multi_strict | Seed | Up to 3 | Strict skip ambiguous | PASS | -1.436067% / 11.371658% |
| seed_multi_buy | Seed | Up to 3 | Touch BUY priority | PASS | -1.380273% / 11.382277% |
| seed_multi_sell | Seed | Up to 3 | Touch SELL priority | INCOMPLETE | — |
| seed_one_strict | Seed | 1 | Strict skip ambiguous | NOT_RUN | — |
| seed_one_buy | Seed | 1 | Touch BUY priority | NOT_RUN | — |
| seed_one_sell | Seed | 1 | Touch SELL priority | NOT_RUN | — |
| lower_multi_strict | One lower first order | Up to 3 | Strict skip ambiguous | NOT_RUN | — |
| lower_multi_buy | One lower first order | Up to 3 | Touch BUY priority | NOT_RUN | — |
| lower_multi_sell | One lower first order | Up to 3 | Touch SELL priority | NOT_RUN | — |
| lower_one_strict | One lower first order | 1 | Strict skip ambiguous | NOT_RUN | — |
| lower_one_buy | One lower first order | 1 | Touch BUY priority | NOT_RUN | — |
| lower_one_sell | One lower first order | 1 | Touch SELL priority | NOT_RUN | — |

INCOMPLETE means a recorded attempt lacks a final receipt; it does not itself
prove liveness. Unavailable financial cells remain blank, never zero.
No winning variant can be selected from this incomplete matrix. Individual
histories cannot substitute for these jointly funded chronological runs.

## Completed matched portfolios

The first case, `seed_multi_strict`, uses the declared seed plus up-to-three
lower BUYs and strict ambiguous-bar handling over the common interval. The
second, `seed_multi_buy`, changes only the OHLC convention to touch BUY priority.
Both passed execution and independent accounting checks; both funded returns
are negative. Two completed cases cannot select a winning policy.

| Measure (USDT) | Strict | BUY priority |
| --- | ---: | ---: |
| Initial funded capital | 10,000.000000 | 10,000.000000 |
| Raw ending NAV | 9,856.985581 | 9,862.564987 |
| Estimated net liquidation NAV | 9,856.393285 | 9,861.972690 |
| Net PnL | -143.606715 | -138.027310 |
| Realized PnL | 981.437989 | 987.017394 |
| Raw unrealized PnL | -1,124.452408 | -1,124.452408 |
| Net unrealized PnL | -1,125.044704 | -1,125.044704 |
| Cash, including reservations | 9,264.719119 | 9,270.298525 |
| Reserved quote, already included in cash | 55.055094 | 55.055094 |
| Remaining acquisition cost | 1,716.718869 | 1,716.718869 |
| Estimated net open-inventory proceeds | 591.674165 | 591.674165 |
| Posted modeled fees | 221.037364 | 222.284196 |
| Turnover | 221,037.222359 | 222,284.053626 |

For the strict case, net return is **-1.436067%**, with **11.371658%** maximum
observed net drawdown.
The exact funded loss is 143.606715249935 USDT. Cash plus remaining acquisition
cost equals initial funding plus realized PnL; cash plus estimated net inventory
proceeds equals ending net NAV. Fees are already reflected in those figures,
and reserved quote is not an additional asset or a second deduction.

The 22,155 modeled BUY fills and 5,433 SELL fills produced 5,433 ordinary basket
SELL completions. All realized sale PnL was positive; gross realized loss is
zero and profit factor is undefined/null. The full-fill historical model's
order counts reconcile: 58,188 placed = 22,155 filled BUYs + 5,433 filled SELLs
+ 30,584 canceled + 16 open. There are zero unknown orders and zero reported
unsellable residual quantities. Five allocations still own sellable inventory;
five are cash-only with a pending seed. These modeled counts do not demonstrate
actual fills or an absence of exchange uncertainty.

The mandatory global +10% trigger never fired: no portfolio exit, completion or
restart occurred. Symbol/exchange TP switches were off. Consequently this case
provides no historical test of blocked global liquidation or exit execution;
those behaviors have separate deterministic fixtures.

The following strict-case allocation results share the same funding and dates.
They are not the different-horizon full-history results above.

| Venue / market | Net ending NAV | Net return | Max observed net DD | Realized PnL | Net unrealized PnL |
| --- | --- | --- | --- | --- | --- |
| gate BGSC/USDT | 508.006156 | -49.199384% | 49.208848% | 0.000000 | -491.993844 |
| gate WPAY/USDT | 1,020.247863 | +2.024786% | 10.928559% | 20.247863 | 0.000000 |
| htx ELA/USDT | 1,152.084240 | +15.208424% | 6.603912% | 152.084240 | 0.000000 |
| htx MINA/USDT | 562.227738 | -43.777226% | 46.125772% | 8.054160 | -445.826422 |
| htx XTZ/USDT | 1,218.721847 | +21.872185% | 5.195640% | 218.721847 | 0.000000 |
| kucoin AKT/USDT | 1,097.865508 | +9.786551% | 8.255405% | 97.865508 | 0.000000 |
| kucoin OP/USDT | 1,111.453541 | +11.145354% | 20.065398% | 111.453541 | 0.000000 |
| okx 1INCH/USDT | 911.016248 | -8.898375% | 21.720478% | 76.131844 | -165.115596 |
| okx AGLD/USDT | 1,157.860395 | +15.786039% | 20.313924% | 159.553437 | -1.693042 |
| okx APE/USDT | 1,116.909749 | +11.690975% | 30.332516% | 137.325549 | -20.415800 |

Seven allocations gained 875.143142387161 USDT in total; three lost
1,018.749857637096. BGSC and MINA together lost 929.766105366037 USDT.
Positive completed baskets therefore did not offset retained inventory losses.
This is a frozen cohort result, not a rule to remove losing markets afterward.

| Venue | Initial USDT | Net ending NAV | Net return | Max observed net DD | Realized PnL | Net unrealized PnL | Posted fees |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gate | 2,000.000000 | 1,528.254019 | -23.587299% | 26.397212% | 20.247863 | -491.993844 | 5.024781 |
| htx | 3,000.000000 | 2,933.033825 | -2.232206% | 12.162507% | 378.860248 | -445.826422 | 85.164070 |
| kucoin | 2,000.000000 | 2,209.319049 | +10.465952% | 9.638483% | 209.319049 | 0.000000 | 46.776648 |
| okx | 3,000.000000 | 3,185.786391 | +6.192880% | 17.238067% | 373.010830 | -187.224438 | 84.071864 |

Venue reserve/open-order/unknown-order totals are Gate 5.005003292540/2/0,
HTX 10.010025420000/3/0, KuCoin 10.010027097441/2/0 and
OKX 30.030037757995/9/0. The reserves sum to 55.055093567976 USDT.

| Market | Mean observed committed utilization | Inventory on observed bars | Longest observed lot hold (days) | Terminal open / unknown orders |
| --- | --- | --- | --- | --- |
| BGSC/USDT | 50.048377% | 99.999900% | 695.379861 | 1 / 0 |
| WPAY/USDT | 4.476069% | 15.340679% | 77.176389 | 1 / 0 |
| ELA/USDT | 6.355263% | 59.858770% | 57.659028 | 1 / 0 |
| MINA/USDT | 47.927743% | 99.965247% | 687.822917 | 1 / 0 |
| XTZ/USDT | 3.860721% | 61.610288% | 19.366667 | 1 / 0 |
| AKT/USDT | 6.243550% | 50.199281% | 59.105556 | 1 / 0 |
| OP/USDT | 16.684372% | 76.329608% | 105.252083 | 1 / 0 |
| 1INCH/USDT | 24.458869% | 85.794763% | 335.736111 | 1 / 0 |
| AGLD/USDT | 14.691946% | 87.631984% | 128.931944 | 4 / 0 |
| APE/USDT | 13.610232% | 70.675059% | 163.318750 | 4 / 0 |

These utilization and inventory fractions use each allocation's own observed-bar
samples. They cannot be averaged into a common-clock portfolio utilization
measure; source gaps also limit holding-time interpretation. The portfolio's
longest known observed underwater span is 100.627778 days; unknown observations
break streaks, and no guaranteed maximum calendar recovery time follows.

The cash reference ends at 10,000 USDT (zero nominal return). Funded buy/hold
ends at 6,311.085850 estimated net NAV, a -36.889142% return and 73.783840%
maximum observed net drawdown. The grid is 35.453074 percentage points ahead
of buy/hold, but 1.436067 points behind cash. Buy/hold's approximately 90%
initial deployment differs from the minimum-lot grid's variable exposure;
matching funds/window alone does not isolate a trading advantage.

All 1,001,349 calendar-minute timestamp groups have at least one source, but
only 993,572 contain every market. The remaining 7,777 groups lack at least
one source, totaling 7,813 missing source-minutes. Grid net NAV is unknown at
2,606 observations and known at 998,743; buy/hold is unknown at 7,777 and known
at 993,572. Cash-only allocations explain why missing source data does not
always make grid NAV unknown. Terminal marks are known. No absent mark was
filled forward or turned into a hypothetical execution.

There were 106 ambiguous bars and 3,473 post-gap planning refreshes. Planner
notices include 16,291 draining/unresolved-BUY notices and 1,199 accumulated-lot
cap notices. These are emitted counts, not durations or failure rates.

The completed result SHA-256 is
`35b43b688474ab3a7556109ecebce998b7be7f353ba23b1826607f7d91d0c980`.
Its input, result, executable and all ten source identities are validated in
`/opt/grid-runs/module1-20261110/full-study-v4-notices/seed_multi_strict/receipt.json`.
The independent supplementary check is
`/opt/grid-runs/module1-20261110/verification/portfolio-statistics-review-v1/seed_multi_strict.json`.

### Second case: BUY-priority comparison

`seed_multi_buy` completed at 2026-11-10 17:48:37 UTC. Its exact ending net NAV
is 9,861.972690405051 USDT, or -1.380273% on the original 10,000 USDT.
Compared with strict handling, net NAV and realized PnL are each higher by
5.579405654986 USDT: **0.055794 percentage points** of original funding.
Maximum observed net drawdown is slightly greater, 11.382277% versus 11.371658%.
These are outcomes of two execution assumptions, not proof of a superior policy
or bounds on real execution performance.

Ending position cost, raw/net open-inventory values, raw/net unrealized PnL,
reserved quote and owned quantities are identical between these two cases.
The NAV difference is additional realized PnL and cash. Posted fees increased
by 1.24683207 USDT and turnover by 1,246.831267169098 USDT; fees are already
included in reported PnL. The same accounting identities stated above hold.

| Venue / market | BUY-priority net NAV | Net return | Max observed net DD | Realized PnL | Net unrealized PnL | NAV change vs strict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gate BGSC/USDT | 508.006156 | -49.199384% | 49.208848% | 0.000000 | -491.993844 | +0.000000 |
| gate WPAY/USDT | 1,020.247863 | +2.024786% | 10.928559% | 20.247863 | 0.000000 | +0.000000 |
| htx ELA/USDT | 1,152.489195 | +15.248920% | 6.603352% | 152.489195 | 0.000000 | +0.404955 |
| htx MINA/USDT | 562.227738 | -43.777226% | 46.125772% | 8.054160 | -445.826422 | +0.000000 |
| htx XTZ/USDT | 1,223.356353 | +22.335635% | 5.195640% | 223.356353 | 0.000000 | +4.634506 |
| kucoin AKT/USDT | 1,098.135479 | +9.813548% | 8.254373% | 98.135479 | 0.000000 | +0.269971 |
| kucoin OP/USDT | 1,111.453541 | +11.145354% | 20.065398% | 111.453541 | 0.000000 | +0.000000 |
| okx 1INCH/USDT | 911.016248 | -8.898375% | 21.720478% | 76.131844 | -165.115596 | +0.000000 |
| okx AGLD/USDT | 1,157.995383 | +15.799538% | 20.313924% | 159.688425 | -1.693042 | +0.134988 |
| okx APE/USDT | 1,117.044734 | +11.704473% | 30.332516% | 137.460534 | -20.415800 | +0.134986 |

Seven allocations gained 880.722548042147 USDT and three lost
1,018.749857637096 USDT. BGSC remains the largest allocation loss and drawdown;
XTZ has the highest whole-funded allocation return. The unchanged BGSC and MINA
losses still total 929.766105366037 USDT. Five allocations have unchanged ending
NAV; the other five gains account for the portfolio difference. These rankings
describe this frozen cohort and do not justify selecting or removing markets
using the observed returns.

| Venue | Initial USDT | BUY-priority net NAV | Net return | Max observed net DD | Realized PnL | Net unrealized PnL | Posted fees | NAV change vs strict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gate | 2,000.000000 | 1,528.254019 | -23.587299% | 26.397212% | 20.247863 | -491.993844 | 5.024781 | +0.000000 |
| htx | 3,000.000000 | 2,938.073287 | -2.064224% | 12.276019% | 383.899709 | -445.826422 | 86.290241 | +5.039461 |
| kucoin | 2,000.000000 | 2,209.589019 | +10.479451% | 9.631714% | 209.589019 | 0.000000 | 46.836979 | +0.269971 |
| okx | 3,000.000000 | 3,186.056365 | +6.201879% | 17.149060% | 373.280803 | -187.224438 | 84.132195 | +0.269974 |

Gate remains the lowest-return and largest-drawdown venue. Venue funding,
reserve/open/unknown-order totals and net unrealized PnL are unchanged from the
strict case. Exact per-market cash, basis, owned/sellable/residual quantities,
fees, turnover, counts, benchmarks and utilization remain in `results.csv`;
venue accounting and benchmarks are in `exchange_results.csv`. Reported venue
net unrealized PnL is net inventory proceeds minus remaining acquisition cost.

The second case has 22,279 BUY fills and 5,458 SELL fills, all ordinary completed
basket sales. Its full-fill count identity is 58,484 placed = 22,279 BUY fills
+ 5,458 SELL fills + 30,731 cancellations + 16 open orders. Gross realized
profit is 987.017394399272 USDT, gross realized loss is zero and profit factor
is undefined/null. There are zero unknown orders, zero unsellable residuals,
five allocations with sellable inventory and five cash-only allocations with
a waiting seed. Global TP still never triggers; there are no cycle completions,
restarts, portfolio-exit SELLs or occupied exit slots. Symbol/exchange TP remains
disabled. Neither case exercises an actual historical portfolio liquidation.

The cash and buy/hold benchmarks are unchanged. This case is 35.508868
percentage points ahead of buy/hold and 1.380273 points behind cash. Coverage
is also unchanged: 10,005,677 processed rows, 993,572 complete timestamp groups,
998,743 known and 2,606 unknown grid-NAV observations, with known terminal marks.
Longest observed net underwater duration remains 100.627778 days. Per-allocation
utilization can change with modeled fills, but differing observed-bar coverage
still prevents a valid common-clock average from those allocation means.

There are 81 ambiguous bars, 3,473 post-gap refreshes, 16,366 emitted
`draining_unresolved_BUYs` notices and 1,199 lot-cap notices. Ambiguity counts
are path-dependent because eligibility depends on prior modeled fills; they are
not a count of immutable ambiguous source candles or elapsed inactivity.

The result SHA-256 is
`9a146cd763e406aab6160f10d5661b1b12d1fc9a199d1e2d3188b8c84e7b70b7`;
its receipt is
`/opt/grid-runs/module1-20261110/full-study-v4-notices/seed_multi_buy/receipt.json`.
Complete source identities and hashes were revalidated. The compact snapshot
retains both completed portfolio results without replacing the first.

## Retrospective monthly descriptions

The unchanged predeclared monthly convention was applied to all ten retained
NAV curves: **466 observed spans, 135 rising, 211 falling, 110 flat and 10
unclassified**. Results are outside Git at
`/opt/grid-runs/module1-20261110/monthly-full10-20261110T1641Z/`.
An independent exact-rational review matched all 13,896 retained NAV points
against original outputs and checked every monthly endpoint, label, change,
truncated return, unknown count and sampling gap. Its receipt is
`independent-verification.json` in that directory.

| Market | Retrospective direction | Classified spans | Median net endpoint return | Minimum | Maximum |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1INCH | Rising | 17 | +1.5227% | 0% | +17.2333% |
| 1INCH | Falling | 24 | -2.0551% | -9.3709% | +0.8399% |
| 1INCH | Flat | 10 | +0.3555% | -2.0676% | +4.5061% |
| MINA | Rising | 14 | +1.2658% | 0% | +10.9131% |
| MINA | Falling | 28 | -0.8462% | -11.3631% | +1.9318% |
| MINA | Flat | 9 | 0% | -0.7809% | +0.9531% |
| WPAY | Rising | 12 | 0% | 0% | +0.7559% |
| WPAY | Falling | 3 | 0% | 0% | 0% |
| WPAY | Flat | 10 | 0% | 0% | 0% |
| AKT | Rising | 20 | 0% | 0% | +3.0342% |
| AKT | Falling | 25 | 0% | -7.2171% | +1.0118% |
| AKT | Flat | 6 | 0% | -0.5608% | +10.2960% |
| AGLD | Rising | 20 | +0.3647% | 0% | +6.2499% |
| AGLD | Falling | 22 | 0% | -6.2921% | +2.0525% |
| AGLD | Flat | 9 | +0.9525% | -1.3572% | +13.7021% |
| XTZ | Rising | 12 | +0.6365% | -0.0438% | +12.4704% |
| XTZ | Falling | 23 | 0% | -7.6057% | +1.6704% |
| XTZ | Flat | 16 | +0.5447% | -0.1666% | +6.5732% |
| BGSC | Rising | 4 | +8.7173% | +1.1456% | +14.9277% |
| BGSC | Falling | 11 | -5.2901% | -31.4836% | -1.7493% |
| BGSC | Flat | 8 | -0.5051% | -0.7937% | +1.0800% |
| OP | Rising | 11 | 0% | 0% | +1.8097% |
| OP | Falling | 24 | 0% | -13.6733% | +1.4155% |
| OP | Flat | 16 | 0% | -4.9168% | +13.6141% |
| APE | Rising | 11 | +3.3138% | +0.8575% | +10.6652% |
| APE | Falling | 23 | -4.1802% | -15.6848% | +1.1328% |
| APE | Flat | 17 | -0.1009% | -1.5331% | +1.6143% |
| ELA | Rising | 14 | +0.5528% | 0% | +7.3476% |
| ELA | Falling | 28 | 0% | -7.2385% | +8.0640% |
| ELA | Flat | 9 | +0.3029% | -0.7994% | +3.3374% |

The ten unclassified spans are the final partial November 2026 intervals.
WPAY's many zero returns reflect low modeled participation, not evidence of a
profitable defensive policy. Falling-period losses and profitable-history
underwater durations remain visible; these descriptions do not establish
robust profits in bullish, bearish and flat markets.

Direction uses the funded buy/hold raw NAV change between each UTC month's
first and last retained observations: above +5% rising, below -5% falling,
otherwise flat. Spans below 20 days or with unavailable endpoints are
unclassified. This is retrospective description, not a live signal, validated
regime model or strategy-selection rule. Endpoints do not cover full calendar
months; intervening intervals are omitted, so returns are not compounded or
annualized. Capital was not reset monthly. Missing intraday observations
remain missing even when retained daily endpoints are present. The twelve
portfolio cases are described separately over matched endpoints below as they
complete; two are available in this snapshot.

### Matched portfolio monthly descriptions

The same unchanged convention gives 24 spans for each completed portfolio:

| Case | Direction | Spans | Median net endpoint return | Minimum | Maximum |
| --- | --- | ---: | ---: | ---: | ---: |
| Strict | rising | 6 | +1.497246% | -1.662272% | +4.308538% |
| Strict | falling | 9 | -0.959332% | -3.756156% | +2.775243% |
| Strict | flat | 8 | -0.289813% | -1.314598% | +1.952047% |
| BUY priority | rising | 6 | +1.496521% | -1.661347% | +4.308423% |
| BUY priority | falling | 9 | -0.959323% | -3.756156% | +2.784262% |
| BUY priority | flat | 8 | -0.289813% | -1.306813% | +1.952047% |

Each case has one unclassified final partial November 2026 span: strict
-0.017595%, BUY priority -0.017585%. The combined extraction now contains
**514 spans and 15,290 retained NAV points** across ten individual studies and
two portfolios: 147 rising, 229 falling, 126 flat and 12 unclassified spans.
`/opt/grid-runs/module1-20261110/monthly-portfolio2-20261110T1748Z/`
retains exact rows and an independent Fraction-based check of every endpoint,
label and return against original outputs. The first portfolio's earlier
490-span/14,593-point extraction remains preserved in
`/opt/grid-runs/module1-20261110/monthly-portfolio1-20261110T1715Z/`.
All earlier individual and first-portfolio observations are unchanged.

These retrospective samples do not establish positive performance in every
market condition and are not compounded into a new return series. Daily
retained points cannot independently reconstruct the engine's minute-observation
drawdown.

## Measured execution and reproducibility

GCC 13.3.0, C++20, CMake 3.31.6, OpenSSL 3 exact integer/rational primitives;
portable Release build without fast-math or native-CPU flags. Each child has a
512-MiB address-space ceiling, six-hour timeout and 32-MiB output-file ceiling.
Input uses bounded 98,304-byte source buffers; runs are sequential.

| Completed run | Processed rows | Wall seconds | Rows/second | Peak child RSS KiB | Result bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| full_okx_1INCH | 2,235,727 | 554.083439 | 4,035.0 | 11,852 | 305,928 |
| full_htx_MINA | 2,235,725 | 502.651547 | 4,447.9 | 12,804 | 306,500 |
| full_gate_WPAY | 1,080,076 | 147.458172 | 7,324.6 | 13,756 | 155,252 |
| full_kucoin_AKT | 2,235,726 | 364.190746 | 6,138.9 | 13,932 | 305,909 |
| full_okx_AGLD | 2,235,727 | 467.775299 | 4,779.5 | 13,932 | 310,150 |
| full_htx_XTZ | 2,235,725 | 511.202550 | 4,373.5 | 13,936 | 307,777 |
| full_gate_BGSC | 999,391 | 194.730632 | 5,132.2 | 13,936 | 140,472 |
| full_kucoin_OP | 2,235,725 | 434.288015 | 5,148.0 | 13,940 | 305,943 |
| full_okx_APE | 2,235,727 | 550.585314 | 4,060.6 | 13,940 | 302,399 |
| full_htx_ELA | 2,235,723 | 483.611522 | 4,623.0 | 13,944 | 308,872 |
| seed_multi_strict | 10,005,677 | 2,035.244700 | 4,916.2 | 13,944 | 181,876 |
| seed_multi_buy | 10,005,677 | 2,015.504502 | 4,964.4 | 13,952 | 181,860 |

The ten sequential child replays totaled 4,210.577237 wall seconds
(70.176287 minutes). This is completed financial replay of
19,965,272 records, separate from the earlier source-scan benchmark.
The first matched portfolio took another 2,035.244700 seconds (33.920745
minutes), including complete validation of all ten sources and replay of the
10,005,677 rows inside the common interval. Its user/system CPU times were
2,034.115960/0.877905 seconds, giving a CPU/wall ratio of 0.999877. The second
matched portfolio took 2,015.504502 seconds (33.591742 minutes), with
2,014.691530/0.586941 user/system CPU seconds and a CPU/wall ratio of 0.999888;
it performs the same full-source validation and processes the same aligned rows.

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

The later CLI stdout-flush correction and additional guard tests are documented
in `CPP_BACKTEST_VERIFICATION.md`. They passed fresh builds, but the historical
driver retains its original copied executable. These historical results are
not relabeled as runs of the corrected binary. Complete-output/hash/accounting
validation, not a successful child exit code alone, is required for PASS.

Executed full-study command (already running; do not duplicate its directory):

```sh
/opt/grid-tools/module1-py312/bin/python -m scripts.module1_study --config research/runs/module1-20261110/experiment_config.json --binary /opt/grid-builds/module1/release/grid_backtest --output /opt/grid-runs/module1-20261110/full-study-v4-notices --group all --execute --timeout-seconds 21600
```

Current snapshot table and monthly analysis commands (working directory
`/opt/grid`):

```sh
/opt/grid-tools/module1-py312/bin/python -m scripts.module1_report --study /opt/grid-runs/module1-20261110/full-study-v4-notices --output /opt/grid-runs/module1-20261110/tables-portfolio2-20261110T1748Z
/opt/grid-tools/module1-py312/bin/python /opt/grid-runs/module1-20261110/analysis-monthly.py --input /opt/grid-runs/module1-20261110/tables-portfolio2-20261110T1748Z/nav_curve.csv --output /opt/grid-runs/module1-20261110/monthly-portfolio2-20261110T1748Z
```

Outputs are exclusive. Reproduction must use a new output directory. The report
command validates copied config/cohort/binary/input/result hashes and independently
checks funded returns, allocation identities and aggregate accounting. It does
not execute a strategy. Source files and all prior attempts remain untouched.
The unchanged monthly script SHA-256 is
`cb65c7a6a17b3965ca8ecdb41cd60218eae0c710aafbe47e256e77ea8cfd492d`.
Another-host reproduction requires that script and the referenced external
source/result artifacts as well as Git; large artifacts are intentionally not
packaged in the repository. Historical replay starts from the beginning and
does not support checkpoint resume.

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
| Full replay of every selected source | 10/10 PASS; all 19,965,272 actual source records replayed |
| Twelve matched funded portfolio cases | 2/12 PASS; one INCOMPLETE, nine NOT_RUN in this snapshot |
| Actual accounting, metrics, benchmarks and reports | All ten individual histories and two matched portfolios, each including four venues and ten allocations |
| Retrospective rising/falling/flat descriptions | All ten individual histories plus two portfolios; 514 spans independently checked |
| One-year ten-market exploratory objective | NOT MET yet; aligned portfolio runs incomplete; 695-day calendar duration available with explicit gaps |
| Gap-free shared one-year data | NOT MET by this cohort; 66 consecutive complete UTC days |
| One-year dozens-of-symbols objective | NOT YET TESTED; ten markets cannot satisfy it |
| Historically executable profit / live readiness | NOT PROVEN; outside OHLC evidence and current authorization |

Continue the existing bounded driver through all twelve portfolio comparisons.
Preserve failures and negative outcomes. Then regenerate
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
