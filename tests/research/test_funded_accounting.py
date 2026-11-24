"""Hand-calculated funded ledger and HTX FX fixtures, without exchange access."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest

from scripts import backtest_performance as metrics
from scripts import backtest_usdt as marks
from scripts.backtest_legacy_n import ArchiveCatalog

START = 1_738_195_200_000  # 2025-01-30 00:00 UTC
M = marks.MINUTE

# Expected values were calculated from the three cash movements by hand.
# Each fixture starts with 1,000 native quote units and a 100-unit reserve.
EXPECTED = {
    "BTC/USDT": {
        "initial": "1000",
        "cash_value": "996.978",
        "inventory": "10",
        "nav": "1006.978",
        "realized": "1.654",
        "unrealized": "5.324",
        "fees": ".022",
        "trading": "6.978",
        "fx": "0",
    },
    "ETH/BTC": {
        "initial": "100000",
        "cash_value": "139576.92",
        "inventory": "1200",
        "nav": "140776.92",
        "realized": "251.76",
        "unrealized": "665.6",
        "fees": "2.64",
        "trading": "917.36",
        "fx": "39859.56",
    },
    "ETH/USDC": {
        "initial": "1000",
        "cash_value": "996.978",
        "inventory": "10",
        "nav": "1006.978",
        "realized": ".69775",
        "unrealized": "5.4075",
        "fees": ".01975",
        "trading": "6.10525",
        "fx": ".87275",
    },
}


def candles(root: Path, market: str, values: list[str]) -> Path:
    base, quote = market.split("/")
    path = root / f"htx_{quote}_{base}.bin"
    path.write_bytes(
        b"".join(marks.BAR.pack(START + i * M - M, *([float(v)] * 4)) for i, v in enumerate(values))
    )
    return path


def case(root: Path, symbol: str) -> metrics.Pair:
    source = candles(root, symbol, ["2", "2", "3", "4", "5" if symbol == "BTC/USDT" else "4"])
    if symbol == "ETH/BTC":
        candles(root, "BTC/USDT", ["100", "110", "120", "130", "140"])
        candles(root, "ETH/USDT", ["200", "220", "360", "520", "600"])
    elif symbol == "ETH/USDC":
        candles(root, "USDC/USDT", ["1", ".875", "1.125", ".75", "1"])
        candles(root, "ETH/USDT", ["2", "1.75", "3.375", "3", "5"])
    fills = [
        metrics.NativeFill(
            (START + M, True, D(2), D(2), D(".008"), D(4), D("995.992"), D(2)), D("4.008"), D(0)
        ),
        metrics.NativeFill(
            (START + 2 * M, True, D(3), D(1), D(".006"), D(3), D("992.986"), D(3)), D("7.014"), D(0)
        ),
        metrics.NativeFill(
            (START + 3 * M, False, D(4), D(1), D(".008"), D(4), D("996.978"), D(2)),
            D("4.676"),
            D("1.654"),
        ),
    ]
    return metrics.Pair(
        "htx:" + symbol,
        source,
        hashlib.sha256(source.read_bytes()).hexdigest(),
        START,
        START + 4 * M,
        D(1000),
        iter(fills),
        True,
        D(".001"),
        reserve_quote=D(100),
        ending_reserved_quote=D(50),
    )


@pytest.mark.parametrize("symbol", list(EXPECTED))
def test_three_native_ledgers_reconcile_to_independent_usdt_balances(
    tmp_path: Path, symbol: str
) -> None:
    pair = case(tmp_path, symbol)
    result = metrics.funded_valuation([pair], ArchiveCatalog(tmp_path), START + 4 * M)
    state = result["pairs"][0]["at"]
    expected = EXPECTED[symbol]
    fields = {
        "cash_value": "cash_value_usdt",
        "inventory": "position_value_usdt",
        "nav": "nav_usdt",
        "realized": "realized_pnl_usdt",
        "unrealized": "unrealized_pnl_usdt",
        "fees": "fees_usdt",
        "trading": "trading_pnl_usdt",
        "fx": "quote_fx_pnl_usdt",
    }
    for key, field in fields.items():
        assert D(state[field]) == D(expected[key]), field
    assert D(state["wallet_pnl_usdt"]) == D(expected["nav"]) - D(expected["initial"])
    assert state["pnl_usdt"] == state["wallet_pnl_usdt"]
    assert state["pnl_usdt"] == result["series"][0]["pnl_usdt"]
    assert state["trading_pnl_usdt"] == result["series"][0]["trading_pnl_usdt"]
    assert D(state["cash_native_quote"]) == D("996.978")
    assert D(state["cost_native_quote"]) == D("4.676")
    assert D(state["realized_pnl_native_quote"]) == D("1.654")
    assert D(state["fees_native_quote"]) == D(".022")
    assert D(state["position_qty"]) == 2
    assert D(state["strategy_reserve_native_quote"]) == 100
    assert D(state["reconciliation_error_usdt"]) == 0
    assert state["rate_timestamp_ms"] == START + 3 * M
    assert state["mark_available_at_ms"] == START + 4 * M


@pytest.mark.parametrize("symbol", list(EXPECTED))
def test_initial_funding_and_unfilled_reservations_do_not_create_pnl(
    tmp_path: Path, symbol: str
) -> None:
    pair = case(tmp_path, symbol)
    pair.fills = iter(())
    first = metrics.funded_valuation([pair], ArchiveCatalog(tmp_path), START)["pairs"][0]["at"]
    assert D(first["nav_usdt"]) == D(EXPECTED[symbol]["initial"])
    assert D(first["wallet_pnl_usdt"]) == 0
    assert D(first["position_qty"]) == 0
    assert D(first["cash_native_quote"]) == 1000


def test_native_cost_rounding_comes_from_the_engine_sidecar(tmp_path: Path) -> None:
    source = candles(tmp_path, "BTC/USDT", ["1", "1", "1", "1", "1"])
    fills = [
        metrics.NativeFill(
            (START + M, True, D(1), D(3), D(".01"), D(3), D("996.99"), D(3)), D("3.01"), D(0)
        ),
        metrics.NativeFill(
            (START + 2 * M, False, D(1), D(1), D(".01"), D(1), D("997.98"), D(2)),
            D("2.01"),
            D("-.01"),
        ),
    ]
    pair = metrics.Pair(
        "htx:BTC/USDT",
        source,
        hashlib.sha256(source.read_bytes()).hexdigest(),
        START,
        START + 4 * M,
        D(1000),
        iter(fills),
        True,
        D(".01"),
    )
    state = metrics.funded_valuation([pair], ArchiveCatalog(tmp_path), START + 4 * M)["pairs"][0][
        "at"
    ]
    assert D(state["realized_pnl_usdt"]) == D("-.01")
    assert D(state["position_cost_usdt"]) == D("2.01")
    assert D(state["nav_usdt"]) == D("999.98")


@pytest.mark.parametrize("wrong", ["cash", "base", "cost", "realized", "duplicate", "oversell"])
def test_corrupt_or_duplicate_funded_postings_are_refused(tmp_path: Path, wrong: str) -> None:
    pair = case(tmp_path, "BTC/USDT")
    rows = list(pair.fills)
    assert isinstance(rows[0], metrics.NativeFill)
    first = rows[0]
    values = list(first.values)
    if wrong in {"cash", "base"}:
        values[6 if wrong == "cash" else 7] += D(1)  # type: ignore[operator]
        rows[0] = metrics.NativeFill(tuple(values), first.cost_after, first.realized_after)  # type: ignore[arg-type]
    elif wrong == "cost":
        rows[0] = metrics.NativeFill(first.values, D(500), first.realized_after)
    elif wrong == "realized":
        rows[0] = metrics.NativeFill(first.values, first.cost_after, D(1))
    elif wrong == "duplicate":
        rows.insert(1, first)
    else:
        rows = [
            metrics.NativeFill(
                (START + M, False, D(1), D(1), D(0), D(1), D(1001), D(0)), D(0), D(1)
            )
        ]
    pair.fills = iter(rows)
    with pytest.raises(ValueError):
        metrics.funded_valuation([pair], ArchiveCatalog(tmp_path), START + 4 * M)


def test_absent_conversion_and_future_candle_are_not_used(tmp_path: Path) -> None:
    pair = case(tmp_path, "ETH/USDC")
    # A newly created synthetic fixture deliberately lacks its required final
    # quote and cross-route candle; the future candle cannot mark this instant.
    for name in ("htx_USDT_USDC.bin", "htx_USDT_ETH.bin"):
        path = tmp_path / name
        content = path.read_bytes()
        path.write_bytes(content[: 3 * marks.BAR.size] + marks.BAR.pack(START + 4 * M, 9, 9, 9, 9))
    state = metrics.funded_valuation([pair], ArchiveCatalog(tmp_path), START + 4 * M)["pairs"][0][
        "at"
    ]
    assert state["nav_usdt"] is None
    assert state["cash_value_usdt"] is None
    assert state["valuation_status"] == "unavailable"
    assert "missing_quote_conversion" in state["unavailable_reasons"]


def test_sidecar_requires_native_ledger_fields_and_quote_fees(tmp_path: Path) -> None:
    row: dict[str, Any] = {
        "market": "htx:ETH/BTC",
        "timestamp_ms": START,
        "buy": True,
        "price": "1",
        "amount": "1",
        "fee": ".002",
        "cash": "998.998",
        "base": "1",
        "cost": "1.002",
        "realized": "0",
    }
    path = tmp_path / "fills.jsonl"

    def write() -> None:
        path.write_text(json.dumps(row, separators=(",", ":")) + "\n")

    write()
    assert len(list(metrics.funded_fills(tmp_path, "htx:ETH/BTC"))) == 1
    row["fee_currency"] = "ETH"
    write()
    with pytest.raises(ValueError, match="quote-currency fees only"):
        list(metrics.funded_fills(tmp_path, "htx:ETH/BTC"))
    row.pop("fee_currency")
    row.pop("cost")
    write()
    with pytest.raises(ValueError, match="Older funded fill sidecar"):
        list(metrics.funded_fills(tmp_path, "htx:ETH/BTC"))


def test_known_ending_nav_does_not_claim_complete_pnl_when_opening_fx_is_missing(
    tmp_path: Path,
) -> None:
    pair = case(tmp_path, "ETH/BTC")
    for name in ("htx_USDT_BTC.bin", "htx_USDT_ETH.bin"):
        path = tmp_path / name
        path.write_bytes(path.read_bytes()[marks.BAR.size :])
    result = metrics.funded_valuation([pair], ArchiveCatalog(tmp_path), START + 4 * M)
    state = result["pairs"][0]["at"]
    assert state["nav_usdt"] is not None
    assert state["pnl_usdt"] is None
    assert not state["complete"]
    assert "missing_initial_quote_conversion" in state["accounting_unavailable_reasons"]
    assert result["series"][0]["missing_pairs"] == 1
