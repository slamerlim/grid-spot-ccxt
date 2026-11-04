# I8 offline operational preparation

The owner approved preparation with local validation. `MockSupervisor` now owns
the I4 executor, I6 universe controller, I7 portfolio controller and one I2
writer. All execution is in `SyntheticExchange`; its exact type is checked
before opening a ledger. This implementation has no real/sandbox trading
transport, public feed, credential reader, external notification or installed
service. It does not complete the observed 48-hour I8 acceptance gate.

## Ownership, scheduling and admission

`MockSupervisor.open` takes an explicit complete allocation roster, patched
SQLite source allowlist, storage/execution/operational limits, clock and synthetic
gateway. Its optional preparation callback exists for trusted synthetic funding.
Every open persists a startup halt. Existing allocations remain under recovery
until authoritative synthetic order/trade reconciliation. Fresh complete facts,
a configured universe and an explicitly begun portfolio cycle are needed before
a confirmed resume. A prior receipt, cache, completed reconciliation or restart
alone never resumes placements.

`observe` publishes one bounded valuation packet and per-scope universe evidence.
No packet is made fresh by changing its timestamp internally. Both controllers
check their evidence again at the writer-side send claim. The operational
revision joins those checks in the same transaction; the ledger itself requires
the current receipt for placement when an operational owner exists. Removing
the executor callback does not bypass the persisted guard. A claimed request
may already reach the synthetic venue when a later halt arrives; it stays
uncertain and owned, and cannot be resubmitted by replaying the claim.

Only one `step` can be active. A concurrent step is refused, without a queued
task. Each step prioritizes unknown/recovering allocations, then working orders,
then idle allocations, with oldest-due ordering. The configured scope cap,
per-scope backoff and finite failed-recovery allowance bound work. Successful
recovery resets that scope's failure count; exhaustion needs a new explicit
reconcile/resume command. One unavailable scope eventually exhausts its budget
while the others remain serviceable. Gateway calls retain I4 page, payload,
per-account rate, timeout and epoch bounds. There is no auto-retry daemon.

The scheduler evaluates portfolio NAV when due and advances a latched exit.
Unknown orders anywhere in the portfolio can block another BUY claim, so
seven owners do not imply seven simultaneous sends. Financial postings remain
Decimal and go through the existing writer. No historical replay runs in its
admission path.

## Typed local controls

`control(request_id, expected_revision, Command(...))` takes explicit scopes,
actor, reason, observed time and confirmation. A stale revision conflicts.
An exact replay returns the original receipt, without clearing a later local
halt or resetting recovery attempts. Commands and their order-state effects
commit atomically. No control refunds a possibly executed order by assumption.

| Action | Effect |
|---|---|
| `PAUSE` | Pause new BUYs; preserve existing orders and inventory management. |
| `CANCEL_BUYS` | Pause new BUYs; revoke provably unattempted zero-fill BUYs locally; queue cancellation of the others. A cancel acknowledgement is not terminal evidence. |
| `STOP` | Hold the chosen scopes against all new gateway claims, including cancellation; continue local ownership and reconciliation. |
| `RECONCILE` | Explicitly reset the selected finite recovery budgets; does not resume placements. |
| `RETIRE` | Retire selected scopes, cancel BUYs and retain inventory/order supervision. |
| `HALT` | Stop all new placements immediately in memory, then persist the full-roster halt. Reconciliation and already queued cancellation can continue unless a scope is held. |
| `RESUME` | Requires confirmation and fresh reconciled portfolio evidence. Releases selected holds and ordinary pause/retirement; full-roster resume can release the global halt. Never clears a latched portfolio exit. |
| Startup/shutdown | Supervisor-owned full-roster halt; unavailable as caller control actions. |

The I7 +10% exit remains distinct: it freezes new BUYs, reconciles cancellations
and coordinates bounded LIMIT liquidation. Emergency halt does not promise
liquidation. There is no below-threshold manual liquidation or widening of exit
floors/budgets in this preparation API.

Controls are an in-process typed API. No HTTP server, shell control socket or
unauthenticated remotely writable endpoint has been added. Every action needs
a stable request identity and reviewed metadata; never put secrets in actor or
reason fields.

## Status, alerts and storage

`status()` reads operational revision, allocations and portfolio cycle in one
writer operation. It exposes modes, held scopes, recovery, unknown/working order
counts, queued cancellations, owned cash/base, baseline/trigger/final NAV,
computed final return/exit shortfall and residual/exit-slot reasons. Fee totals
are explicitly **retained-fill fees**, not lifetime totals: the bounded state
projection can compact completed fills. The durable fill/fee history remains
authoritative. The dry-run artifact also includes all its two synthetic fills.

Fixed-code local alerts cover stale facts, unknown orders, recovery, exhausted
attempts, blocked exit, queue overload, storage/backup faults, backup freshness,
resource thresholds, clock regression and runtime failures. The in-memory alert
ring is bounded and reports evictions; it is not a durable notification service.
Audited controls and a verified backup stamp are durable. Arbitrary exception
text and transport payloads are not copied into health/alert records.

RSS and WAL thresholds are checked before a tick. A monotonic step budget is
checked between bounded service/send operations; it is a soft scheduling limit,
not CPU preemption. Queue/task/RSS/WAL observations and process CPU are exposed.
The application does not enforce a process-wide CPU quota or total database
disk quota. The uninstalled systemd template contains separate fixture ceilings.

SQLite/OS/storage-integrity failure halts local admission even if the halt cannot
be persisted. A generic executor ledger/gateway failure is treated conservatively
as a storage alert. Full disk does not justify discarding records or retrying
sends. Restore requires private paths, checked runtime/schema, integrity,
financial/semantic reconciliation and matching backup watermark/hash. A fresh
backup stamp is recorded only after restoration into a separate temporary file
and successful verification. It describes that backup's cutoff, not later writes.
Only one backup/verification task is admitted. Caller cancellation detaches from
it; shutdown joins the tracked work before closing the writer. A second request
is refused without queuing another restore thread.

Schema v5 adds `operations_revisions`; v1–v4 definitions/checksums stay unchanged.
Semantic backup hashes include v5 data and omit absent newer tables for older
backups. Startup validates the complete operational roster and revision
projection. Never downgrade in place or remove this table on rollback. Preserve
the v5 history and use a separately verified compatible pre-migration backup if
old code is needed.

## Shutdown and failure behavior

Close sets an immediate local halt, cancels and awaits an active tick, attempts
an audited shutdown halt, closes each controller/executor and finally drains and
closes the single writer. Admitted SQLite work is shielded from caller
cancellation. Canceling `run` also closes its owners, including when cancellation
occurs between ticks. `run(steps)` is finite (1–10,000); a successful finite run
leaves explicit close to its caller. The experiment always closes in `finally`.

Shutdown has a bounded caller wait. If admitted work outlasts it, cleanup remains
tracked, admission stays halted and the writer lock remains held. The error
explicitly reports pending shutdown; it does not claim the thread was killed or
that ownership was released. After cleanup completes a subsequent close joins
it. A hung OS/storage operation may still need operator process recovery. A
forced process kill relies on durable uncertainty and startup reconciliation.

## Reproducible synthetic experiments

`python -m grid.operations dry-run|benchmark` requires an output filename outside
Git/original-data trees, a caller-supplied 40-hex code reference and an explicitly
approved mock SQLite source identity. It creates private temporary mock ledgers,
then deletes only those temporary trees. JSON publication is exclusive: no
overwrite or partially published successful result. Failures return 2 with a
fixed message; successful report publication returns 0.

The manifest records UTC start/end, **observed monotonic wall duration**, process
CPU, separate simulated elapsed time, host/kernel/filesystem/cgroup evidence,
runtime versions, fixture limits/hash and a hash of the executed `grid` Python
sources. The supplied Git reference is labeled unverified by the runner; when
testing uncommitted work, the source hash is its exact code identity. Timestamps,
UUID audit identifiers and performance samples naturally vary between runs.

The invented fixture has zero fees, 1,000 QUOTE per allocation, 10% grid spacing,
three lower levels, explicit precision/minimums and no real account. These are
test inputs, not proposed operator financial defaults. Resource caps are defined
before the experiment in `operations/synthetic.py`: 512 MiB RSS, 4 MiB WAL,
seven scopes/tick, 10 ms tick, three failed recovery attempts with 100 ms backoff,
32 alerts, 5 s soft tick and 10 s shutdown waits. Writer queue is eight, event
intake 16, fake gateway capacity 1,000; executor calls are bounded separately.

The dry run exercises explicit resume, an uncertain seed, fake disconnect,
controller close/reopen with the same surviving fake venue, explicit restart
resume, actual fake fills, threshold exit, final halt and verified backup/restore.
It records **one controller restart and zero process restarts**; SIGKILL evidence
comes from separate tests. Simulated elapsed time is 40 ms, not observed uptime.

Six benchmark cases combine 1/7 logical venue owners with 10/100/1,000 evaluated
candidates. Funded, supervised allocations are 1/7, separately measured from
candidate evaluation/selection. Each case performs ten post-initialization
ticks. There is one in-memory gateway and **zero CCXT clients or external calls**.
RSS/WAL/queue/task maxima are post-step sample maxima; transient peaks may be
missed. Process lifetime RSS high-water is labeled separately. Neither candidate
counts nor local timings certify 1,000 funded allocations, seven network clients,
NVMe throughput, subscription capacity or production sizing.

Every synthetic report hard-codes `observed_soak_complete=false` and
`i8_acceptance_complete=false`. No simulated clock duration can change them.

## Uninstalled service review

`deploy/grid-mock.service` is an inert review template: it requires an absent
review marker and replacement code/source arguments. Its configured Python must
also be replaced/prepared as a reviewed patched environment; the checkout's old
default `.venv` is not evidence of an approved runtime. No service user, marker,
unit installation, enablement or service start is performed by this iteration.

The template uses a dedicated user, private state/tmp, restrictive umask,
read-only system/home protections, no privileges/capabilities and no IP network.
It limits memory/CPU/tasks/descriptors and disables automatic restart. SIGINT
allows the finite Python runner's cancellation cleanup; a later forced stop
still needs startup reconciliation. These are fixture choices requiring actual
host review, not a deployed security proof. Review references are the official
[execution settings](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)
and [service lifecycle settings](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html).
Local `systemd-analyze verify` checks parsing only; it does not exercise isolation.
