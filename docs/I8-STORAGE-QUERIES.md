# I8 follow-up — bounded UTC storage queries

Implemented 2026-11-05 from base `92cfdd47d4ed71f9778cf8feaf4f804b4668933c`
on `i8/public-paper-hour`, within the owner's instruction to continue offline
work after the completed public hour. No additional exchange requests, account
access, orders, deployment or paid GitHub capabilities are part of this change.
The original specification and acceptance gates remain unchanged.

## Behavior and measurement contract

`gridctl bench-storage` and `python -m grid.research benchmark` retain the existing
bounded full-scan benchmark. Optional UTC query arguments add a
`queries` object with schema `grid-storage-queries-v1` inside the existing
`grid-storage-sample-v1` result and `grid-research-run-v1` CLI envelope.
Without these arguments the query object is absent. No dependency, financial
record, strategy, ledger migration or live interface changes.

- `--utc-start-ms` and `--utc-end-ms` specify one half-open UTC interval; both
  endpoints must be complete minutes within the selected source's timestamp span.
- `--random-windows N --window-minutes M --query-seed S` adds explicit-duration
  windows, using versioned SHA256 seed/counter modulo available minute offsets.
  The seed is an integer from zero through `2^64-1`. This is reproducible modulo
  sampling, not a claim of unbiased sampling, calendar months or random symbols.
  Duplicate and overlapping windows remain in the result. No seed is invented.
- At most 32 total queries, 131,072 selected source records and 1,048,576 summed
  query records per repeat are allowed. `--repeats` is 1–10, default three.
  Overlapping reads count again toward the work bound. Existing derived-size
  limit remains 128 MiB. Invalid windows/workloads fail before derived writes.
- Validation rejects malformed, nonfinite, nonpositive or disordered records.
  As in the earlier storage-only benchmark, valid gaps and unknown provenance
  remain visible in the manifest; they are not converted into replay approval.
  Empty windows inside a gap return zero records and the empty checksum.
- A bounded in-memory timestamp tuple from the selected immutable raw window
  supplies the same binary-search index for all formats. Its SHA256, Python
  object-size estimate and preparation time are recorded. Independent expected
  window hashes come from the raw records. Index/hash preparation is excluded
  from query timing and included in the reported conversion time, together with
  raw loading and derived writing. The index is discarded after the run.
- Raw queries mmap only the selected row range, respecting the original source
  offset. Arrow reads only overlapping fixed 4,096-row batches; Parquet reads
  only overlapping row groups with parallel decoding disabled. Schema, row
  count and null checks reject unexpected derived layouts. This reader supports
  this benchmark's generated layout, not arbitrary Arrow/Parquet input files.
- Each timed query includes UTC lookup, open, selected read/decode/repack/SHA256
  and close. Empty windows open no file. Readers reopen for each query; no
  persistent reader or decoded-row cache is used. Every format/repeat must
  reproduce the independent raw-window checksum or the entire report fails.
- Reader order rotates across windows and repeats and is reported. Per-repeat
  wall/CPU time and process input/output blocks/major faults are retained, with
  per-window median and maximum wall/CPU summaries. Selected bytes and columnar
  block counts describe logical work, not physical device bytes. RSS remains
  the process lifetime high-water mark, including validation and conversion.
- Full validation, conversion and full scans precede queries, warming caches.
  No cache eviction is attempted. First query timing is not a cold-cache result.
  Source and derived file identities are checked before publication, including
  mutations that leave the same content. Failure retains derived artifacts for
  inspection; no successful partial report is published.

The implementation follows the pinned PyArrow APIs for
[Arrow batch access](https://arrow.apache.org/docs/python/generated/pyarrow.ipc.RecordBatchFileReader.html)
and [Parquet row-group access](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ParquetFile.html).
The fixed block layout and shared timestamp index are declared benchmark
conventions, not a new archive format or production cache decision.

## Observed synthetic sample

Artifacts are outside Git under `/tmp/grid-i8-storage-query-evidence/`:
`synthetic.bin`, `report.json`, `summary.json` and a unique derived-sample directory.
The generating/measurement wrapper is `/tmp/grid-storage-query-evidence.py`.
The original archive and completed public-hour evidence were untouched.

This invented source contains 131,087 records, of which 131,072 are selected at
row offset 13. One explicit window and 16 seeded windows each cover 44,640
minutes (31 days), totalling 758,880 selected query records per repeat.
Three repeats give 51 observations per format. Every checksum matched; source
bytes and modification time were unchanged. Complete CLI elapsed time was
6.041518 seconds, including validation/conversion/full scans/query work and
provenance reporting, measured before starting the full test suites.

| Reader | Median query wall ms | Worst query wall ms | Median query CPU ms |
| --- | ---: | ---: | ---: |
| Raw mmap | 21.212740 | 22.860269 | 21.214428 |
| Arrow IPC | 22.788062 | 28.119338 | 22.800561 |
| Parquet | 25.238431 | 28.134397 | 25.229992 |

All query observations reported zero input blocks and zero major faults.
Index/oracle preparation took 335.683 ms; conversion including that preparation
and raw loading took 468.068 ms. The index object estimate was 5,242,920 bytes;
process lifetime peak RSS was 113,696 KiB. Raw selected bytes: 3,145,728;
derived Arrow: 3,157,938; derived Parquet: 784,572. Compression size alone does
not justify conversion, and these query timings do not measure trading/replay
throughput or cold-device performance.

Observed host: AMD EPYC 7313, x86_64, Linux `6.8.0-124-generic`, reported host
memory 131,596,828 kB, ext4 source/output filesystem. Kernel device mapping
`259:2` resolves to `nvme0n1p2`, model `Micron_7450_MTFDKCC960TFR`, firmware
`E2MU200`. This is positive backing-device evidence for this host and run,
not a deployment certification. Python 3.12.16, CCXT 4.5.91, PyArrow 26.0.1,
linked SQLite 3.53.1; no exchange transport was used.

Audit hashes:

- Full synthetic file SHA256:
  `e2f57107536bde3f726770adc98a31cf3f40ac115221689810fddb65a90c9d6e`.
- Selected source window SHA256:
  `87efb8e8badebad75dffe1e15db36d7cabd7559f77a6f59c8fb71589470689aa`.
- Timestamp index SHA256:
  `c9342bd4d9f9823f89cb41793afb8233574e562460c332b43d3aedd959ed4969`.
- Report SHA256:
  `76899430fa227542aaf4e92484d277833d1f1e2d0cbfb17da727c9171bb2c7b7`.
- Application Python SHA256:
  `d8da981384af3fe7335c19cb730378594531feaa868c525700218e8bf16156e9`.

The report accurately identifies changed application source relative to its
claimed base commit; it is not presented as a clean-base run. Its source bytes
are the implementation validated for this increment.

## Reproduction and verification

For the retained sample, choose a fresh output filename/directory:

```sh
uv run --locked gridctl bench-storage /tmp/grid-i8-storage-query-evidence/synthetic.bin \
  --venue synthetic --symbol TEST/USD --offset 13 --count 131072 --repeats 3 \
  --utc-start-ms 1704067980000 --utc-end-ms 1706746380000 \
  --random-windows 16 --window-minutes 44640 --query-seed 20261105 \
  --code-revision "$(git rev-parse HEAD)" --output /tmp/grid-query-review-new/report.json
```

A minimal install needs the pinned optional `research` dependency group; the
normal development environment already includes it. Reports and derived samples
remain outside Git. An occupied result is refused before processing; inspect
failed artifacts before using a new destination.

The 33 new cases in `tests/research/test_storage_queries.py` cover exact UTC
boundaries, nonzero source offsets, two-block crossings, last partial blocks,
empty gaps, duplicate windows, deterministic subprocess schedules, selective
block access, invalid plans/work bounds, corrupted results, schema/row/null
faults and changed source/derived identities. The existing byte-roundtrip and
financial-replay equivalence tests remain in `test_benchmark_cli.py`.
The focused benchmark/CLI group passed 58 tests in 2.46 seconds.

Checks executed in the locked Python 3.12 environment:

- `ruff check .` and `ruff format --check .`: passed (241 formatted files).
- `mypy`: passed across 181 application/test files.
- `bandit -q -r src scripts`: passed.
- `uv lock --check`: 66 packages resolved, lock unchanged.
- Locked all-group export and `pip-audit --strict --disable-pip --no-deps`:
  no known vulnerabilities reported. The advisory lookup used network access;
  it was not an exchange call.
- Unchanged-baseline secret scan: passed across 250 tracked/new files.
- Wheel build: all 83 application-module bytes matched the checkout; no raw
  histories, databases or JSONL records were included.
- All four imported specifications and their original attachments matched the
  recorded hashes. Dependency manifests and secret baseline matched the base.

Full offline suites both passed, with no failures or skips:

| Interpreter | Tests passed | Pytest elapsed | Wrapper elapsed |
| --- | ---: | ---: | ---: |
| Python 3.12.16 | 1,092 | 645.60 s | 646.115 s |
| Python 3.13.18 | 1,092 | 675.05 s | 675.550 s |

Each ran `python -m pytest -q` through `/tmp/grid-storage-query-check.py`, with
`UV_PYTHON` and `UV_PROJECT_ENVIRONMENT` selecting the locked interpreters as
in README. Application source remained frozen across both complete runs and
matched the SHA256 recorded above. Durable JSON receipts and full logs:

- `/tmp/grid-i8-storage-query-final-checks/3.12-1793836262616578576.{json,log}`.
- `/tmp/grid-i8-storage-query-final-checks/3.13-1793836262607574815.{json,log}`.

Wheel and source distribution are under `/tmp/grid-i8-storage-query-dist/`.
Byte verification checks all 83 application modules and 245 required source,
test, documentation and root-manifest files against the final checkout, with
no `.bin`, database or JSONL artifacts. Verification receipt:
`/tmp/grid-i8-storage-query-package-verification.json`. No new dependency or
security-baseline exceptions were added. Hosted CI was not retried or counted
as passing.

## Remaining gates and rollback

Retain raw history. This sample provides no measured advantage for converting
it. Cold-ish/cache-controlled scans, representative multi-source/month sampling,
100/1,000-symbol chronological merges, repeated parameter replay reuse and
resource-isolated deployment contention remain open ADR work. The existing
synthetic contention experiment does not close those hardware/workload gates.
Static historical assumptions, independent-lot study integration, observed
48-hour I8 acceptance, intended-venue/account evidence and I9 remain incomplete.

Continue offline development. No further public data run or live enablement is
implied. Hosted CI remains pending under the unchanged account lock; GitHub Free
only and `[skip ci]` publication apply. A scoped commit is used under the existing
iteration workflow; no PR was created. Rollback is a normal revert of this
increment; no database migration or financial-state rollback is required.
Preserve already published reports and raw files rather than rewriting them.
