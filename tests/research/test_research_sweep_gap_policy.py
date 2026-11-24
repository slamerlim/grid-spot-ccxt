from __future__ import annotations

import copy
import hashlib
import json
import os
import struct
import subprocess
from fractions import Fraction as F
from pathlib import Path
from typing import Any

import pytest

from scripts import research_sweep as runner
from scripts import research_sweep_matrix as matrix
from scripts.research_sweep_binary import engine_results
from scripts.research_sweep_reference import verify_result

VENUES = ["bingx", "gate", "htx", "kucoin", "okx"]
START = 1_704_067_200_000


@pytest.fixture
def engine() -> Path:
    value = os.environ.get("GRID_SWEEP_TEST_BINARY")
    if not value or not Path(value).is_file():
        pytest.skip("explicit compiled offline sweep executable required")
    return Path(value)


def source(
    directory: Path, venue: str, symbol: str, prices: list[int], offsets: list[int] | None = None
) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    offsets = offsets if offsets is not None else list(range(len(prices)))
    data = b"".join(
        struct.pack("<qffff", START + minute * 60_000, price, price, price, price)
        for minute, price in zip(offsets, prices, strict=True)
    )
    path = directory / f"{venue}_{symbol}.bin"
    path.write_bytes(data)
    return {
        "id": f"{venue}:{symbol}/USDT",
        "venue": venue,
        "symbol": f"{symbol}/USDT",
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "records": len(prices),
        "first_ms": START + offsets[0] * 60_000,
        "last_ms": START + offsets[-1] * 60_000,
        "rules": {
            "price_mode": "tick",
            "price_tick": "0.01",
            "amount_tick": "1",
            "min_amount": "1",
            "min_cost": "0",
        },
    }


def configurations() -> list[dict[str, Any]]:
    return [
        {
            "id": f"s20-t30-e{exchange:02d}-g{global_:02d}",
            "step_pct": "10",
            "tp_pct": "15",
            "exchange_tp_pct": "2" if exchange else None,
            "global_tp_pct": "2" if global_ else None,
        }
        for exchange, global_ in ((0, 0), (0, 4), (4, 0), (4, 4))
    ]


def plan_for(
    sources: list[dict[str, Any]], output: Path, mode: str = "validation"
) -> dict[str, Any]:
    plan = runner.make_plan(sources, output / "engine", mode, configurations=configurations())
    ids = sorted(row["id"] for row in sources)
    plan.update(
        matrix_kind=matrix.MATRIX_KIND,
        statistics_mode="monthly_v1",
        chart_mode="on_demand",
        max_timestamp_groups=1,
        block_bytes=4096,
        source_selection={
            "history_policy": "gap_free_own_span",
            "expected_venues": VENUES,
            "manifest_sha256": "ab" * 32,
            "source_ids": ids,
            "selected_source_ids": ids,
        },
    )
    return plan


def run_failure(engine: Path, plan: dict[str, Any], directory: Path) -> str:
    directory.mkdir(exist_ok=True)
    path = directory / "invalid-plan.json"
    runner.write_json(path, plan)
    completed = subprocess.run(
        [str(engine), "research-sweep", str(path)],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode != 0
    assert not (Path(plan["output_dir"]) / "summary.json").exists()
    return completed.stderr


def test_gap_free_exact_fraction_parallel_binary_and_recovery(tmp_path: Path, engine: Path) -> None:
    sources = [source(tmp_path / "sources", v, "A", [100, 110, 120, 80, 100]) for v in VENUES]
    sources.append(source(tmp_path / "sources", "bingx", "ENDED", [100]))
    sources.append(source(tmp_path / "sources", "okx", "LATER", [100, 105], [2, 3]))
    plan = plan_for(sources, tmp_path / "serial")
    serial = runner.engine_run(engine, plan, tmp_path / "serial")["summary"]
    assert verify_result(plan, Path(plan["output_dir"]))["pairs_checked"] == 28
    for row in serial["configurations"]:
        assert len(row["venues"]) == len(row["performance"]["venues"]) == 5
        assert F(row["total"]["total_pnl"]) == sum(F(v["total_pnl"]) for v in row["venues"])
        payload, checksum = matrix.encode_result(row)
        assert matrix.decode_result(payload, checksum) == row
    parallel_plan = dict(
        plan,
        threads=32,
        scheduler="dynamic",
        block_bytes=16 * 1024**2,
        output_dir=str(tmp_path / "parallel/engine"),
    )
    parallel = runner.engine_run(engine, parallel_plan, tmp_path / "parallel")["summary"]
    assert parallel["configurations"] == serial["configurations"]
    binary_plan = dict(
        parallel_plan,
        mode="matrix_batch",
        result_output="framed_msgpack_v1",
        output_dir=str(tmp_path / "binary/engine"),
    )
    binary = runner.engine_run(engine, binary_plan, tmp_path / "binary")["summary"]
    assert binary["status"] == "COMPLETE_BATCH"
    assert "configurations" not in binary
    assert list(engine_results(Path(binary_plan["output_dir"]), binary)) == serial["configurations"]
    stopped = dict(
        plan, checkpoint_seconds=0, stop_after_blocks=2, output_dir=str(tmp_path / "stopped/engine")
    )
    runner.engine_run(engine, stopped, tmp_path / "stopped", expected_codes=(3,))
    resumed = tmp_path / "resumed"
    resumed.mkdir()
    restored_plan = runner.copy_checkpoint(engine, tmp_path / "stopped", resumed)
    assert restored_plan["source_selection"] == plan["source_selection"]
    restored = runner.engine_run(engine, restored_plan, resumed)["summary"]
    assert restored["configurations"] == serial["configurations"]


def test_ended_open_position_blocks_its_scope_but_not_other_trading(
    tmp_path: Path, engine: Path
) -> None:
    sources = [source(tmp_path / "sources", v, "A", [100, 110, 120]) for v in VENUES]
    sources.append(source(tmp_path / "sources", "bingx", "ENDED", [100]))
    plan = plan_for(sources, tmp_path / "fresh")
    plan.update(trace=True, configurations=[configurations()[-1]])
    runner.engine_run(engine, plan, tmp_path / "fresh")
    assert verify_result(plan, Path(plan["output_dir"]))["pairs_checked"] == 6
    trades = [
        json.loads(line)
        for line in (Path(plan["output_dir"]) / "trades.jsonl").read_text().splitlines()
    ]
    assert not any(t.get("exit_scope") == "global" for t in trades)
    assert not any(
        t.get("exit_scope") == "venue" and t["source_id"].startswith("bingx:") for t in trades
    )
    assert {t["source_id"].split(":")[0] for t in trades if t.get("exit_scope") == "venue"} == set(
        VENUES[1:]
    )
    assert any(
        t["source_id"] == "bingx:A/USDT" and t["side"] == "SELL" and "exit_scope" not in t
        for t in trades
    )
    for trade in trades:
        if "exit_scope" in trade:
            assert trade["quote_timestamp_ms"] == trade["timestamp_ms"]
    # Compatibility: an old diagnostic retains its explicitly accepted stale
    # liquidation behavior. This is not retroactively reinterpreted by the flag.
    legacy = dict(plan, output_dir=str(tmp_path / "legacy/engine"))
    legacy.pop("source_selection")
    runner.engine_run(engine, legacy, tmp_path / "legacy")
    assert verify_result(legacy, Path(legacy["output_dir"]))["pairs_checked"] == 6
    old_trades = [
        json.loads(line)
        for line in (Path(legacy["output_dir"]) / "trades.jsonl").read_text().splitlines()
    ]
    assert any(
        t.get("exit_scope") == "global" and t["quote_timestamp_ms"] < t["timestamp_ms"]
        for t in old_trades
    )


def test_ended_empty_pair_does_not_block_current_global_exit(tmp_path: Path, engine: Path) -> None:
    sources = [source(tmp_path / "sources", v, "A", [100, 100, 110]) for v in VENUES]
    sources[0] = source(tmp_path / "sources", "bingx", "A", [100, 120])
    plan = plan_for(sources, tmp_path / "fresh")
    plan.update(trace=True, configurations=[configurations()[1]])
    runner.engine_run(engine, plan, tmp_path / "fresh")
    assert verify_result(plan, Path(plan["output_dir"]))["pairs_checked"] == 5
    trades = [
        json.loads(line)
        for line in (Path(plan["output_dir"]) / "trades.jsonl").read_text().splitlines()
    ]
    # The first pair closes through its ordinary TP and then reaches EOF empty.
    # Its missing future quote must not deny the other four positions' exit.
    exits = [t for t in trades if t.get("exit_scope") == "global"]
    assert exits
    assert all(t["quote_timestamp_ms"] == t["timestamp_ms"] for t in exits)
    assert not any(t["source_id"] == "bingx:A/USDT" for t in exits)


@pytest.mark.parametrize(
    "damage", ["gap", "long_gap", "block_gap", "duplicate", "backward", "hash"]
)
def test_gap_policy_reaudits_the_actual_source_bytes(
    tmp_path: Path, engine: Path, damage: str
) -> None:
    sources = [source(tmp_path / "sources", v, "A", [100, 100]) for v in VENUES]
    offsets = {
        "gap": [0, 2],
        "long_gap": [0, 10000],
        "block_gap": list(range(4096)) + [4097],
        "duplicate": [0, 0],
        "backward": [1, 0],
        "hash": [0, 1],
    }[damage]
    sources[0] = source(tmp_path / "sources", "bingx", "A", [100] * len(offsets), offsets)
    if damage == "hash":
        sources[0]["sha256"] = "00" * 32
    if damage == "backward":
        sources[0]["first_ms"], sources[0]["last_ms"] = START, START + 60_000
    error = run_failure(engine, plan_for(sources, tmp_path / "failed"), tmp_path / "failed")
    if damage in {"gap", "long_gap", "block_gap"}:
        assert "missing source minutes" in error
    elif damage == "hash":
        assert "SHA256 mismatch" in error
    else:
        assert "timestamp" in error


@pytest.mark.parametrize(
    "damage",
    [
        "policy",
        "manifest",
        "venues",
        "source_ids",
        "selected",
        "no_policy",
        "full",
        "actual_source",
        "actual_venue",
    ],
)
def test_five_venue_admission_cannot_weaken_old_modes(
    tmp_path: Path, engine: Path, damage: str
) -> None:
    sources = [source(tmp_path / "sources", v, "A", [100]) for v in VENUES]
    plan = plan_for(sources, tmp_path / "failed", "matrix_batch")
    if damage == "policy":
        plan["source_selection"]["history_policy"] = "unknown"
    elif damage == "manifest":
        plan["source_selection"]["manifest_sha256"] = "bad"
    elif damage == "venues":
        plan["source_selection"]["expected_venues"] = VENUES[:-1]
    elif damage == "source_ids":
        plan["source_selection"]["source_ids"] = list(
            reversed(plan["source_selection"]["source_ids"])
        )
    elif damage == "selected":
        plan["source_selection"]["selected_source_ids"] = ["different"]
    elif damage == "no_policy":
        plan.pop("source_selection")
    elif damage == "actual_source":
        plan["sources"][0]["id"] = "bingx:OTHER/USDT"
    elif damage == "actual_venue":
        plan["sources"].pop()
    else:
        plan["mode"] = "full"
    run_failure(engine, plan, tmp_path / "failed")


def test_policy_binding_changes_cannot_resume_a_checkpoint(tmp_path: Path, engine: Path) -> None:
    sources = [source(tmp_path / "sources", v, "A", [100, 110, 120]) for v in VENUES]
    plan = plan_for(sources, tmp_path / "stopped")
    plan.update(stop_after_blocks=2, checkpoint_seconds=0)
    runner.engine_run(engine, plan, tmp_path / "stopped", expected_codes=(3,))
    resumed = tmp_path / "resumed"
    resumed.mkdir()
    altered = copy.deepcopy(runner.copy_checkpoint(engine, tmp_path / "stopped", resumed))
    altered["source_selection"]["manifest_sha256"] = "cd" * 32
    assert "checkpoint plan or executable mismatch" in run_failure(engine, altered, resumed)


def test_original_seven_venue_batch_still_has_eight_scopes(tmp_path: Path, engine: Path) -> None:
    sources = [
        source(tmp_path / "sources", v, "A", [100, 110]) for v in [*VENUES, "bitfinex", "xt"]
    ]
    plan = plan_for(sources, tmp_path / "legacy", "matrix_batch")
    plan.pop("source_selection")
    summary = runner.engine_run(engine, plan, tmp_path / "legacy")["summary"]
    assert summary["status"] == "COMPLETE_BATCH"
    assert len(summary["configurations"][0]["performance"]["venues"]) == 7
    assert verify_result(plan, Path(plan["output_dir"]))["pairs_checked"] == 28
