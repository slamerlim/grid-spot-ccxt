"""HTX/Gate public rules: real pinned parsers, mocked HTTP, exact boundaries."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import ccxt  # type: ignore[import-untyped]
import pytest
from exchange.conftest import async_sockets  # noqa: F401
from exchange.test_ccxt import FakeWire

from grid.domain.market import FeeRule, Limits, Market, Precision, PrecisionMode, minimum_buy
from grid.domain.numbers import DomainError
from grid.exchange.adapter import ReadLimits
from grid.exchange.ccxt_transport import PublicCCXT, WireReply, check_endpoint
from grid.exchange.models import ReadError
from scripts.backtest_legacy_n import ArchiveCatalog
from scripts.backtest_minimum import market_rules, refresh_snapshot, snapshot_entry


def raw_market(venue: str, symbol: str) -> dict[str, Any]:
    base, quote = symbol.split("/")
    amount_places, price_places = (6 if base == "BTC" else 4), (6 if quote == "BTC" else 2)
    minimum = "0.00001" if base == "BTC" else "0.001"
    notional = "0.0001" if quote == "BTC" else "10" if quote == "USDC" else "1"
    if venue == "htx":
        return {
            "symbol": (base + quote).lower(),
            "base-currency": base.lower(),
            "quote-currency": quote.lower(),
            "state": "online",
            "api-trading": "enabled",
            "price-precision": price_places,
            "amount-precision": amount_places,
            "min-order-amt": minimum,
            "max-order-amt": "2000",
            "min-order-value": notional,
            "limit-order-min-order-amt": minimum,
            "limit-order-max-buy-amt": "1500",
            "limit-order-max-sell-amt": "1000",
            "buy-limit-must-less-than": "1.1",
            "sell-limit-must-greater-than": "0.9",
        }
    return {
        "id": base + "_" + quote,
        "base": base,
        "quote": quote,
        "trade_status": "tradable",
        "amount_precision": amount_places,
        "precision": price_places,
        "min_base_amount": minimum,
        "max_base_amount": "1000",
        "min_quote_amount": notional,
        "max_quote_amount": "100000",
        "fee": "0.2",
        "up_rate": "0.08" if base == "BTC" else "0.15",
        "down_rate": "0.08" if base == "BTC" else "0.15",
    }


def reply(venue: str) -> WireReply:
    rows = [raw_market(venue, symbol) for symbol in ("BTC/USDT", "ETH/BTC", "ETH/USDC")]
    return WireReply(
        200, json.dumps({"status": "ok", "data": rows} if venue == "htx" else rows).encode()
    )


@pytest.mark.parametrize("venue", ["htx", "gate"])
def test_refresh_persists_six_market_rules_and_old_versions(
    venue: str,
    tmp_path: Path,
    async_sockets: None,  # noqa: F811
) -> None:
    wire = FakeWire([reply(venue)])
    snapshot = asyncio.run(refresh_snapshot(venue, tmp_path, wire=wire))
    assert wire.closed and len(wire.urls) == 1
    assert snapshot["ccxt_version"] == ccxt.__version__ == "4.5.91"
    assert snapshot["historical_rules_verified"] is False
    assert len(snapshot["requests"]) == 1 and snapshot["adapter_source_hashes"]
    catalog = ArchiveCatalog(tmp_path, tmp_path)
    for symbol in ("BTC/USDT", "ETH/BTC", "ETH/USDC"):
        rules = market_rules(catalog, venue + ":" + symbol)
        assert rules["values"]["sell_amount_max"] == "1000"
        assert rules["values"]["buy_amount_max"] == ("1500" if venue == "htx" else "1000")
        assert rules["exchange_specific_fields"] and rules["source_fields"]
        if venue == "htx":
            assert rules["values"]["buy_price_max_ratio"] == "1.1"
            assert rules["values"]["sell_price_min_ratio"] == "0.9"
            assert "candle" in rules["price_reference_assumption"]
        else:
            assert rules["values"]["buy_cost_max"] == "100000"
            assert rules["values"]["limit_reference_bars"] == 2
            assert rules["values"]["buy_price_max_ratio"] == (
                "1.08" if symbol.startswith("BTC") else "1.15"
            )
            assert "two consecutive" in rules["price_reference_assumption"]
    old = (tmp_path / f"{venue}.json").read_bytes()
    asyncio.run(refresh_snapshot(venue, tmp_path, wire=FakeWire([reply(venue)])))
    assert old in [path.read_bytes() for path in (tmp_path / "versions").glob("*.json")]


def test_refresh_failure_keeps_last_snapshot_and_catalog_cannot_place_orders(
    tmp_path: Path,
    async_sockets: None,  # noqa: F811
) -> None:
    existing = tmp_path / "htx.json"
    existing.write_bytes(b"retained previous snapshot")
    wire = FakeWire([WireReply(503, b"unavailable")])
    with pytest.raises(ReadError):
        asyncio.run(refresh_snapshot("htx", tmp_path, wire=wire))
    assert existing.read_bytes() == b"retained previous snapshot" and wire.closed
    for method, url in (
        ("POST", "https://api.gateio.ws/api/v4/spot/orders"),
        ("GET", "https://api.gateio.ws/api/v4/spot/accounts"),
        ("GET", "https://api.gateio.ws/api/v4/spot/order_book"),
        ("GET", "https://api.gateio.ws/api/v4/spot/currency_pairs?anything=1"),
    ):
        with pytest.raises(ReadError):
            check_endpoint("gate", url, method, None, None, catalog=True)


@pytest.mark.parametrize("venue", ["htx", "gate"])
def test_rules_refuse_missing_or_contradictory_context(
    venue: str,
    tmp_path: Path,
    async_sockets: None,  # noqa: F811
) -> None:
    async def parsed() -> dict[str, Any]:
        client = PublicCCXT(
            venue, ReadLimits(requests=1), wire=FakeWire([reply(venue)]), catalog=True
        )
        try:
            markets = await client.load_markets(False)
            assert isinstance(markets, dict)
            return dict(markets["ETH/BTC"])
        finally:
            await client.close()

    row = asyncio.run(parsed())
    key = "max_base_amount" if venue == "gate" else "limit-order-max-sell-amt"
    row["info"][key] = "0.00001"
    assert "rules_error" in snapshot_entry(venue, row, 4)
    row = asyncio.run(parsed())
    del row["info"]["max_quote_amount" if venue == "gate" else "buy-limit-must-less-than"]
    assert "rules_error" in snapshot_entry(venue, row, 4)
    catalog = ArchiveCatalog(tmp_path, tmp_path)
    with pytest.raises(ValueError, match="unavailable"):
        market_rules(catalog, venue + ":ETH/BTC")


@pytest.mark.parametrize("venue", ["htx", "gate"])
@pytest.mark.parametrize(
    "symbol,price", [("BTC/USDT", "100000"), ("ETH/BTC", ".03"), ("ETH/USDC", "3000")]
)
def test_observed_constraints_minimality_rounding_and_funding(
    venue: str,
    symbol: str,
    price: str,
    tmp_path: Path,
    async_sockets: None,  # noqa: F811
) -> None:
    asyncio.run(refresh_snapshot(venue, tmp_path, wire=FakeWire([reply(venue)])))
    values = market_rules(ArchiveCatalog(tmp_path, tmp_path), venue + ":" + symbol)["values"]

    def limit(kind: str) -> Limits:
        return Limits(
            D(values[kind + "_min"]), D(values[kind + "_max"]) if values[kind + "_max"] else None
        )

    base, quote = symbol.split("/")
    market = Market(
        symbol,
        base,
        quote,
        1,
        Precision(PrecisionMode.TICK, tick=D(values["price_tick"])),
        Precision(PrecisionMode.TICK, tick=D(values["amount_tick"])),
        limit("price"),
        limit("buy_amount"),
        limit("buy_cost"),
        limit("sell_amount"),
        limit("sell_cost"),
        True,
        False,
    )
    fee = FeeRule(quote, D(".002"), D("1e-12"), None, 1)
    plan = minimum_buy(D(price), market, fee, fee, D(".01"), D(".00000001"), D(1000))
    client = getattr(ccxt, venue)()
    client.number = str
    client.markets = {
        symbol: {
            "symbol": symbol,
            "precision": {"price": values["price_tick"], "amount": values["amount_tick"]},
        }
    }
    assert D(client.amount_to_precision(symbol, str(plan.amount))) == plan.amount
    assert D(client.price_to_precision(symbol, price)) == D(price)
    assert market.valid(D(price), plan.amount, buy=True)
    assert (
        not market.valid(D(price), plan.amount - D(values["amount_tick"]), buy=True)
        if plan.amount > D(values["amount_tick"])
        else True
    )
    with pytest.raises(DomainError, match="insufficient allocated quote"):
        minimum_buy(
            D(price), market, fee, fee, D(".01"), D(".00000001"), plan.quote_required - D("1e-12")
        )
    assert not market.valid(D(price) + D(values["price_tick"]) / 2, plan.amount, buy=True)
    assert not market.valid(D(price), plan.amount + D(values["amount_tick"]) / 2, buy=False)
    assert not market.valid(D(price), D(1001), buy=False)


@pytest.mark.parametrize("venue", ["htx", "gate"])
@pytest.mark.parametrize("only", ["base", "notional"])
def test_single_minimum_and_explicit_no_limit(
    venue: str,
    only: str,
    tmp_path: Path,
    async_sockets: None,  # noqa: F811
) -> None:
    row = raw_market(venue, "ETH/USDC")
    if venue == "gate":
        row["min_quote_amount" if only == "base" else "min_base_amount"] = None
    elif only == "base":
        row["min-order-value"] = "0"
    else:
        row["min-order-amt"] = row["limit-order-min-order-amt"] = "0"
    payload = {"status": "ok", "data": [row]} if venue == "htx" else [row]
    asyncio.run(
        refresh_snapshot(
            venue, tmp_path, wire=FakeWire([WireReply(200, json.dumps(payload).encode())])
        )
    )
    values = market_rules(ArchiveCatalog(tmp_path, tmp_path), venue + ":ETH/USDC")["values"]
    assert values["buy_cost_min" if only == "base" else "buy_amount_min"] == "0"
    assert values["sell_cost_min" if only == "base" else "sell_amount_min"] == "0"
