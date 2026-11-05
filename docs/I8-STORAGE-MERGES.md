# I8 follow-up — bounded multi-source storage merges

Implemented 2026-11-05 from base `ffd02ed495de86536a21eaa4bc7497350bc6aaf1`
on `i8/public-paper-hour`, within the owner's instruction to continue offline
development after the completed public hour. This increment addresses the
100/1,000-symbol chronological format comparison in ADR-0001. It adds no exchange
access, trading interface, dependency, financial migration or deployment.

## Behavior and bounds

`gridctl bench-storage --merge-plan PLAN.json --code-revision REV --output RESULT`
compares raw mmap, Arrow IPC and Parquet using a declared roster of immutable
source windows. `python -m grid.research benchmark` accepts the same arguments.
The new result schema is `grid-storage-merge-v1`, inside the existing
`grid-research-run-v1` CLI envelope. Single-source scans/queries retain their
existing schemas and default behavior. Merge and single-source arguments cannot
be combined. `--repeats` is 1–10, default three, in both modes.

The strict plan has exactly `schema` and `sources`; each source contains the
existing ten source fields. Paths have the existing working-directory semantics.
For example, a caller-provided synthetic fixture can be described as:

```json
{
  "schema": "grid-storage-merge-plan-v1",
  "sources": [{
    "path": "/tmp/my-fixture.bin",
    "venue": "synthetic", "symbol": "TEST/USD",
    "offset": 0, "count": 1024,
    "format_evidence": "generated little-endian <qffff fixture",
    "identity_evidence": "invented symbol",
    "collection_evidence": "offline synthetic generation",
    "unleveraged_spot": true
  }]
}
```

This example does not create or attest to any actual file or market. Unknown
provenance should remain null, with `unleveraged_spot: false` when unverified.
As in the existing storage benchmark, gaps and unknown identity/provenance remain
visible in manifests and do not prevent a storage comparison. They still prevent
eligible trading replay. Invalid, duplicate, disordered or malformed selected
records refuse the benchmark. No missing minute is filled or interpolated.

- At most 1,000 distinct `(venue, symbol)` entries and distinct physical files;
  hard-link/path aliases cannot inflate the symbol count. Each selected source
  contains 1–131,072 records; the total cannot exceed 1,048,576. Input-plan size
  is at most 4 MiB, with duplicate/extra JSON fields refused. Structural workload
  checks precede source reads; validation precedes derived writes.
- Sources are normalized lexicographically by `(venue, symbol)`. The merge orders
  by UTC timestamp, then that roster index. Equal timestamps from different
  sources remain distinct. Original offsets, unequal lengths, gaps and last
  partial blocks are preserved.
- Conversion processes one source at a time with 256-row batches/row groups.
  A heap merge retains one packed block per source and one next-row heap entry.
  Every block read closes before yielding rows. There is no reader held open
  per symbol, persistent decoded cache or parallel Parquet column decoding.
- Packed record buffers have a conservative bound of `sources * 256 * 24`
  bytes: 6,144,000 bytes at 1,000 sources. Heap/generator/manifest metadata,
  transient copies, decoder buffers and report objects are additional. This is
  not an RSS quota. Conversion and hashing retain bounded transient allocations.
- Derived bytes are checked against 128 MiB after each completed source pair;
  this is a refusal threshold, not a filesystem quota during the current write.
  Complete report JSON is capped at 16 MiB. Failed artifacts remain for review.
  No partial successful report is published and existing outputs are never
  overwritten. Output protection includes Git, original data and public evidence.
- Conversion must match each validated raw-window hash. Every merge repeats that
  per-source check. A raw oracle defines the expected count, UTC endpoints and
  ordered checksum; every format and repeat must match all of them. Hash framing
  is the four-byte little-endian roster index followed by the 24 record bytes.
  The roster and manifest hashes accompany it. Tests also use an independent
  full-sort oracle, rather than reusing the production heap implementation.
- Input and derived identities are checked between phases/readers and before
  return. Byte hashes detect changed selected content; metadata checks detect
  observed replacement/write changes. These observations are not filesystem
  snapshot isolation or a guarantee of detecting every concurrent same-byte write.

## Measurement and observed evidence

Each timed merge includes opening, bounded reads, decode/repack, heap work,
per-source hashing, combined hashing and closing. Identity checks and preparation
are outside each reader timing. Validation, both derived conversions together
and the raw oracle have separate times; complete CLI elapsed time includes
provenance/report publication. Reader order rotates between repeats. Reopening
columnar metadata for every 256-row block is an explicit memory/descriptor
tradeoff; these results do not compare every possible format-reader design.

Validation/conversion/oracle warm caches before measured merges. No cache
eviction or device tuning was attempted. Logical selected bytes, process I/O
block counters and lifetime peak RSS have distinct meanings. No kernel CPU,
RSS, device-I/O or cgroup quota is imposed by this command.

Retained artifacts: `/tmp/grid-i8-storage-merge-evidence/`, including 1,000
invented input files, their hashes/mtimes, separate `100/` and `1000/` plan,
report, CLI receipt and derived-sample directories, plus `summary.json`.
Generator/runner: `/tmp/grid-storage-merge-evidence.py`.
All generated input bytes and modification times were unchanged after both runs.
Original archive, legacy code and public-hour evidence were untouched.

Each file has 1,033 rows, with 1,024 selected at offset seven. Timestamp shifts
by symbol and a five-minute gap exercise overlap and irregular coverage; source
prices vary across symbols and rows. This is about 17 hours per source, not a
month, representative archive sample or target-venue market evidence. Three
repeats per format were measured in fresh CLI processes before full test suites.
All per-source and chronological hashes matched.

| Sources / selected records | Reader | Median wall ms | Worst wall ms | Median CPU ms |
| --- | --- | ---: | ---: | ---: |
| 100 / 102,400 | Raw mmap | 142.653 | 143.601 | 142.619 |
| 100 / 102,400 | Arrow IPC | 195.593 | 198.970 | 198.757 |
| 100 / 102,400 | Parquet | 238.730 | 246.902 | 238.382 |
| 1,000 / 1,024,000 | Raw mmap | 1,600.915 | 1,615.941 | 1,600.210 |
| 1,000 / 1,024,000 | Arrow IPC | 2,017.522 | 2,021.198 | 2,034.123 |
| 1,000 / 1,024,000 | Parquet | 2,567.302 | 2,568.511 | 2,566.144 |

| Observation | 100 sources | 1,000 sources |
| --- | ---: | ---: |
| Complete CLI elapsed seconds | 3.175540 | 30.764739 |
| Validation seconds | 0.770138 | 7.649714 |
| Combined conversion seconds | 0.215068 | 1.862903 |
| Raw oracle seconds | 0.143100 | 1.610265 |
| Process lifetime peak RSS KiB | 73,364 | 84,668 |
| Raw selected bytes | 2,457,600 | 24,576,000 |
| Arrow derived bytes | 2,670,600 | 26,706,000 |
| Parquet derived bytes | 964,006 | 9,640,110 |

All timed merges reported zero input blocks, output blocks and major faults.
The host observed AMD EPYC 7313, x86_64, Linux 6.8.0-124-generic, 131,596,828 KiB
host memory and ext4. Kernel device evidence linked `259:2` to `nvme0n1p2`,
Micron_7450_MTFDKCC960TFR, firmware E2MU200. These cached measurements do not
certify physical device throughput or an untested deployment host. Runtime was
Python 3.12.16, CCXT 4.5.91, PyArrow 26.0.1 and SQLite 3.53.1.

Ordered hashes for 100/1,000 sources respectively:
`76ddbbc8f5c6a8580c9d01ed4230f25cf895b429a607a5fac2d24c3a2dac0857`;
`e3e085543c293cd722735663cf76db013e6317344f825d5b4557c1c2c9e2b7e0`.
Input-manifest hashes:
`6d5527d344257aec96804a3ba84866504260ab832f2f1c709e43d3b632f46183`;
`49860f9ebaaabcd394d7f9889a538d6d5abb7a6064dd5b2a6830a835ff678dea`.
The evidence supplied base `ffd02ed`; its envelope correctly reports the
application additions as differing from that base. Measured source SHA256:
`a77a1ca0c184dd26120b8e5fdaa72f3d1c14cdf52188bb26f4ded97146ff3fb2`.

Decision: retain raw. It was fastest in both tested workloads; smaller Parquet
files do not establish a merge benefit. No whole-archive conversion is justified
by these measurements. No strategy/accounting return is derived from this test.

## Validation, reproduction and remaining work

The 38 new cases in `tests/research/test_merge_benchmark.py` cover the independent sorted oracle,
gaps/offsets/ties/partial blocks, input-order invariance, source/derived mutation,
conversion and merged-hash corruption, strict plans/work/output caps, no retained
file between yields, sanitized CLI failures, exclusive results and 1,000 sources
under a child-only 64-file-descriptor limit. Existing scan/query/CLI tests remain.
The focused group passed 96 tests in 4.79 seconds. An earlier test-only failure
used a same-byte write whose mtime was coalesced within a filesystem tick; the
fixture now explicitly advances mtime, making the intended identity fault
deterministic. No retry or sleep is used to hide that timing assumption.

Local quality/security commands completed:

- `uv run --locked ruff check .` and `ruff format --check .`: pass; 245 files
  formatted. `uv run --locked mypy`: pass, 184 application/test files.
- `uv run --locked bandit -q -r src scripts`: pass.
- `uv lock --check`: pass, 66 packages; `pyproject.toml` and `uv.lock` unchanged.
- `uv export --locked --all-groups --no-emit-project --format requirements-txt`
  followed by `pip-audit --strict --disable-pip --no-deps -r` the exported file:
  no known vulnerabilities found in this check. Export/log are outside Git.
- `python -m detect_secrets.pre_commit_hook --no-verify --cores 2 --baseline
  .secrets.baseline` over all 255 tracked/new files: pass with unchanged baseline.
- All four imported specifications and original attachments still match the
  recorded SHA256 values. `git diff --check`: pass.

Complete suites ran in the existing locked environments, with application source
frozen throughout:

| Python | Result | Pytest elapsed seconds |
| --- | --- | ---: |
| 3.12.16 | 1,152 passed; zero failed/skipped | 652.29 |
| 3.13.18 | 1,152 passed; zero failed/skipped | 687.58 |

Commands were `env UV_PYTHON=3.12.16 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py312
/tmp/grid-tooling/uv run --locked python /tmp/grid-storage-merge-check.py` and
the corresponding 3.13.18/py313 command. That wrapper invokes
`python -m pytest -q`, retains output and checks source SHA256 before/after.
Both hashes match the measured source hash above. All process handles completed.
Receipts/logs are in `/tmp/grid-i8-storage-merge-final-checks/`, with stems
`3.12-1793838943288051016` and `3.13-1793838943289520451`.
Wrapper elapsed times were 653.040907 and 688.121271 seconds respectively.

Wheel and source distribution build with `uv build --no-sources` into
`/tmp/grid-i8-storage-merge-dist/`. Byte verification covers all 84 application
modules in the wheel and 250 required source/test/doc/root-manifest files in the
source distribution, with no datasets/financial database payloads. The final
verification receipt, including both archive hashes, is
`/tmp/grid-i8-storage-merge-package-verification.json`; verifier:
`/tmp/grid-storage-merge-packages.py`. Package verification follows final docs.

To repeat a retained case, use its plan and a new output filename outside Git:

```sh
uv run --locked gridctl bench-storage --merge-plan /tmp/grid-i8-storage-merge-evidence/1000/plan.json --repeats 3 --code-revision "$(git rev-parse HEAD)" --output /tmp/grid-merge-repeat/report.json
uv run --locked pytest -q tests/research/test_merge_benchmark.py tests/research/test_storage_queries.py tests/research/test_benchmark_cli.py tests/research/test_gridctl.py
```

The implementation touches `research/merge_benchmark.py`, CLI routing and an
optional block-size argument on the existing strict columnar reader. It adds no
financial side effects. Rollback is an ordinary revert of this increment after
review; no financial migration or artifact deletion is required. Preserve
evidence and raw inputs. A scoped commit is used under the established authorized
commit/push workflow; a PR is not created. `[skip ci]` avoids retrying the unchanged
hosted-CI account lock. GitHub Free only; local checks are not a hosted-CI pass.

This closes the bounded synthetic 100/1,000-source format-merge measurement gap.
Representative archive/month sampling, cold-device measurements, repeated
parameter replay reuse, historical fee/rule/FX and survivorship evidence,
sustained mixed-side contention and deployment resource quotas remain open.
Original 48-hour observed I8 acceptance, intended-venue/account validation, I9
and deployment remain separately gated. The completed public-hour authorization
is not reused. Continue the remaining authorized offline work.
