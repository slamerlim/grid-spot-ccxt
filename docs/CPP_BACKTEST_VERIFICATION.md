# Module 1 C++ research verification

Status: in progress. Rebuilt Debug/Release/sanitizer gates and a bounded real-bar
financial comparison pass. Full historical return runs are not yet complete.
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

The full replacement study is running with the same command above and exclusive
output directory `/opt/grid-runs/module1-20261110/full-study-v4-notices`.

The exclusive directory retains the copied executable, exact configuration,
selection manifest, all 22 inputs, an append-only trial registry and per-child
resource/output receipts where returned. Cases run sequentially with a six-hour per-case timeout,
512 MiB address-space ceiling and 32 MiB per-output-file cap. No archive writes
or cache eviction are performed. A RUNNING registry entry is not completion.

The next required evidence is all ten full histories and all
twelve frozen portfolio cases, with accounting, failed/negative trials and
source/config/result hashes. No full-study completion claim is made here.

Rollback is a normal revert/removal of this isolated research increment through
reviewed version control. No live database migration or source-data rewrite is
involved. Keep generated receipts and original archives intact.
