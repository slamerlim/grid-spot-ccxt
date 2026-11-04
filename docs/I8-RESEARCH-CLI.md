# I8 offline follow-up — research commands

Later compatibility note: `I8-RESEARCH-PROVENANCE.md` records the subsequent
`grid-research-run-v1` CLI envelope. The engine result described below is now
under `result`; its deterministic bytes/hash remain unchanged. Whole CLI report
bytes include separately hashed observations and are no longer identical across
runs. The evidence below describes this increment at its original revision.
The subsequent optional `--portfolio` mode is described separately in
`I8-PORTFOLIO-RESEARCH.md`; it creates a temporary mock financial ledger and
uses a distinct inner result schema. The default mode remains as described here.

Base: `fee2cdc39067623cd1955e72e928386634866fab`, branch `i8/public-paper-hour`.
This implements the research command names in PRD §10 under the owner's
authorization to continue mock development. All input fixtures in this increment
are synthetic. No exchange request, original dataset conversion, account access,
service installation or deployment is involved. GitHub remains Free-only;
hosted CI is pending under the unchanged account lock. Use `[skip ci]` publication.

## Interface and scope

| Main CLI | Existing equivalent | Input and output |
|---|---|---|
| `gridctl backtest --config BUNDLE --output NEW_REPORT` | `python -m grid.research replay` | Strict explicit research JSON bundle; deterministic OHLC report |
| `gridctl bench-storage SOURCE --venue VENUE --symbol SYMBOL --count COUNT --code-revision REVISION --output NEW_REPORT` | `python -m grid.research benchmark` | Bounded raw window; raw/Arrow/Parquet measurement and retained derived samples |

Both entry points call the same engines and use the same options. Root and
command-specific help list them. The module's `validate` and `demo` commands
remain available. This does not add a second ledger writer, financial authority,
exchange transport, scheduler or background task. Replay accounting/schema and
canonical result bytes are unchanged for identical input. Benchmark reports add
an explicit concurrency disclosure and disable Parquet parallel column decoding.
Dependencies, lockfile and SQL schema remain unchanged.

Replay permits at most 64 allocations, 192 explicit capital pools, 1,000,000
minutes and 10,000 report intervals. Configuration is limited to 1 MiB and each
selected raw window to 1,000,000 records. The format benchmark permits at most
131,072 records (3 MiB raw), three repeated reads per format through this CLI,
4,096-row columnar batches and a 128 MiB derived-output cap. These are input/output
bounds, not measured worst-case RAM/CPU or OS quotas. A standalone research
process does not automatically yield to a financial owner. ADR contention and
deployment resource validation remain unfinished.

The benchmark can succeed on structurally valid bytes with unknown identity or
collection provenance. Its input manifest retains those blockers; exit 0 does
not mean eligible for financial replay. Read timings include decoding, repacking
and hashing in a fixed reader order. Validation/conversion warm the input; cache
state is uncontrolled. Lifetime RSS includes imports/conversion. Neither this
increment nor its seven-bar synthetic test repeats or extends the historical I5
NVMe measurement. Compression size alone does not justify archive conversion.

## Failure handling and retained outputs

Before loading a replay bundle or converting a sample, the CLI checks the output
directory and refuses a known existing report or symlink, including dangling
links. Outputs must remain outside Git, original data/legacy roots and
`/opt/grid-runs/i8-public-hour`. This early check avoids known unnecessary work;
the existing final exclusive hard-link publication still resolves races without
overwriting a competing result. Tests do not write into the public evidence tree.

Argument errors and caught execution exceptions use fixed messages without
echoing untrusted arguments, paths or third-party exception text. Success output
is generic; inspect the explicit report path supplied by the caller. A report
may already exist when stdout fails, so exit 2 says to inspect artifacts rather
than claiming no output was published. A caught interrupt returns 130. Validation
retains its existing 0/1/2 eligible/blockers/failure convention.

Publication fsyncs a private temporary sibling before linking the final name;
it does not claim power-loss durability for directory entries. SIGKILL or an
interrupted conversion can retain temporary files or an incomplete sample
directory. Inspect them and use new output names for any reviewed retry; there
is no automatic deletion or retry. Source bytes and modification time remain
unchanged. These path checks do not isolate a hostile process with the same OS
identity; use private output directories.

## Validation and remaining work

`tests/research/test_gridctl.py` adds 17 cases: root/legacy result equivalence,
separate CLI processes for replay and format sampling, original-byte/mtime
preservation, refused existing/symlink/Git/public-evidence destinations before
engine work, sanitized argument/config/library failures, help without artifacts,
lost stdout after publication, and interruption without a success result.
Existing tests cover data validation, accounting, format equality, source changes,
ambiguity and actual SIGKILL/restart. The initial research/doctor selection passed
93 tests before the two additional interruption/output tests; final full-suite
results below include all cases.

| Final check | Result |
|---|---|
| Full pytest, Python 3.12.16 | **841 passed**, 230.43 s; no failures/skips |
| Full pytest, Python 3.13.18 | **841 passed**, 238.05 s; no failures/skips |
| Ruff lint/format | Passed |
| Strict mypy | Passed, 152 source/test files |
| Bandit (`src`, `scripts`) | Passed |
| Secret scan, including staged additions | Passed; baseline unchanged |
| Lock and dependency advisory audit | Unchanged 66-package graph; no known vulnerabilities found |
| Original/imported specifications and public journals | Recorded hashes unchanged |
| Hosted CI / exchange execution | Not run; unchanged account lock and authorization boundaries |

Application-source SHA-256:
`bf25aa57b12ce8c1a7b5a5ff37a12d6dedbf853cf6ce46c13cea3504a15cd96a`.
Wheel/sdist destination: `/tmp/grid-i8-research-cli-dist/`, outside Git. Packaging
verification compares all 69 application modules and the final documentation/test
bytes against the working tree; generated databases/journals are excluded.

The current requirement review is in `IMPLEMENTATION-REVIEW.md`. In particular,
historical replay still uses at-close NAV without the portfolio exit controller,
has two sensitivity scenarios and lacks the full PRD research comparison suite.
It does not automatically verify a caller-supplied code revision. Nothing in
this command integration certifies profitability or closes those requirements.

Rollback can revert this CLI increment without a database migration. Preserve
all reports/configurations and public evidence; no cleanup or active ledger
downgrade is part of rollback. Existing mock-session source-digest rules still
require the original compatible runtime when resuming an earlier session.
