# Strategy comparison and controlled improvement plan

Reviewed against the saved Binance BTC/USDT runs, the Python trading core,
the funded C++ backtester and the audited `/opt/module1_n` source. This is an
offline research plan, not a forecast of future returns. The original PRD,
acceptance plan and imported master prompt remain unchanged.

**Current scope, 12 November 2026: USDT-quoted spot pairs only for all new
backtests, simulations and trading selection.** Older non-USDT examples below
are retained historical evidence, not enabled trading choices. The implemented
restriction and completed accounting review are recorded in the
[latest review](#htx-batch-2-review-and-usdt-only-policy--12-november-2026).

**The first priority is to make comparisons correct; the first strategy
experiment is to address indefinitely waiting seed orders.** Increasing order
size or removing execution safeguards would not establish a trading advantage.
There is currently no measured result showing that a changed funded strategy
outperforms the existing funded strategy under the same constraints.

The owner approved an exact matching research mode for the paired web runs,
with funded trading validation kept separate. Matching mode deliberately
reproduces the audited simulator's assumptions. It is separate from the
production strategy and from the existing funded backtest. `/opt/module1_n`
must remain unchanged.

Whole-exchange runs use each pair's own full archived history, as explicitly
chosen by the owner. Both engines receive the same window within each pair;
the owner's subsequent correction requires combined charts and ending PnL to
include every retained trade from every successful pair, even with disjoint or
differently ended histories. A shared-window mode remains available. Carried
last marks and sums of terminal values from different dates are explicitly
labeled; separate histories are not a synchronized funded portfolio.

The initial matching-mode matrix passed seven historical pair comparisons
across five suites, including a substantial losing year. A separate 32-market
batch then matched 31 pairs and retained one explicit precision-rule failure.
These results are recorded below. Hosted browser/deployment acceptance is a
separate check; the deployed UI and final verification are recorded at the end.

## 1. What the existing evidence actually shows

All three runs below use the complete Binance BTC/USDT source, from
2017-08-17 04:00 UTC through the last candle at 2026-11-03 20:08 UTC. The exact
requested interval is `[1502942400000, 1793736540000)` UTC milliseconds.
The file contains 4,837,937 candles and 35 gaps totaling 8,632 missing minutes.
Its SHA-256 is
`b32ee355e9b5e0bd49ec65a47ac3bb602493b55c1f4888805cdf552ab4b6fc0c`.

| Measured result | Funded, one pending lower BUY | Funded, three lower BUYs | Audited simulator |
|---|---:|---:|---:|
| Net PnL, USDT | 24.862276 | 30.668053 | 758.649402 |
| Realized PnL, USDT | 24.862276 | 30.668053 | 761.973320 |
| Unrealized PnL, USDT | 0 | 0 | −3.323917 |
| Paid fees, USDT | 12.512172 | 15.433983 | 512.387498 |
| BUY / SELL fills | 599 / 196 | 739 / 208 | 12,931 / 4,888 |
| Peak position cost, USDT | 134.916680 | 228.213307 | 996.116742 |
| Original funded cash, USDT | 1,000 | 1,000 | Not defined; cash may be negative |
| Net funded return | 2.486228% | 3.066805% | Not defined |
| Drawdown | 2.893003% of liquidation-adjusted NAV | 4.436069% of liquidation-adjusted NAV | 373.262341 USDT absolute |
| Terminal inventory, BTC | 0 | 0 | 0.0012 |
| Terminal state | One pending seed BUY | One pending seed BUY | Open basket |

Exact run identities:

- One lower BUY: `run-20261111T200512Z-819c69a9`; its attached audited partner
  is `legacy-20261111T200600Z-f31ceda9`. The original saved inputs and results
  are under `/opt/grid-runs/backtest-ui/`.
- Three lower BUYs: `run-20261111T192943Z-68e8b481`; audited partner
  `legacy-20261111T193032Z-a01042f6`, suite
  `paired-20261111T192938Z-068e5198`. Copies and verification are under
  `/opt/grid-runs/ui-comparison-20261112/`.
- The local audited run with order quote 10 and averaging 0.0001 matched the
  hosted audited run in all 39 deterministic summary fields. Its complete
  trades and chart-series files were byte-identical. This is documented in
  [the earlier engine comparison](BACKTEST_ENGINE_COMPARISON.md).

The audited simulation needed a maximum cash deficit of **588.728546 USDT**
when starting from zero. That is a measured financing requirement for its
assumed execution path, not evidence that an unfunded spot account can trade.
Its absolute PnL is not a return on 1,000 USDT. Its drawdown cannot be compared
directly with a funded percentage drawdown.

The one-lower-BUY funded run held inventory during only **4.245942%** of
observed candle closes. Its average acquisition cost was approximately
**1.47 USDT**, or **0.146755%** of original funding. It ended with one pending
seed and **5.378215 USDT reserved**. Saved daily NAV remained flat from
2020-03-14 to the end of the test. Both funded variants show this long idle
period. This is strong evidence that entry/reentry deserves investigation.
The saved report does not retain the final order's complete event history;
an exact last-fill or missed-low sequence must be reproduced in an explicit
trace before presenting it as a proven cause.

The one-BUY run recorded only **two ambiguous candles** and its peak position
cost was well below the 900-USDT commitment cap. The evidence therefore does
not support attributing the entire PnL difference to ambiguous-bar rejection
or insufficient capital. Nor does it quantify the separate contribution of
each strategy difference; that requires controlled experiments.

The lower saved-run section in the attached page showed a separate **HTX**
BTC/USDT result, 11.1848 USDT, while the upper suite showed Binance. Independent
UI selections caused misleading context. This is a presentation defect, not
evidence of mixed-source financial calculations.

### New matching-mode results

The new current research implementation and the unchanged audited executable
were run with identical source, exact UTC window, precision and economic
settings: 1% step, 1% target, first-order quote 10, averaging 0.0001 and 0.2%
fees per side. All seven pair comparisons passed settings/summary equality
and byte equality of the complete trade and chart-series artifacts.

| Market and window | Quote | Total PnL, both engines | Realized PnL | Unrealized PnL | Maximum absolute drawdown |
|---|---|---:|---:|---:|---:|
| Binance BTC/USDT, full archive | USDT | 758.6494022408 | 761.9733196538 | −3.3239174130000007 | 373.2623414592125 |
| Binance BTC/USDT, calendar 2021 | USDT | 101.035365527375 | 152.2106857394 | −51.175320212025 | 170.9772333612 |
| Binance BTC/USDT, calendar 2022 | USDT | **−306.58442318283124** | 22.7153045082 | **−329.29972769103125** | 373.2623414592125 |
| Binance BTC/USDT, calendar 2023 | USDT | 39.097296519 | 39.4283303372 | −0.3310338182 | 29.465103620625 |
| Binance ETH/BTC, calendar 2023 | BTC | **−24.61328689757828** | 5.390247300906 | −30.00353419848428 | 54.21182561595128 |
| Gate BTC/USDT, calendar 2023 | USDT | 39.1509150678625 | 39.4827864634 | −0.33187139553749995 | 29.58742347164375 |
| HTX BTC/USDT, calendar 2023 | USDT | 34.08961000213852 | 34.36664923196748 | −0.277039229828964 | 27.061326418217387 |

Calendar years use `[January 1 00:00 UTC, next January 1 00:00 UTC)`.
Each annual case starts afresh; these annual PnLs cannot be added to reproduce
the continuous full-history result. These are calendar comparisons, not a
completed preregistered bullish/bearish/flat study.

The 2022 case is particularly important: positive realized basket profit
coexists with a much larger open-position loss. Its peak cash deficit was
975.3936709758 USDT. Reproducing the audited rules therefore does not solve
downside exposure. In ETH/BTC the first-order quote is **10 BTC**, not 10 USDT;
its figures must remain in BTC and must never be added to USDT totals.

Receipts are in
[the comparison matrix](/opt/grid-runs/shared-reference-20261111/matrix-progress.json).
The suites are `paired-20261111T203305Z-0b75372c` (full BTC),
`paired-20261111T203309Z-b7f70232` (2021),
`paired-20261111T203401Z-383328bf` (2022),
`paired-20261111T203402Z-e3e965ba` (2023), and
`paired-20261111T203404Z-237601e8` (three venues/quote groups).
The matrix records child run IDs, hashes, gaps, precise inputs and all outcomes.
These are matching-mode results; no new funded TTL/reseed profitability result
is claimed.

### Actual 32-market batch and precision limits

A separate backend batch froze the first 32 eligible Binance USDT filename
IDs in case-insensitive order whose boundary coverage included calendar 2025.
The selection preceded all returns; no failed market was replaced. It used
the same 1% step/target, 10-quote first order, 0.0001 averaging and fixed 0.2%
fees, with the saved CCXT snapshot where available. The declared fallback
ticks were 0.00000001 price and 0.0001 amount.

The final attempt, `paired-20261111T204155Z-e208b7e6`, attempted all 32 markets
and correctly ended **INCOMPLETE: 31 matched pairs, one failed market**.
All 31 completed pairs matched every deterministic financial summary field
and every byte of their trade and chart-series files. ANKR's audited leg
failed; its current leg was explicitly skipped. The first attempt, which
stopped at that failure, remains preserved beside the completed-roster attempt.

| Measured work, final attempt | Current matching mode | Unchanged audited engine |
|---|---:|---:|
| Successful market runs | 31 | 31, plus one retained failure |
| Replayed candles in successful runs | 16,293,600 | 16,293,600 |
| Full-source records validated in successful runs | 69,327,053 | 69,327,053 |
| BUY / SELL fills | 118,652 / 35,369 | 118,652 / 35,369 |
| Missing minutes in successful selected windows | 0 | 0 |
| Engine-reported time, successful runs | 5.308 s | 5.472 s |
| Worker wall time, including launch and verification | 14.970 s | 15.797 s, including failed leg |
| Maximum individual engine process RSS | 108 MiB | 106.5 MiB |

The **whole submitted backend batch took 41.065 seconds**, including both
engines, retained files, verification and orchestration. These local timings
used warm filesystem caches; they are not a cold-disk or concurrent-user
benchmark. Memory includes mapped source pages and process startup. Workers
run sequentially. This follow-up did not require an additional C++ kernel
optimization and did not change the funded engine's financial rules. Matching
mode timing must not be presented as a like-for-like speedup of funded replay.

Partial totals include **31 successes only; ANKR is excluded**. Both engines
produced the same retained-value sum: PnL **5424.393430793193079 USDT**, realized
PnL **7270.65014240687998**, unrealized PnL **−1846.256711613686885425** and fees
**4732.09477716294 USDT**. The original close-only aggregate had 2,190 common
four-hour closing samples and took 0.459 seconds separately. That reporting
method has been superseded by the all-event aggregation below; the original
receipt remains retained. These are independent, unfunded research
runs, not a shared-wallet portfolio return. They do not establish profitability
for all 32 requested markets or for other settings and periods.

ANKR's recorded 2025-10-10 21:51 UTC candle has a float32 low of
0.000009999999747378752 against a saved price tick of 0.00001. The reference
fills a BUY at that one-tick price; its next 1%-lower BUY rounds down to zero,
so it refuses with “Grid reached price precision limit.” This is a price-grid
boundary, not insufficient arithmetic precision. More floating-point digits
cannot create an admissible positive price below one tick. The extreme wick
is retained as supplied; its real-market accuracy has not been independently
established, and a 2026 snapshot does not establish the historical price rule.

Available precision is explicitly bounded. Original OHLC observations have
24 binary significant bits (float32); lost source digits cannot be recovered.
Matching arithmetic uses this platform's `long double`: **64 significant
bits, 18 decimal digits and 21 digits for round-trip serialization**, despite
occupying 16 bytes. Shared settings cross Python float and the reference's
default JSON binary64 parser; summary/trade/chart outputs retain binary64
values. The current matching mode preserves that contract rather than claiming
extra input or output accuracy. The funded engine continues to use its separate
exact rational and checked currency-unit arithmetic. Fast-math is disabled.

Evidence: [frozen roster and settings](/opt/grid-runs/shared-research-batch32-real-20261111/declaration.json),
[actual backend timings](/opt/grid-runs/shared-research-batch32-real-20261111/attempt2/benchmark.json),
[independent parity checks](/opt/grid-runs/shared-research-batch32-real-20261111/attempt2/independent-parity.json),
[partial totals](/opt/grid-runs/shared-research-batch32-real-20261111/attempt2/partial-totals.json)
and [ANKR refusal evidence](/opt/grid-runs/shared-research-batch32-real-20261111/first-attempt-parity-and-refusal.json).
Dataset identities, both executable hashes and backend file hashes remained
unchanged during the final attempt; successful workers also verified source
hashes before and after replay. The unchanged audited source SHA-256 remains
`196f65c5df6af76aca65ac8c67be7db91e57ca32039313773d877c1c8b3d765e`,
and its original and copied executables both remain
`7405ec85f42811ac7cea6b1693592e3db574528142ccaecf0f600e8261dbe06f`.
This batch evidence does not complete the separate overall test/deployment
acceptance work.

### All-trade combined charts and ending PnL

The owner's correction replaces overlap-only totals. The revised helper reads
every successful pair's full trade file and retained chart closes, rechecks their
hashes, and sums its authoritative terminal result. It keeps native quote groups
separate and explicitly reports failed pairs. Differing end dates no longer
discard PnL or trades. OHLC chart buckets preserve extrema of the combined
retained-event sequence; last marks carry between observations and can be stale.

The same saved results were used for these checks; neither engine was rerun or
modified to improve its PnL:

| Saved suite | Included / requested pairs | Trades processed per engine | Saved closes per engine | Terminal PnL, USDT | OHLC buckets | Aggregation time |
|---|---:|---:|---:|---:|---:|---:|
| Hosted own-history BTC/USDT + ETH/USDT | 2 / 3 | 46,704 | 6,732 | 1996.1577799215032 | 4,439 | 0.921 s |
| Declared 32-market Binance batch | 31 / 32 | 154,021 | 67,890 | 5424.393430793193079 | 4,839 | 3.932 s |

Both engines' aggregate results matched exactly. Every processed-trade count
equaled the included BUY + SELL counts, and each last combined candle close
equaled the sum of the original pair summaries. The process's observed peak RSS
was 32,944 KiB after the first case and 66,992 KiB after both; the second is a
cumulative process peak, not an isolated benchmark. Times exclude engine replay.
Bounded record buffers close each file between reads, and each engine/quote
group emits at most 5,000 OHLC buckets without discarding trade events.

The own-history suite retains the failed HTX BTC/USDT member: its declared
synthetic amount tick makes a 10-quote order round to zero at high prices. The
32-market suite retains ANKR's price-grid refusal described above. Neither is
counted as successful or replaced with a favorable precision assumption.
Evidence: [all-event verification and timings](/opt/grid-runs/all-events-totals-20261111/benchmark.json),
[hosted full-history totals](/opt/grid-runs/all-events-totals-20261111/hosted-own-history-totals.json)
and [32-market totals](/opt/grid-runs/all-events-totals-20261111/batch32-totals.json).

## 2. Three different engines and their responsibilities

| Behavior | Python trading core | Existing funded C++ backtester | Audited `/opt/module1_n` |
|---|---|---|---|
| Purpose | Durable order/fill-driven spot strategy; current integration remains offline/mock | Fast research counterpart to the trading rules, plus declared research variants | Single-pair OHLC research simulator |
| Financial arithmetic | Decimal and exact currency units | Checked exact rational/quantized arithmetic | Its existing floating-point arithmetic and precision rules |
| Initial entry | Minimum LIMIT from a fresh bid/ask book; await actual fill | Minimum LIMIT from completed-close proxy; wait for eligibility | First BUY one step below first observation; waiting entry follows rising observations |
| Unfilled seed | No automatic TTL/reseed implementation | Same indefinite waiting in baseline | Waiting BUY rises while no active basket exists |
| BUY size | Smallest valid executable lot | Same; saved web baseline uses synthetic 5-quote notional minimum | Initial quote amount, then quote growth `1 + step_fraction × averaging` |
| Grid below anchor | `anchor / (1 + g)^k`, quantized down | Same; `ladder_one` has a separately declared fresh-price constraint | Successive `previous_buy × (1 − g)`, quantized down |
| Basket SELL | One possibly active aggregate SELL; reconcile before replacement | Same ownership/one-SELL contract under synthetic lifecycle | Aggregate basket sale at simulated crossing, no external order lifecycle |
| SELL target | Geometric floor over fee-inclusive acquisition cost, plus positive net minimum | Same baseline formula | Percentage above purchase cost before BUY fees |
| Partial/unknown orders | Persist and reconcile; never infer fills from acknowledgements | Deterministic tests; historical primary model uses full eligible fills | Immediate hypothetical fills; no private-order uncertainty |
| Execution timing | Actual future venue evidence would determine fills | Existing orders only; positive eligibility/cancellation delay; three explicit OHLC conventions | Assumed O → H → L → C; every crossed level can fill within that path |
| Funding and exposure | Finite allocated cash, reservations and configured limits | Finite cash, reserve, committed-cost/lot/order limits | Zero initial cash; negative cash and uncapped accumulation permitted |
| Portfolio +10% | Bounded LIMIT exit attempt, not assured profit | Enabled in the measured runs; no trigger in these examples | No funded portfolio cycle |
| Precision | Normalized venue rules, including significant digits in Python | Funded C++ baseline supports constant ticks; these runs used synthetic rules | Saved CCXT precision or declared fallback; historical validity remains unknown |

The quoted **0.01 USDT** funded target is only its minimum net-profit floor.
The saved 1% grid also supplies a **1% geometric SELL floor over fee-inclusive
cost**. Describing the funded strategy as selling for only 0.01 USDT was
incomplete. In Python this is `domain/market.py::aggregate_take_profit`; the
C++ implementation is `cpp/backtest/market.cpp::aggregate_take_profit`.

For the latest one-BUY example, average funded BUY notional was approximately
5.19 USDT, while audited first-order quote was 10 USDT. The audited amount
tick was 0.00001 BTC and price tick 0.01 USDT; funded baseline ticks were
0.0001 BTC and 0.00000001 USDT. Both charged 0.2% on each side. The different
fee totals arise principally from different turnover, not different rates.

The Python `Policy` currently has no seed TTL or retry fields. Its reducer
returns without replacing a working seed; a terminal unfilled seed requires
an explicit new round. The funded C++ reducer has the same behavior.
PRD §3.2 explicitly permits a configured timed cancel/reseed policy with a
fresh quote, bounded retries and a logged reason. Implementing that bounded
option is compatible with the specification; pretending that a live order
can follow price without cancel/reconcile is not.

The existing C++ Python-parity tests cover declared baseline lifecycle/math
fixtures. Matching the audited simulator exercises a different research
contract. Neither comparison certifies real exchange execution. No production
venue is currently certified and no authenticated trading sender is enabled;
see [the implementation review](IMPLEMENTATION-REVIEW.md).

## 3. Exact matching mode: what must be identical

The paired action must derive both members from **one frozen request**, not
two similar forms. The following are equality conditions, not optional labels:

| Input or assumption | Required matching behavior |
|---|---|
| Markets | Identical exchange/base/quote roster; no silent pair exclusions |
| History | Same immutable source hash and exact half-open UTC window for each pair |
| Parameters | Same step, basket target, first-order quote, averaging and precision |
| Fees | 0.2% BUY and 0.2% SELL because the unchanged audited binary fixes these values; unsupported rates must be rejected, not ignored |
| Precision | Same frozen saved snapshot or same explicit synthetic fallback; same rounding modes |
| Entry/grid/exit | Same initial/trailing entry, multiplicative descending steps, basket-cost definition and restart rules |
| Execution | Same O/H/L/C order and crossed-level fills; preserve gaps |
| Funding | Same unfunded independent-pair convention; label cash deficits explicitly |
| Charts | Same aggregation calendar/interval, complete trade markers and ending inventory |
| Native quote units | 10 means 10 units of that pair's quote; never sum BTC, USDT and USD PnL without verified FX |
| Saved results | Charts, metrics and settings selected through the same immutable suite identity |

A changed form starts a new suite; it must not relabel an old result as though
it used the new form. Historical funded suites retain their old model labels.
The funded validation action is separate and exposes its actual differing
capital, order lifecycle and execution assumptions.

Batch totals group independent runs by native quote and process every retained
trade, every retained chart close and each authoritative terminal summary. They
sum each pair's final PnL regardless of its final timestamp; the last combined
chart close must equal that sum. A pair contributes zero before starting and its
last recorded mark between updates and after ending. These carried values may
be stale; terminal date ranges and actual per-pair windows remain visible.
Recompute drawdown from the combined event path; never add individual drawdowns
or OHLC extrema. Bounded OHLC buckets preserve every processed event's extrema
without discarding trades. Fixed trade-before-close and market ordering resolves
equal synthetic timestamps. This does not recover unrecorded intrabar marks or
establish synchronized real portfolio risk. Missing minutes remain reported.
A batch with rejected/failed members must show its
requested, included and failed roster; partial totals must never imply all
selected markets completed successfully.

Detailed values retain the precision serialized by the engine. Audited trade
and series artifacts contain binary64 values, and original OHLC prices are
float32; extra displayed digits cannot recover source precision. Aggregate
values add the retained round-trip decimal values without an extra float
summation. This is a precise reporting method, not a claim of infinite
historical precision.

The correct acceptance result is agreement of deterministic summaries,
trade sequence, prices, sizes, fees and chart data, with the exact binaries
and canonical inputs recorded. Explain any allowed representation difference
before testing. Do not silently widen a financial tolerance or hide a losing
member to manufacture agreement. Repeated agreement is implementation
compatibility, **not an increase in trading profitability**.

## 4. Step-by-step development and research sequence

### Step 1 — Establish a trustworthy comparison

Completed: the matching mode and shared web request reproduce the full
Binance BTC/USDT result and a three-pair, two-quote hosted suite. Test that
changing the pair, dates or settings produces a new suite and all displayed
results follow it. Check unsupported fees, precision rounding and gaps.

Acceptance: both current matching mode and unchanged audited engine consume
the same canonical settings and yield matching deterministic economic outputs;
the separately available funded mode still passes its original invariants.
Attach actual run IDs and comparison receipts. Keep all prior results.

### Step 2 — Make funded idleness observable

Save a compact terminal-order description and diagnostic counters in funded
reports: seed price/age, idle duration, inventory occupancy, blocked-reason
counts and cancellation/reseed attempts. Add a bounded trace around the
March 2020 transition to confirm the precise order sequence. This does not
require storing every candle or introducing another database.

Acceptance: a flat NAV curve is explainable as a working order, risk stop,
data gap or finished cycle. Diagnostic changes must leave financial results
unchanged. A pending order must not be called an engine hang.

### Step 3 — Test a bounded seed-expiry policy first

Retain `fixed seed` as the control. Add one optional policy to the existing
funded strategy path: a completely unfilled seed can expire; cancel it,
reconcile terminal state and any late fills, then reprice from a fresh allowed
book observation. Reuse the existing executor/reducer and reservations.

Start with two declared research candidates: a 60-minute and a 1,440-minute
seed TTL. These are experimental comparison points, **not recommended live
values or measured optima**. For each use at most three reseeds per declared
24-hour interval. Reuse the existing fresh-book, spread, active-range and
budget gates; use the passive bid policy rather than inventing a market fill.
Exhaustion leaves a visible paused entry until the next declared interval or
operator review; it must not spin or silently reset its budget. Persist the
retry interval and count so restart cannot grant more attempts.

The exact retry-window boundaries and reset must be frozen before comparing
returns. If both candidates frequently pause, report that failure; do not
increase the permitted activity until a profitable chart appears.

No new BUY may be submitted while the old seed is possibly active. A late or
partial fill becomes owned inventory and suppresses duplicate seeding. Unknown
cancel, stale book, config changes, restart, fee overrun and insufficient cash
must retain their existing stops. Entry attempts remain LIMIT orders and use
minimum executable lots. The new policy needs shared Python/C++ event fixtures
before any production adoption; the live default stays unchanged pending
funded evidence and trader review.

Acceptance: no duplicated exposure or financial regression in failure tests;
then report funded PnL, drawdown, time invested and cancel load for every
candidate versus fixed seed. Increased trade count alone is not success.

### Step 4 — Measure order placement and targets independently

After isolating seed behavior, compare one versus three pending lower BUYs
with the same cash, rules, fees and execution scenario. Existing runs show
that three levels earned more in this single history, with greater drawdown
and peak position cost; they do not establish a general winner.

Next compare a small declared grid set, for example 0.5%, 1% and 2%, with all
other inputs fixed. In the funded baseline, spacing also changes the geometric
SELL floor, so label this a combined grid/target change unless a separately
approved target parameter is introduced. Test such a parameter only if the
trader needs independent control; avoid a large multidimensional optimizer.

Keep constant minimum-lot sizing in the production baseline. The audited
10-quote entry and averaging factor can be explored in matching mode. Adopting
fixed quote sizing or increasing averaging in the funded/live strategy would
be a separate change to the original minimum-lot/no-increasing-size decision.
Higher absolute PnL obtained by using more capital is not itself evidence of
better capital efficiency.

Acceptance: retain all candidates, failures and uncertainty. Prefer a broad
region of acceptable settings across periods over one isolated best result.
Use net funded performance and exposure, not realized basket profit alone.

### Step 5 — Evaluate bullish, bearish and flat periods separately

Freeze markets, periods, data-quality gates and candidate settings before
running a new comparison. Start with Binance BTC/USDT to reproduce the bug,
then add eligible target-venue histories and several distinct assets selected
by data quality and market availability, not by profitable outcomes.

Use nonoverlapping chronological windows and later held-out windows. For a
simple diagnostic regime split, predeclare 90-day windows and classify their
underlying first-to-last price return as bullish above +10%, bearish below
−10%, otherwise flat. These thresholds are proposed labels, not tested
settings or trading signals. Also show each window's price range and drawdown:
a near-zero endpoint return can conceal a severe crash/recovery. Require
representation in each group before making a cross-regime claim.

Only training windows may select settings; apply the selected configuration
unchanged to later windows. Separately report a continuous funded full-history
run, carrying capital and inventory through regime boundaries. Fresh-funded
window experiments must not be presented as a continuous portfolio.

The full Binance history has already been examined in this project. Reusing
parts of it as a chronological holdout supports a controlled retrospective
comparison, but cannot honestly make it new, unseen evidence. Preserve that
disclosure and reserve uninspected markets/windows or subsequent future data
for stronger confirmation.

Acceptance: publish every window, including losses, halted entries, stuck
inventory and blocked exits. Set the required return, drawdown, funding,
inventory-age and order-load limits before looking at candidate results. No
single profit threshold is invented here on behalf of the trader.

### Step 6 — Restore execution uncertainty and capacity constraints

Matching-mode success is followed by finite-wallet evaluation. Use the same
funding, start/end, market rules and fee assumptions for every funded candidate.
Run the three existing OHLC conventions and separate higher-fee/latency/spread
stress scenarios. These conventions are sensitivity cases, not proven
mathematical best/worst return bounds. Do not select whichever execution
assumption produces the best return.

Report raw and liquidation-adjusted NAV, realized/unrealized PnL, fees,
drawdown and its coverage, peak cost, reserved cash, longest held inventory,
utilization, blocked reasons, cancel counts, dust and unfinished exits.
Compare funded cash and funded buy-and-hold at the same endpoint, retaining
the latter's counterfactual-fill limitation. Keep quote currencies separate.

Acceptance: no negative spendable cash, no duplicate SELL, no loss of owned
inventory, no unknown-order bypass. If performance disappears after reasonable
execution stresses, report that the edge has not been demonstrated.

### Step 7 — Transfer only a demonstrated, small policy change

If a candidate survives the preceding gates, review its exact financial and
operational tradeoffs with the trader. Implement only that policy in the
Python core, through the existing one-writer/one-executor architecture; use
the C++ counterpart for speed and Python differential tests for the common
contract. Do not route matching-mode synthetic fills into live trading.

Complete the remaining exchange/account, observed soak and operator gates
before any real trading. Research UI access is not live-trading authorization.
The observed one-hour public-data run did not satisfy the original 48-hour
acceptance requirement or prove private-order behavior. No new exchange
requests or paid GitHub feature is needed for steps 1–6.

## 5. Data sufficiency and remaining limits

The validated binary data are sufficient for repeatable OHLC scenario studies,
cross-implementation comparisons, exact coverage/gap accounting and bug
reproduction. They are insufficient to establish real fills or future profit.
Missing facts include volume, bid/ask spread, order-book depth, queue priority,
actual latency/cancel races, historical fees/precision/minimums, collection
provenance, delisted-universe completeness and verified historical spot status.

The complete [HTX/Gate audit](HTX_GATE_DATA_AUDIT.md) measured 99.999941%
timestamp coverage for HTX and 55.136197% for Gate inside each file's endpoints.
Those venue-wide statistics do not admit every individual file or prove
liquidity. Gate must be evaluated per selected window, without interpolation.
HTX continuity does not supply missing execution fields. A current CCXT snapshot
is useful for consistent assumptions, not proof of 2017-era rules.

## 6. Decisions to take to the trader

1. When may a zero-fill seed be canceled and repriced, and how much entry
   chasing and order churn is acceptable? What should happen after its retry
   budget is exhausted?
2. What are the maximum committed capital, drawdown and inventory holding
   duration in a prolonged decline? Should a breach stop new buys, hold the
   basket, or attempt a separately defined bounded LIMIT exit?
3. Should the strategy keep minimum executable lots, or is a separately
   funded fixed-quote policy worth evaluating? What capital budget must remain
   the same when comparing them?
4. Is basket profit defined over gross purchase cost or fee-inclusive cost,
   and should its target be independent of grid distance?
5. What performance is acceptable in each market regime relative to cash
   and buy-and-hold, after fees and open-position losses? A spot grid cannot
   be assumed to profit in every declining window.
6. Which exact target-venue markets and capital/risk limits should define
   the first funded validation cohort? Which historical and forward evidence
   would justify a subsequent live-policy approval?

Until those criteria and measured funded outcomes exist, **improved
profitability remains unproven**. Matching the audited simulator is the first
correctness milestone; bounded reentry and controlled funded evaluation are
the next strategy milestones.

## 7. Implementation verification

- C++ release checks: **9/9 passed**. Address/undefined-behavior/leak sanitizer
  checks: **9/9 passed**. The comparison matrix includes passing and refused
  cases; successful cases compare complete binary trade/series outputs.
- Full Python suite before the final date/aggregation increment: **1,781 passed,
  7 skipped**. Those seven archive-integrity checks were then run with the
  available NumPy installation: **7 passed**. The entire suite was not rerun
  after the final reporting increment.
- Final paired/all-event regression checks: **39 passed**. Separate existing
  web, audited-adapter, chart and progress regressions: **44 passed**. These
  cover disjoint histories, full trade counts, terminal reconciliation, fees,
  source/retained-output changes, partial failures and bounded OHLC output.
- Repository Ruff checks and formatting passed; focused strict type checks,
  JavaScript syntax and Git whitespace checks passed.
- The original funded full-history BTC replay was rerun with the new executable:
  **complete output byte-identical** to its prior result, in 48.574 seconds.
  [Retained receipt](/opt/grid-runs/shared-reference-funded-regression-20261111/receipt.json).
  Matching-mode changes have not improved or changed that funded outcome.
- The idle `grid-backtest-ui` service was restarted with the corrected totals.
  The live own-history endpoint independently verifies all **46,704 trades per
  engine**, equal engine aggregates, and a final chart close equal to
  **1996.1577799215032 USDT**. Its failed HTX member remains explicit.
  [Live retained response](/opt/grid-runs/all-events-totals-20261111/hosted-live-totals.json).
- Live browser checks confirm all **4,439 OHLC bars in each of six combined
  traces**, actual OHLC/event hover details, grouped engine legends, unchanged
  individual trade markers, native quote separation, explicit partial failures
  and no horizontal overflow at a 390-pixel mobile width. No browser errors or
  additional simulation launches occurred.
  [Browser verification](/opt/grid-runs/ui-qa-20261112/all-events-live-qa.json).

Local verification does not close hosted CI, private-account, real execution or
the original observed-soak acceptance gaps. GitHub Free-only restrictions remain
in effect. No exchange calls or changes to `/opt/module1_n` or the datasets were
needed for these fixes.

## 8. Whole-exchange timeout correction — 2026-11-12

Nginx recorded a timed-out `POST /api/paired`, followed by timed-out progress
requests. Large preparation ran synchronously in the HTTP event loop and parsed
the complete saved venue precision snapshot again for each pair. The request
timed out while its batch continued. The latest accepted large user batch was
Binance; the Bybit roster was subsequently exercised independently below.

Preparation now runs in a worker thread. The catalog keeps at most one parsed
venue snapshot, invalidated by device/inode/size/mtime/ctime or removal. New pair
artifacts freeze only that market's unchanged precision entry. Both engines
receive identical frozen settings. Neither the dataset nor `/opt/module1_n`
was edited, and Nginx's 30-second limit was not increased.

Combined results use one background task, short HTTP 202 polling responses and
a disposable cached file. Every retained input and the cached output are checked
for identity changes; complete hashes and parity are verified before publication.
Verified identical records/summary values share one aggregate calculation, with
that reuse explicitly recorded. Insignificant fractional zeroes and JSON
whitespace are omitted without rounding, and delivery is compressed. No trade
events, chart extrema or significant numeric digits are discarded.

Actual local checks, with another user batch running concurrently:

| Check | Actual outcome |
|---|---|
| Complete archive selection/preparation | 43,157 files across 60 exchanges; 40,491 passed the current boundary/name/settings checks. Initial pass 13.394 s total, no individual exchange above 1.295 s; warm repeat 4.780 s. This is preflight, not a full replay of every file. |
| Full Bybit roster | 566 filenames: 2 rejected by the existing name heuristic, 551 matched full-history comparisons, 13 engine refusals retained. Both engines matched deterministic summaries and complete trade/series files for every successful pair. |
| Actual Bybit workload | 738,074,517 candles per engine for the 551 successes; 5,913,759 trades per engine. Backend batch 747.324 s (12 min 27.324 s) from retained start/end timestamps, including both engines, validation, wrappers and final parity; excludes initial preflight and client delivery. |
| Successful C++ kernel totals | Current 225.049 s; audited 230.690 s. These are summed local timings, not an isolated speedup claim. |
| Refused Bybit inputs | Current engine independently reproduced all 13 audited refusals: 11 zero-quantity results under declared order/tick assumptions and 2 price-grid limits. Neither settings nor source prices were changed to make them pass. |
| Cross-exchange replay check | First eligible filename on each of 60 exchanges, over its full history: 52 matches, 8 explicit OHLC/timestamp validation refusals; 39.595 s backend time. This does not replay every pair on every exchange. |
| Large combined chart | All 5,913,759 trades per engine included across 15 native quote groups. Optimized calculation 70.037 s in the standalone verification process; every original aggregate value was compared numerically and preserved. |
| Results payload | Original pretty JSON 100,989,897 bytes; compact derived JSON 53,651,895 bytes. Deployed gzip response 8,730,002 wire bytes for 53,654,169 JSON bytes including API metadata; cached public retrieval 2.202 s. |
| HTTP availability | Public progress samples at most 18.5 ms; one measured probe during large aggregation 21.3 ms. The verification harness's complete set of HTTP samples peaked at 0.983 s. These are observed local samples, not universal latency bounds. |
| Regression checks | 92 focused tests passed; the final two API-delivery checks passed again after compact serialization. Repository lint/format, focused strict types, JavaScript syntax and whitespace checks passed. |
| Deployed browser | All 15 native quote groups and 90 chart traces rendered; exact trade inclusion, the 564-pair Bybit selector and a 390-pixel mobile layout passed with no browser errors or simulation/control submissions. |

The two Bybit name-filter refusals are `JUP/USDC` and `JUP/USDT`, flagged by the
existing `UP` suffix heuristic. This is a filename policy, not evidence that
those instruments are leveraged. The eight cross-exchange source refusals
belong to the chosen files on BitMEX, Bitopro, Crypto.com, Delta, Foxbit, Hollaex,
LBank and WOO; they do not establish that every file on those venues is invalid.

The verified Bybit suite is available in the public panel as **bybit-full-history**,
ID `paired-20261111T214459Z-47c30c2c`. Its 15 exclusions/refusals remain explicit.
The active user suite retained its ID and original request through deployment;
1,363 completed matched pairs were revalidated and reused at the final restart.
An unfinished pair is replayed from its beginning. Startup still never resumes
automatically; **Resume remaining pairs** is an explicit operator action.

The first benchmark harness incorrectly compared decimal strings including
trailing zeroes. Its log is retained; verification was corrected to compare
Decimal values and reused the completed simulations. This was a reporting-harness
assertion, not a change to engine results or a favorable rerun of the strategy.

Evidence: [batch timings](/opt/grid-runs/whole-exchange-20261112/benchmarks.json),
[all-exchange preflight](/opt/grid-runs/whole-exchange-20261112/all-exchange-preflight.json),
[refusal parity](/opt/grid-runs/whole-exchange-20261112/refusal-parity.json),
[lossless aggregate comparison](/opt/grid-runs/whole-exchange-20261112/compact-totals-receipt.json),
[deployed Bybit verification](/opt/grid-runs/whole-exchange-20261112/public-bybit.json),
[complete Bybit browser check](/opt/grid-runs/whole-exchange-20261112/bybit-browser.json),
[preserved deployment](/opt/grid-runs/whole-exchange-20261112/final-deployment.json)
and [unchanged originals](/opt/grid-runs/whole-exchange-20261112/unchanged-originals.json).

No full replay of all 43,157 files, real execution or profitability acceptance
is claimed. Invalid source records and incompatible precision/order inputs
remain explicit limitations; they cannot be fixed by pretending they simulated
successfully. The strategy improvement sequence above remains unchanged.

## HTX Batch 2 review and USDT-only policy — 12 November 2026

### A. Outcome and current scope

The accounting backend now uses completed inventory cycles, checks the funded
fill ledger, reconciles historical USDT equity and calculates full-period,
monthly and combined-wallet metrics. An independent implementation checked
952 numerical and structural expectations against retained HTX fills and raw
archived prices: zero failures. Monetary reconciliation differences were exactly
zero. This supports proceeding to review of the metrics presentation; it does
not establish profitable or executable trading.

The owner's subsequent USDT-only instruction is implemented in the public
research panel, its API/runners, Python research configuration and trading
universe admission. Explicit allow lists and pins cannot admit another quote.
New matching and funded runs accept `/USDT` only; old non-USDT suites cannot
resume. Original data and saved results remain readable. Neither live trading
nor another public exchange request was enabled.

The hosted panel was restarted after confirming no active job. Its HTX dropdown
contains 610 USDT files, 607 eligible under the existing name/identifier filters.
The public API returns HTTP 400 for an explicit `htx:ETH/BTC` selection. “Select
all USDT pairs on this exchange” uses those same eligible markets for both
matching engines. Funding/order inputs now explicitly say USDT. The funded
portfolio's existing 64-source bound remains; matching research can queue the
larger exchange selection.

The cross-quote historical verification below was completed before that policy
change and is retained audit evidence only. No further non-USDT historical
simulation was launched afterward. Dashboard Step 5 remains unimplemented;
only the newly requested USDT selector and unit labels were changed.

The owner's follow-up specifically requires that the panel never submit a
non-USDT job to module1_n. Direct single-pair API requests are rejected before
source inspection or job preparation. Bulk/matched submissions and old-suite
resume are checked independently. The worker also checks both the declared
symbol and the archive filename's quote before invoking either executable.
Tests spy on worker/process launch and require zero calls for rejected requests,
including a misleading USDT symbol attached to a BTC-quoted filename.
The deployed direct endpoint also rejected ETH/BTC, ETH/USDC and ETH/USD with
HTTP 400. The audited saved-run count stayed 6,116 before and after all three
requests, with no active job. See the
[public launch-boundary check](/opt/grid-runs/usdt-only-20261112/audited-boundary-hosted.json).

### B. Changed files and purpose

These changes are relative to the captured start-of-batch workspace, which
already contained earlier uncommitted development. They do not describe the
entire Git diff against HEAD.

| Files | Purpose and necessity |
| --- | --- |
| `scripts/backtest_performance.py` | Correct completed-cycle statistics, native ledger checks, independent cash/inventory USDT marks, equity/FX reconciliation, minute drawdown, daily Sharpe, monthly boundaries and streamed portfolio sums. One authoritative reporting implementation. |
| `scripts/backtest_usdt.py` | Invalidate derived-report dependencies when base-asset valuation inputs change as well as quote conversion inputs. |
| `scripts/backtest_archive.py` | Reject non-USDT funded manifests before scanning source data. |
| `scripts/backtest_legacy_n.py` | Restrict new catalog selections and wrapper execution to USDT; retain complete catalog records for reading old results. Original simulator is untouched. |
| `scripts/backtest_web.py` | Restrict dropdown/API/new jobs and resume; select the separately built policy-enabled executable. |
| `scripts/backtest_web_static/index.html` | State the USDT selection and monetary units in the existing controls. |
| `cpp/backtest/trace.cpp` | Refuse non-USDT funded replay sources before dataset loading. |
| `cpp/backtest/comparison.cpp` | Require a declared BASE/USDT symbol for new matching-research commands. |
| `src/grid/config.py` | Reject non-USDT quote settings and affirmative allow/pin entries. |
| `src/grid/universe/selection.py` | Enforce USDT eligibility even for explicit/manual/pinned requests. |
| `src/grid/research/config.py` | Reject new non-USDT Python replay configurations. |
| `src/grid/research/demo.py` | Keep the runnable invented demo compatible with the new admission policy. |
| `src/grid/operations/synthetic.py`, `src/grid/operations/session.py` | Use USDT for the invented operational fixture and its description. |
| `src/grid/operations/contention.py`, `src/grid/operations/mixed_contention.py`, `src/grid/operations/experiment.py` | Keep zero-fee synthetic events in the fixture's new USDT currency. No financial algorithms changed. |
| `tests/research/test_backtest_performance.py` | Completed-cycle, loss/breakeven, partial-sale and actual closure-month regressions. |
| `tests/research/test_funded_accounting.py` | Hand-calculated native and USDT balances, basis, fees, missing marks and corruption refusals. |
| `tests/research/test_portfolio_performance.py` | Combined-equity, monthly, minute-drawdown, daily-Sharpe, cashflow and serialization checks. |
| `tests/research/test_backtest_usdt.py` | Preserve historical cross-quote reading; verify USDT-only HTTP results ignore irrelevant FX files. |
| `tests/research/test_backtest_archive.py`, `tests/research/test_backtest_legacy_n.py`, `tests/research/test_backtest_paired.py` | Selection filtering, explicit rejection, old-suite resume refusal, matched runs, bulk selection and retained failures. |
| `cpp/backtest/comparison_tests.py`, `cpp/backtest/study_tests.py` | USDT successful inputs and non-USDT rejection; preserve existing financial/rule edge cases. |
| `tests/test_config.py`, `tests/universe/test_selection.py` | Test quote settings and all selection modes, including pins and allow lists. |
| `tests/universe/conftest.py`, `tests/universe/test_controller.py`, `tests/universe/crash_child.py` | Use admitted USDT fixtures while retaining recovery and ownership assertions. |
| `tests/execution/helpers.py` | Fund and seed with the supplied context's quote instead of a hard-coded invented currency. |
| `tests/operations/test_supervisor.py`, `tests/operations/test_liquidation.py`, `tests/operations/test_storage_controls.py` | Align synthetic actual-fee currencies with the USDT operational fixture. |
| `tests/operations/test_scoped_dust.py` | Preserve the old QUOTE serialization oracle explicitly despite the new default fixture. |
| `tests/operations/test_mixed_contention.py` | Independently reconcile the new USDT fixture's exact cash expectation. Scope-dependent hashed cancellation order changes which invented late fill occurs; this is not a strategy return. |
| `tests/research/conftest.py`, `tests/research/test_comparison.py`, `tests/research/test_history.py`, `tests/research/test_lot_reference.py`, `tests/research/test_performance.py`, `tests/research/test_portfolio_replay.py`, `tests/research/test_portfolio_risk.py` | Keep existing synthetic replay/recovery tests compatible with USDT admission. |
| `tests/research/test_replay.py` | USDT fixtures plus explicit BTC/USDC/USD configuration refusal before replay. |
| `AGENTS.md`, `README.md`, `docs/BACKTEST_UI.md`, this document | Persist the scope, usage, evidence and remaining review boundary. |

Generated evidence stays outside Git in
`/opt/grid-runs/htx-batch2-20261112/` and
`/opt/grid-runs/usdt-only-20261112/`. The new binary is separately built at
`/opt/grid-builds/module1/usdt-only-20261112/grid_backtest`; the Batch 1 binary
and original strategy configuration bytes remain intact.

### C. Implemented accounting definitions

- A completed cycle starts with acquisition from zero owned inventory and ends
  when owned inventory returns to zero. Partial SELLs accumulate in the same
  cycle. An open residual is excluded from completed-cycle PF and Win Rate.
- Realized PnL removes the authoritative native acquisition cost, including its
  configured cost-quantum rounding. Historical USDT basis follows the actual
  proportion of native cost removed. BUY fees enter acquisition cost; SELL fees
  reduce proceeds. Fees are reported once and never subtracted a second time.
- Unrealized PnL is current marked base value minus remaining historical USDT
  cost. Equity is actual quote cash plus independently marked base inventory.
  Reserved cash is still owned cash, not an additional deduction.
- `pnl_usdt` is economic equity change after classified external flows.
  `trading_pnl_usdt` is historical-cost realized plus unrealized trading PnL.
  For old cross-quote records, quote-cash FX explains the bridge:
  `equity change = trading PnL + quote-cash FX`. New USDT quote cash has no such
  conversion component. The runner creates no external cashflow; the metric
  helper has a deterministic deposit-neutrality check.
- Historical marks use same-venue direct, inverse or bounded two-leg archived
  routes. At boundary T, only the candle opened at T−1 minute is eligible.
  USDT itself is one; USD/USDC are not assumed equal to USDT for old records.
  Missing required marks, costs or opening valuations remain null with reasons.
  There is no future-candle substitution, interpolation or stale FX carry.
- PF is total positive completed-cycle net USDT PnL divided by absolute losses.
  One losing cycle gives zero. No losses gives a null `no_losses` state, not
  infinity; no cycles gives `no_completed_cycles`. Win Rate is wins/all completed
  cycles, including breakevens in the denominator. Counts are separate.
- Returns use actual starting funded equity. Monthly returns use that month's
  opening equity, with clipped UTC calendar boundaries. Monthly realized PnL
  and fees are period deltas; ending unrealized PnL and its change are distinct.
  Cycle completion belongs to the actual fill month, including a fill precisely
  at the final boundary; the separate completion-month list retains that case.
- Sharpe uses complete UTC daily simple equity returns, sample standard
  deviation, zero risk-free rate and sqrt(365). Missing/nonpositive endpoints,
  too few intervals or zero variance produce explicit unavailable states.
  Maximum drawdown uses every completed-minute equity observation and reports
  a negative percentage, peak/trough values/times and completeness counts.
- Portfolio metrics sum each independent funded wallet once at matching time
  boundaries, then calculate returns, Sharpe and drawdown on that combined
  equity. PF pools completed cycles. Pair-level ratios/drawdowns are not averaged
  or summed. Minute streams avoid retaining the entire history in memory.

The report schema is `grid-performance-v2`; funded minute inspection is
`funded-historical-usdt-v2`. Old `completed_positions` and `wallet_pnl_usdt`
aliases remain for readers. Native balances are kept for audit. Arithmetic uses
96-digit Decimal context, but this cannot restore digits absent from binary32
archived prices. There is no change to trading rules or funded execution.

### D. Numerical verification

The independent verifier imports no production accounting/metrics code. It
reconstructs native fills, cost rounding, paid fees and marks from raw HTX bars
at 110 Decimal digits. Across 952 checks, money differences were exactly zero;
34 return/Sharpe/drawdown ratios differed only at rounding precision. Maximum
absolute difference was `5.69981021162966e-94`, within the declared
`1e-88 × max(abs(expected), 1)` tolerance. Expected, actual, absolute and relative
differences are retained in
[the independent verification](/opt/grid-runs/htx-batch2-20261112/independent-verification-final.json).

| Completed net PnLs | Expected PF | Actual PF/state | Expected/actual Win Rate |
| --- | --- | --- | --- |
| −10 | 0 | 0 / zero | 0% / 0% |
| +10 | No losses | null / no_losses | 100% / 100% |
| 0 | No losses | null / no_losses | 0% / 0% |
| +10, −5 | 2 | 2 | 50% / 50% |
| −5, −5 | 0 | 0 | 0% / 0% |
| +10, −5, 0 | 2 | 2 | 33.333…% / 33.333…% |
| No completed cycle | Unavailable | null / no_completed_cycles | null / null |

The following are **invented arithmetic fixtures**, not historical returns.
Each starts with 1,000 native quote units and reserve 100, buys 2 at 2 then 1
at 3, and sells 1 at 4 with 0.2% quote fees. Native ending cash is 996.978,
quantity 2, cost 4.676, realized PnL 1.654 and fees 0.022.

| Pair | Ending USDT cash, expected = actual | Inventory value, expected = actual | Realized / unrealized USDT, expected = actual | Fees USDT | Ending equity, expected = actual | Monetary difference |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BTC/USDT | 996.978 | 10 | 1.654 / 5.324 | 0.022 | 1,006.978 | 0 |
| ETH/BTC, retained accounting test | 139,576.92 | 1,200 | 251.76 / 665.6 | 2.64 | 140,776.92 | 0 |
| ETH/USDC, retained accounting test | 996.978 | 10 | 0.69775 / 5.4075 | 0.01975 | 1,006.978 | 0 |

The corresponding trading-PnL / quote-cash-FX / equity-change bridges are
`6.978 + 0 = 6.978`, `917.36 + 39,859.56 = 40,776.92`, and
`6.10525 + 0.87275 = 6.978`. Each reconciles exactly. A separate 3.01-cost/3-unit
fixture verifies that selling one unit removes native cost 1.00, leaving 2.01;
the reporting layer does not substitute a differently rounded basis.

The declared historical window was **30 January 2025 00:00 through 3 February
2025 00:00 UTC, end exclusive**. It was selected for a calendar boundary, not
for favorable PnL. Every pair used 5,760 candles and 5,761 minute observations,
the original Batch 1 funding/rules, 0.2% quote fees, one-bar latency and strict
ambiguous-candle rejection. No order fills on its creation candle.

| Historical wallet | Starting USDT equity | Ending USDT equity | Expected = actual USDT PnL | Return % | Sharpe | Max drawdown % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BTC/USDT | 1,000 | 999.6514730275 | −0.3485269725 | −0.03485269725 | −18.66192522 | −0.05276258 |
| ETH/BTC, before USDT-only policy | 103,723,140.625 | 97,698,367.00926116 | −6,024,773.61573884 | −5.80851445 | −16.59835568 | −9.44609066 |
| ETH/USDC, before USDT-only policy | 1,000.10001659 | 984.78663924 | −15.31337736 | −1.53118459 | −14.23178979 | −2.24037849 |
| Sum of those independent wallets | 103,725,140.72501659 | 97,700,351.44737342 | −6,024,789.27764317 | −5.80841755 | −16.59836454 | −9.44593252 |

Values above are display-rounded; exact money comparisons have zero difference.
The huge ETH/BTC baseline came from the old **1,000 BTC** native funding setting,
not 1,000 USDT. Its equity loss contains −6,024,773.349291205 USDT of quote-cash
FX and −0.26644763515 USDT of trading PnL. This historical evidence is not an
enabled future strategy. The current USDT-only policy removes this quote-funding
ambiguity without rewriting the old records.

BTC/USDT had 13 BUY and 2 SELL fills, two completed winning cycles and one open
cycle. ETH/BTC had 5/0 fills and no completed cycle; ETH/USDC had 32/8 fills,
eight winning completed cycles and one open cycle. The aggregate had ten
completed wins and three open cycles. Thus a 100% completed-cycle Win Rate and
`no_losses` PF can coexist with negative total equity return; open losses count
in equity. Four-day annualized Sharpe is an arithmetic check, not a reliable
estimate of future performance.

| Historical wallet | January partial-period PnL USDT, expected = actual | January return % | February partial-period PnL USDT, expected = actual | February return % |
| --- | ---: | ---: | ---: | ---: |
| BTC/USDT | −0.048656314375 | −0.0048656314375 | −0.299870658125 | −0.02998852494 |
| ETH/BTC | −1,285,819.56588784 | −1.23966509 | −4,738,954.049851 | −4.62619873 |
| ETH/USDC | −0.28207683154 | −0.02820486 | −15.03130052550 | −1.50340376 |
| Combined wallets | −1,285,819.89662099 | −1.23964151 | −4,738,969.38102218 | −4.62612339 |

February opens at January's ending combined equity, 102,439,320.828395607754…
USDT; it does not reuse the original starting capital. Monetary differences
against independent expectations are zero. These are three independent wallets,
not one cross-quote portfolio with coordinated global exit.

The missing-opening-FX case, **4 August 2022 12:06–12:36 UTC**, retained 30
ETH/BTC candles and one fill. Of 31 valuation boundaries, the opening mark was
missing. Ending NAV 22,874,140.61721535 USDT is known, while original NAV,
whole-period return/PnL and complete drawdown remain unavailable. Future data
was not used to manufacture the opening valuation.

Additional deterministic checks prove a 100→120→60→130 minute path has −50%
drawdown despite its positive terminal return; a 100 deposit on 1,000→1,100
equity has zero cashflow-adjusted return; and a three-wallet combined curve has
−62.5% drawdown computed from summed equity, not individual drawdowns.

The retained audited HTX BTC/USDT **2023** benchmark has 683 BUY/280 SELL fills,
34.08961000213852 USDT total PnL, 34.36664923196748 realized PnL,
−0.277039229828964 unrealized PnL and 23.18760851445048 fees. Its receipt hashes
were rechecked. It uses different dates, fixed 10-USDT sizing, unfunded immediate
O→H→L→C fills and synthetic precision. Comparing its PnL with the four-day
funded case cannot establish which strategy is better. No new historical
module1_n benchmark run was needed.

After the USDT-only change, the new C++ executable replayed the same four-day
HTX BTC/USDT case in approximately 0.224 seconds. Its complete fill file is
byte-identical to the retained pre-policy result:
`c48ec40526d9c5ce82e365391a7cd2c8b4ffe2a8e328086f68457983875faf78`.
This verifies admission changed without changing those funded trades.

### E. Data and execution limits

The retained HTX audit covers 625 files, 763,107,522 candles and 449 missing
pair-minutes. The new USDT-only subset is 610 files, 737,722,967 candles and
435 missing pair-minutes over 737,723,402 file-span minutes: weighted timestamp
coverage **99.9999410348%**. This denominator sums each file's own span; it is
not a claim of a common history or of every exchange trade being observed.

The four-day verification has complete traded-candle and required valuation
coverage. The opening-FX test deliberately does not. Original gaps elsewhere
remain, including isolated August 2026 conversion gaps. No missing candle was
filled and no further archive data was fetched. USDT-only quote cash needs no
FX feed; open base holdings still require a valid historical price.

OHLC archives use binary32 prices and lack trade volume, depth, spread, queue
position and latency observations. Timestamp completeness cannot establish
fresh trading or executable LIMIT fills. Current saved CCXT rules applied to
older history remain declared assumptions, not historical market-rule proof.
Synthetic API tests use invented files, including invented Gate-named fixtures;
no original Gate archive was processed in this batch.

### F. Validation and reproducibility

Use Python `/opt/grid-tools/usdt-validation-20261112/bin/python`, with
`PYTHONDONTWRITEBYTECODE=1`. Local socket/IPC tests require the permitted local
execution environment. `TMPDIR=/var/tmp` avoids sandbox placeholder `.git`
directories under `/tmp`; the application output protections were not relaxed.

Commands and retained evidence:

```sh
python -m pytest tests/test_config.py tests/universe \
  tests/research/test_replay.py tests/research/test_study.py \
  tests/research/test_lot_study.py tests/research/test_portfolio_replay.py \
  tests/research/test_comparison.py tests/research/test_portfolio_risk.py \
  tests/research/test_history.py tests/research/test_performance.py \
  tests/research/test_lot_reference.py -q -p no:cacheprovider -x
# 287 passed before the three additional configuration-refusal cases.

python -m pytest tests/research/test_replay.py \
  -k new_replay_configuration_rejects_non_usdt -q -p no:cacheprovider
# 3 passed, 16 deselected.

python -m pytest tests/research/test_backtest_archive.py \
  tests/research/test_backtest_legacy_n.py tests/research/test_backtest_paired.py \
  tests/research/test_backtest_web.py tests/research/test_backtest_performance.py \
  tests/research/test_funded_accounting.py tests/research/test_portfolio_performance.py \
  tests/research/test_backtest_usdt.py -q -p no:cacheprovider
# 148 passed.

python -m pytest tests/operations -q -p no:cacheprovider -x
# Time-capped attempt completed 145 assertions/tests successfully;
# the remaining 61 tests passed in a separate bounded invocation below.
python -m pytest \
  tests/operations/test_mixed_contention.py::test_cli_publishes_failed_timing_honestly_and_refuses_overwrite \
  tests/operations/test_mixed_contention.py::test_mixed_flag_is_only_for_contention \
  tests/operations/test_mixed_contention.py::test_second_case_failure_never_publishes_partial_report \
  tests/operations/test_scoped_dust.py tests/operations/test_storage_cli.py \
  tests/operations/test_storage_controls.py tests/operations/test_supervisor.py \
  -q -p no:cacheprovider -x
# 61 passed. All 206 collected operations cases are covered across the batches.

/opt/grid-tools/module1/lib/python3.12/site-packages/cmake/data/bin/ctest \
  --test-dir /opt/grid-builds/module1/usdt-only-20261112 --output-on-failure
# All 9 C++/parity test groups passed.
```

The selected Python scope covers 644 distinct tests across those executions;
an uninterrupted full-repository test pass is not claimed. Early attempts found
obsolete quote fixtures and a wrapper dictionary-key error; these were corrected.
Combined operational runs exceeded their process time caps because of bounded
resource/throughput experiments; completed work was retained and only the
remaining cases were finished. Initial failed/capped logs remain available.

The subsequent explicit module1_n launch-boundary check passed all 82 selected
wrapper/panel tests, including ten added non-USDT rejection cases. Together
with the earlier scope this covers 654 distinct tests. Its log is
[audited-boundary-tests.log](/opt/grid-runs/usdt-only-20261112/audited-boundary-tests.log).

Ruff checks/formatting cover changed Python files. Mypy covers the 40 changed
application/script/test Python files with explicit source/test search paths;
C++ Python harnesses are exercised by CTest. Core evidence:

- [287-test pass](/opt/grid-runs/usdt-only-20261112/admission-research-pass.log),
  [3 refusal tests](/opt/grid-runs/usdt-only-20261112/python-config-rejection-pass.log),
  [148 panel/accounting tests](/opt/grid-runs/usdt-only-20261112/python-panel-final.log).
- [145 completed operations cases before cap](/opt/grid-runs/usdt-only-20261112/operations-pass.log),
  [remaining 61](/opt/grid-runs/usdt-only-20261112/operations-remaining-pass.log),
  [C++ groups](/opt/grid-runs/usdt-only-20261112/ctest-all-pass.log).
- [Independent arithmetic verifier](/opt/grid-runs/htx-batch2-20261112/independent_verify.py),
  [952-check results](/opt/grid-runs/htx-batch2-20261112/independent-verification-final.json),
  [declared historical windows](/opt/grid-runs/htx-batch2-20261112/replay-declaration.json),
  [historical run paths/timings](/opt/grid-runs/htx-batch2-20261112/historical-runs.json).
- [Public HTTP smoke check](/opt/grid-runs/usdt-only-20261112/hosted-smoke.json),
  [unchanged USDT fill trace](/opt/grid-runs/usdt-only-20261112/htx-btc-usdt/receipt.json).

The original independent-report snapshots precede the final additive
`accounting_unavailable_reasons` metadata. The tested monetary definitions and
values are the same; the final metadata is covered by the accounting/API tests.
There was no new all-history profitability search, authenticated request,
paid GitHub operation or live deployment.

### G. Protection checks

[Before](/opt/grid-runs/htx-batch2-20261112/protection-before.json) and
[after all final tests](/opt/grid-runs/htx-batch2-20261112/protection-final.json)
record the exact hashes/identities:

- All 625 original HTX source identities and the filename roster are unchanged.
  Prior validated source hashes were reused for unchanged files; there was no
  wasteful whole-archive rehash. Replayed files were independently hash-checked.
- All 18,061 recorded non-symlink entries under `/opt/module1_n` are unchanged,
  covering retained source, binary and output identities; 11 selected legacy
  file hashes are unchanged. Four pre-existing venv symlinks were outside the
  original baseline roster and were excluded from the like-for-like comparison.
- All 23 protected configuration/rule/snapshot hashes are unchanged, including
  `config.example.yaml`, Batch 1 HTX artifacts, `scripts/backtest_minimum.py`,
  `cpp/backtest/engine.cpp` and `cpp/backtest/simulator.cpp`.
- The retained audited benchmark's eight receipt hashes verify. The original
  Batch 1 binary hash remains
  `f35e5f7f00dca5432edf586f37b98238eb1ca8da7e6911d004c87d6ea59f95a3`.
  The new policy build is separate. Synthetic tests may execute copies of the
  audited binary outside its protected project; its original files are untouched.

### H. Remaining limitations

1. Full order-event history is absent from the funded fill sidecar. Terminal
   reservation amounts were compared with authoritative C++ state; they were
   not independently reconstructed from orders. Separate tests verify that
   reservation does not reduce owned equity. Arbitrary fee-token/base-fee replay
   is unsupported here; unsupported/missing old sidecars fail explicitly.
2. Raw C++ `stdout.json` still contains older sell-posting diagnostic metrics.
   Its `profit_factor` is not the new completed-cycle PF. The authoritative
   audited metrics are `performance.json`/`grid-performance-v2`; Step 5 must
   distinguish these instead of presenting the older diagnostic as equivalent.
3. Accurate arithmetic does not close the OHLC, historical-rules, freshness or
   execution-evidence gaps above. No result here certifies profitability.
4. The same-window historical comparison against module1_n remains a separate
   experiment. Existing exact matching research verifies compatibility but
   deliberately shares the immutable simulator's unfunded execution assumptions.
5. Large historical overview charts remain sampled or use their documented
   trade/close aggregation. They are not substitutes for authoritative minute
   drawdown or complete fill records. No new full-archive portfolio performance
   benchmark or visual browser acceptance of a metrics redesign is claimed.

### I. Proposed next reviewable stage — not implemented

Step 5 would make the existing panel consistently consume the verified v2
metrics; label actual funded results versus capital-normalized reference
research; show full-period/monthly PnL, completed-cycle PF/Win Rate, Sharpe and
minute drawdown with coverage/unavailable reasons; and ensure downloaded JSON
agrees with the display. It would keep USDT-only selection and the unchanged
module1_n benchmark. This metrics-presentation work still requires the separate
review/approval requested in the Batch 2 brief. No strategy optimization or
additional data collection is proposed by this accounting review.
