# I8 bounded continuous portfolio replay

Base: `41138d5b36d7004da16c5d804305df58426dadd2`, branch
`i8/public-paper-hour`. This continues the owner's approved offline development
after the completed public hour. It extends PRD §4's explicit post-exit restart
and §9's historical evaluation. All execution is synthetic, using temporary mock
ledgers and the existing financial controller. No new public run, account access,
order authority, installed service or deployment is included. GitHub Free only
and the unchanged hosted-CI account lock remain in force.

## Interface and chronology

```sh
uv run --locked gridctl backtest --config /tmp/study/replay.json --portfolio /tmp/study/portfolio.json --portfolio-cycles 2 --output /tmp/study/two-cycles.json
```

`--portfolio-cycles` accepts an integer from 1 through 32 and requires
`--portfolio`. Omission or an explicit 1 retains the existing single-cycle
schema and behavior. Values greater than 1 select `grid-portfolio-series-v1`
inside the existing provenance envelope. The direct API accepts
`portfolio_replay.run(spec, settings, max_cycles=N)`; the scenario comparison API
accepts the same keyword. Booleans, non-integers and out-of-range values fail.

One ledger, writer, synthetic exchange, executor and portfolio controller live
for the whole replay. Initial pool credits and allocation funding occur once.
Every next cycle uses the prior reconciled holdings, including remaining fee
assets, unallocated funds, conversion proceeds and approved residual inventory.
There is no initial-capital reset, token refill, new deposit or inter-allocation
redistribution at restart. Existing order/client/fill identities and posted
realized PnL remain continuous. Allocation scope identity stays unchanged; the
global portfolio cycle gets a fresh ID and advances the persisted revision.

A completed cycle is recorded on its actual completion bar. Only a later source
bar can begin another cycle. The engine first updates observations and reconciles
all scopes, then invokes the controller's existing explicit `begin` operation
with the prior revision and the ordinary allocations selected for resume.
The controller requires the previous cycle to be `EXIT_COMPLETE`, fresh complete
NAV and no unresolved orders. Conversion allocations remain paused. Pricing
uses the new completed bar, and new placements retain the configured later-bar
fill eligibility. Unknown/blocked exits never become a restart signal.

Restart errors abort the run; they do not bypass the controller or refill funds.
Temporary ledgers close and are removed on normal return or a handled error;
there is no checkpoint-resume interface. A rerun starts from the immutable inputs.
Abrupt process termination may leave scratch artifacts for inspection.

Simulation stops when the requested number of cycles completes or history ends.
EOF does not force a liquidation or start a cycle on its predecessor's completion
bar. Every source is still decoded and checksum-validated after early completion;
later prices cannot change the completed outcome. A source mutation invalidates
the report even when the cycle limit was already reached.

## Series metrics

The series schema replaces the ambiguous single-cycle root fields `cycle`,
`final_return` and `exit_shortfall` with:

| Field | Meaning |
|---|---|
| `requested_cycles`, `completed_cycles` | Explicit upper limit and actual completed count |
| `cycle_outcomes` | Latest immutable snapshot for every started cycle, in order; completed predecessors and possibly one unfinished final cycle |
| `last_cycle` | Last started cycle, including its own baseline/trigger/final valuation and residual evidence |
| `last_cycle_final_return`, `last_cycle_exit_shortfall` | Controller metrics for that last cycle only; null if not completed |
| `initial_nav` | Reconciled baseline of the first cycle, including all owned pools |
| `observed_full_run_return_fraction` | Endpoint NAV divided by original baseline minus 1; null when endpoint NAV is unavailable |
| `completed_full_run_return_fraction` | Same fraction only when all requested cycles have completed; otherwise null |
| `stop_reason` | `cycle_limit_reached` or `history_exhausted` |

Cycle snapshots retain their own timestamps and boundaries. Retained interval
points include `cycle_id`; completion emits an interval even between regular
sampling boundaries. There is no guessed intrabar path. If history ends exactly
on a completed cycle before the limit, the marked return is available but
`completed_full_run_return_fraction` is null: the requested series is unfinished.

Observed portfolio drawdown continues across cycle boundaries. Mean committed
capital uses the original funding denominator throughout. Committed-fill
statistics, fee/turnover totals and allocation risk accumulate over the same
ledger. Allocation transfer adjustments keep their original baseline and all
earlier movement receipts. Cash/buy-hold references enter once, retain their
initial currencies and ownership, and end with the full series. They are never
rebased to a profitable later cycle. Unknown marks and gaps remain explicit.

For the invented two-cycle fixture, 1,000 becomes 1,050 and then 1,100. Both
cycles realize 50, but their returns differ: 5% of 1,000 and 50/1,050. Full-run
return is 10% of the original 1,000. Each +10% trigger still precedes a bounded
LIMIT exit that can settle below its trigger NAV; continuation does not promise
a 10% realized return per cycle.

`--compare-scenarios` can be combined with the option. Each fixed OHLC convention
runs a fresh independent series with the same initial funds and declared cycle
limit. Its summary's starting NAV and observed return refer to the entire series;
`phase`, `final_cycle_return_fraction` and `exit_shortfall` describe the last
started cycle. Complete member reports retain all cycle outcomes. Unknown NAV
or different ending horizons still withhold a shared NAV range. The requested
limit participates in the series and comparison configuration hashes.

## Bounds and remaining scope

The maximum requested count is 32, not permission to exceed another bound.
The initial risk-point preflight includes possible additional completion points:
`min(minutes, ceil(minutes / interval_bars) + max_cycles - 1)` intervals, multiplied
by both pre/post snapshots and allocation count, must fit the existing 20,000
point limit. Existing source, order/fill history, synthetic venue, temporary
database and 32 MiB result caps remain shared across cycles. The scenario suite
retains its combined 64 MiB cap. A bound failure refuses publication; histories
are not silently compacted or dropped. These are logical bounds, not OS quotas.

This is continuous replay under a declared fixed roster and static synthetic
FX/rule/fee assumptions. The walk-forward `--study` runner still uses independent
fresh-funded basket windows; portfolio-series and independent-lot study
integration remain incomplete. Point-in-time universe/delisting evidence,
historical fee/rule/FX changes, the full storage/resource matrix and actual venue
execution quality remain unresolved. No financial schema, dependency or strategy
policy changes are needed for this adapter. Revert is an ordinary scoped code
revert; preserve previous reports and their source revisions.

The observed 48-hour I8, private-account/first-venue evidence, I9 and deployment
gates remain incomplete and separately authorized. The completed one-hour public
run is unchanged and does not authorize another run. Hosted CI is pending, never
reported as passed; publication uses local validation and `[skip ci]`.

## Validation

Twenty-six new focused cases exercise continuous funding/IDs/accounting, fresh
next-bar seed pricing and grid levels, blocked/unknown exits, an unfinished new
cycle, EOF exactly at completion, conversion sinks and transfer history,
quantified dust and retained cost, strict cycle bounds, extra completion-point
bounds, shared venue capacity, before/after-commit restart failures, mutation of
source data after the last exit, scenario summaries, CLI provenance and default
compatibility under low ambient Decimal precision. Fixtures include the existing
serialized cancellation timing and benchmark reserve/spread assumptions.

The original committed single-cycle engine was loaded from Git and compared on
an unfinished holding, a completed exit and internal asset conversion. Every
result field and deterministic result hash matched the new default engine.
The receipt is `/tmp/grid-i8-series-compatibility.json`, outside Git. This is
finite compatibility evidence, not a guarantee for every possible history.
The financial controller/domain/executor modules and persisted schema are
unchanged by this increment.

Final local validation on 2026-11-04 UTC:

| Check | Observed result |
|---|---|
| Python 3.12.16 full suite | 1,035 passed in 476.82 seconds; no failures/skips |
| Python 3.13.18 full suite | 1,035 passed in 498.80 seconds; no failures/skips |
| Ruff lint/format and strict mypy | Passed; 237 formatted files, 179 typechecked source/test files |
| Bandit over application/scripts | Passed |
| Tracked/new-file secret scan | Passed; existing baseline unchanged |
| Dependency lock and advisory audit | Unchanged 66-package graph; no known vulnerabilities reported |
| Wheel/source distribution | 83 application modules and 241 required source/test/document/manifest files byte-verified |
| Original specifications and attachments | All four pairs retain their original SHA-256 fingerprints |

Both full-suite receipts verify unchanged application-source SHA-256
`c9a08bd41efdbd4ea2cbeadc153e1f6704ba72a344d700c11af4142e7bef5fdf`.
Logs and receipts are under `/tmp/grid-i8-series-final-checks/`, with stems
`3.12-1793833423405395642` and `3.13-1793833423402597544`. Tests used
`python -m pytest -q` through locked `uv` environments `/tmp/grid-i2-py312` and
`/tmp/grid-i2-py313`, selecting Python 3.12.16 and 3.13.18 respectively. The
existing approved mock SQLite gate remains required. Other checks follow
README's verification commands with those environment selections.

Package artifacts are under `/tmp/grid-i8-series-dist/`, outside Git. The wheel
contains no generated ledger/history/journal payloads. The scoped change is
published by ordinary commit/push on the existing branch with `[skip ci]`;
hosted CI remains pending under the unchanged account lock. No PR, paid GitHub
feature, exchange endpoint or deployment is used in this increment. Remaining
research and external acceptance work is tracked in `IMPLEMENTATION-REVIEW.md`.
