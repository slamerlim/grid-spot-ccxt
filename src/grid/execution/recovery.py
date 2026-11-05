"""Sole-writer recovery targets and bounded unified reads; no financial posting."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from grid.domain.model import Fill, Order, PlaceLimit, Scope, Side
from grid.domain.numbers import ZERO, bounded_int, exact, identifier
from grid.exchange.models import Failure, ReadError
from grid.exchange.private import (
    AccountReadTransport,
    OrderHistoryBatch,
    OrderHistoryObservation,
    OrderReadTransport,
    TradeObservation,
    read_account_window,
    read_order_window,
)
from grid.execution.models import ExecutionError
from grid.persistence import client_ids, codec
from grid.persistence.ledger import Ledger, client_identity, trade_identity
from grid.persistence.records import ClientIdentity
from grid.persistence.writer import AsyncWriter


@dataclass(frozen=True)
class CommittedTrade:
    external_id: str
    fill: Fill


@dataclass(frozen=True)
class RecoveryTarget:
    order: Order
    external_id: str | None
    client: ClientIdentity | None
    committed: tuple[CommittedTrade, ...] = ()


@dataclass(frozen=True)
class RecoveryPlan:
    scope: Scope
    version: int
    targets: tuple[RecoveryTarget, ...]


@dataclass(frozen=True)
class RecoveryInspection:
    plan: RecoveryPlan
    histories: tuple[OrderHistoryObservation, ...]
    unmatched_trades: tuple[TradeObservation, ...] = ()
    new_trades: tuple[TradeObservation, ...] = ()
    committed_trades: tuple[TradeObservation, ...] = ()

    @property
    def unresolved(self) -> tuple[RecoveryTarget, ...]:
        """Unknown external IDs remain obligations, never inferred rejections."""
        return tuple(target for target in self.plan.targets if target.external_id is None)


def _check_fee_projection(ledger: Ledger, fill: Fill) -> None:
    if fill.fees is None:
        raise ExecutionError("committed recovery fill lacks fee evidence")
    expected = tuple(
        (
            index,
            fee.currency,
            codec.amount(fee.amount),
            None if fee.quote_value is None else codec.amount(fee.quote_value),
        )
        for index, fee in enumerate(fill.fees)
    )
    rows = tuple(
        ledger.db.execute(
            "SELECT ordinal,currency,amount,quote_value FROM fees "
            "WHERE allocation=? AND trade_id=? ORDER BY ordinal LIMIT 17",
            (codec.digest(fill.scope), fill.trade_id),
        )
    )
    if rows != expected:
        raise ExecutionError("committed recovery fee projection differs from its fill")


@exact
def _baseline(
    ledger: Ledger,
    scope: Scope,
    order: Order,
    external: str | None,
    retained: dict[str, Fill],
    remaining: int,
) -> tuple[CommittedTrade, ...]:
    rows = ledger.db.execute(
        "SELECT f.trade_id,f.payload,t.venue,t.account,t.symbol,t.external_id "
        "FROM fills f LEFT JOIN trade_keys t "
        "ON t.allocation=f.allocation AND t.trade_id=f.trade_id "
        "WHERE f.allocation=? AND f.order_id=? ORDER BY f.trade_id LIMIT ?",
        (codec.digest(scope), order.id, remaining + 1),
    ).fetchall()
    if len(rows) > remaining:
        raise ExecutionError("recovery committed-fill bound exceeded")
    if len(rows) != len(retained) or rows and external is None:
        raise ExecutionError("recovery retained fills or external binding are incomplete")
    committed = []
    for trade_id, payload, venue, account, symbol, external_id in rows:
        fill = retained.get(trade_id)
        if (
            fill is None
            or fill.scope != scope
            or fill.order_id != order.id
            or (venue, account, symbol) != (scope.venue, scope.account, scope.symbol)
            or external_id is None
            or trade_identity(scope, external_id) != trade_id
            or payload != codec.dumps(fill, limit=ledger.bounds.snapshot_bytes)
        ):
            raise ExecutionError("recovery committed fill differs from its durable identity")
        _check_fee_projection(ledger, fill)
        committed.append(CommittedTrade(external_id, fill))
    if (
        sum((row.fill.amount for row in committed), ZERO) != order.filled
        or sum((row.fill.amount * row.fill.price for row in committed), ZERO) != order.gross_cost
    ):
        raise ExecutionError("recovery committed fills differ from durable cumulative totals")
    return tuple(committed)


@exact
def recovery_plan(
    ledger: Ledger,
    scope: Scope,
    *,
    max_orders: int,
    max_committed_trades: int = 1000,
) -> RecoveryPlan:
    """Validate working-order identities and a bounded durable fill baseline."""
    bounded_int(max_orders, 1, 1024)
    bounded_int(max_committed_trades, 1, 10000)
    snapshot = ledger.snapshot(scope)
    if snapshot.state is None or snapshot.version is None:
        raise ExecutionError("recovery allocation missing")
    working = snapshot.state.working
    if len(working) > max_orders:
        raise ExecutionError("recovery target bound exceeded")
    allocation = codec.digest(scope)
    retained: dict[int, dict[str, Fill]] = {order.id: {} for order in working}
    for fill in snapshot.state.fills:
        if fill.order_id in retained:
            retained[fill.order_id][fill.trade_id] = fill
    if sum(len(fills) for fills in retained.values()) > max_committed_trades:
        raise ExecutionError("recovery committed-fill bound exceeded")
    targets = []
    remaining = max_committed_trades
    for order in working:
        if ledger.order(scope, order.id) != order:
            raise ExecutionError("recovery order differs from durable projection")
        # This is the unchanged key used by Ledger._commands, including for
        # resolved placement intents. No lifetime intent scan or new index.
        key = codec.digest((allocation, "place", order.id, None))
        row = ledger.db.execute(
            "SELECT allocation,order_id,kind,payload,status,attempt_run FROM intents WHERE id=?",
            (key,),
        ).fetchone()
        if row is None or row[:3] != (allocation, order.id, "place"):
            raise ExecutionError("recovery placement identity missing or inconsistent")
        original = codec.loads(row[3], PlaceLimit, limit=ledger.bounds.snapshot_bytes)
        initial = replace(
            order,
            filled=ZERO,
            gross_cost=ZERO,
            acknowledged=False,
            unknown=False,
            cancel_requested=False,
            terminal_amount=None,
            terminal_proven=False,
        )
        if original != PlaceLimit(scope, initial):
            raise ExecutionError("recovery placement differs from the reserved intent")
        binding = ledger.db.execute(
            "SELECT * FROM order_bindings WHERE allocation=? AND order_id=?",
            (allocation, order.id),
        ).fetchone()
        external = None if binding is None else identifier(binding[5])
        if binding is not None and binding != (
            allocation,
            order.id,
            scope.venue,
            scope.account,
            scope.symbol,
            external,
            client_identity(scope, order.id),
        ):
            raise ExecutionError("recovery external binding differs from owned identity")
        client = client_ids.lookup(ledger, scope, order.id)
        committed = _baseline(ledger, scope, order, external, retained[order.id], remaining)
        remaining -= len(committed)
        if row[5] is None:
            if row[4] != "queued" or order.unknown or order.acknowledged or external is not None:
                raise ExecutionError("unattempted recovery order has external uncertainty")
            continue  # Registration alone does not mean a submission occurred.
        if row[4] not in ("uncertain", "resolved"):
            raise ExecutionError("attempted recovery order has an invalid intent status")
        targets.append(RecoveryTarget(order, external, client, committed))
    return RecoveryPlan(scope, snapshot.version, tuple(targets))


@exact
def _check_terms(
    plan: RecoveryPlan, target: RecoveryTarget, history: OrderHistoryObservation
) -> None:
    observed, order = history.order, target.order
    expected_client = (
        client_identity(plan.scope, order.id)
        if target.client is None
        else target.client.returned_id
    )
    if observed.order_id != target.external_id or observed.symbol != plan.scope.symbol:
        raise ExecutionError("recovery observation belongs to another order")
    for actual, expected in (
        (observed.client_id, expected_client),
        (observed.order_type, "limit"),
        (observed.side, str(order.side)),
        (observed.amount, order.amount),
        (observed.price, order.price),
    ):
        if actual is not None and actual != expected:
            raise ExecutionError("recovery observation contradicts its owned intent")
    if (
        observed.filled is None
        or not order.filled <= observed.filled <= order.amount
        or observed.remaining is not None
        and observed.remaining != order.amount - observed.filled
        or observed.status == "closed"
        and observed.filled != order.amount
        or observed.status == "rejected"
        and observed.filled != ZERO
    ):
        raise ExecutionError("recovery cumulative contradicts the durable order")


async def inspect_orders(
    writer: AsyncWriter,
    scope: Scope,
    transport: OrderReadTransport,
    *,
    max_orders: int,
    max_trades: int,
    max_calls: int,
    timeout_ms: int,
    max_committed_trades: int = 1000,
) -> RecoveryInspection:
    """Observe known IDs, retain unknowns, then recheck the entire plan in SQLite.

    At most 100 orders / 10,000 trade observations. Limits count unified calls;
    the caller-owned transport enforces underlying HTTP, rate and source policy.
    Posted working-order fills have a separate 1–10,000 record cap (default 1,000).
    Stable recent windows may omit old fills only after their baseline is proved.
    This is a read-only inspection, never account completeness or send authority.
    """

    async def read(identities: tuple[str, ...]) -> OrderHistoryBatch:
        histories = []
        for identity in identities:
            histories.append(
                await read_order_window(
                    transport, identity, scope.symbol, max_trades=max_trades, timeout_ms=timeout_ms
                )
            )
        return OrderHistoryBatch(tuple(histories))

    return await _inspect(
        writer,
        scope,
        read,
        calls_per_order=3,
        extra_calls=0,
        max_orders=max_orders,
        max_trades=max_trades,
        max_calls=max_calls,
        timeout_ms=timeout_ms,
        max_committed_trades=max_committed_trades,
    )


async def inspect_account_orders(
    writer: AsyncWriter,
    scope: Scope,
    transport: AccountReadTransport,
    *,
    max_orders: int,
    max_trades: int,
    max_calls: int,
    timeout_ms: int,
    max_committed_trades: int = 1000,
) -> RecoveryInspection:
    """One bounded symbol trade page for all known IDs; retain unmatched trades.

    Explicit alternative to per-order trade reads, never an automatic error
    fallback. The total trade cap covers the whole page, including unmatched
    orders and duplicates. Unknown external IDs remain unresolved. Existing
    committed fills prove the old quantity; observations must cover the new gap.
    """

    async def read(identities: tuple[str, ...]) -> OrderHistoryBatch:
        return await read_account_window(
            transport, identities, scope.symbol, max_trades=max_trades, timeout_ms=timeout_ms
        )

    return await _inspect(
        writer,
        scope,
        read,
        calls_per_order=2,
        extra_calls=1,
        max_orders=max_orders,
        max_trades=max_trades,
        max_calls=max_calls,
        timeout_ms=timeout_ms,
        max_committed_trades=max_committed_trades,
    )


async def _inspect(
    writer: AsyncWriter,
    scope: Scope,
    read: Callable[[tuple[str, ...]], Awaitable[OrderHistoryBatch]],
    *,
    calls_per_order: int,
    extra_calls: int,
    max_orders: int,
    max_trades: int,
    max_calls: int,
    timeout_ms: int,
    max_committed_trades: int,
) -> RecoveryInspection:
    bounded_int(max_orders, 1, 100)
    bounded_int(max_trades, 1, 100)
    bounded_int(max_calls, 1, 300)
    bounded_int(timeout_ms, 1, 30_000)
    bounded_int(max_committed_trades, 1, 10000)
    async with asyncio.timeout(timeout_ms / 1000):
        plan = await writer.submit(
            lambda ledger: recovery_plan(
                ledger, scope, max_orders=max_orders, max_committed_trades=max_committed_trades
            )
        )
        known = {
            target.external_id: target for target in plan.targets if target.external_id is not None
        }
        if calls_per_order * len(known) + (extra_calls if known else 0) > max_calls:
            raise ExecutionError("recovery unified-call budget is insufficient")
        batch = await read(tuple(known)) if known else OrderHistoryBatch(())

        def finish(ledger: Ledger) -> RecoveryInspection:
            current = recovery_plan(
                ledger, scope, max_orders=max_orders, max_committed_trades=max_committed_trades
            )
            if current != plan:
                raise ExecutionError("recovery plan changed during observation")
            return _compare(ledger, plan, batch)

        return await writer.submit(finish)


@exact
def _trade_terms(order: Order, external: str, observed: TradeObservation) -> None:
    if (
        observed.order_id is not None
        and observed.order_id != external
        or observed.side is not None
        and observed.side != str(order.side)
        or observed.order_type not in (None, "limit")
        or order.side is Side.BUY
        and observed.price > order.price
        or order.side is Side.SELL
        and observed.price < order.price
        or observed.cost is not None
        and observed.cost != observed.amount * observed.price
    ):
        raise ExecutionError("recovery trade contradicts its owned limit order")


def _committed_observation(
    ledger: Ledger,
    scope: Scope,
    observed: TradeObservation,
    target: RecoveryTarget | None,
) -> bool:
    """Compare a returned identity by index, including retained terminal fills."""
    trade_id = trade_identity(scope, observed.trade_id)
    key = ledger.db.execute(
        "SELECT allocation,trade_id FROM trade_keys "
        "WHERE venue=? AND account=? AND symbol=? AND external_id=?",
        (scope.venue, scope.account, scope.symbol, observed.trade_id),
    ).fetchone()
    if key is None:
        if (
            ledger.db.execute(
                "SELECT 1 FROM fills WHERE allocation=? AND trade_id=?",
                (codec.digest(scope), trade_id),
            ).fetchone()
            is not None
        ):
            raise ExecutionError("recovery fill is missing its external identity")
        return False
    stored = ledger.db.execute(
        "SELECT order_id,payload FROM fills WHERE allocation=? AND trade_id=?",
        key,
    ).fetchone()
    if stored is None or key[1] != trade_id:
        raise ExecutionError("recovery trade identity is missing its committed fill")
    fill = codec.loads(stored[1], Fill, limit=ledger.bounds.snapshot_bytes)
    if (
        codec.digest(fill.scope) != key[0]
        or fill.trade_id != trade_id
        or stored[0] != fill.order_id
        or (fill.scope.venue, fill.scope.account, fill.scope.symbol)
        != (scope.venue, scope.account, scope.symbol)
        or target is not None
        and (fill.scope != scope or fill.order_id != target.order.id)
    ):
        raise ExecutionError("recovery trade belongs to another order or allocation")
    binding = ledger.db.execute(
        "SELECT * FROM order_bindings WHERE allocation=? AND order_id=?",
        (key[0], fill.order_id),
    ).fetchone()
    if binding is None or binding != (
        key[0],
        fill.order_id,
        scope.venue,
        scope.account,
        scope.symbol,
        binding[5],
        client_identity(fill.scope, fill.order_id),
    ):
        raise ExecutionError("committed recovery trade lacks its durable order binding")
    _trade_terms(ledger.order(fill.scope, fill.order_id), identifier(binding[5]), observed)
    _check_fee_projection(ledger, fill)
    if (
        observed.amount != fill.amount
        or observed.price != fill.price
        or observed.fees is not None
        and observed.fees != tuple((fee.currency, fee.amount) for fee in fill.fees or ())
    ):
        raise ExecutionError("recovery observation contradicts a committed fill")
    return True


@exact
def _compare(ledger: Ledger, plan: RecoveryPlan, batch: OrderHistoryBatch) -> RecoveryInspection:
    known = {
        target.external_id: target for target in plan.targets if target.external_id is not None
    }
    if tuple(history.order.order_id for history in batch.histories) != tuple(known):
        raise ExecutionError("recovery observations differ from the planned order roster")
    unique: dict[str, TradeObservation] = {}
    for observed in (*tuple(t for h in batch.histories for t in h.trades), *batch.unmatched_trades):
        if observed.trade_id in unique and unique[observed.trade_id] != observed:
            raise ExecutionError("recovery observations contain conflicting trade identities")
        unique[observed.trade_id] = observed
    new, committed = [], []
    for history in batch.histories:
        target = known[history.order.order_id]
        _check_terms(plan, target, history)
        added = ZERO
        for observed in history.trades:
            _trade_terms(target.order, history.order.order_id, observed)
            if _committed_observation(ledger, plan.scope, observed, target):
                committed.append(observed)
            else:
                new.append(observed)
                added += observed.amount
        if target.order.filled + added != history.order.filled:
            raise ReadError(Failure.INCOMPLETE)
    for observed in batch.unmatched_trades:
        if _committed_observation(ledger, plan.scope, observed, None):
            committed.append(observed)
    return RecoveryInspection(
        plan, batch.histories, batch.unmatched_trades, tuple(new), tuple(committed)
    )
