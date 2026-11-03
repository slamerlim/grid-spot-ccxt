# I2 review plan — single-owner ledger and crash recovery

**Status: implemented and locally validated; ready for review.** The owner replied
"Approve I2 with local validation" to the approval request for this published
plan. Temporary mock databases and the scope below are authorized. Hosted CI
remains pending; exchange access and live operation remain outside approval.
Imported specifications remain unchanged.
See `I2-DESIGN.md` and `I2-EVIDENCE.md` for the implementation, actual checks and
remaining limitations. I3 remains separately gated.

## Approved scope

The approved scope is I2's single-owner SQLite ledger, deterministic mock/fault tests and
temporary test databases, using local validation while hosted GitHub CI remains
pending. Retain GitHub Free only and `[skip ci]` while the known account lock is
unchanged. Stop after the I2 evidence/review handoff; I3 and later work remain
separately gated. No exchange network call, credential, order, deployment or live
financial database is included.

## Concrete scope and implementation order

1. Add a small `grid.persistence` package with versioned schema/migrations,
   strict Decimal serialization, checked SQLite connection setup, and a process
   owner guard. Use the standard library; do not add an ORM, another database or
   another lockfile. Keep `grid.domain` free of I/O.
2. Add one bounded async writer interface with a single dedicated worker thread
   owning its SQLite connection. Run short transactions serially, with no remote
   I/O in a transaction. Set Python transaction behavior explicitly; keep
   `check_same_thread=True` and create/use/close the connection in its owner.
   Caller cancellation cannot turn an uncertain commit into an automatic retry.
3. Persist normalized facts, exact financial changes, allocation revisions and
   proposed command intents atomically. Add idempotent commands to assign/release
   dedicated simulated funds and reserve them for proposed orders. Integrate the
   existing reducer through a version-checked transaction, without changing its
   strategy or approved partial-seed rule.
4. Add durable recovery and bounded state reload, then safe backup and restore
   verification. Tests use temporary paths and synthetic funds only. The default
   doctor remains read-only, creates no database and cannot enable LIVE.
5. Add deterministic integration/fault tests, update the evidence/runbook and
   publish scoped commits. Prepare the next review boundary; do not add an
   executor, CCXT client or deployment as part of this iteration.

## SQLite and ownership contract

Check the library Python actually links, not merely the Python version. Require
the PRD's patched runtime policy: SQLite 3.51.3+ excluding unapproved/withdrawn
builds, or an explicitly verified fixed 3.50.7/3.44.6 backport. The official
[WAL guidance](https://www.sqlite.org/wal.html#walreset) identifies the defect and
fixed releases. Record runtime/source/build evidence; a successful mock test is
not approval of a production build.

Set and read back WAL, FULL synchronous, foreign keys and an explicit bounded
busy timeout. Refuse unsupported/nonlocal storage evidence and malformed schema.
Use explicit transaction boundaries and version checks. This follows the
user-supplied ADR; WAL is not suitable for network filesystems. Keep readers
bounded and record checkpoint progress/WAL limits instead of allowing an
unlimited background checkpoint loop.

An OS lock and run identity guard the canonical ledger path before writer
authority is granted. Test path aliases and duplicate owners; do not use mere
PID-file existence as a lock. A restarted owner starts in recovery/reconciliation
state, even when the previous process is demonstrably gone. File/directory
permissions must restrict the mock financial state and backups to their owner.

The prepared Python 3.13.18 environment reports SQLite 3.53.1; system Python
3.12.3 reports 3.45.1 and must fail the write-runtime gate. During approved I2,
use isolated, verified free Python 3.12/3.13 builds with patched linked SQLite
for positive integration runs. Keep the old runtime as negative-gate evidence;
do not skip a required positive gate and call it passed. Do not replace the
system Python or weaken the runtime policy to make tests green.

## Records and transaction boundaries

Use one database and one schema history with migration checksums. Required
records include:

| Record | Required identity/invariant |
| --- | --- |
| Dedicated funding and allocation balances | Venue/account/currency, explicit bot-owned assignment; no external wallet import |
| Allocation state and policy snapshots | Complete scope, schema/config/market revisions, optimistic state version |
| Order/command intent and attempts | Stable scoped intent identity; one unresolved SELL slot per allocation |
| Order observations and reservations | Principal separate from fees; currency explicit; uncertainty retains the slot and reserve |
| Confirmed trades and fee postings | Unique scoped trade identity; equal replay is a no-op, conflicting payload blocks |
| Position/cost and accounting journal | Exact atomic deltas tied to the accepted fact; fees/rebates retained |
| Recovery/checkpoint/backup metadata | Durable watermark, schema/version evidence and unresolved obligations |

Represent amounts as validated canonical decimal text, never SQLite REAL or
float JSON. Perform monetary arithmetic using the exact domain helpers; do not
let SQL coercion/SUM convert text to floating point. Validate decoded rows and
snapshots as strictly as new inputs. Parameterize SQL and keep secrets out of
all tables, diagnostics and fault fixtures.

Dedicated funding has explicit ownership. Allocation cash assignments cannot
overlap: assigning funds from the unassigned account pool and reserving a seed
must be one transaction. Two symbols racing for the last available quote cannot
both acquire it. Releasing/reassigning funds requires proof that no outstanding
reservation uses them. This avoids copying the same account cash into several
independent reducer states.

A transaction commits a confirmed fill, fee changes, quantities/cost, reserve
consumption, order state, journal entry, next allocation revision and proposed
commands together. A rejected transition cannot partially debit a balance.
Persistence failures do not acknowledge event acceptance. A later synthetic
send-attempt transition records uncertainty before a fake external boundary;
I2 contains no real sender. Restart cannot treat a possibly-sent intent as
unsent or issue it twice.

Durable uniqueness remains authoritative after bounded in-memory history is
compacted. Retain working-order fee facts, lot provenance, seed/round anchor,
the lowest placed ladder level needed for distinctness, monotonic IDs and
reconciliation revisions. Archive historical evidence in SQLite without
discarding its uniqueness keys. Replay historical duplicate/status events
against durable records before invoking the bounded reducer. Test that
compaction/reload preserves subsequent commands and accounting exactly; if a
safe compact snapshot cannot be produced, block instead of dropping evidence.

## Fault tests and acceptance evidence

| Test group | Required observed result |
| --- | --- |
| Runtime/schema guards | Old SQLite, wrong pragmas, schema corruption and unsupported storage refuse writer authority |
| Decimal round trip | Base/quote/token fees, rebates and precision-boundary values preserve exact canonical amounts |
| Concurrent funding/reservation | Exactly one winner for the last shared funds; no double assignment or negative spendable amount |
| Fill atomicity/idempotency | Partial/full fills and all fee postings commit once; equal replay changes no balances; conflict blocks |
| One SELL / unknown intent | Pending, attempted, unknown and cancel-requested orders occupy their durable slots across restart |
| Crash boundaries | Kill a test child before/after every financial commit and fake send boundary; recover either the whole prior state or whole committed state |
| Lost acknowledgement | Commit succeeded but caller was canceled/lost response: durable request ID finds the same outcome without a second reservation |
| Controller exclusivity | Second process cannot acquire writer authority; stale metadata cannot override an OS lock or skip recovery |
| Queue/storage faults | Bounded queue/backpressure; explicit SQLITE_BUSY, SQLITE_FULL and I/O failures; no silent event loss or retry storm |
| Checkpoint/reload | Same next event yields the same financial state/intents; archived duplicate fills remain no-ops |
| Backup/restore | Verified backup restores in a separate temporary directory, passes integrity/foreign-key checks and retains all uncertainty |

Use SQLite's [online backup API](https://www.sqlite.org/backup.html), exposed by
[Python's connection backup method](https://docs.python.org/3.13/library/sqlite3.html#sqlite3.Connection.backup),
with bounded work and explicit failure reporting. Never copy only an active DB
file and ignore its WAL. A restore must pass semantic ledger comparisons, not
just an integrity check. Backup destinations must be new test paths; never
overwrite an operator's database. Simulate full storage using constrained test
databases/fault injection, not by filling the host disk.

Run the complete existing offline suite plus these tests on both supported
Python versions, lint/type/security checks, dependency audit and packaging.
Record the exact interpreters, linked SQLite, commit IDs, fault boundaries and
test results. These extend Q03/Q05/Q08/Q09/Q10/Q19; they do not claim I4 network
recovery, hardware power-loss durability, production storage provenance or a
paper/live soak. Keep the domain import guard and exchange/network prohibitions;
narrow the old I0 global `connect` prohibition only for the approved persistence
package's SQLite connection.

## Delivery and rollback

Deliver tested code, migrations, precise local evidence, updated offline runbook
and a reviewable summary. Hosted CI stays explicitly pending while locked. No
benchmark, paid service or exchange account is a prerequisite for this mock-only
iteration. Bounds are explicit synthetic test inputs, not production defaults.

There is no deployed ledger to migrate under this approval. Roll back scoped
I2 code with normal revert commits and the matching lock. Temporary test databases
are disposable fixtures; preserve any forensic failure fixture outside Git until
review. Never perform automatic downgrade, destructive migration or restoration
over existing operator state. I3 requires its own next-iteration review.
