# Module 1 offline research specification v1

Status: implementation contract, before comparative returns. This is not a
profitability result or a live-strategy amendment. Starting revision:
`cd48dd01734d2b84ad13651e907e72fc437a0cbc`.

Authority: the owner's attached Module 1 C++ backtest master prompt at
`/root/.codex/attachments/e2fdb6ac-51d7-41bb-829f-506d5bc68220/pasted-text-1.txt`.
It authorizes implementation and actual offline historical replay, including
explicit assumption-based studies where archive provenance cannot be certified.
It supersedes older instructions to defer C++ research, not live invariants.
No credentials, exchange requests, orders, services or Module 2 work are included.
Every file under `/opt/datasets/` is read-only and must never be modified or removed.

## Preserved behavior and reuse

The Python domain reducer, execution, exchange, persistence, portfolio and
operations packages remain unchanged. Reuse the Python financial functions and
reducer as the baseline oracle; never use legacy `module.cpp` as that oracle.
Strict existing replay admission remains strict. Unknown provenance is admitted
only by the new explicitly labeled exploratory research contract.

| Requirement | Existing implementation | Research work required |
|---|---|---|
| Geometric minimum buys, fees, one aggregate SELL | `domain/market.py`, `domain/reducer.py` | Exact C++ counterpart, differential traces |
| Reservations, partials, cancel races and drain | Domain/execution/persistence tests | Deterministic simulated lifecycle and parity |
| Candle interpretation and chronology | `research/replay.py`, `research/history.py` | Streaming full histories, explicit gap/provenance accounting |
| Global +10% and bounded liquidation | `portfolio/`, `research/portfolio_replay.py` | Research integration and exact TP precedence |
| Seed/lower-BUY variants | Existing seed plus anchored ladder only | Four isolated research policies |
| Symbol/exchange TP switches | Manual scoped exits, not automatic tier TP | Research-only independent triggers |
| Cash/buy-hold, metrics, study provenance | Existing research modules | Reuse definitions; complete required study reports |
| C++ replay | Absent in this checkout | One C++20 executable, CMake/CTest, no service |
| Historical fee/rule/identity provenance | Incomplete | Explicit assumptions and strict NOT COMPUTABLE status |

## Four primary policies

All use one basket SELL, the same funding, costs, dates, market roster, spacing,
limits and TP switches. The control is `seed_min_limit + ladder_multi`.

* `seed_min_limit`: place a minimum passive LIMIT BUY using the last available
  completed-bar close as the declared bid reference: `floor_tick(close)`.
  The fixed synthetic spread changes the ask proxy only. It cannot fill in its
  creation bar.
* `no_seed_lower`: first place a minimum LIMIT BUY one geometric step below
  that initial reference. This is a real pending simulated order, not inventory.
  As in the baseline seed lifecycle, lower-ladder expansion starts only after
  this first order is reconciled terminal; its actual modeled fills determine
  the anchor. This explicitly chooses one first-entry order, not an initial
  inventory purchase or a batch of fictitious fills.
  This changes the first funded entry only. Subsequent basket rounds and
  completed portfolio-cycle restarts use the same declared reentry policy in
  all four variants; cumulative accounting and round/order identities continue.
* `ladder_multi`: reserve and pre-place at most N distinct levels below the
  executed anchor. Replenish the next unused anchored level only after fills,
  within funds, range and lot limits. Existing pending orders may fill together
  under the declared candle convention; new orders wait for a future bar.
* `ladder_one`: at most one pending lower BUY. After reconciliation its next
  price is the tick-rounded-down minimum of the next unused geometric anchor
  level and `fresh_reference / (1 + g)`. The fresh reference is available only
  after the current bar has completed. No retroactive multi-level fills.

The active basket does not trail upward. A partial SELL does not start a round.
Full SELL enters drain; existing BUY cancellation and late fills must reconcile
before reentry. Late inventory becomes the next seed without another purchase.
The control reentry uses a fresh completed-bar proxy. The optional one-step
reentry uses `min(last_executed_sell_price/(1+g), fresh_passive_buy_reference)`,
rounded down; it is a separate comparison after the required matrix. Split SELL
is optional and deferred until that matrix is complete. Independent-lot strategy
is not Module 1's baseline or a substitute for the requested variants.

## Temporal and portfolio contract

Process complete equal-timestamp market groups before portfolio decisions; use
lexicographic venue/symbol order for deterministic ties. Only orders already
eligible before a bar can fill in it. Preserve cancellation latency and possible
late fills. Do not infer private events, liquidity or intrabar ordering from OHLC.

Retain `strict_skip_ambiguous`, `touch_buy_priority`, `touch_sell_priority`.
The strict convention skips conflicting BUY/SELL eligibility and requires price
through an eligible limit where representable. The alternatives are declared
one-sided ordering assumptions, not guaranteed best/worst PnL bounds.

Missing/invalid bars produce no fills or manufactured quotes. Track every gap
and missing minute. No fresh portfolio NAV or TP decision when any required
owned position lacks a permissible current mark. Report unknown marks and
incomplete drawdown coverage; never silently substitute zero or forward-fill.
At the first observed boundary following a gap, advance the clock before
cancellation processing so the old book cannot authorize stale replacement
orders. After that bar completes, one explicit simulated-state reconciliation
may resume deferred planning, provided every modeled order/fill is known and
no fee or uncertainty halt is cleared. Orders produced by that recovery wait
for a future bar. This extends the research adapter's scheduling, not the live
strategy or the strict consecutive-bar Python reference. Count these gap
refreshes; do not invent missing-period executions or exchange evidence.

One common quote, initially USDT, with fixed separately funded venue accounts
and non-overlapping allocation budgets. No transfers or external cash flows.
Cash reserved for BUYs is included once in NAV and excluded from spendable cash.
Raw NAV marks all inventory at the exact decoded completed close. Estimated
liquidation NAV uses `floor_tick(close)`, the valid sellable quantity and the
existing Python fee forecast (including the configured settlement bound).
Untradeable residuals remain owned and reported: their raw marked value remains
visible, but they contribute zero estimated liquidation proceeds, as in the
existing portfolio valuation. Nonnegative marked quote values are floored to
the declared quote quantum; cash and posted fees remain exact. Unknown orders
or unreconciled ownership also prevent TP authorization. The primary unlimited
synthetic depth assumption is not evidence of actual executable liquidity.
Symbol and exchange TP are independently optional; global +10% remains enabled.
On simultaneous triggers, global > exchange > symbol. A higher tier owns every
affected allocation, so lower tiers cannot place duplicate exits. Freeze buys,
cancel/reconcile existing orders, place future-bar LIMIT exits and retain blocked
exits/dust. Reset only completed scopes, on a later observation, preserving
original-funding performance and all remaining ownership.

Exit pricing follows the existing Python proposal: ceil to tick the maximum of
the market minimum, trigger bid times `(1-deviation)` and current completed
close; reject a price above trigger bid times `(1+deviation)`. The frozen 5%
deviation is a hypothetical guard, not a guaranteed execution outcome. Allow
one initial order plus three reprices, each with a 60-minute TTL, and a total
240-minute deadline measured from the trigger including cancellation. At the
deadline cancel/reconcile working orders and retain unresolved exposure as
blocked. The scenario's minimum absolute after-fee quote is zero; price guards
still apply. No new cycle restores initial funds or repeats the initial-entry
policy.

## Selection and experiment declaration

Before examining any strategy return, inventory filenames and structural
coverage, prefer target venues, USDT, ten distinct bases passing leveraged-name
exclusions (historical spot status remains unverified) and at
least 365 days of common overlap. Deterministic selection uses target-venue
round-robin, descending endpoint span/row count and filename ties, with >=99%
endpoint density, full structural validity and >=365 shared complete UTC days.
This v2 quality gate replaced a first sparse roster before any returns; retain
that rejected roster. Shared complete days need not be consecutive: separately
report the longest uninterrupted common interval. Preserve all candidates/rejections and the
exact algorithm in the frozen selection manifest. Any non-target fallback is
explicit and follows the same rule; never replace a losing market after returns.

Read every record of every selected full source. Record SHA-256, byte length,
timestamps, invalid records, gaps, overlap and stable source identity. Filenames
do not certify spot identity or collection provenance. The authorized exploratory
study is labeled UNVERIFIED SOURCE / ASSUMPTION-BASED; strict historical venue
performance stays NOT COMPUTABLE while official historical rules are absent.
The exploratory runner counts float32 OHLC observations incompatible with the
assumed tick but does not reject them merely for unknown historical tick rules.
Submitted order prices/amounts must still be quantized and valid. The existing
strict Python admission and the baseline bar-parity adapter retain their source
precision checks. This explicit research distinction is recorded in the config.

The primary matrix is four policies times three candle conventions. Its explicit
synthetic scenario starts with 1,000 USDT per allocation, 1% grid spacing, three
pending BUYs for multi versus one for single, 100 accumulated lots, 100 USDT
quote reserve, 900 USDT maximum committed acquisition cost, and 0.1% quote fees
on each side. These are research assumptions, not venue facts, optimized settings
or live recommendations. Symbol/exchange TP are off in the primary matrix;
global +10% is on. Separate fixtures exercise optional tiers and precedence.
Price/amount quanta, minima, fee settlement quanta, spread, latency, exit guards
and fixed fee-sensitivity values are frozen in
`research/runs/module1-20261110/experiment_config.json` before comparative returns.
They cannot be fitted to those returns. The actual shared 695-day calendar span
has 478 shared complete UTC days but only 66 consecutive complete days; this is
not a continuous gap-free year and does not justify joining separated intervals.

Use matching funded cash and equal-weight buy-and-hold comparators. The latter
reuses `research.replay.buy_hold`: at each market's first completed valid bar,
counterfactually buy at `ceil_tick(close*(1+spread))` with the maximum quantity
funded after the same quote reserve and a single actual modeled entry fee.
Retain cash if constraints fail. This is an explicitly counterfactual benchmark,
not a claimed LIMIT fill; it has no queue/latency or guaranteed execution.
Hold its inventory/cash without TP or rebalance to the same final timestamp,
using both raw and liquidation-adjusted valuation. All capital is funded from
the common start even if a market's first observation is later.

The earlier frozen configs are retained as `experiment_config_v1.json`,
`experiment_config_v2.json` and `experiment_config_v3.json`. Contract revision 4
in `experiment_config.json`
records its predecessor hash and the
pre-comparative-return clarification time. It makes these definitions explicit
without changing the selected cohort or primary numeric parameters. The earlier
4,096-bar Python timing run retains its original v1 hash; it was not a portfolio
or comparative return study.

Report both
raw marked NAV and estimated liquidation-adjusted NAV, realized/unrealized PnL,
drawdown completeness, fees, turnover, inventory/cash/reservations, fill/order
counts, round/cycle outcomes and all incomplete exits. Full individual histories
and the longest genuinely overlapping portfolio interval are separate studies.
Ten markets cannot satisfy the later dozens-of-symbols objective.

## Implementation and acceptance sequence

1. Restore local tooling and inventory existing interfaces/data without edits to
   live code or archives. Record the engine reuse decision.
2. Implement small C++ numeric/source primitives and compare exact observations
   against independent Python calculations before financial replay.
3. Reuse the Python reducer for golden lifecycle traces; add isolated research
   extensions, implement C++ and test all specified policies/accounting cases.
4. Pass differential, corruption, invariants, deterministic debug/release,
   sanitizer and existing Python gates before full historical execution.
5. Run full selected histories and the 12-case common-period matrix, retaining
   every failed/negative case. Profile and optimize only a measured hotspot.
6. Publish compact manifests/reports in Git and large artifacts outside Git,
   including exact commands, hashes, timings, remaining gaps and reproducibility.

This contract is not a claim that any of those stages has passed. Changes to the
research contract before execution must be versioned and explained; changes after
returns require a new declared experiment, with earlier results retained.
