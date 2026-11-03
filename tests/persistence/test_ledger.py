"""Funding, posting, reservations, uncertainty and durable replay invariants."""

import sqlite3
from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext

import pytest

from grid.domain.market import FeeRule, Money
from grid.domain.model import (
    ActualFee,
    CancelAcknowledged,
    Context,
    Fill,
    OrderReport,
    Phase,
    PlaceLimit,
    Reconciled,
    Side,
    Start,
    State,
)
from grid.domain.numbers import DomainError
from grid.domain.reducer import reduce, reserved
from grid.persistence import codec
from grid.persistence.ledger import Ledger, compact
from grid.persistence.records import Bounds, Conflict, LedgerError, Outcome

from .conftest import SOURCES


def current(outcome: Outcome) -> State:
    assert outcome.state is not None
    return outcome.state


def version(outcome: Outcome) -> int:
    assert outcome.version is not None
    return outcome.version


def post(
    ledger: Ledger,
    request: str,
    event: Fill | OrderReport | Reconciled | Start | CancelAcknowledged,
    context: Context,
) -> Outcome:
    return ledger.apply(request, version(ledger.snapshot(event.scope)), event, context)


def test_assignment_reserves_seed_and_cannot_duplicate_last_cash(
    ledger: Ledger, state: State, context: Context
) -> None:
    ledger.credit("fund", state.scope.venue, state.scope.account, Money("QUOTE", D(200)))
    first = ledger.start("one", state.scope, (Money("QUOTE", D(200)),), context)
    assert reserved(current(first), "QUOTE") == 100
    other_scope = replace(state.scope, allocation="second", symbol="OTHER/QUOTE")
    other_context = replace(
        context, market=replace(context.market, symbol="OTHER/QUOTE", base="OTHER")
    )
    with pytest.raises(DomainError):
        ledger.start("two", other_scope, (Money("QUOTE", D(200)),), other_context)
    assert ledger.pool(state.scope.venue, state.scope.account, "QUOTE") == 0
    assert ledger.lookup("two") is None
    assert ledger.db.execute("SELECT count(*) FROM allocations").fetchone() == (1,)
    assert ledger.start("one", state.scope, (Money("QUOTE", D(200)),), context) == first
    with pytest.raises(Conflict):
        ledger.start("one", state.scope, (Money("QUOTE", D(199)),), context)


def test_assignment_is_rolled_back_when_seed_is_invalid(
    ledger: Ledger, state: State, context: Context
) -> None:
    ledger.credit("fund", state.scope.venue, state.scope.account, Money("QUOTE", D(100)))
    with pytest.raises(LedgerError, match="seed"):
        ledger.start("seed", state.scope, (Money("QUOTE", D(100)),), context)
    assert ledger.pool(state.scope.venue, state.scope.account, "QUOTE") == 100
    assert ledger.db.execute("SELECT count(*) FROM allocations").fetchone() == (0,)


def test_release_only_unreserved_cash_and_optimistic_version(funded: Ledger, state: State) -> None:
    initial = funded.snapshot(state.scope)
    with pytest.raises(LedgerError, match="reserved"):
        funded.release("too-much", state.scope, version(initial), Money("QUOTE", D(901)))
    released = funded.release("release", state.scope, version(initial), Money("QUOTE", D(900)))
    assert current(released).quote_cash == reserved(current(released), "QUOTE") == 100
    assert funded.pool(state.scope.venue, state.scope.account, "QUOTE") == 900
    with pytest.raises(Conflict):
        funded.release("stale", state.scope, version(initial), Money("TOKEN", D(1)))


@pytest.mark.parametrize(
    "fees,expected_cash,expected_base,expected_cost,expected_token",
    [
        ((), "950", "0.5", "50", "10"),
        ((ActualFee("QUOTE", D("0.1"), None),), "949.9", "0.5", "50.1", "10"),
        ((ActualFee("BASE", D("0.001"), None),), "950", "0.499", "50", "10"),
        ((ActualFee("TOKEN", D("0.01"), D("0.2")),), "950", "0.5", "50.2", "9.99"),
        ((ActualFee("QUOTE", D("-0.1"), None),), "950.1", "0.5", "49.9", "10"),
        ((ActualFee("TOKEN", D("-0.01"), D("-0.2")),), "950", "0.5", "49.8", "10.01"),
    ],
)
def test_partial_fill_posts_exact_cash_fees_cost_and_reservations_once(
    funded: Ledger,
    state: State,
    context: Context,
    fees: tuple[ActualFee, ...],
    expected_cash: str,
    expected_base: str,
    expected_cost: str,
    expected_token: str,
) -> None:
    event = Fill(state.scope, "trade", 1, D("0.5"), D(100), fees)
    with localcontext() as ambient:
        ambient.prec = 4
        outcome = post(funded, "fill", event, context)
    posted = current(outcome)
    assert outcome.accepted
    assert posted.quote_cash == D(expected_cash)
    assert posted.owned_base == D(expected_base)
    assert posted.position_cost == D(expected_cost)
    assert posted.fee_balances == (Money("TOKEN", D(expected_token)),)
    assert reserved(posted, "QUOTE") == 50  # Fee excess cannot release principal.
    duplicate = post(funded, "replay", event, context)
    assert current(duplicate) == posted
    assert funded.db.execute("SELECT count(*) FROM fills").fetchone() == (1,)
    assert funded.db.execute("SELECT count(*) FROM fees").fetchone() == (len(fees),)
    funded._verify_projection(codec.digest(state.scope), posted, context)
    for table, field in (
        ("balances", "amount"),
        ("fees", "amount"),
        ("positions", "cost"),
        ("journal", "delta"),
    ):
        assert all(
            row[0] == "text" for row in funded.db.execute(f"SELECT typeof({field}) FROM {table}")
        )


def test_rejected_missing_fees_and_conflict_do_not_partially_post(
    funded: Ledger, state: State, context: Context
) -> None:
    before = current(funded.snapshot(state.scope))
    rejected = post(funded, "missing", Fill(state.scope, "t", 1, D("0.5"), D(100), None), context)
    assert not rejected.accepted
    assert current(rejected).quote_cash == before.quote_cash
    assert funded.db.execute("SELECT count(*) FROM fills").fetchone() == (0,)
    event = Fill(state.scope, "t", 1, D("0.5"), D(100), ())
    accepted = post(funded, "known", event, context)
    conflict = post(funded, "conflict", replace(event, price=D(99)), context)
    assert not conflict.accepted
    assert current(conflict).quote_cash == current(accepted).quote_cash
    assert current(conflict).reconcile_reason == "conflicting duplicate fill"
    assert funded.db.execute("SELECT count(*) FROM fills").fetchone() == (1,)


def test_status_only_never_posts_pnl_and_full_fill_preserves_one_sell(
    funded: Ledger, state: State, context: Context
) -> None:
    initial = current(funded.snapshot(state.scope))
    early = post(funded, "early-status", OrderReport(state.scope, 1, "filled", D(1), True), context)
    assert current(early).quote_cash == initial.quote_cash
    assert current(early).position_cost == current(early).realized_pnl == 0
    filled = post(funded, "fill", Fill(state.scope, "trade", 1, D(1), D(100), ()), context)
    held = current(filled)
    assert held.owned_base == 1 and held.quote_cash == 900
    reconciled = post(
        funded,
        "reconcile",
        Reconciled(state.scope, 1, 1, held.reconcile_reason, held.reconcile_revision),
        context,
    )
    sells = [order for order in current(reconciled).working if order.side is Side.SELL]
    assert len(sells) == 1
    with pytest.raises(sqlite3.IntegrityError):
        funded.db.execute(
            "INSERT INTO orders VALUES (?,999,'sell',1,'invalid')", (codec.digest(state.scope),)
        )
    stale = post(funded, "stale-open", OrderReport(state.scope, 1, "open", D(0), False), context)
    assert current(stale).quote_cash == 900
    assert len([order for order in current(stale).working if order.side is Side.SELL]) == 1


def test_unknown_attempt_survives_restart_and_is_never_resubmitted(
    funded: Ledger, state: State, context: Context, bounds: Bounds
) -> None:
    intent = funded.intents(state.scope)[0]
    attempt = funded.attempt("attempt", state.scope, 1, intent.id)
    assert current(attempt).orders[0].unknown
    path = funded.storage.path
    funded.close()
    reopened = Ledger(path, bounds, SOURCES, mock_storage=True)
    try:
        restored = reopened.snapshot(state.scope)
        assert current(restored).orders[0].unknown
        assert reserved(current(restored), "QUOTE") == 100
        assert any(item.status == "uncertain" for item in reopened.intents(state.scope))
        assert reopened.attempt("attempt", state.scope, 1, intent.id) == attempt
        with pytest.raises(LedgerError, match="reconciliation"):
            reopened.attempt("retry", state.scope, version(restored), intent.id)
        recon = post(
            reopened,
            "premature",
            Reconciled(
                state.scope,
                1,
                1,
                current(restored).reconcile_reason,
                current(restored).reconcile_revision,
            ),
            context,
        )
        assert not recon.accepted
        report = post(reopened, "found", OrderReport(state.scope, 1, "open", D(0), True), context)
        known = current(report)
        cleared = post(
            reopened,
            "done",
            Reconciled(state.scope, 1, 1, known.reconcile_reason, known.reconcile_revision),
            context,
        )
        assert cleared.accepted and current(cleared).reconcile_reason is None
        assert not reopened.intents(state.scope)
        assert reopened.db.execute("SELECT count(*) FROM orders").fetchone() == (1,)
    finally:
        reopened.close()


def test_compaction_keeps_next_commands_accounting_and_durable_fill_identity(
    funded: Ledger, state: State, context: Context, bounds: Bounds
) -> None:
    filled = post(funded, "fill", Fill(state.scope, "seed-trade", 1, D(1), D(100), ()), context)
    before = current(filled)
    after = compact(before, context)
    assert after.fills == ()
    ladder = next(order for order in before.working if order.role == "ladder")
    next_fill = Fill(state.scope, "ladder-trade", ladder.id, ladder.amount, ladder.price, ())
    expected = reduce(before, next_fill, context)
    actual = reduce(after, next_fill, context)
    assert actual.commands == expected.commands
    assert compact(actual.state, context) == compact(expected.state, context)
    checkpointed = funded.compact("checkpoint", state.scope, version(filled))
    assert current(checkpointed) == after
    duplicate = post(
        funded, "old-replay", Fill(state.scope, "seed-trade", 1, D(1), D(100), ()), context
    )
    assert current(duplicate) == after
    assert funded.db.execute("SELECT count(*) FROM fills").fetchone() == (1,)
    path = funded.storage.path
    funded.close()
    reopened = Ledger(path, bounds, SOURCES, mock_storage=True)
    try:
        restored = current(reopened.snapshot(state.scope))
        assert restored.orders == after.orders
        assert restored.quote_cash == after.quote_cash
        assert restored.next_id == after.next_id
        assert restored.reconcile_reason == "ledger owner recovery"
    finally:
        reopened.close()


def test_fee_reserve_consumption_tracks_partial_settlements(
    ledger: Ledger, state: State, context: Context
) -> None:
    context = replace(context, buy_fee=FeeRule("QUOTE", D("0.001"), D("0.01"), None, 2))
    ledger.credit("fund", state.scope.venue, state.scope.account, Money("QUOTE", D(1000)))
    started = ledger.start("start", state.scope, (Money("QUOTE", D(1000)),), context)
    assert reserved(current(started), "QUOTE") == D("100.11")
    filled = post(
        ledger,
        "fill",
        Fill(state.scope, "t", 1, D("0.5"), D(100), (ActualFee("QUOTE", D("0.05"), None),)),
        context,
    )
    assert reserved(current(filled), "QUOTE") == D("50.06")


def test_complete_round_compacts_archived_orders_without_losing_identity(
    funded: Ledger,
    state: State,
    context: Context,
) -> None:
    seed_fill = Fill(state.scope, "seed-trade", 1, D(1), D(100), ())
    seeded = current(post(funded, "seed-fill", seed_fill, context))
    for order in seeded.working:
        post(
            funded,
            f"ack-{order.id}",
            OrderReport(state.scope, order.id, "open", D(0), True),
            context,
        )
    sell = next(order for order in seeded.working if order.side is Side.SELL)
    closed = current(
        post(
            funded,
            "sell-fill",
            Fill(state.scope, "sell-trade", sell.id, sell.amount, sell.price, ()),
            context,
        )
    )
    assert closed.phase is Phase.DRAINING
    assert closed.quote_cash == 1010 and closed.position_cost == 0 and closed.realized_pnl == 10
    for order in closed.working:
        post(
            funded,
            f"cancel-{order.id}",
            OrderReport(state.scope, order.id, "canceled", D(0), True),
            context,
        )
    before = current(funded.snapshot(state.scope))
    assert before.round_id == 2
    result = funded.compact("compact", state.scope, version(funded.snapshot(state.scope)))
    after = current(result)
    assert len(after.orders) == 1 and after.orders[0].role == "seed"
    assert after.next_id == before.next_id and after.fills == ()
    replay = post(funded, "archived-trade", seed_fill, context)
    assert current(replay) == after
    old_status = post(
        funded,
        "archived-status",
        OrderReport(state.scope, sell.id, "filled", sell.amount, True),
        context,
    )
    assert current(old_status) == after
    next_seed = after.orders[0]
    event = Fill(state.scope, "next-round", next_seed.id, next_seed.amount, next_seed.price, ())
    expected, actual = reduce(before, event, context), reduce(after, event, context)
    assert expected.commands == actual.commands
    assert compact(expected.state, context) == compact(actual.state, context)
    conflict = post(
        funded,
        "conflicting-old-status",
        OrderReport(state.scope, sell.id, "filled", D("0.5"), True),
        context,
    )
    assert not conflict.accepted and current(conflict).quote_cash == after.quote_cash


def test_sell_cancel_ack_and_late_fill_do_not_free_durable_slot(
    funded: Ledger,
    state: State,
    context: Context,
) -> None:
    seeded = current(
        post(funded, "seed-fill", Fill(state.scope, "seed", 1, D(1), D(100), ()), context)
    )
    for order in seeded.working:
        post(
            funded,
            f"ack-{order.id}",
            OrderReport(state.scope, order.id, "open", D(0), True),
            context,
        )
    sell = next(order for order in seeded.working if order.side is Side.SELL)
    buy = next(order for order in seeded.working if order.role == "ladder")
    changed = current(
        post(
            funded, "buy-fill", Fill(state.scope, "lower", buy.id, D("0.5"), buy.price, ()), context
        )
    )
    assert next(order for order in changed.working if order.side is Side.SELL).cancel_requested
    acked = current(post(funded, "cancel-ack", CancelAcknowledged(state.scope, sell.id), context))
    assert len([order for order in acked.working if order.side is Side.SELL]) == 1
    partial = current(
        post(
            funded,
            "late-sell",
            Fill(state.scope, "late", sell.id, D("0.5"), sell.price, ()),
            context,
        )
    )
    assert partial.owned_base == 1
    assert reserved(partial, "BASE") == D("0.5")
    assert funded.db.execute(
        "SELECT count(*) FROM orders WHERE side='sell' AND unresolved=1"
    ).fetchone() == (1,)
    terminal = current(
        post(
            funded,
            "terminal",
            OrderReport(state.scope, sell.id, "canceled", D("0.5"), True),
            context,
        )
    )
    replacement = [order for order in terminal.working if order.side is Side.SELL]
    assert len(replacement) == 1 and replacement[0].id != sell.id
    assert replacement[0].amount <= terminal.owned_base
    funded._verify_projection(codec.digest(state.scope), terminal, context)


def test_unknown_sell_keeps_slot_and_new_fill_inventory_through_restart(
    funded: Ledger,
    state: State,
    context: Context,
    bounds: Bounds,
) -> None:
    first = post(funded, "partial", Fill(state.scope, "first", 1, D("0.5"), D(100), ()), context)
    sell_intent = next(
        intent
        for intent in funded.intents(state.scope)
        if codec.loads(intent.payload, PlaceLimit).order.side is Side.SELL
    )
    attempted = funded.attempt("sell-attempt", state.scope, version(first), sell_intent.id)
    old_sell = next(order for order in current(attempted).working if order.side is Side.SELL)
    path = funded.storage.path
    funded.close()
    reopened = Ledger(path, bounds, SOURCES, mock_storage=True)
    try:
        after = current(
            post(
                reopened, "late-buy", Fill(state.scope, "second", 1, D("0.5"), D(100), ()), context
            )
        )
        sells = [order for order in after.working if order.side is Side.SELL]
        assert len(sells) == 1 and sells[0].id == old_sell.id and sells[0].unknown
        assert after.owned_base == 1
        assert reserved(after, "BASE") == D("0.5")
        assert reopened.db.execute(
            "SELECT count(*) FROM orders WHERE side='sell' AND unresolved=1"
        ).fetchone() == (1,)
        assert any(
            intent.id == sell_intent.id and intent.status == "uncertain"
            for intent in reopened.intents(state.scope)
        )
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "damage,message",
    [
        ("UPDATE fees SET amount='0'", "fee projection"),
        (
            "UPDATE journal SET delta='999' WHERE request_id='seed' "
            "AND kind='balance' AND currency='QUOTE'",
            "balance journal",
        ),
        ("UPDATE pools SET amount='999' WHERE currency='QUOTE'", "unassigned funds journal"),
    ],
)
def test_recovery_rejects_corrupted_fee_and_journal_evidence(
    funded: Ledger,
    state: State,
    context: Context,
    bounds: Bounds,
    damage: str,
    message: str,
) -> None:
    post(
        funded,
        "fill",
        Fill(state.scope, "t", 1, D("0.5"), D(100), (ActualFee("QUOTE", D("0.1"), None),)),
        context,
    )
    path = funded.storage.path
    funded.db.execute(damage)
    funded.close()
    with pytest.raises(LedgerError, match=message):
        Ledger(path, bounds, SOURCES, mock_storage=True)


@pytest.mark.parametrize("page_size", [1, 2])
def test_restart_reconciles_multiple_allocations_without_pinning_reader(
    ledger: Ledger,
    state: State,
    context: Context,
    bounds: Bounds,
    page_size: int,
) -> None:
    ledger.credit("fund-all", state.scope.venue, state.scope.account, Money("QUOTE", D(3000)))
    scopes = []
    for index, base in enumerate(("BASE", "OTHER", "THIRD")):
        scope = replace(state.scope, allocation=f"allocation-{index}", symbol=f"{base}/QUOTE")
        scoped_context = replace(
            context, market=replace(context.market, symbol=scope.symbol, base=base)
        )
        ledger.start(f"seed-{index}", scope, (Money("QUOTE", D(1000)),), scoped_context)
        scopes.append(scope)
    before = {scope: ledger.snapshot(scope) for scope in scopes}
    path = ledger.storage.path
    ledger.close()

    recovered = Ledger(path, replace(bounds, read_rows=page_size), SOURCES, mock_storage=True)
    try:
        for scope in scopes:
            snapshot = recovered.snapshot(scope)
            restored = current(snapshot)
            assert restored.quote_cash == current(before[scope]).quote_cash == 1000
            assert restored.orders == current(before[scope]).orders
            assert reserved(restored, "QUOTE") == 100
            assert restored.reconcile_reason == "ledger owner recovery"
            assert version(snapshot) == version(before[scope]) + 1
            assert recovered.db.execute(
                "SELECT recovery FROM allocations WHERE id=?", (codec.digest(scope),)
            ).fetchone() == (1,)
        assert recovered.db.execute(
            "SELECT count(*) FROM intents WHERE kind='reconcile' AND status='queued'"
        ).fetchone() == (3,)
        assert recovered.pool(state.scope.venue, state.scope.account, "QUOTE") == 0
        recovered.storage.checkpoint()
    finally:
        recovered.close()
