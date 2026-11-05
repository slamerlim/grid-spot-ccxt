"""Unified CCXT observations bound to owned intents, exercised by a finite fake."""

from dataclasses import replace
from decimal import Decimal
from typing import Literal

from grid.domain.market import Market
from grid.domain.model import ActualFee, Scope, Side
from grid.domain.numbers import ZERO, exact
from grid.exchange import private
from grid.exchange.models import ReadError
from grid.execution.models import ExecutionError, LimitRequest, OrderFact, Page, TradeFact
from grid.execution.synthetic import SyntheticExchange
from grid.persistence import client_ids, codec
from grid.persistence.ledger import Ledger
from grid.persistence.records import ClientIdentity, ClientIdPolicy
from grid.persistence.writer import AsyncWriter


@exact
def normalize(ledger: Ledger, scope: Scope, wire: object) -> OrderFact:
    """Read-only ownership proof; the executor validates the whole snapshot next."""
    try:
        observed = private.orders([wire], scope.symbol, 1)[0]
    except ReadError as exc:
        raise ExecutionError("invalid unified order") from exc
    if observed.client_id is not None:
        record = client_ids.resolve(ledger, scope.venue, scope.account, observed.client_id)
    else:
        # A missing optional client ID may use an already committed exact binding.
        # A supplied but unknown/changed client ID must never take this fallback.
        bound = ledger.db.execute(
            "SELECT order_id FROM order_bindings WHERE allocation=? "
            "AND venue=? AND account=? AND symbol=? AND external_id=?",
            (codec.digest(scope), scope.venue, scope.account, scope.symbol, observed.order_id),
        ).fetchone()
        record = None if bound is None else client_ids.lookup(ledger, scope, bound[0])
    if record is None or record.scope != scope or not ledger.attempted(scope, record.order_id):
        raise ExecutionError("unified order lacks a matching attempted identity")
    order = ledger.order(scope, record.order_id)
    for actual, expected in (
        (observed.order_type, "limit"),
        (observed.side, str(order.side)),
        (observed.price, order.price),
        (observed.amount, order.amount),
        (ledger.binding(scope, order.id), observed.order_id),
    ):
        if actual is not None and actual != expected:
            raise ExecutionError("unified order contradicts the reserved intent or binding")
    if observed.filled is None or (
        observed.remaining is not None and observed.remaining != order.amount - observed.filled
    ):
        raise ExecutionError("unified order cumulative is missing or inconsistent")
    status: Literal["open", "canceled", "rejected", "filled"]
    match observed.status:
        case "open":
            status = "open"
        case "closed":
            status = "filled"
        case "canceled" | "expired":
            status = "canceled"
        case "rejected":
            status = "rejected"
        case _:
            raise ExecutionError("unified order status is unresolved")
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
    return OrderFact(request, observed.order_id, status, observed.filled)


@exact
def normalize_trade(
    wire: object,
    owned: OrderFact,
    market: Market,
    sequence: int,
    valued_fees: tuple[ActualFee, ...] | None = None,
) -> TradeFact:
    """Sequence/settlement FX are explicit source evidence, not CCXT arrival order."""
    try:
        observed = private.trades([wire], owned.request.scope.symbol, 1)[0]
    except ReadError as exc:
        raise ExecutionError("invalid unified trade") from exc
    if (
        market.symbol != observed.symbol
        or observed.order_id != owned.external_id
        or observed.side is not None
        and observed.side != str(owned.request.side)
        or observed.order_type not in (None, "limit")
    ):
        raise ExecutionError("unified trade lacks its owned limit order")
    limit = codec.parse_amount(owned.request.price)
    if (
        owned.request.side is Side.BUY
        and observed.price > limit
        or owned.request.side is Side.SELL
        and observed.price < limit
        or observed.cost is not None
        and observed.cost != observed.amount * observed.price
    ):
        raise ExecutionError("unified trade contradicts the limit or exact settlement cost")
    fees = (
        None
        if observed.fees is None
        else tuple(ActualFee(currency, amount, None) for currency, amount in observed.fees)
    )
    if valued_fees is not None:
        if (
            type(valued_fees) is not tuple
            or not 1 <= len(valued_fees) <= 16
            or any(type(fee) is not ActualFee for fee in valued_fees)
            or tuple((fee.currency, fee.amount) for fee in valued_fees) != observed.fees
        ):
            raise ExecutionError("settlement valuation differs from the actual unified fees")
        fees = valued_fees
    if fees is not None:
        # A known zero charge has zero reporting cost without an FX observation.
        fees = tuple(
            replace(fee, quote_value=ZERO)
            if fee.currency not in (market.base, market.quote)
            and fee.amount == ZERO
            and fee.quote_value is None
            else fee
            for fee in fees
        )
    if fees is not None and any(
        fee.currency not in (market.base, market.quote) and fee.quote_value is None for fee in fees
    ):
        raise ExecutionError("third-currency fee settlement valuation is missing")
    return TradeFact(
        owned.request.scope,
        observed.order_id,
        observed.trade_id,
        sequence,
        owned.request.side,
        observed.amount,
        observed.price,
        fees,
    )


@exact
def order_payload(fact: OrderFact, returned_id: str) -> dict[str, object]:
    """Synthetic unified data, with no internal order or allocation identity."""
    return {
        "id": fact.external_id,
        "clientOrderId": returned_id,
        "symbol": fact.request.scope.symbol,
        "type": "limit",
        "side": str(fact.request.side),
        "price": fact.request.price,
        "amount": fact.request.amount,
        "status": "closed" if fact.status == "filled" else fact.status,
        "filled": codec.amount(fact.cumulative),
        "remaining": codec.amount(codec.parse_amount(fact.request.amount) - fact.cumulative),
    }


class MappedSyntheticExchange(SyntheticExchange):
    """A finite fake venue reporting only mapped client IDs; never a real gateway."""

    def __init__(self, capacity: int, writer: AsyncWriter, policy: ClientIdPolicy) -> None:
        super().__init__(capacity)
        if type(policy) is not ClientIdPolicy:
            raise ExecutionError("mock mapping policy required")
        self.writer, self.policy = writer, policy
        self.wire_orders: dict[str, dict[str, object]] = {}
        self.wire_trades: dict[str, dict[str, object]] = {}
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
        self.wire_orders[fact.external_id] = order_payload(fact, record.returned_id)
        return fact

    def settle_cancel(self, external_id: str) -> None:
        super().settle_cancel(external_id)
        status = self.orders[external_id].status
        self.wire_orders[external_id]["status"] = "closed" if status == "filled" else status

    @exact
    def fill(
        self,
        external_id: str,
        trade_id: str,
        amount: Decimal,
        price: Decimal,
        fees: tuple[ActualFee, ...] | None,
    ) -> TradeFact:
        trade = super().fill(external_id, trade_id, amount, price, fees)
        fact = self.orders[external_id]
        self.wire_orders[external_id].update(
            status="closed" if fact.status == "filled" else fact.status,
            filled=codec.amount(fact.cumulative),
            remaining=codec.amount(codec.parse_amount(fact.request.amount) - fact.cumulative),
        )
        self.wire_trades[trade_id] = {
            "id": trade_id,
            "order": external_id,
            "symbol": trade.scope.symbol,
            "type": None,
            "side": str(trade.side),
            "price": codec.amount(price),
            "amount": codec.amount(amount),
            "cost": codec.amount(price * amount),
            "fees": None
            if fees is None
            else [{"currency": fee.currency, "cost": codec.amount(fee.amount)} for fee in fees],
        }
        return trade

    async def page(self, scope: Scope, epoch: int, cursor: str | None, size: int) -> Page:
        page = await super().page(scope, epoch, cursor, size)

        def observations(ledger: Ledger) -> Page:
            orders = tuple(
                normalize(ledger, scope, self.wire_orders[f.external_id]) for f in page.orders
            )
            _, context, _, _ = ledger._load(codec.digest(scope))
            trades = []
            for fact in page.trades:
                owned = normalize(ledger, scope, self.wire_orders[fact.external_order])
                trade = normalize_trade(
                    self.wire_trades[fact.external_id],
                    owned,
                    context.market,
                    fact.sequence,
                    fact.fees,
                )
                if trade.external_id != fact.external_id:
                    raise ExecutionError("mock settlement context belongs to another trade")
                trades.append(trade)
            return replace(page, orders=orders, trades=tuple(trades))

        return await self.writer.submit(observations)
