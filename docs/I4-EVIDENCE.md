# I4 local execution and recovery evidence

Recorded 2026-11-04 local date. The owner explicitly selected **"Approve I4 with
local validation"** for `I4-PLAN.md`. This authorized synthetic execution/recovery
and temporary ledgers only. No exchange probe, authenticated access, real/sandbox
order or cancellation, transfer or deployment was performed.

**Result: the I4 mock implementation and local gate pass. Hosted CI remains
PENDING / account lock. All seven venues remain HOLD for live readiness.**

## Revision and implementation

- Branch: `i4/mock-executor-recovery`.
- Base: `5c3ab22858efd87cdbc95885c424c10c15b5ed83`, the published I3 handoff.
- Implementation and evidence are published together. The final chat records the
  exact commit after checking the remote; reproduce that revision, not a future tip.
- PR: not created; no authenticated PR API/CLI is available. Ordinary SSH Git
  publication is authorized. No merge or hosted check result is claimed.
- Workflow unchanged; substantive publication uses `[skip ci]`. No rerun,
  workflow dispatch, paid GitHub feature or spending change was used.

Added `grid.execution` typed LIMIT payloads, a bounded in-memory gateway, one
mock executor, bounded private-event hints and fake REST reconciliation. Added
ledger identity/first-attempt/revocation methods and append-only schema migration
2. Doctor describes I4 but remains offline, unready and LIVE DISABLED. See
`I4-DESIGN.md` for exact contracts and conservative holds.

The I1 reducer, dependency lock, actual read-only CCXT transport, four imported
specifications, legacy source and historical datasets were not changed. The 333
previous tests remain; this iteration adds **86** cases, including 14 actual
SIGKILL cases around the executor and its identity/financial posting boundaries.

## Validation actually executed

uv 0.13.7; Python 3.12.16 in `/tmp/grid-i2-py312` and Python 3.13.18 in
`/tmp/grid-i2-py313`; both use the existing approved mock SQLite 3.53.1 source.
These environment names are retained from I2, not production build approvals.

| Check | Result |
| --- | --- |
| Full pytest, Python 3.12.16 | **419 passed**, 0 failed/skipped; 42.47 seconds |
| Full pytest, Python 3.13.18 | **419 passed**, 0 failed/skipped; 43.69 seconds |
| Ruff lint / formatting | PASS; 78 Python files formatted |
| Strict mypy, application and tests | PASS; 57 source files |
| Bandit | PASS; no findings or suppressions |
| `uv lock --check` | PASS; 65 packages, lock unchanged |
| Tracked/staged secret scan | PASS; baseline unchanged, no new allowlists |
| Dependency advisory audit | PASS; no known vulnerabilities found |
| Wheel / sdist build | PASS; both built successfully |
| Hosted GitHub Actions | PENDING; known account lock not retried |

Commands are the README verification commands, using explicit interpreter and
environment variables. Prefix uv with
`UV_PYTHON=3.12.16 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py312` and invoke
`/tmp/grid-tooling/uv` on this host; substitute 3.13.18 and `/tmp/grid-i2-py313`
for the second suite. Pytest disables Internet sockets. Async fixtures allow
only the local Unix socket required by asyncio and verify TCP remains blocked.
SIGKILL subprocesses use only synthetic objects and temporary files; the
application's static guard still forbids CCXT order/cancel helpers.

Actual offline doctor also returned exit 0, iteration I4, LIVE DISABLED and
`ready: false`. The advisory tool's generic no-hash-export warning was emitted;
installation remains governed by the existing hashed `uv.lock`.

The first full run passed 417 tests; the final runs above include the last backup
identity and send-side retry-after checks. Earlier development failures identified
an unsent-order draining gap, a one-page intent traversal edge case and incorrect
test expectations for pooled cost basis, the saved seed receipt and Bitfinex
rounding. Those were corrected before the reported passing runs. The actual
financial strategy and its cost-basis convention were preserved.

## Acceptance mapping and executed evidence

| IDs | Executed evidence | Limit of claim |
| --- | --- | --- |
| Q01 | Typed LIMIT-only payload; hidden params/type overrides refused; changed precision values/floats refused; all seven pinned CCXT formatters feed captured fake payloads; static prohibition on real order helpers | No real private request serialization, global/manual exit or shutdown liquidation path exists |
| Q04/Q06 | Seed ack creates no fills; partial below sell minimum stays owned; actual weighted anchor; three bounded distinct ladder BUYs; existing minimum-lot/property suite retained | Synthetic rules and account fee evidence only |
| Q05 | Delayed/timeout cancel ack preserves old SELL slot; OPEN snapshot cannot finish cancel; partial/full SELL fills during cancel post before replacement; local revocation proves no first attempt | No real exchange cancel semantics certified |
| Q07 | Full SELL drains; a late BUY becomes next-round inventory/anchor without duplicate seed | Portfolio-cycle exit remains I7 |
| Q08 | Duplicate/reordered trades and repeated private hints/REST replay preserve finances; conflicting facts rejected; same physical trade cannot cross allocations; backup/restart preserves identity keys | Audit versions/events can advance on distinct requests; real venue pagination/sequence semantics untested |
| Q09 | Accepted-but-timeout and missing external outcome; repeated calls/restart never resend unknown; replayed attempt receipt refused | Unknown absence is an indefinite explicit hold, with no guessed safe retry |
| Q10 | Atomic external trade key/fill/fees/journal; base/quote/token charges and rebates; missing fees/conversion blocked; forecast breach posts valid liabilities and remains blocked; pooled cost conservation | Account balance-drift and actual fee-tier verification still untested |
| Q13 | Unsupported/disconnected/quiet stream; stale/reconnected epoch; snapshot token/cursor/completeness failures; REST timeout/429; caller cancellation; retry-after on send and recovery; unaffected accounts remain serviceable | Stream notices and REST are synthetic; no authenticated subscription or 48-hour soak |
| Q17 | Stale book/rules/fees/config/generation rejected; previously queued BUY blocked after pause/retirement/exit control | I6 hot-universe and real cache revalidation remain later scope |
| Q18 | Bounded pages/records/calls; 1,000-hint burst retains four admitted hints and recovers the actual fill; 100 busy callers refused without waiters; one/seven synthetic venue/account paths | Does not establish throughput/memory for 1,000 actual symbols, NVMe performance or production latency |
| Q19 | 14 new SIGKILL cases; all original I2 process/DB failure tests retained; single writer/executor; migration before/after commit; v1 restore/upgrade; v2 identity restore/dedup; corrupted identity refuses recovery | Synthetic storage; deployment power-loss and hardware durability certification remain untested |

Test files: `tests/execution/test_boundary.py`, `test_executor.py`,
`test_recovery.py`, `test_capacity.py`, `test_crashes.py`, and
`tests/persistence/test_execution_identity.py`, alongside the existing domain,
persistence, exchange and diagnostics suites. `crash_child.py` is test-only.

The new process-death points are before the executor claim, after attempt posting,
before/after its commit, after claim return, before/after the fake external effect,
after send return, and after posting/before commit/after commit for both external
binding and actual fill transactions. Assertions distinguish an uncommitted
unsent intent, committed unknown with no receipt, and accepted external order.
No test resolves an unknown merely because it cannot find the order.

## Remaining gates and rollback

All seven actual venues retain their I3 readiness blockers, including XT's
unresolved rejected book, Bitfinex compound precision and XT offset/protection
rules. Private client IDs, fees, account ownership, pagination windows, WS events,
balance drift, transport serialization and actual rate weights require separately
approved account/venue work. I4 provides no credential interface to test them.

The private intake currently treats messages as hints and reconciles complete
synthetic history. Real incremental event processing and finite history windows
need verified venue contracts. Hitting any history bound pauses admission.
Observed execution/LOB evidence, hardware profiles, I8 soak and explicit I9
canary approval remain mandatory. No profitability or realized +10% claim.

Rollback uses scoped revert commits and the matching lock. No deployed financial
database exists. Migration 2 has no destructive downgrade: retain temporary
failure artifacts outside Git if needed; a v1 restore requires its validated v1
backup and matching code, never deleting identity rows from a live database.
Original specifications, GitHub Free preference and authorization history remain.

`I5-PLAN.md` is the next concrete review proposal. I4 approval does not authorize
historical processing, I5 implementation or exchange access.
