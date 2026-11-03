# I5 local historical validation and replay evidence

Recorded 2026-11-04 local date. The owner explicitly selected **"Approve I5 with
local validation"** for `I5-PLAN.md`. No exchange call, authenticated access,
real/sandbox order/cancellation, transfer, financial database or deployment was
performed. Original legacy source, datasets and imported specifications remain
unchanged. Generated data, configurations, manifests and results remain outside Git.

**Result: I5 implementation and local checks pass. Actual archive samples remain
ineligible for financial replay because provenance is unresolved. Hosted CI is
PENDING under the known account lock; all venues remain HOLD for live readiness.**

## Revision and reproducibility

- Branch: `i5/historical-validation-replay`.
- Base: `a2d16ced516ed993ab9b25aaf78418ee21ea2bc4`, the published I4 handoff.
- Code, tests and evidence are published together. The final chat gives the exact
  commit after remote verification. No merge is claimed.
- PR: not created; no authenticated PR connector is available and `gh` is absent.
  Ordinary SSH commit/push is authorized. Publication uses `[skip ci]`; the
  account lock was not retried and no paid feature/spending change was used.
- Reviewed artifacts: `/tmp/grid-i5-reviewed-evidence-20261104/`. Earlier
  exploratory artifacts are retained separately and are not the final evidence.
- Source/test/lock fingerprint:
  `c9aad4302c4ff4ae269302edbebbbd2dace52b20d5b2f3035da3a3c2269efd6d`.
  Compute SHA-256 over sorted relative paths for every `src/**/*.py`,
  `tests/**/*.py`, `pyproject.toml` and `uv.lock`: concatenate UTF-8 path, NUL,
  unsigned 8-byte big-endian file length, then file bytes. The external
  `source-fingerprint.json` records that list. Documentation is excluded so this
  evidence can name the exact measured implementation without a self-hash cycle.

Added `grid.research` read-only history validation, explicit-provenance manifests,
bounded UTC merge, synthetic fill adapter using the existing I1 reducer, finite
account funding, reproducible reports, exclusive publication, strict config/CLI,
synthetic demo and bounded raw/Arrow/Parquet comparison. PyArrow **26.0.1** is the
only new package, in an optional research group included by dev. Runtime trading
dependencies, SQLite schema/ledger, strategy math, exchange transport and CI
workflow are unchanged. Doctor now describes I5 and remains LIVE DISABLED.

## Local checks actually run

uv 0.13.7; Python 3.12.16 in `/tmp/grid-i2-py312`, Python 3.13.18 in
`/tmp/grid-i2-py313`; both link the existing approved test SQLite 3.53.1 build.
These are test environments, not deployment provenance approval.

| Check | Observed result |
| --- | --- |
| Full pytest, Python 3.12.16 | **461 passed**, 0 failed/skipped, 43.55 seconds |
| Full pytest, Python 3.13.18 | **461 passed**, 0 failed/skipped, 44.88 seconds |
| New research cases | **42**, including a real SIGKILL publication/restart case |
| Ruff lint / formatting | PASS; 97 Python files checked for formatting |
| Strict mypy | PASS; 71 application/test source files |
| Bandit | PASS; no findings or new suppressions |
| `uv lock --check` | PASS; 66 packages |
| Dependency advisory audit | PASS; no known vulnerabilities found |
| Secret scan | PASS; existing baseline unchanged; no new allowlists |
| Wheel / sdist build | PASS |
| Actual offline doctor | Exit 0; iteration I5, `live: DISABLED`, `ready: false` |
| Same saved demo configs under 3.12 and 3.13 | Identical result SHA-256 in both models |
| Hosted GitHub Actions | PENDING; known lock not retried |

Commands are the README checks with explicit `UV_PYTHON` and
`UV_PROJECT_ENVIRONMENT`, invoking `/tmp/grid-tooling/uv` on this host. All tests
disable Internet sockets. PyPI installation and the dependency advisory audit
are the only network-dependent validation steps; no exchange API was probed.
The audit's generic no-hash-export warning is not a failure; installation is
still governed by the hashed lock.

During development, the first complete runs passed 460/461 and exposed a
canonicalization defect: equivalent Decimals such as `1000000` and `1E+6` hashed
differently after config reload. Canonical financial serialization now reuses
the existing normalized amount codec. The final suites above include the fix
and a direct in-memory/config-reload hash assertion. An initial long-run fixture
also incorrectly assumed an unchanging seed price; it was corrected to exercise
repeated completed rounds. No financial invariant or test gate was weakened.

## Historical evidence, not an archive certification

The current metadata-only directory inventory found **16,169 `.bin` files,
211,043,742,552 bytes**, across 25 filename venue prefixes. This is a dated
directory snapshot, not validation of every file. It differs from the older
snapshot preserved in `LEGACY_REVIEW.md`.

`/opt/datasets.md` and inspected OHLC bytes support the 24-byte little-endian
layout. The document's referenced downloader source is missing. The inspected
C++ legacy reader consumes a different 16-byte format and is not an OHLC decoder.
Every source was opened read-only. Window hashes remained identical across
validation/benchmark passes; readers also checked size, inode, mtime and ctime.
No original file was rewritten, repaired, converted, chmodded or deleted.

| Selected prefix window | Records | Accepted / rejected | Missing valid minutes | Replay status |
| --- | ---: | ---: | ---: | --- |
| `bigone_USDT_BOX.bin` | 8,192 | 0 / 8,192 | 0 between accepted rows; none accepted | BLOCKED: zero prices and provenance |
| `binance_USDT_BTC.bin` | 8,192 | 8,192 / 0 | 0 | BLOCKED: identity/collection provenance |
| `bingx_USDT_BTC.bin` | 32,768 | 32,768 / 0 | 0 | BLOCKED: identity/collection provenance |
| `bitfinex_USD_BTC.bin` | 8,192 | 8,192 / 0 | 2,534 | BLOCKED: gaps and provenance |

Accepted coverage is inclusive first/last minute: Binance
2017-08-17 04:00 to 2017-08-22 20:31 UTC; BingX 2026-07-29 00:09 to
2026-08-20 18:16 UTC; Bitfinex 2015-01-01 00:00 to 2015-01-08 10:45 UTC.
Filename market mappings remain hints, not approved historical identities.
The four unique windows total 57,344 records / 1,376,256 bytes; readers revisit
those windows for checks. No full dataset conversion or whole-file hash is claimed.

Window SHA-256 values, also in the external manifests:

| Window | SHA-256 |
| --- | --- |
| BigONE BOX | `cae1ce25cd5f364caf598255670440ef269367d9ae33739248d7b89524c164af` |
| Binance BTC | `e5ec25c0376679c32c315ad11f57c56812df4c902ebbd56b8fb1237a0fe5e667` |
| BingX BTC | `6bec094c0083a00f5b4a7ee9383d95340e233f18870525dd5f0ea0622749a528` |
| Bitfinex BTC | `d4dcfdb3d79cb05c7e016fb7bc02dff74a237362802b95c35fe7cc0278438461` |

## Bounded storage measurement

The final benchmark used the **32,768-record BingX window**, with three passes per
reader. It permits a storage comparison while financial replay stays blocked.
All four reconstructed streams matched the same raw SHA-256 byte for byte.

Hardware evidence: x86_64 Linux 6.8.0-124-generic; AMD EPYC 7313 16-Core Processor;
reported host RAM 131,596,828 KiB. Both input and output are ext4 on device 259:2,
`/dev/nvme0n1p2`. `st_dev`, mountinfo and sysfs resolve to the NVMe controller,
model **Micron_7450_MTFDKCC960TFR**, firmware **E2MU200**. This establishes observed
NVMe backing; it does not turn a cached scan into an NVMe throughput test.
Runtime: Python 3.12.16, CCXT 4.5.91, PyArrow 26.0.1, linked SQLite 3.53.1.

| Format / reader | Bytes | Median wall ms | Worst wall ms | Median CPU ms |
| --- | ---: | ---: | ---: | ---: |
| Raw sequential | 786,432 | 12.885 | 13.516 | 12.886 |
| Raw mmap | same raw window | 12.891 | 13.044 | 12.892 |
| Arrow IPC | 790,002 | 17.066 | 17.623 | 17.077 |
| Parquet / Zstd | 442,421 | 19.395 | 20.618 | 23.788 |

Conversion took 29.799 ms. Combined derived files were **1,232,423 bytes**, well
below 128 MiB. Process lifetime peak RSS was 82,992 KiB; Arrow allocator peak was
902,528 bytes. RSS includes imports/conversion and is not per-reader attribution.
Read/decode/repack/SHA-256 timing used fixed reader order. All measured passes
reported zero input/output blocks and major faults: validation/conversion had
warmed the pages. No cache eviction, cold scan, random-month query, 100/1,000-market
merge, concurrent trading load, full replay throughput or deployment soak was
measured. Larger ADR benchmarks remain NOT TESTED.

**Decision: retain raw immutable history.** Parquet compressed this sample but
did not improve measured scan time; neither derived format justifies copying
the archive. The benchmark is not evidence of a profitable strategy.

## Acceptance coverage and practical limits

| IDs | Executed evidence | Limit of claim |
| --- | --- | --- |
| Q14 | Wrong length/endian/legacy16, NaN/Inf/zero/negative/impossible OHLC, UTC alignment, duplicate/disorder, 468-day gap, missing provenance, float32/tick mismatch, suspicious token, window bounds and changed source/symlink tests | No archive-wide clean bill, original decimal precision or independent collection provenance |
| Q15 | Strict touches do not fill; uncertainty cells respected; new orders cannot fill in their creation bar; crossed pending BUY and SELL sides cannot make a two-sided same-bar round trip | Full fills, latency, queue and volume remain model assumptions |
| Q16 | Two distinct markets with opposite marked results, exact UTC coverage, deterministic scope ties, reordered inputs, repeat/config-reload/cross-Python hashes, final partial intervals, injected fsync failure and actual SIGKILL/restart | Replay restarts from input; no resumable trading checkpoint or power-loss durability claim |
| Q02/Q03/Q05/Q10 | Existing minimum-lot reducer reused; explicit finite pool over-allocation refused; quote/base/token fees and reserves; conservation; one SELL during delayed cancel/fill; 800-bar compacted run | No actual account balances, fee tiers or capacity/queue certification |
| ADR format equality | Arrow/Parquet byte-equivalent reconstruction and identical financial replay in synthetic tests | Small warm scan only; no archive conversion recommendation |

The separate seven-minute demo is entirely synthetic. Both scenarios returned
the same financial outcome on that particular fixture; their model-bound hashes
differ. Dedicated ambiguity tests demonstrate scenario divergence. Hashes from
the reviewed demo match across Python 3.12/3.13:

- Strict: `5b57fce6de064457cf50ac1b1a2315ee75ee6a41b650834e1f500f1464c633d0`.
- Touch: `5d29a4eecf0c4971afbf454d1780f5c74e57f449c04339650f4f2fe607b889ee`.

Reports explicitly mark open inventory at close, exclude hypothetical exit
costs, retain unfinished orders and leave profit factor undefined without enough
realized losses. They are not I7 executable portfolio NAV or evidence of an
achievable return. No actual historical PnL result was published because the
archive provenance gates remain unresolved.

## Reproduce, rollback and next gate

Use README's `demo`, `replay` and bounded `validate` commands with a new outside-Git
directory. Use `benchmark` with the same BingX arguments and 32,768 rows to repeat
the sample; wall time/cache conditions can differ. The external configs and
source-fingerprint file reproduce the recorded synthetic hashes on this host.
Review `I5-DESIGN.md` before changing model assumptions or declaring provenance.

Rollback is scoped code/docs/dependency revert with the matching lock. There is
no ledger migration or deployed service. Generated samples remain disposable
derived artifacts outside Git; no automatic deletion of originals is authorized.

Recommendation: **GO to review of `I6-PLAN.md`**, subject to separate approval.
HOLD actual historical performance claims until collection/market provenance is
resolved; HOLD live trading for account/execution/fees, I7 NAV, I8 soak, hardware
acceptance and explicit I9 canary signoff. No additional exchange access is
authorized by the I5 completion or GitHub publication.
