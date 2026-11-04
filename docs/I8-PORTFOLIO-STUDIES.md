# I8 portfolio walk-forward studies at fixed endpoints

Base: `9eecbca54fa54d08644dc0c5315260fa6a0e539c`, branch
`i8/public-paper-hour`. This continues approved offline development after the
completed public hour. It integrates the existing consolidated portfolio
controller and continuous cycles into PRD §9's chronological selection and
sensitivity studies. All orders/fills are synthetic, financial databases are
temporary, and inputs remain immutable. GitHub Free only and local validation
remain required; no exchange access or deployment is included.

## Command and study contract

```sh
uv run --locked gridctl backtest --config /tmp/study/replay.json --study /tmp/study/plan.json --portfolio /tmp/study/portfolio.json --portfolio-cycles 2 --output /tmp/study/portfolio-study.json
```

The existing strict `grid-study-plan-v1` and `grid-portfolio-replay-v1` inputs
are reused. The cycle limit defaults to 1 and accepts 1–32. The result inside
the provenance envelope is `grid-portfolio-walk-forward-study-v1`. Omitting
`--portfolio` retains the basket-only study and its original schema. The study
flag still excludes `--compare-lots` and `--compare-scenarios`; its own declared
scenario list fixes OHLC convention, latency and buy/sell fee rates independently.
Only spacing is ranked, never the assumed execution conditions.

The API is `study.run(spec, plan, settings, max_cycles=N)`. The optional settings
select portfolio mode. Every effective candidate/scenario/window configuration
and its portfolio bounds are checked before any history read or ledger creation.
All base and sliced manifests, exact UTC endpoints, funding declarations and
candidate outcomes remain in the report. Invalid history in an unused gap of
the base range still rejects the study. Changes during or after a member run,
failed members and output-cap failures refuse publication of a partial report.

Each candidate starts with the same declared funds in a fresh temporary ledger
for each window. Within a window, up to N portfolio cycles use the existing
reconciled restart path and continuous funds/accounting/identities. Training and
testing do not share holdings or orders. Later folds also start fresh. These
independent experiments cannot be compounded into a continuous trading equity
curve, and training/test boundaries do not represent actual fund transfers.

## A common endpoint for comparison

The ordinary standalone portfolio replay stops execution/evaluation when its
requested cycles complete. That endpoint can differ by candidate. Comparing
such returns would give candidates different time exposure and could conceal
subsequent retained-asset risk. The study always evaluates every candidate
through the declared window end.

`portfolio_replay.run(..., through_end=True)` provides this fixed-horizon mode.
It uses `grid-portfolio-series-v1` even for one requested cycle. After the last
allowed cycle completes, the engine continues observing/reconciling and valuing
owned cash, pools and residual inventory. It places no new order or extra cycle,
adds no funding, and performs no forced EOF liquidation. Completion receipts
remain immutable. Fresh NAV uses each later bar's facts and timestamp; an
unavailable mark stays null instead of reusing an earlier completed exit NAV.

The existing default `through_end=False` retains its early-stop behavior and
schemas. The new API flag requires an actual boolean. Fixed-horizon results add:

| Field | Meaning |
|---|---|
| `evaluation_horizon` | `history_end` |
| `simulation_end_ms_exclusive` | The declared source/window endpoint |
| `execution_complete_ms_exclusive` | Final exit timestamp when all requested cycles completed; otherwise null |
| `post_completion_valuation_bars` | Later bars valued after all requested cycles completed |
| `completed_cycles_return_fraction_at_exit` | Final completed-cycle NAV divided by original window baseline minus 1; otherwise null |
| `observed_full_run_return_fraction` | Fresh window-end NAV divided by original window baseline minus 1; null if endpoint NAV is unknown |
| `completed_full_run_return_fraction` | The same marked endpoint return only when all requested cycles completed and endpoint NAV is known |

`stop_reason` is `history_exhausted` in this mode even if execution completed
earlier. `cycle_outcomes`, `last_cycle_final_return` and exit shortfall retain
their original cycle-completion meaning. Post-completion retained assets can
make a marked endpoint return differ from exit return; these are not guaranteed
realized cash returns. Benchmark holdings continue to the same endpoint, with
their original entry/funding basis. Risk, drawdown, inventory time, utilization
and unknown-mark counters include all evaluated bars.

Completion emits one additional interval; subsequent completed-state bars emit
only at the regular interval or EOF. The preflight includes every possible
completion plus the fixed endpoint:
`min(minutes, ceil(minutes / interval_bars) + max_cycles)` intervals, times two
snapshots and allocation count, must fit the existing 20,000 risk-point bound.
The earlier stopping mode retains its previous bound. Reconciliation history,
venue capacity, database bytes and result bytes remain shared across cycles and
post-completion observation; a requested horizon does not waive these caps.

## Selection and unavailable valuations

For each fold/scenario, all training candidates must have the same positive
initial owned NAV and the exact declared evaluation endpoint. Score is fresh
endpoint net NAV divided by that initial NAV minus 1. It includes unrealized
inventory, owned pools, fees and the controller's synthetic exit-cost assumptions.
Selection chooses the maximum score, breaking ties by candidate name.
It is frozen before any held-out replay. All test candidates are retained,
including unfavorable alternatives; test results never replace the selection.

If any training candidate's endpoint NAV is unavailable, the report retains
all training/test members but withholds selection for that entire fold/scenario.
`selection_status` is `unknown_training_nav`; `selection_unavailable_candidates`
lists those names; `selected_candidate` and both selected returns are null.
An available subset cannot win by excluding an uncertain candidate. If all
training marks are known, status is `selected`; an unknown selected test NAV
leaves only its test return null. Missing/malformed endpoint evidence, a changed
baseline, an early evaluation endpoint or incompatible horizon refuses the study.
Unknown valuation is distinct from an invalid member or execution failure.

## Bounds, compatibility and remaining work

The existing limits remain: 16 spacing candidates, 8 execution scenarios,
8 chronological folds, at most 128 member replays, 2,000,000 allocation/bar steps
and a combined 64 MiB report. Members run sequentially and retain the portfolio
engine's individual bounds. This is bounded work, not OS CPU/RSS/device-I/O
isolation or an assurance that a large accepted plan will finish quickly.
Data-format, dependency, financial-schema and live strategy decisions are unchanged.
Rollback is an ordinary revert; preserve previously published reports and revisions.

Inputs still use a fixed caller-declared roster and static fee/FX/rule assumptions
per scenario. Regime labels and plan hashes are not verified historical facts or
proof of preregistration. Point-in-time universe/delisting checks, historical
rules/fee/FX changes, independent-lot study integration, full storage benchmarks
and deployment resource evidence remain incomplete. No optimizer-to-live export
or profitability claim is introduced.

The observed 48-hour I8 gate, first-venue/private-account evidence, I9 and
deployment remain separately gated. The completed public hour is not extended.
Hosted CI remains pending under the unchanged account lock; publication uses
local checks, ordinary GitHub Free commit/push and `[skip ci]`.

## Validation

Twenty-four new focused cases cover fixed endpoints after early exits, continued
cash/benchmark valuation, fresh unknown NAV after a completed exit, requested
continuous cycles, retained completion/EOF intervals, strict flags, training NAV
selection, held-out losses, unknown training/test marks, one known/one unknown
training peer, frozen selection, changed future test prices, canonical ordering,
malformed evidence, unequal baselines, complete preflight, source mutation,
member/output-cap failure and actual CLI-process provenance. The prior basket
study tests also pass with their obsolete portfolio-rejection assertion removed.

One invented training window ends at NAV 1,015 for the selected spacing and
1,010 for its peer; held-out losses remain visible. Another fixture completes
its two candidates' exits at different times, then values both at the same
training endpoint before applying the declared name tie-break. A two-cycle
window carries 1,000 → 1,050 → 1,100, while its separate test window begins
again at 1,000. These are synthetic contract checks, not investment evidence.

The committed base portfolio and basket-study engines were loaded from Git for
unfinished holdings, a completed exit, a two-cycle replay and a basket study.
Every default result field and deterministic hash matched. The receipt is
`/tmp/grid-i8-portfolio-study-compatibility.json`, outside Git. This is finite
compatibility evidence; the default behavior was not inferred from passing new
tests alone. The controller, domain, executor and persisted financial schema
remain unchanged by this integration.

Final local validation on 2026-11-04 UTC:

| Check | Observed result |
|---|---|
| Python 3.12.16 full suite | 1,059 passed in 645.39 seconds; no failures/skips |
| Python 3.13.18 full suite | 1,059 passed in 676.78 seconds; no failures/skips |
| Ruff lint/format and strict mypy | Passed; 239 formatted files, 180 typechecked source/test files |
| Bandit over application/scripts | Passed |
| Tracked/new-file secret scan | Passed; existing baseline unchanged |
| Dependency lock and advisory audit | Unchanged 66-package graph; no known vulnerabilities reported |
| Wheel/source distribution | 83 application modules and 243 required source/test/document/manifest files byte-verified |
| Original specifications and attachments | All four pairs retain their original SHA-256 fingerprints |

Both full-suite receipts verify unchanged application-source SHA-256
`0566d7721227bcaeefd702f1eb031d871235ca6c39e3abcebe2d0edebd35e952`.
Logs/receipts are under `/tmp/grid-i8-portfolio-study-final-checks/`, with stems
`3.12-1793834869461226642` and `3.13-1793834869454247030`. Tests used
`python -m pytest -q` through locked `uv` environments `/tmp/grid-i2-py312` and
`/tmp/grid-i2-py313`, selecting Python 3.12.16 and 3.13.18 respectively. The
approved mock SQLite source gate remains required. Other checks follow README's
commands with these runtime/environment selections.

Artifacts are under `/tmp/grid-i8-portfolio-study-dist/`, outside Git, and the
package receipt is `/tmp/grid-i8-portfolio-study-package-verification.json`.
Generated ledger/history/journal payloads are excluded. The scoped increment
uses an ordinary commit/push on the existing branch with `[skip ci]`; no PR is
created under the established publication workflow. Network activity is limited
to Git publication/access checks and dependency advisory services. Hosted CI
remains pending under the unchanged account lock. Continue authorized offline
work from `IMPLEMENTATION-REVIEW.md`; hold live activation and paid GitHub use.
