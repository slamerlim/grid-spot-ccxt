from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from fractions import Fraction as F
from pathlib import Path
from typing import Any

import pytest

from scripts.research_sweep import (
    DAY_MS,
    HeavyJobLock,
    ResourceLimit,
    acquire_heavy_lock,
    best_window,
    check_resources,
    choose_sources,
    prior_evidence,
    release_heavy_lock,
    sha256,
    worker,
    write_json,
)
from scripts.research_sweep_reference import VENUES, Position, matrix, price_round


def rules(**changes: Any) -> dict[str, Any]:
    return {
        "price_mode": "tick",
        "price_tick": "0.01",
        "amount_tick": "1",
        "min_amount": "1",
        "min_cost": "0",
        **changes,
    }


def test_cancelled_matrix_worker_preserves_bound_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import research_sweep as runner
    from scripts import research_sweep_matrix

    monkeypatch.setenv("GRID_RESEARCH_HEAVY_LOCK", str(tmp_path / "lock"))
    monkeypatch.setattr(runner, "check_resources", lambda *_args: None)
    evidence = {"matrix-validate": {"sha256": "a" * 64, "receipt": "fixture"}}

    def stop(_request: dict[str, Any], output: Path) -> dict[str, Any]:
        write_json(
            output / "admission.json",
            {
                "status": "RUNNING",
                "evidence": evidence,
                "study_sha256": "b" * 64,
                "acceptance_policy": {"fixture": True},
                "resume_stage": "matrix-rehearsal",
            },
        )
        raise InterruptedError("stopped at a safe boundary")

    monkeypatch.setattr(research_sweep_matrix, "run_matrix", stop)
    receipt = worker({"action": "matrix-rehearsal"}, tmp_path / "job")
    assert receipt["status"] == "CANCELLED"
    assert receipt["evidence"] == evidence
    assert receipt["study_sha256"] == "b" * 64
    assert receipt["acceptance_policy"] == {"fixture": True}
    assert receipt["resume_stage"] == "matrix-rehearsal"


def test_hand_calculated_fee_and_strict_equality() -> None:
    state = Position(rules(), F(1, 100), F(1, 10))
    state.point(F(100), 0)
    state.point(F(99), 1000)
    assert state.buys == 1
    state.point(F(110), 2000)
    assert state.sells == 0
    state.point(F(111), 3000)
    assert state.sells == 1
    assert state.realized == 10
    assert state.fees == F("0.42")
    assert state.pnl == F("9.58")
    assert state.buy == F("108.90")
    assert state.floor == 44


def test_sell_anchor_and_unrounded_grid() -> None:
    state = Position(rules(), F(1, 100), F(8, 100))
    state.point(F(100), 0)
    state.point(F(111), 1000)
    assert state.buy == F("106.92")
    assert state.floor == F("43.20")
    state = Position(rules(price_tick="1"), F(15, 1000), F(1, 10))
    state.point(F(100), 0)
    assert state.raw == F("98.5")
    assert state.buy == 98
    state.point(F("97.5"), 1000)
    assert state.raw == F("97.0225")
    assert state.buy == 97


def test_duplicates_floor_and_open_end() -> None:
    state = Position(rules(price_tick="1"), F(5, 1000), F(1, 10))
    state.point(F(10), 0)
    state.point(F(3), 1000)
    prices = [trade["price"] for trade in state.trades]
    assert len(prices) == len(set(prices))
    assert min(prices) == 4
    assert state.buy == 0
    assert state.qty > 0
    assert state.fees == sum((trade["cost"] / 500 for trade in state.trades), F(0))
    assert state.metrics()["last_price_ms"] == 1000


def test_significant_precision_direction_and_decimal_cap() -> None:
    rule = rules(price_mode="significant", price_digits=5, max_price_decimals=8)
    assert price_round(F("99999.1"), rule) == 99999
    assert price_round(F("99999.1"), rule, True) == 100000
    assert price_round(F("0.000000015"), rule) == F("0.00000001")
    assert price_round(F("0.000000015"), rule, True) == F("0.00000002")


def test_matrix_and_round_robin_are_fixed() -> None:
    configs = matrix()
    assert len(configs) == len({row["id"] for row in configs}) == 540
    assert (configs[0]["step_pct"], configs[-1]["tp_pct"]) == ("0.5", "15.0")
    sources = [
        {"id": f"{venue}-{i}", "venue": venue} for venue in ("okx", "htx", "gate") for i in range(4)
    ]
    assert [row["id"] for row in choose_sources(sources, 5)] == [
        "okx-0",
        "htx-0",
        "gate-0",
        "okx-1",
        "htx-1",
    ]


def test_best_calendar_window_matches_brute_force() -> None:
    sources = [
        {"first_ms": 2 * DAY_MS + 60000, "last_ms": 7 * DAY_MS + 120000},
        {"first_ms": 5 * DAY_MS, "last_ms": 8 * DAY_MS},
        {"first_ms": 12 * DAY_MS, "last_ms": 12 * DAY_MS + 60000},
    ]
    scores = []
    for day in range(2, 13):
        start, end = day * DAY_MS, (day + 3) * DAY_MS
        score = sum(
            max(0, min(end, row["last_ms"] + 60000) - max(start, row["first_ms"]))
            for row in sources
        )
        scores.append((score, -start))
    best = -max(scores)[1]
    assert best_window(sources, 3) == (best, best + 3 * DAY_MS)


def test_global_heavy_lock_refuses_second_owner(tmp_path: Path) -> None:
    path = tmp_path / "heavy.lock"
    with HeavyJobLock(path), pytest.raises(BlockingIOError), HeavyJobLock(path):
        pass
    with HeavyJobLock(path):
        pass


def test_receipt_evidence_is_content_and_binary_bound(tmp_path: Path) -> None:
    before = tmp_path / "before"
    before.mkdir()
    receipt = {
        "status": "PASS",
        "action": "validate",
        "binary_sha256": "binary",
        "preparation_sha256": "sources",
        "code_sha256": "code",
        "evidence": {},
    }
    write_json(before / "receipt.json", receipt)
    evidence = prior_evidence(before, "sources", "binary", "code")
    assert list(evidence) == ["validate"]
    assert not prior_evidence(before, "sources", "different-binary", "code")
    assert not prior_evidence(before, "sources", "binary", "different-code")
    current = tmp_path / "current"
    current.mkdir()
    write_json(current / "receipt.json", {"status": "FAIL", "evidence": evidence})
    assert prior_evidence(current, "sources", "binary", "code") == evidence
    write_json(before / "receipt.json", dict(receipt, changed=True))
    assert not prior_evidence(current, "sources", "binary", "code")


def test_missing_prerequisites_cannot_start_full_run(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("GRID_RESEARCH_HEAVY_LOCK", str(tmp_path / "heavy.lock"))
    binary = tmp_path / "binary"
    binary.write_bytes(b"not executed")
    outcome = worker({"action": "full", "binary": str(binary)}, tmp_path / "run")
    assert outcome["status"] == "BLOCKED"
    assert "missing matching passed stages" in outcome["error"]
    assert not (tmp_path / "run/engine").exists()


def test_existing_receipt_is_never_overwritten(tmp_path: Path) -> None:
    write_json(tmp_path / "receipt.json", {"status": "COMPLETE"})
    digest = sha256(tmp_path / "receipt.json")
    with pytest.raises(ValueError, match="overwrite"):
        worker({"action": "prepare"}, tmp_path)
    assert sha256(tmp_path / "receipt.json") == digest
    assert json.loads((tmp_path / "receipt.json").read_text())["status"] == "COMPLETE"


def test_child_retains_heavy_lock_after_parent_handle_closes(tmp_path: Path) -> None:
    path = tmp_path / "heavy.lock"
    owner = acquire_heavy_lock(path)
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.readline()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        pass_fds=(owner.fileno(),),
    )
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "ready"
        release_heavy_lock(owner)
        with pytest.raises(BlockingIOError):
            acquire_heavy_lock(path)
        child.communicate("done\n", timeout=10)
        with HeavyJobLock(path):
            pass
    finally:
        owner.close()
        if child.poll() is None:
            child.kill()
            child.wait()


@pytest.mark.parametrize(
    "field,limit",
    [
        ("rss_bytes", "rss_bytes"),
        ("artifact_bytes", "max_output_bytes"),
        ("available_bytes", "min_available_bytes"),
        ("free_disk_bytes", "min_free_disk_bytes"),
    ],
)
def test_resource_bounds_are_distinct_terminal_failures(field: str, limit: str) -> None:
    observed = {
        "rss_bytes": 100,
        "artifact_bytes": 100,
        "available_bytes": 100,
        "free_disk_bytes": 100,
    }
    value = 101 if limit.startswith("min_") else 99
    with pytest.raises(ResourceLimit, match=field):
        check_resources(observed, {limit: value})


def test_new_artifact_budget_keeps_memory_and_free_space_guards(tmp_path: Path) -> None:
    from scripts import research_sweep as runner

    plan = runner.make_plan([], tmp_path, "validation")
    assert plan["runtime_limits"] == {
        "rss_bytes": 64 * 1024**3,
        "min_available_bytes": 32 * 1024**3,
        "min_free_disk_bytes": 32 * 1024**3,
        "max_output_bytes": 64 * 1024**3,
    }
    observed = {
        "rss_bytes": 1,
        "artifact_bytes": 64 * 1024**3,
        "available_bytes": 32 * 1024**3,
        "free_disk_bytes": 32 * 1024**3,
    }
    runner.check_resources(observed, plan["runtime_limits"])
    observed["artifact_bytes"] += 1
    with pytest.raises(ResourceLimit, match="artifact_bytes"):
        runner.check_resources(observed, plan["runtime_limits"])


def test_checkpoint_copy_retains_original_artifact_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import research_sweep as runner

    source = tmp_path / "source"
    target = tmp_path / "target"
    original = source / "engine"
    original.mkdir(parents=True)
    target.mkdir()
    (original / "checkpoint.latest.json").write_text("saved checkpoint")
    binary = tmp_path / "binary"
    binary.write_bytes(b"same executable")
    plan = runner.make_plan([], original, "validation")
    plan["runtime_limits"]["max_output_bytes"] = 32 * 1024**3
    runner.write_json(source / "plan.json", plan)
    runner.write_json(
        source / "execution-start.json",
        {"binary_sha256": sha256(binary), "plan_sha256": sha256(source / "plan.json")},
    )
    checked = []
    monkeypatch.setattr(runner, "check_resources", lambda _observed, limits: checked.append(limits))
    resumed = runner.copy_checkpoint(binary, source, target)
    assert resumed["runtime_limits"]["max_output_bytes"] == 32 * 1024**3
    assert "numa_interleave_nodes" not in resumed
    assert checked == [plan["runtime_limits"]]
    assert (target / "engine" / "checkpoint.latest.json").read_text() == "saved checkpoint"


def test_resumed_stage_counts_as_original_evidence(tmp_path: Path) -> None:
    write_json(
        tmp_path / "receipt.json",
        {
            "status": "COMPLETE",
            "action": "resume",
            "completed_stage": "rehearsal",
            "binary_sha256": "binary",
            "preparation_sha256": "sources",
            "code_sha256": "code",
        },
    )
    assert list(prior_evidence(tmp_path, "sources", "binary", "code")) == ["rehearsal"]


def test_nested_evidence_cannot_relabel_a_different_stage(tmp_path: Path) -> None:
    proof = tmp_path / "proof.json"
    write_json(
        proof,
        {
            "status": "PASS",
            "action": "validate",
            "binary_sha256": "binary",
            "preparation_sha256": "sources",
            "code_sha256": "code",
        },
    )
    write_json(
        tmp_path / "receipt.json",
        {
            "status": "BLOCKED",
            "evidence": {
                "calibrate": {
                    "receipt": str(proof),
                    "sha256": sha256(proof),
                    "binary_sha256": "binary",
                    "preparation_sha256": "sources",
                    "code_sha256": "code",
                }
            },
        },
    )
    assert not prior_evidence(tmp_path, "sources", "binary", "code")


def test_calibration_uses_replay_time_and_prefers_smaller_equivalent_settings(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from scripts import research_sweep as runner

    calls = []

    def execute(_binary: Path, plan: dict[str, Any], *_args: Any) -> dict[str, Any]:
        calls.append(plan)
        times = {1: 90, 2: 50, 4: 48, 8: 70, 16: 70, 24: 70, 32: 70}
        return {
            "wall_seconds": 1000 / plan["threads"],
            "summary": {
                "configurations": [],
                "timings": {"replay_seconds": times[plan["threads"]]},
            },
        }

    monkeypatch.setattr(runner, "engine_run", execute)
    monkeypatch.setattr(runner, "exact_result_fingerprint", lambda _outcome: "exact")
    sources = [
        {"id": str(index), "venue": venue, "first_ms": DAY_MS, "last_ms": 9 * DAY_MS}
        for index, venue in enumerate((*VENUES, "okx"))
    ]
    result = runner.calibrate(tmp_path / "binary", sources, tmp_path / "calibration")
    assert result["settings"]["threads"] == 2
    assert result["settings"]["block_bytes"] == 1024**2
    assert len(calls) == 32  # smoke + frozen pilot + 21 worker and 9 buffer measurements
    assert all(plan.get("max_records") == 4096 for plan in calls[1:])


@pytest.mark.parametrize("conflict", [False, True])
def test_calibration_adopts_verified_benchmark_settings_before_any_replay(
    tmp_path: Path, monkeypatch: Any, conflict: bool
) -> None:
    from scripts import research_sweep as runner

    monkeypatch.setenv("GRID_RESEARCH_HEAVY_LOCK", str(tmp_path / "heavy.lock"))
    binary = tmp_path / "binary"
    binary.write_bytes(b"fixture")
    parent = tmp_path / "parent"
    sources = [
        {"id": str(index), "venue": venue, "audit_accepted": True, "rules": rules()}
        for index, venue in enumerate((*VENUES, "okx"))
    ]
    write_json(
        parent / "manifest.json",
        {"schema": "grid-research-sweep-sources-v1", "status": "READY", "sources": sources},
    )
    proof_path = tmp_path / "validation.json"
    write_json(proof_path, {"acceptance_policy": runner.ACCEPTANCE_POLICY})
    monkeypatch.setattr(runner, "check_resources", lambda *_args: None)
    monkeypatch.setattr(
        runner, "freeze_study", lambda _prep, _binary, output: output / "study.json"
    )
    monkeypatch.setattr(runner, "study_code_identity", lambda _path: "code")
    monkeypatch.setattr(
        runner, "prior_evidence", lambda *_args: {"validate": {"receipt": str(proof_path)}}
    )
    chosen = runner.execution_settings(
        {"audit_threads": 8, "reader_buffer_records": 16384, "scheduler": "static"}
    )
    events = []

    def benchmark(*_args: Any) -> tuple[dict[str, Any], dict[str, str]]:
        events.append("benchmark")
        return chosen, {"receipt": "benchmark", "sha256": "proof"}

    def calibrate(*_args: Any, threads: int, settings: dict[str, Any]) -> dict[str, Any]:
        events.append("calibrate")
        assert threads == 32 and settings == chosen
        return {"settings": dict(settings, block_bytes=1024**2)}

    monkeypatch.setattr(runner, "benchmark_settings", benchmark)
    monkeypatch.setattr(runner, "calibrate", calibrate)
    request: dict[str, Any] = {
        "action": "calibrate",
        "binary": str(binary),
        "source_output": str(parent),
        "benchmark_output": str(tmp_path / "benchmark"),
    }
    if conflict:
        request["audit_threads"] = 4
    result = runner.worker(request, tmp_path / "output")
    if conflict:
        assert result["status"] == "BLOCKED" and events == ["benchmark"]
        assert "audit_threads differs" in result["error"]
    else:
        assert result["status"] == "PASS" and events == ["benchmark", "calibrate"]
        assert result["settings"] == chosen
        assert result["short_calibration_settings"]["block_bytes"] == 1024**2


def test_pair_chart_uses_completed_historical_horizon_not_wall_clock(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from scripts import research_sweep as runner

    parent, output = tmp_path / "parent", tmp_path / "chart"
    result = parent / "engine"
    row = {"source_id": "pair", "config_id": "config", "trade_hash": "same"}
    write_json(parent / "receipt.json", {"status": "COMPLETE", "result_dir": str(result)})
    write_json(
        parent / "plan.json",
        {
            "mode": "endurance",
            "sources": [{"id": "pair"}],
            "configurations": [{"id": "config"}],
            "duration_seconds": 3600,
            "max_records": 20,
        },
    )
    write_json(result / "summary.json", {"last_ms": 123000})
    (result / "pairs.jsonl").write_text(json.dumps(row) + "\n")

    def execute(_binary: Path, plan: dict[str, Any], *_args: Any) -> dict[str, Any]:
        assert plan["end_ms"] == 120001
        assert "duration_seconds" not in plan and "max_records" not in plan
        target = Path(plan["output_dir"])
        target.mkdir(parents=True)
        (target / "pairs.jsonl").write_text(json.dumps(row) + "\n")
        return {"result_dir": str(target)}

    monkeypatch.setattr(runner, "engine_run", execute)
    actual = runner.build_chart(
        tmp_path / "binary",
        {"source_output": str(parent), "config_id": "config", "scope": "pair:pair"},
        output,
    )
    assert actual["scope"] == "pair:pair"


def test_collect_keeps_saved_catalogues_alongside_new_snapshots(
    tmp_path: Path,
    monkeypatch: Any,
    async_sockets: None,
) -> None:
    from scripts import research_sweep_sources as sources

    monkeypatch.setenv("GRID_RESEARCH_HEAVY_LOCK", str(tmp_path / "heavy.lock"))
    saved, previous = tmp_path / "saved", tmp_path / "previous"
    monkeypatch.setattr(sources, "DEFAULT_SNAPSHOT_DIRS", (saved,))
    write_json(
        previous / "manifest.json",
        {"sources": [{"rules_snapshot": {"snapshot_path": str(tmp_path / "older/snapshot.json")}}]},
    )
    captured: list[Path] = []

    async def collect(_output: Path) -> dict[str, Any]:
        return {"offline_test": True}

    def prepare(
        _root: Path, output: Path, directories: list[Path], **_kwargs: Any
    ) -> dict[str, Any]:
        captured.extend(directories)
        manifest = {"status": "BLOCKED", "sources": [], "blockers": ["invented test"]}
        write_json(output / "manifest.json", manifest)
        return manifest

    monkeypatch.setattr(sources, "collect_snapshots", collect)
    monkeypatch.setattr(sources, "prepare_sources", prepare)
    result = worker({"action": "collect", "source_output": str(previous)}, tmp_path / "job")
    assert result["status"] == "BLOCKED"
    assert {saved, previous, tmp_path / "older", tmp_path / "job/snapshots"} <= set(captured)


def test_execution_settings_are_bounded_and_new_runs_generate_charts_on_request() -> None:
    from scripts import research_sweep as runner

    settings = runner.execution_settings({})
    assert settings["threads"] == 32 and settings["chart_mode"] == "on_demand"
    assert settings["numa_interleave_nodes"] is None
    for invalid in (
        {"audit_threads": 0},
        {"reader_buffer_records": -1},
        {"threads": True},
        {"chart_mode": "drop"},
        {"scheduler": "unbounded"},
    ):
        with pytest.raises(ValueError):
            runner.execution_settings(invalid)


def _mock_numa_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    launcher = tmp_path / "numactl"
    launcher.write_bytes(b"frozen NUMA launcher")
    launcher.chmod(0o700)
    read_text = Path.read_text
    observations = {
        "/proc/self/status": "Mems_allowed_list:\t1,5\n",
        "/sys/devices/system/node/online": "0-7\n",
        "/sys/devices/system/node/has_memory": "1,5\n",
    }

    def read(path: Path, *args: Any, **kwargs: Any) -> str:
        if str(path) in observations:
            return observations[str(path)]
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(shutil, "which", lambda _name: str(launcher))
    return launcher


@pytest.mark.parametrize(
    "value", [True, [], "", "all", "1-5", "+1", "1,1", "65536", "1;id", "1, 5"]
)
def test_numa_setting_rejects_implicit_or_invalid_nodes(value: Any) -> None:
    from scripts import research_sweep as runner

    with pytest.raises(ValueError, match="NUMA"):
        runner.execution_settings({"numa_interleave_nodes": value})


def test_explicit_numa_policy_is_bound_to_matrix_settings_and_exact_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import research_sweep as runner
    from scripts import research_sweep_matrix as matrix_runner

    launcher = _mock_numa_host(tmp_path, monkeypatch)
    binary, plan_path = tmp_path / "engine with spaces", tmp_path / "plan.json"
    settings = runner.execution_settings({"numa_interleave_nodes": "05,1"})
    assert settings["numa_interleave_nodes"] == "1,5" and settings["threads"] == 32
    study = {
        "identity": {"settings": settings, "runtime_limits": runner.LIMITS},
        "study_sha256": "study",
    }
    plan = matrix_runner._batch_plan(study, [], [], tmp_path)
    assert plan["numa_interleave_nodes"] == "1,5" and plan["threads"] == 32
    command, identity = runner._engine_command(binary, plan_path, plan)
    assert command == [
        str(launcher),
        "--interleave=1,5",
        "--",
        str(binary),
        "research-sweep",
        str(plan_path),
    ]
    assert identity == {
        "kind": "numactl_interleave",
        "nodes": "1,5",
        "path": str(launcher),
        "sha256": sha256(launcher),
    }
    monkeypatch.setattr(runner, "sha256", lambda path: str(path))
    first = matrix_runner._identity(tmp_path / "preparation", binary, settings, 1024)
    second = matrix_runner._identity(
        tmp_path / "preparation", binary, dict(settings, numa_interleave_nodes="1"), 1024
    )
    assert first != second


def test_numa_default_does_not_require_linux_or_a_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import research_sweep as runner

    def unexpected(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("disabled NUMA must not inspect the host or resolve a launcher")

    monkeypatch.setattr(Path, "read_text", unexpected)
    monkeypatch.setattr(shutil, "which", unexpected)
    binary, plan_path = tmp_path / "binary", tmp_path / "plan.json"
    plan: dict[str, Any] = {}
    assert runner._engine_command(binary, plan_path, plan) == (
        [str(binary), "research-sweep", str(plan_path)],
        {"kind": "direct"},
    )
    assert plan == {}


def test_requested_numa_policy_has_no_unavailable_or_missing_tool_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import research_sweep as runner

    _mock_numa_host(tmp_path, monkeypatch)
    for nodes in ("0", "1,6", "9"):
        with pytest.raises(runner.Blocked, match="unavailable"):
            runner._engine_command(
                tmp_path / "binary", tmp_path / "plan", {"numa_interleave_nodes": nodes}
            )
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(runner.Blocked, match="no fallback"):
        runner._engine_command(
            tmp_path / "binary", tmp_path / "plan", {"numa_interleave_nodes": "1,5"}
        )


@pytest.mark.parametrize("exit_code", [0, 23])
def test_engine_run_records_actual_numa_command_without_shell_or_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    from scripts import research_sweep as runner

    launcher = _mock_numa_host(tmp_path, monkeypatch)
    binary = tmp_path / "binary"
    binary.write_bytes(b"frozen engine")
    directory = tmp_path / "run"
    plan = runner.make_plan(
        [],
        directory / "engine",
        "validation",
        settings=runner.execution_settings({"numa_interleave_nodes": "1,5"}),
    )
    monkeypatch.setattr(runner, "check_resources", lambda *_args: None)
    monkeypatch.setattr(runner, "resource_observation", lambda *_args: {})
    monkeypatch.setattr(runner, "_ACTIVE_LOCK_FD", 91)
    calls = []

    class Child:
        pid = 123

        def poll(self) -> int:
            return exit_code

        def wait(self, **_kwargs: Any) -> int:
            return exit_code

    def launch(command: list[str], **kwargs: Any) -> Child:
        calls.append((command, kwargs))
        if exit_code == 0:
            Path(plan["output_dir"]).mkdir()
            runner.write_json(Path(plan["output_dir"]) / "summary.json", {"status": "COMPLETE"})
        return Child()

    monkeypatch.setattr(subprocess, "Popen", launch)
    if exit_code:
        with pytest.raises(ValueError, match="engine exit 23"):
            runner.engine_run(binary, plan, directory)
    else:
        runner.engine_run(binary, plan, directory)
    assert len(calls) == 1 and calls[0][0][0:3] == [str(launcher), "--interleave=1,5", "--"]
    assert not calls[0][1].get("shell", False) and calls[0][1]["pass_fds"] == (91,)
    start, end = (
        runner.read_json(directory / name) for name in ("execution-start.json", "execution.json")
    )
    assert start["command"] == end["command"] == calls[0][0]
    assert start["launcher"] == end["launcher"] and start["launcher"]["sha256"] == sha256(launcher)
    assert runner.read_json(directory / "plan.json") == plan


def test_numa_checkpoint_preserves_policy_and_rejects_changed_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import research_sweep as runner

    launcher = _mock_numa_host(tmp_path, monkeypatch)
    source, target, rejected = (tmp_path / name for name in ("source", "target", "rejected"))
    original = source / "engine"
    original.mkdir(parents=True)
    target.mkdir()
    rejected.mkdir()
    (original / "checkpoint.latest.json").write_text("checkpoint")
    binary = tmp_path / "binary"
    binary.write_bytes(b"frozen engine")
    plan = runner.make_plan(
        [],
        original,
        "validation",
        settings=runner.execution_settings({"numa_interleave_nodes": "1,5"}),
    )
    runner.write_json(source / "plan.json", plan)
    _, identity = runner._engine_command(binary, source / "plan.json", plan)
    runner.write_json(
        source / "execution-start.json",
        {
            "binary_sha256": sha256(binary),
            "plan_sha256": sha256(source / "plan.json"),
            "launcher": identity,
        },
    )
    monkeypatch.setattr(runner, "check_resources", lambda *_args: None)
    resumed = runner.copy_checkpoint(binary, source, target)
    assert resumed["numa_interleave_nodes"] == "1,5" and resumed["threads"] == 32
    assert {key for key in plan if resumed[key] != plan[key]} == {"output_dir", "resume"}
    launcher.write_bytes(b"different launcher")
    with pytest.raises(runner.Blocked, match="launcher or policy"):
        runner.copy_checkpoint(binary, source, rejected)
    assert not (rejected / "engine").exists()


def test_endurance_policy_preserves_legacy_hour_and_requires_two_checkpoints() -> None:
    from scripts import research_sweep as runner

    complete = {"timings": {"replay_seconds": 600}, "checkpoint_count": 2}
    runner.check_endurance(complete, runner.ACCEPTANCE_POLICY)
    with pytest.raises(ValueError, match="duration"):
        runner.check_endurance(complete, None)
    with pytest.raises(ValueError, match="checkpoints"):
        runner.check_endurance(dict(complete, checkpoint_count=1), runner.ACCEPTANCE_POLICY)
    runner.check_endurance({"timings": {"replay_seconds": 3600}}, None)


@pytest.mark.parametrize("scope", ["all", "venue:okx"])
def test_on_demand_chart_replays_whole_config_checks_results_and_reuses_cache(
    tmp_path: Path,
    monkeypatch: Any,
    scope: str,
) -> None:
    from scripts import research_sweep as runner

    parent = tmp_path / "parent"
    original = parent / "engine"
    source_rows = [{"id": "p1", "venue": "okx"}, {"id": "p2", "venue": "htx"}]
    pair_rows = [
        {
            "config_id": "c1",
            "source_id": source["id"],
            "trade_hash": "retained",
            "peak_spent_ms": 61000,
        }
        for source in source_rows
    ]
    config = {"id": "c1", "total": {"peak_spent_ms": 61000}, "venues": [], "trade_hash": "all"}
    write_json(parent / "receipt.json", {"status": "COMPLETE", "result_dir": str(original)})
    write_json(
        parent / "plan.json",
        {
            "mode": "rehearsal",
            "chart_mode": "on_demand",
            "sources": source_rows,
            "configurations": [{"id": "c1"}],
            "start_ms": 60000,
            "end_ms": 180000,
            "max_records": 100,
        },
    )
    write_json(
        original / "summary.json",
        {"configurations": [config], "last_ms": 123000, "processed_records": 4},
    )
    (original / "pairs.jsonl").write_text("".join(json.dumps(row) + "\n" for row in pair_rows))
    calls = []

    def execute(_binary: Path, plan: dict[str, Any], *_args: Any) -> dict[str, Any]:
        calls.append(plan)
        assert plan["sources"] == source_rows
        assert plan["configurations"] == [{"id": "c1"}] and plan["threads"] == 1
        assert plan["chart_mode"] == "eager" and plan["end_ms"] == 180000
        assert plan["max_records"] == 4
        target = Path(plan["output_dir"])
        write_json(target / "summary.json", {"configurations": [config]})
        (target / "pairs.jsonl").write_text("".join(json.dumps(row) + "\n" for row in pair_rows))
        (target / "series.jsonl").write_text('{"config_id":"c1","scope":"all"}\n')
        return {"result_dir": str(target), "summary": {"configurations": [config]}}

    monkeypatch.setattr(runner, "engine_run", execute)
    request = {"source_output": str(parent), "config_id": "c1", "scope": scope}
    first = runner.build_chart(tmp_path / "binary", request, tmp_path / "chart1")
    write_json(tmp_path / "chart1/receipt.json", dict(first, status="COMPLETE"))
    second = runner.build_chart(tmp_path / "binary", request, tmp_path / "chart2")
    assert len(calls) == 1 and second["cache_hit"]
    assert second["parent_result_identity"] == first["parent_result_identity"]
    # A changed cached artifact is not trusted or repaired in place.
    (tmp_path / "chart1/engine/series.jsonl").write_text("changed")
    third = runner.build_chart(tmp_path / "binary", request, tmp_path / "chart3")
    assert len(calls) == 2 and not third["cache_hit"]


def test_on_demand_chart_refuses_changed_pair_extreme(tmp_path: Path, monkeypatch: Any) -> None:
    from scripts import research_sweep as runner

    parent = tmp_path / "parent"
    engine = parent / "engine"
    row = {"config_id": "c1", "source_id": "p1", "peak_spent_ms": 61000}
    config = {"id": "c1", "trade_hash": "same"}
    write_json(parent / "receipt.json", {"status": "COMPLETE", "result_dir": str(engine)})
    write_json(
        parent / "plan.json",
        {
            "chart_mode": "on_demand",
            "sources": [{"id": "p1", "venue": "okx"}],
            "configurations": [{"id": "c1"}],
        },
    )
    write_json(
        engine / "summary.json",
        {"last_ms": 63000, "configurations": [config], "processed_records": 1},
    )
    (engine / "pairs.jsonl").write_text(json.dumps(row) + "\n")

    def execute(_binary: Path, plan: dict[str, Any], *_args: Any) -> dict[str, Any]:
        target = Path(plan["output_dir"])
        target.mkdir(parents=True)
        (target / "pairs.jsonl").write_text(json.dumps(dict(row, peak_spent_ms=62000)) + "\n")
        return {"summary": {"configurations": [config]}}

    monkeypatch.setattr(runner, "engine_run", execute)
    with pytest.raises(ValueError, match="exact pair"):
        runner.build_chart(
            tmp_path / "binary",
            {"source_output": str(parent), "config_id": "c1"},
            tmp_path / "chart",
        )


def test_stalled_progress_still_checks_resources_and_stop_deadline(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from scripts import research_sweep as runner

    binary = tmp_path / "binary"
    binary.write_bytes(b"synthetic executable identity")
    engine = tmp_path / "job/engine"
    write_json(
        engine / "progress.json", {"status": "RUNNING", "processed_records": 1, "input_records": 10}
    )
    clock = {"now": 100.0}
    observed_pids = []

    class Child:
        pid = 42
        killed = False
        terminated = False
        polls = 0

        def poll(self) -> int | None:
            self.polls += 1
            return -9 if self.killed else (7 if self.polls > 25 else None)

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, **_kwargs: Any) -> int:
            if "timeout" in _kwargs and not self.killed:
                advance(float(_kwargs["timeout"]))
                raise subprocess.TimeoutExpired("fixture", _kwargs["timeout"])
            return -9 if self.killed else 7

    child = Child()

    def observe(_root: Path, pid: int = 0) -> dict[str, int]:
        observed_pids.append(pid)
        return {
            "rss_bytes": runner.LIMITS["rss_bytes"] + 1 if pid else 0,
            "available_bytes": 128 * 1024**3,
            "free_disk_bytes": 128 * 1024**3,
            "artifact_bytes": 1000,
        }

    def advance(_seconds: float) -> None:
        clock["now"] += 5

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: child)
    monkeypatch.setattr(runner, "resource_observation", observe)
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(time, "sleep", advance)
    plan = runner.make_plan([], engine, "validation")
    with pytest.raises(ResourceLimit, match="rss_bytes"):
        runner.engine_run(binary, plan, tmp_path / "job", tmp_path / "job/progress.json")
    assert child.terminated and child.killed
    assert observed_pids.count(child.pid) >= 3
