"""Hand-calculated currency, chronology, missing-data and HTTP valuation checks."""

from __future__ import annotations

import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import backtest_usdt as usdt
from scripts.backtest_legacy_n import ArchiveCatalog
from scripts.module1_study import read_json
from scripts.module1_verify_sources import utc_ms

START = utc_ms("2025-01-01T00:00:00Z")
M = 60000


def candles(path: Path, values: list[tuple[int, float]]) -> None:
    path.write_bytes(b"".join(usdt.BAR.pack(t, p, p, p, p) for t, p in values))


@pytest.fixture
def research_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[tuple[str, Path, Path]], ArchiveCatalog]:
    root = tmp_path / "archive"
    root.mkdir()
    source = root / "venue_BTC_ETH.bin"
    candles(source, [(START, 2), (START + M, 3), (START + 2 * M, 4)])
    candles(
        root / "venue_USDT_BTC.bin",
        [(START - M, 100), (START, 200), (START + M, 300), (START + 2 * M, 400)],
    )
    folder = tmp_path / "current"
    folder.mkdir()
    raw = b"".join(
        [
            usdt.TRADE.pack(START + 2, 2, 1, 2, 0.004, 1, 2, 0, 0, 1),
            usdt.TRADE.pack(START + M + 1, 3, 1, 3, 0.006, 0, 0, 0.99, 1, 1),
        ]
    )
    (folder / "trades.bin").write_bytes(raw)
    summary = {
        "first_ts": START,
        "last_ts": START + 2 * M,
        "pnl": 0.99,
        "realized_pnl": 0.99,
        "unrealized_pnl": 0,
        "position_cost": 0,
        "fees": 0.01,
        "buys": 1,
        "sells": 1,
        "trades": 2,
        "chart_points": 1,
    }
    (folder / "summary.json").write_text(json.dumps(summary))
    (folder / "config.json").write_text(
        json.dumps(
            {
                "dataset": str(source),
                "dataset_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "order_quote": 2,
            }
        )
    )
    (folder / "receipt.json").write_text(
        json.dumps(
            {
                "hashes": {
                    "trades.bin": hashlib.sha256(raw).hexdigest(),
                    "series.bin": "equal-chart-hash",
                }
            }
        )
    )
    monkeypatch.setattr(usdt, "review", lambda directory: read_json(directory / "summary.json"))
    return [("venue:ETH/BTC", folder, folder)], ArchiveCatalog(root)


def test_historical_cost_pnl_is_distinct_from_revalued_native_profit(
    research_pair: tuple[list[tuple[str, Path, Path]], ArchiveCatalog],
) -> None:
    children, catalog = research_pair
    data = usdt.report(children, catalog, START + 3 * M, at_ms=START + M)
    value = data["pairs"][0]["at"]
    # BUY 1 ETH for 2 BTC + .004 BTC fees at 100 USDT/BTC; mark at 2 * 200.
    assert Decimal(value["position_cost_usdt"]) == Decimal("200.4")
    assert Decimal(value["position_value_usdt"]) == 400
    assert Decimal(value["pnl_usdt"]) == Decimal("199.6")
    assert Decimal(value["translated_native_pnl_usdt"]) == Decimal("-0.8")
    assert Decimal(value["fees_usdt"]) == Decimal("0.4")
    assert Decimal(data["pairs"][0]["first_order_usdt_at_start"]) == 200
    # SELL receives 2.994 BTC at 200 USDT/BTC, after the BUY's historical cost.
    sold = usdt.report(children, catalog, START + 3 * M, at_ms=START + 2 * M)
    end = sold["pairs"][0]["at"]
    assert Decimal(end["realized_pnl_usdt"]) == Decimal("398.4")
    assert Decimal(end["translated_native_pnl_usdt"]) == 297
    assert Decimal(end["fees_usdt"]) == Decimal("1.6")
    assert data["funded_nav_usdt"] is None


def test_missing_trade_time_fx_cannot_be_replaced_by_future_fx(
    research_pair: tuple[list[tuple[str, Path, Path]], ArchiveCatalog],
) -> None:
    children, catalog = research_pair
    candles(catalog.root / "venue_USDT_BTC.bin", [(START, 200), (START + M, 300)])
    data = usdt.report(children, catalog, START + 3 * M, at_ms=START + M)
    value = data["pairs"][0]["at"]
    assert value["position_value_usdt"] == "400"
    assert value["pnl_usdt"] is None and value["fees_usdt"] is None
    assert data["series"][0]["pnl_usdt"] is None
    assert data["series"][0]["missing_pairs"] == 1


def test_missing_conversion_minute_is_not_carried_forward_or_assumed_one(
    research_pair: tuple[list[tuple[str, Path, Path]], ArchiveCatalog],
) -> None:
    children, catalog = research_pair
    candles(catalog.root / "venue_USDT_BTC.bin", [(START - M, 100), (START + M, 300)])
    data = usdt.report(children, catalog, START + 3 * M, at_ms=START + M)
    value = data["pairs"][0]["at"]
    assert value["rate_usdt"] is None and value["position_value_usdt"] is None
    assert value["pnl_usdt"] is None
    assert Decimal(value["position_cost_usdt"]) == Decimal("200.4")


def test_incomplete_cross_currency_total_keeps_only_an_explicit_known_subtotal(
    research_pair: tuple[list[tuple[str, Path, Path]], ArchiveCatalog],
) -> None:
    children, catalog = research_pair
    native = catalog.root / "venue_USDT_OTHER.bin"
    candles(native, [(START, 2), (START + M, 3), (START + 2 * M, 4)])
    folder = children[0][1].with_name("usdt-child")
    shutil.copytree(children[0][1], folder)
    config = read_json(folder / "config.json")
    config.update(
        dataset=str(native), dataset_sha256=hashlib.sha256(native.read_bytes()).hexdigest()
    )
    (folder / "config.json").write_text(json.dumps(config))
    children.append(("venue:OTHER/USDT", folder, folder))
    candles(catalog.root / "venue_USDT_BTC.bin", [(START - M, 100)])
    data = usdt.report(children, ArchiveCatalog(catalog.root), START + 3 * M, at_ms=START + M)
    point = data["series"][0]
    assert point["pnl_usdt"] is None
    assert Decimal(point["known_subtotals"]["pnl_usdt"]) == Decimal("-0.004")
    assert point["known_pairs"] == point["missing_pairs"] == 1


def test_inverse_and_two_leg_conversion_require_same_venue_completed_closes(tmp_path: Path) -> None:
    candles(tmp_path / "venue_EUR_USDT.bin", [(START, 2)])
    candles(tmp_path / "venue_EUR_COIN.bin", [(START, 3)])
    # Use ETH as the permitted intermediate asset.
    candles(tmp_path / "venue_ETH_COIN.bin", [(START, 4)])
    candles(tmp_path / "venue_USDT_ETH.bin", [(START, 10)])
    candles(tmp_path / "other_USDT_UNKNOWN.bin", [(START, 999)])
    prices = usdt.Prices(ArchiveCatalog(tmp_path))
    try:
        assert prices.rate("venue", "EUR", START + M) == Decimal("0.5")
        assert prices.rate("venue", "COIN", START + M) == 40
        assert prices.rate("venue", "UNKNOWN", START + M) is None
        assert prices.rate("venue", "USDC", START + M) is None
        assert prices.rate("venue", "USDT", START + M) == 1
    finally:
        prices.close()


def test_open_position_is_unknown_after_native_history_ends(
    research_pair: tuple[list[tuple[str, Path, Path]], ArchiveCatalog],
) -> None:
    children, catalog = research_pair
    folder = children[0][1]
    # Retain only the initial BUY, leaving inventory open after the source ends.
    raw = (folder / "trades.bin").read_bytes()[: usdt.TRADE.size]
    (folder / "trades.bin").write_bytes(raw)
    receipt = read_json(folder / "receipt.json")
    receipt["hashes"]["trades.bin"] = hashlib.sha256(raw).hexdigest()
    (folder / "receipt.json").write_text(json.dumps(receipt))
    summary = read_json(folder / "summary.json")
    summary.update(trades=1, sells=0)
    (folder / "summary.json").write_text(json.dumps(summary))
    candles(
        catalog.root / "venue_USDT_BTC.bin", [(START - M, 100), (START, 200), (START + 3 * M, 500)]
    )
    data = usdt.report(children, catalog, START + 4 * M, at_ms=START + 4 * M)
    assert data["pairs"][0]["at"]["position_value_usdt"] is None
    assert data["series"][0]["pnl_usdt"] is None


def test_price_input_changes_invalidate_validation_and_invalid_fx_is_reported(
    research_pair: tuple[list[tuple[str, Path, Path]], ArchiveCatalog],
) -> None:
    children, catalog = research_pair
    usdt.report(children, catalog, START + 3 * M)
    candles(catalog.root / "venue_USDT_BTC.bin", [(START - M, 0), (START, 200)])
    data = usdt.report(children, catalog, START + 3 * M, at_ms=START + M)
    assert data["pairs"][0]["at"]["rate_usdt"] is None
    assert "venue_USDT_BTC.bin" in data["conversion_errors"]
    candles(catalog.root / "venue_BTC_ETH.bin", [(START, 99)])
    with pytest.raises(ValueError, match="hash differs"):
        usdt.report(children, catalog, START + 3 * M)


def test_usdt_base_inventory_and_cost_need_no_previous_fx_for_the_trade(
    research_pair: tuple[list[tuple[str, Path, Path]], ArchiveCatalog],
) -> None:
    children, catalog = research_pair
    source = catalog.root / "venue_BRL_USDT.bin"
    candles(source, [(START, 2), (START + M, 3), (START + 2 * M, 4)])
    folder = children[0][1]
    config = read_json(folder / "config.json")
    config.update(
        dataset=str(source), dataset_sha256=hashlib.sha256(source.read_bytes()).hexdigest()
    )
    (folder / "config.json").write_text(json.dumps(config))
    selected = [("venue:USDT/BRL", folder, folder)]
    data = usdt.report(selected, ArchiveCatalog(catalog.root), START + 3 * M, at_ms=START + M)
    value = data["pairs"][0]["at"]
    assert value["price_usdt"] == value["position_value_usdt"] == "1"
    assert Decimal(value["position_cost_usdt"]) == Decimal("1.002")
    assert Decimal(value["fees_usdt"]) == Decimal("0.002")
    assert Decimal(data["pairs"][0]["pnl_usdt"]) == Decimal("-0.004")
