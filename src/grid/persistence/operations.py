"""Audited operational controls on the existing writer; no gateway side effects."""

from collections.abc import Callable
from dataclasses import replace

from grid.domain.model import Command as DomainCommand
from grid.domain.model import Control, Mode, OrderReport, Scope, Side
from grid.domain.numbers import ZERO, bounded_int
from grid.domain.reducer import _cancel, reduce
from grid.operations.model import Action, AllocationStatus, BackupStamp, Command, Operations
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.persistence.portfolio import save_rows
from grid.persistence.records import Conflict, LedgerError, Outcome
from grid.portfolio.model import CyclePhase


def current(ledger: Ledger) -> Operations | None:
    return ledger.operations()


def commit(
    ledger: Ledger,
    request_id: str,
    expected: int,
    command: Command,
    roster: tuple[Scope, ...],
    resume_check: Callable[[], None],
) -> Outcome:
    bounded_int(expected, 0, 2**63 - 2)

    def action() -> Outcome:
        previous = current(ledger)
        if expected != (previous.revision if previous else 0):
            raise Conflict("stale operational revision")
        if {codec.digest(s) for s in roster} != set(ledger._allocation_ids()):
            raise LedgerError("complete operational roster required")
        if previous is not None and (
            set(previous.scopes) != set(roster)
            or command.observed_ms < previous.command.observed_ms
        ):
            raise Conflict("operational roster or clock regressed")
        if not set(command.scopes) <= set(roster):
            raise LedgerError("control outside supervised ownership")
        if previous is None and command.action is not Action.START:
            raise LedgerError("startup halt required")
        halted = previous.halted if previous else True
        held = set(previous.held if previous else ())
        if command.action in (Action.START, Action.HALT, Action.SHUTDOWN):
            if set(command.scopes) != set(roster):
                raise LedgerError("global halt requires full roster")
            halted = True
        elif command.action is Action.STOP:
            held.update(command.scopes)
        elif command.action is Action.RESUME:
            resume_check()
            held.difference_update(command.scopes)
            if set(command.scopes) == set(roster):
                halted = False
        rows = {s: ledger._load(codec.digest(s)) for s in roster}
        after = dict(rows)
        commands: dict[Scope, tuple[DomainCommand, ...]] = {}
        cycle = ledger.portfolio()
        latched = cycle is not None and cycle.phase is not CyclePhase.ACTIVE
        for scope in command.scopes:
            state, context, version, recovery = rows[scope]
            context = replace(context, now_ms=command.observed_ms, allow_new_exposure=False)
            desired = {
                Action.PAUSE: Mode.PAUSED,
                Action.CANCEL_BUYS: Mode.PAUSED,
                Action.RETIRE: Mode.RETIRING,
                Action.RESUME: Mode.ACTIVE,
            }.get(command.action)
            emitted: list[DomainCommand] = []
            if desired is not None and not latched:
                transition = reduce(
                    state, Control(scope, state.control_revision + 1, desired), context
                )
                if not transition.accepted:
                    raise LedgerError("operational control refused")
                state = transition.state
                emitted.extend(transition.commands)
            if command.action in (Action.CANCEL_BUYS, Action.RETIRE):
                for order in tuple(o for o in state.working if o.side is Side.BUY):
                    if (
                        not ledger.attempted(scope, order.id)
                        and not order.unknown
                        and order.filled == ZERO
                    ):
                        transition = reduce(
                            state, OrderReport(scope, order.id, "canceled", ZERO, True), context
                        )
                        state = transition.state
                        emitted.extend(transition.commands)
                    else:
                        state, pending = _cancel(state, order)
                        emitted.extend(pending)
            after[scope] = state, context, version, recovery
            commands[scope] = tuple(emitted)
        save_rows(ledger, request_id, rows, after, commands)
        updated = Operations(
            expected + 1,
            roster,
            halted,
            tuple(s for s in roster if s in held),
            command,
            previous.backup if previous else None,
        )
        ledger.db.execute(
            "INSERT INTO operations_revisions VALUES (?,?,?)",
            (updated.revision, request_id, ledger._text(updated)),
        )
        return Outcome(None, updated.revision, True)

    return ledger._request(
        request_id, ("operations", expected, command, roster), "operations", None, action
    )


def backup_verified(ledger: Ledger, request_id: str, expected: int, stamp: BackupStamp) -> Outcome:
    def action() -> Outcome:
        previous = current(ledger)
        if previous is None or previous.revision != expected:
            raise Conflict("stale backup control revision")
        if previous.backup is not None and stamp.observed_ms < previous.backup.observed_ms:
            raise Conflict("backup clock regressed")
        updated = replace(previous, revision=expected + 1, backup=stamp)
        ledger.db.execute(
            "INSERT INTO operations_revisions VALUES (?,?,?)",
            (updated.revision, request_id, ledger._text(updated)),
        )
        return Outcome(None, updated.revision, True)

    return ledger._request(
        request_id, ("verified_backup", expected, stamp), "verified_backup", None, action
    )


def allocations(ledger: Ledger, scopes: tuple[Scope, ...]) -> tuple[AllocationStatus, ...]:
    from grid.domain.market import Money
    from grid.domain.numbers import exact

    @exact
    def read(scope: Scope) -> AllocationStatus:
        state, _, version, recovery = ledger._load(codec.digest(scope))
        from decimal import Decimal

        totals: dict[str, Decimal] = {}
        for fill in state.fills:
            for fee in fill.fees or ():
                totals[fee.currency] = totals.get(fee.currency, ZERO) + fee.amount
        queued = ledger.db.execute(
            "SELECT count(*) FROM intents WHERE allocation=? AND kind='cancel' AND status='queued'",
            (codec.digest(scope),),
        ).fetchone()[0]
        return AllocationStatus(
            scope,
            state.mode,
            version,
            recovery or state.reconcile_reason is not None,
            state.quote_cash,
            state.owned_base,
            len(state.working),
            sum(o.unknown for o in state.working),
            int(queued),
            tuple(Money(c, a) for c, a in sorted(totals.items())),
        )

    return tuple(read(s) for s in scopes)
