# I8 offline follow-up — audited universe controls

Base: `6d6e357859fa51067ee437d660d499b05cbc0bb8`, branch `i8/public-paper-hour`.
This implements the remaining local universe controls under the owner's approved
continued mock development. No exchange access, credential discovery, real/sandbox
order, transfer or deployment is involved. GitHub remains Free-only; hosted CI
stays pending under its unchanged lock, and publication uses `[skip ci]`.

## Operator behavior

`gridctl operator universe --socket PATH` shows the durable policy, universe
revision, selected membership and control ownership. `status` includes that
snapshot alongside the operational revision. Every configuration command uses
both `--revision` and `--universe-revision`, an actor derived from the local peer,
reason and private saved request. All require a preview followed by a second
invocation with `--confirm`; the first invocation cannot already confirm.

| Command | Required edit arguments | Meaning |
|---|---|---|
| `enable-venue` | Repeated `--venue ID` | Enable known, already supervised venues |
| `disable-venue` | Repeated `--venue ID`, `--retirement MODE` | Exclude those venues while retaining every allocation |
| `set-symbol-mode` | `--mode automatic`, `manual` or `hybrid` | Change selection mode without bypassing eligibility |
| `allow` | `--allocation ID` (repeatable) or `--all` | Add to allowlist and remove explicit denial |
| `deny` | Allocation selectors, `--retirement MODE` | Add explicit denial; exclusion wins over pinning |
| `pin` / `unpin` | Allocation selectors | Change ranking preference; never override denial or eligibility |
| `set-capacity` | `--capacity N` | Set the active-selection cap within the existing candidate bound |
| `refresh-universe` | No extra target argument | Re-evaluate the current policy using bounded cached facts |

Configuration acts on the fixed, pre-funded supervised roster. It cannot create
funding, import a wallet, discover new exchange accounts or drop ownership. Unknown
and ambiguous targets are refused. Selection still uses the existing documented
cost/depth gates, residency and hysteresis. Cache limits, risk policy, market/fee
generations and portfolio exit policy are not editable through these commands.

Changes to capacity, mode, pinning or fresh eligibility may retire unselected
members through the existing manage-existing path. They never silently liquidate
an allocation. An enable/allow/selection refresh may readmit a retirement owned by
the universe, but cannot override a separate pause, stop, exit or halt. A confirmed
readmission can reset an empty, terminal, reconciled universe-owned round; a new
minimum seed still needs fresh evidence and all send guards. Startup/global halt
and held operational scopes stay in effect, requiring their separate controls.

## Explicit retirement choices

`deny` and `disable-venue` require one of these choices, with no silent default:

| Retirement | Existing orders and inventory |
|---|---|
| `manage-existing` | Stop new BUYs, revoke proven unsent BUYs and request cancellation of other BUYs. Continue existing take-profit/inventory supervision; no new seed while retired. |
| `cancel-buys-and-hold` | Hold inventory: cancel outstanding BUYs **and SELLs**, revoke proven unsent orders, continue ingesting/reconciling fills, and block every new placement. A SELL may still fill until its cancellation is terminal. |
| `bounded-limit-exit` | Atomically add the denial/venue exclusion and request scoped LIMIT liquidation for the selected principal allocations under the reviewed current cycle policy. |

Hold has an explicit `hold_inventory` reducer mode and is checked again at the
durable send claim. It does not discard inventory, release uncertain reservations
or stop reconciliation. Its cancellation of existing SELLs is necessary to hold
the remaining inventory; this is distinct from manage-existing TP behavior.
The existing `pause` command retains its earlier meaning and is not renamed hold.

Bounded exit additionally requires `--cycle` and `--policy-sha256` from status.
Every new scoped exit requires fresh verified rules, book, fees and reconciled
ownership. If those prerequisites fail, the entire combined edit is refused;
neither denial nor partial liquidation is recorded. Manage-existing or hold can
still be requested to restrict an uncertain allocation without authorizing a new
SELL. Conversion sinks are not valid new scoped-liquidation targets. Already
latched scoped exits retain their earlier anchor, deadline and budget.

A later global portfolio exit supersedes universe retirement/hold and retains
ownership of disabled venues. Subsequent universe edits cannot reactivate those
exit modes. Scoped dust remains owned and blocked under the prior increment's
rules; this change does not add dust completion or implicit asset conversion.

## Atomicity, replay and compatibility

The operational CAS check, universe CAS check, effective policy/membership,
retirement effects, optional scoped-exit approval, cancellations/reservations,
actor/reason audit and receipt commit through one existing SQLite writer and
transaction. No nested request transaction or network wait is added. Scoped-exit
rows posted earlier in the same transaction are retained by the universe update
without duplicate financial journal entries. Their supervision remains present.

The commit invalidates a placement that has not yet passed the durable send claim.
A submit claimed before the edit may still finish after it; that order remains
possibly live and must reconcile/cancel through the existing executor. Neither
policy persistence nor a cancellation acknowledgement proves an external order
has disappeared.

Canonical `UniverseEdit` data and any exit approval are included in the immutable
request event and identity fingerprint. Exact receipt replay works after policy
changes or restart and cannot clear a later halt. Changed targets, action, mode,
revision or retirement conflict with the retained request. On an unknown response,
retain the request, inspect status and explicitly repeat the exact packet only;
there is no automatic client retry. On a known revision conflict, inspect both
revisions and prepare a new reviewed request rather than editing the retained one.

SQL schema v5 and dependencies are unchanged. Existing snapshots retain their
bytes and codec contracts. New command/retirement enums and edit records are
allowlisted; unknown fields, invalid types, boolean capacities and string-as-list
venue inputs are refused. Older runtimes cannot interpret new hold/edit records:
retain all ledgers and history and use a compatible runtime, or a separate verified
pre-change backup for a disposable mock session. Do not downgrade an active owner
or alter source-digest markers to resume a session under changed code.

## Validation and traceability

The existing universe/domain/operator/scoped-exit selection passed **171 tests in
33.97 seconds**. The first new configuration/CLI selection passed **42 tests in
20.80 seconds**. The expanded configuration integration selection passed **27
tests in 17.64 seconds**. These selections overlap and are not added together.

| Requirement | Evidence |
|---|---|
| PRD §5, Q11: runtime modes and ownership | All nine edits, deny-over-pin, capacity, explicit retirement choices, unrelated venue retention and hold/readmission scenarios |
| Q05/Q08/Q09: late fills and uncertain effects | Existing SELL fills during hold/exit cancellation; disabled unknown BUY remains reconciled and is never resubmitted |
| PRD §4/Q12: global ownership | Global LIMIT exit supersedes held inventory; later allow cannot clear the global exit |
| PRD §10: reviewed controls | Two-step confirmation for every edit, both revision checks, strict protocol, immutable saved packet and separate-process restart/replay |
| ADR-0001/Q19: one atomic writer | Nine real SIGKILL cases at combined-exit/configuration/posting/commit boundaries, plus backup/restore |
| Q17: stale generations cannot spend | A concurrent hold committed immediately before a prepared SELL claim prevents that send; existing universe admission regressions remain covered |

The source review retained bounded rosters (at most 1,000 allocations/seven
venues), bounded IPC, one writer and one fake gateway. No benchmark here proves
production throughput or a 48-hour soak.

| Final check | Result |
|---|---|
| Full pytest, Python 3.12.16 | **776 passed**, 186.57 s; no failures/skips |
| Full pytest, Python 3.13.18 | **776 passed**, 192.29 s; no failures/skips |
| Ruff lint/format | Passed |
| Strict mypy | Passed, 145 source/test files |
| Bandit (`src`, `scripts`) | Passed |
| Secret scan, including staged additions | Passed; baseline unchanged |
| Lock and dependency advisory audit | Unchanged 66-package graph; no known vulnerabilities found |
| Wheel contents | All 68 application modules match source bytes |
| Original/imported specifications and preserved public journals | Recorded hashes unchanged |
| Hosted CI / exchange execution | Not run; account lock and authorization boundaries unchanged |

The application-source digest is
`fb6fee8e6d58f12ce4901cfaebb1af650f5d8a0906b287d42e3dd26bb3955bf1`.
Build artifacts are outside Git at `/tmp/grid-i8-hot-config-dist/`.

Remaining work includes scoped dust completion, owner-mediated backup/restore CLI
and research/storage aliases. Public/private CCXT capability gaps, the full
observed 48-hour gate, intended-first-venue evidence and I9 remain separately
gated. The completed public-hour authorization is spent and its evidence remains
preserved. No mock result is an authorization to deploy or trade.
