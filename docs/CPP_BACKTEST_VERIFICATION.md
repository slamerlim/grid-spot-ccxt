# Module 1 C++ research verification

Status: in progress. Rebuilt Debug/Release/sanitizer gates and a bounded real-bar
financial comparison pass. All ten individual full histories and the first of
twelve matched portfolio comparisons are complete; the matrix remains incomplete.
This document records executed scopes, not a blanket acceptance pass.

Starting HEAD: `cd48dd01734d2b84ad13651e907e72fc437a0cbc`, branch
`i8/public-paper-hour`. Live Python domain, execution, exchange, ledger/schema,
portfolio and operations behavior is unchanged. Original history is read-only.
All results are offline; no exchange credentials, requests or trading are used.
GitHub Free only; the known hosted CI lock is not retried.

## Tools and build commands

GCC 13.3.0; CMake 3.31.6 at `/opt/grid-tools/module1/bin/cmake`; C++20;
OpenSSL 3 Crypto supplies exact BIGNUM arithmetic and SHA-256. Financial ledger
units use checked integers, not binary floating point. Project flags include
`-Wall -Wextra -Wconversion -Wsign-conversion -Wshadow -Werror`; no fast math.

```sh
/opt/grid-tools/module1/bin/cmake -S cpp/backtest -B /opt/grid-builds/module1/debug -DCMAKE_BUILD_TYPE=Debug -DGRID_PYTHON=/opt/grid-tools/module1-py312/bin/python
/opt/grid-tools/module1/bin/cmake --build /opt/grid-builds/module1/debug -j2
/opt/grid-tools/module1/bin/ctest --test-dir /opt/grid-builds/module1/debug --output-on-failure
```

Release uses `-DCMAKE_BUILD_TYPE=Release`; sanitizer testing uses Debug with
`-DGRID_SANITIZE=ON` (AddressSanitizer and UndefinedBehaviorSanitizer). Initial
numeric/source foundation passed 2/2 CTests in Debug, Release and sanitizer
builds, including 12 exact Python mathematical cases. Later engine additions
must pass their own rebuilt suites; the foundation result does not cover them.
Valgrind and Clang were not installed at the initial inspection.

## Independent arithmetic and lifecycle comparisons

An independent seeded primitive comparison checked 1,200 minimum-BUY and
aggregate-SELL cases: 1,140 accepted exact financial matches and 60 matched
rejections, with no mismatches. Its executed sources, script, binary and receipt
are retained under
`/opt/grid-runs/module1-20261110/verification/primitive-review/`.

The first complete C++ allocation lifecycle comparison passed 20 cases and 159
transitions against the actual Python reducer. It compares every state, working
order, reservation, emitted command, notice and accepted/rejected result, not
only final NAV. Cases include partial seed, cancel/fill races, late drain fills,
base/quote/token BUY and SELL fees, rebates, fee overruns, duplicate/conflicting
fills, unknown orders, missing fees, terminal reports before fills, controls and
four seeded interleavings. Nonterminating non-money averages have an explicitly
documented Decimal96 comparison; financial amounts remain exact.

```sh
/opt/grid-tools/module1-py312/bin/python -m grid.research.module1_reference --cpp /opt/grid-builds/module1/debug/grid_backtest
/opt/grid-tools/module1-py312/bin/python -m grid.research.module1_lifecycle --cpp /opt/grid-builds/module1/debug/grid_backtest
```

Lifecycle result SHA-256:
`1448681773100c5cd2490d0f47f4b0411181ad17d7cff70b1b4f117f8fd80dad`.
Receipt with executed binary/source hashes:
`/opt/grid-runs/module1-20261110/verification/lifecycle/debug.json`.
These are synthetic correctness checks, not historical returns.

## Full selected-source verification

```sh
python scripts/module1_verify_sources.py --help
```

The durable summary contains each exact executed `grid_backtest scan SOURCE
EXPECTED_SHA256` command and exit status:
`/opt/grid-runs/module1-20261110/verification/source-scans/summary.json`.
All ten commands exited 0 and matched complete file hashes, row counts,
structural rejection counts, gaps and first/last timestamps from the frozen
selection manifest. Source and executable identities remained unchanged.

| Measurement | Observed value |
|---|---:|
| Actual records checked | 19,965,272 |
| Source bytes read | 479,166,528 |
| Total child wall time, including startup/polling | 0.964628 s |
| Child user / system CPU | 0.671441 / 0.219851 s |
| Maximum child RSS | 11,336 KiB |
| Observed kernel input blocks | 0 |

This is warm-cache sequential decoding and hashing, **not strategy replay
throughput**. Sources were previously read; OS cache was not evicted. No
physical device throughput or cold-cache claim follows. Children used a
98,304-byte source buffer, a 512 MiB address-space ceiling and bounded logs.
Summary SHA-256:
`6a922bca7d5cb897b29c1b0d6e9e06200f4f8f247beb7f405d9748d8b6326721`.

A separate bounded cache experiment on the full 1INCH source issued Linux
`POSIX_FADV_DONTNEED` through a read-only descriptor for that file only, then
performed three identical scans. No global cache drop or archive write occurred.
The advisory was accepted; it does not guarantee eviction of every cached page.

| Scan condition | Wall seconds | Kernel input blocks | Peak child RSS |
|---|---:|---:|---:|
| First scan after per-file advisory | 0.123383 | 104,800 | 10,832 KiB |
| Immediate warm repeat 1 | 0.093830 | 0 | 10,844 KiB |
| Immediate warm repeat 2 | 0.093645 | 0 | 10,848 KiB |

Each scan checked 2,235,727 rows / 53,657,448 bytes, matched the frozen complete
source hash and left its identity unchanged. Identical result hashes demonstrate
that cache condition did not affect decoding. These small local scan observations
are not replay or sustained-device benchmarks. Exact commands, driver, copied
gated executable and receipts are in
`/opt/grid-runs/module1-20261110/verification/scan-cache-v1/summary.json`.

## Python regression scope

The full Python 3.12.16 and 3.13.18 suites passed against a frozen local
snapshot containing the existing implementation plus the new reference and
selection modules. Their receipts are under
`/opt/grid-runs/module1-20261110/verification/python-foundation/`.
Python 3.12 passed **1,600 tests** in 608.16 pytest seconds; Python 3.13 passed
**1,600 tests** in 634.99 seconds, both exit 0 with no failures or skips.
All 291 snapshotted file hashes remained unchanged before and after both runs.
Source-manifest SHA-256:
`f74c74e9cdd61b9deffd3097a980159560470d900ac73f03d8ae47477d18b9a4`.
The snapshot excludes later lifecycle/bar/source-driver additions, which need
the next full-suite pass. Both interpreters link SQLite 3.53.1 with the approved
synthetic source identity; old system SQLite was not used to bypass the guard.

The I0 static boundary now permits only the named offline C++ reference,
lifecycle and bar-comparison subprocess modules in addition to existing approved exceptions.
It does not permit exchange transport or change any live admission rule.
Bounded local executable invocation and deterministic nonsecurity random
fixtures have narrow, explained Bandit annotations rather than global ignores.

## Outstanding completion evidence

On 2026-11-10, the complete C++ suite passed **8/8 CTests in each of Debug,
Release and AddressSanitizer/UndefinedBehaviorSanitizer**. The checks cover 14
Python mathematical cases, 20 lifecycle cases/159 transitions, 27 bar paths,
13 valuation cases, 64 coordinator assertions and synthetic streaming-study
integration. The integration checks include unknown terminal marks, declared
window gaps, quote funding, counterfactual buy/hold, trigger/completion/restart
history and full-source validation outside the trading window.

Commands, build caches, actual logs, binary hashes and the source-file manifest
are retained in
`/opt/grid-runs/module1-20261110/verification/cpp-gates-v1/receipt.json`.
The Debug and Release synthetic study JSON is byte-identical, SHA-256
`ae354a1600fa82076beadd9fe5f62f43982386081818522c530e42fdd734b164`.

Independent review found two previously uncovered TP prices that differed by
one tick because Python rounds intermediate calculations to Decimal96 while
the initial C++ planner used exact rationals. The corrected planner applies
the same 96-significant-digit half-even rounding at those intermediates;
the ledger remains exact checked integer money. Both counterexamples are
permanent differential fixtures. A further 180-case fee/spacing/quantity sweep
passed. These finite tests do not establish universal input equivalence.

Gap review also corrected the scheduling clock before cancellation processing
and prevented a cash-only allocation from restarting a BUY on another market's
fresh timestamp using its own stale book. The configured source age remains
60 seconds: at the exact freshness boundary the existing `<=` policy applies.
Unknown liquidation marks produce null sellability/dust estimates while actual
owned quantity remains visible; unknown inventory is never relabeled dust.

### Actual 4,096-bar financial and performance comparison

Executed command:

```sh
/opt/grid-tools/module1-py312/bin/python /opt/grid-runs/module1-20261110/verification/cpp-window-profile/run.py release-v1
```

The same consecutive actual 1INCH window used in the earlier Python profile
passed exact comparison of cash, position cost, realized PnL, owned base, quote
reservations, fees and turnover. Raw marked NAV matches after the declared
1e-12 quote rounding. No global TP occurred in that window. Three C++ repeats
produced identical result SHA-256
`8581947239e6b60765ce9479e2375d982db4b223bc8dc9d9ccf71690b64f0d42`.

| Measurement | C++ Release | Python reference |
|---|---:|---:|
| Three warm wall times, seconds | 3.728007 / 3.707905 / 3.708223 | 0.574415 / 0.569242 / 0.567525 |
| Maximum child RSS | 20,696 KiB | not isolated by this comparison |
| Source integrity | full 53,657,448-byte source checked | same predecoded 4,096-bar window |

These workloads have the same bars and baseline financial result, but C++ also
computes portfolio decisions, buy/hold, risk and reporting and reads/hashes the
entire source. The Python reference has no portfolio TP. Therefore this is not
an instruction-for-instruction speed comparison. The measured C++ cost makes
the full study expensive; retain this baseline and profile before optimizing.
No fast-math, parameter change or suppressed valuation is permitted to improve
timings. Raw source identity was unchanged; cache was warm with no eviction.

The measured profile attributed about 60% inclusive time to rational reduction,
with OpenSSL's general GCD path dominant. The isolated optimization uses ordinary
exact Euclidean GCD for these public research values, with a native unsigned-word
path where both operands fit. It changes no financial formula, precision,
strategy input or experiment parameter. Signed/zero/word-boundary and large
Fibonacci coprime/common-factor regression cases were added.

Debug, Release and ASan/UBSan again passed all **8/8** gates. The receipt is
`/opt/grid-runs/module1-20261110/verification/cpp-gates-v2-gcd/receipt.json`.
The same registered profile was rerun with argument `release-v2`: C++ warm wall
times were **1.040040 / 1.050189 / 1.039774 seconds**, maximum child RSS
**20,808 KiB**, and all three result hashes remain exactly
`8581947239e6b60765ce9479e2375d982db4b223bc8dc9d9ccf71690b64f0d42`.
This is approximately **3.57x** faster than the previous C++ median for this
specific workload. Python's narrower reference measured approximately 0.57
seconds; no C++-over-Python speedup is claimed. Full source identity was unchanged.
The Release executable SHA-256 is
`a7a2a4cf0b4d207339a82d5263981e8485522e6cad5c8d27a12b658520145714`.
The optimized source identity is
`218307433706d3609c4b179739ec36002eec4a8fd55442edabd027b5e3122b1a`.

### Final Python gate correction

The first broad final snapshot retained **1,659 passes and one failure on each
interpreter** under `verification/python-final/`. The failure was the static
network/import boundary omitting the explicitly authorized offline
`module1_bars.py` subprocess use. It was not a financial assertion failure and
has not been relabeled a pass. The narrow named-module exception now passes all
four boundary tests. A subsequent immutable snapshot includes that correction
and the final benchmark/unknown-NAV reporting validations; its complete Python
3.12/3.13 suites passed under `verification/python-complete-v2/`.
Its 312-file source manifest SHA-256 is
`1d576646bbe0739044fa489d9eb1086462de738094a390db4a7dd11442a23335`.
Python **3.12.16 passed 1,668 tests in 613.79 seconds** and Python **3.13.18
passed 1,668 tests in 633.88 seconds**, with no failures or skips. All eleven
quality commands passed, including lint/format, typing, Bandit, dependency
lock/audit, secret scan, doctor and build. Each immutable snapshot remained
unchanged before and after every command.

The subsequent report-only change preserves planner-notice maps as deterministic
compact JSON in allocation/exchange/portfolio CSV rows. Its separate
`verification/python-complete-v2/delta-001/` snapshot passed all 108 Module 1
tests on each interpreter plus lint/format and typing. The two changed Python
files are `scripts/module1_report.py` and its existing integration test. This
separate coverage is not silently included in the earlier full-suite scope.

### Full study execution

The first frozen revision-4 study attempt used the optimized executable:

```sh
/opt/grid-tools/module1-py312/bin/python -m scripts.module1_study --config research/runs/module1-20261110/experiment_config.json --binary /opt/grid-builds/module1/release/grid_backtest --output /opt/grid-runs/module1-20261110/full-study-v4-gcd --group all --execute --timeout-seconds 21600
```

Final review found that existing bounded planner-notice counters were missing
from study JSON. Before any full replay completed, this attempt was explicitly
interrupted to add that diagnostic output. The driver exited 130; its child was
killed/reaped by the existing exception path and both PIDs were verified absent.
`full_okx_1INCH/receipt.json` and `interruption.json` retain the failed attempt,
21 unexecuted cases, hashes and reason. No return or completed replay is claimed.
The replacement keeps the identical cohort, financial parameters and policies.

The replacement executable adds only bounded `planning_notice_counts` to each
scope's metrics, counting emitted notices rather than elapsed inactivity. A
handwritten fresh-book versus one-step reentry test also verifies different
seed prices and future-bar eligibility. All eight gates passed again in each
build mode; receipts are in `verification/cpp-gates-v3-notices/`. Source identity:
`6a6ac0c21660730b55caa9f2e1792e279a0a305ca5284d7581429b8ab3dd6767`.
Release executable SHA-256:
`1284b8e183ccb49bf6db0bfa2de80165a0ab09f4fc7dfe80f15ace633281315b`.
The registered 4,096-bar profile took approximately 1.062 seconds in each of
three repeats, with result SHA-256
`e32afef580c5e00e0a4f7b8f6ad2bc11ee813a43d07c44572ddae6e226fa8fad`.
Every preceding JSON field matches the earlier result after removing only the
added notice maps; see `verification/cpp-window-profile/release-v3-notices/`.

The full replacement study uses the same command above and exclusive
output directory `/opt/grid-runs/module1-20261110/full-study-v4-notices`.

The exclusive directory retains the copied executable, exact configuration,
selection manifest, all 22 inputs, an append-only trial registry and per-child
resource/output receipts where returned. Cases run sequentially with a six-hour per-case timeout,
512 MiB address-space ceiling and 32 MiB per-output-file cap. No archive writes
or cache eviction are performed. A RUNNING registry entry is not completion.

All ten full histories and `seed_multi_strict` have now completed. Eleven frozen
portfolio cases remain without completed receipts in the first-portfolio
publication snapshot below. No full-study completion claim is made here.

### Interim result publication and current-build check

The first actual-results snapshot is recorded in
`MODULE1_10_MARKET_BACKTEST_REPORT.md` and compact tables under
`research/runs/module1-20261110/`. Its extraction at 2026-11-10 15:54 UTC
validated three completed histories, one incomplete attempt and eighteen
unstarted cases. Later driver progress does not change that immutable snapshot.
The full ten-history/twelve-portfolio goal remains incomplete.

The 2026-11-10 16:41 UTC snapshot now contains all ten completed histories,
one incomplete portfolio attempt and eleven unstarted comparisons. All
19,965,272 selected source records were replayed, totaling 4,210.577237 child
wall seconds. Exact table hashes and command receipts are under
`verification/full10-extraction-20261110T1641Z/`; the immutable tables are in
`tables-full10-20261110T1641Z/`. The unchanged monthly analysis produced 466
spans; independent Fraction arithmetic checked every endpoint, label, change,
return and sampling count against all 13,896 retained NAV points. Its receipt
is `monthly-full10-20261110T1641Z/independent-verification.json`. This completes
the individual-history reporting scope, not the funded portfolio experiment.

The report now distinguishes an attempted trial with no final receipt as
INCOMPLETE from an unstarted NOT_RUN trial. Neither classification proves
process liveness. It retains FAIL receipts, blank unavailable financial cells
and an explicit external table-directory pointer. This reporting-only delta
passed 17 focused tests, Ruff lint/format and mypy; exact commands and file
hashes are in `verification/report-interim-delta-v1/receipt.json` under the
external run root. It is separate from the earlier full Python suite.

A subsequent reporting-only delta retains each allocation's scoped exit slot
(including blocked reason, anchor, order budget and timing), source/window gap
counts, terminal assessment and NAV sampling contract in the compact summary.
Unknown values remain null; daily NAV curves remain in the external CSV.
The existing integration fixture exercises blocked exits and an unobserved
terminal endpoint. All 17 focused tests, Ruff lint/format and mypy passed;
`verification/report-context-delta-v1/receipt.json` binds those checks to the
two changed Python files. The adjacent `actual-results-review.json` verifies
that previously retained fields are unchanged and that the new fields equal
the original outputs for seven completed histories. No financial model,
execution driver or historical result was changed.

The existing streaming-study integration script subsequently added targeted
negative admission and boundary tests. Its eight scenario groups and 78
controlled refusals passed against the same Debug, Release and sanitizer
executables, with lint/format and strict typing checks. Each refusal checks the
expected error and absence of partial result output. The tests cover malformed
configuration, financial precision/funding bounds, unsupported policies,
whole-source corruption outside the replay window, parser size limits and the
64-source admission boundary using tiny invented files. Exact commands, source
and executable hashes, and a guard-to-test mapping are retained under
`verification/guard-delta-v1/`. Neither the engine nor its binaries changed.

The next focused delta expands the same script to twelve scenario groups and
148 controlled refusals. It covers trace/bar schema, field counts, duplicate
configuration, malformed fills/reports/controls, late configuration, raw-bar
encoding/chronology/precision, the 1-MiB input and 65,536-byte line limits,
20,000 configuration rows, 10,000 events/bars and portfolio exit-policy bounds.
Boundary fixtures check accepted limits as well as rejection. Trace and bar
commands deliberately retain valid streamed observations before a later error;
their cap tests independently parse and count exactly 10,000 retained rows.
Replay refusal still requires no partial result publication. Output is streamed
to temporary files under a 16-MiB file limit and 30-second child deadline;
the largest observed output was 7,610,000 bytes.

The existing study-integration CTest passed against the unchanged Debug,
Release and ASan/UBSan executables in 2.74, 1.30 and 14.45 seconds respectively.
Scoped lint/format and strict typing passed. Exact commands, logs, executed test
script, source/binary hashes and the updated guard mapping are under
`verification/guard-trace-delta-v1/`. No engine source, historical configuration
or executable changed.

This is targeted guard coverage, not exhaustive defensive-branch coverage.
The mapping predates the byte-cap probes below. Remaining unforced paths include
the cycle-event byte cap, internal-state corruption, allocation/crypto failures
and other operating-system faults beyond the specific I/O checks recorded here. Do not
infer that every defensive error branch was triggered. Existing seeded
conservation/interleaving checks and all per-run financial validations remain.
Historical C++ replay is deterministic from the beginning; it has no checkpoint
resume capability. Strategy cycle/round restarts are a separate tested behavior.

A later bounded Release-only probe directly exercised the 100,000-NAV-sample
guard using 100,001 invented flat observations, one per UTC day (2,400,024 source
bytes). It exited 2 with the expected capacity error and zero stdout in
11.751503 seconds, with 24,148 KiB peak RSS under 60-second/512-MiB/32-MiB caps.
Source identity/hash and the frozen executable hash remained unchanged. The
earlier guard mapping predates this additional evidence; its NAV-sample-count
gap is now closed. `verification/guard-nav-cap-v1/receipt.json` retains the exact
command, fixture, helper/source/executable hashes, status and retained output.
The probe was executed inline; no standalone driver file is claimed.
No historical source was read and
no production limits were lowered to make the test pass.

Two further bounded probes used the unchanged frozen historical executable and
invented daily flat bars with new exposure disabled. A 32-bar control verified
exact cash-only NAV before testing large decimal output using `1e96` synthetic
cash/quanta/reserve values. Both refusals exited 2 with their specific JSON error,
zero stdout and unchanged source/executable identities:

| Guard | Invented bars / source bytes | Wall time | Peak RSS |
|---|---:|---:|---:|
| NAV sample bytes exceeded | 55,392 / 1,329,408 | 3.736960 s | 73,552 KiB |
| Final study JSON exceeds 32 MiB | 55,391 / 1,329,384 | 3.806646 s | 139,092 KiB |

The second fixture remains below both NAV sample caps and reaches the separate
final JSON guard. Its refused final JSON size was not emitted or measured.
Receipts, standalone probe scripts, exact commands, control results and hashes are
`verification/guard-nav-bytes-v1/receipt.json` and
`verification/guard-final-json-v1/receipt.json`. Each child had a 60-second timeout,
512-MiB address-space ceiling and 32-MiB per-output-file limit. No historical
archive, production limit or running job was changed. These tests close the NAV
buffer and final result byte-cap evidence gaps; they do not exercise the remaining
cycle-event buffer, allocation, crypto or arbitrary syscall failures.

The first interim publication scan flagged public fingerprints in the new
compact summary. Each of the 33 distinct flagged values was independently
matched to the SHA-256 of its config, cohort, executable, input, result or CSV
artifact before a filename/value-specific baseline entry was added. Detector
and filter settings were unchanged; the original failed scan and exact review
remain under `verification/interim-publication-v1/`.

The full-ten-history summary added 22 flagged public source/result/CSV hashes.
Each was recomputed from its referenced artifact before adding only that
filename/value to the non-secret baseline. Source reads were read-only and
identity checked; detector/filter settings remained unchanged. The exact
review is `verification/full10-publication-v1/public-fingerprint-review.json`.

The final diagnostic-output Debug and Release builds were also compared using
the existing six-row synthetic study input. Their complete JSON outputs are
byte-identical and all previously expected fields match after excluding only
the additive planner-notice maps. Commands, source/binary/result hashes and
comparison receipt are in `verification/current-crossbuild-v1/receipt.json`.
This closes the latest-build comparison without another historical run.

The adjacent `fd-observation.json` records five descriptors for the live driver
and four for its single-market child, including one read-only source descriptor.
The earlier retained single-market child observation also recorded four.
These are discrete samples, not a continuously measured high-water mark.
Reader ownership is bounded to one source descriptor and a 98,304-byte buffer
per admitted source; the frozen portfolio has ten sources. No running child's
limits or file descriptors were modified for this observation.

The first actual ten-market portfolio child was separately observed running
with 13 descriptors, including exactly ten source descriptors, all opened
read-only. Observed RSS was 10,448 KiB. Its process identity, descriptor flags
and inherited resource limits are retained in
`verification/portfolio-live-observation-v1/receipt.json`. This is a discrete
live observation, not a completed replay or a continuous resource peak.
Source read-ahead positions are not proof of completed financial processing.

### Same-timestamp source-order permutation evidence

`verification/source-order-permutation-v1/receipt.json` records **60 passing
comparisons**: all six permutations of three complete SOURCE rows, five successive
replay-window cutoffs, and the preserved Debug/Release executables. The three
invented six-bar files total 432 bytes, with tied timestamps and separately funded
allocations on two venues. Symbol and exchange TP were enabled alongside mandatory
global TP. Handwritten checks cover distinct seed quantities and cash, global
precedence, three exit SELLs, completion and a strictly later restart.

Every field and array order in the complete published JSON matches after removing
only `input_sha256`, which necessarily changes with TSV row order. Five cutoffs
expose financial/state summaries and complete cycle-event chronology; this does
not claim a full internal order-event trace. The largest child used 15,072 KiB RSS,
0.034 seconds and 18,111 output bytes, under 10-second/512-MiB/32-MiB bounds.
Source and binary identities were unchanged. The initial probe omitted the
required paired start bound and was correctly refused; that failed setup and its
corrected inputs remain retained. No repository edit or historical read occurred.

### Final stdout failure correction and fresh builds

A small output redirected to Linux `/dev/full` exposed a CLI defect: the preserved
executable returned success before buffered stdout was flushed. The sole production
change is an explicit `std::cout.flush()` before the existing stream check in
`main.cpp`. The old failure remains reproduced in
`verification/output-write-probe-v2/receipt.json`; the fixed Release returns 2
with `{"error":"output write failed"}` for the same tiny mathematical command.

The existing study integration now has **13 scenarios and 159 controlled
refusals**. Six paired checks verify ordinary output and write-failure reporting
for math-grid, decode, scan, trace, bars and replay. Five additional negatives
exercise the SELL price-search bound, rational product bound, Decimal96 exponent
bound, and directory read failures for replay/trace. Existing C++ tests add
source truncation after admission, simulator transition/ownership/notice guards,
and invalid portfolio rosters. These are targeted additions, not proof that every
defensive branch or operating-system failure has been exercised.

Fresh directories preserve all preceding executables. Each uses the commands
above with `-j2`, `GRID_PYTHON` unchanged, the appropriate build type and
`GRID_SANITIZE=ON` only for the sanitizer build. All commands and exit codes are in
`verification/cpp-writefix-v1/receipt.json`:

| Fresh build directory under `/opt/grid-builds/module1/` | CTests | CTest wall time |
|---|---:|---:|
| `debug-writefix` | 8/8 | 5.12 s |
| `release-writefix` | 8/8 | 3.22 s |
| `sanitize-writefix` (ASan/UBSan) | 8/8 | 23.00 s |

Each suite includes 10,074 primitive checks, 70 portfolio checks, 27 engine
integration checks, the 13 study scenarios, and the existing Python mathematical,
lifecycle, bar and portfolio-NAV comparisons. The 120 captured C++/Python/config
and lock files matched before/after; all 13 preserved binaries were unchanged.
Ordinary output on the existing tiny synthetic study is byte-identical between
the old binary and all three fresh builds, with **no excluded fields**, SHA-256
`6de55877431d0054d8ca0a29644984a556bc8987e8001bba84c002e1aedd44cb`.
The external evidence driver initially collided output and receipt filenames
after all tests passed; its failure is retained, and a separate finalizer completed
the comparisons without repeating builds or changing production code.

The fresh Release SHA-256 is
`1ef9b68151d9632b4f9141e52f8797732a0ac76fe5759122b64cdb0fef6516c1`.
Historical runs continue to use their original copied executable, SHA-256
`1284b8e183ccb49bf6db0bfa2de80165a0ab09f4fc7dfe80f15ace633281315b`.
The new executable has not been substituted into the frozen matrix. Accepted
historical results require complete parseable output, verified result hashes,
source identity and accounting checks; the old CLI exit status alone is not a
publication gate. The tiny parity result does not imply historical runs were
repeated with the fixed binary.

### First portfolio extraction and supplementary statistics checks

`seed_multi_strict` completed at 2026-11-10 17:15:01 UTC: exit 0, 10,005,677
aligned rows processed, complete hashes checked over all 19,965,272 source rows,
2,035.244700 child wall seconds and 13,944 KiB peak RSS. Its retained result
SHA-256 is `35b43b688474ab3a7556109ecebce998b7be7f353ba23b1826607f7d91d0c980`.
Net ending NAV is 9,856.393284750065 from 10,000 funded USDT, with 2,606 unknown
valuation samples retained; there was no global TP trigger. This is a completed
negative assumption-based research result, not certified executable performance.

`verification/portfolio1-extraction-20261110T1715Z/receipt.json` records successful
immutable CSV/summary extraction and unchanged monthly-analysis/verifier commands.
The snapshot contains eleven PASS results (ten individual histories plus this
portfolio), one INCOMPLETE attempt and ten NOT_RUN cases. Exact Fraction checks
validated 490 monthly spans and 14,593 retained NAV points. Daily sampling cannot
independently reconstruct the reported minute-level drawdown or underwater path.

The separate `verification/portfolio-statistics-review-v1/` receipts supplement
money/NAV checks with integer fill/cancel/order and notice aggregates, reservation
sums, profit-factor arithmetic, global cycle chronology/baselines, within-trial
rankings and derived exchange totals. Ten individual outputs and the first
portfolio passed. Checker fixtures rejected twelve altered results and accepted
three handwritten profit-factor cases; unknown returns stayed unranked.
`first-portfolio-execution.json` retains the exact first-portfolio command and
result binding. Its full-fill order partition applies only to the frozen fill
scenario. Child restart baselines are checked for funding/aggregate consistency,
not independently reconstructed from absent per-allocation restart marks. No
portfolio time-average utilization is inferred from unequal observed-bar averages.
These checks read retained outputs only; they neither replay history nor establish
profitability, continuous valuation coverage or completion of the remaining matrix.

Rollback is a normal revert/removal of this isolated research increment through
reviewed version control. No live database migration or source-data rewrite is
involved. Keep generated receipts and original archives intact.
