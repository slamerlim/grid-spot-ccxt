# I8 offline walk-forward and sensitivity studies

This increment follows `0fe53c2c37a0c9bbd072bca8add848d7323dee13` under the owner's
instruction to continue development after the completed public hour. It addresses
part of PRD §9 with an offline study coordinator over the existing basket replay.
It changes no production strategy, ledger schema, execution path or dependency.
Original specifications, archives and public-run evidence remain unchanged.

## Behavior and boundaries

`gridctl backtest --config REPLAY --study PLAN --output RESULT` accepts an explicit
plan with chronological folds, spacing candidates and independent assumption
scenarios. It uses the ordinary replay engine and its reducer, funded cash/buy-hold
references, full-fill OHLC conventions and financial metrics. `--study` excludes
`--compare-scenarios`, `--compare-lots` and `--portfolio`. The same option is
available through `python -m grid.research replay`.

For each fold and scenario, run every candidate on training data, select the
highest total mark-to-close NAV return, and break ties by candidate name in
lexicographic order. Freeze this choice before running any held-out candidate.
Then run and retain **every** test candidate, including those that underperform.
The result identifies the training selection and its actual held-out return.
It never replaces that selection with the best test outcome.

Only grid spacing is selected. Fees, OHLC fill convention and modeled latency
are separate scenarios with a separate selection each; there is no ranking of
scenarios or choice of a favorable fee tier. A scenario applies its explicit
BUY/SELL rates to every allocation while retaining each allocation's fee currency,
increment, conversion assumption and maximum settlement count. A candidate applies
one spacing across allocations; target, budgets, ranges, pending-order limits,
minimum executable lots and all other policies remain as configured. The fixed
net target can dominate spacing in the SELL calculation. These are parameter
experiments, not verified account fee tiers or an exportable live configuration.

Every candidate/window starts with the same configured cash and fee tokens, no
open orders and no inventory. There is no warm-up, state carry, reinvestment or
stitched equity curve. This is walk-forward **parameter evaluation** with reset
funding, not a continuously traded strategy across fold boundaries. Training
windows may overlap or expand and later training may include earlier test data;
each fold must train strictly before its own test interval. Test intervals must
be disjoint and chronological. Training endpoints cannot move backward. Windows
use exact UTC minutes, end-exclusive, with at least two bars each.

Selection uses total NAV including unrealized inventory, not realized winners
alone. NAV remains the base engine's mark-to-close valuation without exit fees;
it is not the I7 executable portfolio NAV. Ending orders/inventory are reported,
not forcibly liquidated. Reported regimes (`up`, `down`, `sideways`,
`unclassified`) are caller-supplied descriptive labels; the program does not
infer or use regimes as signals. Each member retains its full metrics, including
fees, exposure, unfinished orders, drawdown and benchmark marks.

## Inputs, provenance and bounded work

The replay bundle defines a complete static roster and base history span. Before
any replay, validate all effective configurations and the complete selected base
window for every allocation. Validate exact timestamps, provenance, continuity,
precision and checksum using the existing importer. Thus a corrupt unused gap
between training and test is not silently discarded. Slices preserve the source's
original record offset and add the UTC window offset; every member independently
validates its slice. Compare candidate manifests within each phase and check the
original file identity before and after every replay and at completion. Any
member failure or changed input refuses the whole report, without publishing a
successful subset. Original history is read only.

The JSON plan schema is `grid-study-plan-v1`, capped at 1 MiB, with exact fields,
no duplicate keys, decimal strings for all rates/spacing and bounded declarations.
`research/study_config.py:bundle` can serialize a `research/study.py:Plan`.
Example of the complete plan shape (invented dates/rates, not trading advice):

```json
{
  "schema": "grid-study-plan-v1",
  "candidates": [
    {"name": "spacing-01", "spacing": "0.01"},
    {"name": "spacing-02", "spacing": "0.02"}
  ],
  "scenarios": [
    {
      "name": "declared-fee-strict",
      "model": "strict_skip_ambiguous",
      "latency_bars": 1,
      "buy_fee_rate": "0.001",
      "sell_fee_rate": "0.001"
    }
  ],
  "folds": [
    {
      "name": "fold-01",
      "train_start_ms": 1704067200000,
      "train_end_ms": 1704153600000,
      "test_start_ms": 1704153600000,
      "test_end_ms": 1704240000000,
      "train_regime": "unclassified",
      "test_regime": "unclassified"
    }
  ],
  "universe_evidence": "Describe original roster, exclusions and missing/delisted coverage.",
  "regime_evidence": "Describe labeling source/method, or explicitly declare unclassified."
}
```

All configured sources must cover the base replay interval, including both
windows in this example. A seven-bar demo cannot satisfy these two-day windows.
Use a new output outside the repository and protected original/evidence roots:

```sh
uv run --locked gridctl backtest --config /tmp/study-input/replay.json --study /tmp/study-input/plan.json --output /tmp/study-output/result.json
```

There are at most 16 candidates, 8 scenarios and 8 folds, additionally limited
to 128 total replays and 2,000,000 aggregate allocation/bar replay steps. The
step cap sums training and test lengths, including repeated overlapping training,
then multiplies by candidates, scenarios and allocations. Base validation and
repeated checksum reads are additional work. Results run sequentially, retaining
complete member reports, with a 64 MiB encoded member sum and final encoded report
cap. Checks occur after encoding; this is not a hard process-memory quota.
Existing per-replay bounds remain in force. No worker pool or archive cache is
added. This increment does not close the CPU/RSS/IO contention requirement.

The inner result schema is `grid-walk-forward-study-v1`. It contains canonical
config/plan, the fixed selection rule, config hash, complete base manifests and
one result row per fold/scenario with training/testing mappings by candidate name.
Each member keeps its ordinary `grid-research-v1` schema and hashes. The existing
CLI `grid-research-run-v1` envelope adds observed source/runtime/Git provenance
and payload hash. Direct engine calls do not add that envelope. There is no
resume/checkpoint; interrupted or failed work must be inspected and explicitly
rerun to a fresh path. No existing output is overwritten.

## Scientific limits and remaining work

A plan hash records declared inputs; it cannot prove the plan was fixed before
the researcher saw historical test outcomes. Retaining all test candidates helps
inspection but creates multiple-testing exposure. Subsequent human selection
using these results requires new untouched data; this program supplies no
statistical-significance or profitability claim.

Universe and regime evidence strings are declarations, not independently verified
proof. The static complete-history requirement can exclude delisted/missing
markets and produce survivorship bias. Point-in-time listing/fee/rule snapshots,
explicit delisting transitions, historical FX, representative target-venue data,
automatic regime classification and historical roster changes remain absent.
No assertion that survivorship has been resolved is permitted. The independent-lot
reference and portfolio exit engine remain separate commands; neither is included
in this study coordinator. Continuous multi-cycle replay and resource isolation
also remain outstanding. The minimum observed 48-hour soak, intended-first-venue
evidence and I9 authorization remain open; no exchange calls were added or run.

## Validation and rollback

Seventeen focused cases cover an unfavorable held-out ranking reversal, changes
to held-out prices leaving training selection unchanged, training-before-test
execution, separate fee/model/latency scenarios, shared funding/rule preservation,
original record offsets, expanding folds, canonical ordering at low ambient
Decimal precision, deterministic ties, invalid/overlapping windows, unused corrupt
history, mid-study/final-member changes, workload/output caps and member failure,
strict plans, a separate CLI process, conflicts and existing report hashes.

Final local validation on 2026-11-05 (local date):

| Check | Observed result |
|---|---|
| Full pytest, Python 3.12.16 | 969 passed in 523.59 seconds |
| Full pytest, Python 3.13.18 | 969 passed in 551.75 seconds |
| Focused study cases, both interpreters | 17 passed each |
| Ruff lint and format | Passed; 223 formatted files |
| Strict mypy over application/tests | Passed, 169 files |
| Bandit over `src` and `scripts` | Passed |
| Locked dependency resolution | Passed, 66 packages; manifests unchanged |
| Advisory audit of locked export | No known vulnerabilities reported |
| Staged secret scan | Passed with unchanged baseline |
| Wheel byte comparison | All 79 application modules match; no data/ledger artifacts |
| Source archive byte comparison | 228 required source/test/doc/manifest files match; no data/ledger artifacts |
| Documented JSON example | Loads as two candidates, one scenario, two 1,440-bar windows |

Both full-suite receipts record an unchanged application source digest:
`830c77929d550ba19baf2e51d9751275605ff01556051c85cfd3046467f7fdf4`.
Durable logs and JSON completion receipts are outside Git in
`/tmp/grid-i8-study-final-checks/`, named
`3.12-1793827996578918318` and `3.13-1793827996568140608`.
Packages are under `/tmp/grid-i8-study-dist/`; the audit used
`/tmp/grid-i8-study-audit-requirements.txt`. All four supplied specification
hashes remain unchanged. These tests use synthetic data/venues and temporary
ledgers; no external market/account access was performed.

No hosted CI pass is claimed; the known account lock is unchanged. Publication
uses GitHub Free only, ordinary authorized pushes and `[skip ci]`.

Rollback by omitting `--study` or reverting this scoped commit through a new
commit. Existing commands and schemas remain available. There is no migration,
dependency change, service installation or live configuration change to undo;
preserve any generated reports as evidence.
