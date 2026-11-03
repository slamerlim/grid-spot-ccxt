"""Deterministic allocation reducer. Returned commands are proposals, never effects.

Every accepted fill is applied before planning. A terminal order report needs
matching locally posted trades and an explicit reconciliation fact. I2/I4 must
durably verify those facts and reserve the returned intents before sending.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Literal

from grid.domain.market import Money, aggregate_take_profit, grid_prices, minimum_buy
from grid.domain.model import (
    Cancel,
    CancelAcknowledged,
    Command,
    Context,
    Control,
    Event,
    Fill,
    Lot,
    Mode,
    Order,
    OrderReport,
    Phase,
    PlaceLimit,
    Reconcile,
    Reconciled,
    Side,
    Start,
    State,
    SubmissionUnknown,
    Transition,
)
from grid.domain.numbers import ZERO, DomainError, arithmetic, exact, proportional_cost


def _order(state: State, order_id: int) -> Order:
    for order in state.orders:
        if order.id == order_id:
            return order
    raise DomainError("unknown allocation order")


def _put(state: State, order: Order) -> State:
    return replace(
        state, orders=tuple(order if old.id == order.id else old for old in state.orders)
    )


def _block(state: State, reason: str, *, accepted: bool = False) -> Transition:
    commands: tuple[Command, ...] = ()
    if state.reconcile_reason is None:
        commands = (Reconcile(state.scope, reason),)
    revision = state.reconcile_revision + int(state.reconcile_reason != reason)
    return Transition(
        replace(state, reconcile_reason=reason, reconcile_revision=revision),
        commands,
        (reason,),
        accepted,
    )


@exact
def reserved(state: State, currency: str) -> Decimal:
    """Outstanding principal plus unused fee allowance; terminal orders release it."""
    total = ZERO
    for order in state.working:
        for reserve in order.reserves:
            if reserve.currency == currency:
                paid = sum(
                    (
                        fee.amount
                        for fill in state.fills
                        if fill.order_id == order.id
                        for fee in (fill.fees or ())
                        if fee.currency == currency
                    ),
                    ZERO,
                )
                if currency != order.principal_currency:
                    total += max(ZERO, reserve.amount - max(ZERO, paid))
                elif order.side is Side.BUY:
                    principal = order.amount * order.price
                    unused_fee = max(ZERO, reserve.amount - principal - max(ZERO, paid))
                    total += order.remaining * order.price + unused_fee
                else:
                    unused_fee = max(ZERO, reserve.amount - order.amount - max(ZERO, paid))
                    total += order.remaining + unused_fee
    return total


@exact
def available_quote(state: State, context: Context) -> Decimal:
    return max(
        ZERO,
        state.quote_cash - reserved(state, context.market.quote) - context.policy.quote_reserve,
    )


def _token_balance(state: State, currency: str) -> Decimal:
    return next((money.amount for money in state.fee_balances if money.currency == currency), ZERO)


def _funding_shortfall(state: State, context: Context) -> bool:
    currencies = {money.currency for order in state.working for money in order.reserves}
    for currency in currencies:
        funds = (
            state.quote_cash
            if currency == context.market.quote
            else state.owned_base
            if currency == context.market.base
            else _token_balance(state, currency)
        )
        if reserved(state, currency) > funds:
            return True
    return False


def _active_sell(state: State) -> Order | None:
    orders = [order for order in state.working if order.side is Side.SELL]
    if len(orders) > 1:
        raise DomainError("multiple possibly-active SELLs")
    return orders[0] if orders else None


def _cancel(state: State, order: Order) -> tuple[State, tuple[Command, ...]]:
    if order.terminal or order.cancel_requested:
        return state, ()
    if order.unknown or not order.acknowledged:
        blocked = _block(state, "cancel_requires_order_reconciliation", accepted=True)
        return blocked.state, blocked.commands
    return _put(state, replace(order, cancel_requested=True)), (Cancel(state.scope, order.id),)


@exact
def _consume_lots(lots: tuple[Lot, ...], amount: Decimal) -> tuple[Lot, ...]:
    remaining = amount
    result: list[Lot] = []
    for lot in lots:
        take = min(lot.quantity, remaining)
        remaining -= take
        if lot.quantity > take:
            result.append(replace(lot, quantity=lot.quantity - take))
    if remaining != ZERO:
        raise DomainError("SELL exceeds bot-owned base")
    return tuple(result)


@arithmetic
def _weighted_price(first: Lot, quantity: Decimal, price: Decimal) -> Decimal:
    return (first.quantity * first.execution_price + quantity * price) / (first.quantity + quantity)


@exact
def _apply_fill(state: State, fill: Fill, context: Context) -> State:
    order = _order(state, fill.order_id)
    if fill.fees is None:
        raise DomainError("fill fee evidence missing")
    if len(state.fills) >= context.policy.max_fill_records:
        raise DomainError("fill history capacity requires durable checkpoint")
    if fill.amount > order.remaining:
        raise DomainError("fill exceeds order amount")
    if order.terminal_amount is not None and order.filled + fill.amount > order.terminal_amount:
        raise DomainError("fill exceeds reported terminal amount")
    if order.terminal:
        raise DomainError("new fill contradicts proven terminal order")
    if (order.side is Side.BUY and fill.price > order.price) or (
        order.side is Side.SELL and fill.price < order.price
    ):
        raise DomainError("fill violates LIMIT price")
    market = context.market
    base_fee = quote_fee = token_cost = ZERO
    balances = {money.currency: money.amount for money in state.fee_balances}
    for fee in fill.fees:
        if fee.currency == market.base:
            base_fee += fee.amount
        elif fee.currency == market.quote:
            quote_fee += fee.amount
        else:
            if fee.quote_value is None:
                raise DomainError("actual third-token fee conversion missing")
            token_cost += fee.quote_value
            balances[fee.currency] = balances.get(fee.currency, ZERO) - fee.amount
            if balances[fee.currency] < ZERO:
                raise DomainError("fee exceeds allocated token funds")
    gross = fill.amount * fill.price
    if order.side is Side.BUY:
        quantity = fill.amount - base_fee
        if quantity <= ZERO:
            raise DomainError("nonpositive net BUY quantity")
        cash = state.quote_cash - gross - quote_fee
        cost = state.position_cost + gross + quote_fee + token_cost
        lots = list(state.lots)
        for index, lot in enumerate(lots):
            if lot.order_id == order.id:
                combined = lot.quantity + quantity
                lots[index] = Lot(
                    order.id,
                    combined,
                    _weighted_price(lot, quantity, fill.price),
                )
                break
        else:
            lots.append(Lot(order.id, quantity, fill.price))
        realized = state.realized_pnl
    else:
        quantity = fill.amount + base_fee
        if quantity <= ZERO or quantity > state.owned_base:
            raise DomainError("SELL plus fee exceeds bot-owned base")
        basis = (
            state.position_cost
            if quantity == state.owned_base
            else proportional_cost(
                state.position_cost, quantity, state.owned_base, context.policy.cost_quantum
            )
        )
        cash = state.quote_cash + gross - quote_fee
        cost = state.position_cost - basis
        lots = list(_consume_lots(state.lots, quantity))
        realized = state.realized_pnl + gross - quote_fee - token_cost - basis
    if cash < ZERO or cost < ZERO:
        raise DomainError("negative allocated funds or cost basis")
    updated = replace(
        order,
        filled=order.filled + fill.amount,
        gross_cost=order.gross_cost + gross,
        acknowledged=True,
        unknown=False,
    )
    result = _put(
        replace(
            state,
            quote_cash=cash,
            fee_balances=tuple(
                Money(currency, value) for currency, value in sorted(balances.items())
            ),
            position_cost=cost,
            realized_pnl=realized,
            lots=tuple(lots),
            fills=(*state.fills, fill),
            sell_dirty=state.sell_dirty or order.side is Side.BUY,
            ladder_credits=state.ladder_credits
            + int(order.role == "ladder" and order.filled == ZERO),
        ),
        updated,
    )
    if order.side is Side.SELL and updated.filled == updated.amount:
        result = replace(result, phase=Phase.DRAINING, sell_dirty=False)
    return result


@arithmetic
def _report(state: State, report: OrderReport) -> State:
    order = _order(state, report.order_id)
    if report.cumulative > order.amount:
        raise DomainError("order report exceeds requested amount")
    if report.status == "open":
        if order.terminal:
            return state  # A stale OPEN report cannot resurrect a closed order.
        if report.cumulative > order.filled:
            raise DomainError("order report has unposted fills")
        return _put(state, replace(order, acknowledged=True, unknown=False))
    if report.cumulative < order.filled:
        raise DomainError("terminal report contradicts posted fills")
    if report.status == "filled" and report.cumulative != order.amount:
        raise DomainError("FILLED report has incomplete amount")
    if report.status == "rejected" and report.cumulative != ZERO:
        raise DomainError("REJECTED report contains executions")
    if order.terminal:
        if report.cumulative != order.filled:
            raise DomainError("conflicting terminal report")
        return state
    return _put(
        state,
        replace(
            order,
            acknowledged=True,
            unknown=False,
            terminal_amount=report.cumulative,
            terminal_proven=report.trades_reconciled,
        ),
    )


@exact
def _new_order(
    state: State,
    context: Context,
    side: Side,
    role: Literal["seed", "ladder", "take_profit"],
    price: Decimal,
    amount: Decimal,
    reserves: tuple[Money, ...],
    level: int | None = None,
    acquisition_fee: Decimal = ZERO,
) -> tuple[State, PlaceLimit]:
    if len(state.working) >= context.policy.max_open_orders:
        raise DomainError("open-order cap reached")
    if len(state.orders) >= context.policy.max_order_records:
        raise DomainError("order history capacity requires durable checkpoint")
    if side is Side.SELL and _active_sell(state) is not None:
        raise DomainError("SELL slot already occupied")
    if role not in ("seed", "ladder", "take_profit"):
        raise DomainError("invalid order role")
    for reserve in reserves:
        if reserve.currency not in (context.market.base, context.market.quote) and (
            reserve.amount
            > _token_balance(state, reserve.currency) - reserved(state, reserve.currency)
        ):
            raise DomainError("insufficient reserved fee-token funds")
    order = Order(
        id=state.next_id,
        round_id=state.round_id,
        side=side,
        role=role,
        price=price,
        amount=amount,
        reserves=reserves,
        level=level,
        principal_currency=context.market.quote if side is Side.BUY else context.market.base,
        fee_rule=context.buy_fee if side is Side.BUY else context.sell_fee,
        acquisition_fee=acquisition_fee,
        config_revision=context.config_revision,
        market_generation=context.market.generation,
    )
    result = replace(state, orders=(*state.orders, order), next_id=state.next_id + 1)
    return result, PlaceLimit(state.scope, order)


@exact
def _buy(
    state: State, context: Context, price: Decimal, level: int | None
) -> tuple[State, PlaceLimit]:
    policy, market = context.policy, context.market
    if not policy.lower_price <= price <= policy.upper_price:
        raise DomainError("BUY outside configured range")
    held = {lot.order_id for lot in state.lots}
    committed_lots = held | {order.id for order in state.working if order.side is Side.BUY}
    if len(committed_lots) >= policy.max_lots:
        raise DomainError("maximum accumulated lots reached")
    plan = minimum_buy(
        price,
        market,
        context.buy_fee,
        context.sell_fee,
        policy.spacing,
        policy.net_target,
        available_quote(state, context),
    )
    pending_cost = sum(
        (
            order.price * order.remaining + order.acquisition_fee
            for order in state.working
            if order.side is Side.BUY
        ),
        ZERO,
    )
    if state.position_cost + pending_cost + plan.acquisition_cost > policy.max_quote_committed:
        raise DomainError("committed acquisition cost cap reached")
    future_fee = plan.prospective_sell.fee
    if future_fee.currency not in (market.base, market.quote):
        needed = future_fee.amount + (
            plan.fee.amount if plan.fee.currency == future_fee.currency else ZERO
        )
        if needed > _token_balance(state, future_fee.currency) - reserved(
            state, future_fee.currency
        ):
            raise DomainError("insufficient fee-token funds for prospective SELL")
    reserves: tuple[Money, ...] = (Money(market.quote, plan.quote_required),)
    if plan.fee.currency not in (market.base, market.quote):
        reserves += (plan.fee,)
    result, command = _new_order(
        state,
        context,
        Side.BUY,
        "seed" if level is None else "ladder",
        price,
        plan.amount,
        reserves,
        level,
        plan.acquisition_cost - price * plan.amount,
    )
    if level is None:
        result = replace(result, seed_id=command.order.id, phase=Phase.SEED_PENDING)
    else:
        result = replace(result, next_level=level + 1, ladder_credits=state.ladder_credits - 1)
    return result, command


@arithmetic
def _plan(state: State, context: Context) -> Transition:
    commands: list[Command] = []
    notices: list[str] = []
    policy, market = context.policy, context.market

    if state.phase is Phase.DRAINING or state.mode in (
        Mode.RETIRING,
        Mode.PORTFOLIO_EXIT,
        Mode.EXIT_BLOCKED,
    ):
        for buy in tuple(order for order in state.working if order.side is Side.BUY):
            state, emitted = _cancel(state, buy)
            commands.extend(emitted)
        if state.phase is Phase.DRAINING:
            if any(order.side is Side.BUY for order in state.working):
                return Transition(state, tuple(commands), ("draining_unresolved_BUYs",))
            if state.reconcile_reason is not None:
                return Transition(state, tuple(commands), (state.reconcile_reason,))
            # Existing late-fill inventory is the next seed; retain its exact
            # actual execution prices and never buy a duplicate seed.
            anchor = (
                sum((lot.quantity * lot.execution_price for lot in state.lots), ZERO)
                / state.owned_base
                if state.lots
                else None
            )
            state = replace(
                state,
                round_id=state.round_id + 1,
                phase=Phase.ACCUMULATING if state.lots else Phase.INACTIVE,
                seed_id=None,
                anchor=anchor,
                next_level=1,
                ladder_credits=policy.ladder_size if state.lots else 0,
                sell_dirty=bool(state.lots),
                replacements=0,
            )

    if state.reconcile_reason is not None:
        return Transition(state, tuple(commands), (state.reconcile_reason,))
    if (state.config_revision, state.market_generation) != (
        context.config_revision,
        market.generation,
    ):
        blocked = _block(state, "stale_config_or_market_generation", accepted=True)
        return replace(blocked, commands=(*commands, *blocked.commands))
    if not context.fresh(context.rules_observed_ms) or not context.fresh(context.fees_observed_ms):
        return Transition(state, tuple(commands), ("stale_market_or_fee_facts",))
    if (
        context.book is None
        or not context.fresh(context.book.observed_ms)
        or context.book.market_generation != market.generation
    ):
        return Transition(state, tuple(commands), ("fresh eligible book required",))
    if state.mode in (Mode.PORTFOLIO_EXIT, Mode.EXIT_BLOCKED):
        return Transition(state, tuple(commands), ("portfolio_supervision_required",))

    if state.seed_id is not None:
        seed = _order(state, state.seed_id)
        if seed.terminal and seed.filled > ZERO and state.anchor is None:
            state = replace(
                state,
                anchor=seed.gross_cost / seed.filled,
                phase=Phase.ACCUMULATING,
                ladder_credits=policy.ladder_size,
            )

    sell = _active_sell(state)
    if state.lots and (sell is None or state.sell_dirty):
        try:
            target = aggregate_take_profit(
                state.owned_base,
                state.position_cost,
                market,
                policy.spacing,
                policy.net_target,
                context.sell_fee,
            )
            if sell is not None:
                if target.amount != sell.remaining or target.price != sell.price:
                    if not sell.cancel_requested and state.replacements >= policy.max_replacements:
                        blocked = _block(state, "SELL replacement bound reached", accepted=True)
                        return replace(blocked, commands=(*commands, *blocked.commands))
                    state, emitted = _cancel(state, sell)
                    commands.extend(emitted)
                    if emitted and isinstance(emitted[0], Cancel):
                        state = replace(state, replacements=state.replacements + 1)
                    state = replace(state, phase=Phase.SELL_REPLACING)
                else:
                    state = replace(state, sell_dirty=False)
            else:
                reserves: tuple[Money, ...] = (Money(market.base, target.reserved_base),)
                if target.fee.currency not in (market.base, market.quote):
                    reserves += (target.fee,)
                state, command = _new_order(
                    state, context, Side.SELL, "take_profit", target.price, target.amount, reserves
                )
                commands.append(command)
                state = replace(
                    state,
                    sell_dirty=False,
                    phase=Phase.ACCUMULATING if state.anchor is not None else Phase.SEED_PENDING,
                )
        except DomainError as exc:
            # Residual ownership stays supervised; do not accumulate into an
            # inventory for which an aggregate exit cannot currently be planned.
            return Transition(state, tuple(commands), (*notices, str(exc)))

    if state.reconcile_reason is not None or state.mode is not Mode.ACTIVE:
        return Transition(state, tuple(commands), tuple(notices))
    if not context.allow_new_exposure:
        return Transition(state, tuple(commands), (*notices, "risk blocks new exposure"))
    if state.phase is Phase.SELL_REPLACING:
        return Transition(state, tuple(commands), tuple(notices))
    if state.seed_id is not None and not _order(state, state.seed_id).terminal:
        return Transition(state, tuple(commands), tuple(notices))

    if not state.lots:
        if state.seed_id is not None:
            # A rejected/cancelled unfilled seed stays waiting; no automatic
            # cancel/reseed policy is implied by a status update.
            return Transition(
                state, tuple(commands), (*notices, "seed requires explicit new round")
            )
        book = context.book
        if (
            book is None
            or not context.fresh(book.observed_ms)
            or book.market_generation != market.generation
            or (book.ask - book.bid) / book.bid > policy.max_spread
        ):
            return Transition(state, tuple(commands), (*notices, "fresh eligible book required"))
        try:
            price = market.price.round(book.bid if policy.seed_at == "bid" else book.ask, up=False)
            state, command = _buy(state, context, price, None)
            commands.append(command)
        except DomainError as exc:
            notices.append(str(exc))
    elif state.anchor is not None:
        working_buys = sum(order.side is Side.BUY for order in state.working)
        for _ in range(min(state.ladder_credits, max(0, policy.ladder_size - working_buys))):
            try:
                price = grid_prices(state.anchor, policy.spacing, (state.next_level,), market)[0]
                previous = [
                    order.price
                    for order in state.orders
                    if order.round_id == state.round_id and order.level is not None
                ]
                if previous and price >= min(previous):
                    raise DomainError("grid levels collapse after quantization")
                state, command = _buy(state, context, price, state.next_level)
                commands.append(command)
            except DomainError as exc:
                notices.append(str(exc))
                break
    return Transition(state, tuple(commands), tuple(notices))


def _fee_breach(state: State, context: Context, reason: str) -> Transition:
    blocked = _block(state, reason, accepted=True)
    state = blocked.state
    commands = list(blocked.commands)
    if _funding_shortfall(state, context):
        # Confirmed facts may reveal a shortage. Preserve the obligation in
        # full and cancel known working orders; an ack cannot free reserves.
        for order in state.working:
            if order.acknowledged and not order.unknown:
                state, emitted = _cancel(state, order)
                commands.extend(emitted)
    # Even a fee breach cannot skip DRAINING/retirement cancellation duties.
    planned = _plan(state, context)
    return replace(planned, commands=(*commands, *planned.commands))


@arithmetic
def reduce(state: State, event: Event, context: Context) -> Transition:
    """Apply one normalized fact and return deterministic proposed commands."""
    if event.scope != state.scope or context.market.symbol != state.scope.symbol:
        return Transition(state, notices=("scope mismatch",), accepted=False)
    if any(
        money.currency in (context.market.base, context.market.quote)
        for money in state.fee_balances
    ):
        return _block(state, "fee-token funds overlap principal funds")
    applied = False
    try:
        _active_sell(state)
        if isinstance(event, Fill):
            previous = next((fill for fill in state.fills if fill.trade_id == event.trade_id), None)
            if previous is not None:
                if previous == event:
                    return Transition(state)
                return _block(state, "conflicting duplicate fill")
            state = _apply_fill(state, event, context)
            applied = True
            order = _order(state, event.order_id)
            posted = tuple(fill for fill in state.fills if fill.order_id == order.id)
            if len(posted) > order.fee_rule.max_settlements:
                return _fee_breach(state, context, "fee settlement bound exceeded")
            expected_fee = order.fee_rule.estimate(context.market, order.price, order.amount)
            totals: dict[str, Decimal] = {}
            for fill in posted:
                for fee in fill.fees or ():
                    totals[fee.currency] = totals.get(fee.currency, ZERO) + fee.amount
            if any(
                amount > (expected_fee.amount if currency == expected_fee.currency else ZERO)
                for currency, amount in totals.items()
            ):
                return _fee_breach(state, context, "actual fee exceeds forecast")
        elif isinstance(event, OrderReport):
            before = state
            state = _report(state, event)
            if state == before:
                return Transition(state)
            applied = True
            reported = _order(state, event.order_id)
            if reported.terminal_amount is not None and not reported.terminal:
                return _block(state, "terminal report requires reconciled trades", accepted=True)
        elif isinstance(event, SubmissionUnknown):
            order = _order(state, event.order_id)
            if order.terminal:
                return Transition(state)
            state = _put(state, replace(order, unknown=True))
            return _block(state, "submission outcome unknown", accepted=True)
        elif isinstance(event, CancelAcknowledged):
            _order(state, event.order_id)
            return Transition(state, notices=("cancel acknowledgement is not terminal proof",))
        elif isinstance(event, Control):
            if event.revision < state.control_revision:
                return Transition(state, notices=("stale control revision",))
            if event.revision == state.control_revision:
                return (
                    Transition(state)
                    if event.mode is state.mode
                    else _block(state, "conflicting control revision")
                )
            state = replace(state, mode=event.mode, control_revision=event.revision)
            applied = True
        elif isinstance(event, Reconciled):
            if event.revision != state.reconcile_revision:
                return Transition(state, notices=("stale reconciliation revision",), accepted=False)
            if event.expected_reason != state.reconcile_reason:
                return Transition(
                    state, notices=("stale reconciliation assertion",), accepted=False
                )
            if (event.config_revision, event.market_generation) != (
                context.config_revision,
                context.market.generation,
            ) or any(
                order.unknown or (order.terminal_amount is not None and not order.terminal)
                for order in state.working
            ):
                return _block(state, "reconciliation incomplete")
            if _funding_shortfall(state, context):
                return _block(state, "outstanding reservation exceeds allocated funds")
            state = replace(
                state,
                reconcile_reason=None,
                config_revision=event.config_revision,
                market_generation=event.market_generation,
                sell_dirty=state.sell_dirty
                or (
                    bool(state.lots)
                    and (
                        state.config_revision != event.config_revision
                        or state.market_generation != event.market_generation
                    )
                ),
            )
            applied = True
        elif isinstance(event, Start):
            if state.phase is not Phase.INACTIVE or state.orders:
                return Transition(state)
        else:
            return _block(state, "unsupported normalized event")
        return _plan(state, context)
    except DomainError as exc:
        return _block(state, str(exc), accepted=applied)
