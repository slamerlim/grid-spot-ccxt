"""Immutable normalized facts and proposed commands for a single allocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from grid.domain.market import FeeRule, Market, Money
from grid.domain.numbers import (
    ZERO,
    DomainError,
    arithmetic,
    bounded_int,
    decimal,
    exact,
    identifier,
)


@dataclass(frozen=True)
class Scope:
    venue: str
    account: str
    symbol: str
    allocation: str
    strategy: str
    cycle: str

    def __post_init__(self) -> None:
        for value in (
            self.venue,
            self.account,
            self.symbol,
            self.allocation,
            self.strategy,
            self.cycle,
        ):
            identifier(value)


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class Phase(StrEnum):
    INACTIVE = "inactive"
    SEED_PENDING = "seed_pending"
    ACCUMULATING = "accumulating"
    SELL_REPLACING = "sell_replacing"
    DRAINING = "draining"


class Mode(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRING = "retiring"
    PORTFOLIO_EXIT = "portfolio_exit"
    EXIT_BLOCKED = "exit_blocked"


@dataclass(frozen=True)
class Policy:
    spacing: Decimal
    net_target: Decimal
    quote_reserve: Decimal
    max_quote_committed: Decimal
    lower_price: Decimal
    upper_price: Decimal
    cost_quantum: Decimal
    max_spread: Decimal
    ladder_size: int
    max_lots: int
    max_open_orders: int
    max_order_records: int
    max_fill_records: int
    max_replacements: int
    max_source_age_ms: int
    seed_at: Literal["bid", "ask"]

    def __post_init__(self) -> None:
        for value in (
            self.spacing,
            self.net_target,
            self.max_quote_committed,
            self.lower_price,
            self.upper_price,
            self.cost_quantum,
        ):
            decimal(value, positive=True)
        decimal(self.quote_reserve)
        decimal(self.max_spread)
        if self.lower_price >= self.upper_price or self.seed_at not in ("bid", "ask"):
            raise DomainError("invalid range or seed policy")
        bounded_int(self.ladder_size, 1, 256)
        bounded_int(self.max_lots, 1, 10000)
        bounded_int(self.max_open_orders, 1, 1024)
        bounded_int(self.max_order_records, self.max_open_orders, 100000)
        bounded_int(self.max_fill_records, 1, 100000)
        bounded_int(self.max_replacements, 0, 10000)
        bounded_int(self.max_source_age_ms, 1, 2**53 - 1)


@dataclass(frozen=True)
class Book:
    bid: Decimal
    ask: Decimal
    observed_ms: int
    market_generation: int

    def __post_init__(self) -> None:
        decimal(self.bid, positive=True)
        decimal(self.ask, positive=True)
        if self.bid > self.ask:
            raise DomainError("crossed book")
        bounded_int(self.observed_ms, 0, 2**53 - 1)
        bounded_int(self.market_generation, 0, 2**63 - 1)


@dataclass(frozen=True)
class Context:
    market: Market
    policy: Policy
    buy_fee: FeeRule
    sell_fee: FeeRule
    config_revision: int
    now_ms: int
    rules_observed_ms: int
    fees_observed_ms: int
    book: Book | None
    allow_new_exposure: bool

    def __post_init__(self) -> None:
        bounded_int(self.config_revision, 0, 2**63 - 1)
        for value in (self.now_ms, self.rules_observed_ms, self.fees_observed_ms):
            bounded_int(value, 0, 2**53 - 1)
        if type(self.allow_new_exposure) is not bool:
            raise DomainError("explicit exposure permission required")

    def fresh(self, observed_ms: int) -> bool:
        return 0 <= self.now_ms - observed_ms <= self.policy.max_source_age_ms


@dataclass(frozen=True)
class ActualFee:
    currency: str
    amount: Decimal
    quote_value: Decimal | None

    def __post_init__(self) -> None:
        identifier(self.currency)
        decimal(self.amount, signed=True)
        if self.quote_value is not None:
            decimal(self.quote_value, signed=True)
            if (self.amount > ZERO) != (self.quote_value > ZERO) or (
                (self.amount < ZERO) != (self.quote_value < ZERO)
            ):
                raise DomainError("fee conversion sign mismatch")


@dataclass(frozen=True)
class Lot:
    order_id: int
    quantity: Decimal
    execution_price: Decimal

    def __post_init__(self) -> None:
        bounded_int(self.order_id, 1, 2**63 - 1)
        decimal(self.quantity, positive=True)
        decimal(self.execution_price, positive=True)


@dataclass(frozen=True)
class Order:
    id: int
    round_id: int
    side: Side
    role: Literal["seed", "ladder", "take_profit"]
    price: Decimal
    amount: Decimal
    config_revision: int
    market_generation: int
    reserves: tuple[Money, ...]
    principal_currency: str
    fee_rule: FeeRule
    acquisition_fee: Decimal = ZERO
    level: int | None = None
    filled: Decimal = ZERO
    gross_cost: Decimal = ZERO
    acknowledged: bool = False
    unknown: bool = False
    cancel_requested: bool = False
    terminal_amount: Decimal | None = None
    terminal_proven: bool = False

    def __post_init__(self) -> None:
        for value in (self.id, self.round_id):
            bounded_int(value, 1, 2**63 - 1)
        for value in (self.config_revision, self.market_generation):
            bounded_int(value, 0, 2**63 - 1)
        for number in (self.price, self.amount):
            decimal(number, positive=True)
        for number in (self.filled, self.gross_cost, self.acquisition_fee):
            decimal(number)
        identifier(self.principal_currency)
        if not isinstance(self.side, Side) or self.role not in ("seed", "ladder", "take_profit"):
            raise DomainError("invalid order side or role")
        if (self.side is Side.SELL) != (self.role == "take_profit"):
            raise DomainError("order side and role disagree")
        if (self.role == "ladder") != (self.level is not None):
            raise DomainError("only ladder orders have a level")
        if self.level is not None:
            bounded_int(self.level, 1, 10000)
        if self.filled > self.amount:
            raise DomainError("order overfilled")
        if self.terminal_amount is not None:
            decimal(self.terminal_amount)
            if not self.filled <= self.terminal_amount <= self.amount:
                raise DomainError("terminal amount contradicts fills")
        if self.terminal_proven and self.terminal_amount is None:
            raise DomainError("terminal proof requires cumulative amount")
        for value in (self.acknowledged, self.unknown, self.cancel_requested, self.terminal_proven):
            if type(value) is not bool:
                raise DomainError("explicit order flags required")
        if not isinstance(self.reserves, tuple) or len(self.reserves) > 3:
            raise DomainError("bounded immutable reserves required")
        if len({money.currency for money in self.reserves}) != len(self.reserves):
            raise DomainError("duplicate order reserve")
        if any(money.amount < ZERO for money in self.reserves):
            raise DomainError("negative order reserve")

    @property
    def terminal(self) -> bool:
        return self.filled == self.amount or (
            self.terminal_proven and self.terminal_amount == self.filled
        )

    @property
    @arithmetic
    def remaining(self) -> Decimal:
        return self.amount - self.filled


@dataclass(frozen=True)
class Start:
    scope: Scope


@dataclass(frozen=True)
class Fill:
    scope: Scope
    trade_id: str
    order_id: int
    amount: Decimal
    price: Decimal
    fees: tuple[ActualFee, ...] | None

    def __post_init__(self) -> None:
        identifier(self.trade_id)
        bounded_int(self.order_id, 1, 2**63 - 1)
        decimal(self.amount, positive=True)
        decimal(self.price, positive=True)
        if self.fees is not None and (not isinstance(self.fees, tuple) or len(self.fees) > 16):
            raise DomainError("bounded normalized fee tuple required")


@dataclass(frozen=True)
class OrderReport:
    scope: Scope
    order_id: int
    status: Literal["open", "canceled", "rejected", "filled"]
    cumulative: Decimal
    trades_reconciled: bool

    def __post_init__(self) -> None:
        bounded_int(self.order_id, 1, 2**63 - 1)
        decimal(self.cumulative)
        if self.status not in ("open", "canceled", "rejected", "filled"):
            raise DomainError("unsupported order status")
        if type(self.trades_reconciled) is not bool:
            raise DomainError("explicit reconciliation fact required")


@dataclass(frozen=True)
class SubmissionUnknown:
    scope: Scope
    order_id: int

    def __post_init__(self) -> None:
        bounded_int(self.order_id, 1, 2**63 - 1)


@dataclass(frozen=True)
class CancelAcknowledged:
    scope: Scope
    order_id: int

    def __post_init__(self) -> None:
        bounded_int(self.order_id, 1, 2**63 - 1)


@dataclass(frozen=True)
class Control:
    scope: Scope
    revision: int
    mode: Mode

    def __post_init__(self) -> None:
        bounded_int(self.revision, 1, 2**63 - 1)
        if not isinstance(self.mode, Mode):
            raise DomainError("unsupported control mode")


@dataclass(frozen=True)
class Reconciled:
    """Normalized assertion after external reconciliation, not an operator bypass."""

    scope: Scope
    config_revision: int
    market_generation: int
    expected_reason: str | None
    revision: int

    def __post_init__(self) -> None:
        bounded_int(self.config_revision, 0, 2**63 - 1)
        bounded_int(self.market_generation, 0, 2**63 - 1)
        bounded_int(self.revision, 0, 2**63 - 1)


type Event = (
    Start | Fill | OrderReport | SubmissionUnknown | CancelAcknowledged | Control | Reconciled
)


@dataclass(frozen=True)
class PlaceLimit:
    scope: Scope
    order: Order
    order_type: Literal["limit"] = field(default="limit", init=False)


@dataclass(frozen=True)
class Cancel:
    scope: Scope
    order_id: int


@dataclass(frozen=True)
class Reconcile:
    scope: Scope
    reason: str


type Command = PlaceLimit | Cancel | Reconcile


@dataclass(frozen=True)
class State:
    scope: Scope
    quote_cash: Decimal
    fee_balances: tuple[Money, ...]
    config_revision: int
    market_generation: int
    phase: Phase = Phase.INACTIVE
    mode: Mode = Mode.ACTIVE
    control_revision: int = 0
    round_id: int = 1
    next_id: int = 1
    next_level: int = 1
    ladder_credits: int = 0
    anchor: Decimal | None = None
    seed_id: int | None = None
    orders: tuple[Order, ...] = ()
    fills: tuple[Fill, ...] = ()
    lots: tuple[Lot, ...] = ()
    position_cost: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    reconcile_reason: str | None = None
    reconcile_revision: int = 0
    sell_dirty: bool = False
    replacements: int = 0

    def __post_init__(self) -> None:
        decimal(self.quote_cash)
        decimal(self.position_cost)
        decimal(self.realized_pnl, signed=True)
        for value in (
            self.config_revision,
            self.market_generation,
            self.control_revision,
            self.reconcile_revision,
        ):
            bounded_int(value, 0, 2**63 - 1)
        for value in (self.round_id, self.next_id, self.next_level):
            bounded_int(value, 1, 2**63 - 1)
        bounded_int(self.replacements, 0, 10000)
        bounded_int(self.ladder_credits, 0, 512)
        if not isinstance(self.phase, Phase) or not isinstance(self.mode, Mode):
            raise DomainError("invalid state phase or mode")
        for values in (self.fee_balances, self.orders, self.fills, self.lots):
            if not isinstance(values, tuple):
                raise DomainError("immutable state collections required")
        ids = {order.id for order in self.orders}
        if len(ids) != len(self.orders) or any(order.id >= self.next_id for order in self.orders):
            raise DomainError("order identity collision")
        if self.seed_id is not None and self.seed_id not in ids:
            raise DomainError("seed order missing")
        if any(lot.order_id not in ids for lot in self.lots):
            raise DomainError("lot provenance missing")
        if any(fill.scope != self.scope or fill.order_id not in ids for fill in self.fills):
            raise DomainError("fill scope or provenance mismatch")
        if len({fill.trade_id for fill in self.fills}) != len(self.fills):
            raise DomainError("duplicate posted fill")
        if len({money.currency for money in self.fee_balances}) != len(self.fee_balances):
            raise DomainError("duplicate fee-token balance")
        if any(money.amount < ZERO for money in self.fee_balances):
            raise DomainError("negative fee-token funds")
        if self.anchor is not None:
            decimal(self.anchor, positive=True)

    @property
    @exact
    def owned_base(self) -> Decimal:
        return sum((lot.quantity for lot in self.lots), ZERO)

    @property
    def working(self) -> tuple[Order, ...]:
        return tuple(order for order in self.orders if not order.terminal)


@dataclass(frozen=True)
class Transition:
    state: State
    commands: tuple[Command, ...] = ()
    notices: tuple[str, ...] = ()
    accepted: bool = True
