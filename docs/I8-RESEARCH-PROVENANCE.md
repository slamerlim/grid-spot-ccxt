# I8 offline follow-up — research run provenance

Base: `5637656b1171085681372e5c6bd9aceef3ca5bbf`, branch `i8/public-paper-hour`.
This implements the source/runtime reporting part of PRD §9 and the current
requirements review under the owner's continued offline-development approval.
No exchange calls, credentials, live/sandbox orders, transfers, installed service
or deployment are involved. GitHub remains Free-only; hosted CI is pending under
the unchanged account lock. Publication uses `[skip ci]`.

## Report contract and compatibility

The `replay`, `validate` and `benchmark` research CLI operations now publish a
`grid-research-run-v1` envelope. The `gridctl backtest` and `bench-storage` aliases
use the same contract. `demo` still emits its existing synthetic source/config.

| Field | Meaning |
|---|---|
| `schema`, `operation` | Explicit envelope version and research operation |
| `result` | Unchanged engine result object, including replay's existing `result_sha256` |
| `result_payload_sha256` | SHA-256 of the complete canonical `result`, including any inner result hash |
| `provenance.source` | Actual source-file hashes, modes/sizes, dependency-manifest hashes and aggregate Python source digest |
| `provenance.git` | Observed local HEAD, comparison scope, supplied-label/source matches and explicit verification status |
| `provenance.runtime` | Python implementation/version, linked SQLite, named installed distribution versions and dependency-verification limitation |
| `provenance.host_at_start` | OS/kernel/architecture, logical/affinity CPU counts, CPU model and host RAM where observable |
| `provenance` timing fields | UTC endpoints, monotonic wall duration and process CPU, with measurement scope |
| `report_sha256` | SHA-256 of the complete canonical envelope excluding only this field |

Consumers must check the new outer schema and access old fields under `result`.
An old result's schema/hash must not be interpreted as an envelope hash. Existing
retained files are untouched. Direct Python engine APIs still return their
original result objects; they do not silently acquire environmental observations.
No financial formula, replay scenario, dependency pin or database schema changed.

The deterministic replay result binds its existing model/config/data inputs.
Its bytes and hash are unchanged for the same inputs. Run timestamps and host
observations intentionally make the outer report different on repeated runs.
Compare inner results for accounting equality and outer hashes for the integrity
of a particular evidence record. SHA-256 binds contents; it is not a signature or
proof that an untrusted author ran the program.

## Actual source and local Git comparison

The source snapshot traverses the executing installation's `grid` package with
bounds of 512 Python files, 4,096 examined entries, 1 MiB per source/manifest file
and 16 MiB aggregate Python bytes. Symlinks, nonregular Python files and unsupported
names are refused; `__pycache__` is not source. Raw bytes are checked for changes
while reading. The aggregate Python SHA-256 uses the existing operational
path/NUL/bytes/NUL algorithm in sorted relative-path order. Per-file SHA-256,
Git-compatible blob identity, size and executable mode are retained without
embedding source text or absolute installation paths.

In a `src/grid` layout, `pyproject.toml` and `uv.lock` are fingerprinted if
present. Both are required for a successful Git comparison. For a source checkout,
two fixed local plumbing commands read the current HEAD and its source tree.
Current Python files, including untracked additions, must exactly match that
commit's Python path/blob/mode map and dependency manifests. A supplied label
must equal the full observed 40-character HEAD SHA for `revision_verified=true`.
Branch names, arbitrary labels, changed files and missing manifests cannot pass.
Revision labels must be printable and at most 256 characters. Other Git object
formats are explicitly unavailable in this version.

The comparison excludes docs/tests and other repository files, so it does not
claim whole-repository cleanliness. It also does not verify installed dependency
code, native libraries or the loaded interpreter/bytecode. Installed packages
without the source checkout still receive actual source hashes and an explicit
`no_source_checkout` Git status. Missing/broken Git or unsupported/missing objects
produce `unavailable`, not verified. Stable mismatch/unavailability does not
forbid synthetic research; it prevents a false claim of verified commit identity.

Source, manifest, runtime and Git observations are collected again after the
engine finishes. Any difference refuses final publication. Derived samples may
remain for inspection. Before/after snapshots cannot detect an edit made and
reverted between snapshots, attest to loaded modules, or defend against a hostile
process with the same OS authority. Do not edit application source during a run;
use a dedicated fixed checkout for evidence intended to be reviewed.

## Process, resource and information boundaries

The Git helper admits only its two exact plumbing argument patterns, uses the
fixed `/usr/bin/git` executable, no shell, a 256 KiB output cap and a two-second
deadline per command. On interruption/timeout/overflow it kills and reaps its
owned process group; cleanup has its own two-second wait. Stderr is discarded
instead of echoing repository/configuration details. No retries are performed.
These software deadlines do not guarantee interruption of blocked kernel I/O.

The child receives an explicit minimal environment, with no inherited credentials,
Git overrides or trace settings. System/global Git configuration is disabled;
local repository configuration still exists, but hooks/fsmonitor and replacement
objects are disabled for these reads. An empty `GIT_ALLOW_PROTOCOL` denies every
transport even if repository configuration enables one. This uses the documented
override in the [Git 2.43 environment contract](https://git-scm.com/docs/git/2.43.0#Documentation/git.txt-codeGITALLOWPROTOCOLcode).
The helper issues no fetch/push/credential commands and includes no remote settings
in reports. Git's attempted lazy fetch for a missing promisor object is denied at
transport selection. The static import boundary grants
only this module a subprocess exception; runtime tests exercise its restrictions.
Bandit has two narrow reviewed annotations for that import and fixed-argument
process creation; the rest of its checks remain enabled.

Host collection reads only fixed bounded `/proc` fields and basic OS metadata.
Missing CPU model/RAM observations remain null. No hostname, user identity,
environment dump or network interface inventory is included. Distribution versions
are limited to the application, CCXT, aiohttp, PyYAML and optional PyArrow. The
profile describes the current host, not effective container CPU/RAM/IO quotas
or an approved deployment budget. Timings include the engine and evidence capture;
they exclude initial config loading and report serialization/publication. Process
CPU excludes Git child CPU. Source/report bounds do not implement research
yielding or complete the contention requirement.

## Tests, validation and rollback

The 32 new provenance cases cover clean temporary Git repositories; changed source,
manifests, executable mode, added/removed files; incorrect revision labels;
explicitly scoped docs exclusions; isolated Git environment and unchanged index;
replacement refs; denied promisor transport; unavailable Git/installed packages;
source/HEAD/runtime changes during work; refused CLI publication; separate host
and financial hashes; source-digest compatibility; symlink/FIFO/size/count limits;
child timeout/overflow/reaping; command allowlist; and envelope tampering. Existing
CLI/replay/accounting/format/crash tests remain required. The focused selection
passed **95 tests in 3.32 seconds**, before the final timing-scope clarification;
the full suites below validate the final source.

| Final check | Result |
|---|---|
| Full pytest, Python 3.12.16 | **873 passed**, 231.92 s; no failures/skips |
| Full pytest, Python 3.13.18 | **873 passed**, 242.61 s; no failures/skips |
| Ruff lint/format | Passed |
| Strict mypy | Passed, 155 source/test files |
| Bandit (`src`, `scripts`) | Passed with the two narrow subprocess annotations described above |
| Secret scan, including staged additions | Passed; baseline unchanged |
| Lock and dependency advisory audit | Unchanged 66-package graph; no known vulnerabilities found |
| Original/imported specifications and public journals | Recorded hashes unchanged |
| Hosted CI / exchange execution | Not run; unchanged account lock and authorization boundaries |

Application-source SHA-256:
`17d87518b8a1e8a459b148802d26184c26c0dbc1c7ce095c15e1c71d23039060`.
Wheel/sdist destination: `/tmp/grid-i8-provenance-dist/`, outside Git. Packaging
verification compares all 71 application modules and final documentation/test
bytes against the working tree; generated databases/journals are excluded.

Rollback can revert this reporting increment without a financial migration.
Preserve new and old reports separately and use a consumer that understands their
schemas. Do not rewrite evidence or downgrade an active mock ledger; existing
session source-digest compatibility requirements still apply. The next offline
work is full-cycle portfolio replay, comparison/metric coverage and constrained
research/fill contention. No additional exchange access is authorized. Full I8
observed acceptance, first-venue evidence and I9 remain separately gated.
