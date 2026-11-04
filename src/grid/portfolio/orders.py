"""Pure LIMIT exit proposals with explicit floors, fees, dust and one SELL slot."""

from dataclasses import replace

from grid.domain.market import Money
from grid.domain.model import Command, Context, Order, OrderReport, PlaceLimit, Side, State
from grid.domain.numbers import ONE, ZERO, DomainError, arithmetic
from grid.domain.reducer import _cancel, reduce, reserved
from grid.portfolio.model import ExitRule, ExitSlot
from grid.portfolio.valuation import sell_quantity


def plan_cancellations(
    state: State, context: Context, unsent: frozenset[int], *, keep: int | None = None
) -> tuple[State, tuple[Command, ...]]:
    commands: list[Command] = []
    for order in tuple(state.working):
        if order.id == keep:
            continue
        if order.id in unsent and order.filled == ZERO and not order.unknown:
            result = reduce(
                state, OrderReport(state.scope, order.id, "canceled", ZERO, True), context
            )
            if not result.accepted:
                raise DomainError("proven unsent revocation refused")
            state = result.state
            commands.extend(result.commands)
        else:
            state, emitted = _cancel(state, order)
            commands.extend(emitted)
    return state, tuple(commands)


def retain_exit(
    state: State, context: Context, rule: ExitRule, slot: ExitSlot, ms: int, deadline: int
) -> int | None:
    """A global handoff may retain one valid scoped SELL, never an uncertain order."""
    order = next((o for o in state.working if o.id == slot.order_id), None)
    if (
        state.reconcile_reason is not None
        or order is None
        or order.side is not Side.SELL
        or order.role != "portfolio_exit"
        or order.unknown
        or order.cancel_requested
        or slot.created_ms is None
        or not 0 <= ms - slot.created_ms < rule.ttl_ms
        or ms >= deadline
        or (order.config_revision, order.market_generation, order.fee_rule)
        != (context.config_revision, context.market.generation, context.sell_fee)
    ):
        return None
    return order.id


@arithmetic
def plan_exit(
    state: State,
    context: Context,
    rule: ExitRule,
    slot: ExitSlot,
    now_ms: int,
    deadline_ms: int,
    unsent: frozenset[int],
) -> tuple[State, tuple[Command, ...], ExitSlot]:
    if state.reconcile_reason is not None or any(o.unknown for o in state.working):
        return state, (), replace(slot, reason="reconciliation required")
    if any(o.side is Side.BUY or o.role != "portfolio_exit" for o in state.working):
        updated, commands = plan_cancellations(state, context, unsent)
        return updated, commands, replace(slot, reason="ordinary orders must reconcile")
    sell = next(iter(state.working), None)
    if sell is not None:
        expired = (
            slot.created_ms is None
            or now_ms - slot.created_ms >= rule.ttl_ms
            or now_ms >= deadline_ms
        )
        obsolete = (sell.config_revision, sell.market_generation, sell.fee_rule) != (
            context.config_revision,
            context.market.generation,
            context.sell_fee,
        )
        if expired or obsolete or sell.cancel_requested:
            updated, commands = plan_cancellations(state, context, unsent)
            return updated, commands, replace(slot, reason="exit cancellation pending")
        return state, (), replace(slot, reason="awaiting actual exit fills")
    if state.owned_base == ZERO:
        return state, (), replace(slot, reason="base reconciled to zero", order_id=None)
    if now_ms >= deadline_ms or slot.orders >= rule.max_orders:
        return state, (), replace(slot, reason="exit time or order budget exhausted")
    book = context.book
    if book is None:
        raise DomainError("exit book required")
    lower = max(rule.floor, slot.anchor_bid * (ONE - rule.max_deviation))
    price = context.market.price.round(
        max(lower, book.bid if rule.pricing == "bid" else book.ask), up=True
    )
    if price > slot.anchor_bid * (ONE + rule.max_deviation):
        return state, (), replace(slot, reason="exit price outside permitted drift")
    amount = sell_quantity(state.owned_base, context.market, context.sell_fee, price)
    if amount <= ZERO or not context.market.valid(price, amount, buy=False):
        return state, (), replace(slot, reason="unsellable residual")
    fee = context.sell_fee.estimate(context.market, price, amount)
    required = amount + (fee.amount if fee.currency == context.market.base else ZERO)
    net = amount * price - (
        context.sell_fee.quote_cost(context.market, fee, price)
        if fee.currency != context.market.base
        else ZERO
    )
    if net < rule.minimum_net_quote or required > state.owned_base:
        return state, (), replace(slot, reason="exit after-fee outcome below floor")
    reserves: tuple[Money, ...] = (Money(context.market.base, required),)
    if fee.currency not in (context.market.base, context.market.quote):
        available = next(
            (m.amount for m in state.fee_balances if m.currency == fee.currency), ZERO
        ) - reserved(state, fee.currency)
        if available < fee.amount:
            return state, (), replace(slot, reason="exit fee-token funds insufficient")
        reserves += (fee,)
    if len(state.orders) >= context.policy.max_order_records:
        return state, (), replace(slot, reason="exit order history bound")
    order = Order(
        state.next_id,
        state.round_id,
        Side.SELL,
        "portfolio_exit",
        price,
        amount,
        context.config_revision,
        context.market.generation,
        reserves,
        context.market.base,
        context.sell_fee,
    )
    updated = replace(
        state, orders=(*state.orders, order), next_id=state.next_id + 1, sell_dirty=False
    )
    return (
        updated,
        (PlaceLimit(state.scope, order),),
        replace(
            slot,
            orders=slot.orders + 1,
            order_id=order.id,
            created_ms=now_ms,
            reason="exit intent reserved",
        ),
    )
