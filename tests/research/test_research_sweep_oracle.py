"""The financial oracle rejects incomplete or substituted engine artifacts."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from scripts.research_sweep_reference import BASE_MS, fixture_sources, verify_result

START = 1704067200000


def test_fixture_templates_preserve_bytes_gaps_and_source_identity(tmp_path: Path) -> None:
    prices = (100, 100, 99, 98, 97, 35, 40, 80, 111, 120, 108, 106, 90, 150, 70, 70)
    sources = fixture_sources(tmp_path, count=64, minutes=35)
    for index, source in enumerate(sources):
        bars = [
            (BASE_MS + (minute + index % 3) * 60000, prices[(minute + index) % 16])
            for minute in range(35)
            if not (index % 3 == 1 and minute % 11 == 4)
        ]
        expected = b"".join(
            struct.pack("<qffff", ts, price, price + 1, price - 1, price) for ts, price in bars
        )
        assert Path(source["path"]).read_bytes() == expected
        assert source["sha256"] == hashlib.sha256(expected).hexdigest()
        assert source["records"] == len(bars)
        assert (source["first_ms"], source["last_ms"]) == (bars[0][0], bars[-1][0])
    assert len({source["path"] for source in sources}) == 64


@pytest.fixture
def report(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    """Two flat markets: each buys one coin at 100 and pays 0.20 USDT."""
    sources = []
    for venue, symbol in (("htx", "A"), ("gate", "B")):
        path = tmp_path / f"{symbol}.bin"
        path.write_bytes(struct.pack("<qffff", START, 100, 100, 100, 100))
        sources.append(
            {
                "id": f"{venue}:{symbol}/USDT",
                "venue": venue,
                "symbol": f"{symbol}/USDT",
                "path": str(path),
                "rules": {
                    "price_mode": "tick",
                    "price_tick": "0.01",
                    "amount_tick": "1",
                    "min_amount": "1",
                    "min_cost": "0",
                },
            }
        )
    configs = [
        {"id": "c1", "step_pct": "1", "tp_pct": "10"},
        {"id": "c2", "step_pct": "2", "tp_pct": "2"},
    ]
    plan = {"sources": sources, "configurations": configs, "trace": True}
    pairs, trades = [], []
    for config in configs:
        for source in sources:
            common = {"config_id": config["id"], "source_id": source["id"]}
            pairs.append(
                {
                    **common,
                    "realized_gross": "0",
                    "unrealized_gross": "0",
                    "fees": "0.2",
                    "total_pnl": "-0.2",
                    "spent": "100",
                    "peak_spent": "100",
                    "max_drawdown": "0.2",
                    "open_qty": "1",
                    "last_price_ms": START + 3000,
                    "longest_hold_ms": 3000,
                    "buy_count": 1,
                    "sell_count": 0,
                }
            )
            trades.append(
                {
                    **common,
                    "timestamp_ms": START,
                    "side": "BUY",
                    "price": "100",
                    "quantity": "1",
                    "cost": "100",
                    "fee": "0.2",
                    "sequence": 1,
                }
            )
    summary = {
        "configurations": [
            {
                "id": config["id"],
                "total": {
                    "total_pnl": "-0.4",
                    "fees": "0.4",
                    "peak_spent": "200",
                    "max_drawdown": "0.4",
                },
            }
            for config in configs
        ]
    }
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    write_lines(tmp_path / "pairs.jsonl", pairs)
    write_lines(tmp_path / "trades.jsonl", trades)
    return plan, tmp_path


def read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def write_lines(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_complete_hand_calculated_report_passes(report: tuple[dict[str, Any], Path]) -> None:
    assert verify_result(*report) == {"pairs_checked": 4, "trades_checked": 4}


@pytest.mark.parametrize("corruption", ["duplicate", "missing", "unknown"])
def test_pair_count_cannot_hide_missing_identity(
    report: tuple[dict[str, Any], Path], corruption: str
) -> None:
    plan, directory = report
    path = directory / "pairs.jsonl"
    rows = read_lines(path)
    if corruption == "duplicate":
        rows[-1] = rows[0]
    elif corruption == "missing":
        rows.pop()
    else:
        rows[-1]["source_id"] = "unknown:BTC/USDT"
    write_lines(path, rows)
    with pytest.raises(ValueError, match="pair"):
        verify_result(plan, directory)


@pytest.mark.parametrize("corruption", ["empty", "duplicate", "missing", "unknown"])
def test_summary_requires_every_configuration_exactly_once(
    report: tuple[dict[str, Any], Path], corruption: str
) -> None:
    plan, directory = report
    path = directory / "summary.json"
    summary = json.loads(path.read_text())
    rows = summary["configurations"]
    if corruption == "empty":
        rows.clear()
    elif corruption == "duplicate":
        rows[-1] = rows[0]
    elif corruption == "missing":
        rows.pop()
    else:
        rows[-1]["id"] = "unknown"
    path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="summary"):
        verify_result(plan, directory)


def test_requested_trace_cannot_be_silently_omitted(report: tuple[dict[str, Any], Path]) -> None:
    plan, directory = report
    (directory / "trades.jsonl").unlink()
    with pytest.raises(ValueError, match="required trade trace"):
        verify_result(plan, directory)
    plan["trace"] = False
    assert verify_result(plan, directory) == {"pairs_checked": 4, "trades_checked": 0}


@pytest.mark.parametrize("corruption", ["unknown", "duplicate", "fee", "sequence"])
def test_trace_rejects_extra_identity_wrong_fee_and_wrong_sequence(
    report: tuple[dict[str, Any], Path], corruption: str
) -> None:
    plan, directory = report
    path = directory / "trades.jsonl"
    rows = read_lines(path)
    if corruption == "unknown":
        rows.append(dict(rows[0], source_id="unknown:BTC/USDT"))
    elif corruption == "duplicate":
        rows.append(rows[0])
    elif corruption == "fee":
        rows[0]["fee"] = "0.25"
    else:
        rows[0]["sequence"] = 2
    write_lines(path, rows)
    with pytest.raises(ValueError, match="trade"):
        verify_result(plan, directory)


def test_trade_counters_are_financially_checked(report: tuple[dict[str, Any], Path]) -> None:
    plan, directory = report
    path = directory / "pairs.jsonl"
    rows = read_lines(path)
    rows[0]["buy_count"] = 2
    write_lines(path, rows)
    with pytest.raises(ValueError, match="buy_count"):
        verify_result(plan, directory)
