"""Exact independent examples for the approved variable BUY depth matrix."""

from __future__ import annotations

import json
import os
from fractions import Fraction as F
from pathlib import Path
from typing import Any

import pytest

from scripts import research_sweep as runner
from scripts.research_sweep_reference import Position, verify_result
from scripts.research_sweep_statistics import calendar_fixtures, verify_performance

RULES = {
    "price_mode": "tick",
    "price_tick": "0.01",
    "amount_tick": "1",
    "min_amount": "1",
    "min_cost": "0",
}


@pytest.mark.parametrize(
    ("depth", "prices"),
    [
        (30, "100 90 81 72.9"),
        (35, "100 90 81 72.9 65.61"),
        (40, "100 90 81 72.9 65.61"),
        (45, "100 90 81 72.9 65.61 59.04"),
        (50, "100 90 81 72.9 65.61 59.04 53.14"),
        (55, "100 90 81 72.9 65.61 59.04 53.14 47.82"),
        (60, "100 90 81 72.9 65.61 59.04 53.14 47.82 43.04"),
    ],
)
def test_hand_calculated_purchase_levels_and_costs(depth: int, prices: str) -> None:
    state = Position(RULES, F(1, 10), F(15, 100), depth=F(depth, 100))
    state.point(F(100), 1_000)
    state.point(F(25), 2_000)
    expected = [F(value) for value in prices.split()]
    assert [trade["price"] for trade in state.trades] == expected
    assert state.floor == 100 - depth
    assert state.spent == sum(expected)
    assert state.qty == len(expected)
    assert state.fees == sum(expected) / 500
    assert state.pnl == 25 * len(expected) - sum(expected) * F(501, 500)
    assert state.buy == 0
    assert state.sell > 0  # The lower boundary never discards the owned position.


def test_exact_boundary_level_requires_strict_crossing_but_can_fill_below_floor() -> None:
    state = Position(dict(RULES, price_tick="10"), F(1, 10), F(1, 10), depth=F(3, 10))
    state.point(F(100), 1_000)
    state.point(F(70), 2_000)
    assert [trade["price"] for trade in state.trades] == [100, 90, 80]
    assert state.buy == state.floor == 70
    state.point(F(69), 3_000)
    assert [trade["price"] for trade in state.trades] == [100, 90, 80, 70]
    assert state.buy == 0
    assert state.pnl == F("-64.68")


def test_coin_sale_and_scope_sale_keep_their_different_anchor_rules() -> None:
    state = Position(RULES, F(1, 100), F(8, 100), depth=F(3, 10))
    state.point(F(100), 1_000)
    state.point(F(109), 2_000)
    assert state.trades[-1]["price"] == 108
    assert state.floor == F("75.60")
    assert state.buy == F("106.92")

    scoped = Position(RULES, F(1, 100), F(15, 100), depth=F(3, 10))
    scoped.point(F(100), 1_000)
    scoped.point(F(108), 2_000)
    scoped.aggregate_exit(2_000, "venue")
    assert scoped.qty == 0
    scoped.point(F(105), 3_000)
    assert scoped.trades[-1]["price"] == 105
    assert scoped.floor == F("73.50")


@pytest.mark.parametrize("depth", [0, 25, 29, 31, 59, 61, 65, 100])
def test_unapproved_depth_is_rejected(depth: int) -> None:
    with pytest.raises(ValueError, match="grid depth"):
        Position(RULES, F(1, 100), F(1, 10), depth=F(depth, 100))


@pytest.fixture
def engine() -> Path:
    configured = os.environ.get("GRID_SWEEP_TEST_BINARY")
    if not configured or not Path(configured).is_file():
        pytest.skip("explicit compiled offline sweep executable required")
    return Path(configured)


def _run(
    binary: Path,
    sources: list[dict[str, Any]],
    configurations: list[dict[str, Any]],
    directory: Path,
    threads: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = runner.make_plan(
        sources,
        directory / "engine",
        "validation",
        configurations=configurations,
        threads=threads,
        block_bytes=4096,
    )
    plan.update(
        matrix_kind="aggregate_tp_v1",
        statistics_mode="monthly_v1",
        trace=True,
        chart_mode="on_demand",
        scheduler="dynamic",
        max_timestamp_groups=1 if threads > 1 else 4096,
    )
    outcome = runner.engine_run(binary, plan, directory)
    assert outcome["exit_code"] == 0
    return plan, outcome["summary"]


def test_all_depths_match_fraction_and_monthly_oracles_with_parallelism(
    tmp_path: Path, engine: Path
) -> None:
    sources = calendar_fixtures(tmp_path / "sources")
    configs = [
        {
            "id": f"depth-{depth}-probe-{index}",
            "step_pct": step,
            "tp_pct": "5",
            "exchange_tp_pct": "12",
            "global_tp_pct": "12",
            "grid_depth_pct": str(depth),
        }
        for depth in range(30, 61, 5)
        for index, step in enumerate(("0.5", "1.5", "5.5", "6.5", "10"))
    ]
    baseline = None
    for threads in (1, 32):
        directory = tmp_path / f"threads-{threads}"
        plan, summary = _run(engine, sources, configs, directory, threads)
        assert verify_result(plan, directory / "engine")["pairs_checked"] == len(sources) * 35
        assert verify_performance(plan, directory / "engine")["configurations"] == 35
        for config, result in zip(configs, summary["configurations"], strict=True):
            assert F(result["grid_depth_pct"]) == F(config["grid_depth_pct"])
        if baseline is None:
            baseline = summary["configurations"]
        else:
            assert summary["configurations"] == baseline


def test_default_and_explicit_sixty_keep_frozen_financial_results(
    tmp_path: Path, engine: Path
) -> None:
    configured = os.environ.get("GRID_SWEEP_BASELINE_BINARY")
    if not configured or not Path(configured).is_file():
        pytest.skip("explicit frozen baseline executable required")
    sources = calendar_fixtures(tmp_path / "sources")
    configurations = [
        {
            "id": "s12-t10-e08-g12",
            "step_pct": "6",
            "tp_pct": "5",
            "exchange_tp_pct": "4",
            "global_tp_pct": "6",
        },
        {
            "id": "s02-t08-e10-g08",
            "step_pct": "1",
            "tp_pct": "4",
            "exchange_tp_pct": "5",
            "global_tp_pct": "4",
        },
    ]
    _, old = _run(Path(configured), sources, configurations, tmp_path / "old", 1)
    for name, configs in (
        ("default", configurations),
        ("explicit", [dict(row, grid_depth_pct="60") for row in configurations]),
    ):
        _, current = _run(engine, sources, configs, tmp_path / name, 32)
        rows = json.loads(json.dumps(current["configurations"]))
        for row in rows:
            if name == "explicit":
                assert F(row.pop("grid_depth_pct")) == 60
        assert rows == old["configurations"]
        assert (tmp_path / name / "engine" / "pairs.jsonl").read_bytes() == (
            tmp_path / "old" / "engine" / "pairs.jsonl"
        ).read_bytes()
        # Different block sizes interleave configurations differently in the
        # diagnostic trace. Every trade's bytes and per-config order must match.
        old_trades = (tmp_path / "old" / "engine" / "trades.jsonl").read_bytes().splitlines()
        new_trades = (tmp_path / name / "engine" / "trades.jsonl").read_bytes().splitlines()
        assert len(new_trades) == len(old_trades)
        for config in configurations:
            assert [row for row in new_trades if json.loads(row)["config_id"] == config["id"]] == [
                row for row in old_trades if json.loads(row)["config_id"] == config["id"]
            ]
