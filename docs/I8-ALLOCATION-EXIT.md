# I8 offline follow-up — per-allocation LIMIT exits

Subsequent universe CLI integration, including explicit bounded exits on
deny/disable, is recorded in [I8-HOT-CONFIG.md](I8-HOT-CONFIG.md). The evidence
below describes this earlier scoped-exit increment.

Base: `0acd0271a90498c66b3473e9cb7530f2081d3fd3`, branch `i8/public-paper-hour`.
Scope: the owner's approved continued offline/mock development after the completed
public hour. No new exchange request, private account access, order, transfer or
deployment is authorized or performed. GitHub remains Free-only; hosted CI stays
pending under the unchanged lock and publication uses `[skip ci]`.

## Command and ownership

`gridctl operator limit-liquidate --allocation ID` now selects an allocation exit.
Repeat the selector for an explicit set. `--all` keeps its separate whole-cycle
meaning. Selecting every allocation by ID does not silently convert the command
into a global exit, including in a one-allocation fixture. The durable action is
`liquidate_allocation`, distinct from the existing `limit_liquidate` action.

Both forms require the same saved request, second confirmation, operational CAS
revision, peer-derived actor, reason, cycle ID and complete policy digest. A
selection cannot include ambiguous/unknown IDs, an already latched scope or a
conversion sink. Exact receipt replay remains safe after later state changes;
changing the command's action or selection conflicts with its retained identity.

The scoped preflight checks fresh verified account/rule/book/fee facts and
reconciled ownership for every selected allocation, without requiring an unrelated
market's fresh book. Unclassified cashflow still prevents a new scoped request.
The single existing writer atomically records approvals, audit, exit state,
unsent-order revocations, cancellation intents and the command receipt. Failure
on a later selected allocation cannot partially accept earlier selections.

Existing holds and the global halt remain effective. Liquidation cannot resume
send admission. Only selected modes change to portfolio-exit supervision; other
allocations remain subject to their existing strategy, universe and NAV guards.
Direct reducer controls, operational resume and universe changes cannot override
an unresolved scoped exit. No new writer, gateway or transport is introduced.

## Execution and global takeover

Scoped execution shares the existing Decimal LIMIT planner, minimums, fee
reservations, price floor/drift, minimum proceeds, TTL, duration and order cap.
Ordinary orders must cancel/reconcile first. Partial fills, late fills and unknown
submissions retain ownership; a cancel acknowledgement cannot free a SELL slot.
Fresh evidence is checked again at the serialized send claim.

The global phase, baseline and NAV trigger remain unchanged by a scoped request.
Scoped progress has its own durable observation time, so it cannot postpone the
global NAV sampling interval. Clock regression checks include both clocks. A
stale book raises a bounded evidence refusal and alert; it does not crash the
supervisor or prevent cancellation on a different, sufficiently observed scope.
Fresh whole-portfolio NAV is still required for any new ordinary BUY claim.

A later automatic +10% trigger or manual whole-cycle request transfers coordination
to the global exit. It inherits each scoped anchor and consumed order budget,
and uses the earlier of the scoped and global absolute deadlines. A valid scoped
SELL remains working through the global cancellation barrier. Unknown, expired,
obsolete or cancellation-pending orders are never retained as valid; no new SELL
is admitted until the relevant reconciliation barrier clears. Global snapshots'
`slots` then carry execution state; `allocation_exits` retain handoff provenance.

## Completion, resume and remaining scope

A scoped exit completes only after base reaches zero and all orders/ownership
are reconciled. Quote proceeds and fee balances remain owned by the allocation;
completion is not a claim of reporting-currency conversion or portfolio return.
The allocation stays protected until a separate confirmed resume. That resume
rechecks fresh portfolio evidence and zero reconciled inventory, removes the
current scoped latch and resets the empty strategy round in the same transaction
as its operational audit. Earlier exit snapshots and receipts remain immutable.
Resume of an unresolved exit can clear holds but cannot restart ordinary trading.

At this increment nonzero residuals remained blocked. The subsequent
`I8-SCOPED-DUST.md` implements approved dust completion and confirmed resume while
retaining inventory, cost and shared allowance. It adds no implicit conversion.
Hot configuration and backup/restore CLI follow-ups are in `I8-HOT-CONFIG.md` and
`I8-STORAGE-CONTROLS.md`; research aliases remain subsequent offline work. The
observed 48-hour and first-venue/account acceptance gates remain incomplete.

## Compatibility and recovery

SQL schema v5 and dependencies are unchanged. Cycle snapshots gain a bounded,
unique `allocation_exits` tuple, omitted canonically when empty. Old active,
automatic and manual cycle records still decode without rewriting their bytes.
Unknown fields, missing required fields and explicit redundant empty encodings
are refused. Every retained scoped approval is checked against the cycle's
immutable policy digest. Older readers cannot interpret the new records: preserve
the ledger and use a compatible runtime, or verify a separate pre-change backup
for a disposable mock session. Never delete audit history to permit downgrade.

## Validation

| Requirement | Relevant coverage |
|---|---|
| PRD §3: confirmed fills, one SELL, no blind retries | Scoped partial/late-fill and unknown-ACK restart scenarios in `test_allocation_exit.py` |
| PRD §4: independent NAV trigger, bounded global exit | Automatic/manual handoff, original deadline/order budget and NAV-cadence scenarios |
| PRD §5: no orphaned inventory on retirement | Protected scoped modes through operational controls and universe deny; full hot-configuration CLI remains unfinished |
| PRD §10: explicit targets, confirmation and audited revision | Separate-process CLI selector tests, selected-roster preflight and exact receipt replay |
| ADR-0001: one durable writer and recoverable state | SIGKILL admission/release boundaries, backup/restore and historical codec checks |

Scoped records are bounded by the existing policy roster (at most 1,000 unique
allocations), and existing IPC, queue, order-history and storage bounds remain.
Policy integrity now computes its digest once per read, then compares every
approval, avoiding repeated serialization of the entire policy for each scope.
This increment does not establish a 1,000-allocation throughput or soak result.

Focused implementation pass: **46 tests passed in 39.58 seconds**, covering the
new paths plus overlapping manual/process/codec regressions. Early focused runs
found an actual duplicate journal posting during scoped resume; release and
control now use one final allocation posting. They also exposed an uncaught
stale-book `ReadError` in the supervisor's NAV loop; it now reports stale evidence
and continues permitted cancellation work. Test fixture assumptions about BUY
admission, reconciliation and mutable slot reasons were corrected without
relaxing production guards.

Coverage includes two venues with unchanged non-target ownership, partial and
late fills, one-SELL replacement after terminal proof, explicit empty resume,
unknown ACK/restart with no resend, backup/restore, atomic selected-roster refusals,
control/universe protection, scoped dust retention, NAV cadence and unrelated
stale books, automatic/manual global handoff with unchanged bounds, exact replay,
separate CLI processes and byte-compatible historical snapshots. Real SIGKILL
tests check both exit admission and completed-scope release at transaction
boundaries. The additional exit/release crash selection passed **25 tests in
11.98 seconds**. These overlapping focused counts are not added to suite totals.

| Check | Result |
|---|---|
| Full pytest, Python 3.12.16, final source | **732 passed**, 164.44 s; no failures/skips |
| Full pytest, Python 3.13.18, final source | **732 passed**, 168.40 s; no failures/skips |
| Ruff lint/format | Passed |
| Strict mypy | Passed, 141 source/test files |
| Bandit (`src`, `scripts`) | Passed |
| Locked dependency graph | 66 packages resolved; unchanged |
| Advisory audit of all dependency groups | No known vulnerabilities found |
| Secret scan, including all staged additions | Passed; baseline unchanged |
| Wheel contents | All 67 application modules match the source bytes |
| Imported specifications and retained public journals | Recorded SHA-256 values unchanged |
| Hosted CI / live or sandbox execution | Not run; authorization and account-lock boundaries unchanged |

The first full 3.12 run passed 732 tests in 164.11 seconds. Final review then
removed repeated policy hashing, required an enclosing transaction for scoped
release, and clarified an error message; the final runtime matrix covers those
changes. The application-source digest is
`1b75d29c93d4ac54496558d06d1db22edb44e20ea2d403d081a1e9963bf89dd9`.
Build artifacts are outside Git at `/tmp/grid-i8-allocation-exit-dist/`.
