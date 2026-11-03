# I2 — single-owner local ledger

I2 makes the I1 reducer's proposed state and commands durable. Dedicated funding,
fill/fee postings, positions, reservations, revisions and intents commit together.
The implementation contains no exchange client or order sender. Only temporary
synthetic databases were exercised under the owner's I2 approval.

## Ownership and runtime

`AsyncWriter` admits work through a bounded asyncio queue and dispatches it to
one dedicated thread. That thread creates, uses and closes one SQLite connection
with `check_same_thread=True` and explicit `autocommit=True`; each mutation uses
`BEGIN IMMEDIATE` / `COMMIT` / rollback. The synchronous `Ledger` is the same core
used by integration/fault fixtures. No network work belongs in a submitted job.

An OS `flock` on the canonical path's persistent `.owner` sidecar is acquired
before opening the database. The sidecar is never unlinked to release ownership.
A second process, a second instance in the same process, and a parent-directory
alias cannot acquire it. Database/sidecar symlinks and hardlinks are refused.
Existing parents must be owner-only; the library does not silently chmod an
operator directory. Files are created with mode 0600, and the new database and
parent directory are synced. Each owner receives a durable random run ID.

Linux mount evidence must identify ext4, XFS or Btrfs; explicit mock mode also
admits overlay/tmpfs. Other filesystems, including NFS, fail. This is a filesystem
type check, not proof of local NVMe hardware or truthful fsync behavior. WAL,
FULL synchronous, foreign keys, trusted-schema off, bounded busy timeout and
automatic checkpoint settings are set and read back. Runtime version plus an
explicit source-ID allowlist are required; old SQLite fails before creating a
database. No production build is approved by the fixture allowlist.

Schema version 1 has an exact SQL checksum, `user_version`, and complete expected
schema comparison, including indexes, unique constraints and foreign keys. An
existing unknown/empty schema is refused. There is no automatic downgrade or
destructive migration. This iteration does not migrate an operator database.

## Transactions and accounting

| Operation | Atomic result |
| --- | --- |
| `credit` | Explicit synthetic dedicated funding enters a venue/account/currency pool and journal |
| `start` | Funds move from that pool into one allocation, with a valid seed reservation and intent; failure rolls back the assignment |
| `release` | Only unreserved quote/fee-token funds move back to the pool; inventory and unreconciled funds cannot be released |
| `apply` | Normalized observation, accepted fill and fees, cash/base/cost/PnL, reservations, state revision and commands commit together |
| `attempt` | A queued place/cancel intent becomes uncertain and its order requires reconciliation before a simulated external boundary |
| `compact` | A safe bounded snapshot, its revision/hash and historical event watermark are committed; durable facts remain |

Allocation identity hashes the complete I1 `Scope`. A trade ID is unique within
that normalized allocation scope. Equal replay is a financial no-op; conflicting
payload blocks the allocation. Request IDs are globally unique within a ledger:
equal inputs return the original receipt, while changed inputs raise `Conflict`.
Expected allocation versions reject stale mutations. I3/I4 must establish venue
trade identity, exchange-order mapping and exclusive scope attribution before
constructing these normalized facts; I2 does not infer those from status messages.

Funding is an explicit ownership transfer. It never copies an account's total
cash into each symbol state. Two queued allocations racing for the same final
pool balance have exactly one winner. Assignment cannot succeed without the
seed reservation in the same commit. There is no API to adopt unexplained base
inventory; base ownership comes from accepted fills and lot provenance.

Money is canonical Decimal TEXT, including signed rebates and explicit currency.
No REAL columns, floating JSON amounts or SQL monetary SUM are used. Financial
deltas and journal totals use the domain's exact arithmetic context. The codec
accepts only listed immutable records/enums, exact field types and bounded
collections/bytes/nesting; it cannot import a stored class or execute a payload.

Principal and remaining fee reserve are separate per order/currency. Actual fee
excess cannot release unfilled principal. Quote/base/token fees and rebates are
retained individually. If the reducer accepts a confirmed fee breach, its exact
financial facts and reconciliation block commit together. Missing fee evidence
does not partially debit a balance. Status-only observations never create PnL.

A partial unique index enforces at most one unresolved SELL per allocation.
Pending, uncertain and cancel-requested orders retain that slot until terminal
evidence. A cancel acknowledgement alone releases neither the slot nor reserves.
Terminal old orders are updated before a replacement SELL is inserted.

## Cancellation, uncertainty and recovery

Admitted operations are shielded from caller cancellation. A lost response can
mean the commit succeeded: query the original request ID. A receipt is evidence
of a transaction, not permission to repeat an external action. `attempt` has no
sender and never automatically retries. Its uncertainty is durable before the
test's fake-send marker; restart cannot turn it back into a queued intent.

Every new owner marks allocations as requiring reconciliation, including after
clean shutdown. Previously clear states receive a durable recovery observation,
revision and reconcile intent. Existing uncertainty is retained. Startup checks
SQLite integrity/FKs, strict snapshots, all durable order/fill totals and fee
rows, working-order evidence, monotonic IDs, balance/position/reservation
projections, and exact journal totals against owned funds. Corruption refuses
the ledger. Normalized `Reconciled` facts must match current revisions and resolve
unknown order evidence; startup does not produce them on the caller's behalf.

No queue, transaction or storage failure is silently retried. Queue overflow is
an explicit admission refusal; the future intake/controller must retain the
fact and pause exposure. BUSY/FULL/I/O errors propagate. A caller must check its
request receipt before retrying after an uncertain result. I4 still needs the
supervisor, event priorities, exchange reconciliation and network fault handling.

## Bounds, compaction and backup

All queue, busy, snapshot, read-page, WAL and backup limits are explicit inputs.
Historical validation/hash scans stream rows rather than hydrate the full audit
history; indexes support order-fill and journal lookup. No latency/throughput
benchmark is claimed. Unresolved-intent pagination is bounded; callers must keep
using its last ID to consume further pages.

Compaction retains working orders and their fee facts, remaining lot provenance,
the current seed, the lowest current-round ladder price, anchor/round state,
ladder credits, monotonic IDs and reconciliation revisions. All historical orders,
fills, fees, observations and unique keys remain in SQLite. Archived duplicate
fills/status reports are checked before invoking the bounded reducer. Tests
compare subsequent commands/accounting before and after compaction, including a
completed round. If required facts cannot fit, the operation blocks; it does not
evict financial evidence or change the I1 strategy. The controller must request
compaction before history limits are exhausted.

Admission checks checkpoint progress. A bounded passive checkpoint is followed
by at most one bounded TRUNCATE attempt when the WAL limit is exceeded. A pinned
reader causes refusal, not a checkpoint retry loop. This is an admission bound;
one atomic transaction can temporarily grow beyond it. It is not a disk quota.

Backup and restore use SQLite's online backup API with bounded page steps and
deadline checks during copying, hash verification and SQLite query progress.
Filesystem sync can still block in the kernel; hardware I/O time bounds are not
certified. Destinations must be new private paths. Both integrity/FKs and a
streamed business-record hash are checked; backups retain a request-count
watermark. Restore reads its source without modification, validates schema,
compares the recorded hash and opens no writer authority. A subsequent ledger
owner validates again and requires reconciliation. Run metadata is excluded
from the business hash; allocations, requests, events, orders, fills, fees,
reservations, positions, balances, journal, intents and checkpoints are included.

Failed artifacts are retained but never reported as successful. Tests restore
into a separate temporary directory and preserve uncertain intent evidence.
No copying of a lone active database file, operator overwrite or automatic
financial-state repair is provided.

## Remaining gates

These tests cover mock persistence parts of Q03/Q05/Q08/Q09/Q10/Q19. They do not
certify hardware power-loss behavior, production runtime provenance, exchange
semantics, operational resource limits, profitability, a soak or live readiness.
Doctor remains metadata-only and creates no database. I3 and all later exchange
or deployment work require their own approval. GitHub hosted CI remains pending;
only local validation was authorized, with GitHub Free only.
