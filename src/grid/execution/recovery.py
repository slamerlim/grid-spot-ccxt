"""Sole-writer recovery targets and bounded unified reads; no financial posting."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from grid.domain.model import Order, PlaceLimit, Scope
from grid.domain.numbers import ZERO, bounded_int, exact, identifier
from grid.exchange.private import OrderHistoryObservation, OrderReadTransport, read_order_history
from grid.execution.models import ExecutionError
from grid.persistence import client_ids, codec
from grid.persistence.ledger import Ledger, client_identity
from grid.persistence.records import ClientIdentity
from grid.persistence.writer import AsyncWriter


@dataclass(frozen=True)
class RecoveryTarget:
    order: Order
    external_id: str | None
    client: ClientIdentity | None


@dataclass(frozen=True)
class RecoveryPlan:
    scope: Scope
    version: int
    targets: tuple[RecoveryTarget, ...]


@dataclass(frozen=True)
class RecoveryInspection:
    plan: RecoveryPlan
    histories: tuple[OrderHistoryObservation, ...]

    @property
    def unresolved(self) -> tuple[RecoveryTarget, ...]:
        """Unknown external IDs remain obligations, never inferred rejections."""
        return tuple(target for target in self.plan.targets if target.external_id is None)


def recovery_plan(ledger: Ledger, scope: Scope, *, max_orders: int) -> RecoveryPlan:
    """Read current working orders and indexed identities in one writer callback."""
    bounded_int(max_orders, 1, 100)
    snapshot = ledger.snapshot(scope)
    if snapshot.state is None or snapshot.version is None:
        raise ExecutionError("recovery allocation missing")
    working = snapshot.state.working
    if len(working) > max_orders:
        raise ExecutionError("recovery target bound exceeded")
    allocation = codec.digest(scope)
    targets = []
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
        if row[5] is None:
            if row[4] != "queued" or order.unknown or order.acknowledged or external is not None:
                raise ExecutionError("unattempted recovery order has external uncertainty")
            continue  # Registration alone does not mean a submission occurred.
        if row[4] not in ("uncertain", "resolved"):
            raise ExecutionError("attempted recovery order has an invalid intent status")
        targets.append(RecoveryTarget(order, external, client))
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
) -> RecoveryInspection:
    """Observe known IDs, retain unknowns, then recheck the entire plan in SQLite.

    At most 100 orders / 10,000 trade observations. Limits count unified calls;
    the caller-owned transport enforces underlying HTTP, rate and source policy.
    This is a read-only inspection, never account completeness or send authority.
    """
    bounded_int(max_orders, 1, 100)
    bounded_int(max_trades, 1, 100)
    bounded_int(max_calls, 1, 300)
    bounded_int(timeout_ms, 1, 30_000)
    async with asyncio.timeout(timeout_ms / 1000):
        plan = await writer.submit(
            lambda ledger: recovery_plan(ledger, scope, max_orders=max_orders)
        )
        if 3 * sum(target.external_id is not None for target in plan.targets) > max_calls:
            raise ExecutionError("recovery unified-call budget is insufficient")
        histories = []
        for target in plan.targets:
            if target.external_id is None:
                continue
            history = await read_order_history(
                transport,
                target.external_id,
                scope.symbol,
                max_trades=max_trades,
                timeout_ms=timeout_ms,
            )
            _check_terms(plan, target, history)
            histories.append(history)
        current = await writer.submit(
            lambda ledger: recovery_plan(ledger, scope, max_orders=max_orders)
        )
        if current != plan:
            raise ExecutionError("recovery plan changed during observation")
        return RecoveryInspection(plan, tuple(histories))
