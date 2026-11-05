"""Real pinned CCXT parsers/signers with synthetic HTTP; zero Internet sockets."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from urllib.parse import urlencode

import pytest

from grid.config import VENUE_IDS
from grid.exchange.adapter import ReadLimits, ReadOnlyClient
from grid.exchange.ccxt_transport import (
    ENDPOINTS,
    PublicCCXT,
    WireReply,
    check_endpoint,
    decode_json,
)
from grid.exchange.models import Failure, ReadError


def payloads(venue: str) -> tuple[object, object]:
    # Deliberately unsorted, with digit widths that expose string-price sorting.
    bids = [["9.9", "3"], ["100.123456789012345678901", "1"], ["99.9", "4"]]
    asks = [["1000", "3"], ["101.1", "2"], ["102.2", "4"]]
    if venue == "okx":
        return (
            {
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT",
                        "instType": "SPOT",
                        "baseCcy": "BTC",
                        "quoteCcy": "USDT",
                        "tickSz": "0.01",
                        "lotSz": "0.000001",
                        "minSz": "0.00001",
                        "maxLmtSz": "100",
                        "state": "live",
                    }
                ],
            },
            {"code": "0", "data": [{"bids": bids, "asks": asks, "ts": "1000"}]},
        )
    if venue == "htx":
        return (
            {
                "status": "ok",
                "data": [
                    {
                        "symbol": "btcusdt",
                        "base-currency": "btc",
                        "quote-currency": "usdt",
                        "price-precision": 2,
                        "amount-precision": 6,
                        "state": "online",
                        "min-order-amt": "0.00001",
                        "limit-order-min-order-amt": "0.00001",
                        "min-order-value": "5",
                        "api-trading": "enabled",
                    }
                ],
            },
            {"status": "ok", "ts": 1000, "tick": {"bids": bids, "asks": asks, "ts": 1000}},
        )
    if venue == "gate":
        return (
            {
                "id": "BTC_USDT",
                "base": "BTC",
                "quote": "USDT",
                "fee": "0.1",
                "amount_precision": 6,
                "precision": 2,
                "trade_status": "tradable",
                "min_base_amount": "0.00001",
                "min_quote_amount": "5",
            },
            {"bids": bids, "asks": asks, "current": "1.000", "update": "1.000", "id": 1},
        )
    if venue == "xt":
        return (
            {
                "rc": 0,
                "mc": "SUCCESS",
                "result": {
                    "symbols": [
                        {
                            "symbol": "btc_usdt",
                            "baseCurrency": "btc",
                            "quoteCurrency": "usdt",
                            "pricePrecision": 2,
                            "quantityPrecision": 6,
                            "state": "ONLINE",
                            "tradingEnabled": True,
                            "openapiEnabled": True,
                            "filters": [
                                {"filter": "QUANTITY", "min": "0.00001", "tickSize": "0.000001"}
                            ],
                        }
                    ]
                },
            },
            {"rc": 0, "mc": "SUCCESS", "result": {"bids": bids, "asks": asks, "timestamp": 1000}},
        )
    if venue == "bingx":
        return (
            {
                "code": 0,
                "data": {
                    "symbols": [
                        {
                            "symbol": "BTC-USDT",
                            "tickSize": "0.01",
                            "stepSize": "0.000001",
                            "minNotional": "5",
                            "apiStateSell": True,
                            "apiStateBuy": True,
                            "status": 1,
                        }
                    ]
                },
            },
            {"code": 0, "data": {"bids": bids, "asks": asks, "ts": 1000}},
        )
    if venue == "kucoin":
        return (
            {
                "code": "200000",
                "data": [
                    {
                        "symbol": "BTC-USDT",
                        "name": "BTC-USDT",
                        "baseCurrency": "BTC",
                        "quoteCurrency": "USDT",
                        "baseMinSize": "0.00001",
                        "baseMaxSize": "100",
                        "quoteMinSize": "5",
                        "quoteMaxSize": "1000000",
                        "baseIncrement": "0.000001",
                        "quoteIncrement": "0.01",
                        "priceIncrement": "0.01",
                        "enableTrading": True,
                        "isMarginEnabled": False,
                    }
                ],
            },
            {"code": "200000", "data": {"bids": bids, "asks": asks, "time": 1000, "sequence": "1"}},
        )
    return (
        [[["BTCUST", [None, None, None, "0.00001", "100"]]]],
        [[price, 1, amount] for price, amount in bids]
        + [[price, 1, "-" + amount] for price, amount in asks],
    )


class FakeWire:
    def __init__(self, replies: list[WireReply | Exception]) -> None:
        self.replies = replies
        self.urls: list[str] = []
        self.closed = False

    async def get(self, url: str, limits: ReadLimits) -> WireReply:
        self.urls.append(url)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("venue", VENUE_IDS)
def test_pinned_sdk_public_parsers_signers_and_exact_book(venue: str, async_sockets: None) -> None:
    async def scenario() -> None:
        fixtures = payloads(venue)
        # Model numeric JSON too, not only APIs returning strings. The long
        # fractional literal cannot survive a default JSON float decoder.
        replies: list[WireReply | Exception] = [
            WireReply(
                200,
                json.dumps(row)
                .replace('"100.123456789012345678901"', "100.123456789012345678901")
                .encode(),
            )
            for row in fixtures
        ]
        wire = FakeWire(replies)
        transport = PublicCCXT(venue, ReadLimits(), wire=wire)
        client = ReadOnlyClient(venue, transport, ("BTC/USDT",), ReadLimits())
        try:
            markets = await client.load_markets()
            market = markets["BTC/USDT"]
            assert market.base == "BTC" and market.quote == "USDT"
            assert market.price is not None and market.amount is not None
            book = await client.fetch_order_book(
                "BTC/USDT", depth=25 if venue == "bitfinex" else 20
            )
            assert book.bids[0][0] == Decimal("100.123456789012345678901")
            assert book.asks[0][1] == Decimal(2)
            assert [p for p, _ in book.bids] == [
                Decimal("100.123456789012345678901"),
                Decimal("99.9"),
                Decimal("9.9"),
            ]
            assert [p for p, _ in book.asks] == [
                Decimal("101.1"),
                Decimal("102.2"),
                Decimal("1000"),
            ]
            if venue == "bitfinex":
                assert book.source_ms is None
                assert market.quote_id == "UST"  # raw/unified identity preserved
                assert market.amount.tick == Decimal("1e-8")
                value = Decimal("0.0000400099")
                # The venue overrides amount_to_precision to DECIMAL_PLACES,
                # even though its global precisionMode is SIGNIFICANT_DIGITS.
                wire_amount = Decimal(transport._client.amount_to_precision("BTC/USDT", str(value)))
                assert market.amount.round(value, up=False) == wire_amount
                assert market.unmapped_constraints
            assert len(wire.urls) == 2
            assert len(transport.evidence) == 2
            assert transport.catalog_rows == 1
            assert await client.load_markets() == markets
            assert len(wire.urls) == 2
            with pytest.raises(ReadError, match=Failure.BUDGET):
                await client.fetch_order_book("BTC/USDT", depth=25 if venue == "bitfinex" else 20)
        finally:
            await client.close()
        assert wire.closed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "url,method,headers,body",
    [
        ("https://www.okx.com/api/v5/trade/order", "GET", None, None),
        ("https://www.okx.com/api/v5/public/instruments?instType=SWAP", "GET", None, None),
        (
            "https://www.okx.com/api/v5/public/instruments?instType=SPOT&instId=BTC-USDT",
            "POST",
            None,
            None,
        ),
        (
            "https://www.okx.com/api/v5/public/instruments?instType=SPOT&instId=BTC-USDT",
            "GET",
            {"Authorization": "synthetic"},
            None,
        ),
        (
            "https://www.okx.com/api/v5/public/instruments?instType=SPOT&instId=BTC-USDT",
            "GET",
            None,
            "payload",
        ),
        ("https://www.okx.com.evil.invalid/api/v5/market/books", "GET", None, None),
        ("http://www.okx.com/api/v5/market/books", "GET", None, None),
        ("https://www.okx.com/api/v5/market/books?instId=BTC-USDT&sz=400", "GET", None, None),
        ("https://www.okx.com/api/v5/market/books?instId=BTC-USDT&sz=20&sz=20", "GET", None, None),
    ],
)
def test_wire_allowlist_refuses_writes_auth_wrong_hosts_and_unbounded_params(
    url: str, method: str, headers: object, body: object
) -> None:
    with pytest.raises(ReadError, match=Failure.POLICY):
        check_endpoint("okx", url, method, headers, body)


@pytest.mark.parametrize(
    "reply,expected",
    [
        (WireReply(429, b"sensitive raw response"), Failure.RATE_LIMIT),
        (WireReply(403, b"private raw message"), Failure.ACCESS),
        (WireReply(302, b"redirect"), Failure.UNAVAILABLE),
        (WireReply(500, b"server error"), Failure.UNAVAILABLE),
        (WireReply(200, b"not json"), Failure.MALFORMED),
        (WireReply(200, b"x" * 257), Failure.BUDGET),
        (TimeoutError("sensitive internal address"), Failure.TIMEOUT),
    ],
)
def test_failure_is_sanitized_no_retry_and_latches_transport(
    reply: WireReply | Exception, expected: Failure, async_sockets: None
) -> None:
    async def scenario() -> None:
        wire = FakeWire([reply])
        transport = PublicCCXT("okx", ReadLimits(response_bytes=256), wire=wire)
        try:
            with pytest.raises(ReadError) as captured:
                await transport.load_markets(False)
            assert str(captured.value) == expected.value
            with pytest.raises(ReadError, match=Failure.CLOSED):
                await transport.load_markets(True)
            assert len(wire.urls) == 1
            assert transport.evidence[0].outcome == expected.value
            assert "sensitive" not in repr(transport.evidence)
        finally:
            await transport.close()

    asyncio.run(scenario())


def test_json_decimal_literal_is_lossless_and_nonfinite_refused() -> None:
    assert decode_json(b'{"n":0.123456789012345678901}', 100)["n"] == "0.123456789012345678901"
    for raw in (b'{"n":NaN}', b'{"n":Infinity}', b'{"n":1,"n":2}'):
        with pytest.raises(ReadError):
            decode_json(raw, 100)


def test_all_profiles_have_only_two_exact_get_routes() -> None:
    for venue, endpoints in ENDPOINTS.items():
        assert len(endpoints) == 2
        for index, endpoint in enumerate(endpoints):
            url = f"https://{endpoint.host}{endpoint.path}?{urlencode(endpoint.query)}"
            if venue == "bingx":
                url += "&timestamp=1793744513650"
            assert check_endpoint(venue, url, "GET", None, None) == index


def test_failed_refresh_cannot_resurrect_cached_market_as_fresh(async_sockets: None) -> None:
    async def scenario() -> None:
        market, _ = payloads("okx")
        wire = FakeWire([WireReply(200, json.dumps(market).encode()), WireReply(429, b"limited")])
        transport = PublicCCXT("okx", ReadLimits(), wire=wire)
        client = ReadOnlyClient("okx", transport, ("BTC/USDT",), ReadLimits())
        try:
            await client.load_markets()
            with pytest.raises(ReadError, match=Failure.RATE_LIMIT):
                await client.load_markets(refresh=True)
            with pytest.raises(ReadError, match=Failure.CLOSED):
                await client.load_markets()
            assert len(wire.urls) == 2
            with pytest.raises(ReadError, match=Failure.POLICY):
                await client.fetch_order_book("BTC/USDT")
        finally:
            await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "raw_fee,normalized_fee",
    [("-0.00000001", "0.00000001"), ("0", "0"), ("0.00000001", "-0.00000001"), (None, None)],
)
def test_documented_numeric_mode_preserves_unified_order_and_trade_fees(
    raw_fee: str | None,
    normalized_fee: str | None,
    raw_market: dict[str, object],
    async_sockets: None,
) -> None:
    """Pure pinned OKX parsers over invented inputs; no account or order API."""

    async def scenario() -> None:
        wire = FakeWire([])
        transport = PublicCCXT("okx", ReadLimits(), wire=wire)
        try:
            client = transport._client
            client.set_markets([raw_market])
            market = client.market("BTC/USDT")
            raw = {
                "instId": "BTC-USDT",
                "instType": "SPOT",
                "tdMode": "cash",
                "ordId": "synthetic-order",
                "clOrdId": "synthetic-client",
                "tradeId": "synthetic-trade",
                "side": "buy",
                "ordType": "limit",
                "px": "123456789.12345677",
                "sz": "0.12345678",
                "fillPx": "123456789.12345677",
                "fillSz": "0.12345678",
                "accFillSz": "0.12345678",
                "avgPx": "123456789.12345677",
                "state": "filled",
                "fee": raw_fee,
                "feeCcy": "USDT",
                "cTime": "1700000000000",
                "uTime": "1700000000001",
                "ts": "1700000000001",
            }
            order = client.parse_order(raw, market)
            trade = client.parse_trade(raw, market)
            for parsed in (order, trade):
                assert parsed["symbol"] == "BTC/USDT"
                assert parsed["price"] == "123456789.12345677"
                assert parsed["amount"] == "0.12345678"
                assert Decimal(parsed["cost"]) == Decimal("15241577.6543209952934006")
                assert type(parsed["timestamp"]) is int
                if normalized_fee is None:
                    assert not parsed["fees"]
                    assert parsed["fee"] is None or parsed["fee"]["cost"] is None
                else:
                    assert parsed["fee"]["cost"] == normalized_fee
                    assert parsed["fee"]["currency"] == "USDT"
                    assert parsed["fees"] == [parsed["fee"]]
            assert order["type"] == "limit" and order["status"] == "closed"
            assert order["clientOrderId"] == "synthetic-client"
            assert trade["order"] == order["id"] == "synthetic-order"
            assert trade["type"] is None  # Optional unified fields remain unknown.
            assert not wire.urls and not transport.evidence
        finally:
            await transport.close()

    asyncio.run(scenario())
