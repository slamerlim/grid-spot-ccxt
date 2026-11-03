"""Bounded synthetic gateway contracts; no arbitrary exchange params or floats."""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from grid.domain.model import ActualFee, Context, Order, Scope, Side
from grid.domain.numbers import DomainError, bounded_int, decimal, identifier
from grid.persistence import codec
from grid.persistence.ledger import client_identity


class ExecutionError(RuntimeError):
    """Supervision must retain uncertainty and stop admission."""


class RateLimited(ExecutionError):
    def __init__(self, retry_after_ms: int) -> None:
        bounded_int(retry_after_ms, 1, 86400000)
        self.retry_after_ms = retry_after_ms
        super().__init__("synthetic venue rate limit")


@dataclass(frozen=True)
class Limits:
    queue_size: int
    page_size: int
    max_pages: int
    max_records: int
    timeout_ms: int
    quiet_ms: int
    calls_per_window: int
    window_ms: int
    backoff_ms: int
    max_backoff_ms: int

    def __post_init__(self) -> None:
        for value, upper in (
            (self.queue_size, 10000),
            (self.page_size, 1000),
            (self.max_pages, 100),
            (self.max_records, 10000),
            (self.timeout_ms, 60000),
            (self.quiet_ms, 60000),
            (self.calls_per_window, 1000),
            (self.window_ms, 86400000),
            (self.backoff_ms, 60000),
            (self.max_backoff_ms, 86400000),
        ):
            bounded_int(value, 1, upper)
        if self.max_backoff_ms < self.backoff_ms:
            raise ExecutionError("backoff bounds inverted")


@dataclass(frozen=True)
class Evidence:
    verified_spot: bool
    verified_rules: bool
    verified_account_fees: bool


@dataclass(frozen=True)
class LimitRequest:
    scope: Scope
    order_id: int
    client_id: str
    side: Side
    amount: str
    price: str
    config_revision: int
    market_generation: int
    order_type: Literal["limit"] = field(default="limit", init=False)

    def __post_init__(self) -> None:
        bounded_int(self.order_id, 1, 2**63 - 1)
        if not isinstance(self.side, Side) or self.order_type != "limit":
            raise ExecutionError("only explicit limit sides are allowed")
        if self.client_id != client_identity(self.scope, self.order_id):
            raise ExecutionError("client identity mismatch")
        for value in (self.price, self.amount):
            decimal(codec.parse_amount(value), positive=True)
        for revision in (self.config_revision, self.market_generation):
            bounded_int(revision, 0, 2**63 - 1)


type Formatter = Callable[[Scope, Decimal, Decimal], tuple[str, str]]


def exact_format(scope: Scope, price: Decimal, amount: Decimal) -> tuple[str, str]:
    return codec.amount(price), codec.amount(amount)


def limit_request(
    scope: Scope,
    order: Order,
    context: Context,
    evidence: Evidence,
    formatter: Formatter = exact_format,
) -> LimitRequest:
    if any(
        value is not True
        for value in (
            evidence.verified_spot,
            evidence.verified_rules,
            evidence.verified_account_fees,
        )
    ):
        raise ExecutionError("verified spot/rule/account fee evidence required")
    if scope.venue in {"bitfinex", "xt"}:
        raise ExecutionError("compound precision or offset/protection rules remain unverified")
    if (
        scope.symbol != context.market.symbol
        or ":" in scope.symbol
        or order.config_revision != context.config_revision
        or order.market_generation != context.market.generation
        or context.book is None
        or context.book.market_generation != context.market.generation
        or not all(
            context.fresh(time)
            for time in (
                context.rules_observed_ms,
                context.fees_observed_ms,
                context.book.observed_ms,
            )
        )
    ):
        raise ExecutionError("stale or mismatched order, rules, fees or book")
    fee = context.buy_fee if order.side is Side.BUY else context.sell_fee
    if fee != order.fee_rule or not context.market.valid(
        order.price,
        order.amount,
        buy=order.side is Side.BUY,
    ):
        raise ExecutionError("intent no longer satisfies fee or market constraints")
    if order.side is Side.BUY and not context.allow_new_exposure:
        raise ExecutionError("new exposure is paused")
    price, amount = formatter(scope, order.price, order.amount)
    # CCXT may return padded strings. Compare exact values, then canonicalize.
    if (
        not isinstance(price, str)
        or not isinstance(amount, str)
        or len(price) > 200
        or len(amount) > 200
    ):
        raise ExecutionError("formatter must return bounded decimal strings")
    try:
        formatted_price, formatted_amount = Decimal(price), Decimal(amount)
        decimal(formatted_price, positive=True)
        decimal(formatted_amount, positive=True)
    except (ArithmeticError, DomainError) as exc:
        raise ExecutionError("invalid precision conversion") from exc
    if (formatted_price, formatted_amount) != (order.price, order.amount):
        raise ExecutionError("precision conversion changed the reserved order")
    return LimitRequest(
        scope,
        order.id,
        client_identity(scope, order.id),
        order.side,
        codec.amount(formatted_amount),
        codec.amount(formatted_price),
        order.config_revision,
        order.market_generation,
    )


@dataclass(frozen=True)
class OrderFact:
    request: LimitRequest
    external_id: str
    status: Literal["open", "canceled", "rejected", "filled"]
    cumulative: Decimal

    def __post_init__(self) -> None:
        identifier(self.external_id)
        decimal(self.cumulative)
        if self.status not in {"open", "canceled", "rejected", "filled"}:
            raise ExecutionError("unsupported status")
        amount = codec.parse_amount(self.request.amount)
        if (
            self.cumulative > amount
            or self.status == "filled"
            and self.cumulative != amount
            or self.status == "rejected"
            and self.cumulative != 0
        ):
            raise ExecutionError("inconsistent order cumulative")


@dataclass(frozen=True)
class TradeFact:
    scope: Scope
    external_order: str
    external_id: str
    sequence: int
    side: Side
    amount: Decimal
    price: Decimal
    fees: tuple[ActualFee, ...] | None

    def __post_init__(self) -> None:
        for value in (self.external_order, self.external_id):
            identifier(value)
        bounded_int(self.sequence, 1, 2**63 - 1)
        if not isinstance(self.side, Side):
            raise ExecutionError("invalid trade side")
        decimal(self.amount, positive=True)
        decimal(self.price, positive=True)
        if self.fees is not None and (
            not isinstance(self.fees, tuple)
            or not 1 <= len(self.fees) <= 16
            or any(type(fee) is not ActualFee for fee in self.fees)
        ):
            raise ExecutionError("explicit actual fees, including zero fees, required")


@dataclass(frozen=True)
class Page:
    epoch: int
    token: str
    orders: tuple[OrderFact, ...]
    trades: tuple[TradeFact, ...]
    next_cursor: str | None
    complete: bool


@dataclass(frozen=True)
class Notice:
    scope: Scope
    epoch: int
    kind: Literal["order", "trade", "heartbeat"]

    def __post_init__(self) -> None:
        bounded_int(self.epoch, 1, 2**63 - 1)
        if self.kind not in {"order", "trade", "heartbeat"}:
            raise ExecutionError("unsupported private event")
