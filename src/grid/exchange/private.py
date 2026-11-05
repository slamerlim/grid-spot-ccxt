"""Pure read-only private response contracts, tested on synthetic fixtures only.

No authenticated transport is implemented here. Observations alone authorize no
financial posting. The mapped mock executor separately proves ownership, whole
snapshot completeness, fees and stream epochs before posting reconciled facts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from grid.domain.numbers import ZERO, DomainError, exact
from grid.exchange.models import Failure, ReadError, integer, mapping, money, optional_money, token


@dataclass(frozen=True)
class BalanceObservation:
    currency: str
    free: Decimal | None
    used: Decimal | None
    total: Decimal | None


def balances(raw: object, currencies: tuple[str, ...]) -> tuple[BalanceObservation, ...]:
    if not 1 <= len(currencies) <= 32 or len(set(currencies)) != len(currencies):
        raise ReadError(Failure.BUDGET)
    response = mapping(raw)
    result = []
    for currency in currencies:
        row = mapping(response.get(token(currency), {}))
        free, used, total = (optional_money(row.get(k)) for k in ("free", "used", "total"))
        # Do not invent missing components from CCXT convenience aggregates.
        result.append(BalanceObservation(currency, free, used, total))
    return tuple(result)


@dataclass(frozen=True)
class TradeObservation:
    trade_id: str
    order_id: str | None
    symbol: str
    side: str | None
    amount: Decimal
    price: Decimal
    fees: tuple[tuple[str, Decimal], ...] | None
    cost: Decimal | None = None
    order_type: str | None = None


def trades(raw: object, symbol: str, limit: int) -> tuple[TradeObservation, ...]:
    if not isinstance(raw, list) or not 1 <= limit <= 100 or len(raw) > limit:
        raise ReadError(Failure.BUDGET)
    result = []
    for item in raw:
        row = mapping(item)
        if row.get("symbol") != symbol or row.get("side") not in (None, "buy", "sell"):
            raise ReadError(Failure.MALFORMED)
        fee_rows = row.get("fees")
        if fee_rows is None and row.get("fee") is not None:
            fee_rows = [row["fee"]]
        fees = None
        if fee_rows is not None:
            if not isinstance(fee_rows, list) or len(fee_rows) > 16:
                raise ReadError(Failure.MALFORMED)
            parsed = []
            for entry in fee_rows:
                fee = mapping(entry)
                parsed.append((token(fee.get("currency")), money(fee.get("cost"), signed=True)))
            # Empty list isn't evidence that the venue settled zero fees.
            fees = tuple(parsed) if parsed else None
        result.append(
            TradeObservation(
                token(row.get("id")),
                None if row.get("order") is None else token(row["order"]),
                token(symbol),
                None if row.get("side") is None else token(row["side"]),
                money(row.get("amount"), positive=True),
                money(row.get("price"), positive=True),
                fees,
                optional_money(row.get("cost")),
                None if row.get("type") is None else token(row["type"]),
            )
        )
    # Keep duplicates for I4 scoped reconciliation; do not silently lose conflicts.
    return tuple(result)


@dataclass(frozen=True)
class OrderObservation:
    order_id: str
    symbol: str
    status: str | None
    filled: Decimal | None
    remaining: Decimal | None
    client_id: str | None = None
    order_type: str | None = None
    side: str | None = None
    amount: Decimal | None = None
    price: Decimal | None = None


def orders(raw: object, symbol: str, limit: int) -> tuple[OrderObservation, ...]:
    if not isinstance(raw, list) or not 1 <= limit <= 100 or len(raw) > limit:
        raise ReadError(Failure.BUDGET)
    result = []
    for item in raw:
        row = mapping(item)
        status = row.get("status")
        if row.get("symbol") != symbol or status not in (
            None,
            "open",
            "closed",
            "canceled",
            "expired",
            "rejected",
        ):
            raise ReadError(Failure.MALFORMED)
        result.append(
            OrderObservation(
                token(row.get("id")),
                token(symbol),
                status,
                optional_money(row.get("filled")),
                optional_money(row.get("remaining")),
                None if row.get("clientOrderId") is None else token(row["clientOrderId"]),
                None if row.get("type") is None else token(row["type"]),
                None if row.get("side") is None else token(row["side"]),
                optional_money(row.get("amount")),
                optional_money(row.get("price")),
            )
        )
    return tuple(result)


class OrderReadTransport(Protocol):
    """Caller-owned unified reads; no client construction, credentials or writes."""

    async def fetch_order(self, id: str, symbol: str) -> object: ...
    async def fetch_order_trades(self, id: str, symbol: str, since: None, limit: int) -> object: ...


class AccountReadTransport(Protocol):
    """Unified fallback for venues without a per-order trade method."""

    async def fetch_order(self, id: str, symbol: str) -> object: ...
    async def fetch_my_trades(self, symbol: str, since: None, limit: int) -> object: ...


@dataclass(frozen=True)
class OrderHistoryObservation:
    """Quantity-matched observations, not ownership, fee or chronology authority."""

    order: OrderObservation
    trades: tuple[TradeObservation, ...]


@dataclass(frozen=True)
class OrderHistoryBatch:
    histories: tuple[OrderHistoryObservation, ...]
    unmatched_trades: tuple[TradeObservation, ...] = ()


def _unique_trades(rows: tuple[TradeObservation, ...]) -> tuple[TradeObservation, ...]:
    unique: dict[str, TradeObservation] = {}
    for trade in rows:
        if trade.trade_id in unique and unique[trade.trade_id] != trade:
            raise ReadError(Failure.MALFORMED)
        unique[trade.trade_id] = trade
    return tuple(unique.values())


@exact
def _order_history(
    before: OrderObservation, after: OrderObservation, rows: tuple[TradeObservation, ...]
) -> OrderHistoryObservation:
    if (
        before.order_id != after.order_id
        or before.symbol != after.symbol
        or before.status is None
        or before.filled is None
        or (before.status, before.filled) != (after.status, after.filled)
    ):
        raise ReadError(Failure.INCOMPLETE)
    # Optional terms may be populated on one read only; only conflicting known
    # values invalidate stability. The final observation retains its unknowns.
    for name in ("client_id", "order_type", "side", "amount", "price", "remaining"):
        first, last = getattr(before, name), getattr(after, name)
        if first is not None and last is not None and first != last:
            raise ReadError(Failure.INCOMPLETE)
    unique = _unique_trades(rows)
    for trade in unique:
        if trade.order_id != after.order_id or (
            trade.side is not None and after.side is not None and trade.side != after.side
        ):
            raise ReadError(Failure.MALFORMED)
    if sum((trade.amount for trade in unique), ZERO) != after.filled:
        raise ReadError(Failure.INCOMPLETE)
    return OrderHistoryObservation(after, unique)


async def read_order_history(
    transport: OrderReadTransport,
    order_id: str,
    symbol: str,
    *,
    max_trades: int,
    timeout_ms: int,
) -> OrderHistoryObservation:
    """At most three unified calls, one bounded order-trade query, no retries.

    Read status/cumulative on both sides of the trade query. Equality with the
    deduplicated positive trade quantities proves the reported quantity only.
    This is not account-wide recovery, an execution sequence or send permission.
    The caller supplies a transport with its own byte/rate/capability enforcement
    and owns its lifetime. Tests supply only offline fakes or guarded SDK clients.
    """
    token(order_id)
    token(symbol)
    integer(max_trades, 1, 100)
    integer(timeout_ms, 1, 30_000)
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            before = orders([await transport.fetch_order(order_id, symbol)], symbol, 1)[0]
            if before.order_id != order_id or before.status is None or before.filled is None:
                raise ReadError(Failure.INCOMPLETE)
            rows = trades(
                await transport.fetch_order_trades(order_id, symbol, None, max_trades),
                symbol,
                max_trades,
            )
            after = orders([await transport.fetch_order(order_id, symbol)], symbol, 1)[0]
            if after.order_id != order_id:
                raise ReadError(Failure.INCOMPLETE)
            return _order_history(before, after, rows)
    except ReadError:
        raise
    except TimeoutError:
        raise ReadError(Failure.TIMEOUT) from None
    except DomainError:
        raise ReadError(Failure.MALFORMED) from None
    except Exception:
        raise ReadError(Failure.UNAVAILABLE) from None


async def read_account_history(
    transport: AccountReadTransport,
    order_ids: tuple[str, ...],
    symbol: str,
    *,
    max_trades: int,
    timeout_ms: int,
) -> OrderHistoryBatch:
    """Bracket one symbol-scoped personal-trade query with N order reads each side.

    At most 2*N+1 unified calls, with 1–100 distinct orders and 1–100 trade rows
    total. Preserve unmatched trades for later ownership checks. This is neither
    account completeness nor an atomic snapshot. No automatic fallback or retry.
    """
    token(symbol)
    integer(max_trades, 1, 100)
    integer(timeout_ms, 1, 30_000)
    if not isinstance(order_ids, tuple) or not 1 <= len(order_ids) <= 100:
        raise ReadError(Failure.BUDGET)
    for identity in order_ids:
        token(identity)
    if len(set(order_ids)) != len(order_ids):
        raise ReadError(Failure.MALFORMED)
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            before = []
            for identity in order_ids:
                observed = orders([await transport.fetch_order(identity, symbol)], symbol, 1)[0]
                if (
                    observed.order_id != identity
                    or observed.status is None
                    or observed.filled is None
                ):
                    raise ReadError(Failure.INCOMPLETE)
                before.append(observed)
            rows = _unique_trades(
                trades(
                    await transport.fetch_my_trades(symbol, None, max_trades), symbol, max_trades
                )
            )
            grouped: dict[str, list[TradeObservation]] = {identity: [] for identity in order_ids}
            unmatched = []
            for trade in rows:
                if trade.order_id is not None and trade.order_id in grouped:
                    grouped[trade.order_id].append(trade)
                else:
                    unmatched.append(trade)
            histories = []
            for identity, first in zip(order_ids, before, strict=True):
                after = orders([await transport.fetch_order(identity, symbol)], symbol, 1)[0]
                histories.append(_order_history(first, after, tuple(grouped[identity])))
            return OrderHistoryBatch(tuple(histories), tuple(unmatched))
    except ReadError:
        raise
    except TimeoutError:
        raise ReadError(Failure.TIMEOUT) from None
    except DomainError:
        raise ReadError(Failure.MALFORMED) from None
    except Exception:
        raise ReadError(Failure.UNAVAILABLE) from None
