from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext
from typing import Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from grid.domain.market import FeeRule, Limits, Money
from grid.domain.model import (
    ActualFee,
    Cancel,
    CancelAcknowledged,
    Context,
    Control,
    Event,
    Fill,
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
from grid.domain.reducer import available_quote, reduce, reserved


def reconciled(state: State) -> Reconciled:
    return Reconciled(state.scope, 1, 1, state.reconcile_reason, state.reconcile_revision)


def placed(result: Transition, side: Side | None = None) -> tuple[Order, ...]:
    return tuple(
        command.order
        for command in result.commands
        if isinstance(command, PlaceLimit) and (side is None or command.order.side is side)
    )


def report(
    state: State,
    order: Order,
    status: Literal["open", "canceled", "rejected", "filled"] = "open",
    cumulative: D = D(0),
    reconciled: bool = False,
) -> OrderReport:
    return OrderReport(state.scope, order.id, status, cumulative, reconciled)


def execution(
    state: State,
    order: Order,
    trade: str,
    amount: D | None = None,
    price: D | None = None,
    fees: tuple[ActualFee, ...] | None = (),
) -> Fill:
    return Fill(state.scope, trade, order.id, amount or order.remaining, price or order.price, fees)


def seeded(state: State, context: Context) -> State:
    started = reduce(state, Start(state.scope), context)
    seed = placed(started)[0]
    return reduce(started.state, execution(started.state, seed, "seed"), context).state


def acknowledge(state: State, context: Context) -> State:
    for order in state.working:
        state = reduce(state, report(state, order, cumulative=order.filled), context).state
    return state


def test_seed_acknowledgement_is_not_inventory(state: State, context: Context) -> None:
    start = reduce(state, Start(state.scope), context)
    assert len(placed(start)) == 1
    seed = placed(start)[0]
    assert seed.role == "seed" and seed.price == 100 and seed.amount == 1
    result = reduce(start.state, report(state, seed), context)
    assert result.state.phase is Phase.SEED_PENDING
    assert result.state.owned_base == result.state.position_cost == 0
    assert result.state.anchor is None and not placed(result)
    assert reduce(result.state, Start(state.scope), context).state == result.state


def test_partial_seed_rule_and_weighted_execution_anchor(state: State, context: Context) -> None:
    start = reduce(state, Start(state.scope), context)
    seed = placed(start)[0]
    small = reduce(start.state, execution(state, seed, "small", D("0.05"), D("98")), context)
    assert small.state.owned_base == D("0.05") and not placed(small)
    partial = reduce(small.state, execution(state, seed, "partial", D("0.35"), D("99")), context)
    assert len(placed(partial, Side.SELL)) == 1 and not placed(partial, Side.BUY)
    assert partial.state.anchor is None
    acknowledged = acknowledge(partial.state, context)
    full = reduce(acknowledged, execution(state, seed, "rest", D("0.6"), D("100")), context)
    assert full.state.anchor == D("99.55")
    assert full.state.phase is Phase.SELL_REPLACING
    assert not placed(full, Side.SELL) and not placed(full, Side.BUY)
    old = next(order for order in full.state.working if order.side is Side.SELL)
    terminal = reduce(full.state, report(state, old, "canceled", D(0), True), context)
    assert len(placed(terminal, Side.SELL)) == 1
    assert len(placed(terminal, Side.BUY)) == 3
    assert placed(terminal, Side.SELL)[0].amount == 1


def test_seed_cancel_with_partial_inventory_fixes_anchor(state: State, context: Context) -> None:
    start = reduce(state, Start(state.scope), context)
    seed = placed(start)[0]
    partial = reduce(start.state, execution(state, seed, "partial", D("0.4"), D("99")), context)
    terminal = reduce(partial.state, report(state, seed, "canceled", D("0.4"), True), context)
    assert terminal.state.anchor == 99
    assert len(placed(terminal, Side.BUY)) == 3


def test_cancel_ack_never_frees_sell_slot_and_fill_is_accounted_first(
    state: State, context: Context
) -> None:
    active = acknowledge(seeded(state, context), context)
    sell = next(order for order in active.working if order.side is Side.SELL)
    buy = next(order for order in active.working if order.side is Side.BUY)
    bought = reduce(active, execution(state, buy, "lower"), context)
    assert any(
        isinstance(command, Cancel) and command.order_id == sell.id for command in bought.commands
    )
    assert not placed(bought, Side.SELL)
    for _ in range(2):
        ack = reduce(bought.state, CancelAcknowledged(state.scope, sell.id), context)
        assert ack.state == bought.state and not placed(ack)
    sold = reduce(bought.state, execution(state, sell, "partial-sell", D("0.4")), context)
    assert sold.state.owned_base == D("1.6")
    assert sold.state.position_cost == D("152.72")
    assert sold.state.realized_pnl == D("5.82")
    assert not placed(sold, Side.SELL)
    terminal = report(state, sell, "canceled", D("0.4"), True)
    successor = reduce(sold.state, terminal, context)
    assert len(placed(successor, Side.SELL)) == 1
    assert placed(successor, Side.SELL)[0].amount == D("1.6")
    assert len([order for order in successor.state.working if order.side is Side.SELL]) == 1
    duplicate = reduce(successor.state, terminal, context)
    assert duplicate.state == successor.state and not duplicate.commands


@pytest.mark.parametrize("late", [False, True])
def test_draining_reconciles_late_buy_before_next_round(
    state: State, context: Context, late: bool
) -> None:
    active = acknowledge(seeded(state, context), context)
    sell = next(order for order in active.working if order.side is Side.SELL)
    buys = tuple(order for order in active.working if order.side is Side.BUY)
    sold = reduce(active, execution(state, sell, "sold"), context)
    assert sold.state.phase is Phase.DRAINING and not placed(sold)
    assert len([command for command in sold.commands if isinstance(command, Cancel)]) == 3
    current = sold.state
    if late:
        late_result = reduce(current, execution(state, buys[0], "late", D("0.4")), context)
        assert not placed(late_result)
        current = late_result.state
    for index, buy in enumerate(buys):
        result = reduce(
            current,
            report(state, buy, "canceled", D("0.4") if late and index == 0 else D(0), True),
            context,
        )
        current = result.state
    assert current.round_id == 2
    if late:
        assert current.anchor == buys[0].price
        assert all(order.role != "seed" for order in placed(result))
        assert placed(result, Side.SELL)[0].amount == D("0.4")
    else:
        assert current.anchor is None
        assert len(placed(result)) == 1 and placed(result)[0].role == "seed"


def test_full_sell_drains_still_partial_seed(state: State, context: Context) -> None:
    start = reduce(state, Start(state.scope), context)
    seed = placed(start)[0]
    partial = reduce(start.state, execution(state, seed, "partial", D("0.4")), context)
    sell = placed(partial, Side.SELL)[0]
    sold = reduce(partial.state, execution(state, sell, "sold"), context)
    assert sold.state.phase is Phase.DRAINING
    assert any(
        isinstance(command, Cancel) and command.order_id == seed.id for command in sold.commands
    )
    late = reduce(sold.state, execution(state, seed, "late", D("0.6"), D("99")), context)
    assert late.state.round_id == 2 and late.state.anchor == 99
    assert not any(order.role == "seed" for order in placed(late))


def test_terminal_report_before_trades_requires_explicit_reconciliation(
    state: State, context: Context
) -> None:
    start = reduce(state, Start(state.scope), context)
    seed = placed(start)[0]
    pending = reduce(start.state, report(state, seed, "filled", D(1), True), context)
    assert pending.state.owned_base == 0 and not placed(pending)
    assert pending.state.reconcile_reason is not None
    premature = reduce(pending.state, reconciled(pending.state), context)
    assert not placed(premature) and premature.state.reconcile_reason is not None
    filled = reduce(pending.state, execution(state, seed, "seed"), context)
    assert filled.state.owned_base == 1 and not placed(filled)
    resumed = reduce(filled.state, reconciled(filled.state), context)
    assert len(placed(resumed, Side.SELL)) == 1 and len(placed(resumed, Side.BUY)) == 3


def test_unknown_submission_has_no_blind_retry(state: State, context: Context) -> None:
    start = reduce(state, Start(state.scope), context)
    seed = placed(start)[0]
    unknown = reduce(start.state, SubmissionUnknown(state.scope, seed.id), context)
    assert len(unknown.commands) == 1 and isinstance(unknown.commands[0], Reconcile)
    for event in (
        Start(state.scope),
        SubmissionUnknown(state.scope, seed.id),
        reconciled(unknown.state),
    ):
        result = reduce(unknown.state, event, context)
        assert not placed(result) and result.state.reconcile_reason is not None
    found = reduce(unknown.state, report(state, seed), context)
    result = reduce(found.state, reconciled(found.state), context)
    assert result.state.reconcile_reason is None and not placed(result)
    assert len(result.state.orders) == 1


def test_exact_duplicate_is_noop_but_conflicting_trade_blocks(
    state: State, context: Context
) -> None:
    start = reduce(state, Start(state.scope), context)
    fill = execution(state, placed(start)[0], "seed")
    posted = reduce(start.state, fill, context)
    duplicate = reduce(posted.state, fill, context)
    assert duplicate.state == posted.state and not duplicate.commands
    conflict = reduce(posted.state, replace(fill, price=D("99")), context)
    assert conflict.state.owned_base == posted.state.owned_base
    assert not conflict.accepted and conflict.state.reconcile_reason is not None


@pytest.mark.parametrize(
    "fee",
    [
        ActualFee("BASE", D("0.001"), None),
        ActualFee("QUOTE", D("0.1"), None),
        ActualFee("TOKEN", D("0.05"), D("0.1")),
        ActualFee("QUOTE", D("-0.1"), None),
        ActualFee("BASE", D("-0.001"), None),
        ActualFee("TOKEN", D("-0.05"), D("-0.1")),
    ],
)
def test_actual_fees_and_rebates_preserve_allocated_value(
    state: State, context: Context, fee: ActualFee
) -> None:
    start = reduce(state, Start(state.scope), context)
    result = reduce(start.state, execution(state, placed(start)[0], "seed", fees=(fee,)), context)
    base_fee = fee.amount if fee.currency == "BASE" else D(0)
    quote_fee = fee.amount if fee.currency == "QUOTE" else D(0)
    token_cost = fee.quote_value or D(0) if fee.currency == "TOKEN" else D(0)
    assert result.state.owned_base == D(1) - base_fee
    assert result.state.quote_cash == D(900) - quote_fee
    assert result.state.position_cost == D(100) + quote_fee + token_cost
    assert result.state.quote_cash + result.state.position_cost - token_cost == state.quote_cash
    if fee.currency == "TOKEN":
        assert result.state.fee_balances == (Money("TOKEN", D(10) - fee.amount),)


@pytest.mark.parametrize("fees", [None, (ActualFee("TOKEN", D("0.1"), None),)])
def test_incomplete_fee_evidence_blocks_posting(
    state: State, context: Context, fees: tuple[ActualFee, ...] | None
) -> None:
    start = reduce(state, Start(state.scope), context)
    result = reduce(start.state, execution(state, placed(start)[0], "missing", fees=fees), context)
    assert not result.accepted and not placed(result)
    assert result.state.owned_base == 0 and result.state.quote_cash == state.quote_cash


def test_partial_fill_retains_third_token_reserve(state: State, context: Context) -> None:
    context = replace(context, buy_fee=FeeRule("TOKEN", D("0.01"), D("0.01"), D("2"), 1))
    start = reduce(state, Start(state.scope), context)
    assert reserved(start.state, "TOKEN") == D("0.5")
    partial = reduce(
        start.state,
        execution(
            state,
            placed(start)[0],
            "partial",
            D("0.4"),
            fees=(ActualFee("TOKEN", D("0.2"), D("0.4")),),
        ),
        context,
    )
    assert reserved(partial.state, "TOKEN") == D("0.3")
    assert reserved(partial.state, "QUOTE") == D("60")
    assert available_quote(partial.state, context) == D("890")


@pytest.mark.parametrize("mode", [Mode.PAUSED, Mode.RETIRING, Mode.PORTFOLIO_EXIT])
def test_controls_retain_inventory_and_stop_new_exposure(
    state: State, context: Context, mode: Mode
) -> None:
    active = acknowledge(seeded(state, context), context)
    result = reduce(active, Control(state.scope, 1, mode), context)
    assert result.state.owned_base == 1 and result.state.mode is mode
    assert not placed(result, Side.BUY)
    assert len(result.state.working) == 4
    assert sum(isinstance(command, Cancel) for command in result.commands) == (
        0 if mode is Mode.PAUSED else 3
    )
    stale = reduce(result.state, Control(state.scope, 1, mode), context)
    assert stale.state == result.state and not stale.commands


@pytest.mark.parametrize("field", ["rules", "fees", "book", "risk", "config", "market"])
def test_stale_or_unapproved_inputs_cannot_admit_exposure(
    state: State, context: Context, field: str
) -> None:
    match field:
        case "rules":
            context = replace(context, rules_observed_ms=1)
        case "fees":
            context = replace(context, fees_observed_ms=1)
        case "book":
            context = replace(context, book=None)
        case "risk":
            context = replace(context, allow_new_exposure=False)
        case "config":
            context = replace(context, config_revision=2)
        case "market":
            context = replace(context, market=replace(context.market, generation=2))
    result = reduce(state, Start(state.scope), context)
    assert not placed(result) and result.notices


@given(cash=st.integers(100, 1200), cap=st.integers(1, 12), slots=st.integers(1, 5))
@settings(max_examples=120, derandomize=True)
def test_budget_lots_and_open_orders_are_bounded(
    state: State, context: Context, cash: int, cap: int, slots: int
) -> None:
    state = replace(state, quote_cash=D(cash))
    context = replace(context, policy=replace(context.policy, max_lots=cap, max_open_orders=slots))
    start = reduce(state, Start(state.scope), context)
    if not placed(start):
        assert cash < 110
        return
    result = reduce(start.state, execution(state, placed(start)[0], "seed"), context)
    assert len(result.state.working) <= slots
    committed = {lot.order_id for lot in result.state.lots} | {
        order.id for order in result.state.working if order.side is Side.BUY
    }
    assert len(committed) <= cap
    assert reserved(result.state, "QUOTE") + context.policy.quote_reserve <= result.state.quote_cash
    assert sum(order.side is Side.BUY for order in result.state.working) <= 3
    assert sum(order.side is Side.SELL for order in result.state.working) <= 1
    for command in result.commands:
        if isinstance(command, PlaceLimit):
            assert command.order_type == "limit"


def test_replay_is_deterministic_and_ambient_context_independent(
    state: State, context: Context
) -> None:
    start = reduce(state, Start(state.scope), context)
    seed = placed(start)[0]
    events: tuple[Event, ...] = (
        Start(state.scope),
        report(state, seed),
        execution(state, seed, "partial", D("0.05")),
        execution(state, seed, "rest", D("0.95")),
        execution(state, seed, "partial", D("0.05")),
    )

    def replay() -> tuple[Transition, ...]:
        current = state
        trace: list[Transition] = []
        for event in events:
            result = reduce(current, event, context)
            trace.append(result)
            current = result.state
        return tuple(trace)

    expected = replay()
    with localcontext() as ambient:
        ambient.prec = 3
        assert replay() == expected


def test_range_and_unsellable_partial_never_grow_inventory(state: State, context: Context) -> None:
    context = replace(context, market=replace(context.market, sell_amount=Limits(D("0.9"), None)))
    start = reduce(state, Start(state.scope), context)
    seed = placed(start)[0]
    partial = reduce(start.state, execution(state, seed, "partial", D("0.4")), context)
    canceled = reduce(partial.state, report(state, seed, "canceled", D("0.4"), True), context)
    assert canceled.state.owned_base == D("0.4") and not placed(canceled)
    bounded = replace(context, policy=replace(context.policy, lower_price=D("95")))
    full = seeded(state, bounded)
    assert sum(order.side is Side.BUY for order in full.working) == 0
    assert sum(order.side is Side.SELL for order in full.working) == 1


def test_out_of_order_open_reports_never_change_financials(state: State, context: Context) -> None:
    start = reduce(state, Start(state.scope), context)
    seed = placed(start)[0]
    posted = reduce(start.state, execution(state, seed, "seed"), context)
    old = reduce(posted.state, report(state, seed, cumulative=D("0.3")), context)
    assert old.state == posted.state and not old.commands
    contradictory = reduce(posted.state, report(state, seed, "canceled", D("0.5"), True), context)
    assert not contradictory.accepted and not placed(contradictory)
    assert contradictory.state.quote_cash == posted.state.quote_cash


def test_late_reconciliation_assertion_cannot_clear_a_new_block(
    state: State, context: Context
) -> None:
    start = reduce(state, Start(state.scope), context)
    seed = placed(start)[0]
    unknown = reduce(start.state, SubmissionUnknown(state.scope, seed.id), context)
    old_assertion = reconciled(unknown.state)
    found = reduce(unknown.state, report(state, seed), context)
    resumed = reduce(found.state, old_assertion, context)
    duplicate = reduce(resumed.state, old_assertion, context)
    assert duplicate.state == resumed.state and not duplicate.commands
    next_unknown = reduce(resumed.state, SubmissionUnknown(state.scope, seed.id), context)
    stale = reduce(next_unknown.state, old_assertion, context)
    assert stale.state == next_unknown.state and not placed(stale)


def test_actual_fee_bound_breach_posts_fact_then_stops_placement(
    state: State, context: Context
) -> None:
    context = replace(context, buy_fee=replace(context.buy_fee, max_settlements=1))
    start = reduce(state, Start(state.scope), context)
    seed = placed(start)[0]
    first = reduce(start.state, execution(state, seed, "first", D("0.4")), context)
    second = reduce(first.state, execution(state, seed, "second", D("0.6")), context)
    assert second.accepted and second.state.owned_base == 1 and not placed(second)
    assert second.state.reconcile_reason == "fee settlement bound exceeded"
    over_fee = reduce(
        start.state,
        execution(state, seed, "fee", fees=(ActualFee("QUOTE", D("1"), None),)),
        context,
    )
    assert over_fee.accepted and over_fee.state.position_cost == 101
    assert over_fee.state.reconcile_reason == "actual fee exceeds forecast" and not placed(over_fee)


def test_replacement_and_history_caps_fail_closed(state: State, context: Context) -> None:
    active = acknowledge(seeded(state, context), context)
    buy = next(order for order in active.working if order.side is Side.BUY)
    limited = replace(context, policy=replace(context.policy, max_replacements=0))
    blocked = reduce(active, execution(state, buy, "lower"), limited)
    assert blocked.accepted and blocked.state.owned_base == 2
    assert blocked.state.reconcile_reason == "SELL replacement bound reached" and not placed(
        blocked
    )
    limited = replace(context, policy=replace(context.policy, max_fill_records=1))
    full_history = reduce(active, execution(state, buy, "lower"), limited)
    assert not full_history.accepted and len(full_history.state.fills) == 1
    assert (
        full_history.state.reconcile_reason == "fill history capacity requires durable checkpoint"
    )


@pytest.mark.parametrize("currency", ["BASE", "QUOTE", "TOKEN"])
def test_partial_sell_fee_reservation_and_cost_conservation(
    state: State, context: Context, currency: str
) -> None:
    rule = FeeRule(currency, D("0.001"), D("0.0001"), D("2"), 4)
    context = replace(context, sell_fee=rule)
    active = acknowledge(seeded(state, context), context)
    sell = next(order for order in active.working if order.side is Side.SELL)
    quantity = D("0.2")
    estimated = replace(rule, max_settlements=1).estimate(context.market, sell.price, quantity)
    quote_value = estimated.amount * D(2) if currency == "TOKEN" else None
    result = reduce(
        active,
        execution(
            state,
            sell,
            "partial-sale",
            quantity,
            fees=(ActualFee(currency, estimated.amount, quote_value),),
        ),
        context,
    )
    assert result.accepted
    assert reserved(result.state, "BASE") <= result.state.owned_base
    assert result.state.position_cost + result.state.quote_cash - (quote_value or D(0)) == (
        state.quote_cash + result.state.realized_pnl
    )
    assert sum(order.side is Side.SELL for order in result.state.working) == 1


@given(actions=st.lists(st.tuples(st.integers(0, 6), st.integers(0, 50)), min_size=1, max_size=80))
@settings(max_examples=100, derandomize=True, deadline=None)
def test_generated_event_sequences_preserve_ownership_and_single_sell(
    state: State, context: Context, actions: list[tuple[int, int]]
) -> None:
    current = reduce(state, Start(state.scope), context).state
    for index, (kind, choice) in enumerate(actions):
        working = current.working
        if not working:
            break
        order = working[choice % len(working)]
        event: Event
        if kind == 0:
            event = report(current, order, cumulative=order.filled)
        elif kind == 1:
            amount = min(D("0.05"), order.remaining)
            event = execution(current, order, f"trade-{index}", amount)
        elif kind == 2:
            event = report(current, order, "canceled", order.filled, True)
        elif kind == 3:
            event = CancelAcknowledged(current.scope, order.id)
        elif kind == 4 and current.fills:
            event = current.fills[choice % len(current.fills)]
        elif kind == 5:
            event = SubmissionUnknown(current.scope, order.id)
        else:
            event = reconciled(current)
        previous = current
        result = reduce(current, event, context)
        current = result.state
        assert current.quote_cash >= 0 and current.position_cost >= 0 and current.owned_base >= 0
        assert len([order for order in current.working if order.side is Side.SELL]) <= 1
        assert len(current.working) <= context.policy.max_open_orders
        assert current.quote_cash + current.position_cost == state.quote_cash + current.realized_pnl
        assert reserved(current, "QUOTE") + context.policy.quote_reserve <= current.quote_cash
        assert reserved(current, "BASE") <= current.owned_base
        if isinstance(event, Fill) and event in previous.fills:
            assert current == previous and not result.commands
        else:
            repeat = reduce(previous, event, context)
            assert repeat == result
        for command in result.commands:
            if isinstance(command, PlaceLimit):
                assert command.order_type == "limit"


@given(ordering=st.permutations((0, 1, 2, 3)))
@settings(max_examples=24, derandomize=True)
def test_all_drain_event_orderings_reuse_late_inventory(
    state: State, context: Context, ordering: list[int]
) -> None:
    active = acknowledge(seeded(state, context), context)
    sell = next(order for order in active.working if order.side is Side.SELL)
    buys = tuple(order for order in active.working if order.side is Side.BUY)
    current = reduce(active, execution(state, sell, "sold"), context).state
    events: tuple[Event, ...] = (
        execution(state, buys[0], "late", D("0.4")),
        report(state, buys[0], "canceled", D("0.4"), True),
        report(state, buys[1], "canceled", D(0), True),
        report(state, buys[2], "canceled", D(0), True),
    )
    all_placed: list[Order] = []
    for index in ordering:
        result = reduce(current, events[index], context)
        current = result.state
        all_placed.extend(placed(result))
        assert sum(order.side is Side.SELL for order in current.working) <= 1
    settled = reduce(current, reconciled(current), context)
    all_placed.extend(placed(settled))
    assert settled.state.round_id == 2 and settled.state.owned_base == D("0.4")
    assert settled.state.anchor == buys[0].price
    assert all(order.role != "seed" for order in all_placed)
    assert sum(order.side is Side.SELL for order in all_placed) == 1


def test_fee_token_shortage_rejects_buy_with_unfunded_exit(state: State, context: Context) -> None:
    context = replace(context, sell_fee=FeeRule("TOKEN", D("0.01"), D("0.01"), D("2"), 2))
    state = replace(state, fee_balances=(Money("TOKEN", D("0.1")),))
    result = reduce(state, Start(state.scope), context)
    assert not placed(result) and result.state.owned_base == 0
    assert result.notices == ("insufficient fee-token funds for prospective SELL",)


def test_pending_fee_cost_counts_towards_acquisition_cap(state: State, context: Context) -> None:
    context = replace(
        context,
        policy=replace(context.policy, max_quote_committed=D("273.80")),
        buy_fee=FeeRule("QUOTE", D("0.001"), D("0.01"), None, 2),
    )
    start = reduce(state, Start(state.scope), context)
    result = reduce(
        start.state,
        execution(state, placed(start)[0], "seed", fees=(ActualFee("QUOTE", D("0.1"), None),)),
        context,
    )
    assert len(placed(result, Side.BUY)) == 1
    assert "committed acquisition cost cap reached" in result.notices


def test_cancellation_without_fill_does_not_advance_buy_ladder(
    state: State, context: Context
) -> None:
    active = acknowledge(seeded(state, context), context)
    buy = next(order for order in active.working if order.side is Side.BUY)
    canceled = reduce(active, report(state, buy, "canceled", D(0), True), context)
    assert canceled.state.next_level == active.next_level
    assert not placed(canceled, Side.BUY)
    assert sum(order.side is Side.BUY for order in canceled.state.working) == 2
    repeated = reduce(canceled.state, reconciled(canceled.state), context)
    assert not placed(repeated, Side.BUY)


def test_buy_fill_earns_one_replenishment_after_sell_replacement(
    state: State, context: Context
) -> None:
    active = acknowledge(seeded(state, context), context)
    buy = next(order for order in active.working if order.side is Side.BUY)
    sell = next(order for order in active.working if order.side is Side.SELL)
    filled = reduce(active, execution(state, buy, "lower"), context)
    assert filled.state.ladder_credits == 1 and not placed(filled, Side.BUY)
    replaced = reduce(filled.state, report(state, sell, "canceled", D(0), True), context)
    assert len(placed(replaced, Side.BUY)) == 1
    assert placed(replaced, Side.BUY)[0].level == 4
    assert replaced.state.ladder_credits == 0
    assert sum(order.side is Side.BUY for order in replaced.state.working) == 3
