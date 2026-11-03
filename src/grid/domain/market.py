"""Minimum BUY and one basket SELL math for explicitly normalized spot markets."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from grid.domain.numbers import (
    ONE,
    ZERO,
    DomainError,
    arithmetic,
    bounded_int,
    decimal,
    exact,
    identifier,
    ratio_units,
    units,
)


class PrecisionMode(StrEnum):
    TICK = "tick_size"
    SIGNIFICANT = "significant_digits"


@dataclass(frozen=True)
class Precision:
    mode: PrecisionMode
    tick: Decimal | None = None
    digits: int | None = None

    def __post_init__(self) -> None:
        if self.mode is PrecisionMode.TICK:
            if self.tick is None or self.digits is not None:
                raise DomainError("tick precision requires only a tick")
            decimal(self.tick, positive=True)
        elif self.mode is PrecisionMode.SIGNIFICANT:
            if self.digits is None or self.tick is not None:
                raise DomainError("significant precision requires only digits")
            bounded_int(self.digits, 1, 32)
        else:
            raise DomainError("unsupported precision mode")

    @arithmetic
    def quantum(self, value: Decimal) -> Decimal:
        decimal(value, positive=True)
        if self.tick is not None:
            return self.tick
        if self.digits is None:
            raise DomainError("missing precision")
        return ONE.scaleb(value.adjusted() - self.digits + 1)

    @arithmetic
    def round(self, value: Decimal, *, up: bool) -> Decimal:
        decimal(value)
        if value == ZERO:
            return ZERO
        return units(value, self.quantum(value), up=up)

    @arithmetic
    def previous(self, value: Decimal) -> Decimal:
        decimal(value, positive=True)
        quantum = self.quantum(value)
        if self.digits is not None and value == ONE.scaleb(value.adjusted()):
            quantum /= Decimal(10)
        return max(ZERO, value - quantum)

    @arithmetic
    def next(self, value: Decimal) -> Decimal:
        return self.round(value + self.quantum(value), up=True)


@dataclass(frozen=True)
class Limits:
    """Required verified minimum; maximum=None explicitly means known unbounded."""

    minimum: Decimal
    maximum: Decimal | None

    def __post_init__(self) -> None:
        decimal(self.minimum)
        if self.maximum is not None:
            decimal(self.maximum, positive=True)
            if self.maximum < self.minimum:
                raise DomainError("inverted limits")

    def admits(self, value: Decimal) -> bool:
        return value >= self.minimum and (self.maximum is None or value <= self.maximum)


@dataclass(frozen=True)
class Market:
    symbol: str
    base: str
    quote: str
    generation: int
    price: Precision
    amount: Precision
    prices: Limits
    buy_amount: Limits
    buy_cost: Limits
    sell_amount: Limits
    sell_cost: Limits
    spot: bool
    leveraged: bool

    def __post_init__(self) -> None:
        for value in (self.symbol, self.base, self.quote):
            identifier(value)
        bounded_int(self.generation, 0, 2**63 - 1)
        if self.base == self.quote or self.spot is not True or self.leveraged is not False:
            raise DomainError("only verified unleveraged spot markets are eligible")
        if self.symbol != f"{self.base}/{self.quote}":
            raise DomainError("spot symbol and currencies disagree")
        if not all(isinstance(rule, Precision) for rule in (self.price, self.amount)) or not all(
            isinstance(rule, Limits)
            for rule in (
                self.prices,
                self.buy_amount,
                self.buy_cost,
                self.sell_amount,
                self.sell_cost,
            )
        ):
            raise DomainError("explicit normalized precision and limits required")

    @exact
    def valid(self, price: Decimal, amount: Decimal, *, buy: bool) -> bool:
        decimal(price, positive=True)
        decimal(amount, positive=True)
        amount_limits, cost_limits = (
            (self.buy_amount, self.buy_cost) if buy else (self.sell_amount, self.sell_cost)
        )
        return (
            self.price.round(price, up=False) == price
            and self.amount.round(amount, up=False) == amount
            and self.prices.admits(price)
            and amount_limits.admits(amount)
            and cost_limits.admits(price * amount)
        )


@dataclass(frozen=True)
class Money:
    currency: str
    amount: Decimal

    def __post_init__(self) -> None:
        identifier(self.currency)
        decimal(self.amount, signed=True)


@dataclass(frozen=True)
class FeeRule:
    """Conservative nonnegative forecast; actual fills may include rebates.

    Third-token rates are fractions of quote notional, converted using an
    explicitly supplied quote/token value and rounded UP to a fee increment.
    The caller is responsible for supplying fresh, verified conversion facts.
    """

    currency: str
    rate: Decimal
    increment: Decimal
    quote_per_token: Decimal | None
    max_settlements: int

    def __post_init__(self) -> None:
        identifier(self.currency)
        decimal(self.rate)
        decimal(self.increment, positive=True)
        bounded_int(self.max_settlements, 1, 100000)
        if self.rate >= ONE:
            raise DomainError("fee forecast must be below one")
        if self.quote_per_token is not None:
            decimal(self.quote_per_token, positive=True)

    @exact
    def estimate(self, market: Market, price: Decimal, amount: Decimal) -> Money:
        decimal(price, positive=True)
        decimal(amount)
        conversion = ONE
        if self.currency == market.base:
            raw = amount * self.rate
        elif self.currency == market.quote:
            raw = amount * price * self.rate
        elif self.quote_per_token is not None:
            raw = amount * price * self.rate
            conversion = self.quote_per_token
        else:
            raise DomainError("missing fee-token conversion")
        # ceil(sum(raw_i)) + (n - 1) increments bounds sum(ceil(raw_i)).
        # The explicit settlement bound must be verified by the future adapter;
        # the reducer freezes new exposure if actual execution exceeds it.
        rounding_reserve = self.increment * (self.max_settlements - 1) if self.rate > ZERO else ZERO
        return Money(
            self.currency,
            ratio_units(raw, conversion, self.increment, up=True) + rounding_reserve,
        )

    @exact
    def quote_cost(self, market: Market, fee: Money, price: Decimal) -> Decimal:
        if fee.currency != self.currency:
            raise DomainError("fee currency mismatch")
        if fee.currency == market.quote:
            return fee.amount
        if fee.currency == market.base:
            return fee.amount * price
        if self.quote_per_token is None:
            raise DomainError("missing fee-token conversion")
        return fee.amount * self.quote_per_token


@dataclass(frozen=True)
class TakeProfit:
    price: Decimal
    amount: Decimal
    reserved_base: Decimal
    residual_base: Decimal
    expected_profit: Decimal
    fee: Money
    geometric_floor: Decimal
    unrounded_price_floor: Decimal
    market_generation: int


@dataclass(frozen=True)
class MinimumBuy:
    price: Decimal
    amount: Decimal
    fee: Money
    quote_required: Decimal
    net_base: Decimal
    acquisition_cost: Decimal
    prospective_sell: TakeProfit
    unrounded_amount_floor: Decimal
    market_generation: int


@arithmetic
def grid_prices(
    anchor: Decimal, spacing: Decimal, indices: tuple[int, ...], market: Market
) -> tuple[Decimal, ...]:
    decimal(anchor, positive=True)
    decimal(spacing, positive=True)
    if len(indices) > 256:
        raise DomainError("too many grid levels")
    previous = anchor
    result: list[Decimal] = []
    previous_index = 0
    for index in indices:
        bounded_int(index, 1, 10000)
        if index <= previous_index:
            raise DomainError("grid indices must be strictly increasing")
        price = market.price.round(anchor / (ONE + spacing) ** index, up=False)
        if price <= ZERO or price >= previous or not market.prices.admits(price):
            raise DomainError("collapsed or out-of-range grid level")
        result.append(price)
        previous, previous_index = price, index
    return tuple(result)


@arithmetic
def aggregate_take_profit(
    owned: Decimal,
    cost: Decimal,
    market: Market,
    spacing: Decimal,
    net_target: Decimal,
    fee_rule: FeeRule,
) -> TakeProfit:
    decimal(owned, positive=True)
    decimal(cost)
    decimal(spacing, positive=True)
    decimal(net_target, positive=True)

    @exact
    def base_required(candidate: Decimal) -> Decimal:
        return candidate + (
            fee_rule.estimate(market, ONE, candidate).amount
            if fee_rule.currency == market.base
            else ZERO
        )

    # A base fee is rounded at settlement; division by (1 + rate) alone is
    # insufficient. Find the largest precision-valid amount whose fee fits.
    amount = market.amount.round(owned, up=False)
    if fee_rule.currency == market.base and fee_rule.rate > ZERO:
        amount = market.amount.round(owned / (ONE + fee_rule.rate), up=False)
        if base_required(amount) > owned:
            lower, upper = ZERO, amount
            for _ in range(1024):
                midpoint = market.amount.round((lower + upper) / Decimal(2), up=False)
                if midpoint <= lower:
                    break
                if base_required(midpoint) <= owned:
                    lower = midpoint
                else:
                    upper = midpoint
            else:
                raise DomainError("base-fee quantity search bound exceeded")
            amount = lower
    if amount <= ZERO or not market.sell_amount.admits(amount):
        raise DomainError("inventory below SELL amount minimum or above maximum")
    geometric = cost / owned * (ONE + spacing)
    target = max(geometric, market.prices.minimum, market.sell_cost.minimum / amount)
    if fee_rule.currency != market.base:
        target = max(target, (cost + net_target) / (amount * (ONE - fee_rule.rate)))
    else:
        target = max(target, (cost + net_target) / amount)
    price = market.price.round(target, up=True)

    @exact
    def evaluate(candidate: Decimal) -> tuple[Decimal, Money]:
        estimated = fee_rule.estimate(market, candidate, amount)
        cost_of_fee = (
            ZERO
            if estimated.currency == market.base
            else fee_rule.quote_cost(market, estimated, candidate)
        )
        return amount * candidate - cost_of_fee - cost, estimated

    # Fee rounding makes net proceeds discontinuous, so ordinary binary
    # search of profit is unsafe. Each step below proves that every intervening
    # lower price fails, because the nonnegative fee cannot decrease as price
    # rises. The first passing price is therefore the least valid one.
    for _ in range(256):
        profit, fee = evaluate(price)
        if profit >= net_target:
            break
        if fee_rule.currency == market.base:
            raise DomainError("inconsistent base-fee target")
        required = (cost + net_target + fee_rule.quote_cost(market, fee, price)) / amount
        successor = market.price.round(required, up=True)
        price = successor if successor > price else market.price.next(price)
    else:
        raise DomainError("SELL price search bound exceeded")

    @exact
    def geometric_admitted() -> bool:
        return price * owned >= cost * (ONE + spacing)

    if (
        profit < net_target
        or not geometric_admitted()
        or not market.valid(price, amount, buy=False)
    ):
        raise DomainError("no valid profitable aggregate SELL")
    reserved = base_required(amount)
    if reserved > owned:
        raise DomainError("SELL fee exceeds owned base")
    return TakeProfit(
        price,
        amount,
        reserved,
        owned - reserved,
        profit,
        fee,
        geometric,
        target,
        market.generation,
    )


@arithmetic
def _buy_floor(price: Decimal, market: Market) -> Decimal:
    return max(market.buy_amount.minimum, market.buy_cost.minimum / price)


@exact
def minimum_buy(
    price: Decimal,
    market: Market,
    buy_fee: FeeRule,
    sell_fee: FeeRule,
    spacing: Decimal,
    net_target: Decimal,
    available_quote: Decimal,
) -> MinimumBuy:
    decimal(price, positive=True)
    decimal(available_quote)
    lower = _buy_floor(price, market)
    if lower == ZERO:
        if market.amount.tick is None:
            raise DomainError("no provable smallest BUY")
        lower = market.amount.tick
    amount = market.amount.round(lower, up=True)
    if amount * price < market.buy_cost.minimum:
        amount = market.amount.next(amount)
    if not market.valid(price, amount, buy=True):
        raise DomainError("minimum BUY violates market constraints")
    previous = market.amount.previous(amount)
    if previous > ZERO and market.valid(price, previous, buy=True):
        raise DomainError("minimum BUY could not be proven minimal")
    fee = buy_fee.estimate(market, price, amount)
    net_base = amount - (fee.amount if fee.currency == market.base else ZERO)
    quote_required = price * amount + (fee.amount if fee.currency == market.quote else ZERO)
    if quote_required > available_quote:
        raise DomainError("insufficient allocated quote")
    acquisition = price * amount
    if fee.currency != market.base:
        acquisition += buy_fee.quote_cost(market, fee, price)
    # This must reject the .999-base / 1-base SELL-minimum case, never enlarge
    # the independently determined smallest BUY to make it sellable.
    sell = aggregate_take_profit(net_base, acquisition, market, spacing, net_target, sell_fee)
    return MinimumBuy(
        price, amount, fee, quote_required, net_base, acquisition, sell, lower, market.generation
    )
