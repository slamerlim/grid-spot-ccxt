"""Pure read-only private response contracts, tested on synthetic fixtures only.

No authenticated transport is implemented here. Observations alone authorize no
financial posting. The mapped mock executor separately proves ownership, whole
snapshot completeness, fees and stream epochs before posting reconciled facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from grid.exchange.models import Failure, ReadError, mapping, money, optional_money, token


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
    order_id: str
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
                token(row.get("order")),
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
