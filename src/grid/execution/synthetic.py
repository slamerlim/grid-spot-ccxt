"""Finite in-memory venue. This is the only gateway admitted by MockExecutor."""

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

from grid.domain.model import ActualFee, Scope
from grid.domain.numbers import ZERO, bounded_int, exact
from grid.execution.models import ExecutionError, LimitRequest, OrderFact, Page, TradeFact
from grid.persistence import codec


class SyntheticExchange:
    def __init__(
        self, capacity: int, *, record: Callable[[str, object], None] | None = None
    ) -> None:
        bounded_int(capacity, 1, 10000)
        self.capacity = capacity
        self.orders: dict[str, OrderFact] = {}
        self.trades: list[TradeFact] = []
        self.sent: list[LimitRequest] = []
        self.cancels: list[str] = []
        self.revision = 0
        self.reads = 0
        self.record = record

    def _record(self, kind: str, payload: object) -> None:
        if self.record is not None:
            self.record(kind, payload)

    async def submit_limit(self, request: LimitRequest) -> OrderFact:
        if type(request) is not LimitRequest or request.order_type != "limit":
            raise ExecutionError("only typed limit requests are allowed")
        if len(self.sent) >= self.capacity or any(
            fact.request.client_id == request.client_id for fact in self.orders.values()
        ):
            raise ExecutionError("synthetic capacity or duplicate submission")
        external_id = "order-" + str(len(self.sent) + 1)
        fact = OrderFact(request, external_id, "open", ZERO)
        self._record("submit", request)
        self.sent.append(request)
        self.orders[external_id] = fact
        self.revision += 1
        return fact

    async def cancel_limit(self, scope: Scope, external_id: str) -> None:
        if len(self.cancels) >= self.capacity:
            raise ExecutionError("synthetic cancel capacity")
        fact = self.orders.get(external_id)
        if fact is None or fact.request.scope != scope:
            raise ExecutionError("unknown cancellation outcome")
        self._record("cancel", external_id)
        self.cancels.append(external_id)
        # Ack alone changes no order status. Tests explicitly settle or fill it.

    def settle_cancel(self, external_id: str) -> None:
        fact = self.orders[external_id]
        if fact.status == "open":
            self._record("settle", external_id)
            self.orders[external_id] = replace(fact, status="canceled")
            self.revision += 1

    @exact
    def fill(
        self,
        external_id: str,
        trade_id: str,
        amount: Decimal,
        price: Decimal,
        fees: tuple[ActualFee, ...] | None,
    ) -> TradeFact:
        if len(self.trades) >= self.capacity:
            raise ExecutionError("synthetic trade capacity")
        fact = self.orders[external_id]
        cumulative = fact.cumulative + amount
        trade = TradeFact(
            fact.request.scope,
            external_id,
            trade_id,
            len(self.trades) + 1,
            fact.request.side,
            amount,
            price,
            fees,
        )
        if fact.status != "open":
            raise ExecutionError("synthetic order is terminal")
        if cumulative > codec.parse_amount(fact.request.amount) or any(
            existing.external_id == trade_id for existing in self.trades
        ):
            raise ExecutionError("synthetic overfill or duplicate trade")
        self._record("fill", trade)
        self.orders[external_id] = replace(
            fact,
            cumulative=cumulative,
            status="filled" if cumulative == codec.parse_amount(fact.request.amount) else "open",
        )
        self.trades.append(trade)
        self.revision += 1
        return trade

    async def page(self, scope: Scope, epoch: int, cursor: str | None, size: int) -> Page:
        self.reads += 1

        def same_account_market(other: Scope) -> bool:
            return (other.venue, other.account, other.symbol) == (
                scope.venue,
                scope.account,
                scope.symbol,
            )

        orders = tuple(
            fact for fact in self.orders.values() if same_account_market(fact.request.scope)
        )
        trades = tuple(fact for fact in self.trades if same_account_market(fact.scope))
        start = 0 if cursor is None else int(cursor)
        end = start + size
        total = len(orders) + len(trades)
        # Orders then trades share one bounded cursor; each page has <= size rows.
        selected_orders = orders[start:end]
        selected_trades = trades[max(0, start - len(orders)) : max(0, end - len(orders))]
        next_cursor = str(end) if end < total else None
        return Page(
            epoch,
            str(self.revision),
            selected_orders,
            selected_trades,
            next_cursor,
            next_cursor is None,
        )
