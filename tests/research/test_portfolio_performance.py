"""Independent expected returns, minute drawdown and combined-wallet fixtures."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

import pytest

from scripts import backtest_performance as m
from scripts import backtest_usdt as fx
from scripts.backtest_legacy_n import ArchiveCatalog

from .test_funded_accounting import START, M


@pytest.mark.parametrize(
    ("values", "status"),
    [
        ([100], "insufficient_observations"),
        ([100, 110], "insufficient_observations"),
        ([100, 100, 100], "zero_volatility"),
        ([100, None, 110], "missing_valuation"),
        ([100, 0, 110], "invalid_equity"),
        ([100, -1, 110], "invalid_equity"),
        ([100, 110, 121, 121], "available"),
    ],
)
def test_sharpe_states(values: list[int | None], status: str) -> None:
    nav = {i * m.DAY: D(v) if v is not None else None for i, v in enumerate(values)}
    row = m.statistics("test", 0, (len(values) - 1) * m.DAY, nav, m.Closures(), True)
    assert row["sharpe_status"] == status
    assert (row["sharpe_ratio"] is not None) == (status == "available")
    json.dumps(fx.text_values(row), allow_nan=False)


def test_minute_drawdown_finds_intraday_trough_and_earliest_peak() -> None:
    nav = {0: D(100), M: D(120), 2 * M: D(60), 3 * M: D(130)}
    row = m.statistics("test", 0, 3 * M, nav, m.Closures(), True)
    assert row["max_drawdown_pct"] == -50
    assert row["drawdown_peak_ms"] == M and row["drawdown_trough_ms"] == 2 * M
    assert row["drawdown_peak_equity_usdt"] == 120
    assert row["drawdown_trough_equity_usdt"] == 60
    assert row["pnl_pct"] == 30
    # A missing mark cannot be bridged, even when endpoint return is known.
    nav[M] = None  # type: ignore[assignment]
    row = m.statistics("test", 0, 3 * M, nav, m.Closures(), True)
    assert row["max_drawdown_pct"] is None
    assert row["max_drawdown_status"] == "missing_valuation"
    assert row["pnl_pct"] == 30


def test_sparse_or_nonpositive_equity_cannot_produce_drawdown() -> None:
    for nav, status in (
        ({0: D(100), 2 * M: D(120)}, "incomplete_minute_curve"),
        ({0: D(100), M: D(0), 2 * M: D(120)}, "invalid_equity"),
    ):
        row = m.statistics("test", 0, 2 * M, nav, m.Closures(), True)
        assert row["max_drawdown_pct"] is None
        assert row["max_drawdown_status"] == status


def test_external_deposit_is_not_profit_and_months_use_own_equity() -> None:
    nav = {0: D(1000), m.DAY: D(1100), 2 * m.DAY: D(1210)}
    row = m.statistics("deposit", 0, m.DAY, nav, m.Closures(), True, external_flows={m.DAY: D(100)})
    assert row["equity_change_usdt"] == 100
    assert row["pnl_usdt"] == row["pnl_pct"] == 0
    assert row["external_net_cash_flows_usdt"] == 100
    next_period = m.statistics("next", m.DAY, 2 * m.DAY, nav, m.Closures(), True)
    assert next_period["starting_equity_usdt"] == 1100
    assert next_period["pnl_pct"] == 10


def test_streamed_curve_keeps_all_minute_extrema_but_only_daily_and_boundary_values() -> None:
    curve = m.EquityCurve(START, START + 4 * m.DAY)
    for time in range(START, START + 4 * m.DAY + 1, M):
        curve.add(time, D(50) if time == START + m.DAY + M else D(100))
    assert len(curve.nav) == 5
    assert curve.drawdowns[0].metrics()["max_drawdown_pct"] == -50
    assert curve.drawdowns[1].metrics()["max_drawdown_pct"] == -50
    assert curve.drawdowns[2].metrics()["max_drawdown_pct"] == 0
    assert curve.drawdowns[0].observations == 5761


def test_three_shared_asset_wallets_use_combined_equity_and_month_boundaries(
    tmp_path: Path,
) -> None:
    end = START + 4 * m.DAY
    rates = {
        "BTC/USDT": [100, 110, 90, 120, 100],
        "ETH/USDT": [2, 3, 4, 5, 6],
        "USDC/USDT": [1, 1, 1, 1, 1],
        "ETH/BTC": [1, 1, 1, 1, 1],
        "ETH/USDC": [2, 3, 4, 5, 6],
    }
    for symbol, daily in rates.items():
        base, quote = symbol.split("/")
        with (tmp_path / f"htx_{quote}_{base}.bin").open("wb") as stream:
            for time in range(START, end + 1, M):
                value = daily[(time - START) // m.DAY]
                # Interior day-1 crash recovers before the next daily mark.
                if symbol == "BTC/USDT" and time == START + m.DAY + M:
                    value = 40
                stream.write(fx.BAR.pack(time - M, value, value, value, value))
    pairs = []
    for symbol in ("BTC/USDT", "ETH/BTC", "ETH/USDC"):
        base, quote = symbol.split("/")
        source = tmp_path / f"htx_{quote}_{base}.bin"
        pairs.append(
            m.Pair(
                "htx:" + symbol,
                source,
                hashlib.sha256(source.read_bytes()).hexdigest(),
                START,
                end,
                D(1000),
                iter(()),
                True,
            )
        )
    result = m.report(pairs, ArchiveCatalog(tmp_path))
    full = result["full_period"]
    assert full["starting_equity_usdt"] == full["ending_equity_usdt"] == "102000"
    assert D(full["pnl_pct"]) == 0
    assert full["completed_cycles"] == full["open_cycles"] == 0
    assert full["max_drawdown_status"] == "available"
    assert full["drawdown_peak_equity_usdt"] == "112000"
    assert full["drawdown_trough_equity_usdt"] == "42000"
    assert D(full["max_drawdown_pct"]) == D("-62.5")
    with localcontext() as context:
        context.prec = 96
        # Derived directly from three wallet balances, not pair ratios.
        equity = [D(v) for v in (102000, 112000, 92000, 122000, 102000)]
        returns = [b / a - 1 for a, b in zip(equity[:-1], equity[1:], strict=True)]
        mean = sum(returns, D(0)) / 4
        sd = (sum(((v - mean) ** 2 for v in returns), D(0)) / 3).sqrt()
        expected = mean / sd * D(365).sqrt()
        assert abs(D(full["sharpe_ratio"]) - expected) < D("1e-90")
        january, february = result["monthly"]
        assert D(january["pnl_pct"]) == -D(10000) / 102000 * 100
        assert D(february["pnl_pct"]) == D(10000) / 92000 * 100
    assert D(full["quote_fx_pnl_usdt"]) == 0
    assert full["minute_observations"] == 5761
    assert len(result["pairs"]) == 3
    assert len(result["pairs"][1]["monthly"]) == 2
    json.dumps(result, allow_nan=False)


def test_duplicate_market_funding_is_refused(tmp_path: Path) -> None:
    pair = m.Pair("htx:BTC/USDT", tmp_path / "unused", "", START, START + M, D(1000), iter(()))
    with pytest.raises(ValueError, match="double-count"):
        m.report([pair, pair], ArchiveCatalog(tmp_path))


def test_report_cache_depends_on_base_conversion_as_well_as_quote(tmp_path: Path) -> None:
    for name in ("htx_BTC_ETH.bin", "htx_USDT_ETH.bin", "htx_USDT_BTC.bin"):
        (tmp_path / name).write_bytes(fx.BAR.pack(START, 1, 1, 1, 1))
    paths = fx.dependencies(ArchiveCatalog(tmp_path), ["htx:ETH/BTC"])
    assert tmp_path / "htx_USDT_ETH.bin" in paths
    assert tmp_path / "htx_USDT_BTC.bin" in paths
