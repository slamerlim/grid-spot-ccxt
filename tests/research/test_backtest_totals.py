"""All-trade native-quote totals retain every pair and disclose carried marks."""

from __future__ import annotations

import hashlib
import json
import struct
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import backtest_legacy_n as legacy_n
from scripts import backtest_totals as totals_module
from scripts.module1_study import read_json
from scripts.module1_verify_sources import utc_ms

START = utc_ms("2025-01-01T00:00:00Z")
MINUTE = 60000


@pytest.mark.parametrize(
    "value",
    [
        "0.0000000000000000000001234000",
        "1234567890123456789012345678901234567890.0000",
        "1.234000E+100",
        "-12.34000",
    ],
)
def test_compact_decimal_serialization_never_rounds_any_significant_digit(value: str) -> None:
    original = Decimal(value)
    result = totals_module.decimal_text(original)
    assert Decimal(result) == original
    assert len(result) <= len(str(original))


@pytest.fixture
def reviewed_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pure aggregation fixtures still use real binary records and receipt hashes.
    # The API test below also exercises the real retained-artifact review.
    monkeypatch.setattr(legacy_n, "review", lambda folder: read_json(folder / "summary.json"))


def child(
    root: Path,
    name: str,
    samples: list[tuple[int, float, float]],
    *,
    trades: list[tuple[int, float, float, float, float]] | None = None,
    terminal_pnl: float | None = None,
    fees: float = 0.1,
    missing_minutes: int = 0,
) -> Path:
    folder = root / name
    folder.mkdir()
    bars = b"".join(
        struct.pack(
            "<qq16d",
            end - MINUTE,
            end,
            10,
            20,
            5,
            10,
            0,
            1000,
            -1000,
            pnl,
            0,
            1000,
            0,
            cost,
            0,
            999,
            0,
            777,
        )
        for end, pnl, cost in samples
    )
    rows = trades or []
    trade_bytes = b"".join(
        struct.pack("<q7dII", moment, price, 1, price, price * 0.002, qty, cost, realized, 0, 1)
        for moment, price, qty, cost, realized in rows
    )
    hashes = {}
    for filename, raw in (("series.bin", bars), ("trades.bin", trade_bytes)):
        (folder / filename).write_bytes(raw)
        hashes[filename] = hashlib.sha256(raw).hexdigest()
    (folder / "receipt.json").write_text(json.dumps({"hashes": hashes}), encoding="utf-8")
    last_pnl = samples[-1][1] if terminal_pnl is None else terminal_pnl
    summary = {
        "first_ts": samples[0][0] - MINUTE,
        "last_ts": samples[-1][0] - MINUTE,
        "chart_interval_ms": MINUTE,
        "chart_points": len(samples),
        "missing_minutes": missing_minutes,
        "pnl": last_pnl,
        "realized_pnl": last_pnl,
        "unrealized_pnl": 0,
        "position_cost": samples[-1][2],
        "fees": fees,
        "buys": len(rows),
        "sells": 0,
        "trades": len(rows),
        "max_drawdown": 999,
    }
    (folder / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return folder


def group_for(children: list[tuple[str, Path, Path]], end: int) -> dict[str, Any]:
    return cast(dict[str, Any], totals_module.totals(children, end)["groups"][0])


@pytest.mark.usefixtures("reviewed_summary")
def test_all_pair_totals_keep_native_quotes_and_decimal_values(tmp_path: Path) -> None:
    first = child(tmp_path, "first", [(START + MINUTE, 0.1, 0.4)], fees=0.1)
    second = child(tmp_path, "second", [(START + MINUTE, 0.2, 0.5)], fees=0.2)
    btc = child(tmp_path, "btc", [(START + MINUTE, 0.00000001, 0.00000002)])
    result = totals_module.totals(
        [("gate:A/USD", first, first), ("htx:B/USD", second, second), ("okx:C/BTC", btc, btc)],
        START + MINUTE,
    )
    groups = {group["quote"]: group for group in result["groups"]}
    assert set(groups) == {"USD", "BTC"}
    usd = groups["USD"]
    assert usd["markets"] == ["gate:A/USD", "htx:B/USD"]
    assert usd["terminal_marks_synchronous"] is True
    assert usd["terminal_basis"] == "sum_of_pair_terminal_marks"
    assert usd["current"] == usd["audited"]
    assert usd["current"]["pnl"] == "0.3" and usd["current"]["fees"] == "0.3"
    assert usd["current"]["series_marks_processed"] == 2
    assert usd["current"]["terminal_marks_processed"] == 2
    assert usd["current"]["series"][0]["position_cost"] == "0.9"
    assert groups["BTC"]["current"]["pnl"] == "1E-8"
    assert "no FX" in result["sampling_note"]
    assert "common_samples" not in usd and "omitted_samples" not in usd


@pytest.mark.usefixtures("reviewed_summary")
def test_identical_verified_events_reuse_aggregate_but_differing_results_do_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = child(tmp_path, "first", [(START + MINUTE, 1.0, 10.0)])
    second = child(tmp_path, "second", [(START + MINUTE, 2.0, 10.0)])
    calls = 0
    original = totals_module.aggregate

    def counted(*args: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(totals_module, "aggregate", counted)
    same = group_for([("bybit:A/USDT", first, first)], START + MINUTE)
    assert same["identical_aggregate_reused"] is True
    assert same["current"] == same["audited"] and calls == 1
    different = group_for([("bybit:A/USDT", first, second)], START + MINUTE)
    assert different["identical_aggregate_reused"] is False and calls == 3
    assert Decimal(different["current"]["pnl"]) == 1
    assert Decimal(different["audited"]["pnl"]) == 2


@pytest.mark.usefixtures("reviewed_summary")
def test_every_intrabar_trade_preserves_extrema_even_when_close_is_flat(tmp_path: Path) -> None:
    folder = child(
        tmp_path,
        "spikes",
        [(START + MINUTE, 0.0, 0.0)],
        trades=[(START + 1, 10, 0, 0, 100), (START + 2, 10, 0, 0, -80)],
    )
    group = group_for([("gate:A/USD", folder, folder)], START + MINUTE)
    current = group["current"]
    assert current == group["audited"]
    assert current["trade_events_processed"] == current["buys"] == 2
    assert current["merged_events_processed"] == 4
    bar = current["series"][0]
    assert list(map(Decimal, bar["pnl_ohlc"])) == [100, 100, -80, 0]
    assert list(map(Decimal, bar["drawdown_ohlc"])) == [0, 180, 0, 100]
    assert bar["events"] == 4 and Decimal(bar["pnl"]) == 0


@pytest.mark.usefixtures("reviewed_summary")
def test_disjoint_histories_include_both_terminals_and_carry_last_mark(tmp_path: Path) -> None:
    first = child(tmp_path, "first", [(START + MINUTE, 1.0, 10.0)])
    second = child(tmp_path, "second", [(START + 10 * MINUTE, 2.0, 20.0)])
    group = group_for(
        [("gate:A/USD", first, first), ("htx:B/USD", second, second)], START + 10 * MINUTE
    )
    assert group["terminal_marks_synchronous"] is False
    assert group["terminal_timestamp"] is None
    assert group["terminal_earliest"] == totals_module.timestamp(START + 3)
    assert group["terminal_latest"] == totals_module.timestamp(START + 9 * MINUTE + 3)
    for label in ("current", "audited"):
        result = group[label]
        assert result["pnl"] == "3" and result["unrealized_pnl"] == "0"
        assert [point["pnl"] for point in result["series"]] == ["1", "3"]
        assert [point["position_cost"] for point in result["series"]] == ["10", "30"]


@pytest.mark.usefixtures("reviewed_summary")
def test_missing_observations_retain_marks_without_excluding_other_pair_updates(
    tmp_path: Path,
) -> None:
    first = child(tmp_path, "first", [(START + n * MINUTE, float(n), 10.0) for n in (1, 2, 3)])
    second = child(
        tmp_path,
        "second",
        [(START + n * MINUTE, float(n), 20.0) for n in (1, 3)],
        missing_minutes=1,
    )
    group = group_for(
        [("gate:A/USD", first, first), ("htx:B/USD", second, second)], START + 3 * MINUTE
    )
    assert group["missing_minutes"] == 1
    assert group["current"]["series_marks_processed"] == 5
    assert [point["pnl"] for point in group["current"]["series"]] == ["2", "3", "6"]


@pytest.mark.usefixtures("reviewed_summary")
def test_drawdown_recomputed_from_aggregate_events_not_sum_of_pair_maxima(tmp_path: Path) -> None:
    folder = child(
        tmp_path,
        "path",
        [(START + n * MINUTE, pnl, 10.0) for n, pnl in ((1, -3.0), (2, 10.0), (3, 5.0))],
    )
    current = group_for([("gate:A/USD", folder, folder)], START + 3 * MINUTE)["current"]
    assert [Decimal(point["drawdown"]) for point in current["series"]] == [3, 0, 5]
    assert "max_drawdown" not in current


@pytest.mark.usefixtures("reviewed_summary")
def test_trade_fee_basis_and_authoritative_terminal_rounding_override(tmp_path: Path) -> None:
    folder = child(
        tmp_path,
        "fees",
        [(START + MINUTE, 0.0, 20.0)],
        trades=[(START + 3, 10.0, 2.0, 20.0, 0.0)],
        terminal_pnl=1e-17,
    )
    current = group_for([("gate:A/USD", folder, folder)], START + MINUTE)["current"]
    bar = current["series"][0]
    assert Decimal(bar["pnl_ohlc"][0]) == Decimal("-0.04")
    assert Decimal(bar["pnl_ohlc"][2]) == Decimal("-0.04")
    assert Decimal(bar["pnl"]) == Decimal(current["pnl"]) == Decimal("1e-17")
    assert bar["events"] == 3  # same timestamp: trade, close, then summary


@pytest.mark.usefixtures("reviewed_summary")
def test_large_event_stream_counts_every_trade_and_bounds_ohlc_output(tmp_path: Path) -> None:
    count = 6001
    samples = [(START + (index + 1) * MINUTE, float(index), 0.0) for index in range(count)]
    trades = [(START + index * MINUTE, 1.0, 0.0, 0.0, float(index)) for index in range(count)]
    folder = child(tmp_path, "many", samples, trades=trades)
    result = group_for([("gate:A/USD", folder, folder)], START + count * MINUTE)["current"]
    assert result["trade_events_processed"] == result["buys"] == count
    assert result["series_marks_processed"] == count
    assert result["merged_events_processed"] == 2 * count + 1
    assert 1 < len(result["series"]) <= 5000
    assert sum(bar["events"] for bar in result["series"]) == 2 * count + 1
    assert Decimal(result["series"][-1]["pnl"]) == Decimal(result["pnl"]) == count - 1


@pytest.mark.usefixtures("reviewed_summary")
@pytest.mark.parametrize("artifact", ["trades.bin", "series.bin"])
def test_totals_recheck_every_consumed_binary_hash(tmp_path: Path, artifact: str) -> None:
    folder = child(
        tmp_path, "changed", [(START + MINUTE, 1.0, 10.0)], trades=[(START, 10, 1, 10, 0)]
    )
    raw = bytearray((folder / artifact).read_bytes())
    raw[24] ^= 1
    (folder / artifact).write_bytes(raw)
    with pytest.raises(ValueError, match="changed during aggregation"):
        group_for([("gate:A/USD", folder, folder)], START + MINUTE)


def test_chunk_reader_closes_file_before_yielding_and_rechecks_final_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "records.bin"
    raw = b"".join(struct.pack("<q", value) for value in range(200))
    path.write_bytes(raw)
    stream = totals_module.records(path, struct.Struct("<q"), 200, hashlib.sha256(raw).hexdigest())
    assert next(stream) == (0,)
    assert not any(
        link.resolve() == path for link in Path("/proc/self/fd").iterdir() if link.exists()
    )
    assert list(stream)[-1] == (199,)
