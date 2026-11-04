# I8 offline follow-up — audited backup and restore verification

Base: `6fb080eb3b36edf33dc9fdef38c24e23f3a4f9f6`, branch `i8/public-paper-hour`.
This implements PRD §10 storage commands under the owner's authorization to
continue mock development after the completed public hour. No further exchange
access, credential discovery, trading, transfer or deployment is involved.
GitHub remains Free-only. Hosted CI is pending under the unchanged account lock;
publication uses `[skip ci]` without retrying it.

## Commands and results

All commands use the existing same-user Unix socket of a finite mock supervisor.
The client never opens the financial database. `backup` and `restore-verify`
require the current operational revision, actor derived from the peer UID,
reason, private saved request and a second invocation with `--confirm`.
They act on the complete ledger and accept no allocation selector.

| Command | Arguments beyond the common control fields | Behavior |
|---|---|---|
| `backup` | `--output NEW_BACKUP --verify-to NEW_RESTORE` | Ask the sole writer for a consistent SQLite backup, then restore it into a separate new file and verify it |
| `restore-verify` | `--source BACKUP --destination NEW_RESTORE --digest SHA256 --requests COUNT` | Check an existing backup against its reviewed semantic hash and request watermark, then verify a separate restored copy |
| `storage-status` | `--request-id SAVED_ID` | Read the durable intent and result without executing another copy |

Use existing private directories outside Git and the preserved data/evidence
trees. No destination is overwritten, repaired, renamed into service or deleted
on failure. Existing destination files, symlinks, hard links, SQLite sidecars and
owner files are refused. Active ledger paths are excluded. The existing trusted
same-UID/private-directory boundary remains; these controls do not isolate a
malicious process already running as the owning OS user.

The saved wire plan calls the backup file `source` and the verification copy
`destination`. For `backup`, that source is a **new output**, selected with
`--output`; the active source database is always the owner's current ledger.
The success result's `notices` contain `[semantic_hash, request_count_string]`.
The hash covers canonical ordered business/audit rows, not raw database bytes.
Retain that result, both artifacts and the saved request together.

`storage-status` returns `not-found`, `incomplete`, `failed` or `verified`.
`incomplete` means durable intent exists without a terminal receipt; work may
still be running, or an interruption may have stopped it. A verified receipt is
historical evidence, not a fresh check that the artifact remains present and
unchanged. To check it again, prepare a separate `restore-verify` request with a
new destination and the retained hash/watermark. A foreign or older backup may
be verified, but that never refreshes the active owner's backup-freshness stamp.

## Persistence and interruption behavior

The reviewed plan, actor/reason/time, revision check and operational audit are
committed before filesystem work. Storage output does not occur inside a SQLite
write transaction. The existing writer serializes the online backup with
financial posting; an independent bounded thread performs the separate restore.
A second transaction records successful verification or a fixed failure result.
Only verified backups created by this owner update its backup stamp. The stamp
uses the operation's conservative start time, not a later completion time.

The snapshot contains its preparation receipt. It necessarily predates the
completion receipt and may predate concurrent fills and controls. Those later
facts remain in the active ledger. A restored owner must reconcile external
truth; a consistent backup is not proof that no subsequent trading occurred.
The verification command does not activate a restored owner. Normal owner startup
performs the separate schema, journal/projection and recovery checks.

Result posting preserves the latest halt/held controls and does not reset a
concurrent command. The original plan remains in the durable event even when a
later halt becomes the current command. Storage operations have their own path;
ordinary control dispatch cannot skip verification by posting a backup action.

There is at most one legacy backup or audited storage job at a time. Existing
copy/restore deadlines, bounded SQLite page batches and writer queue limits apply.
For the finite fixture each copy has a 5-second deadline; IPC dispatch has a
10-second deadline and the client a 15-second timeout. Slow I/O or queueing can
therefore produce an unknown response while admitted work continues. Cancellation
detaches the caller; shutdown joins storage work before releasing the writer.
These software deadlines do not guarantee interruption of a blocked kernel I/O.

Neither client nor owner automatically restarts a saved storage job. Exact replay
returns its retained terminal result, or `incomplete` without another copy. A
reused identity with changed contents conflicts. An interruption after preparation
but before the terminal commit never yields a verified result, even if both files
exist. Failed verification retains artifacts, leaves freshness unchanged and
halts admission. A lost acknowledgement after the terminal commit can be resolved
by inspecting/replaying the same request. No later halt is cleared by that replay.

Before deciding on another attempt, inspect `storage-status`, owner status and
retained files, and ensure the earlier job has stopped. Use a new reviewed request
and new paths; never alter a saved identity or delete artifacts to force a retry.
The original incomplete/failed audit remains. A resumed mock session requires the
same source digest and begins halted under the existing session contract.

## Compatibility and evidence

SQL schema v5 and dependencies are unchanged. The codec adds an allowlisted
`StoragePlan` and two operational action values. Older runtimes cannot interpret
new storage records; do not downgrade an active ledger or edit session markers.
Rollback preserves artifacts, requests and newer history, with a compatible
runtime or separately verified pre-change backup in a disposable location.

The initial expanded storage selection passed **36 tests in 7.62 seconds**,
including existing backup regressions. A later test also checks incomplete-job
replay and a conflicting result identity. These are part of the full suites below,
not additional independent acceptance totals.

| Requirement | Evidence |
|---|---|
| PRD §10: reviewed storage commands | Two-step confirmation, strict fields/types, saved request, same-UID actor, revision and reason audit; separate CLI process and restart |
| ADR-0001 / Q19: consistent recovery | Hash/watermark, schema, integrity and foreign-key verification; unresolved order retained after opening a restored owner |
| Q05/Q09: concurrent truth | Fill posted while independent restore is waiting remains in active cash/base; older backup retains earlier unresolved order |
| Q19: interruption boundaries | Ten real SIGKILL cases: preparation posting/before/after commit, prepared intent, backup page, restore page, verified files, result posting/before/after commit |
| Q19: no overwrite/false readiness | Existing files, symlinks, hard links, public directory, sidecars, owner files, active ledger and Git destinations refused; corruption fails without refreshing freshness |
| PRD §10: recovery controls | Detached work, one-job bound, concurrent halt preservation, lost result acknowledgement, completed and incomplete exact replay |

| Final check | Result |
|---|---|
| Full pytest, Python 3.12.16 | **806 passed**, 193.90 s; no failures/skips |
| Full pytest, Python 3.13.18 | **806 passed**, 200.49 s; no failures/skips |
| Ruff lint/format | Passed |
| Strict mypy | Passed, 149 source/test files |
| Bandit (`src`, `scripts`) | Passed |
| Secret scan, including staged additions | Passed; baseline unchanged |
| Lock and dependency advisory audit | Unchanged 66-package graph; no known vulnerabilities found |
| Original/imported specifications and preserved public journals | Recorded hashes unchanged |
| Hosted CI / exchange execution | Not run; account lock and authorization boundaries unchanged |

Application-source SHA-256:
`18d68d81c50d3fb90236db62d11b7978842e3af254680515c45c31ead24836dd`.
Wheel/sdist destination: `/tmp/grid-i8-storage-controls-dist/` (outside Git).
There are 69 application modules; generated databases and reports are excluded.

No mock check establishes production storage performance,
intended-venue readiness or completion of the original observed 48-hour gate.
Scoped dust completion and research/storage command integration remain. Private
account access, further public runs, I9 and deployment retain their separate gates.
