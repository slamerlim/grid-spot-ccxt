# Local mock operator CLI

This increment implements part of PRD section 10 under the owner's authorization
to continue mock development after the completed public hour. `gridctl operator`
connects to an existing mock supervisor through a Linux Unix-domain socket. The
supervisor remains the only financial writer. No exchange transport, credential
loader, remote listener, service installation or live arming path is added.

## Commands and scope

| Command | Behavior |
|---|---|
| `serve-mock` | Finite, 1–300-second invented-account session; starts halted |
| `status` | Operational revision, admission halt, allocations, cycle, bounded alerts, writer/intake depth, RSS and WAL bytes |
| `allocations`, `venues`, `symbols`, `markets` | Inspect the owner's existing fixture roster and current market facts |
| `pause` | Pause new BUY admission for selected allocations |
| `cancel-buys` | Request cancellation of selected BUYs through the existing mock executor |
| `stop` | Stop admission and require reconciliation; does not terminate the owner process |
| `reconcile` | Reset bounded recovery scheduling; does not resume admission |
| `retire` | Retire selected allocations while retaining supervision of orders/inventory |
| `resume` | Explicit reviewed resume, subject to existing fresh-evidence/recovery guards |
| `halt` | Emergency admission halt; full supervised roster required |
| `limit-liquidate` | Reviewed LIMIT exit: `--all` for the whole cycle, explicit allocation IDs for scoped exits |

Mutations require the current **operational** revision, a printable reason up to
128 characters, explicit `--all` or `--allocation ID` targets, and a new private
`--request PATH`. The operational revision is distinct from allocation strategy,
universe and portfolio configuration revisions. This increment does not expose
their configuration mutations. Actor is derived from Linux peer credentials as
`local-uid-N`; clients cannot supply an actor field.

The client saves and fsyncs the exact request and its parent directory before
sending. The request carries a session ID, durable request ID, revision, targets,
action, reason and the owner's synthetic observation time. A retained request
cannot be reused with different action arguments. Repeating it returns the
durable receipt; it does not repeat the mutation. A lost acknowledgement is an
unknown outcome, never an instruction to create a new ID or automatically retry.

`resume`, `cancel-buys`, `retire` and `limit-liquidate` require two invocations: the first saves and
prints a preview with exit 3; after reviewing it, repeat exactly with `--confirm`.
`--confirm` without a previously saved request is refused. The server also
requires the sensitive command's confirmation flag. This is an operator workflow,
not protection against a malicious process running under the same OS account.
Responses include current revision/halt state alongside historical receipts so
replaying an old resume cannot be mistaken for clearing a subsequent halt.

Manual liquidation additionally requires `--cycle` and `--policy-sha256` from
status. `--all` records a global operator cause without claiming +10% was met;
explicit allocation IDs retain an active portfolio and record scoped approvals.
Both preserve existing halts/holds and reuse bounded LIMIT exit supervision.
See `I8-MANUAL-EXIT.md` and `I8-ALLOCATION-EXIT.md` for transaction boundaries,
global handoff, record compatibility, confirmation, preflight and remaining scope.

Exit codes: 0 for a successful view or committed/replayed command; 2 for refusal
or an unknown outcome; 3 for confirmation required; 130 for keyboard interruption.
A successful cancellation command means the request was recorded, not that every
order is already terminal. Inspect status and allow reconciliation to resolve it.

## Transport, ownership and bounds

The local channel uses AF_UNIX/SOCK_STREAM only. Both endpoints check matching
effective UID using Linux `SO_PEERCRED`. The directory and socket must be owned
by that UID with no group/other permissions. Socket symlinks and existing bind
paths are refused. The path must fit the Linux 108-byte pathname buffer. These
checks follow the [Linux Unix socket contract](https://man7.org/linux/man-pages/man7/unix.7.html).
They do not distinguish human operators who share one Unix account.

Each connection carries one four-byte length-prefixed JSON request and response.
Requests are capped at 16 KiB and responses at 1 MiB. Duplicate keys, nonfinite
numbers, unknown fields/actions, ambiguous targets and mismatched sessions are
refused. Four handler tasks and a requested listen backlog of four bound admitted
clients; the kernel controls its actual pending-connection queue. Read/write
deadlines are two seconds, dispatch ten seconds and the client fifteen seconds.
There is no automatic retry or arbitrary code/file/credential operation.

Nonblocking sockets use the documented [asyncio socket methods](https://docs.python.org/3.12/library/asyncio-eventloop.html#working-with-socket-objects-directly).
Timeouts bound cooperative waits, not host descheduling or synchronous OS work.
A caller timeout can follow a committed transaction; the financial writer shields
admitted work. Fixed error codes and sanitized CLI diagnostics avoid reflecting
untrusted arguments or exception traces. Status contains invented financial facts;
request previews necessarily contain the supplied reason. Never enter secrets.

Only the exact `MockSupervisor` and `SyntheticExchange` classes are admitted.
The existing mock ledger, actor/reason audit, optimistic revision checks,
idempotency and reconciliation are reused. Static tests restrict the socket
exception to this module and AF_UNIX constructors; runtime IPC tests still block
IP sockets. This is a regression guard, not a proof against arbitrary code changes.

## Finite session and recovery

`serve-mock` uses the existing one-allocation fixture: 1,000 invented QUOTE,
BASE0/QUOTE, synthetic-0, zero assumed fees and invented grid/risk constraints.
It refreshes synthetic facts every quarter second, admits mock intents only after
reviewed resume, and settles synthetic cancellation acknowledgements on later
ticks. It generates no fills. These constants are not production defaults and
the session cannot establish trading performance or account readiness.

The private outside-Git session directory contains `world/` for the journal and
mock ledger, and `control/` for the endpoint. The journal's first marker identifies
an operator session; it cannot pass as a public paper observation. The journal
holds an exclusive process lock; SQLite retains its independent single owner.
The existing 512 MiB/10,000-record journal bounds apply, as do the existing mock
supervisor's resource, queue and order limits. Endpoint names are unique per
process; crash leftovers are preserved and their count is bounded at restart.

`--resume` requires the same application-source digest, OS user and host boot.
Its clock is explicitly synthetic: 10,000 ms plus monotonic elapsed time. It is
neither UTC storage nor observed-soak evidence. Restart replays already durable
fake venue effects before reconciliation, retains one accepted order without
resending it, and establishes a new startup halt. Use the newly printed socket;
an old successful resume request returns its old receipt and leaves the new halt.

Normal expiry and keyboard interruption close admission, drain financial cleanup,
then release journal ownership. If financial cleanup remains pending, journal
ownership remains held too. Socket cleanup removes only the bound inode and never
a replacement file. SIGKILL leaves evidence and a stale endpoint; restart does
not delete either. Source/boot mismatches require inspection and a new disposable
session, not editing the journal marker or bypassing the resume guard.

## Remaining work and rollback

This is not completion of PRD section 10. Scoped dust completion,
audited hot configuration CLI, owner-mediated backup and
restore verification commands, and unified research/storage command aliases remain
to implement. Existing research and backup APIs are still available only through
their documented modules/typed interfaces. The views inspect a fixed fixture;
they do not discover exchanges or modify allocations.

The observed minimum 48-hour gate, intended-first-venue private evidence and I9
remain separately gated. The completed public-hour authorization is spent. No
additional public calls, account access, trading or deployment follows from this
CLI. GitHub is Free-only; local checks do not close the hosted-CI gap.

Rollback is a scoped code/docs revert that preserves request files, journals,
ledgers and prior evidence. No schema/dependency change or migration is added.
Do not downgrade an active owner or attempt to resume a session with changed code.
