# Interview research sweep

Current viewer: the earlier HTTP UI described in historical sections below was
replaced by the read-only SSH Streamlit table; Nginx is disabled and masked.
See [current access and controls](BACKTEST_UI.md). Existing result artifacts and
CLI financial behavior are preserved.

This implements the owner's 2026-11-19 approved plan, based on
[the completed interview](RESEARCH_INTERVIEW_2026-11-19.md). It is a separate
research policy, not a change to funded accounting or the live trading core.

The owner subsequently approved all decisions for the expanded exchange/global-TP
matrix on 2026-11-20. The final section of
[the interview](RESEARCH_INTERVIEW_2026-11-19.md) records those answers. The
423,360-configuration extension is implemented as a separate bounded workflow;
its contract, binary storage and current validation limits are documented at
the end of this page. The completed historical evidence below belongs to the
original 540-configuration study. It does not certify a new expanded full run.

## Original 540-configuration contract

The strict roster consists of every archived spot USDT pair on OKX, HTX,
Gate, XT, BingX, KuCoin and Bitfinex, with each source's full available history.
The owner's subsequent filtered research plan admits every pair whose agreed
spot, precision, minimum quantity and minimum cost rules are established, and
retains a reason for each exclusion. It still requires all seven venues and the
full available history of every admitted pair. Strict preparation remains
available separately.
Grid steps 0.5–10% and TP 2–15%, both at 0.5 percentage-point increments,
produce 540 independent configurations. Configurations share immutable input
blocks, never financial state. Parallel work is across configurations.

The first observed price creates one minimum BUY. Lower theoretical levels
follow the unrounded geometric sequence. BUY rounds down, SELL rounds up;
duplicate BUY prices are skipped, not grounds to reject a pair/configuration.
An already valid price gets no additional tick. Waiting BUY requires a strictly
lower observed price; SELL requires a strictly higher one. Modelled execution
is full at the order price. TP is above quantity-weighted average cost, before
fees. Fees are exactly 0.2% of each trade's cost, charged in USDT. There is no
cash limit, reservation model, partial execution or global sale.

After SELL the next BUY and the 60% lower boundary use the executed SELL price.
Growth alone does not raise the BUY ladder. No artificial boundary level is
inserted. Holdings remain owned below the boundary and at EOF. Paid BUY fees
remain in the result; an unexecuted SELL creates no fee. `spent` is open cost
without fees; portfolio peaks use simultaneous sums. Net PnL includes marked
open inventory. Percentage return divides net PnL by peak simultaneous spent,
without annualization. Monetary drawdown starts with a zero PnL baseline.

The adapter emits O/H/L/C in four common phases, with synthetic offsets of
0/1/2/3 seconds. Those are declared ordering labels, not recovered trade times.
Missing records generate no trading events. Carried valuations retain the
actual last observation time. One dated rule snapshot applies to all history;
these results are assumption-based, not certified historical executions.

## Interfaces and ownership

`grid_backtest research-sweep PLAN_JSON` consumes
`grid-research-sweep-plan-v1`. Sources include immutable paths, SHA-256, record
counts/endpoints and exact minimum/precision rules; configurations contain an
ID, decimal-string `step_pct` and `tp_pct`. `full` requires all 540 combinations
and all seven venues, and rejects diagnostic windows/record caps. It accepts
either a strict-ready preparation or a ready seven-venue selection verified
against its immutable audited parent.

The Python job entry point is:

```sh
python -m scripts.research_sweep worker --request REQUEST.json --output NEW_DIRECTORY
```

The direct CLI also accepts `prepare`, `collect`, `select`, `validate`, `calibrate`,
`endurance`, `rehearsal`, `full`, `resume` and `chart`, with `--output`, an
optional `--source-output` referring to a retained previous job, and `--binary`.
The normal executable is
`/opt/grid-builds/module1/research-sweep-release/grid_backtest`.
CLI and UI share `/opt/grid-runs/.research-heavy.lock`; UI delegates one inherited
lock to its owned child. Old research runs and derived charts use the same gate.
There is no new six-hour timeout and no automatic resume at service startup.

Job receipts preserve readiness/blockers, executable identity and completed
stage evidence. A complete run requires matching validation, calibration,
endurance and rehearsal receipts. A strict preparation with unresolved required
rules cannot admit a full run; a ready selected manifest explicitly identifies
the admitted roster and every excluded source. Diagnostic subsets never replace
the declared roster.
`study-manifest.json` binds the source/rule manifest hash, complete 540-row
configuration matrix, declared assumptions and calculation/source-code identities.
Retained `admission.json` preserves prerequisite evidence across a stopped job.
Resource limits have the distinct terminal status `RESOURCE_LIMIT`.
Live progress includes observed resource samples, completed records, recent
record throughput and the latest checkpoint time. Full-history ETA is an
updating estimate from observed throughput, excluding future final verification;
diagnostic windows do not claim an ETA from the full archive's record count.

Engine artifacts are `summary.json`, `pairs.jsonl`, `pairs.index.json`,
`series.jsonl`, `progress.json` and two checkpoint generations. Financial strings
retain exact ratios; only presentation converts/rounds them. Full event dumps
are restricted to small validation fixtures. Detailed pair charts replay the
frozen source/configuration and must reproduce the exact pair result and hash.

Checkpoints commit at common block boundaries, retain next unprocessed source
positions and output commit offsets, and bind source/rule/configuration/build
identity. A resumed job preserves the earlier directory. Uncommitted output
tails cannot be counted twice. Committed output prefixes are hashed before
continuation; a damaged latest checkpoint may fall back to the previous valid
generation. A changed rule, parameter, source or executable cannot resume it.

## Verification and experiments

The independent Python Fraction oracle covers the interview policy rather than
reusing the old funded reducer. Exact trade/state comparisons cover strict
equality, minimum sizing, geometric rounding, collapsed levels, fees, local
reset, gaps, EOF, portfolio peaks and significant-digit pricing. Tests compare
thread counts/block sizes and interruption/recovery. Existing Python and C++
regression suites remain required.

Calibration freezes a deterministic, venue-balanced set of at most 64 pairs,
the earliest seven-day window with maximal source-span overlap, and one bounded
workload before comparing settings. The sequence is 1/2/4/8/16/24/32 workers at
4 MiB, then 1/4/16 MiB buffers at the chosen worker count, three rotating repeats.
Choose the fastest median with matching exact outputs; within 5%, prefer fewer
workers and smaller buffers. Retained OS cache is disclosed; no cache eviction
or host tuning is performed. Blocks additionally stop after 64 timestamp groups.
The calibration report explicitly notes that a small 64-pair workload may not
fill any tested buffer; such measurements cannot establish a benefit from
unexercised buffer capacity. A preceding 8-pair, 24-hour smoke run uses all 540
configurations. The historical stages have not run on this installation yet.

Resource defaults are 64 GiB worker RSS, at least 32 GiB available RAM and free
disk, and at most 64 GiB of job artifacts (owner amendment on 2026-11-21).
Previously prepared studies retain their original bound. The full-roster synthetic endurance
test lasts an observed hour. Historical rehearsal uses seven calendar days;
full execution uses all records without a deadline or profit-based selection.

## Evidence

As of 2026-11-19, the separate calculation engine, preparation/collection,
checkpoint recovery, CLI and existing web integration are implemented. No
complete historical sweep, calibrated production speed or observed full-roster
hour is claimed.

The complete audit read **6,314 files / 113,258,323,464 bytes /
4,719,096,811 records**, with zero structural, chronology or price failures.
Gaps remain in the inputs and report. Seven bounded public catalogue GETs
completed, one per venue, with no private calls. The immutable
[source manifest](/opt/grid-runs/research-sweep-20261119/preparation/manifest.json)
has SHA-256 `4d3da838cf827e5b9ca8b9bcea02a9f4610e7eaa8532a2795793e802c45665c2`.
The original scanner used for that audit is retained byte-for-byte; subsequent
scanner changes add the independently checked C++ fallback. Original datasets
were not changed.

| Venue | Sources | Complete rules | Blocked |
|---|---:|---:|---:|
| OKX | 493 | 0 | 493 |
| HTX | 626 | 626 | 0 |
| Gate | 2,443 | 2,006 | 437 |
| XT | 982 | 16 | 966 |
| BingX | 795 | 0 | 795 |
| KuCoin | 909 | 802 | 107 |
| Bitfinex | 66 | 0 | 66 |
| Total | 6,314 | 3,450 | 2,864 |

The nonoverlapping causes are 600 missing matching metadata entries, 1,705
unknown minimum quantities, 558 unknown minimum costs, and one unconfirmed spot
market. Unknown minima are not assumed zero; delisted histories are not removed.
All individual reasons are in
[blockers.json](/opt/grid-runs/research-sweep-20261119/preparation/blockers.json).
This does not prove the missing rules are impossible to recover.

Validation evidence under
[`verification/`](/opt/grid-runs/research-sweep-20261119/verification) records:

- Release CTest **12/12**; ASan/UBSan C++ checks **5/5** and an independent
  instrumented replay matching **1,931 trades**.
- Fraction comparisons at 1/2/4 workers and different block sizes; a separate
  complete 540-configuration fixture checked **4,320 pair states**, with matching
  full-matrix results at 4 and 32 workers.
- **10 recovery checks**: exact uninterrupted/resumed output, discarded
  uncommitted tail, previous-generation fallback, source/output corruption,
  changed rules/TP, insufficient RSS/disk, and actual SIGTERM continuation.
- **75 focused sweep tests**, **91 UI/paired/chart tests** and **5 child-helper
  tests** passed. Ruff, formatting, mypy, JavaScript syntax and Bandit passed.
- The full Python run had **1,999 passed, 7 skipped, 3 failed**. Two failures
  come from pre-existing QUOTE→USDT synthetic-fixture changes versus frozen
  codec hashes; one totals fixture expects USD/BTC despite now providing USDT.
  Those unrelated changes were preserved. This is not a green full regression
  suite; details and baseline evidence are in `verification-summary.json`.

The release binary is
`/opt/grid-builds/module1/research-sweep-release/grid_backtest`, SHA-256
`be8723f358624f1d81ce7302b8d9dc51bb1a1f7ff97031bfa0bf7048649caf1c`.
Historical calibration is **BLOCKED** because three venues have no pair with
complete mandatory rules. Endurance, historical rehearsal and full replay have
saved **BLOCKED** receipts for their missing prerequisites; they were not
replaced by reduced-universe or invented-rule performance claims. The next
step is resolving the listed metadata and rerunning preparation, then the
existing staged CLI/UI workflow.

The existing UI service was updated and restarted. Its actual HTTP worker
completed the Fraction/recovery validation and returned the expected blocked
historical-calibration receipt. The saved preparation, validation and blockers
are visible in Research 540. This installation's existing Nginx listener is
loopback-only and was preserved; use the existing SSH/IDE forwarding to
`http://127.0.0.1:8765` (or localhost `/module1/backtesting/`). No public proxy
exposure was added or claimed by this change.

## Filtered seven-venue admission (2026-11-19)

The latest owner-approved plan permits excluding archived pairs only when an
agreed model rule is unknown. A fresh byte audit read all **6,314** original
sources and found no data, chronology or price blockers. The preserved
[strict preparation](/opt/grid-runs/research-sweep-20261119/filtered-preparation-20261119/preparation/manifest.json)
has SHA-256 `d919c539e9eaeafda8172fe9a64a0d9826175b2a192b694ca3b896f0f7e189a4`.
It remains BLOCKED because it contains all 1,522 pairs without complete rules.
The separate [selected manifest](/opt/grid-runs/research-sweep-20261119/filtered-selection-final-20261119/preparation/manifest.json)
has SHA-256 `3a74eaac4925798c3d332d64f8473f62522a6a0fadc460a29eaccb5505638152`
and is READY with no data blockers. It admits **4,792 pairs, 4,173,223,584
records and 100,157,366,016 source bytes** across seven venues:

| Venue | Admitted | Excluded |
|---|---:|---:|
| OKX | 483 | 10 |
| HTX | 626 | 0 |
| Gate | 2,006 | 437 |
| XT | 16 | 966 |
| BingX | 795 | 0 |
| KuCoin | 802 | 107 |
| Bitfinex | 64 | 2 |

All 1,522 exclusions retain their pair ID, source hash and precise reason in
the selected manifest: 910 unknown minimum quantities, 600 absent matching
market entries, 11 unknown minimum costs and one unconfirmed spot identity.
The original strict manifest and every original dataset byte are untouched.

The public-rule recovery used saved CCXT snapshots plus two bounded Bitfinex
public GETs. The latter saved both the official `pub:info:pair` minimum-size
table and `pub:list:pair:exchange` exchange-pair list; only pair IDs present in
both were considered. The raw response hashes are
`b40c5189c0ba7afdc169e4ab0515af5431960a8e7d9f22d6a59a6a0a73e9d1fa`
and `48dda4f7847e5343f5d7db3c4e977d0e7c2134ff237dba0806088d2088a12e0a`.
The [Bitfinex Configs reference](https://docs.bitfinex.com/reference/rest-public-conf)
defines those fields; its [minimum-order help page](https://support.bitfinex.com/hc/en-us/articles/115003283709-What-is-the-minimum-order-size-on-Bitfinex)
describes the pair-specific size rule. Treating that published size rule as the
complete minimum-order restriction for ordinary exchange LIMIT orders is an
explicit research-model interpretation; it is not a live-order certification.
The [BingX spot reference](https://github.com/BingX-API/api-ai-skills/blob/main/skills/spot-market/api-reference.md)
documents native `minQty` and `minNotional`; both are checked against unified
values where present. The [OKX instrument guide](https://www.okx.com/docs-v5/en/)
provides spot `minSz`, `lotSz`, and `tickSz`, while the
[RPI change log](https://my.okx.com/docs-v5/log_en/) explicitly exempts non-RPI
orders from the RPI minimum notional.

`select` is available in the same CLI and Research 540 web workflow. It writes
READY only with at least one admitted pair per venue, verifies the complete
partition against the audited parent, and rejects altered counts, reasons,
rules, source hashes or data findings. All subsequent stage receipts bind the
selected manifest, calculation-code and binary hashes; the earlier strict-roster
validation is not admitted as proof for this new roster. The historical stages
must complete in order before publication of a full-history result.

The selected-roster [validation receipt](/opt/grid-runs/research-sweep-20261119/filtered-validation-formatted-20261119/receipt.json)
is PASS (SHA-256 `aec45c9c4ff7d52e9b304171d8731e25646588c84e9868440f0471f11e98d8fb`).
It binds selection SHA-256 `3a74eaac4925798c3d332d64f8473f62522a6a0fadc460a29eaccb5505638152`,
implementation identity `d8d7b530c137676d970524ecf4cfc6c70a558aaec1a195b8c03a9c4e4ce85bbd`
and release binary SHA-256 `be8723f358624f1d81ce7302b8d9dc51bb1a1f7ff97031bfa0bf7048649caf1c`.
The exact serial/parallel fixtures and ten recovery checks passed. Release CTest
passed 12/12 and ASan/UBSan CTest 5/5 for the unchanged C++ engine. These are
calculation checks, not substitutes for the pending measured historical stages.
The new full Python suite reports **2,035 passed, 7 skipped and the same 3
pre-existing fixture failures** as the earlier baseline: two frozen manual-codec
hash expectations against changed synthetic quote fixtures and one paired totals
expectation of USD/BTC groups against USDT-only fixtures. The complete
[test log](/opt/grid-runs/research-sweep-20261119/filtered-selection-final-20261119/pytest-full.log)
retains the comparisons; no new failure was observed. Ruff, mypy and JavaScript
syntax checks passed.

The running web service completed its `select` action on a byte-identical copy
of the audited preparation. Its saved
[READY selection](/opt/grid-runs/backtest-ui/sweep-20261119T094715Z-09b70867/preparation/manifest.json)
has the same 4,792/1,522 partition. Its own manifest hash differs because its
audited parent path and creation time differ; CLI evidence is therefore not
silently reused for a web job. The research UI remains on loopback behind its
existing proxy configuration.

The approved sequence is running as transient local unit
`grid-research-sweep-filtered-r2-20261119.service`. Its
[stage script](/opt/grid-runs/research-sweep-20261119/filtered-selection-final-20261119/run-stages.sh)
runs calibration, observed one-hour endurance, seven-day rehearsal and then the
full history only after each preceding command exits successfully. The unit has
private networking, a read-only system tree except `/opt/grid-runs/`, a 68 GiB
memory ceiling and no automatic restart/resume. Each stage retains its own
receipt and logs under `/opt/grid-runs/research-sweep-20261119/`; an interrupted
stage still requires an explicit compatible resume action. The
[calibration receipt](/opt/grid-runs/research-sweep-20261119/filtered-calibrate-r2-20261119/receipt.json)
is PASS (SHA-256 `e063bae2ba7ed3a17d07ad4ae964b7bc6951e8938a5015dcaa118e470f49a211`).
Three comparable worker-count repeats selected **32 calculation threads**:
median replay time was 3.91 s versus 4.31 s with 24 threads, with identical
exact result fingerprints across all tested counts. Three buffer repeats then
selected a **1 MiB block**, with median replay time 4.07 s at 32 threads.
The 32-thread calculation process reported 239 MiB RSS in its first worker
measurement. These short, warm-cache timings are not a full-roster throughput
forecast. The observed one-hour full-roster synthetic endurance stage is now
running with the calibrated settings; historical rehearsal and full replay
remain pending. This is not a completed historical result.
Its first 15-minute checkpoint was published as a 1.62 GiB
`checkpoint.latest.json` after 1,360,938 processed records and 38 complete
blocks. The saved header has 4,792 source cursors, the expected plan and binary
hashes, and a 38,156,126-byte committed series prefix whose SHA-256 was checked
independently against the saved prefix hash. The worker continued afterward.

Before publishing a final research report, run the independent streaming
publication audit after the full job completes:

```bash
/opt/grid-tools/module1-py312/bin/python -m scripts.research_sweep_verify \
  --job /opt/grid-runs/research-sweep-20261119/filtered-full-r2-20261119 \
  --selection /opt/grid-runs/research-sweep-20261119/filtered-selection-final-20261119/preparation/manifest.json \
  --output /opt/grid-runs/research-sweep-20261119/filtered-full-r2-20261119/publication-audit.json \
  --rehash-sources
```

It independently checks prerequisite receipt hashes, the exact 540-member
matrix, all admitted source identities and final close prices, every
configuration/pair row and offset, exact pair-to-venue/global sums, complete
record coverage, and source SHA-256 when requested. Omitting
`--rehash-sources` produces `STRUCTURE_VERIFIED`, not a final publication
status. It does not claim to reconstruct every trade from a final hash alone;
a detailed selected-pair chart
uses the existing exact replay and compares that pair's trade hash. The auditor
is separate from the calculation-code identity and cannot change an ongoing
study. The audit also accepts a compatible completed `resume` job, using that
job's output path as `--job`. Its five offline tests, Ruff and mypy checks
passed. The exact rollup
checks also passed against all 540 configurations of a retained calibration
result (64 pairs each). A C++ engine
`COMPLETE` receipt alone is not the final independently verified report.

The first calibration attempt was deliberately stopped while investigating a
3.2 GiB smoke-chart file; its CANCELLED receipt and checkpoint are retained.
Inspection showed that the C++ chart already caps output at 2,000 time bins
per configuration and scope, retaining first/last and extrema. The observed
6,125,760 lines averaged 548 bytes; the cap for this eight-scope smoke chart
is about 8.64 million lines. The size does not scale with the full record
count, so the same verified code and roster were restarted in the `r2` unit.
The first attempt is not counted as a passed stage.

## Optimization workflow (2026-11-19)

This section supersedes the earlier running-stage status, not its retained
measurements. The original rehearsal was stopped directly at the C++ worker.
Its verified checkpoint covers 4,414,898 records and all 540 configurations;
the old full-history job had not started. The wrapper recorded FAIL because
exit code 3 denotes an explicit checkpoint stop, not successful completion.
The original executable, source snapshot, plan and profile are preserved in
[`optimization-baseline-20261119/`](/opt/grid-runs/research-sweep-20261119/optimization-baseline-20261119).
Its `stop-receipt.json` records the independently checked checkpoint payload
and committed series prefix. New binaries must not continue that checkpoint.

The optimization comparison retains the same 4,792-pair selected manifest and
540 configurations. `scripts.research_sweep_benchmark` requires a new matching
validation receipt and holds the shared heavy-work lock. It compares the
unchanged baseline executable with candidate static and dynamic configuration
scheduling, then compares 1/4/16 MiB buffers with the selected scheduler. All
runs use 32 calculation threads. Every set has three rotations so each variant
occupies each execution-order position once; OS cache is retained, not evicted.
Background host processes are not stopped. Each trial records the 1/5/15-minute
load averages before and after execution; the report records CPU count/affinity.
The earliest maximal-span-coverage seven-day window is frozen before returns
are read. For this manifest it is `[1794182400000, 1794787200000)` UTC ms.

The default workload cap is 2,097,152 source records; replay stops before a
timestamp group that would exceed the cap, preserving complete groups. The first 64 actual timestamp
groups form a fixed financial-state warmup; the next group begins at
`1794186240000` for this manifest. Candidate blocks end exactly at that warmup
boundary. Candidate diagnostics report the later measured interval separately;
old-versus-new speedup and settings selection use total replay time including
the identical warmup prefix, because the frozen old executable cannot report
that separate interval. Initial/final source audits and result publication are
reported in wall time, outside replay time. A buffer trial must fill at least
two measured input blocks; otherwise calibration fails instead of selecting an
unexercised capacity. The candidate permits up to 4,096 timestamp groups per
block; the original executable retains its actual 64-group limit.

Exact comparison includes summary accounting, every pair result and trade
hash, and per-scope chart rows, including first-extreme times. Technical block
counts and timing fields do not enter the financial fingerprint. Failed or
mismatching outputs remain intact. Successful duplicate pair/chart outputs and
completed benchmark checkpoints are removed only after their hashes and exact
fingerprint are recorded in `compaction.json`. Detailed financial outputs from
the first baseline and selected candidate remain available; their checkpoints
are unnecessary for completed diagnostic runs. Scheduler-phase candidate
outputs are compacted before the buffer phase to bound storage.

The original rehearsal processed about 500 records/s. At that rate the three
2.1-million-record baseline repetitions alone would take about 3.5 hours;
this is an estimate, not a measured optimization result. The 18 executions
also re-audit the 100 GB sources before and after replay, approximately 3.6 TB
of logical reads. This correctness overhead is visible separately. At review,
free disk was about 49 GiB, only about 17 GiB above the required floor. Benchmark,
smoke, synthetic endurance, rehearsal and final outputs all consume that same
free space. Every stage rechecks existing resource limits; insufficient space
must leave RESOURCE_LIMIT and cannot be represented as a completed stage.
Original datasets and earlier retained runs are never removed by compaction.

After the final candidate is frozen and local C++/Python, differential and
recovery checks pass, this is the ordered CLI workflow. Use a new candidate
build directory and new output names; these commands describe the workflow and
are not evidence that the stages have run:

```bash
set -euo pipefail
cd /opt/grid
grid_python=/opt/grid-tools/module1-py312/bin/python
grid_binary=/opt/grid-builds/module1/research-sweep-opt-final/grid_backtest
grid_runs=/opt/grid-runs/research-sweep-20261119
grid_selected="$grid_runs/filtered-selection-final-20261119"
grid_baseline="$grid_runs/optimization-baseline-20261119/grid_backtest"

"$grid_python" -m scripts.research_sweep_differential fixtures \
  --baseline "$grid_baseline" --candidate "$grid_binary" \
  --output "$grid_runs/optimization-final-differential"
"$grid_python" -m scripts.research_sweep validate --binary "$grid_binary" \
  --source-output "$grid_selected" --output "$grid_runs/optimization-final-validate"
"$grid_python" -m scripts.research_sweep_benchmark \
  --baseline-binary "$grid_baseline" --binary "$grid_binary" \
  --source-output "$grid_runs/optimization-final-validate" \
  --output "$grid_runs/optimization-final-benchmark" --max-records 2097152
"$grid_python" -m scripts.research_sweep calibrate --binary "$grid_binary" \
  --threads 32 --source-output "$grid_runs/optimization-final-validate" \
  --benchmark-output "$grid_runs/optimization-final-benchmark" \
  --output "$grid_runs/optimization-final-calibrate"

grid_source_job="$grid_runs/optimization-final-calibrate"
for grid_stage in endurance rehearsal full; do
  grid_output="$grid_runs/optimization-final-$grid_stage"
  "$grid_python" -m scripts.research_sweep "$grid_stage" --binary "$grid_binary" \
    --source-output "$grid_source_job" --output "$grid_output"
  grid_source_job="$grid_output"
done
"$grid_python" -m scripts.research_sweep_verify \
  --job "$grid_runs/optimization-final-full" \
  --selection "$grid_selected/preparation/manifest.json" \
  --output "$grid_runs/optimization-final-full/publication-audit.json" \
  --rehash-sources
```

The short calibration still runs its eight-pair smoke and bounded 64-pair
measurements. `--threads 32` fixes its candidate count; the sequential pilot
still fixes work volume. `--benchmark-output` accepts only a PASS receipt with
the same preparation, code, candidate executable, source count and 540-config
matrix. It preserves short-calibration results separately and adopts the
full-roster scheduler/buffer settings. Each later stage checks the benchmark
receipt hash and those settings again. The code identity now includes the
exact-unit implementation and benchmark/differential scripts, so earlier
validation or calibration receipts cannot admit this changed implementation.
An explicit `resume` must reference an interrupted job of this exact candidate;
there is no automatic resume after interruption or a resource stop.

The CLI and UI progress retain nested timing and resource diagnostics. The
benchmark/calibration/UI focused tests currently pass 47 checks; Ruff, mypy and
JavaScript syntax were checked locally. The fresh full Python regression with
an isolated `GRID_RESEARCH_HEAVY_LOCK` finished with **2,070 passed, 7 skipped
and the same three pre-existing fixture failures in 622.47 seconds**:
[`optimization-python-isolated-20261119T140417Z/`](/opt/grid-runs/research-sweep-20261119/optimization-python-isolated-20261119T140417Z).
Those failures are `test_manual_codec` False/True and the paired USD/BTC totals
expectation against USDT fixtures. There are no additional failures relative
to the retained baseline. The original concurrent suite attempt remains in
[`optimization-python-regression-20261119T135720Z/`](/opt/grid-runs/research-sweep-20261119/optimization-python-regression-20261119T135720Z):
it had 23 additional worker/HTTP/timeout failures while a separate diagnostic
held the global heavy-work lock. The isolated repeat removed those conflicts
without a source change. A new failure still blocks advancement. The external
`optimization-final-run-stages.sh` contains the subsequent command chain but
has not been started. No completed optimization benchmark or new historical
result is claimed by this section.

The optimized executable SHA-256 is
`70021888218af5e7fcbd9ae8163ff0ba2a03d230d502f444ceea3b5927782b4c`.
It is preserved in `research-sweep-opt-final` and installed atomically at the
existing CLI/UI default executable path; `optimization-final-install.json`
binds both versions and the retained original. This does not continue any old
checkpoint or pass the new full-roster measurement gates.

Amounts use exact lot units and monetary coefficients with denominators made
from powers of two and five. Source audit includes the exact binary32 price
denominators; pair increments and the 1/500 fee determine sufficient monetary
scales. Nothing is rounded to integer USDT. Checked signed 128-bit operations
promote to bounded big integers on overflow. Geometric prices, average cost,
TP and order rounding retain the existing exact rational implementation.
Price-only events avoid unchanged spent/fee/realized calculations. Shared input
blocks contain prepared timestamp groups and immutable valuation coefficients.
Parallel checkpoint producers stream canonical state in bounded chunks through
the existing worker pool; one writer preserves order and atomic publication.
The maximum serialization allowance is 609 MiB with 32 producers. Incremental
output hashes avoid repeated full-prefix reads during normal checkpoint saves;
recovery still verifies the complete committed prefix.

Final local evidence:

- Release CTest **13/13**, ASan/UBSan CTest **6/6**, and an instrumented dynamic
  replay matching **1,612 trades / 24 pair states** against Fraction.
- [`optimization-final-differential-r2-20261119/receipt.json`](/opt/grid-runs/research-sweep-20261119/optimization-final-differential-r2-20261119/receipt.json):
  **16/16** exact old/new/Fraction scenarios including all 540 configurations,
  numeric extremes, static/dynamic 32-thread operation, graphs and first-extreme
  dates, and an exercised warmup boundary with more than 64 groups in a block.
- [`optimization-final-validate/receipt.json`](/opt/grid-runs/research-sweep-20261119/optimization-final-validate/receipt.json):
  matching validation PASS, including **12 recovery checks**. A 4,096-market
  synthetic fixture produces configuration lines larger than two 1 MiB queue
  chunks, proving actual backpressure and exact resume. An injected file-size
  limit makes the checkpoint writer fail inside a write syscall; producers
  terminate without deadlock and no successful report is published.
- [`optimization-stage2-real64-benchmark-20261119/receipt.json`](/opt/grid-runs/research-sweep-20261119/optimization-stage2-real64-benchmark-20261119/receipt.json):
  one fixed retained-cache comparison on 64 real pairs × 540 configurations,
  **65,504 records**. Baseline replay **79.691 s**, removal of redundant rational
  work **25.316 s**, exact-unit version **15.561 s** (5.12× baseline). All exact
  fingerprints, including **1,058,400 graph rows**, match. This is preliminary
  evidence, not the required repeated 4,792-pair calibration. Output writing
  consumed 13.173 s of that small exact-unit replay.

The complete benchmark/recalibration/endurance/rehearsal/full chain is prepared
but has not started. With about 41–42 GiB free and the unchanged 32 GiB free-disk
floor, retained stages cannot all fit. The read-only
[`disk-retention-proposal-20261119.json`](/opt/grid-runs/research-sweep-20261119/disk-retention-proposal-20261119.json)
lists exact successful diagnostic files and hashes for possible lossless archival.
The storage choice is pending; no prior diagnostics have been archived or removed.

## Approved follow-up: on-demand charts and ten-minute endurance

The owner subsequently freed disk space without authorizing changes to old
artifacts, and approved a new acceleration pass. The former accelerated binary
and sources are preserved at
`/opt/grid-runs/research-sweep-20261119/optimization-next-baseline-20261119/`.
The completed `optimization-final-rehearsal` remains the measured baseline:
45,459,351 rows on 4,792 pairs and all 540 configurations, 448.127 s replay,
264.339 s initial audit, 237.342 s final audit and 143.092 s final pair output.
Replay includes 125.587 s chart output; its series occupied 6,848,313,916 bytes.
These timings overlap where stated and must not be added twice.

New CLI/UI research jobs explicitly use `chart_mode=on_demand`. This removes
series accumulation and writing from the main calculation, while preserving all
exact financial values, first timestamps of extrema, holding durations and
trade hashes. Internal validation plans and old missing-field plans retain
`eager` behavior. A requested aggregate chart replays the selected configuration
on the original entire roster and horizon, checks every pair plus venue/global
results, and caches the verified chart. The existing pair replay is retained.
Absent precomputed charts are explicitly marked, not represented as empty data.

The new acceptance policy is `optimized-600s-v1`: at least 600 seconds of
full-roster/full-matrix synthetic endurance, at least two published checkpoints,
and passing independent recovery checks. Checkpoints are requested every 120
seconds in that stage. This replaces the previous hourly gate for this chain;
it is not a claim of one-hour endurance. The seven-day rehearsal and full-history
stage remain required, with fresh state for the new build. Every receipt binds
the policy, selected manifest, code and executable. Older evidence cannot pass
the new gate merely by sharing a directory or parameter count.

Technical comparisons use the current candidate only. A bounded process-owned
batch audits sources before and after all members, preserves immutable input
identity between members and starts fresh state each time. All financial rows
are hashed even when duplicate detailed output is suppressed. A retained member
also undergoes independent semantic verification. No member is published as
complete until the batch's final hash audit succeeds. Queue, input block and
reader settings are measured in three alternating repetitions; the resource
limits and exact financial contract remain unchanged. Optional `GRID_LTO=ON`
enables checked compiler interprocedural optimization without fast-math.

The first checked on-demand executable is
`/opt/grid-builds/module1/research-sweep-on-demand/grid_backtest`, SHA-256
`559e8c256c7c6531241494bff86773c26a88252280bbc0474cd799c778a13715`.
It uses LTO without fast-math and was installed at the existing research UI
default executable path. The retained source identity is
`1c99a993904506aab6826cd006efc4923ad5a0b20bb19645405c251097037f72`.

Local evidence for this build:

- Release and LTO CTest: **13/13 each**, retained in
  `optimization-next-local-checks/`.
- `optimization-next-final-differential/receipt.json`: **16 exact comparisons**
  against the retained accelerated baseline and independent Fraction reference,
  including fine minimum-cost boundaries and all 540 configurations.
- `optimization-next-validate/receipt.json`: PASS, including **13 recovery and
  failure checks** and eager/on-demand financial equality.
- `optimization-next-python-regression/receipt.json`: **2,109 passed, 7 skipped**;
  the same three pre-existing failures remain (two legacy manual-codec fixtures
  and the real paired-totals fixture). Their assertions match the earlier
  baseline. The final runner changes additionally passed 64 focused tests,
  Ruff and mypy; this is not a claim that the full suite is green.
- `optimization-next-python-regression/actual-charts-final-r2/receipt.json`:
  actual C++ aggregate replay matches eager chart bins, exact financial results
  and hashes, preserves the original time window, and reuses the checked cache.

The three-repeat LTO probe used 64 real pairs and a small fixed workload;
replay medians were 0.270 s without LTO and 0.247 s with LTO. These short timings
select the candidate, not a reliable full-study speedup claim. Source-audit
probes with 4/8/16/32 readers favored 32 on the same retained-cache subset.
The full-roster settings batch in `optimization-next-benchmark/` passed all
36 members: 654,360 source rows each, including 286,901 fixed warmup rows and
367,459 measured rows. Exact pair and aggregate fingerprints match, and the
largest blocks filled at least twice in the measured portion. Selected settings
are 32 calculation threads, dynamic configuration scheduling, 16 MiB input blocks,
4,096 records per source reader, 4,096 timestamp groups and 32 audit readers.
Median replay was 6.794 s versus 7.887 s for static scheduling with the same
16 MiB/4,096 settings. A 16,384-record reader was within 5%, so the smaller
reader was retained. These are fixed short-workload comparisons, not full-history
forecasts. Environment notes record the research UI startup during repetition
zero and unrelated host work during the final repetition; the OS cache was
retained and the host was not isolated.

The prepared `optimization-next-chain/` applies the common 32 GiB output budget
across endurance, rehearsal and full stages by passing each stage the remaining
budget. No old artifacts are removed. New endurance, rehearsal and full-history
results must be reported from their actual receipts; the prepared chain alone
is not evidence that they ran or passed.

Timing limitations remain explicit: input decoding, exact price preparation and
timestamp merging share one combined timer; trade and metric CPU work is not
instrumented with per-event clocks. Big-integer input memory is estimated, while
the worker's actual RSS is checked independently. Artifact sizes are periodically
reconciled with the filesystem. Checkpoint publication still occurs at a common
safe boundary, with bounded parallel preparation and one ordered writer.

The first full-roster synthetic endurance exposed an additional measured cost:
an 18,516-sample user-space profile attributed about 54% to per-trade OpenSSL
digest-provider lookup, including shared locks. Reusing the context alone did
not avoid that lookup. The worker was deliberately interrupted directly and
saved an intact checkpoint after 107,023 rows; the chain stopped before rehearsal
or full history. Its failed wrapper receipt records the requested exit code 3,
not successful endurance. These artifacts remain in `optimization-next-chain/`.

The narrow follow-up binds SHA256 to each thread's context once, then resets
that context with a null algorithm argument; the digest bytes remain unchanged.
It also publishes RUNNING before the first block. The replacement executable is
`/opt/grid-builds/module1/research-sweep-on-demand-r2/grid_backtest`, SHA-256
`51804346020bd83ef10688bc3e76debbfa718ddc4a70393ccc5287df7fdc5e49`, with source
identity `55a50a338ee09b942c2decc2324b18cee238119d086bea702d0ca3e3b5e681b9`.
It passed CTest 13/13 and a fresh `optimization-next-r2-validate` including all
13 recovery checks. A separate 513-step chained binary-payload probe matches
both the former implementation and independent SHA256. The actual threaded
speedup still requires the replacement workload and profile.

The new `optimization-next-r2-{benchmark,calibrate,chain}` artifacts bind the
replacement identity. Earlier checkpoints are not resumed with it. The old
local Python suite remains applicable to unchanged Python code, while the
engine-dependent validation and performance gates are repeated for this binary.

The R2 full-roster settings batch passed all 36 members and chose the same
32/dynamic/16 MiB/4,096-reader settings. Median replay (including the identical
warmup) fell from 6.793593403 s to 3.127485718 s, or 2.172 times faster on this
654,360-row workload. `optimization-next-r2-comparison.json` binds both receipts:
all 2,587,680 canonical pair rows and aggregate/configuration hashes are exactly
identical. This is separate from the pending seven-day overall speed comparison.
The 14 bound implementation files are copied under the R2 executable's `source/`
directory, each checked against the validated study manifest.

The R2 dense synthetic profile confirms that provider lookup and its shared locks
are gone: trade hashing accounts for about 0.8% of samples. Its first block now
spends about 91% in intermediate rational normalization while preparing BUY
levels. This is a deliberately dense synthetic workload, not a whole-history
profile. R2 was explicitly stopped directly at the C++ worker and published its
fourth checkpoint after 3,526,936 rows and 464.871 seconds; its wrapper FAIL is
the expected diagnostic exit 3. It did not pass the 600-second gate or start
rehearsal/full history. All artifacts remain in `optimization-next-r2-chain/`.

The final narrow follow-up computes fixed-tick BUY rounding by exact integer
product and floor division, avoiding normalization of two unused intermediate
fractions. It normalizes the final order price once. Conservative bit-size
checks fall back to the old calculation on local copies; neither the 131,072-bit
bound nor immutable shared operands are relaxed. Significant-digit Bitfinex
rounding remains on its previous path.

The R3 executable is
`/opt/grid-builds/module1/research-sweep-on-demand-r3/grid_backtest`, SHA-256
`7612a717178a2f6d01b19c3c339cd0c777fb317b8ce31ae63c578df9126070a1`, with source
identity `c9849829d404d98c74f50da62cb03dc0633c2939113d5f7fd583632b04157a62`.
It passes CTest 13/13 and the focused integer tests under ASan/UBSan. These include
all 20 steps and 184 levels, tick neighbors/equality, native-to-big promotion,
valid large-intermediate fallback, genuine bound refusal and concurrent reads.
`optimization-next-r3-validate` passes all six primary checks and 13 recovery/
failure checks. `optimization-next-r3-rounding-differential.json` records exact
R2/R3 equality for the six retained fixtures, including all 540 configurations,
charts, extrema dates and trade hashes. Fourteen bound source files are retained
beside the binary. Fresh benchmark, calibration and chain evidence use the
`optimization-next-r3-` prefix; prior checkpoints are never resumed by this build.

The R3 full-roster benchmark passes all 36 members with the same 654,360 rows
and exact 2,587,680 pair-row fingerprint as R1/R2. Its selected median replay is
2.753052884 seconds (R2: 3.127485718; R1: 6.793593403), with 32 dynamic workers,
16 MiB blocks, 4,096 source-buffer records and 32 audit readers. These are short
warm-cache measurements on a shared host; they do not establish full-history
throughput. `optimization-next-r3-comparison.json` binds the three receipts.
Fresh short calibration passes, and R3 is installed atomically at the UI's
existing default executable path. The R3 chain now requires the saved exact
seven-day baseline comparison, including full pair-file hashes, before starting
the full-history stage. Nine offline driver checks cover this additional gate
and the cumulative output budget. Current stage status remains in
`optimization-next-r3-chain/progress.json`; a running stage is not a completed
research result.

The R3 endurance profile (`optimization-next-r3-chain/profile/analysis.json`)
contains 18,607 samples at 49 Hz over 12 seconds, with no lost samples and about
31.6 active CPUs. Provider-fetch/shared-lock symbols are absent. It samples
blocks 7–14; the older profiles sampled the first block, so their percentage
breakdowns must not be treated as matched speed measurements. Canonical trade
rendering/hashing and exact accounting now account for most sampled work.
The separately retained first-block comparison verifies equal normalized plans,
all declared source hashes/rules, 540 configurations and the identical 107,023-row
boundary: R1 strategy time was 223.845755780 seconds and R3 11.965716533 seconds.
The observed 18.707-fold reduction applies only to that dense synthetic block,
not to the real-history study. Both profiles and failed/interrupted prior runs
remain intact.

R3 endurance is complete: 4,792 sources × 540 configurations, 28,748,994
synthetic records, 607.831857389 seconds of replay and five published checkpoints.
Engine wall time was 615.133313834 seconds; the wrapper including fixture
generation took 669.674 seconds. Checkpoint work totaled 35.651 seconds, and the
reported final worker RSS was about 3.18 GiB. All 13 fresh recovery checks remain
bound through the validation receipt. This passes the amended ten-minute gate,
not the superseded one-hour test. The chain proceeded to the real seven-day
rehearsal using the same selected settings.

The R3 real seven-day rehearsal and its independent comparison now pass.
All 45,459,351 window records were processed for 4,792 pairs and 540
configurations. Every aggregate/venue financial value, extrema date, counter
and trade hash matches the preserved `optimization-final-rehearsal` baseline.
The 2,587,680 canonical pair rows are byte-identical (1,867,291,464 bytes,
SHA-256 `b50c46c397beacdfd55c4c3f26eaff4746f76cba90f92d518d3ada3167a3c537`).
The saved proof is `optimization-next-r3-chain/rehearsal-comparison.json`.

Measured replay time fell from 448.127 to 137.506 seconds (3.259 times faster),
engine wall time from 1,099.486 to 200.758 seconds (5.477 times faster), and
wrapper wall time from 1,104.228 to 202.531 seconds (5.452 times faster).
Initial full-source audit took 28.875 seconds and final full-hash verification
25.346 seconds; final output took about 7.899 seconds. The former 6,848,313,916
bytes of eager chart series are absent. These measurements use the same history
and exact outputs on the existing shared host; they are not cold-cache or
isolated-device benchmarks. Separate fixture tests establish eager/deferred
graph equality; the seven-day comparison checks financial results, not graphs.

After that comparison passed, the chain started a fresh full-history run at
`optimization-next-r3-chain/full/`: 4,173,223,584 source records, 4,792 pairs,
seven venues and 540 configurations. Its plan has no window, row or duration
cutoff, uses 32 dynamic calculation workers and on-demand charts, and requests
checkpoints every 900 seconds. It retains the common chain output budget and
resource guards. Full replay completed with all 4,173,223,584 records and all
2,587,680 pair/configuration result rows. The independent audit rehashed every
source and reported `VERIFIED`; the additional publication gate also reported
`VERIFIED`. The full wrapper took 10,734.492 seconds (2 h 58 min 54 s), followed
by 179.856 seconds of independent verification. Full-stage start to verified
publication was about 3 h 1 min 55 s, ending on 2026-11-19 at 23:19:29 Moscow
time. Eleven checkpoints were published. Final worker RSS was about 3.17 GiB;
this is the final observation, not a recorded peak. Full-stage engine artifacts
occupy 5,973,026,166 bytes; the complete new chain remains within its 32 GiB
budget. No resource-limit or duration-stop status was reported.

The additional publication gate ran as
`grid-research-sweep-next-r3-publication-20261119.service`. It waited for that
owned chain without a deadline and compared the independent auditor's pair-file
SHA and row count with the engine fingerprint, also checking byte count and
summary/executable/manifest bindings. This closes a gap where the auditor
calculated the pair SHA but did not compare it with the engine's saved value.
The gate reads metadata and small identity files only; it does not reread the
pair file or historical sources and does not alter the frozen running build.
Its 16 offline success/failure checks pass, including owner exit/PID reuse,
missing reports, changed identities and hash/count mismatches. Waiting status
is `optimization-next-r3-chain/final-publication-progress.json`; the final
completion proof is `optimization-next-r3-chain/final-publication.json` with
status `VERIFIED`. The internal chain status alone is not that additional proof.
The final pair-file SHA-256 is
`a70279d4ff7b191d81c4c884ddaea68a013874db692cf1fbe1034567fc55da54`,
with 2,587,680 rows and 1,893,806,220 bytes, matching both engine and independent
auditor. Both owned systemd processes exited successfully. The numerical
results remain an assumption-based research simulation; on-demand chart series
have not been generated for all configurations.

## Expanded exchange/global TP matrix (2026-11-20)

The new `aggregate_tp_v1` research mode implements the seven completed owner
decisions. It preserves the source roster, source bytes, minimum-buy rules,
per-pair TP, strict ordinary order crossings and exact 0.2% fees of the original
study. It changes only the new research configuration and aggregate exits.

The full Cartesian product has 20 grid steps, 27 per-pair TP values, 28 exchange
TP choices and 28 global TP choices: **423,360 configurations**. Each aggregate
axis contains disabled plus 2–15% in 0.5 percentage-point increments. Within a
configuration, one exchange percentage applies independently to every venue.
Configuration order is grid step, pair TP, exchange TP, global TP, with disabled
before enabled values. The off/off members keep their original `sXX-tXX` IDs;
other members add the two aggregate-axis identifiers. No profitability ranking
or candidate pruning is applied.

At each common O/H/L/C point, ordinary pair buys and sells finish first. The
engine observes the lifetime metrics and attained peak simultaneous `spent` of
each current scope cycle before checking aggregate exits. Global TP has priority;
only when it does not trigger are individual exchange TPs evaluated. Equality
triggers these aggregate exits; ordinary pair BUY/SELL crossings remain strict.

The aggregate target uses that cycle's net realized result, marked open result,
paid fees and the estimated fee for closing every remaining position, relative
to the cycle's attained peak `spent`. It never uses a future capital maximum or
current `spent` as the denominator. For example, a 1,000 USDT cycle peak and
100 USDT after all closing fees meet a 10% target even if only 600 USDT is still
invested. A cycle with zero attained peak cannot trigger. The comparison uses
exact scaled integers and cross multiplication; it does not divide fractions
or rescan the portfolio at every point.

An aggregate exit sells every open position in its scope immediately at its
last known exact source price, including a stale quote or a pair whose history
has already ended. This is an explicit new research assumption. Such a trade
records both its execution time and `quote_timestamp_ms`; it does not make the
quote fresh. Its fee is charged once. Ordinary sales in the same point are
already accounted for and cannot be sold again by a higher scope.

After an exchange exit, only that exchange's cycle baselines and peak reset;
the global cycle retains its accumulated result. A global exit resets all scope
cycles. Every pair in the exited scope, including an already empty pair, waits
for its next actual observation and then buys a fresh minimum lot at that price.
The next O/H/L/C phase qualifies if that pair has an observation there. A pair
with no later observation cannot rebuy from its retained quote. Lifetime totals,
fees, peaks, drawdowns, dates and trade hashes never reset. Completed-cycle
counts increase only for cycles which previously had nonzero peak `spent`;
resetting a venue which has not started trading does not invent a completed
trading cycle. Scope baselines and pending pair reseeds are checkpointed.

### Bounded execution and interfaces

The expanded CLI actions are `matrix-prepare`, `matrix-validate`,
`matrix-calibrate`, `matrix-endurance`, `matrix-rehearsal`, `matrix-full`,
`matrix-resume` and `matrix-chart`. They use the existing research worker and
heavy-job lock. The research UI code supports the same action family and
bounded result pages; deployment status is separate from these local changes.
The separate default executable is
`/opt/grid-builds/module1/research-sweep-monthly-v5/grid_backtest`; the earlier
`research-sweep-tp-v2` executable,
540-configuration workflow and retained results remain separate.

For example, preparation over the existing admitted seven-venue roster is:

```sh
/opt/grid-tools/module1-py312/bin/python -m scripts.research_sweep matrix-prepare \
  --source-output /opt/grid-runs/research-sweep-20261119/filtered-selection-final-20261119 \
  --output /opt/grid-runs/tp-matrix-prepare \
  --threads 32 --scheduler dynamic --block-bytes 16777216 \
  --reader-buffer-records 4096 --audit-threads 32 --batch-size 1024 \
  --numa-interleave-nodes 1,5
```

The NUMA option explicitly interleaves this worker's memory over the selected
allowed memory nodes. It is disabled by default for portable execution. It does
not set CPU affinity or change the host policy; the node list and launcher hash
are bound to the study and checkpoint compatibility.

This is a command example, not evidence that those stages have run. Subsequent
stages receive a fresh output directory and the preceding job through
`--source-output`. Matching validation, calibration, endurance and rehearsal
receipts are required before `matrix-full`. Settings, code, executable, rule
snapshot and source manifest are bound by `matrix-study.json`; old 540-stage
receipts cannot satisfy the expanded study's gates.

The default batch contains **1,024 configurations**, giving 414 batches for the
complete matrix. The CLI accepts 1–8,192 configurations per batch subject to the
existing memory limits; it never places all 423,360 pair-state sets in RAM.
Inside a batch, 32 persistent calculation workers own configurations and share
two bounded immutable source buffers. `matrix_batch` is a distinct C++ mode:
it requires all seven venues and full source histories and rejects history
windows, record caps and duration stops. The Python rehearsal explicitly uses
a diagnostic seven-calendar-day window instead.

Calibration runs three short repetitions over up to 64 venue-balanced sources,
at most 65,536 records, and **540 deterministic representative configurations**.
Those representatives cover every original grid-step/pair-TP combination and
every value on both new axes; they are not all 423,360 interactions. The current
calibration retains the requested technical settings and does not claim an
unmeasured optimum. The owner subsequently required the load stage to process
**all 423,360 configurations**, superseding its earlier 540-representative load
policy. The `complete-matrix-600s-v2` gate runs every configuration in bounded
batches on the same complete deterministic synthetic price history (96 bars
per source before declared gaps), using every admitted pair ID, venue and rule.
It requires at least 600 seconds of summed observed complete-batch replay time,
two durable checkpoints and exact uninterrupted/resumed equivalence of a full
batch. Recovery-probe time is excluded from the 600 seconds. It does not demand
600 seconds for each of the 414 batches. Counts, batch intervals, record coverage,
source identity and compressed result checksums are verified before acceptance.
Old 540-representative load receipts cannot satisfy this policy. Historical
rehearsal and full replay also process **all 423,360 configurations**. No hourly
endurance pass is implied by this minimum ten-minute gate.

Each batch commits its results and receipt in one SQLite transaction. Only
newly generated, marked scratch belonging to that successfully committed batch
is removed after payload readback. Failed or uncommitted batch artifacts remain
available. Prior study outputs and original datasets are never cleanup targets.
An explicit resume copies the verified committed catalog and resumes the
compatible unfinished batch from a saved checkpoint. It does not rerun committed
configurations or resume automatically after reboot. The same RSS, available
RAM, free disk and artifact limits apply; the current 64 GiB artifact limit covers the
current job's catalog and scratch, not every retained earlier research job.

### Binary results and details on demand

`results.sqlite` stores an indexed row per configuration, containing financial
totals for the portfolio and every venue, scope-cycle state, extrema and dates,
trade hashes and the per-configuration fingerprint of every pair result. It
does not retain billions of detailed pair rows. Result pages are limited to
200 configurations; Python does not load the full matrix into one JSON object.

The payload uses positional MessagePack fields with zlib level 3 compression.
Exact monetary values remain canonical rational strings inside the binary
payload; no conversion to floating point or rounding is introduced. Trade
digests use 32 raw bytes. SQLite supplies the ordinal/configuration indexes and
atomic batch commit. A SHA-256 of each uncompressed payload detects corruption;
decoding has a fixed per-configuration bound. Publication verifies complete
ordinal/configuration coverage and committed batch receipts before marking the
catalog complete.

A storage-only test on the **540 real financial summaries from the completed
original full run** is retained in
[`tp-codec-kmae316c/measurement.json`](/opt/grid-runs/tp-codec-kmae316c/measurement.json):

| Representation | Bytes |
|---|---:|
| Canonical JSON | 3,346,546 |
| Positional MessagePack before compression | 2,274,209 |
| Compressed payloads, zlib level 3 | 1,076,960 |
| Complete SQLite file in this test | 1,728,512 |

Every value survived exact encode/decode comparison; the measured combined
encode/decode time was about 0.081 seconds. Three compression-only repetitions
also compared levels 1, 3 and 6. This test used the old financial results with
aggregate fields explicitly null. Linear expansion at those same row sizes
would occupy about 1.36 GB of SQLite storage for 423,360 rows; it is only a size
estimate. New aggregate-cycle fields and different result magnitudes can change
the actual size. This measurement neither ran the new strategies nor measures
their historical runtime.

`matrix-chart --config-id ID --scope all|venue:VENUE|pair:SOURCE_ID` reconstructs
one selected configuration over the original complete source roster and its
original historical window. Even a requested single-pair graph needs the whole
roster because its exits depend on the exchange and portfolio. The replay must
match every configuration financial result, cycle state, trade hash and pair
fingerprint before returning details. Selected-pair series include aggregate
exit effects at their actual simulation times; the detailed pair result retains
its last quote date separately. Verified generated graphs and details are
cached for repeated views.

### Validation and remaining performance limits

The C++ focused tests cover exact equality after all fees, global-over-venue
priority, immediate stale/EOF closing, delayed reseeding, venue-only cycle reset,
checkpoint restoration, compact fingerprints, full-roster selected-pair graphs
and admission limits. Release/LTO CTest passes 13/13. The retained
[`tp-offoff-parity-pulhrkw2/verification.json`](/opt/grid-runs/tp-offoff-parity-pulhrkw2/verification.json)
compares all 540 original configurations on eight short synthetic sources
covering seven venues: the old R3 executable, the new legacy mode and the new
matrix off/off mode produce identical financial values, all 4,320 pair rows and
trade hashes. This is a short correctness comparison, not a full-history speed
measurement.

Fresh CLI preparation and validation are saved under
[`tp-matrix-accepted-__ydp2mt`](/opt/grid-runs/tp-matrix-accepted-__ydp2mt/validate/receipt.json).
The accepted roster contains 4,792 sources and the manifest declares all 423,360
configurations. The independent Fraction check covers eight synthetic sources
and eight configurations: 64 pair results and 24,536 trades match at one and
32 threads and after checkpoint continuation. Compact fingerprints and binary
round trips preserve every compared financial value. The focused Python research
sweep suite passes 195 tests; Ruff, formatting, and strict targeted mypy pass.
The [complete Python suite](/opt/grid-runs/tp-full-python-suite-y9px1a38/result.json)
reports 2,132 passed, seven skipped and the same three pre-existing failures:
the two manual-codec historical byte-hash expectations and the paired-totals
USD/BTC-versus-USDT fixture expectation. The full suite is not labelled green.

The [short real-source calibration](/opt/grid-runs/tp-matrix-accepted-__ydp2mt/calibrate/receipt.json)
used 64 sources, 540 representative configurations and 65,504 records per repeat,
with 32 dynamic workers, 16 MiB blocks, 4,096-record readers and 32 audit readers.
All three result fingerprints match. Median engine replay time was 0.271 seconds;
median wrapper time was 1.005 seconds. This is a small warm-cache integration
measurement, not a reliable full-history runtime estimate or proof of an optimal
thread/buffer setting. It retains the requested settings without profit-based
selection.

The engine reuses maintained financial sums, skips aggregate trigger work when
disabled, and walks positions only when an aggregate exit actually occurs.
The common reader prepares venue-presence masks once; workers with graphs off
skip metric/trigger work for venues without an observation. Empty chart/trace
output passes are skipped. These optimizations preserve eager chart observations
and all original off/off values. No precision relaxation or `fast-math` is used.

A material finalization cost remains: to retain an exact replay-verifiable
pair fingerprint, C++ still renders and hashes canonical pair rows even when it
does not save those rows. Across the full expanded matrix this covers about
2.03 billion rows. Binary storage removes their retained disk cost but does not
remove that computation. Full source identity audits also remain before and
after each batch. The expanded strategy runtime and resource peaks have not
been measured on complete history; no old 540-run acceleration factor is claimed
for the new workload. At this documentation update, the expanded 600-second
endurance, all-configuration seven-day rehearsal and full-history run have not
been performed. Their gates remain required before new complete results can be
published.

## Performance follow-up: aggregate matrix, 2026-11-20

The owner requested a complete performance investigation and explicitly excluded
further UI work. This follow-up changes the research calculation, CLI orchestration
and local validation helpers. Baseline executable/source copies, profiles and
every measurement are retained under
[`tp-perf-opt-5b8crt0c`](/opt/grid-runs/tp-perf-opt-5b8crt0c).
Original histories, snapshots, old runs and trading rules are retained.

The investigation covered source reads and verification, timestamp merging,
price-only accounting, ordinary/aggregate trades, numeric conversion, scheduling,
report generation, binary storage and checkpoint save/restore. Two different
profiles matter: ordinary historical observations spend most CPU on price and
metric updates, while a volatile synthetic stream spends much more on orders,
exact grid arithmetic and trade text. Increasing input buffering cannot remove
those computation costs. A full-roster probe used about 31.5 worker CPUs with
roughly 1.2% of worker capacity waiting at block boundaries.

The implemented changes are:

- Cache the trade-dependent cash basis and the current drawdown threshold.
  Price observations retain exact valuations and first extrema dates while
  avoiding repeated subtraction of unchanged financial values.
- Cache the aggregate-cycle baseline contribution to its TP threshold. Preserve
  inclusive crossing, exit fees, ordinary/global/venue ordering and all resets.
- Keep checked native integer paths small enough for compiler inlining; retain
  the same overflow promotion, exact division and bounded BIGNUM fallback.
- Prepare fixed-tick product components once per shared grid step/precision.
  A bounded per-worker cache also reuses exact levels and strict float32 cutoffs
  for repeated anchors. Only native canonical prices enter that cache; wide
  values and significant-digit rules retain the exact fallback.
- Render native integers in decimal chunks and native Rational components
  directly, avoiding BIGNUM materialization and temporary strings. Reuse bounded
  immutable numeric trade text across equal monetary-scale/amount-tick groups.
  Config/source identity, timestamps, sequence and both hashes are still produced
  for every actual trade. Cached and uncached results are compared explicitly.
- Serialize canonical pair rows directly into reusable buffers. In compact matrix
  runs, each worker hashes its own exact pair bytes. The summary now binds their
  ordered metadata with `pairs_fingerprints_sha256` and
  `pairs_hash_scope=per_configuration`; it does not invent `pairs_sha256` for an
  absent combined file. Individual pair fingerprints and all trade hashes retain
  their original bytes. Retained pair files still have their full SHA-256.
- Save only independent checkpoint financial state, reconstructing derived
  values on restore. Restore and verify configurations in bounded groups on the
  existing worker pool; preserve ordered checkpoint/prefix hashes, source checks
  and previous-generation fallback. Restore progress and elapsed time are exposed
  through CLI artifacts.
- Wait for child completion instead of adding a fixed half-second delay. Matrix
  workers read the saved summary and suppress its duplicate stdout serialization.
- Generate the synthetic load histories from their 48 immutable byte templates,
  bounded by 64 MiB. Each source still gets its own file, unchanged raw bytes,
  timestamps, gaps and SHA-256. This changes fixture preparation, not the
  independent Fraction calculation.

All caches are disposable, bounded and private to a calculation thread. None
contains shared mutable financial state or is required for recovery. Runtime
memory/disk floors, exact arithmetic, source admission and durable publication
remain enforced. Full histories and the full matrix's states are not loaded
into RAM. The final quantitative report and fresh admission receipts are stored
with this follow-up; short benchmark speedups are not a full-history duration
claim. Necessary exact valuation, per-trade hashes and source-integrity work
remain part of the calculation.

Fresh comparison uses three alternating repetitions with new states and warm
file caches. The final executable is
`b887867df28aa7c205ad673b27e5e9cc653b37ccbb1afcb211f1dc70eec87d6b`.
All exact configuration values, cycle states, pair fingerprints and trade hashes
match the retained pre-optimization executable in each measured workload.

| Work measured | Before, median | After, median | Ratio |
|---|---:|---:|---:|
| Strategy, 64 real-source prefixes, 1,006,077 bars × 540 configurations | 7.359 s | 4.616 s | 1.59× |
| Strategy, volatile synthetic stream, 64 markets × 540 configurations | 1.103 s | 0.461 s | 2.39× |
| Pair-result preparation, 4,792 markets × 540 configurations | 1.672 s | 0.144 s | 11.61× |

These rows measure their named components. Whole-process medians, input and
result fingerprints are retained in
[`final-measurements.json`](/opt/grid-runs/tp-perf-opt-5b8crt0c/final-measurements.json).
The complete synthetic-source preparation, including file writes, improved from
0.387 to 0.085 seconds on 512 sources × 2,048 bars; all bytes and metadata match.
An intermediate save-only comparison reduced checkpoint bytes by 16% and save
time by 19%; its exact conditions are in `checkpoint-measurements.json`.

The large recovery check used **1,024 configurations × 4,792 markets**. Each build
resumed its own checkpoint. The optimized continuation processed the same
129,386 records and reproduced all original exact results and trade hashes.
Its total continuation time was 23.63 seconds versus 68.67 seconds before;
the new restore stage itself took 14.00 seconds. Observed RSS was about 3.8 GiB.
See [the bound recovery receipt](/opt/grid-runs/tp-perf-opt-5b8crt0c/final-batch1024-verification.json).

Fresh validation passes 13 CTest tests, 196 focused Python tests, 13 independent
recovery/fault checks and two ASan/UBSan tests. The Fraction validation compares
one and 32 threads, checkpoint continuation, both derived caches disabled and a
wide-money-scale cache bypass.

The fresh full-roster load test completed **605.72 seconds of replay** on
4,792 synthetic markets and 540 representative configurations, processing
10,473,784 records and saving five checkpoints. Restore and continuation passed:
the restored process advanced to 10,580,806 records; restore itself took 8.07
seconds. This is the approved ten-minute test, not an hour-long soak or a replay
of all 423,360 configurations. The executable and every bound code file were
rechecked against the final validation/calibration receipts.

During strategy calculation, the load test used an average 30.98 worker CPUs;
block-boundary waiting was 2.40% of their capacity. Input decoding, preparation
and merging took 5.79 seconds, checkpoint saves 27.32 seconds (4.51% of replay),
and pair-result publication 0.13 seconds. Load/recovery RSS remained below 2.7
GiB, and available memory and disk stayed above their required floors. These
are observed synthetic-load timings, not an isolated throughput benchmark;
ASan/UBSan tests overlapped roughly its first 107 seconds.

The final CPU profile still contains exact order/grid arithmetic, valuations,
trade hashing and object allocation. Those costs are not a claim that every
possible future optimization has been exhausted. Full input histories remain
streamed and the full expanded historical matrix has not started.
The completed [performance report](/opt/grid-runs/tp-perf-opt-5b8crt0c/PERFORMANCE_REPORT.json)
and [load/recovery receipt](/opt/grid-runs/tp-perf-opt-5b8crt0c/acceptance-final/endurance/receipt.json)
retain the measured scope, resource observations and compatible identities.

### Проверка точности и отбор следующих ускорений

В этой серии рассмотрены 40 предложений. Из десяти реализованных пробных изменений в рабочем варианте оставлены четыре: короткий путь переоценки без сделки, заранее вычисляемый точный порог общего TP, создание текста ошибки только при отказе и одно последовательное обновление SHA вместо двух. Для проверки диапазона целых сохранён вспомогательный метод определения их разрядности. Дополнительно выбран явный режим распределения памяти между NUMA-узлами 1 и 5 этого сервера; переносимый режим запуска по умолчанию сохранён. Новые исследования допускают до 64 GiB артефактов; предел RSS 64 GiB и минимальные свободные память и диск по 32 GiB сохраняются. Старые планы и контрольные точки не переписаны; лимиты новых исследований входят в их идентификатор.

Безопасность изменения и полезность для скорости проверялись отдельно. Для каждого пробного этапа выполнялись три чередующихся запуска предыдущей и следующей сборки с одинаковыми входами и свежим состоянием. Во всех этих сравнениях совпали все поля результатов конфигураций, включая точные деньги, даты экстремумов, состояния циклов, хеши сделок и контрольные отпечатки результатов пар. Совпадение с предыдущей сборкой дополняется независимым Fraction-эталоном и проверками восстановления; одна только одинаковая прибыль недостаточна.

В таблицах коэффициент больше 1 означает ускорение расчёта, меньше 1 — замедление. Это отношение медианного времени предыдущей сборки к новой. Например, 1,17× означает примерно на 17% больше работы за секунду, а сокращение времени — примерно на 14,5%. Коэффициенты отдельных этапов нельзя перемножать: это измерения соседних накопительных вариантов, а итоговый выбранный набор проверялся отдельно.

| Проверенное изменение | Условие сохранения результата | Реальные 64 пары / частые сделки на 64 парах | Решение |
|---|---|---:|---|
| Короткий путь цены без сделки | Вход уже состоялся, нет пересечения BUY/SELL, количество и денежное основание неизменны. При движении цены сохраняются переоценка, длительность удержания и первые даты экстремумов | 1,085× / 1,012× | Оставлено |
| Точный порог общего TP | Граница пересчитывается после сделки, нового пика `spent`, сброса и восстановления. Срабатывание при равенстве и порядок глобального/биржевого выхода сохраняются | 1,092× / 0,992× | Оставлено |
| Повторное использование коэффициентов денежных масштабов | Повторно используется только результат умножения на точно одинаковые коэффициенты; единичный коэффициент проверяется точно | 0,990× / 0,994× | Удалено из выбранного варианта: выигрыша нет |
| Отложенное создание строки ошибки | Все проверки и причины отказа остаются; строка создаётся только при отрицательном результате проверки | 1,028× / 1,175× | Оставлено |
| Постоянный точный множитель глубины 0,4 | Значение равно прежнему; объект отдельный для каждого потока, без совместного изменяемого состояния | 1,012× / 1,043× | Не оставлено: преимущество меньше принятого порога 5% |
| Запись времени и номера сделки прямо в строку | Десятичные байты знаковых/беззнаковых чисел должны полностью совпадать с прежними | 1,019× / 0,983× | Не оставлено: нет устойчивого выигрыша |
| Передача уже вычисленных стоимости и комиссии общей продажи | Используются ровно значения той же сделки, цены и денежного масштаба | 0,933× / 1,009× | Не оставлено: замедление реального участка |
| Одно обновление SHA для предыдущего хеша и JSON | Обе цепочки хешей сохраняются; в SHA поступают прежние байты в прежнем порядке; trace исключает служебный префикс | 1,055× / 1,034× | Оставлено |
| Прямой расчёт фиксированного SELL в тиках | Только точная арифметика и прежнее округление вверх; значащие цифры Bitfinex остаются на прежнем пути; большие промежуточные значения переходят к прежнему расчёту | 0,939× / 1,000× | Не оставлено. Дополнительное сравнение на 4 792 парах также показало замедление |
| 64-битный алгоритм сокращения подходящих дробей | Применяется только при попадании обоих целых в диапазон, иначе прежний алгоритм | Около 1,000× / 1,007×; первый реальный замер имел пересечение с компиляцией и не принят как доказательство выигрыша | Не оставлено: полезного ускорения бэктеста не показано |

Прямой SELL ускорил изолированный числовой микротест в 8,72 раза, а сокращение дробей — в 2,04 раза. Это не улучшило весь бэктест. Отдельное сравнение выбранной сборки с добавлением прямого SELL на 4 792 парах × 1 024 конфигурации дало 5,640 → 6,519 секунды расчёта, то есть 0,865× и увеличение времени примерно на 15,6%. Финансовые результаты совпали, но такое изменение не включено.

Для нового порога TP дополнительно выполнено 36 550 прямых сравнений с прежним точным условием. Они покрыли 12 000 обращений к уже вычисленной границе, знаковые денежные основания, нулевой и отрицательный порог, равенство и соседние значения, восстановление поверх устаревшего кэша и значения у предела 131 072 бит. В 103 случаях прежний алгоритм отказывал по числовому пределу; новый отказал так же, включая текст ошибки. Это проверка фактического внутреннего `Cycle` без добавления тестового API в производственный код.

Выбранные четыре изменения отдельно сопоставлены с исходной сборкой v3:

| Одинаковая работа | Было / стало: расчёт | Ускорение расчёта | Было / стало: весь процесс |
|---|---:|---:|---:|
| Реальные 64 пары, 1 006 077 записей, 540 конфигураций | 4,424 / 3,780 с | 1,170× | 4,971 / 4,257 с |
| Частые сделки: 64 синтетические пары, 119 205 записей, 540 конфигураций | 7,421 / 6,316 с | 1,175× | 7,803 / 6,703 с |
| Реальные 4 792 пары всех семи бирж, 1 044 187 записей, 1 024 распределённые конфигурации | 7,223 / 6,648 с | 1,087× | 10,919 / 10,210 с |

Последний случай обработал восемь полностью заполненных блоков по 16 MiB, включал фиксированный предварительный участок и оба режима точности цены. Состояния всех конфигураций совпали. Это короткие замеры с тёплым файловым кэшем; они не устанавливают скорость полной истории всех 423 360 конфигураций. Сравнения PGO и машинного кода `znver3` завершены: для полного состава сохранена переносимая сборка. Технические сравнения завершены: сохранены 32 потока, динамическая очередь, блоки 16 MiB, исходные буферы по 4 096 записей, 32 потока аудита и пакеты по 1 024 конфигурации. Выбрано явное распределение памяти по NUMA-узлам 1 и 5. Два варианта расположения полей и увеличение пакета отклонены по результатам измерений.

Остальные 30 предложений:

| Предложение | Что требуется для безопасности | Эффект и решение на этом этапе |
|---|---|---|
| Отдельная оптимизация точности Bitfinex | Проверить переходы значащих цифр вверх и вниз, соседние float32, прежние цены BUY/SELL и хеши | Отдельного замера нет; прежний точный путь сохранён |
| Кэш минимального лота | Ключ включает цену, все правила количества/стоимости и масштаб; большие значения проверяются как прежде | Выигрыш неизвестен; не добавлен без подтверждённой необходимости |
| Размер и включение существующих кэшей | Кэш — только производные данные; выключение не меняет результаты и восстановление | Новое отключение обоих кэшей дало 0,9996× на полном составе, результаты совпали; существующие кэши сохранены |
| Параллельная обработка независимых цепочек SHA | Не менять зависимости внутри одной цепочки и порядок байтов каждой сделки | Выигрыш неизвестен; отдельная реализация не добавлена |
| Размещение памяти по NUMA | Не менять владельца состояния и порядок событий; отдельно проверить задержки и RSS | Чередование памяти между узлами 1 и 5 дало 1,387× расчёта и 1,149× всего процесса. Результаты трёх повторов совпали; добавлен явный параметр с проверкой узлов и хеша launcher. Переносимый режим по умолчанию сохранён |
| Изменение расположения полей состояния | Два подготовленных варианта меняют только порядок объявления полей, сохраняя вычисления и сериализацию; нужны те же проверки результатов и ASan/UBSan | Оба варианта дали точное совпадение; скорость 0,939× и 1,008× на 4 792 парах. Отклонены: замедление либо выигрыш меньше 5% |
| Более 32 расчётных потоков | Сохранять единственного владельца каждой конфигурации и ограниченные очереди | 32 физических ядра уже используются почти полностью; новые потоки без доказанного выигрыша не добавлены |
| Увеличение пакета конфигураций | Одна конфигурация должна дать прежний результат при любом пакете; учитывать три поколения контрольной точки и весь каталог | Один пакет 2 048 вместо двух по 1 024: 1,039× расчёта и 1,048× всего процесса, RSS 3,71 → 7,25 GB. Результаты совпали; оставлены 1 024, поскольку выигрыш меньше 5%, а память почти удвоилась |
| Бинарные или сжатые контрольные точки | Точно сохранять целые, масштабы, большие числа и порядок вывода; проверить повреждение, нехватку диска и атомарную публикацию | Численного выигрыша нет; формат не меняется до измерения реального ограничения |
| Повторное использование аудита источников | Повторно использовать только доказанную неизменность; размер и дата файла не заменяют полный SHA | На прежнем полном прогоне аудиты занимали около 0,4% времени расчёта; обязательные проверки сохранены |
| SHA источника одновременно с чтением | Должен быть проверен каждый исходный байт, включая пропущенные начальные и конечные участки | Для оконного чтения это не автоматическая замена полного аудита; отдельного выигрыша нет, не внедрено |
| Переработка Python-каталога и обёртки | Сохранить точные строки денег, контрольные суммы, транзакции и возобновление без дублей | Ранее упаковка 540 строк занимала 0,044 с, распаковка 0,028 с; новой переработки без существенного выигрыша нет |
| Сборка PGO | Профиль готовится на отдельной нагрузке; те же точные флаги, проверка всех результатов и аварийных путей | 1,390× на реальных 64 парах, 1,178× при частых сделках, но 0,987× на 4 792 парах; не выбрана для полного состава |
| SIMD | Сохранять точные сравнения, строгие/нестрогие границы, проверку переполнения и порядок событий | Выигрыш неизвестен; новый вычислительный путь не добавлен |
| Совместный расчёт одинакового начала траекторий | До разделения совпадает всё состояние; при разных TP разделяются сетки, деньги, циклы, даты и собственные хеши | Высокая сложность доказательства, измерений нет; не внедрено |
| GPU | Нужен эквивалент точных широких целых и проверки очередей/передачи состояния | Подходящий вычислительный GPU здесь не установлен; ускорение не измерено и не реализовано |
| Несколько машин | Одинаковые источники/сборки и независимые пакеты, защита от повторной публикации | Дополнительных машин в этой работе нет; выигрыш неизвестен |
| Деньги в float/double либо дополнительное округление | Такое изменение не сохраняет точные пороги и финансовые значения | Отклонено независимо от скорости |
| Пропуск цен или timestamps | Может пропустить покупку, продажу или экстремум и изменить дальнейшую историю | Отклонено как изменение результата |
| Более редкая проверка общего TP | Меняет первую точку выхода и последующие входы | Отклонено как изменение торговой логики |
| Сокращение матрицы или истории | Уменьшает заявленный охват исследования | Отклонено; это сокращение работы, а не эквивалентное ускорение |
| Экстремумы только по точкам графика | Может скрыть пик вложений, просадку и дату первого достижения | Отклонено как потеря качества итоговых метрик |
| Пропуск общих показателей при неизменном контуре | Нужны точные признаки изменения; сделки и выходы учитываются даже при взаимном сокращении дельт; время и графики продолжаются | Отдельного выигрыша нет; после выбранных ускорений не добавлено |
| Кэш денежных значений сделки вместе с её текстом | Полный ключ количества, цены и масштаба, прежняя проверка больших промежуточных значений | Дополнительная память не обоснована текущими попаданиями кэша; не внедрено |
| Специальное деление комиссии на 500 | Сохранить требование нулевого остатка и все числовые пределы | Отдельно не измерено; константа уже видна компилятору в обычном числовом пути |
| Размеры исходных и общих буферов | Не менять порядок и полноту записей; учитывать фактическую память декодированных данных и безопасные границы остановки | Буфер чтения 16 384 дал только 1,009× против 4 096; оставлен 4 096. Общие блоки 1/4/32/64 MiB дали 0,203× / 0,556× / 0,948× / 0,841× против 16 MiB; оставлены 16 MiB. Все результаты совпали |
| Число потоков аудита | Все исходные записи и SHA проверяются полностью независимо от параллельности | Во всех сравнениях явно заданы 32 проверяющих потока. Новая отдельная серия 4/8/16/32 не проводилась; прежние полные проверки сохранены |
| Статическое и динамическое распределение конфигураций | Конфигурация остаётся у одного работника во время обработки блока; порядок событий/вывода детерминирован | Статическое распределение дало 0,953× расчёта и 0,959× всего процесса; сохранена динамическая очередь |
| Машинный код под конкретный CPU | Те же точные операции; бинарный файл применяется только на совместимом процессоре, универсальная сборка сохраняется | `znver3`: 1,022× / 0,969× / 1,065× на реальных 64 парах, частых сделках и полном составе. Разброс повторов полного состава перекрывается, выигрыш неодинаков; сохранена переносимая сборка |
| Удаление одного или обоих хешей сделок | Ухудшает возможность независимой проверки и аудита восстановления | Отклонено независимо от возможного ускорения |

Профиль выбранной переносимой сборки на полном составе показал 1,27 инструкции за такт и 46,71% промахов относительно общего счётчика `cache-references`. Аппаратные события измерялись с мультиплексированием примерно 83,3% времени. Эти общие события нельзя автоматически называть промахами последнего уровня кэша или ожиданием DRAM. Два небольших варианта порядка полей были измерены и отклонены; новая архитектура состояния не вводилась.

Технические настройки сравнивались на одинаковых записях, по три чередующихся повтора с новым состоянием. Проверка больших общих буферов обработала минимум два заполненных блока крупнейшего размера; проверка исходных буферов — 3 889 247 записей, причём 59 из 64 источников содержали не менее двух буферов по 16 384 записи. Короткие источники сохранены. Для пакетов сравнивались одни и те же 2 048 конфигураций целиком и двумя последовательными частями; показатели всего процесса включают Python-обёртку.

Выбранный режим NUMA отдельно сократил расчёт с 6,668 до 4,808 с, а весь процесс с 10,160 до 8,841 с. Это дополнительное измерение поверх выбранного кода. Его коэффициент нельзя перемножать с предыдущими таблицами и выдавать за итоговый результат. Полностью собранный вариант сравнен отдельно, результаты приведены ниже.

Итоговая сборка v4 с выбранным NUMA-режимом проверена против сохранённой v3 в новой серии из трёх чередующихся повторов без одновременной компиляции или тестов. Исполняемый файл: `/opt/grid-builds/module1/research-sweep-tp-opt-v4/grid_backtest`, SHA-256 `7488bbf6d8cb985b827572242585721324c9dea36830bb771308391f57d6f23e`. Во всех повторах полностью совпали состояния конфигураций, точные финансовые числа, даты и хеши.

| Одинаковая работа | Расчёт v3 → v4 | Ускорение расчёта | Весь процесс v3 → v4 | Ускорение всего процесса |
|---|---:|---:|---:|---:|
| Реальные 64 пары × 540 конфигураций | 4,533 → 3,662 с | 1,238× | 4,966 → 4,178 с | 1,189× |
| 64 пары с частыми сделками × 540 конфигураций | 7,481 → 6,254 с | 1,196× | 7,858 → 6,649 с | 1,182× |
| Все 4 792 пары × 1 024 конфигурации, фиксированное окно | 7,280 → 4,805 с | 1,515× | 10,669 → 8,957 с | 1,191× |

На полном составе это примерно на 34,0% меньше времени расчёта и на 16,0% меньше времени всего процесса. Это измерение фиксированного окна; полная история всех 423 360 конфигураций здесь не выполнялась. [Данные итогового сравнения](/opt/grid-runs/tp-opt-v4-fxdqy_fb/measure-final-selected/measurements.json).

Финальная локальная проверка выбранной сборки прошла: 13 CTest, 215 целевых Python-тестов, Ruff, mypy, два теста ASan/UBSan, независимый Fraction-эталон, сравнение с сохранённой сборкой и 13 проверок отказов/восстановления. Прямые проверки порога TP повторены на финальном коде: 36 550 сравнений без расхождений.

Свежая последовательность CLI завершилась успешно: prepare — READY, validate и короткая calibrate — PASS; каждый процесс завершился с кодом 0. [Свидетельство CLI](/opt/grid-runs/tp-opt-v4-fxdqy_fb/final-cli-chain/chain.json).

Дополнительный независимый Fraction-эталон проверил 14 источников и 12 конфигураций: 168 результатов пар/конфигураций и 32 586 сделок в каждом из трёх вариантов исполнения, без расхождений. [Свидетельство эталона](/opt/grid-runs/tp-opt-v4-fxdqy_fb/extra-oracle-final/receipt.json).

Новая нагрузочная проверка, семидневная репетиция и полная расширенная история в эту проверку оптимизаций не входили; прежние условия допуска полного запуска сохраняются.

Источник результатов этой серии: `/opt/grid-runs/tp-opt-v4-fxdqy_fb/`. Таблицы десяти отдельных изменений находятся в `measure-*/measurements.json`, итоговый выбранный набор — в `measure-selected/measurements.json`, отрицательный результат SELL на полном составе — в `measure-selected-sell/measurements.json`. Полный список оценок — [40 предложений с доказательствами и решениями](/opt/grid-runs/tp-opt-v4-fxdqy_fb/candidate-decision-evidence.json). Финальные проверки относятся к указанному SHA-256 v4; результаты промежуточных сборок не подменяют эти свидетельства.

Восстановление дополнительно проверено на всех 4 792 источниках и 1 024 конфигурациях, с обработкой 1 613 346 записей. Непрерывный расчёт точно совпал с остановкой после первого блока, продолжением до второго и последующим завершением. Обе версии контрольной точки сохранены; повреждение последней в отдельной копии привело к успешному восстановлению предыдущей с теми же результатами. Проверены все состояния конфигураций, финансовые числа, даты и отпечатки пар/сделок. Максимальный наблюдённый RSS составил 4 117 319 680 байт (3,84 GiB), общий размер новых артефактов после проверки — 23 109 027 316 байт. Исходные файлы, исполняемый файл и привязанные версии кода не изменились. Подробности — [свидетельство полного состава при восстановлении](/opt/grid-runs/tp-opt-v4-fxdqy_fb/final-fullroster-recovery/receipt.json); это проверка ограниченного окна, без заявления о полном историческом прогоне матрицы.

### Monthly and final statistics for the complete TP matrix

The owner requested immediate monthly and final spent, PnL, profit factor and
Sharpe/Sortino for every configuration, separately for the portfolio and each
of the seven venues. These now belong to the primary binary catalog, under
`performance.total` and `performance.venues`, rather than a chart replay.
Each scope contains `overall` and a chronological `monthly` array. New plans
use `statistics_mode=monthly_v1`; the exact definition is bound in the study's
`statistics_policy`. Existing financial totals and trade hashes keep their
original meaning. Earlier study receipts/checkpoints cannot certify this build.

The accepted definitions are:

- UTC calendar months. Monthly `total_pnl` is the change in cumulative net
  marked PnL during that month, including paid fees and open-position marks.
  First/last partial months remain in financial totals and are flagged.
- `spent_open` and `spent` are opening and ending cost of open positions;
  `peak_spent` is the maximum simultaneous exposure within the month, with
  its first date. Overall spent is ending exposure, with the lifetime peak.
  Monthly spent values must not be summed as a capital requirement.
- Profit factor groups each completed pair position, including closures from
  venue/global exits, by its sale month. Each closed net result includes all
  buy fees of that position and its sell fee. Positive net results and the
  absolute negative results are accumulated separately. Open positions do
  not contribute until closed; an earlier month's buy fees remain earlier
  paid fees while their allocation to a later closure is retained for PF.
  No closures or no losses yields null plus an explicit reason, never infinity.
- The owner selected **monthly**, not daily, Sharpe/Sortino observations and
  confirmed cumulative ratios from history start in each monthly row. Only
  complete calendar months enter this sample. Overall ratios use the same
  complete-month sample for the whole run. A partial month does not enter it.
- Observations are monthly changes in net marked PnL. Dividing every observation
  by one fixed positive capital reference would cancel in these ratios, so no
  fictional funded account or changing spent denominator is introduced.
  Risk-free rate and target are zero; annualization uses 12 observations.
  Sharpe uses sample standard deviation. Sortino uses downside RMS over all
  observations, including zeros for nonnegative changes. Insufficient months,
  zero variance and absent downside have explicit unavailable statuses.
- Financial sums, the sample count, sums and squared sums, downside squared
  sums, PF and the squared risk ratios are exact. Only the displayed square
  root is truncated to 24 decimal places; the exact basis is also stored.
  None of these report values changes trading or TP decisions.

The statistical definitions were checked against
[Sharpe's original explanation](https://web.stanford.edu/~wfsharpe/art/sr/SR.htm)
and the [Sortino calculation explanation](https://www.cmegroup.com/education/files/rr-sortino-a-sharper-ratio.pdf).
The fixed-capital unfunded reporting convention and monthly cumulative window
are the owner's research choices, not a claim of funded NAV returns.

On price-only observations there is one calendar-boundary check. Monthly
exposure peaks are inspected only in scopes with a BUY at that common point;
closing-result accumulation runs only on SELL. Month boundaries carry the
existing last mark without inventing trades. No full daily series or trade log
is retained for these metrics. Completed month states are bounded per active
configuration batch and included in exact checkpoint recovery.

Report generation runs across the existing configuration workers. The binary
codec removes repeated monthly field names and stores arbitrary-size signed
integer numerators/denominators in variable-length bytes before compression.
It preserves the exact decoded result and existing catalogs remain readable.
Publication checks calendar/exposure continuity, sums of monthly money and
counts, closed-result PF and exact cumulative risk moments/ratios. Source
histories, the 423,360 configurations and the agreed trading assumptions remain
unchanged. The artifact cap is 64 GiB; existing RSS/free RAM/free disk bounds
remain active. No web-interface work is included.

The load-coverage correction is implemented under `complete-matrix-600s-v2`.
The superseded 540-configuration worker was stopped directly, retaining its
1,484,597,297-byte checkpoint and failed/interrupted receipt; it is not accepted
as evidence for the expanded load gate. New checks reject a sampled catalog,
missing configurations or batches, shortened source processing, changed source
rosters, and insufficient/nonfinite observed replay duration. A compiled-engine
test checks the two-checkpoint recovery path against uninterrupted monthly and
financial results. The focused Python suite passed **232 tests**, with Ruff
and mypy passing on the changed Python files. The C++ executable is unchanged
from the monthly-v5 build already covered by 13 release CTests and two sanitizer
checks. Fresh preparation, validation and calibration are bound to the amended
policy; the new complete-matrix load is running and real seven-day rehearsal is
queued behind its successful completion. Current state is recorded in
[the owned chain status](/opt/grid-runs/tp-monthly-v5-6_1xwrvi/complete-matrix-chain-status.json)
and [load progress](/opt/grid-runs/tp-monthly-v5-6_1xwrvi/complete-matrix-run/endurance/progress.json).
Neither a complete load nor the new weekly rehearsal is claimed yet.

### Selected 4,480 configurations and streaming binary results (2026-11-21)

The owner replaced the current study with exactly 4,480 combinations: grid
steps 1–10% by 1, pair TP 2–8% by 1, and independently off or 2–8% by 1 for
exchange and global TP. The depth remains **60%**. There are 3,430 combinations
with both common TP enabled, 490 exchange-only, 490 global-only and 70 controls
with both disabled. No axis or admitted source was removed implicitly. The
canonical configuration IDs are preserved; a separate study ordinal determines
the five batches: 1,024 / 1,024 / 1,024 / 1,024 / 384.

`--configuration-plan` reads a versioned explicit configuration list. Its
SHA-256, canonical ordinals and exact parameters belong to the immutable study
identity and all subsequent gates. The canonical 423,360-element matrix remains
available for compatibility; it no longer dictates a selected study's completion
count. `--artifact-root` permits the whole selected chain, including retained
recovery files, to share the 64 GiB artifact budget. The 64 GiB RSS and 32 GiB
free RAM/disk limits remain active during C++ and Python result processing.

The previous controller and worker were stopped directly, retaining the
2,949,182,013-byte checkpoint and original artifacts. The old executable and
relevant sources were copied before edits. New checkpoints are never restored
by a different executable. The new release executable SHA-256 is
`a188997ceb10985f45ff93e0c4be6113ec45c15602449174a48c718262aab133`;
the study SHA-256 is
`b67db023f08ed2f7223b8cd5564e10134610048a12220b97b3a8609e0b6b3d06`.

Production matrix plans now use `result_output=framed_msgpack_v1`. C++ prepares
at most four configuration reports concurrently and streams ordered frames
through the existing bounded serializer, with a declared buffer budget below
1 GiB. It does not construct one JSON array containing every monthly report.
`configurations.bin` starts with `GRIDMP1\n`; each frame is a little-endian
uint64 payload length, 32-byte SHA-256 and one MessagePack map. The frame bound
is 16 MiB. Canonical configuration hashes retain the old report byte meaning.

Python validates frames and the whole transport file incrementally, verifies
each configuration's accounting/months, and uses the existing exact positional
codec with zlib level 3. The final `results.bin` starts with `GRIDRB2\n` and has
the same length/checksum framing around compressed positional records. Its
checksum covers the uncompressed positional bytes; bounded decoding additionally
requires a complete zlib stream without trailing data. Arbitrary-size signed
integers and exact ratios are preserved. `results.sqlite` contains configuration
IDs, dense/canonical ordinals, offsets, lengths, checksums, batch receipts and
metadata; it contains no financial payload column. Small production
`summary.json` and `execution.json` contain descriptors, identities and timings,
not financial tables. The old small JSON engine path remains for compatibility
and independent controls; old SQLite BLOB catalogs remain readable unchanged.

Each batch exhausts the verified input iterator before publication. Binary
bytes are flushed and fsynced before the SQLite transaction commits its index,
batch receipt and committed prefix length/hash together. A crash cannot publish
an uncommitted tail. Resume backs up the index and copies only its verified
committed binary prefix into a new output. Final admission independently
re-reads all financial records, checks their canonical engine fingerprints and
rehashes sources before marking the catalog complete. Source/rule/code/binary
and selected-list identities are checked again before publication.

Observed validation for this implementation:

- 13 release CTests; two ASan/UBSan CTests and a separate binary-calendar replay
  under both sanitizers passed.
- 250 focused Python tests passed. The 18 binary tests cover exact selected
  coverage/five batches, a stream above 128 MiB, rollback, hard crashes before
  and after index commit, cancellation, truncation, corruption, disk failure
  and legacy catalog reads.
- Full Python regression: **2,202 passed, seven skipped, three pre-existing
  failures**, with no new failures. The two manual-codec actual/expected hashes
  match the retained baseline exactly; the paired-totals test retains the same
  USD/BTC expectation against USDT fixtures. The full suite is not green.
  This full-suite run preceded the final optional exact-record fixture flag;
  default fixture bytes were independently proved unchanged on 48 patterns
  at 24 and 32,000 minutes. The final focused run covers the added branch.
- Repository Ruff and mypy on all 240 application/test source files passed;
  changed scripts/tests also passed strict targeted checks and formatting.
- Independent Fraction, sequential/parallel, recovery and old-build controls
  agree on all compared trades, hashes, finances and calendar statistics.
- A real C++ binary report with **1,024 configurations × 64 months × eight
  scopes** produced 456,484,788 transport bytes and 75,872,563 final binary
  bytes, with a 3,570-byte JSON summary. Every row was checked during streaming
  publication. Four independent controls checked 32 pair outcomes, 13,846
  trades and 2,048 monthly scope rows. Observed peak RSS was 498,925,568 bytes
  for C++ and 587,198,464 bytes for Python.
- All 4,792 original source hashes were rechecked. The short real calibration
  used 64 pairs, 1,024 selected configurations, 65,504 records and 32 dynamic
  workers. Three warm-cache repetitions had identical fingerprints and replay
  times 0.283 / 0.299 / 0.289 seconds. This small integration measurement ran
  alongside the bounded source rehash and is not a full-history time estimate.

Evidence and preserved inputs are under
[`exact-records-r2`](/opt/grid-runs/tp-selected-bin-v086fr43/exact-records-r2/verification/local-gates.json).
The owned [controller](/opt/grid-runs/tp-selected-bin-v086fr43/exact-records-r2/run-selected-chain.py)
has started the selected-matrix load on **all 4,480 configurations and all
4,792 admitted pair/rule identities**, initially exactly 1,024 synthetic records
per source (4,907,008 total input records per configuration), retaining gaps.
The earlier attempt used a minute-count fixture that produced fewer records
on gapped sources and was safely stopped with its checkpoint before admission.
The explicit exact-record fixture option fixes that mismatch; tests cover
1 / 1,024 / 2,048 / 32,000 records on every source. The stopped attempt remains
under the parent directory and does not count toward the corrected load gate.
The policy `selected-matrix-600s-binary-v3` doubles input up to 32,000 bars if
complete selected-matrix replay totals less than 600 seconds. It then requires
two checkpoints and exact recovery of a full batch. Only after those conditions
pass does the controller run the seven-day rehearsal and then every source's
full history. It stops on failure or resource limits and does not automatically
resume an interrupted worker. Final publication requires independent complete
financial/source checks and the committed binary hash.

Current state is in
[`chain-status.json`](/opt/grid-runs/tp-selected-bin-v086fr43/exact-records-r2/chain-status.json)
and [load progress](/opt/grid-runs/tp-selected-bin-v086fr43/exact-records-r2/stages/endurance/progress.json).
Neither the new load, rehearsal nor full history is claimed complete here.
The 1–3 day duration remains a target to be revised from actual measurements;
there is no automatic history/configuration reduction. Trading semantics,
fees, datasets and the web interface were not changed by this increment.

### Owner amendment: proceed directly after recovery (2026-11-21)

The owner explicitly canceled the seven-day rehearsal for this selected study
and requested full history immediately after the remaining checks. This
supersedes the rehearsal prerequisite above for this run only. The immutable
[`rehearsal-waiver.json`](/opt/grid-runs/tp-selected-bin-v086fr43/exact-records-r2/rehearsal-waiver.json)
records the exact instruction and binds it to the study, code, executable and
selected configuration list. Rehearsal status is **WAIVED_BY_OWNER**, not PASS.

All five load batches have completed, covering 4,480 configurations and all
4,792 admitted pairs/rules with 4,907,008 synthetic records per configuration.
The complete-batch replay totaled 2,251.324 seconds; publication/audit brought
the load calculation to 2,282.468 seconds. Exact financial/monthly binary checks
and the repeated synthetic-source hashes passed. Recovery of a full batch is
still running; two durable checkpoints and exact comparison against its
uninterrupted baseline remain mandatory before full history may start.

The new owned controller
[`run-selected-chain-without-rehearsal.py`](/opt/grid-runs/tp-selected-bin-v086fr43/exact-records-r2/run-selected-chain-without-rehearsal.py)
adopted the same running research CLI and C++ recovery process. No signal was
sent to either calculation process. The prior monitoring parent was retired;
the handoff is recorded in
[`scheduling-handoff.json`](/opt/grid-runs/tp-selected-bin-v086fr43/exact-records-r2/scheduling-handoff.json).
Its remaining stage order is `endurance -> full`. The small run-specific full
entry point checks the matching validation, calibration and endurance receipts,
minimum duration, both checkpoints and exact full-size recovery before invoking
the unchanged `_execute_batches` under the existing heavy-worker lock. Its
admission receipt carries the waiver separately; no rehearsal evidence is
invented. Twenty-two admission/handoff checks passed, including rejection of
changed identities, missing evidence and insufficient recovery; the unchanged
full executor receives no time-window limit.

Trading code, executable, study identity and earlier artifacts remain unchanged.
The full run still covers every available source record for all 4,480 selected
configurations on 32 calculation workers. Its final publication still requires
the independent financial/source audit and committed binary size/hash checks.
The forecast observer now uses actual full-run progress; it no longer waits
for a rehearsal which the owner has canceled. Full history is not yet claimed
started or complete by this scheduling amendment.

### Gap-free own-history selection (owner approval, 2026-11-22)

`select --history-policy gap_free_own_span` derives a separate immutable
`grid-research-sweep-selection-v2` manifest from the verified seven-venue
selection. It re-reads every parent source with bounded parallel readers,
validates record structure and timestamps across read boundaries, and checks
the complete SHA-256. Missing minutes exclude the whole exchange/symbol;
duplicates, time regressions, changed files or incomplete audits block readiness.
No history is filled, shortened or rewritten. There is no minimum duration.
The original selection command and seven-venue admissions retain their rules.

The actual repeated audit completed in 75.25 seconds and reproduced the
approved counts exactly:

| Venue | Included | Excluded |
|---|---:|---:|
| BingX | 114 | 681 |
| Gate | 38 | 1,968 |
| HTX | 619 | 7 |
| KuCoin | 411 | 391 |
| OKX | 462 | 21 |
| Bitfinex | 0 | 64 |
| XT | 0 | 16 |
| Total | 1,644 | 3,148 |

The retained sources contain 1,599,348,160 records (38,384,355,840 bytes).
The manifest records each exclusion, each original audit/hash, each retained
source's own span, complete UTC days/months, and 43 endings before the archive's
last calendar day. Short continuous histories remain included and identifiable.
Publication checks the parent and source identities again, checks that the
auditor did not change, and uses exclusive atomic publication with `fsync`.

- [Selection manifest](/opt/grid-runs/gap-free-selection-20261122-5hk483e3/selection/preparation/manifest.json)
- [Frozen 4,480-configuration study](/opt/grid-runs/gap-free-selection-20261122-5hk483e3/study-final/matrix-study.json)
- [Manifest-bound validation](/opt/grid-runs/gap-free-selection-20261122-5hk483e3/validate/receipt.json)
- [Previous-result compatibility audit](/opt/grid-runs/gap-free-selection-20261122-5hk483e3/compatibility-receipt.json)
- [Final verification and limitations](/opt/grid-runs/gap-free-selection-20261122-5hk483e3/verification.json)

The new study preserves canonical configuration IDs and five batches of
1,024 / 1,024 / 1,024 / 1,024 / 384 configurations. Python and C++ bind the
exact declared five-venue roster, source IDs, source/rule hashes and build
identity. Changed or shortened configuration metadata is rejected even when
the displayed list still contains 4,480 rows. Old checkpoints and admission
receipts are not reusable for this study. Bounded synthetic diagnostics carry
their distinct effective roster explicitly.

For this policy only, an aggregate TP requires a price from the current common
O/H/L/C phase for every open position in its scope. An open position whose
history ended suppresses that scope's aggregate trigger; ordinary trades and
other eligible venue exits continue. Empty expired sources do not block an
exit. Counts are updated incrementally and rebuilt from inventory on recovery,
without scanning all positions at every price. The independent Fraction oracle
checks the same rule. Legacy studies retain their historical quote convention.

Five-venue tests verified the portfolio plus five exchange scopes, exact trades,
financial totals, binary roundtrips, sequential/32-thread equality and checkpoint
recovery. The manifest-bound short validation compared 19,601 trades for eight
configurations on six invented pairs with all five venues; this is a correctness
test, not a full-roster load test. Detailed single-configuration replay now
preserves the same roster and quote policy. Every one of the old 4,480 binary
frames remains readable: 35,840 total and 3,834,880 monthly scope rows were
decoded, and the old binary/index SHA-256 values remained unchanged.

Final focused validation passed **229 tests**; Release CTest passed **13/13**,
and ASan/UBSan passed the same **13/13** plus **20/20** gap-policy integration
tests. Ruff, formatting and mypy passed (240 application/test files and 12
targeted files). The full Python regression finished in 617.82 seconds with
**2,198 passed and two existing manual-codec failures**. Their actual and
expected hashes match the preserved earlier regression exactly; the full suite
is not green. That broader run started before final parent-study metadata
hardening; the final focused run covers the resulting implementation and added
tamper cases. The final receipt binds evidence to the immutable study and build.

These 1,644 pairs are **candidates**, with admission explicitly pending price,
token-identity and historical-rule review. Continuity alone does not prove
price provenance, historical trading rules or real intraminute order. No new
full-history matrix run or deployment is started by this preparation step.
The existing binary format and Streamlit venue discovery are reused; old
datasets, manifests and published results remain untouched.

### Applying abnormal-asset exclusions (owner reminder, 2026-11-22)

The 1,644-pair gap-only selection above is superseded for new research preparation
by [the reviewed manifest](/opt/grid-runs/reviewed-selection-20261122-1mgj2aww/selection/preparation/manifest.json)
and [its 4,480-configuration study](/opt/grid-runs/reviewed-selection-20261122-1mgj2aww/study/matrix-study.json).
All original files and historical results remain unchanged.

A second independent complete scan read 1,599,348,160 records and matched every
SHA-256 and file identity. It found no missing/duplicate/unordered minutes or
invalid OHLC values, but found unmodeled unit changes and uncertain prices.
For example Gate DATA3L and SOXL3L jump about 1,000-fold after the announced
1,000:1 consolidation; maintaining the previous token quantity would fabricate
profit. Continuous timestamps during the trading halt do not establish fresh trades.

The applied conservative review excludes a whole exchange+pair when any of
these conditions is present, independently of prior backtest results:

- A symmetric ratio >=2 for previous-close/next-open, consecutive closes, or
  high/low within one minute.
- At least 1,440 consecutive identical flat OHLC records, or 60 identical
  nonflat records; runs include read-block boundaries.
- Identified leveraged tokens, tokenized equities/funds, or token rebases whose
  quantity/corporate-action/credit behavior the replay does not implement.

There are 596 price-screen exclusions and 241 unsupported-asset exclusions,
with 88 overlaps: **749 additional exclusions, 895 retained sources**. The new
roster is BingX 50, Gate 9, HTX 276, KuCoin 304, OKX 256, with 1,068,138,807
records (25,635,331,368 bytes). The earlier 3,148 gap exclusions remain excluded
through the immutable parent chain. No minimum history length is introduced.

These thresholds also exclude potentially genuine crashes or low-activity
markets. The resulting universe is a declared conservative subset, not an
unbiased representation of all markets or independently certified market data.

`select --history-policy gap_free_own_span --exclusion-review REVIEW.json`
publishes `grid-research-sweep-selection-v3`. Its verifier checks the v2 parent,
complete source decisions and their SHA-256, linked evidence hashes, exact
partition, unchanged included rules/records, and five-venue coverage. It rejects
unknown/duplicate/missing decisions, retained exclusion reasons and altered
artifacts. Existing manifests cannot be overwritten. `matrix-prepare` binds the
new roster while preserving canonical configuration IDs and 32-worker settings.

Full replay and full replay resume now refuse unresolved research admission
even if technical test receipts exist. Rehearsal resume cannot silently use a
missing window as full history. The current historical-rule snapshot decision
and the reproduced own-exchange-calendar Sharpe/Sortino defect remain pending;
the new selection does not claim those issues are fixed. No full run was started.

Verification: 193 focused Python/integration tests passed in 7.82 seconds;
Ruff/formatting and mypy passed for five targeted files. The new study's short
synthetic validation passed exact Fraction comparisons with 1/32 threads,
binary roundtrips and checkpoint recovery. A direct full-admission check was
refused for the pending review before any engine launch (the existing job
wrapper records this expected `Blocked` exception as `FAIL`). This is not a
full-roster backtest. See [verification](/opt/grid-runs/reviewed-selection-20261122-1mgj2aww/verification.json)
and the [complete exclusion decisions](/opt/grid-runs/reviewed-selection-20261122-1mgj2aww/exclusion-review.json).

### Current public CCXT rules for the reviewed roster (owner decision, 2026-11-22)

The owner chose the most recent available public CCXT rules instead of
reconstructing historical rule changes before the next run. A separate CCXT
4.5.93 environment collected dated public spot catalogues for BingX, Gate, HTX,
KuCoin and OKX on 2026-11-21 UTC. The production Python environment and lockfile
were not changed. The immutable
[v4 selection manifest](/opt/grid-runs/reviewed-selection-20261122-1mgj2aww/current-rules-selection-job/preparation/manifest.json)
(SHA-256 `0539b4545525f2d37a3e4e4e2f43e11ed4ff35c71c2357163318493848f1229c`)
binds each pair's current rule snapshot and its hash, the reviewed v3 parent,
and the unchanged source files. Its full source-hash audit passed.

The v4 selection is READY with **881 pairs**: BingX 38, Gate 9, HTX 274,
KuCoin 304 and OKX 256. Relative to the reviewed 895-pair roster, 14 pairs
are additionally excluded because the current public catalogue marks them
inactive (12 BingX, two HTX). Their reasons remain in the manifest. The
included files contain 1,065,649,146 records and 25,575,579,504 bytes;
the original files and earlier results remain untouched. Two retained OKX
price ticks changed against the previous snapshot: CORE/USDT from 0.00001 to
0.000001, and SOPH/USDT from 0.000001 to 0.0000001.

`CURRENT_PUBLIC_RULES_ASSUMPTION` means one contemporary rule snapshot is
applied to each pair's entire archive. This is an assumption-based historical
simulation, **not** verification of the rules in force at each historical date.
The v3 manifest remains blocked under its separate
`PENDING_HISTORICAL_RULES_AND_STATISTICS_REVIEW` readiness; its old receipts
cannot admit or resume the v4 study.

For this gap-free selection, monthly Sharpe/Sortino and monthly medians use
each venue's own observed history. Months before that venue has observations
are not treated as zero-return months; the portfolio retains its portfolio
calendar. The engine marks this policy
`venue_own_observed_span_v1`, and the independent validator and viewer follow
the same marker. Legacy result calendars remain readable as recorded.

As recorded on 2026-11-22, the new
[4,480-configuration study](/opt/grid-runs/reviewed-selection-20261122-1mgj2aww/current-rules-matrix-r2/prepare/matrix-study.json)
uses 32 calculation threads and has matching [preparation](/opt/grid-runs/reviewed-selection-20261122-1mgj2aww/current-rules-matrix-r2/prepare/receipt.json),
[validation](/opt/grid-runs/reviewed-selection-20261122-1mgj2aww/current-rules-matrix-r2/validate/receipt.json)
and [calibration](/opt/grid-runs/reviewed-selection-20261122-1mgj2aww/current-rules-matrix-r2/calibrate/receipt.json)
receipts. Their statuses are READY, PASS and PASS, respectively. The three
short calibration repeats produced identical fingerprints. The owner waived
the weekly rehearsal. The [complete-matrix load](/opt/grid-runs/reviewed-selection-20261122-1mgj2aww/current-rules-matrix-r2/endurance/receipt.json)
finished with all 4,480 configurations, 725.7 seconds of replay and two
checkpoints; the full-size-batch recovery matched the uninterrupted result.
The [full-history run](/opt/grid-runs/reviewed-selection-20261122-1mgj2aww/current-rules-matrix-r2/full/receipt.json)
then completed on the same study and build in 14,779.97 seconds (4 h 6 min
20 s). Its binary catalog contains all 4,480 configurations in five batches;
each processed all 1,065,649,146 selected source records. The final receipt
reports full source SHA-256 re-verification and independent financial/monthly
checks for every configuration. `results.bin` is 342,361,949 bytes, with
`results.sqlite` holding the index. These are assumption-based historical
results under the dated current-rule snapshot, not certified historical-rule
performance.

### One-hour flat-price filter for the next study (owner decision, 2026-11-22)

The owner chose a further, separate selection derived from the 881-source v4
manifest. Exclude the entire exchange+pair when its own history contains **60
consecutive one-minute records with identical `O=H=L=C` prices**. Count runs
across reader-block boundaries and through the final record. The earlier
gap-free, abnormal-price and unsupported-asset exclusions remain in force;
no price series is trimmed, filled or rewritten. The rule is independent of
backtest profit.

The derived immutable v5 manifest confirmed **109 of 881 parent sources** and **70,628,589
records**: BingX 15, Gate 5, HTX 11, KuCoin 26 and OKX 52. It excludes 772 parent
sources, including all 26 previously discussed long flat HTX cases. The
manifest binds the v4 manifest and complete-audit hashes and records an explicit
reason for every exclusion. See [the selection manifest](/opt/grid-runs/flat-hour-20261122-0435/selection/preparation/manifest.json).

Keep all **4,480 canonical configuration IDs**, the same dated public CCXT
rule snapshot and trading assumptions, 32 calculation threads and binary
results. The owner subsequently waived another load test and explicitly requested
the full backtest immediately. The controller records `matrix-endurance` as
`WAIVED_BY_OWNER`; a new code-bound prepare, validation and short calibration
passed before the full run. No weekly rehearsal was run. The
[full receipt](/opt/grid-runs/flat-hour-20261122-0435/direct-matrix/full/receipt.json)
reports `COMPLETE` for all 4,480 configurations and 70,628,589 records per
configuration, with 109 source hashes reverified and all financial/monthly
results independently checked. The five binary-index batches cover ordinals
0–4,479 without gaps, including the final 384 configurations. The run took
1,019.09 seconds; `results.bin` is 182,641,330 bytes. The
[separate publication](/opt/grid-runs/results-viewer-ssh/flat-hour-20261122-0435/final-publication.json)
and its own median index are active in the SSH viewer and detail worker.
The 881-source publication, its medians, earlier manifests and all original
datasets remain preserved and separate.

No official exchange archive comparison is part of this filter. An unchanged
price can reflect real trading, and a stale price can persist for less than an
hour. The resulting study is a conservative assumption-based subset, not proof
that every retained minute has a fresh traded price or that the historical
CCXT rules were unchanged.

### Variable BUY depth and 59,367 configurations (2026-11-22)

The owner retained every integer grid step 1–10%, coin TP 2–8%, and selected
exchange/global TP **always enabled**, each 2–12% by one percentage point.
Seven independent BUY depths (30/35/40/45/50/55/60%) give 59,290 factorial
configurations. Eleven approved fractional probes at each depth add 77; the
complete immutable list contains **59,367** unique configurations. The exact
probes and boundary example are recorded in `RESEARCH_INTERVIEW_2026-11-19.md`.
Use the same 109-source v5 selection and dated CCXT rules as the completed
hour-filter run. No profit-based pair exclusions or historical slices are added.

`matrix-plan --output FILE` exclusively creates the accepted
`grid-research-configuration-plan-v2` without launching a worker. Each row has
`grid_depth_pct`; the old v1 4,480 plan remains unchanged. Depth 60 retains the
original canonical ID/ordinal, while other depths add `-d30`…`-d55`. Internal
canonical blocks are 60,30,35,40,45,50,55. At batch size 1,024 there are 58 batches:
57 full batches and a final 999. Five-venue preparation admits only the complete
approved old or new list; diagnostic subsets do not weaken that admission.

The C++ engine prepares the floor multiplier once per configuration. It applies
that multiplier at initial/reseed entry, ordinary executed SELL and checkpoint
restore; there is no new per-price financial calculation. The boundary limits
BUY levels, without discarding positions or acting as a stop loss. Rounded BUY
equal to the floor remains eligible only after a strict market-price crossing.
Aggregate reseeding still starts at each pair's next observed price. Explicit
depth is bound into plan/checkpoint identities and result metadata; omission
retains legacy 60% behavior and output bytes. Old checkpoints cannot be resumed
under a changed plan/build. Binary frames retain depth in their existing exact
extension map, without changing the result codec or rewriting old archives.

Streamlit and the detail worker reconstruct selected rows from the new identity
and validate result depth against it. The main table has a numeric negative BUY
boundary field for sorting/filtering; detail/confirmation pages show it too.
Legacy archives default to −60%. Native table double-click behavior is retained.
The bounded binary median cache limit is 64 MiB (about 30 MiB estimated for the
new matrix); old caches and the active 4,480-result publication remain separate.

Implementation checks: all 13 CTest checks passed in the fresh
`/opt/grid-builds/depth-matrix-test` build. The 78-test Fraction/aggregate/monthly/
verification group passed, including all seven depths, strict boundary equality,
SELL versus aggregate reseed anchors, 1/32-thread equivalence, and comparison
against the frozen calendar build. Financial/monthly output, pair rows and the
bytes/order of each configuration's trades matched for default and explicit60.
Additional matrix/gap/codec tests and 33 viewer plus 12 detail tests passed;
depth-aware detail replay, stop/restore, old archive reading and changed-depth
rejection are covered. Scoped Ruff/format/mypy checks passed. These checks are
correctness evidence, not a load or historical-performance measurement.

Prepared artifacts:
`/opt/grid-runs/depth-matrix-20261122-nc93lrj3/configuration-plan.json` and
`prepare/matrix-study.json`. The plan SHA-256 is
`4be7f40b9da0d98861ef671bb5ffaa8d00edfccbdab4667441f42732756ef5a3`.
Settings retain 32 calculation threads, dynamic scheduling, 1,024 configurations
per batch, 16 MiB shared blocks, 4,096-record source buffers and 16 audit readers.
Source membership remains 109 with 70,628,589 records per configuration. No full
backtest, calibration, endurance or weekly rehearsal is started by plan creation.
The owner's earlier no-repeat-load-test instruction remains in effect.

The subsequent code-bound `validate/receipt.json` is **PASS** for the same
prepared study. It compares 15 diagnostic configurations (including all seven
depths) against Fraction at 1/32 threads, checks exact monthly calendars and
binary roundtrips, and verifies checkpoint continuation. This short synthetic
validation is not a 59,367-configuration historical replay or a load test.
`implementation-checks.json` records the final identity check and local evidence.
The live loopback viewer was restarted once without changing its publication.
Six Chromium checks passed, including the −60% legacy column, single/double
click distinction, native sorting and scrolling, with no page errors. Evidence
is in `browser/browser-receipt.json` under the same new artifact directory.
