"""The optimization comparison rejects changed exact results, including graph dates."""

from __future__ import annotations

import copy
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from scripts.research_sweep_differential import compare_results, edge_sources, result_fingerprint
from scripts.research_sweep_reference import reference


def test_fine_minimum_cost_fixture_requires_an_extra_lot(tmp_path: Path) -> None:
    sources = edge_sources(tmp_path / "edge-inputs")
    selected = [source for source in sources if source["symbol"].startswith("min_cost_")]
    result = reference(
        {
            "sources": selected,
            "configurations": [{"id": "ordinary", "step_pct": "1", "tp_pct": "10"}],
        }
    )["ordinary"]["pairs"]
    expected = {"min_cost_fine/USDT": (2, 3), "min_cost_exact/USDT": (1, 2)}
    for source in selected:
        state = result[source["id"]]
        initial_qty, halfway_qty = expected[source["symbol"]]
        assert state.trades[0]["price"] == 100
        assert state.trades[0]["quantity"] == initial_qty
        halfway = next(
            trade for trade in state.trades if trade["side"] == "BUY" and trade["price"] == 50
        )
        assert halfway["quantity"] == halfway_qty
        for trade in state.trades:
            if trade["side"] == "BUY":
                assert trade["cost"] >= Fraction(source["rules"]["min_cost"])
                assert trade["cost"] - trade["price"] < Fraction(source["rules"]["min_cost"])


def test_on_demand_keeps_exact_finances_without_pretending_graphs_exist(
    reports: tuple[Path, Path],
) -> None:
    original, deferred = reports
    summary = json.loads((deferred / "summary.json").read_text())
    summary.update(chart_mode="on_demand", charts_available=False)
    write_json(deferred / "summary.json", summary)
    (deferred / "series.jsonl").write_text("")
    assert compare_results(original, deferred, include_charts=False)["status"] == "PASS"
    with pytest.raises(ValueError, match="exact differential mismatch"):
        compare_results(original, deferred)
    summary["configurations"][0]["total"]["peak_spent_ms"] += 1
    write_json(deferred / "summary.json", summary)
    with pytest.raises(ValueError, match="exact differential mismatch"):
        compare_results(original, deferred, include_charts=False)


def test_on_demand_cannot_hide_unexpected_series(reports: tuple[Path, Path]) -> None:
    _, deferred = reports
    summary = json.loads((deferred / "summary.json").read_text())
    summary.update(chart_mode="on_demand", charts_available=False)
    write_json(deferred / "summary.json", summary)
    with pytest.raises(ValueError, match="unexpectedly contains chart rows"):
        result_fingerprint(deferred)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value) + "\n")


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def reports(tmp_path: Path) -> tuple[Path, Path]:
    metrics = {
        "realized_gross": "0",
        "unrealized_gross": "0",
        "fees": "0.2",
        "total_pnl": "-0.2",
        "spent": "100",
        "open_value": "100",
        "peak_spent": "100",
        "peak_spent_ms": 1000,
        "return_pct": "-0.2",
        "max_drawdown": "0.2",
        "max_drawdown_ms": 1000,
        "longest_hold_ms": 60000,
        "buy_count": 1,
        "sell_count": 0,
    }
    configs = [
        {
            "id": config,
            "step_pct": "1",
            "tp_pct": "10",
            "trade_hash": "a" * 64,
            "total": metrics,
            "venues": [{**metrics, "venue": "okx"}],
        }
        for config in ("one", "two")
    ]
    summary = {
        "schema": "grid-research-sweep-result-v1",
        "status": "COMPLETE_DIAGNOSTIC",
        "mode": "validation",
        "assumption_based": True,
        "certified_historical_performance": False,
        "source_count": 1,
        "venue_order": ["okx"],
        "configuration_count": 2,
        "input_records": 2,
        "processed_records": 2,
        "last_ms": 61000,
        "duration_stopped": False,
        "audit": {
            "bytes": 48,
            "records": 2,
            "gaps": 0,
            "missing_minutes": 0,
            "source_sha256_verified": True,
        },
        "configurations": configs,
        "build_sha256": "old",
        "plan_sha256": "old",
        "timings": {"wall_seconds": 3.5},
        "resources": {"threads": 1},
        "processed_blocks": 1,
    }
    pair_rows = [
        {
            **metrics,
            "config_id": config["id"],
            "source_id": "okx:TEST/USDT",
            "venue": "okx",
            "symbol": "TEST/USDT",
            "open_qty": "1",
            "last_price": "100",
            "last_price_ms": 61000,
            "trade_hash": "b" * 64,
        }
        for config in configs
    ]
    graphs = [
        {
            "config_id": config["id"],
            "scope_index": scope,
            "scope": "all" if scope == 0 else "venue:okx",
            "bin": bin_index,
            "first_ms": 1000 + 60000 * bin_index,
            "last_ms": 1000 + 60000 * bin_index,
            "pnl_first": "-0.2",
            "pnl_last": "-0.2",
            "pnl_min": "-0.2",
            "pnl_max": "-0.2",
            "pnl_min_ms": 1000 + 60000 * bin_index,
            "pnl_max_ms": 1000 + 60000 * bin_index,
            "spent_first": "100",
            "spent_last": "100",
            "spent_min": "100",
            "spent_max": "100",
            "spent_min_ms": 1000 + 60000 * bin_index,
            "spent_max_ms": 1000 + 60000 * bin_index,
        }
        for bin_index in range(2)
        for config in configs
        for scope in range(2)
    ]
    trades = [
        {
            "config_id": config["id"],
            "source_id": "okx:TEST/USDT",
            "sequence": 1,
            "timestamp_ms": 1000,
            "side": "BUY",
            "price": "100",
            "quantity": "1",
            "cost": "100",
            "fee": "0.2",
        }
        for config in configs
    ]
    old, new = tmp_path / "old", tmp_path / "new"
    for directory in (old, new):
        directory.mkdir()
        write_json(directory / "summary.json", summary)
        write_rows(directory / "pairs.jsonl", pair_rows)
        write_rows(directory / "series.jsonl", graphs)
        write_rows(directory / "trades.jsonl", trades)
    return old, new


def test_exact_comparison_ignores_only_execution_metadata(reports: tuple[Path, Path]) -> None:
    old, new = reports
    summary = json.loads((new / "summary.json").read_text())
    summary.update(
        build_sha256="new",
        plan_sha256="new",
        processed_blocks=8,
        timings={"wall_seconds": 0.1},
        resources={"threads": 32},
    )
    write_json(new / "summary.json", summary)
    result = compare_results(old / "summary.json", new)
    assert result["status"] == "PASS"
    assert result["fingerprint"]["pair_rows"] == 2


def test_graph_and_trade_interleaving_can_change_but_scope_order_cannot(
    reports: tuple[Path, Path],
) -> None:
    old, new = reports
    graph = sorted(
        rows(new / "series.jsonl"),
        key=lambda row: (row["config_id"], row["scope_index"], row["bin"]),
    )
    write_rows(new / "series.jsonl", graph)
    write_rows(new / "trades.jsonl", list(reversed(rows(new / "trades.jsonl"))))
    assert compare_results(old, new)["status"] == "PASS"
    graph[0], graph[1] = graph[1], graph[0]
    write_rows(new / "series.jsonl", graph)
    with pytest.raises(ValueError, match="nonchronological"):
        result_fingerprint(new)


def test_financial_strings_compare_as_exact_numbers(reports: tuple[Path, Path]) -> None:
    old, new = reports
    pair = rows(new / "pairs.jsonl")
    pair[0]["fees"] = "1/5"
    pair[0]["total_pnl"] = "-2/10"
    write_rows(new / "pairs.jsonl", pair)
    assert compare_results(old, new)["status"] == "PASS"
    pair[0]["fees"] = "0.200000000000000000000000000000000000001"
    write_rows(new / "pairs.jsonl", pair)
    with pytest.raises(ValueError, match="pairs_sha256"):
        compare_results(old, new)


@pytest.mark.parametrize(
    "filename,field,value",
    [
        ("pairs.jsonl", "peak_spent_ms", 1001),
        ("pairs.jsonl", "max_drawdown_ms", 1001),
        ("pairs.jsonl", "longest_hold_ms", 60001),
        ("pairs.jsonl", "trade_hash", "c" * 64),
        ("series.jsonl", "pnl_min_ms", 1001),
        ("series.jsonl", "spent_max_ms", 1001),
        ("series.jsonl", "last_ms", 1001),
        ("trades.jsonl", "fee", "0.21"),
    ],
)
def test_changed_money_hashes_and_first_extreme_dates_are_detected(
    reports: tuple[Path, Path],
    filename: str,
    field: str,
    value: Any,
) -> None:
    old, new = reports
    changed = rows(new / filename)
    changed[0][field] = value
    write_rows(new / filename, changed)
    with pytest.raises(ValueError, match="differential mismatch"):
        compare_results(old, new)


@pytest.mark.parametrize("level", ["total", "venues", "trade_hash"])
def test_all_summary_financial_levels_are_compared(reports: tuple[Path, Path], level: str) -> None:
    old, new = reports
    summary = json.loads((new / "summary.json").read_text())
    config = summary["configurations"][0]
    if level == "trade_hash":
        config[level] = "c" * 64
    elif level == "total":
        config[level]["fees"] = "0.21"
    else:
        config[level][0]["peak_spent_ms"] = 1001
    write_json(new / "summary.json", summary)
    with pytest.raises(ValueError, match="summary_sha256"):
        compare_results(old, new)


@pytest.mark.parametrize(
    "kind",
    [
        "pair_missing",
        "pair_duplicate",
        "scope_missing",
        "bin_duplicate",
        "sequence",
        "trace_missing",
    ],
)
def test_missing_or_duplicated_output_is_not_parity(reports: tuple[Path, Path], kind: str) -> None:
    old, new = reports
    if kind.startswith("pair"):
        values = rows(new / "pairs.jsonl")
        values.pop() if kind == "pair_missing" else values.append(copy.deepcopy(values[-1]))
        write_rows(new / "pairs.jsonl", values)
    elif kind.startswith("scope") or kind.startswith("bin"):
        values = rows(new / "series.jsonl")
        values = (
            [row for row in values if row["scope_index"] != 0]
            if kind == "scope_missing"
            else values + [copy.deepcopy(values[-1])]
        )
        write_rows(new / "series.jsonl", values)
    elif kind == "sequence":
        values = rows(new / "trades.jsonl")
        values[0]["sequence"] = 2
        write_rows(new / "trades.jsonl", values)
    else:
        (new / "trades.jsonl").unlink()
    with pytest.raises(ValueError):
        compare_results(old, new)


def test_financial_float_is_rejected_without_tolerance(reports: tuple[Path, Path]) -> None:
    _old, new = reports
    pair = rows(new / "pairs.jsonl")
    pair[0]["fees"] = 0.2
    write_rows(new / "pairs.jsonl", pair)
    with pytest.raises(ValueError, match="invalid exact"):
        result_fingerprint(new)
