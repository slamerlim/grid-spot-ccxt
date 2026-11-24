"""Independent arithmetic checks for calendar equity, completed positions and Sharpe."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import pytest

from scripts import backtest_performance as metrics
from scripts import backtest_usdt as usdt
from scripts.backtest_legacy_n import ArchiveCatalog
from scripts.module1_verify_sources import utc_ms

D = Decimal
DAY = metrics.DAY
START = utc_ms("2025-01-30T00:00:00Z")


def test_daily_sharpe_uses_sample_variance_and_365_days() -> None:
    with localcontext() as context:
        context.prec = 96
        nav = {i * DAY: D(v) for i, v in enumerate((100, 110, 121, 121))}
        row = metrics.statistics("period", 0, 3 * DAY, nav, metrics.Closures(), True)
        assert row["pnl_pct"] == 21
        assert abs(row["sharpe_ratio"] - (D(1460) / 3).sqrt()) < D("1e-90")
        assert row["daily_intervals"] == row["known_daily_intervals"] == 3


def test_missing_days_zero_variance_and_insufficient_funding_are_not_faked() -> None:
    nav: dict[int, Decimal | None] = {0: D(100), DAY: None, 2 * DAY: D(120)}
    gap = metrics.statistics("period", 0, 2 * DAY, nav, metrics.Closures(), True)
    assert gap["pnl_pct"] == 20 and gap["sharpe_ratio"] is None
    assert gap["missing_daily_intervals"] == 2
    flat = metrics.statistics(
        "period", 0, 2 * DAY, dict.fromkeys(nav, D(100)), metrics.Closures(), True
    )
    assert flat["pnl_pct"] == 0 and flat["sharpe_ratio"] is None
    unfunded = metrics.statistics("period", 0, 2 * DAY, nav, metrics.Closures(), False)
    assert unfunded["pnl_pct"] is None and unfunded["sharpe_ratio"] is None
    assert unfunded["funding_sufficient"] is False


def test_profit_factor_and_win_rate_count_completed_net_positions() -> None:
    stats = metrics.Closures()
    for gain in (D(10), D(-5), D(0), D(20)):
        stats.add(gain)
    result = stats.metrics()
    assert result["profit_factor"] == 6
    assert result["win_rate_pct"] == 50  # Breakeven remains in denominator.
    assert result["completed_positions"] == 4
    no_losses = metrics.Closures()
    no_losses.add(D(2))
    no_losses.add(D(3))
    assert no_losses.metrics()["profit_factor"] is None
    assert no_losses.metrics()["win_rate_pct"] == 100
    stats.add(None)
    assert stats.metrics()["profit_factor"] is stats.metrics()["win_rate_pct"] is None


@pytest.mark.parametrize(
    ("pnls", "factor", "status", "wins", "losses", "breakeven"),
    [
        ([-10], "0", "zero", 0, 1, 0),
        ([10], None, "no_losses", 1, 0, 0),
        ([0], None, "no_losses", 0, 0, 1),
        ([10, -5], "2", "finite", 1, 1, 0),
        ([-5, -5], "0", "zero", 0, 2, 0),
        ([10, -5, 0], "2", "finite", 1, 1, 1),
        ([], None, "no_completed_cycles", 0, 0, 0),
    ],
)
def test_completed_cycle_metric_states(
    pnls: list[int], factor: str | None, status: str, wins: int, losses: int, breakeven: int
) -> None:
    cycles = metrics.Closures()
    for pnl in pnls:
        cycles.add(D(pnl))
    result = cycles.metrics()
    assert result["profit_factor"] == (D(factor) if factor is not None else None)
    assert result["profit_factor_status"] == status
    assert (result["wins"], result["losses"], result["breakeven"]) == (wins, losses, breakeven)
    assert result["completed_cycles"] == len(pnls)
    assert result["win_rate_pct"] == (D(wins) * 100 / len(pnls) if pnls else None)


def test_multi_fill_round_trips_and_open_residual_are_not_sell_posting_statistics(
    tmp_path: Path,
) -> None:
    source = tmp_path / "htx_USDT_ASSET.bin"
    source.write_bytes(
        b"".join(usdt.BAR.pack(START + i * DAY - usdt.MINUTE, 10, 10, 10, 10) for i in range(3))
    )
    rows = [
        (True, "10", "1", ".02", "10", "989.98", "1"),
        (True, "20", "1", ".04", "20", "969.94", "2"),
        (False, "30", ".5", ".03", "15", "984.91", "1.5"),
        (False, "5", "1.5", ".015", "7.5", "992.395", "0"),
        (True, "5", "1", ".01", "5", "987.385", "1"),
        (False, "10", "1", ".02", "10", "997.365", "0"),
        (True, "2", "1", ".004", "2", "995.361", "1"),
    ]
    fills: list[metrics.Fill] = [
        (START + (i + 1) * usdt.MINUTE, row[0], *map(D, row[1:]))  # type: ignore[misc]
        for i, row in enumerate(rows)
    ]

    def run() -> dict[str, Any]:
        pair = metrics.Pair(
            "htx:ASSET/USDT",
            source,
            hashlib.sha256(source.read_bytes()).hexdigest(),
            START,
            START + 2 * DAY,
            D(1000),
            iter(fills),
            True,
        )
        return metrics.report([pair], ArchiveCatalog(tmp_path))

    result = run()
    assert run() == result
    full = result["full_period"]
    assert full["completed_cycles"] == 2 and full["open_cycles"] == 1
    assert full["wins"] == full["losses"] == 1
    assert D(full["winning_position_pnl_usdt"]) == D("4.97")
    assert D(full["losing_position_pnl_usdt"]) == D("7.605")
    assert full["win_rate_pct"] == "50"
    assert D(full["ending_equity_usdt"]) == D("1005.361")
    partial = metrics.Pair(
        "htx:ASSET/USDT",
        source,
        hashlib.sha256(source.read_bytes()).hexdigest(),
        START,
        START + DAY,
        D(1000),
        iter(fills[:3]),
        True,
    )
    partial_result = metrics.report([partial], ArchiveCatalog(tmp_path))["full_period"]
    assert partial_result["completed_cycles"] == 0 and partial_result["open_cycles"] == 1
    assert partial_result["win_rate_pct"] is None


def test_cycle_closing_at_month_boundary_belongs_to_completion_month(tmp_path: Path) -> None:
    source = tmp_path / "htx_USDT_ASSET.bin"
    source.write_bytes(
        b"".join(usdt.BAR.pack(START + i * DAY - usdt.MINUTE, 10, 10, 10, 10) for i in range(5))
    )
    fills: list[metrics.Fill] = [
        (START + usdt.MINUTE, True, D(10), D(1), D(0), D(10), D(90), D(1)),
        (START + 2 * DAY, False, D(9), D(1), D(0), D(9), D(99), D(0)),
    ]
    pair = metrics.Pair(
        "htx:ASSET/USDT",
        source,
        hashlib.sha256(source.read_bytes()).hexdigest(),
        START,
        START + 4 * DAY,
        D(100),
        iter(fills),
        True,
    )
    rows = metrics.report([pair], ArchiveCatalog(tmp_path))["monthly"]
    assert rows[0]["completed_cycles"] == 0
    assert rows[1]["completed_cycles"] == 1
    assert rows[1]["profit_factor_status"] == "zero"


def test_monthly_equity_includes_open_inventory_and_uses_month_start(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    path = root / "venue_USDT_ASSET.bin"
    values = [(START + i * DAY - usdt.MINUTE, value) for i, value in enumerate((10, 11, 12, 9, 8))]
    path.write_bytes(b"".join(usdt.BAR.pack(time, p, p, p, p) for time, p in values))
    before = path.read_bytes()
    # Buy 1 @10 + .02 fee. Sell 1 @9 - .018 fee on Feb 2.
    trades: list[metrics.Fill] = [
        (START + 1, True, D(10), D(1), D(".02"), D(10), D("89.98"), D(1)),
        (START + 3 * DAY + 1, False, D(9), D(1), D(".018"), D(9), D("98.962"), D(0)),
    ]
    pair = metrics.Pair(
        "venue:ASSET/USDT",
        path,
        hashlib.sha256(before).hexdigest(),
        START,
        START + 4 * DAY,
        D(100),
        iter(trades),
    )
    result = metrics.report([pair], ArchiveCatalog(root))
    january, february = result["monthly"]
    assert january["period"] == "2025-01" and february["period"] == "2025-02"
    assert D(january["ending_equity_usdt"]) == D("101.98")
    assert D(january["pnl_pct"]) == D("1.98")  # Includes still-open inventory.
    assert D(february["starting_equity_usdt"]) == D("101.98")
    with localcontext() as context:
        context.prec = 96
        expected = (D("98.962") / D("101.98") - 1) * 100
    assert abs(D(february["pnl_pct"]) - expected) < D("1e-90")
    assert january["completed_positions"] == 0
    assert february["completed_positions"] == 1
    assert february["losses"] == 1 and D(february["win_rate_pct"]) == 0
    assert result["full_period"]["profit_factor"] == "0"
    assert result["full_period"]["profit_factor_status"] == "zero"
    assert path.read_bytes() == before


def test_month_windows_clip_partial_months_without_daily_resampling() -> None:
    start, end = utc_ms("2025-01-31T12:00:00Z"), utc_ms("2025-02-01T01:00:00Z")
    rows = metrics.periods(start, end)
    assert [label for label, _, _ in rows] == ["Entire selected period", "2025-01", "2025-02"]
    assert rows[1][1] == start and rows[2][2] == end
    data: dict[int, Decimal | None] = {start: D(100), end: D(120)}
    row = metrics.statistics("partial", start, end, data, metrics.Closures(), True)
    assert row["daily_intervals"] == 0 and row["sharpe_ratio"] is None


def test_partial_sells_form_one_completed_position_with_all_fees(tmp_path: Path) -> None:
    source = tmp_path / "venue_USDT_ASSET.bin"
    source.write_bytes(
        b"".join(usdt.BAR.pack(START + i * DAY - usdt.MINUTE, 100, 100, 100, 100) for i in range(4))
    )
    fills: list[metrics.Fill] = [
        (START + 1, True, D(100), D(1), D(".2"), D(100), D("899.8"), D(1)),
        (START + DAY + 1, False, D(110), D(".4"), D(".088"), D(44), D("943.712"), D(".6")),
        (START + 2 * DAY + 1, False, D(90), D(".6"), D(".108"), D(54), D("997.604"), D(0)),
    ]
    pair = metrics.Pair(
        "venue:ASSET/USDT",
        source,
        hashlib.sha256(source.read_bytes()).hexdigest(),
        START,
        START + 3 * DAY,
        D(1000),
        iter(fills),
    )
    result = metrics.report([pair], ArchiveCatalog(tmp_path))
    row = result["full_period"]
    assert row["completed_positions"] == 1
    assert row["win_rate_pct"] == "0"
    assert D(row["losing_position_pnl_usdt"]) == D("2.396")
    assert result["monthly"][0]["completed_positions"] == 0
    assert result["monthly"][1]["completed_positions"] == 1


def test_combined_return_values_quote_cash_at_each_historical_fx_rate(tmp_path: Path) -> None:
    for quote, base in (("BTC", "ASSET"), ("USDT", "OTHER")):
        (tmp_path / f"venue_{quote}_{base}.bin").write_bytes(
            b"".join(usdt.BAR.pack(START + i * DAY - usdt.MINUTE, 10, 10, 10, 10) for i in range(3))
        )
    (tmp_path / "venue_USDT_BTC.bin").write_bytes(
        b"".join(
            usdt.BAR.pack(START + i * DAY - usdt.MINUTE, p, p, p, p)
            for i, p in enumerate((100, 150, 200))
        )
    )
    pairs = []
    for quote, base, capital in (("BTC", "ASSET", 10), ("USDT", "OTHER", 100)):
        source = tmp_path / f"venue_{quote}_{base}.bin"
        pairs.append(
            metrics.Pair(
                f"venue:{base}/{quote}",
                source,
                hashlib.sha256(source.read_bytes()).hexdigest(),
                START,
                START + 2 * DAY,
                D(capital),
                iter(()),
            )
        )
    result = metrics.report(pairs, ArchiveCatalog(tmp_path))
    row = result["full_period"]
    assert row["starting_equity_usdt"] == "1100"
    assert row["ending_equity_usdt"] == "2100"
    assert abs(D(row["pnl_pct"]) - D(1000) / 1100 * 100) < D("1e-24")


def test_funded_minute_inspection_includes_fills_at_bar_completion(tmp_path: Path) -> None:
    source = tmp_path / "venue_USDT_ASSET.bin"
    source.write_bytes(
        b"".join(
            usdt.BAR.pack(START + i * usdt.MINUTE, p, p, p, p) for i, p in enumerate((10, 12, 11))
        )
    )
    before = source.read_bytes()
    fill: metrics.Fill = (START + usdt.MINUTE, True, D(10), D(1), D(".02"), D(10), D("89.98"), D(1))

    def pair() -> metrics.Pair:
        return metrics.Pair(
            "venue:ASSET/USDT",
            source,
            hashlib.sha256(before).hexdigest(),
            START,
            START + 3 * usdt.MINUTE,
            D(100),
            iter([fill]),
            True,
            D("1e-8"),
        )

    at = START + usdt.MINUTE
    data = metrics.funded_valuation([pair()], ArchiveCatalog(tmp_path), at)
    state = data["pairs"][0]["at"]
    assert state["position_qty"] == "1"
    assert D(state["cash_value_usdt"]) == D("89.98")
    assert D(state["nav_usdt"]) == D("99.98")
    assert D(state["wallet_pnl_usdt"]) == D("-.02")
    assert D(state["fees_usdt"]) == D(".02")
    end = metrics.funded_valuation([pair()], ArchiveCatalog(tmp_path), START + 3 * usdt.MINUTE)
    assert D(end["funded_nav_usdt"]) == D("100.98")
    assert source.read_bytes() == before


def test_minimum_rules_preserve_distinct_amount_cost_and_precision(
    tmp_path: Path,
) -> None:

    from scripts.backtest_minimum import market_rules

    (tmp_path / "venue_USDT_ASSET.bin").write_bytes(b"x")
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    entry: dict[str, Any] = {
        "symbol": "ASSET/USDT",
        "precision_mode": "tick_size",
        "price_precision": "0.01",
        "amount_precision": "0.001",
        "limits": {
            "amount": {"min": "0.005", "max": "1000"},
            "cost": {"min": "3", "max": "10000"},
            "price": {"min": "0.01", "max": None},
        },
    }
    snapshot = {
        "schema": 2,
        "exchange": "venue",
        "fetched_utc": "2026-11-12T00:00:00Z",
        "markets": {"USDT_ASSET": entry},
    }
    path = snapshots / "venue.json"
    path.write_text(json.dumps(snapshot))
    catalog = ArchiveCatalog(tmp_path, snapshots)
    rules = market_rules(catalog, "venue:ASSET/USDT")
    assert rules["values"]["buy_cost_min"] == "3"
    assert rules["values"]["buy_amount_min"] == "0.005"
    assert rules["values"]["amount_tick"] == "0.001"
    assert rules["values"]["base_quantum"] == "0.001"
    assert D(rules["values"]["quote_quantum"]) <= D("0.00001")
    assert rules["historical_rules_verified"] is False
    entry["precision_mode"] = "significant_digits"
    path.write_text(json.dumps(snapshot))
    with pytest.raises(ValueError, match="does not support"):
        market_rules(catalog, "venue:ASSET/USDT")
    path.unlink()
    with pytest.raises(ValueError, match="unavailable"):
        market_rules(catalog, "venue:ASSET/USDT")
