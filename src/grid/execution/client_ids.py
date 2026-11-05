"""Mock wire-ID adapter and strict lookup before normal reconciliation."""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from grid.domain.model import Scope, Side
from grid.domain.numbers import decimal
from grid.execution.models import ExecutionError, LimitRequest, OrderFact, Page
from grid.execution.synthetic import SyntheticExchange
from grid.persistence import client_ids, codec
from grid.persistence.ledger import Ledger
from grid.persistence.records import ClientIdentity, ClientIdPolicy
from grid.persistence.writer import AsyncWriter


@dataclass(frozen=True)
class WireOrder:
    """Invented venue report; it contains no internal order/allocation identity."""

    symbol: str
    client_id: str
    external_id: str
    order_type: str
    side: Side
    price: str
    amount: str
    status: Literal["open", "canceled", "rejected", "filled"]
    cumulative: Decimal


def normalize(ledger: Ledger, scope: Scope, wire: WireOrder) -> OrderFact:
    if (
        type(wire) is not WireOrder
        or wire.symbol != scope.symbol
        or wire.order_type != "limit"
        or type(wire.side) is not Side
    ):
        raise ExecutionError("wire order is not an owned spot limit")
    record = client_ids.resolve(ledger, scope.venue, scope.account, wire.client_id)
    if record is None or record.scope != scope or not ledger.attempted(scope, record.order_id):
        raise ExecutionError("wire client identity lacks a matching attempted order")
    order = ledger.order(scope, record.order_id)
    values = []
    for text in (wire.price, wire.amount):
        if type(text) is not str or not 1 <= len(text) <= 200:
            raise ExecutionError("wire price and amount require bounded decimal strings")
        try:
            values.append(decimal(Decimal(text), positive=True))
        except (ArithmeticError, ValueError) as exc:
            raise ExecutionError("invalid wire decimal") from exc
    if (wire.side, *values) != (order.side, order.price, order.amount):
        raise ExecutionError("wire order differs from the reserved intent")
    request = LimitRequest(
        scope,
        order.id,
        record.internal_id,
        order.side,
        codec.amount(order.amount),
        codec.amount(order.price),
        order.config_revision,
        order.market_generation,
    )
    return OrderFact(request, wire.external_id, wire.status, wire.cumulative)


class MappedSyntheticExchange(SyntheticExchange):
    """A finite fake venue reporting only mapped client IDs; never a real gateway."""

    def __init__(self, capacity: int, writer: AsyncWriter, policy: ClientIdPolicy) -> None:
        super().__init__(capacity)
        if type(policy) is not ClientIdPolicy:
            raise ExecutionError("mock mapping policy required")
        self.writer, self.policy = writer, policy
        self.wire_orders: dict[str, WireOrder] = {}
        self.submitted_clients: dict[str, str] = {}

    async def submit_limit(self, request: LimitRequest) -> OrderFact:
        def registered(ledger: Ledger) -> ClientIdentity:
            record = client_ids.lookup(ledger, request.scope, request.order_id)
            if (
                record is None
                or record.policy != self.policy
                or record.internal_id != request.client_id
                or not ledger.attempted(request.scope, request.order_id)
            ):
                raise ExecutionError("mock wire submission lacks committed identity and attempt")
            return record

        record = await self.writer.submit(registered)
        fact = await super().submit_limit(request)
        self.submitted_clients[fact.external_id] = record.submitted_id
        self.wire_orders[fact.external_id] = WireOrder(
            request.scope.symbol,
            record.returned_id,
            fact.external_id,
            request.order_type,
            request.side,
            request.price,
            request.amount,
            fact.status,
            fact.cumulative,
        )
        return fact

    async def page(self, scope: Scope, epoch: int, cursor: str | None, size: int) -> Page:
        page = await super().page(scope, epoch, cursor, size)
        wire = tuple(
            replace(
                self.wire_orders[fact.external_id], status=fact.status, cumulative=fact.cumulative
            )
            for fact in page.orders
        )
        orders = await self.writer.submit(
            lambda ledger: tuple(normalize(ledger, scope, w) for w in wire)
        )
        return replace(page, orders=orders)
