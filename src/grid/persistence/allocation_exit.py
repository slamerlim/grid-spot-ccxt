"""Scoped operator liquidation, sharing the portfolio's sole writer and exit rules."""

from dataclasses import replace

from grid.domain.model import Command, Mode, Scope
from grid.domain.numbers import ZERO, DomainError
from grid.exchange.models import ReadError
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.persistence.portfolio import Rows, load, persist, require, save_rows
from grid.persistence.records import Conflict, LedgerError, Outcome
from grid.portfolio.model import AllocationExit, CyclePhase, ExitApproval, ExitSlot, ValuationFacts
from grid.portfolio.orders import plan_cancellations, plan_exit
from grid.portfolio.valuation import Conversions, fresh, market_facts


def request(
    ledger: Ledger,
    request_id: str,
    scopes: tuple[Scope, ...],
    approval: ExitApproval,
    facts: ValuationFacts,
    ms: int,
    ns: int,
    actor: str,
    reason: str,
) -> None:
    if not ledger.db.in_transaction:
        raise LedgerError("allocation exit requires an audited transaction")
    cycle = ledger.portfolio()
    if (
        cycle is None
        or cycle.phase is not CyclePhase.ACTIVE
        or cycle.id != approval.cycle_id
        or codec.digest(cycle.policy) != approval.policy_sha256
    ):
        raise Conflict("reviewed active cycle and exit policy required")
    if ms < cycle.observation_ms or cycle.pending_flows:
        raise LedgerError("regressed clock or unclassified cashflow blocks allocation exit")
    if (
        not scopes
        or len(set(scopes)) != len(scopes)
        or any(cycle.protects(s) or s in cycle.policy.sinks for s in scopes)
    ):
        raise LedgerError("allocation exit already latched or target is a conversion sink")
    rows = load(ledger, cycle.policy)
    if not set(scopes) <= set(rows):
        raise LedgerError("allocation exit outside supervised ownership")
    fresh(facts, cycle.policy, ms, ns)
    after: Rows = {}
    commands: dict[Scope, tuple[Command, ...]] = {}
    exits = list(cycle.allocation_exits)
    for scope in scopes:
        state, context, version, recovery = rows[scope]
        if recovery or state.reconcile_reason is not None or any(o.unknown for o in state.working):
            raise LedgerError("allocation exit requires reconciled ownership")
        observed = market_facts(state, context, facts, cycle.policy, ms, ns)
        context = replace(observed.context, now_ms=ms, allow_new_exposure=False)
        Conversions(facts, cycle.policy, ms, ns).check_fee(state, context)
        state = replace(
            state, mode=Mode.PORTFOLIO_EXIT, control_revision=state.control_revision + 1
        )
        unsent = frozenset(o.id for o in state.working if not ledger.attempted(scope, o.id))
        state, commands[scope] = plan_cancellations(state, context, unsent)
        after[scope] = state, context, version, recovery
        exits.append(
            AllocationExit(
                ExitSlot(scope, observed.book.bids[0][0], 0, None, None, "cancel and reconcile"),
                approval,
                CyclePhase.REQUESTED,
                ms,
                ms,
                actor,
                reason,
            )
        )
    save_rows(ledger, request_id, rows, after, commands)
    persist(
        ledger,
        request_id,
        replace(cycle, revision=cycle.revision + 1, allocation_exits=tuple(exits)),
    )


def advance(
    ledger: Ledger,
    request_id: str,
    expected: int,
    facts: ValuationFacts,
    ms: int,
    ns: int,
    actor: str,
    reason: str,
) -> Outcome:
    def action() -> Outcome:
        cycle = require(ledger, expected)
        if cycle.phase is not CyclePhase.ACTIVE or not cycle.allocation_exits:
            raise Conflict("no active allocation exit coordinator")
        if ms < cycle.observation_ms:
            raise DomainError("allocation exit clock regressed")
        rows = load(ledger, cycle.policy)
        after: Rows = {}
        commands: dict[Scope, tuple[Command, ...]] = {}
        exits: list[AllocationExit] = []
        for entry in cycle.allocation_exits:
            if entry.phase is CyclePhase.COMPLETE:
                exits.append(entry)
                continue
            slot = entry.slot
            scope = slot.scope
            state, context, version, recovery = rows[scope]
            context = replace(context, now_ms=ms, allow_new_exposure=False)
            unsent = frozenset(o.id for o in state.working if not ledger.attempted(scope, o.id))
            phase = CyclePhase.BLOCKED
            try:
                fresh(facts, cycle.policy, ms, ns)
                observed = market_facts(state, context, facts, cycle.policy, ms, ns)
                context = replace(observed.context, now_ms=ms, allow_new_exposure=False)
                Conversions(facts, cycle.policy, ms, ns).check_fee(state, context)
                if recovery or cycle.pending_flows:
                    raise DomainError("reconciliation or unclassified cashflow pending")
                rule = next(r for r in cycle.policy.exits if r.scope == scope)
                state, commands[scope], slot = plan_exit(
                    state, context, rule, slot, ms, cycle.deadline_ms(scope), unsent
                )
                if (
                    state.reconcile_reason is None
                    and not state.working
                    and state.owned_base == ZERO
                ):
                    phase = CyclePhase.COMPLETE
                elif state.working and not any(o.unknown for o in state.working):
                    phase = CyclePhase.EXITING
            except (DomainError, ReadError) as exc:
                state, commands[scope] = plan_cancellations(state, context, unsent)
                slot = replace(slot, reason=str(exc)[:128])
            after[scope] = state, context, version, recovery
            exits.append(replace(entry, slot=slot, phase=phase, observed_ms=ms))
        save_rows(ledger, request_id, rows, after, commands)
        return persist(
            ledger,
            request_id,
            replace(cycle, revision=expected + 1, allocation_exits=tuple(exits)),
        )

    return ledger._request(
        request_id,
        ("allocation_exit_advance", expected, codec.digest(facts), ms, actor, reason),
        "allocation_exit_advance",
        None,
        action,
    )


def release_completed(
    ledger: Ledger, request_id: str, scopes: tuple[Scope, ...]
) -> frozenset[Scope]:
    """Only an audited, confirmed RESUME can release a zero-inventory scoped exit."""
    if not ledger.db.in_transaction:
        raise LedgerError("allocation release requires an audited transaction")
    cycle = ledger.portfolio()
    if cycle is None or cycle.phase is not CyclePhase.ACTIVE:
        return frozenset()
    completed = {
        e.slot.scope for e in cycle.allocation_exits if e.phase is CyclePhase.COMPLETE
    } & set(scopes)
    if not completed:
        return frozenset()
    rows = load(ledger, cycle.policy)
    for scope in completed:
        state, _, _, recovery = rows[scope]
        if (
            recovery
            or state.reconcile_reason is not None
            or state.working
            or state.owned_base != ZERO
        ):
            raise LedgerError("completed allocation exit needs zero reconciled inventory")
    persist(
        ledger,
        request_id,
        replace(
            cycle,
            revision=cycle.revision + 1,
            allocation_exits=tuple(
                e for e in cycle.allocation_exits if e.slot.scope not in completed
            ),
            last_evaluation_ms=cycle.observation_ms,
        ),
    )
    return frozenset(completed)
