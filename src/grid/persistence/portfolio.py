"""Portfolio records and mutations on the existing sole SQLite writer."""

from dataclasses import replace
from decimal import Decimal

from grid.domain.market import Money
from grid.domain.model import (
    Command,
    Context,
    Control,
    Mode,
    Phase,
    Scope,
    Side,
    State,
    TransferredLot,
)
from grid.domain.numbers import ZERO, DomainError, arithmetic, identifier
from grid.domain.reducer import reduce
from grid.exchange.models import ReadError
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.persistence.records import Conflict, LedgerError, Outcome
from grid.portfolio.model import (
    Cashflow,
    Cycle,
    CyclePhase,
    ExitApproval,
    ExitSlot,
    Nav,
    PoolKey,
    PortfolioPolicy,
    Residual,
    ValuationFacts,
)
from grid.portfolio.orders import plan_cancellations, plan_exit, retain_exit
from grid.portfolio.valuation import Conversions, fresh, market_facts, sell_quantity, valuation
from grid.universe.model import MarketKey

type Rows = dict[Scope, tuple[State, Context, int, bool]]


def current(ledger: Ledger) -> Cycle | None:
    return ledger.portfolio()


def require(ledger: Ledger, expected: int) -> Cycle:
    cycle = current(ledger)
    if cycle is None or type(expected) is not int or cycle.revision != expected:
        raise Conflict("stale portfolio revision")
    return cycle


def load(ledger: Ledger, policy: PortfolioPolicy) -> Rows:
    rows = {rule.scope: ledger._load(codec.digest(rule.scope)) for rule in policy.exits}
    if set(ledger._allocation_ids()) != {codec.digest(scope) for scope in rows}:
        raise LedgerError("portfolio must supervise every registered allocation")
    return rows


def nav(
    ledger: Ledger, rows: Rows, facts: ValuationFacts, policy: PortfolioPolicy, ms: int, ns: int
) -> Nav:
    if any(recovery for _, _, _, recovery in rows.values()):
        raise LedgerError("portfolio startup reconciliation required")
    return valuation(
        tuple((s, c) for s, c, _, _ in rows.values()),
        tuple((key, ledger.pool(key.venue, key.account, key.currency)) for key in policy.pools),
        facts,
        policy,
        ms,
        ns,
    )


def persist(ledger: Ledger, request_id: str, cycle: Cycle) -> Outcome:
    ledger.db.execute(
        "INSERT INTO portfolio_revisions VALUES (?,?,?,?)",
        (cycle.revision, cycle.id, request_id, ledger._text(cycle)),
    )
    return Outcome(None, cycle.revision, True)


def save_rows(
    ledger: Ledger,
    request_id: str,
    before: Rows,
    after: Rows,
    commands: dict[Scope, tuple[Command, ...]],
) -> None:
    for scope, (state, context, version, recovery) in after.items():
        original = before[scope][0]
        allocation = codec.digest(scope)
        if original != state or before[scope][1] != context or commands.get(scope):
            ledger._changes(request_id, allocation, original, state, context)
            ledger._save(allocation, state, context, version + 1, recovery=recovery)
            ledger._commands(allocation, commands.get(scope, ()), version + 1)


def register_sink(ledger: Ledger, request_id: str, scope: Scope, context: Context) -> Outcome:
    """Explicit empty, paused conversion allocation; no synthetic purchase/fill."""

    def action() -> Outcome:
        previous = current(ledger)
        if previous is not None:
            raise LedgerError("cannot change ownership during a portfolio cycle")
        state = State(
            scope, ZERO, (), context.config_revision, context.market.generation, mode=Mode.PAUSED
        )
        safe = replace(context, allow_new_exposure=False)
        allocation = codec.digest(scope)
        ledger.db.execute(
            "INSERT INTO allocations VALUES (?,?,?,?,1,1)",
            (allocation, ledger._text(scope), ledger._text(state), ledger._text(safe)),
        )
        ledger._changes(request_id, allocation, None, state, safe)
        ledger._save(allocation, state, safe, 1, recovery=True)
        return Outcome(state, 1, True)

    return ledger._request(request_id, ("conversion_sink", scope, context), "sink", None, action)


def begin(
    ledger: Ledger,
    request_id: str,
    expected: int,
    cycle_id: str,
    policy: PortfolioPolicy,
    facts: ValuationFacts,
    ms: int,
    ns: int,
    actor: str,
    reason: str,
    resume: tuple[Scope, ...],
) -> Outcome:
    for value in (cycle_id, actor, reason):
        identifier(value)

    def action() -> Outcome:
        previous = current(ledger)
        if (
            type(expected) is not int
            or expected != (previous.revision if previous else 0)
            or previous is not None
            and previous.phase is not CyclePhase.COMPLETE
        ):
            raise Conflict("previous portfolio cycle is unresolved")
        if ledger.db.execute(
            "SELECT 1 FROM portfolio_revisions WHERE cycle_id=? LIMIT 1", (cycle_id,)
        ).fetchone():
            raise Conflict("cycle identity cannot be reused")
        rows = load(ledger, policy)
        if not set(resume) <= set(rows) - set(policy.sinks) or len(set(resume)) != len(resume):
            raise LedgerError("invalid explicit resume scopes")
        for scope in policy.sinks:
            state, context, _, _ = rows[scope]
            if context.market.quote != policy.reporting or context.sell_fee.currency not in (
                context.market.base,
                context.market.quote,
            ):
                raise LedgerError(
                    "conversion sinks require direct reporting market and base/quote fees"
                )
            if any(order.side is Side.BUY for order in state.working):
                raise LedgerError("conversion sink cannot carry a working BUY")
        baseline = nav(ledger, rows, facts, policy, ms, ns)
        if baseline.net <= ZERO or facts.liabilities:
            raise DomainError("positive reconciled baseline without unpaid liabilities required")
        after = dict(rows)
        commands: dict[Scope, tuple[Command, ...]] = {}
        for scope, (state, context, version, recovery) in rows.items():
            if previous is not None and state.working:
                raise LedgerError("restart cannot inherit unresolved orders")
            if scope in resume:
                if not state.lots:
                    state = replace(
                        state,
                        phase=Phase.INACTIVE,
                        seed_id=None,
                        anchor=None,
                        round_id=state.round_id + 1,
                        ladder_credits=0,
                    )
                transition = reduce(
                    state, Control(scope, state.control_revision + 1, Mode.ACTIVE), context
                )
                state, commands[scope] = transition.state, transition.commands
            elif scope in policy.sinks:
                state = replace(
                    state, mode=Mode.PAUSED, control_revision=state.control_revision + 1
                )
            after[scope] = state, context, version, recovery
        save_rows(ledger, request_id, rows, after, commands)
        cycle = Cycle(
            cycle_id,
            expected + 1,
            CyclePhase.ACTIVE,
            policy,
            baseline,
            ZERO,
            None,
            None,
            None,
            ms,
            (),
            (),
            (),
            reason,
            actor,
        )
        return persist(ledger, request_id, cycle)

    return ledger._request(
        request_id,
        (
            "portfolio_begin",
            expected,
            cycle_id,
            policy,
            codec.digest(facts),
            ms,
            actor,
            reason,
            resume,
        ),
        "portfolio_begin",
        None,
        action,
    )


@arithmetic
def latch_exit(
    ledger: Ledger,
    request_id: str,
    cycle: Cycle,
    rows: Rows,
    facts: ValuationFacts,
    value: Nav,
    ms: int,
    manual_exit: ExitApproval | None = None,
) -> Cycle:
    """Join the caller's transaction; revoke unsent intents and retain uncertain orders."""
    if not ledger.db.in_transaction:
        raise LedgerError("exit latch requires an enclosing financial transaction")
    after = dict(rows)
    commands: dict[Scope, tuple[Command, ...]] = {}
    slots: list[ExitSlot] = []
    for scope, (state, _context, version, recovery) in rows.items():
        observation = next(f for f in facts.markets if f.key == MarketKey.of(scope))
        safe = replace(observation.context, now_ms=ms, allow_new_exposure=False)
        state = replace(
            state, mode=Mode.PORTFOLIO_EXIT, control_revision=state.control_revision + 1
        )
        unsent = frozenset(o.id for o in state.working if not ledger.attempted(scope, o.id))
        local = next(
            (e for e in cycle.allocation_exits if e.slot.scope == scope and not e.released), None
        )
        slot = (
            local.slot
            if local
            else ExitSlot(scope, observation.book.bids[0][0], 0, None, None, "cancel and reconcile")
        )
        rule = next(r for r in cycle.policy.exits if r.scope == scope)
        keep = (
            retain_exit(state, safe, rule, slot, ms, cycle.deadline_ms(scope))
            if local and not recovery
            else None
        )
        state, commands[scope] = plan_cancellations(state, safe, unsent, keep=keep)
        after[scope] = state, safe, version, recovery
        slots.append(slot)
    save_rows(ledger, request_id, rows, after, commands)
    return replace(
        cycle,
        phase=CyclePhase.REQUESTED,
        trigger=value,
        trigger_baseline=cycle.baseline.net + cycle.adjustment,
        started_exit_ms=ms,
        slots=tuple(slots),
        manual_exit=manual_exit,
    )


def request_manual_exit(
    ledger: Ledger,
    request_id: str,
    approval: ExitApproval,
    facts: ValuationFacts,
    ms: int,
    ns: int,
    actor: str,
    reason: str,
) -> None:
    """Called only inside the audited operational request transaction."""
    cycle = current(ledger)
    if (
        cycle is None
        or cycle.id != approval.cycle_id
        or codec.digest(cycle.policy) != approval.policy_sha256
        or cycle.phase is not CyclePhase.ACTIVE
    ):
        raise Conflict("reviewed active cycle and exit policy required")
    if ms < cycle.observation_ms or cycle.pending_flows:
        raise LedgerError("regressed clock or unclassified cashflow blocks manual exit")
    rows = load(ledger, cycle.policy)
    value = nav(ledger, rows, facts, cycle.policy, ms, ns)
    updated = replace(
        cycle, revision=cycle.revision + 1, last_evaluation_ms=ms, actor=actor, reason=reason
    )
    persist(
        ledger,
        request_id,
        latch_exit(ledger, request_id, updated, rows, facts, value, ms, approval),
    )


@arithmetic
def evaluate(
    ledger: Ledger,
    request_id: str,
    expected: int,
    facts: ValuationFacts,
    ms: int,
    ns: int,
    actor: str,
    reason: str,
    *,
    at_claim: bool = False,
) -> Outcome:
    def action() -> Outcome:
        cycle = require(ledger, expected)
        if cycle.phase is not CyclePhase.ACTIVE:
            raise Conflict("portfolio exit already latched")
        if ms < cycle.observation_ms:
            raise DomainError("portfolio clock regressed")
        if not at_claim and ms - cycle.last_evaluation_ms < cycle.policy.evaluation_interval_ms:
            raise LedgerError("portfolio evaluation rate budget")
        if cycle.pending_flows:
            raise LedgerError("unclassified external cashflow blocks valuation")
        rows = load(ledger, cycle.policy)
        value = nav(ledger, rows, facts, cycle.policy, ms, ns)
        updated = replace(
            cycle, revision=expected + 1, last_evaluation_ms=ms, actor=actor, reason=reason
        )
        if value.net >= (cycle.baseline.net + cycle.adjustment) * Decimal("1.10"):
            updated = latch_exit(ledger, request_id, updated, rows, facts, value, ms)
        return persist(ledger, request_id, updated)

    return ledger._request(
        request_id,
        ("portfolio_evaluate", expected, codec.digest(facts), ms, actor, reason, at_claim),
        "portfolio_evaluate",
        None,
        action,
    )


@arithmetic
def sweep(
    ledger: Ledger,
    request_id: str,
    rows: Rows,
    cycle: Cycle,
    facts: ValuationFacts,
    ms: int,
    ns: int,
) -> Rows:
    """Move owned fee/quote/pool assets to explicit same-account conversion sinks."""
    after = dict(rows)
    sinks: dict[PoolKey, Scope] = {}
    for scope in cycle.policy.sinks:
        state, context, _, _ = rows[scope]
        market_facts(state, context, facts, cycle.policy, ms, ns)
        key = PoolKey(scope.venue, scope.account, context.market.base)
        if key in sinks:
            raise DomainError("one conversion sink per account/currency required")
        sinks[key] = scope

    def transfer(key: PoolKey, amount: Decimal, provenance: str) -> bool:
        if amount == ZERO or key.currency == cycle.policy.reporting or key not in sinks:
            return False
        scope = sinks[key]
        state, context, version, recovery = after[scope]
        if state.working or state.reconcile_reason is not None or recovery:
            return False
        if context.book is None:
            raise DomainError("conversion transfer requires a fresh reference book")
        # This is an internal asset movement with a marked transfer basis, not a fill.
        lot = TransferredLot(provenance, amount, context.book.bid)
        if len(state.lots) >= context.policy.max_lots:
            raise DomainError("conversion lot bound")
        state = replace(
            state,
            lots=(*state.lots, lot),
            position_cost=state.position_cost + amount * context.book.bid,
        )
        after[scope] = state, context, version, recovery
        return True

    for source in tuple(rows):
        state, context, version, recovery = after[source]
        if source in cycle.policy.sinks:
            continue
        balances = list(state.fee_balances)
        for index, money in enumerate(balances):
            key = PoolKey(source.venue, source.account, money.currency)
            if transfer(key, money.amount, codec.digest((request_id, source, money.currency))):
                balances[index] = Money(money.currency, ZERO)
        quote = state.quote_cash
        if transfer(
            PoolKey(source.venue, source.account, context.market.quote),
            quote,
            codec.digest((request_id, source, "quote")),
        ):
            quote = ZERO
        after[source] = (
            replace(state, quote_cash=quote, fee_balances=tuple(balances)),
            context,
            version,
            recovery,
        )
    pool_moves: list[tuple[PoolKey, Decimal]] = []
    for key in cycle.policy.pools:
        amount = ledger.pool(key.venue, key.account, key.currency)
        if transfer(key, amount, codec.digest((request_id, key))):
            pool_moves.append((key, amount))
    # Validate every transfer before making any database changes. A caught planning
    # error cannot commit a pool debit without its matching allocation credit.
    ledger.db.execute("SAVEPOINT portfolio_sweep")
    try:
        for key, amount in pool_moves:
            ledger._pool_delta(request_id, key.venue, key.account, Money(key.currency, -amount))
    except Exception:
        ledger.db.execute("ROLLBACK TO portfolio_sweep")
        raise
    finally:
        ledger.db.execute("RELEASE portfolio_sweep")
    return after


@arithmetic
def unavoidable_dust(
    state: State, context: Context, cycle: Cycle, slot: ExitSlot | None = None
) -> bool:
    rule = next(r for r in cycle.policy.exits if r.scope == state.scope)
    slot = slot or next(s for s in cycle.slots if s.scope == state.scope)
    maximum = min(
        context.market.prices.maximum or slot.anchor_bid * (1 + rule.max_deviation),
        slot.anchor_bid * (1 + rule.max_deviation),
    )
    quantity = sell_quantity(state.owned_base, context.market, context.sell_fee, maximum)
    return quantity == ZERO or (
        quantity < context.market.sell_amount.minimum
        or quantity * maximum < context.market.sell_cost.minimum
    )


@arithmetic
def base_residual(
    state: State,
    context: Context,
    cycle: Cycle,
    facts: ValuationFacts,
    ms: int,
    ns: int,
    slot: ExitSlot | None = None,
) -> Residual:
    observed = market_facts(state, context, facts, cycle.policy, ms, ns)
    if state.reconcile_reason is not None or state.working:
        raise DomainError("orders or ownership remain unresolved")
    if not unavoidable_dust(state, observed.context, cycle, slot):
        raise DomainError("sellable residual cannot be declared dust")
    key = PoolKey(state.scope.venue, state.scope.account, context.market.base)
    value = Conversions(facts, cycle.policy, ms, ns).value(
        PoolKey(key.venue, key.account, context.market.quote),
        state.owned_base * observed.book.bids[0][0],
    )
    return Residual(key, state.owned_base, value)


@arithmetic
def check_dust(residuals: tuple[Residual, ...], policy: PortfolioPolicy) -> None:
    totals: dict[PoolKey, tuple[Decimal, Decimal]] = {}
    approvals = {d.key: d for d in policy.dust}
    for residual in residuals:
        amount, value = totals.get(residual.key, (ZERO, ZERO))
        totals[residual.key] = amount + residual.amount, value + residual.value
    for key, (amount, value) in totals.items():
        approval = approvals.get(key)
        if approval is None or amount > approval.max_amount or value > approval.max_value:
            raise DomainError("residual dust lacks explicit approval")
    if sum((r.value for r in residuals), ZERO) > policy.dust_limit:
        raise DomainError("dust ceiling blocks completion")


@arithmetic
def residuals(
    ledger: Ledger, rows: Rows, cycle: Cycle, facts: ValuationFacts, ms: int, ns: int
) -> tuple[Residual, ...]:
    result: list[Residual] = []
    for state, context, _, recovery in rows.values():
        if recovery or state.reconcile_reason is not None or state.working:
            raise DomainError("orders or ownership remain unresolved")
        market_facts(state, context, facts, cycle.policy, ms, ns)
        if any(
            m.amount > ZERO and m.currency != cycle.policy.reporting for m in state.fee_balances
        ) or (context.market.quote != cycle.policy.reporting and state.quote_cash > ZERO):
            raise DomainError("non-reporting fee/quote assets require a conversion sink")
        if state.owned_base == ZERO:
            continue
        result.append(base_residual(state, context, cycle, facts, ms, ns))
    for key in cycle.policy.pools:
        if (
            key.currency != cycle.policy.reporting
            and ledger.pool(key.venue, key.account, key.currency) > ZERO
        ):
            raise DomainError("non-reporting pool assets remain")
    check_dust(tuple(result), cycle.policy)
    if facts.liabilities:
        raise DomainError("dust ceiling or outstanding liability blocks completion")
    return tuple(result)


@arithmetic
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
        if cycle.phase in (CyclePhase.ACTIVE, CyclePhase.COMPLETE) or cycle.started_exit_ms is None:
            raise Conflict("no unresolved portfolio exit")
        if ms < cycle.observation_ms:
            raise DomainError("portfolio clock regressed")
        rows = load(ledger, cycle.policy)
        after = dict(rows)
        commands: dict[Scope, tuple[Command, ...]] = {}
        slots: list[ExitSlot] = []
        # Global barrier: no replacement until all ordinary orders/unknowns reconcile.
        ordinary = any(
            recovery
            or s.reconcile_reason is not None
            or any(o.role != "portfolio_exit" for o in s.working)
            for s, _, _, recovery in rows.values()
        )
        for slot in cycle.slots:
            state, context, version, recovery = rows[slot.scope]
            context = replace(context, now_ms=ms, allow_new_exposure=False)
            unsent = frozenset(
                o.id for o in state.working if not ledger.attempted(slot.scope, o.id)
            )
            if ordinary or cycle.pending_flows:
                keep = None
                if (
                    not recovery
                    and not cycle.pending_flows
                    and any(
                        e.slot.scope == slot.scope and not e.released
                        for e in cycle.allocation_exits
                    )
                ):
                    try:
                        fresh(facts, cycle.policy, ms, ns)
                        observed = market_facts(state, context, facts, cycle.policy, ms, ns)
                        context = replace(observed.context, now_ms=ms, allow_new_exposure=False)
                        Conversions(facts, cycle.policy, ms, ns).check_fee(state, context)
                        rule = next(r for r in cycle.policy.exits if r.scope == slot.scope)
                        keep = retain_exit(
                            state, context, rule, slot, ms, cycle.deadline_ms(slot.scope)
                        )
                    except (DomainError, ReadError):
                        pass
                state, commands[slot.scope] = plan_cancellations(state, context, unsent, keep=keep)
                slot = replace(slot, reason="global cancellation/reconciliation barrier")
            else:
                try:
                    fresh(facts, cycle.policy, ms, ns)
                    observed = market_facts(state, context, facts, cycle.policy, ms, ns)
                    context = replace(observed.context, now_ms=ms, allow_new_exposure=False)
                    Conversions(facts, cycle.policy, ms, ns).check_fee(state, context)
                    rule = next(r for r in cycle.policy.exits if r.scope == slot.scope)
                    state, commands[slot.scope], slot = plan_exit(
                        state,
                        context,
                        rule,
                        slot,
                        ms,
                        cycle.deadline_ms(slot.scope),
                        unsent,
                    )
                except (DomainError, ReadError) as exc:
                    # Stale data cannot prevent cancellation of an existing exit order.
                    state, commands[slot.scope] = plan_cancellations(state, context, unsent)
                    slot = replace(slot, reason=str(exc)[:128])
            after[slot.scope] = state, context, version, recovery
            slots.append(slot)
        updated = replace(
            cycle,
            revision=expected + 1,
            phase=CyclePhase.BLOCKED,
            slots=tuple(slots),
            last_evaluation_ms=ms,
            actor=actor,
            reason=reason,
        )
        if not ordinary and not cycle.pending_flows:
            try:
                fresh(facts, cycle.policy, ms, ns)
                # Sweep only when principal exits have no outstanding obligations.
                if all(
                    not s.working and (s.owned_base == ZERO or unavoidable_dust(s, c, updated))
                    for scope, (s, c, _, _) in after.items()
                    if scope not in cycle.policy.sinks
                ):
                    after = sweep(ledger, request_id, after, updated, facts, ms, ns)
                dust = residuals(ledger, after, updated, facts, ms, ns)
                final = nav(ledger, after, facts, cycle.policy, ms, ns)
                updated = replace(
                    updated,
                    phase=CyclePhase.COMPLETE,
                    final=final,
                    residuals=dust,
                    reason="exit reconciled complete",
                )
            except (DomainError, LedgerError, ReadError) as exc:
                updated = replace(updated, reason=str(exc)[:128])
                if any(s.working for s, _, _, _ in after.values()) and not ordinary:
                    updated = replace(updated, phase=CyclePhase.EXITING)
        save_rows(ledger, request_id, rows, after, commands)
        return persist(ledger, request_id, updated)

    return ledger._request(
        request_id,
        ("portfolio_advance", expected, codec.digest(facts), ms, actor, reason),
        "portfolio_advance",
        None,
        action,
    )


@arithmetic
def cashflow(
    ledger: Ledger, request_id: str, expected: int, flow: Cashflow, actor: str, reason: str
) -> Outcome:
    def action() -> Outcome:
        cycle = require(ledger, expected)
        if (
            cycle.phase is CyclePhase.COMPLETE
            or flow.key not in cycle.policy.pools
            or flow.observed_ms < cycle.baseline.now_ms
        ):
            raise LedgerError("cashflow outside active portfolio ownership")
        previous = ledger.db.execute(
            "SELECT payload FROM portfolio_flows WHERE cycle_id=? AND id=?", (cycle.id, flow.id)
        ).fetchone()
        if previous is not None:
            old = codec.loads(previous[0], Cashflow)
            if (
                old.disposition != "unclassified"
                or replace(flow, disposition="unclassified", evidence_id=old.evidence_id) != old
            ):
                raise Conflict("cashflow identity or classification conflict")
        elif flow.disposition != "unclassified":
            raise LedgerError("cashflow must first be recorded as unclassified")
        pending = set(cycle.pending_flows)
        adjustment = cycle.adjustment
        if flow.disposition == "unclassified":
            pending.add(flow.id)
        else:
            pending.discard(flow.id)
            if flow.disposition == "adjusted":
                if (
                    cycle.policy.cashflow_rule != "additive_baseline"
                    or flow.key.currency != cycle.policy.reporting
                ):
                    raise LedgerError("cashflow adjustment convention not authorized")
                adjustment += flow.amount
                if cycle.baseline.net + adjustment <= ZERO:
                    raise DomainError("cashflow would invalidate positive cycle baseline")
                ledger._pool_delta(
                    request_id,
                    flow.key.venue,
                    flow.key.account,
                    Money(flow.key.currency, flow.amount),
                )
        ledger.db.execute(
            "INSERT INTO portfolio_flows VALUES (?,?,?) ON CONFLICT(cycle_id,id) "
            "DO UPDATE SET payload=excluded.payload",
            (cycle.id, flow.id, ledger._text(flow)),
        )
        return persist(
            ledger,
            request_id,
            replace(
                cycle,
                revision=expected + 1,
                adjustment=adjustment,
                pending_flows=tuple(sorted(pending)),
                reason=reason,
                actor=actor,
            ),
        )

    return ledger._request(
        request_id,
        ("portfolio_cashflow", expected, flow, actor, reason),
        "portfolio_cashflow",
        None,
        action,
    )
