# I8 offline follow-up — independent-lot research reference

Base: `0c54773d7f08010dbf18cf2998addd738c07e01c`, branch
`i8/public-paper-hour`. PRD §9 explicitly requests a research comparison of the
aggregate basket against an independent-lot reference. This increment implements
one predetermined counterfactual convention on synthetic/validated OHLC inputs.
It does not change the specified production strategy or authorize exchange use.

## Declared convention

`fixed-anchor-independent-lots-v1` operates as follows:

1. Start one minimum-lot seed with the same configured bid/ask pricing proxy,
   precision, fees, minimum/sellability checks and delayed eligibility as the
   ordinary replay. An unfilled seed admits no lower entries. Establish the
   anchor only from the terminal seed's modeled execution price.
2. Keep that anchor fixed. Define level zero at the anchor and lower levels at
   `anchor / (1 + spacing)^level`, quantized by the same market functions. The
   finite grid contains levels `0 .. max_lots - 1`; reject collapsed/out-of-range
   admission, never enlarge a minimum lot or create unlimited deeper levels.
3. Each occupied level owns one minimum-size entry and its own take-profit SELL.
   The same existing reducer computes and accounts for that lot's SELL using
   its actual net quantity, cost, fees and target. Separate lot costs never
   become the basket's weighted aggregate target.
4. After fills, admit vacant shallow levels first, within the original shared
   `ladder_size`, `max_open_orders`, `max_lots`, quote reserve, committed-cost and
   allocated-cash limits. A sold level becomes eligible for another minimum lot
   at its original price. Existing deeper pending buys are not canceled merely
   to accelerate a shallower reentry; a full pending-buy allowance makes it wait.
   Stop admission at the first unfunded/ineligible shallow candidate rather than
   searching future prices or selecting an outcome-dependent alternative.
5. Preserve unsellable residuals and their cost. A residual keeps its level
   occupied; no duplicate entry may consume it as though it disappeared. There
   is no automatic reanchor, forced EOF exit or consolidated portfolio cycle.

This is one explicitly specified reference, not every possible independent-lot
grid. The basket's round reanchoring, aggregate cancel/replace, and descending
ladder differ from this fixed-grid convention. Differences in results cannot be
attributed to SELL aggregation alone or treated as evidence to change live policy.

## Shared accounting and execution assumptions

The reference uses bounded internal lot states, each with a derived scope and
one entry/SELL lifecycle. They are research objects only, never registered in
the financial ledger or passed to an executor. The parent owns the original
allocation's shared cash, token balances and risk limits. Funding moves to a lot
before its synthetic entry; unreserved quote is returned to the parent. BUY and
prospective SELL fee tokens are escrowed for that lot until it fully closes.
This explicit conservative token commitment may constrain admissions more than
the basket's aggregate fee reserve. It never borrows another allocation's funds.

Each lot uses the actual domain reducer and the same synthetic fill-posting
routine as ordinary replay, including fee currencies and exact cost basis.
Its local reducer cannot start further entries; only the parent can admit a
funded new lot. Production's one-aggregate-SELL invariant is unchanged. Research
may have several independent SELLs across the original allocation's lot states.

Candidate selection combines all pre-existing orders of the **original
allocation** before applying its configured OHLC convention. There is at most
one filled side per original allocation/bar, even across different lot states.
All three current OHLC models are supported. Deterministic price priority is
followed by level/local-order priority for ties. New entries and SELLs wait the
configured latency; a new opposite order cannot fill in the same candle.
Unexpected replacement/cancellation requirements refuse the reference rather
than inventing settlement. Full fills at limits remain hypotheses, with no
observed queue, depth, volume, slippage or maker-status evidence.

## CLI and result contract

```sh
uv run --locked gridctl backtest --config /tmp/study/replay.json --compare-lots --output /tmp/study/new-lot-comparison.json
```

The legacy `python -m grid.research replay` entry accepts the same flag.
`--compare-lots` runs the ordinary basket and the reference sequentially with
fresh state on the model already specified in the bundle. It is mutually
exclusive with `--compare-scenarios`; use separate explicitly configured runs
for other fill models. Combining it with `--portfolio` is refused: this
comparison has no independent-lot version of the I7 global exit. There is no
new live strategy selector or configuration that enables this research policy.

The existing CLI provenance envelope contains `grid-lot-comparison-v1` under
`result`, retaining complete `basket_single_sell` and `independent_lots` members.
The reference member has schema `grid-independent-lot-research-v1`. Both share
the original allocated capital, contexts, fee assumptions, latency, UTC range
and exact manifests. The comparator checks manifests and final file identities;
if either member fails or history changes, no partial comparison is published.

The common valuation is allocated mark-to-close NAV without estimated exit costs.
Unallocated pools are excluded in both members, as in ordinary replay. The
comparison retains the basket's funded cash/buy-hold benchmarks and reports
`reference_minus_basket_nav` at the same endpoint. It does not select a winner.
Both members retain their own config/result hashes; the comparison has a separate
hash and the outer envelope binds source/runtime/Git observations.

Reference reporting includes realized/unrealized PnL, gross net-realized
wins/losses, defined profit factor, turnover, fee burden, unfinished lot states,
free funds, anchor, ambiguous bars, NAV intervals, observed bar-close drawdown,
inventory time and committed-capital utilization. Completed lot histories are
folded into cumulative totals and hashes; this is not a durable production
journal or a resumable checkpoint. Input replay is required after interruption.

## Limits, validation and compatibility

At most 4,096 configured lot slots across the complete roster are accepted.
The reference and combined comparison each have a 64 MiB encoded result cap;
encoding happens before the size check and both members are retained in memory.
Existing history/interval bounds also apply. These are bounded-work safeguards,
not OS CPU/RSS/IO quotas. There is no archive-wide sweep, data-cache conversion,
parallel research pool or claim of safe trading/research contention.

Seventeen focused cases cover independent targets, shared finite funds and
BASE/QUOTE/TOKEN fee conservation at every bar; each OHLC ambiguity model and
delayed opposite orders; fixed-anchor reentry; unsellable/unfunded minimum lots;
scope ordering and low ambient Decimal precision; repeated lot lifetimes;
separate CLI invocation, output hashes, mode refusal and immutable inputs;
source changes between/after members and capacity refusal without publication.

One fixture realizes 17.36 from lower lots while retaining the original lot at
cost 100 and TP 110. Another realizes 10 but ends with an unrealized loss of 50,
so total marked change is -40. These are arithmetic fixtures, not trading results.
The unchanged ordinary basket engine was compared byte-for-byte against the
previous commit for nine fixture/model combinations after extracting the shared
fill routine; its full serialized results and hashes matched in those cases.
Ruff lint/format, strict mypy (166 source/test files), Bandit over application
and scripts, lock consistency, staged secret scanning with the unchanged
baseline, and the dependency advisory audit passed. The audit found no known
vulnerabilities, not proof of absence of undisclosed issues. The wheel's 77
application modules match the current tree byte for byte; generated financial
and history artifacts are excluded. Dependencies and imported specifications
remain unchanged.

The full suites passed against application source SHA-256
`8d27396b88edcc74f2bd98747c1a3bf016a914f9c400cc05b460d8dc6619823a`.
Python 3.12.16 passed **952 tests in 525.72 seconds**; Python 3.13.18 passed
**952 tests in 552.17 seconds**. Both receipts confirm unchanged application
source. After these suites, the conflicting-flags CLI test was strengthened to
use a fresh output path, avoiding refusal caused merely by an existing report;
that final test passed again on both interpreters (one test each). Lint, type
checks and formatting also passed after this test-only correction.
Durable full-suite logs and receipts are in
`/tmp/grid-i8-lot-final-checks/`, with stems `3.12-1793826894259513261` and
`3.13-1793826894253774020`. Package artifacts are in `/tmp/grid-i8-lot-dist/`;
the final source archive is verified against application/test Python files,
documentation and dependency manifests. These temporary artifacts and logs are
not committed evidence.

Reverting this increment removes the research flag/member schema without a
financial migration or dependency change. Preserve generated reports and use
consumers that recognize their schemas. Existing operator source-digest
compatibility rules continue to apply. No installed service, real/sandbox orders,
account access, public requests, dataset modification or paid GitHub feature is
included. Hosted CI remains pending under the unchanged lock; use `[skip ci]`.

The automatic cross-product study of strategy/fill/fee conventions, walk-forward
and regime splits, survivorship/delisting checks, reference portfolio exits,
remaining per-allocation portfolio metrics, constrained research/fill contention
and full storage benchmark matrix remain open. The 48-hour observed gate,
first-venue readiness, I9 and deployment remain separately gated.
