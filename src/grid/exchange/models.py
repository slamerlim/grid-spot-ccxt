"""Exact, immutable observations. Public facts never grant trading authority."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from grid.domain.market import Limits, Market, Precision, PrecisionMode
from grid.domain.numbers import DomainError, decimal


class Failure(StrEnum):
    POLICY = "POLICY_REFUSED"
    BUDGET = "BUDGET_EXHAUSTED"
    BUSY = "BUSY"
    CLOSED = "CLOSED"
    AUTH = "AUTH_NOT_AUTHORIZED"
    ACCESS = "ACCESS_DENIED"
    UNSUPPORTED = "UNSUPPORTED"
    RATE_LIMIT = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    MALFORMED = "MALFORMED"
    STALE = "STALE"
    INCOMPLETE = "INCOMPLETE_EVIDENCE"
    INTERNAL = "INTERNAL_ERROR"


class ReadError(Exception):
    """Messages contain only fixed classification codes, never raw venue errors."""

    def __init__(self, failure: Failure) -> None:
        self.failure = failure
        super().__init__(failure.value)


def mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ReadError(Failure.MALFORMED)
    return value


def token(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value):
        raise ReadError(Failure.MALFORMED)
    return value


def money(value: object, *, signed: bool = False, positive: bool = False) -> Decimal:
    # In particular, Decimal(str(float)) would conceal prior loss of precision.
    if type(value) not in (str, int, Decimal) or len(str(value)) > 100:
        raise ReadError(Failure.MALFORMED)
    try:
        result = Decimal(str(value))
        decimal(result, signed=signed, positive=positive)
    except (InvalidOperation, DomainError, ValueError) as exc:
        raise ReadError(Failure.MALFORMED) from exc
    return result


def optional_money(value: object, *, signed: bool = False) -> Decimal | None:
    return None if value is None else money(value, signed=signed)


def integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ReadError(Failure.MALFORMED)
    return value


@dataclass(frozen=True)
class ObservedLimits:
    """None means UNKNOWN on either side, never unlimited or zero."""

    minimum: Decimal | None
    maximum: Decimal | None

    @classmethod
    def parse(cls, value: object) -> ObservedLimits:
        raw = mapping(value) if value is not None else {}
        minimum = optional_money(raw.get("min"))
        maximum = optional_money(raw.get("max"))
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ReadError(Failure.MALFORMED)
        return cls(minimum, maximum)


def precision(value: object, mode: int) -> Precision | None:
    if value is None:
        return None
    # CCXT constants: DECIMAL_PLACES=2, SIGNIFICANT_DIGITS=3, TICK_SIZE=4.
    if mode == 4:
        return Precision(PrecisionMode.TICK, tick=money(value, positive=True))
    if mode == 3:
        return Precision(PrecisionMode.SIGNIFICANT, digits=integer(value, 1, 32))
    if mode == 2:
        return Precision(PrecisionMode.TICK, tick=Decimal(1).scaleb(-integer(value, -32, 32)))
    raise ReadError(Failure.UNSUPPORTED)


@dataclass(frozen=True)
class MarketObservation:
    venue: str
    symbol: str
    raw_id: str
    base: str
    quote: str
    base_id: str
    quote_id: str
    generation: int
    received_ns: int
    active: bool | None
    price: Precision | None
    amount: Precision | None
    prices: ObservedLimits
    amounts: ObservedLimits
    costs: ObservedLimits
    buy_amount: ObservedLimits
    sell_amount: ObservedLimits
    maker_estimate: Decimal | None
    taker_estimate: Decimal | None
    unmapped_constraints: tuple[str, ...]
    # A spot listing may itself be a leveraged token. Naming is only a rejection
    # heuristic; absence of a suffix does NOT verify a product as unleveraged.
    product_verified: bool = False

    def require_fresh(self, now_ns: int, max_age_ns: int) -> None:
        if max_age_ns <= 0 or not 0 <= now_ns - self.received_ns <= max_age_ns:
            raise ReadError(Failure.STALE)


def map_market(
    raw: object, venue: str, mode: int, generation: int, received_ns: int
) -> MarketObservation:
    row = mapping(raw)
    if (
        row.get("spot") is not True
        or row.get("type") != "spot"
        or row.get("contract") is not False
        or any(row.get(k) is True for k in ("swap", "future", "option", "leveraged"))
        or row.get("settle") is not None
    ):
        raise ReadError(Failure.POLICY)
    base, quote, symbol = token(row.get("base")), token(row.get("quote")), token(row.get("symbol"))
    if symbol != f"{base}/{quote}" or base == quote:
        raise ReadError(Failure.MALFORMED)
    if re.search(r"(?:[235][LS]|BULL|BEAR|UP|DOWN)$", base):
        # Deliberately conservative; false positives require product review.
        raise ReadError(Failure.POLICY)
    active = row.get("active")
    if active is not None and type(active) is not bool:
        raise ReadError(Failure.MALFORMED)
    rules = mapping(row.get("limits") or {})
    precisions = mapping(row.get("precision") or {})
    amounts = ObservedLimits.parse(rules.get("amount"))
    buy_amount = sell_amount = amounts
    if venue == "htx":
        info = mapping(row.get("info") or {})
        if "etp" in str(info.get("tags", "")).lower() or info.get("underlying"):
            raise ReadError(Failure.POLICY)
        # CCXT's generic amount limits use the old non-side-specific fields.
        # Preserve explicitly supplied LIMIT buy/sell constraints independently.
        minimum = info.get("limit-order-min-order-amt", amounts.minimum)
        maximum = info.get("limit-order-max-order-amt", amounts.maximum)
        buy_amount = ObservedLimits.parse(
            {"min": minimum, "max": info.get("limit-order-max-buy-amt", maximum)}
        )
        sell_amount = ObservedLimits.parse(
            {"min": minimum, "max": info.get("limit-order-max-sell-amt", maximum)}
        )
    # Static library fee defaults may already be floats. Do not promote them to
    # exact/account fees; public decimal estimates are observations only as well.
    maker, taker = row.get("maker"), row.get("taker")
    return MarketObservation(
        token(venue),
        symbol,
        token(row.get("id")),
        base,
        quote,
        token(row.get("baseId")),
        token(row.get("quoteId")),
        integer(generation, 1, 2**63 - 1),
        integer(received_ns, 0, 2**63 - 1),
        active,
        precision(precisions.get("price"), mode),
        precision(precisions.get("amount"), 2 if venue == "bitfinex" else mode),
        ObservedLimits.parse(rules.get("price")),
        amounts,
        ObservedLimits.parse(rules.get("cost")),
        buy_amount,
        sell_amount,
        None if isinstance(maker, float) else optional_money(maker, signed=True),
        None if isinstance(taker, float) else optional_money(taker, signed=True),
        ("price_significant_digits_plus_8_decimal_places",)
        if venue == "bitfinex"
        else ("offset_filters_and_limit_price_protection",)
        if venue == "xt"
        else (),
    )


@dataclass(frozen=True)
class VerifiedRules:
    """Separate reviewed evidence required before conversion to the I1 contract.

    Supplying this object is not an authorization to trade. I3 has no source of
    actual account fee rules or verified side-specific market constraints.
    """

    venue: str
    raw_id: str
    generation: int
    product_unleveraged: bool
    evidence_id: str
    prices: Limits
    buy_amount: Limits
    buy_cost: Limits
    sell_amount: Limits
    sell_cost: Limits


def strategy_market(
    observed: MarketObservation,
    verified: VerifiedRules | None,
    *,
    now_ns: int,
    max_age_ns: int,
) -> Market:
    observed.require_fresh(now_ns, max_age_ns)
    if (
        verified is None
        or verified.product_unleveraged is not True
        or (verified.venue, verified.raw_id, verified.generation)
        != (observed.venue, observed.raw_id, observed.generation)
        or not verified.evidence_id
        or observed.active is not True
        or observed.price is None
        or observed.amount is None
        or observed.unmapped_constraints
    ):
        raise ReadError(Failure.INCOMPLETE)
    # Reviewed side-specific constraints cannot weaken observed generic bounds.
    for facts, rules in (
        (observed.prices, verified.prices),
        (observed.amounts, verified.buy_amount),
        (observed.amounts, verified.sell_amount),
        (observed.buy_amount, verified.buy_amount),
        (observed.sell_amount, verified.sell_amount),
        (observed.costs, verified.buy_cost),
        (observed.costs, verified.sell_cost),
    ):
        if facts.minimum is not None and rules.minimum < facts.minimum:
            raise ReadError(Failure.INCOMPLETE)
        if facts.maximum is not None and (rules.maximum is None or rules.maximum > facts.maximum):
            raise ReadError(Failure.INCOMPLETE)
    return Market(
        observed.symbol,
        observed.base,
        observed.quote,
        observed.generation,
        observed.price,
        observed.amount,
        verified.prices,
        verified.buy_amount,
        verified.buy_cost,
        verified.sell_amount,
        verified.sell_cost,
        True,
        False,
    )


@dataclass(frozen=True)
class BookObservation:
    symbol: str
    generation: int
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    source_ms: int | None
    received_ms: int
    received_ns: int

    def require_fresh(self, now_ns: int, now_ms: int, max_age_ms: int) -> None:
        if (
            max_age_ms <= 0
            or not 0 <= now_ns - self.received_ns <= max_age_ms * 1_000_000
            or self.source_ms is None
            or not 0 <= now_ms - self.source_ms <= max_age_ms
        ):
            raise ReadError(Failure.STALE)


def map_book(
    raw: object,
    market: MarketObservation,
    depth: int,
    received_ms: int,
    received_ns: int,
    *,
    source_timestamp: bool = True,
) -> BookObservation:
    row = mapping(raw)
    if row.get("symbol") != market.symbol:
        raise ReadError(Failure.MALFORMED)
    integer(depth, 1, 100)
    sides: list[tuple[tuple[Decimal, Decimal], ...]] = []
    for key in ("bids", "asks"):
        rows = row.get(key)
        if not isinstance(rows, list) or not 1 <= len(rows) <= depth:
            raise ReadError(Failure.MALFORMED)
        parsed: list[tuple[Decimal, Decimal]] = []
        for level in rows:
            if not isinstance(level, list) or not 2 <= len(level) <= 4:
                raise ReadError(Failure.MALFORMED)
            parsed.append((money(level[0], positive=True), money(level[1], positive=True)))
        prices = [p for p, _ in parsed]
        if len(set(prices)) != len(prices) or prices != sorted(prices, reverse=key == "bids"):
            raise ReadError(Failure.MALFORMED)
        sides.append(tuple(parsed))
    if sides[0][0][0] >= sides[1][0][0]:
        raise ReadError(Failure.MALFORMED)
    stamp = row.get("timestamp") if source_timestamp else None
    source_ms = None if stamp is None else integer(stamp, 1, 2**63 - 1)
    return BookObservation(
        market.symbol,
        market.generation,
        sides[0],
        sides[1],
        source_ms,
        integer(received_ms, 1, 2**63 - 1),
        integer(received_ns, 0, 2**63 - 1),
    )
