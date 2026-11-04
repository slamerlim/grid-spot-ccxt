"""Audited operational controls on the existing writer; no gateway side effects."""

from collections.abc import Callable
from dataclasses import replace

from grid.domain.model import Command as DomainCommand
from grid.domain.model import Control, Mode, OrderReport, Phase, Scope, Side
from grid.domain.numbers import ZERO, bounded_int
from grid.domain.reducer import _cancel, reduce
from grid.operations.model import Action, AllocationStatus, BackupStamp, Command, Operations
from grid.persistence import allocation_exit, codec
from grid.persistence.ledger import Ledger
from grid.persistence.portfolio import request_manual_exit, save_rows
from grid.persistence.records import Conflict, LedgerError, Outcome
from grid.portfolio.model import ExitApproval, ValuationFacts


def current(ledger: Ledger) -> Operations | None:
    return ledger.operations()


def commit(
    ledger: Ledger,
    request_id: str,
    expected: int,
    command: Command,
    roster: tuple[Scope, ...],
    resume_check: Callable[[], None],
    *,
    exit_approval: ExitApproval | None = None,
    exit_evidence: tuple[ValuationFacts, int, int] | None = None,
) -> Outcome:
    bounded_int(expected, 0, 2**63 - 2)
    is_exit = command.action in (Action.LIMIT_LIQUIDATE, Action.LIQUIDATE_ALLOCATION)
    if is_exit != (exit_approval is not None) or not is_exit and exit_evidence is not None:
        raise LedgerError("exit approval is required only for manual limit liquidation")

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
        released: frozenset[Scope] = frozenset()
        if command.action in (Action.START, Action.HALT, Action.SHUTDOWN):
            if set(command.scopes) != set(roster):
                raise LedgerError("global halt requires full roster")
            halted = True
        elif command.action is Action.STOP:
            held.update(command.scopes)
        elif command.action is Action.RESUME:
            resume_check()
            released = allocation_exit.release_completed(ledger, request_id, command.scopes)
            if released:
                ledger.storage.fault("operations:after_release")
            held.difference_update(command.scopes)
            if set(command.scopes) == set(roster):
                halted = False
        if is_exit:
            if (
                command.action is Action.LIMIT_LIQUIDATE
                and set(command.scopes) != set(roster)
                or exit_approval is None
                or exit_evidence is None
            ):
                raise LedgerError(
                    "manual exit needs fresh evidence; whole-cycle exit needs full roster"
                )
            facts, ms, ns = exit_evidence
            if command.action is Action.LIQUIDATE_ALLOCATION:
                allocation_exit.request(
                    ledger,
                    request_id,
                    command.scopes,
                    exit_approval,
                    facts,
                    ms,
                    ns,
                    command.actor,
                    command.reason,
                )
            else:
                request_manual_exit(
                    ledger, request_id, exit_approval, facts, ms, ns, command.actor, command.reason
                )
            ledger.storage.fault("operations:after_liquidation")
        rows = {} if is_exit else {s: ledger._load(codec.digest(s)) for s in roster}
        after = dict(rows)
        commands: dict[Scope, tuple[DomainCommand, ...]] = {}
        cycle = ledger.portfolio()
        for scope in () if is_exit else command.scopes:
            latched = cycle is not None and cycle.protects(scope)
            state, context, version, recovery = rows[scope]
            if scope in released:
                state = replace(
                    state,
                    phase=Phase.INACTIVE,
                    seed_id=None,
                    anchor=None,
                    round_id=state.round_id + 1,
                    ladder_credits=0,
                )
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

    payload: tuple[object, ...] = ("operations", expected, command, roster)
    if exit_approval is not None:
        payload += (exit_approval,)
    return ledger._request(request_id, payload, "operations", None, action)


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
