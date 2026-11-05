from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from grid.domain.market import Limits
from grid.exchange.models import (
    Failure,
    ReadError,
    VerifiedRules,
    map_book,
    map_market,
    money,
    precision,
    strategy_market,
)
from grid.exchange.private import balances, orders, trades


@pytest.mark.parametrize("value", [0.1, True, "NaN", "Infinity", "1e999", "-1", "9" * 101])
def test_money_rejects_lossy_nonfinite_negative_and_unbounded(value: object) -> None:
    with pytest.raises(ReadError):
        money(value)


def test_precisions_preserve_sub_float_ticks_and_significant_boundaries() -> None:
    exact = money("0.12345678901234567890123456789")
    assert str(exact) == "0.12345678901234567890123456789"
    tick = precision("0.000000000000000000000000001", 4)
    sig = precision(3, 3)
    places = precision(-2, 2)
    assert tick is not None and tick.tick == Decimal("1e-27")
    assert sig is not None and sig.round(Decimal("9.999"), up=True) == Decimal("10.0")
    assert places is not None and places.tick == Decimal(100)
    with pytest.raises(ReadError):
        precision(".01", 99)


@pytest.mark.parametrize(
    "update",
    [
        {"spot": False},
        {"contract": True},
        {"type": "swap"},
        {"leveraged": True},
        {"settle": "USDT"},
        {"symbol": "BTC/USDC"},
        {"base": "BTC3L", "symbol": "BTC3L/USDT"},
        {"active": "true"},
        {"baseId": None},
    ],
)
def test_market_identity_and_eligibility_rejection(
    raw_market: dict[str, object], update: dict[str, object]
) -> None:
    with pytest.raises(ReadError):
        map_market(raw_market | update, "okx", 4, 1, 100)


def test_unknowns_need_separate_side_specific_verified_evidence(
    raw_market: dict[str, object],
) -> None:
    observed = map_market(raw_market, "okx", 4, 1, 100)
    assert observed.prices.minimum is None and observed.amounts.maximum is None
    assert observed.maker_estimate is None and not observed.product_verified
    with pytest.raises(ReadError, match=Failure.INCOMPLETE):
        strategy_market(observed, None, now_ns=100, max_age_ns=100)
    rules = VerifiedRules(
        "okx",
        "BTC-USDT",
        1,
        True,
        "synthetic-review",
        Limits(Decimal("0.01"), None),
        Limits(Decimal(".00001"), None),
        Limits(Decimal(5), None),
        Limits(Decimal(".00002"), None),
        Limits(Decimal(10), None),
    )
    mapped = strategy_market(observed, rules, now_ns=100, max_age_ns=100)
    assert mapped.buy_amount != mapped.sell_amount
    with pytest.raises(ReadError, match=Failure.INCOMPLETE):
        strategy_market(
            replace(observed, unmapped_constraints=("compound_precision",)),
            rules,
            now_ns=100,
            max_age_ns=100,
        )
    for bad in (
        replace(rules, generation=2),
        replace(rules, product_unleveraged=False),
        replace(rules, buy_cost=Limits(Decimal(0), None)),
    ):
        with pytest.raises(ReadError):
            strategy_market(observed, bad, now_ns=100, max_age_ns=100)
    with pytest.raises(ReadError, match=Failure.STALE):
        observed.require_fresh(50, 100)
    with pytest.raises(ReadError, match=Failure.STALE):
        observed.require_fresh(201, 100)


def test_public_fee_estimate_never_becomes_an_account_fee(raw_market: dict[str, object]) -> None:
    observed = map_market(raw_market | {"maker": 0.001, "taker": "0.002"}, "okx", 4, 1, 100)
    assert observed.maker_estimate is None
    assert observed.taker_estimate == Decimal(".002")
    with pytest.raises(ReadError):
        strategy_market(observed, None, now_ns=100, max_age_ns=100)


def test_htx_limit_sides_and_leveraged_product_tags(raw_market: dict[str, object]) -> None:
    raw = raw_market | {
        "info": {
            "limit-order-min-order-amt": "0.00002",
            "limit-order-max-buy-amt": "10",
            "limit-order-max-sell-amt": "5",
        }
    }
    observed = map_market(raw, "htx", 4, 1, 100)
    assert observed.buy_amount.minimum == Decimal(".00002")
    assert observed.buy_amount.maximum == Decimal(10)
    assert observed.sell_amount.maximum == Decimal(5)
    with pytest.raises(ReadError, match=Failure.POLICY):
        map_market(raw_market | {"info": {"tags": "etp,nav"}}, "htx", 4, 1, 100)


@pytest.mark.parametrize(
    "bids,asks",
    [
        ([], [["101", "1"]]),
        ([["102", "1"]], [["101", "1"]]),
        ([["100", "1"], ["100", "2"]], [["101", "1"]]),
        ([["99", "1"], ["100", "2"]], [["101", "1"]]),
        ([["100", "0"]], [["101", "1"]]),
        ([[100.0, "1"]], [["101", "1"]]),
    ],
)
def test_books_reject_unsafe_levels(
    raw_market: dict[str, object], bids: object, asks: object
) -> None:
    market = map_market(raw_market, "okx", 4, 1, 100)
    with pytest.raises(ReadError):
        map_book({"symbol": "BTC/USDT", "bids": bids, "asks": asks}, market, 20, 1000, 100)


def test_book_source_and_receipt_clocks_are_independent(raw_market: dict[str, object]) -> None:
    market = map_market(raw_market, "okx", 4, 1, 100)
    raw = {"symbol": "BTC/USDT", "bids": [["100", "1"]], "asks": [["101", "1"]], "timestamp": 1000}
    book = map_book(raw, market, 20, 1010, 100)
    book.require_fresh(110, 1020, 30)
    for now_ns, now_ms in ((110, 999), (31_000_101, 1020), (110, 1040)):
        with pytest.raises(ReadError, match=Failure.STALE):
            book.require_fresh(now_ns, now_ms, 30)
    local_only = map_book(raw, market, 20, 1010, 100, source_timestamp=False)
    assert local_only.source_ms is None
    with pytest.raises(ReadError, match=Failure.STALE):
        local_only.require_fresh(110, 1020, 30)


def test_private_snapshots_preserve_missing_balances_and_actual_fee_identity() -> None:
    result = balances({"BTC": {"free": "1.000000000000000000001"}}, ("BTC", "USDT"))
    assert result[0].free == Decimal("1.000000000000000000001")
    assert result[0].total is None and result[1].free is None
    trade = {
        "id": "trade-1",
        "order": "external-2",
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": "1",
        "price": "100",
        "fees": [{"currency": "TOKEN", "cost": "-.001"}],
    }
    parsed = trades([trade, trade], "BTC/USDT", 2)
    assert parsed[0] == parsed[1]
    assert parsed[0].fees == (("TOKEN", Decimal("-.001")),)
    assert trades([trade | {"fees": []}], "BTC/USDT", 1)[0].fees is None
    assert trades([trade | {"order": None}], "BTC/USDT", 1)[0].order_id is None
    # A closed order/status-only message has no financial effect.
    order = orders([{"id": "external-2", "symbol": "BTC/USDT", "status": "closed"}], "BTC/USDT", 1)[
        0
    ]
    assert order.filled is None and order.remaining is None
    for bad in (
        trade | {"symbol": "OTHER/USDT"},
        trade | {"fees": [{"currency": None, "cost": "0"}]},
    ):
        with pytest.raises(ReadError):
            trades([bad], "BTC/USDT", 1)
    with pytest.raises(ReadError):
        trades([trade, trade], "BTC/USDT", 1)
