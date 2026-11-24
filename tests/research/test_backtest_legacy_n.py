"""Offline adapter tests with a synthetic source and executable."""

from __future__ import annotations

import json
import struct
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from scripts import backtest_legacy_n as legacy
from scripts.module1_verify_sources import identity, sha256, write_json


def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Any], Path]:
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr(legacy, "DATA_ROOT", data_root)
    source_path = data_root / "gate_USDT_TEST.bin"
    first = 1_735_689_600_000  # 2025-01-01 00:00 UTC
    source_path.write_bytes(
        b"".join(struct.pack("<qffff", first + i * 60000, 10, 11, 9, 10) for i in range(3))
    )
    source = {
        "venue": "gate",
        "market": "TEST/USDT",
        "file": source_path.name,
        "source_path": str(source_path),
        "sha256": sha256(source_path),
        "file_identity_before_and_after": list(identity(source_path)),
        "first_utc": datetime.fromtimestamp(first / 1000, UTC).isoformat(),
        "last_utc": datetime.fromtimestamp((first + 120000) / 1000, UTC).isoformat(),
    }
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    market = {
        "precision_mode": "tick_size",
        "price_precision_mode": "tick_size",
        "amount_precision_mode": "tick_size",
        "price_min_tick": 0,
        "price_max_tick": 0,
        "price_precision": 0.01,
        "amount_precision": 0.001,
    }
    write_json(
        snapshots / "gate.json",
        {
            "schema": 2,
            "exchange": "gate",
            "fetched_utc": "2025-01-01T00:00:00+00:00",
            "ccxt_version": "synthetic",
            "markets": {"USDT_TEST": market},
        },
    )
    return source_path, source, snapshots


def test_catalog_and_input_bounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_path, _, snapshots = fixture(tmp_path, monkeypatch)
    archive = legacy.ArchiveCatalog(source_path.parent, snapshots)
    assert archive.search("gate:TEST")["matches"] == 1
    market = archive.detail("gate:TEST/USDT")
    assert market["precision_source"] == "saved_ccxt"
    payload = {
        "name": "Trial",
        "market": "gate:TEST/USDT",
        "start": "2025-01-01T00:00:00+00:00",
        "end": "2025-01-01T00:03:00+00:00",
        "step_pct": "1",
        "take_profit_pct": "1",
        "order_quote": "10",
        "averaging": "0",
        "price_tick": "0.00000001",
        "amount_tick": "0.0001",
        "confirm_synthetic": False,
        "assert_spot": True,
    }
    checked = legacy.settings(payload, market)
    assert checked["market"] == "gate:TEST/USDT"
    assert checked["price_tick"] is None  # ignored when saved CCXT precision applies
    with pytest.raises(ValueError, match="coverage"):
        legacy.settings({**payload, "end": "2025-01-01T00:04:00+00:00"}, market)
    with pytest.raises(ValueError, match="inspected"):
        legacy.settings({**payload, "market": "gate:OTHER/USDT"}, market)
    with pytest.raises(ValueError, match="decimal"):
        legacy.settings({**payload, "step_pct": "NaN"}, market)
    with pytest.raises(ValueError, match="spot-market"):
        legacy.settings({**payload, "assert_spot": False}, market)


def test_precision_snapshot_is_loaded_once_and_invalidates_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _, snapshots = fixture(tmp_path, monkeypatch)
    (path.parent / "gate_USDT_OTHER.bin").write_bytes(path.read_bytes())
    archive = legacy.ArchiveCatalog(path.parent, snapshots)
    calls = 0
    original = legacy.snapshot

    def counted(saved: Path, venue: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return original(saved, venue)

    monkeypatch.setattr(legacy, "snapshot", counted)
    for _ in range(3):
        assert archive.detail("gate:TEST/USDT")["precision_source"] == "saved_ccxt"
        assert archive.detail("gate:OTHER/USDT")["precision_source"] == "synthetic"
    assert calls == 1
    value = original(snapshots / "gate.json", "gate")
    value["markets"]["USDT_OTHER"] = value["markets"].pop("USDT_TEST")
    (snapshots / "gate.json").write_text(json.dumps(value))
    assert archive.detail("gate:OTHER/USDT")["precision_source"] == "saved_ccxt"
    assert archive.detail("gate:TEST/USDT")["precision_source"] == "synthetic"
    assert calls == 2
    (snapshots / "gate.json").unlink()
    assert archive.detail("gate:OTHER/USDT")["precision_source"] == "synthetic"
    write_json(snapshots / "gate.json", {**value, "exchange": "wrong"})
    with pytest.raises(ValueError, match="unexpected format"):
        archive.detail("gate:OTHER/USDT")


def test_archive_exchange_counts_and_compact_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, _, snapshots = fixture(tmp_path, monkeypatch)
    (source_path.parent / "gate_USDT_OTHER.bin").write_bytes(source_path.read_bytes())
    (source_path.parent / "okx_BTC_TEST.bin").write_bytes(source_path.read_bytes())
    archive = legacy.ArchiveCatalog(source_path.parent, snapshots)
    assert archive.exchange_counts() == {
        "gate": {"total": 2, "usdt": 2},
    }
    assert archive.expand_selection(["gate"], [], ["gate:OTHER/USDT"]) == [
        "gate:TEST/USDT",
    ]
    assert archive.search("")["total"] == 2
    with pytest.raises(ValueError, match="USDT-only"):
        archive.expand_selection(["gate"], ["okx:TEST/BTC"], [])
    with pytest.raises(ValueError, match="unknown exchange"):
        archive.expand_selection(["missing"], [], [])
    with pytest.raises(ValueError, match="unknown archive market"):
        archive.expand_selection([], ["gate:NOT_HERE/USDT"], [])
    with pytest.raises(ValueError, match="Invalid include"):
        archive.expand_selection([], ["gate:TEST/USDT", "gate:TEST/USDT"], [])


def test_archive_synthetic_assumptions_and_leveraged_exclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _, snapshots = fixture(tmp_path, monkeypatch)
    (snapshots / "gate.json").unlink()
    archive = legacy.ArchiveCatalog(path.parent, snapshots)
    market = archive.detail("gate:TEST/USDT")
    assert market["precision_source"] == "synthetic"
    payload = {
        "name": "Synthetic",
        "market": market["id"],
        "start": market["first"],
        "end": market["end_exclusive"],
        "step_pct": "1",
        "take_profit_pct": "1",
        "order_quote": "10",
        "averaging": "0",
        "price_tick": "0.00000001",
        "amount_tick": "0.0001",
        "assert_spot": True,
        "confirm_synthetic": False,
    }
    with pytest.raises(ValueError, match="Confirm the declared synthetic"):
        legacy.settings(payload, market)
    confirmed = legacy.settings({**payload, "confirm_synthetic": True}, market)
    assert confirmed["precision_source"] == "synthetic"
    path.rename(path.with_name("gate_USDT_TEST3L.bin"))
    leveraged = legacy.ArchiveCatalog(path.parent, snapshots).detail("gate:TEST3L/USDT")
    with pytest.raises(ValueError, match="leveraged"):
        legacy.settings(
            {**payload, "market": leveraged["id"], "confirm_synthetic": True}, leveraged
        )


def test_worker_retains_verified_result_and_rejects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, source, snapshots = fixture(tmp_path, monkeypatch)
    folder = tmp_path / "run"
    folder.mkdir()
    script = folder / "engine"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "config=json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
        "output=pathlib.Path(config['output_dir'])\n"
        "(output/'trades.bin').write_bytes(b'')\n"
        "(output/'series.bin').write_bytes(b'')\n"
        "summary={'format_version':1,'series_format_version':2,'trades':0,'chart_points':0,"
        "'first_ts':config['start_ts'],'last_ts':config['end_ts']-60000}\n"
        "(output/'summary.json').write_text(json.dumps(summary))\n"
        "print(json.dumps(summary))\n"
    )
    script.chmod(0o700)
    (folder / "market_snapshot.json").write_bytes((snapshots / "gate.json").read_bytes())
    write_json(
        folder / "job.json",
        {
            "engine": "audited_n",
            "source": source,
            "settings": {
                "name": "Trial",
                "market": "gate:TEST/USDT",
                "start": "2025-01-01T00:00:00+00:00",
                "end": "2025-01-01T00:03:00+00:00",
                "step_pct": "1",
                "take_profit_pct": "1",
                "order_quote": "10",
                "averaging": "0",
            },
            "engine_sha256": sha256(script),
            "snapshot_sha256": sha256(folder / "market_snapshot.json"),
        },
    )
    legacy.worker(folder)
    assert json.loads((folder / "receipt.json").read_text())["status"] == "PASS"
    assert legacy.review(folder)["trades"] == 0
    (folder / "summary.json").write_text("{}")
    with pytest.raises(ValueError, match="artifact changed"):
        legacy.review(folder)
    assert source_path.read_bytes()  # original input remains untouched


def test_worker_refuses_changed_source_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, source, snapshots = fixture(tmp_path, monkeypatch)
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "engine").write_text("#!/bin/sh\nexit 0\n")
    (folder / "market_snapshot.json").write_bytes((snapshots / "gate.json").read_bytes())
    write_json(
        folder / "job.json",
        {
            "engine": "audited_n",
            "source": source,
            "settings": {},
            "engine_sha256": sha256(folder / "engine"),
            "snapshot_sha256": sha256(folder / "market_snapshot.json"),
        },
    )
    source_path.write_bytes(source_path.read_bytes() + b"X")
    legacy.worker(folder)
    receipt = json.loads((folder / "receipt.json").read_text())
    assert receipt["status"] == "FAIL"
    assert "identity changed" in receipt["error"]
    assert not (folder / "config.json").exists()


@pytest.mark.parametrize("engine", ["audited_n", "current_reference"])
@pytest.mark.parametrize(
    ("market", "filename"),
    [
        ("TEST/BTC", "gate_BTC_TEST.bin"),
        ("TEST/USDC", "gate_USDC_TEST.bin"),
        ("TEST/USD", "gate_USD_TEST.bin"),
        ("TEST/USDT", "gate_BTC_TEST.bin"),  # A misleading label cannot bypass the file quote.
    ],
)
def test_non_usdt_worker_never_invokes_either_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str, market: str, filename: str
) -> None:
    source_path, source, snapshots = fixture(tmp_path, monkeypatch)
    source.update(market=market, file=filename)
    folder = tmp_path / "blocked-run"
    folder.mkdir()
    (folder / "engine").write_text("This synthetic executable must never be invoked")
    (folder / "market_snapshot.json").write_bytes((snapshots / "gate.json").read_bytes())
    write_json(
        folder / "job.json",
        {
            "engine": engine,
            "source": source,
            "settings": {},
            "engine_sha256": sha256(folder / "engine"),
            "snapshot_sha256": sha256(folder / "market_snapshot.json"),
        },
    )
    execute = Mock(side_effect=AssertionError("No simulator process may start"))
    monkeypatch.setattr(legacy, "run_child", execute)
    before = source_path.read_bytes()
    legacy.worker(folder)
    receipt = json.loads((folder / "receipt.json").read_text())
    assert receipt["status"] == "FAIL" and "USDT-only" in receipt["error"]
    execute.assert_not_called()
    assert not (folder / "config.json").exists()
    assert source_path.read_bytes() == before


def chart_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source_path, source, snapshots = fixture(tmp_path, monkeypatch)
    folder = tmp_path / "chart-run"
    folder.mkdir()
    first = 1_735_689_600_000
    prices = [
        (10, 12, 8, 11),
        (11, 15, 9, 13),
        (13, 14, 7, 12),
        (12, 16, 10, 14),
        (14, 15, 12, 13),
    ]
    with (folder / "series.bin").open("wb") as stream:
        for index, price in enumerate(prices):
            account = (float(index),) * 4
            stream.write(
                struct.pack(
                    "<qq16d",
                    first + index * 60000,
                    first + (index + 1) * 60000,
                    *price,
                    *account,
                    *account,
                    *account,
                )
            )
    (folder / "trades.bin").write_bytes(
        struct.pack("<q7dII", first + 1, 10, 1, 10, 0.02, 1, 10, 0, 0, 1)
        + struct.pack("<q7dII", first + 120003, 12, 1, 12, 0.024, 0, 0, 2, 1, 1)
    )
    (folder / "engine").write_bytes(b"retained synthetic engine")
    (folder / "market_snapshot.json").write_bytes((snapshots / "gate.json").read_bytes())
    write_json(
        folder / "job.json",
        {
            "source": source,
            "settings": {
                "start": "2025-01-01T00:00:00+00:00",
                "end": "2025-01-01T00:05:00+00:00",
            },
        },
    )
    write_json(
        folder / "config.json",
        {
            "dataset": str(source_path),
            "dataset_sha256": source["sha256"],
            "start_ts": first,
            "end_ts": first + 300000,
        },
    )
    write_json(
        folder / "summary.json",
        {
            "format_version": 1,
            "series_format_version": 2,
            "trades": 2,
            "chart_points": 5,
            "first_ts": first,
            "last_ts": first + 240000,
        },
    )
    names = (
        "config.json",
        "engine",
        "market_snapshot.json",
        "summary.json",
        "trades.bin",
        "series.bin",
    )
    write_json(
        folder / "receipt.json",
        {
            "status": "PASS",
            "source_sha256": source["sha256"],
            "hashes": {name: sha256(folder / name) for name in names},
        },
    )
    return folder


def test_chart_data_preserves_trades_and_aggregates_ohlc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = chart_fixture(tmp_path, monkeypatch)
    result = legacy.chart_data(folder, max_bars=2)
    assert result["chart_points"] == 5
    assert result["display_points"] == 2
    assert result["aggregated"] is True
    assert result["series"][0]["price"] == [10, 15, 7, 12]
    assert result["series"][1]["price"] == [12, 16, 10, 13]
    assert result["series"][0]["start_ms"] == 1_735_689_600_000
    assert result["series"][0]["end_ms"] == 1_735_689_780_000
    assert [trade["side"] for trade in result["trades"]] == ["BUY", "SELL"]
    assert len(result["trades"]) == result["trades_count"] == 2


def test_chart_data_rejects_unverified_or_excessive_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = chart_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="cannot be truncated"):
        legacy.chart_data(folder, max_trades=1)
    (folder / "series.bin").write_bytes((folder / "series.bin").read_bytes() + b"X")
    with pytest.raises(ValueError, match="artifact changed"):
        legacy.chart_data(folder)


def test_chart_data_rejects_receipt_source_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = chart_fixture(tmp_path, monkeypatch)
    receipt = json.loads((folder / "receipt.json").read_text())
    receipt["source_sha256"] = "0" * 64
    (folder / "receipt.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="source hash disagrees"):
        legacy.chart_data(folder)


def test_chart_data_returns_all_markers_beyond_old_twenty_thousand_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = chart_fixture(tmp_path, monkeypatch)
    trade = (folder / "trades.bin").read_bytes()[:72]
    (folder / "trades.bin").write_bytes(trade * 20_001)
    summary = json.loads((folder / "summary.json").read_text())
    summary["trades"] = 20_001
    (folder / "summary.json").write_text(json.dumps(summary))
    receipt = json.loads((folder / "receipt.json").read_text())
    for name in ("summary.json", "trades.bin"):
        receipt["hashes"][name] = sha256(folder / name)
    (folder / "receipt.json").write_text(json.dumps(receipt))
    result = legacy.chart_data(folder)
    assert len(result["trades"]) == result["trades_count"] == 20_001
