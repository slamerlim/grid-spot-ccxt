from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from scripts import research_sweep as runner
from scripts import research_sweep_benchmark as benchmark
from scripts.research_sweep_reference import VENUES


def sources() -> list[dict[str, Any]]:
    return [
        {"id": venue, "venue": venue, "first_ms": runner.DAY_MS, "last_ms": 9 * runner.DAY_MS}
        for venue in VENUES
    ]


def test_warmup_uses_64_actual_timestamp_groups_with_gaps(tmp_path: Path) -> None:
    rows = []
    for index in (0, 1):
        path = tmp_path / f"source-{index}.bin"
        timestamps = [runner.DAY_MS + (i * 2 + index) * 60_000 for i in range(80)]
        path.write_bytes(b"".join(struct.pack("<qffff", t, 1, 1, 1, 1) for t in timestamps))
        rows.append({"path": str(path), "records": len(timestamps)})
    assert benchmark.warmup_boundary(rows, runner.DAY_MS, 2 * runner.DAY_MS) == (
        runner.DAY_MS + 64 * 60_000
    )
    with pytest.raises(runner.Blocked, match="warmup"):
        benchmark.warmup_boundary(rows, runner.DAY_MS, runner.DAY_MS + 60_000)


def test_compaction_is_limited_to_successful_owned_duplicate_outputs(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "pairs.jsonl").write_text("original pair result")
    (engine / "series.jsonl").write_text("original chart result")
    (engine / "summary.json").write_text("retained")
    runner.write_json(tmp_path / "execution.json", {"exit_code": 1, "result_dir": str(engine)})
    with pytest.raises(ValueError, match="unsuccessful"):
        benchmark.compact_success(tmp_path, {"hash": "checked"})
    assert (engine / "pairs.jsonl").exists()
    runner.write_json(tmp_path / "execution.json", {"exit_code": 0, "result_dir": str(engine)})
    benchmark.compact_success(tmp_path, {"hash": "checked"})
    receipt = runner.read_json(tmp_path / "compaction.json")
    assert receipt["status"] == "VERIFIED_DUPLICATE"
    assert {row["name"] for row in receipt["removed_files"]} == {"pairs.jsonl", "series.jsonl"}
    assert all(len(row["sha256"]) == 64 for row in receipt["removed_files"])
    assert not (engine / "pairs.jsonl").exists()
    assert (engine / "summary.json").read_text() == "retained"


def setup_execution(tmp_path: Path, monkeypatch: Any) -> tuple[Path, Path, list[dict[str, Any]]]:
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    baseline.write_text("old executable")
    candidate.write_text("new executable")
    calls: list[dict[str, Any]] = []

    def execute(binary: Path, plan: dict[str, Any], directory: Path, *_args: Any) -> dict[str, Any]:
        calls.append(plan)
        engine = Path(plan["output_dir"])
        engine.mkdir(parents=True)
        for name in ("pairs.jsonl", "series.jsonl"):
            (engine / name).write_text("result\n")
        seconds = 200.0 if binary == baseline else 100.0
        if plan["scheduler"] == "dynamic":
            seconds = {benchmark.MIB: 50, 4 * benchmark.MIB: 20, 16 * benchmark.MIB: 19.5}[
                plan["block_bytes"]
            ]
        summary = {
            "processed_records": 2_097_160,
            "timings": {"replay_seconds": seconds, "measured_replay_seconds": seconds - 1},
            "resources": {"measured_filled_blocks": 2},
        }
        if binary != baseline:
            summary["measured_records"] = 2_000_000
        result = {
            "exit_code": 0,
            "result_dir": str(engine),
            "wall_seconds": seconds + 500,
            "summary": summary,
        }
        runner.write_json(directory / "execution.json", result)
        runner.write_json(engine / "summary.json", summary)
        return result

    monkeypatch.setattr(runner, "engine_run", execute)
    monkeypatch.setattr(benchmark, "result_fingerprint", lambda _path: {"financial": "exact"})
    return baseline, candidate, calls


def test_full_roster_alternates_repeats_and_retains_only_two_detailed_results(
    tmp_path: Path, monkeypatch: Any
) -> None:
    baseline, candidate, calls = setup_execution(tmp_path, monkeypatch)
    output = tmp_path / "benchmark"
    result = benchmark.compare_binaries(
        baseline, candidate, sources(), output, warmup_until_ms=runner.DAY_MS + 60_000
    )
    assert len(calls) == 18
    assert all(len(plan["sources"]) == 7 and len(plan["configurations"]) == 540 for plan in calls)
    assert all(plan["threads"] == 32 for plan in calls)
    assert [row["label"] for row in result["measurements"][:6]] == [
        "baseline",
        "static",
        "dynamic",
        "static",
        "dynamic",
        "baseline",
    ]
    assert result["settings"]["scheduler"] == "dynamic"
    assert result["settings"]["block_bytes"] == 4 * benchmark.MIB
    assert result["speedup_total_replay"] == 10
    assert set(result["retained_full_results"]) == {"baseline", "buffer-4"}
    assert len(list(output.glob("*/engine/pairs.jsonl"))) == 2
    assert (output / "baseline-r0/engine/pairs.jsonl").exists()
    assert (output / "buffer-4-r0/engine/pairs.jsonl").exists()
    assert "warmup_until_ms" not in calls[0]
    assert calls[1]["warmup_until_ms"] == runner.DAY_MS + 60_000


def test_failed_parity_retains_diagnostic_output(tmp_path: Path, monkeypatch: Any) -> None:
    baseline, candidate, _ = setup_execution(tmp_path, monkeypatch)
    monkeypatch.setattr(
        benchmark, "result_fingerprint", lambda path: {"financial": path.parent.name}
    )
    output = tmp_path / "benchmark"
    with pytest.raises(ValueError, match="parity"):
        benchmark.compare_binaries(
            baseline, candidate, sources(), output, warmup_until_ms=runner.DAY_MS + 60_000
        )
    assert (output / "static-r0/engine/pairs.jsonl").exists()
    assert not (output / "static-r0/compaction.json").exists()
    assert (
        runner.read_json(output / "baseline-r0/compaction.json")["full_financial_result_retained"]
        is True
    )


def test_unfilled_buffers_cannot_win_calibration(tmp_path: Path, monkeypatch: Any) -> None:
    baseline, candidate, _ = setup_execution(tmp_path, monkeypatch)
    execute = runner.engine_run

    def unfilled(*args: Any) -> dict[str, Any]:
        result = execute(*args)
        result["summary"]["resources"]["measured_filled_blocks"] = 1
        return result

    monkeypatch.setattr(runner, "engine_run", unfilled)
    with pytest.raises(ValueError, match="two measured input blocks"):
        benchmark.compare_binaries(
            baseline,
            candidate,
            sources(),
            tmp_path / "benchmark",
            warmup_until_ms=runner.DAY_MS + 60_000,
        )


def test_matched_full_roster_evidence_is_required_for_settings(tmp_path: Path) -> None:
    proof = {
        "schema": "grid-research-sweep-benchmark-v1",
        "status": "PASS",
        "preparation_sha256": "sources",
        "binary_sha256": "binary",
        "code_sha256": "code",
        "sources": 4792,
        "configurations": 540,
        "settings": {
            "threads": 32,
            "scheduler": "static",
            "block_bytes": benchmark.MIB,
            "max_timestamp_groups": 4096,
        },
    }
    runner.write_json(tmp_path / "receipt.json", proof)
    settings, evidence = runner.benchmark_settings(tmp_path, "sources", "binary", "code", 4792)
    assert settings["threads"] == 32
    assert evidence["sha256"] == runner.sha256(tmp_path / "receipt.json")
    for field, value in (("binary_sha256", "changed"), ("sources", 4791), ("status", "FAIL")):
        runner.write_json(tmp_path / "receipt.json", {**proof, field: value})
        with pytest.raises(runner.Blocked, match="does not match"):
            runner.benchmark_settings(tmp_path, "sources", "binary", "code", 4792)


def test_short_calibration_can_lock_32_threads(tmp_path: Path, monkeypatch: Any) -> None:
    calls = []

    def execute(_binary: Path, plan: dict[str, Any], *_args: Any) -> dict[str, Any]:
        calls.append(plan)
        return {
            "wall_seconds": 90,
            "summary": {"configurations": [], "timings": {"replay_seconds": 90}},
        }

    monkeypatch.setattr(runner, "engine_run", execute)
    monkeypatch.setattr(runner, "exact_result_fingerprint", lambda _outcome: "same")
    result = runner.calibrate(
        tmp_path / "binary", [*sources(), sources()[0]], tmp_path / "cal", threads=32
    )
    assert result["settings"]["threads"] == 32
    assert {plan["threads"] for plan in calls} == {32}
    assert len(calls) == 14


def test_full_roster_benchmark_requires_matching_correctness_evidence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("GRID_RESEARCH_HEAVY_LOCK", str(tmp_path / "heavy.lock"))
    monkeypatch.setattr(runner, "check_resources", lambda *_args: None)
    monkeypatch.setattr(runner, "_engine_sources", lambda _manifest: sources())
    preparation = tmp_path / "prepared"
    runner.write_json(preparation / "manifest.json", {"status": "READY"})
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    baseline.write_text("baseline")
    candidate.write_text("candidate")

    def freeze(_preparation: Path, _binary: Path, output: Path) -> Path:
        study = output / "study-manifest.json"
        runner.write_json(study, {"code_sha256": {"fixture": "code"}})
        return study

    monkeypatch.setattr(runner, "freeze_study", freeze)
    result = benchmark.worker(
        {
            "source_output": str(preparation),
            "binary": str(candidate),
            "baseline_binary": str(baseline),
        },
        tmp_path / "run",
    )
    assert result["status"] == "BLOCKED"
    assert "matching validation" in result["error"]
    assert not (tmp_path / "run/workload.json").exists()


def test_completed_checkpoints_retire_without_losing_first_result(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    engine.mkdir()
    for name in (
        "pairs.jsonl",
        "series.jsonl",
        "checkpoint.latest.json",
        "checkpoint.previous.json",
    ):
        (engine / name).write_text(name)
    runner.write_json(tmp_path / "execution.json", {"exit_code": 0, "result_dir": str(engine)})
    fingerprint = {"financial": "same"}
    benchmark.compact_success(tmp_path, fingerprint, keep_result=True)
    assert (engine / "pairs.jsonl").exists()
    assert not (engine / "checkpoint.latest.json").exists()
    benchmark.compact_success(tmp_path, fingerprint)
    receipt = runner.read_json(tmp_path / "compaction.json")
    assert {row["name"] for row in receipt["removed_files"]} == {
        "pairs.jsonl",
        "series.jsonl",
        "checkpoint.latest.json",
        "checkpoint.previous.json",
    }
    assert not (engine / "pairs.jsonl").exists()


def setup_candidate_batch(
    tmp_path: Path, monkeypatch: Any
) -> tuple[Path, list[dict[str, Any]], dict[str, Any]]:
    """Fake the external engine boundary; exercise real batch receipt validation."""
    binary = tmp_path / "candidate"
    binary.write_text("candidate executable")
    calls: list[dict[str, Any]] = []
    controls: dict[str, Any] = {}

    def execute(
        executable: Path, plan: dict[str, Any], directory: Path, *_args: Any
    ) -> dict[str, Any]:
        assert executable == binary
        calls.append(plan)
        engine = Path(plan["output_dir"])
        engine.mkdir(parents=True)
        encoded_configs = json.dumps(
            plan["configurations"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        pair_bytes = b"fixture retained pair rows\n"
        fingerprint = {
            "pair_rows": len(plan["sources"]) * len(plan["configurations"]),
            "configuration_count": len(plan["configurations"]),
            "pairs_sha256": hashlib.sha256(pair_bytes).hexdigest(),
            "pairs_bytes": len(pair_bytes),
            "configurations_sha256": hashlib.sha256(encoded_configs).hexdigest(),
        }
        members = []
        for variant in plan["benchmark_variants"]:
            child = engine / variant["id"]
            child.mkdir()
            if variant["keep_results"]:
                (child / "pairs.jsonl").write_bytes(pair_bytes)
            small = (
                variant["block_bytes"] == benchmark.MIB and variant["reader_buffer_records"] == 4096
            )
            seconds = (10.4 if variant["scheduler"] == "static" else 10.0) if small else 12.0
            summary = {
                "schema": "grid-research-sweep-result-v1",
                "status": "COMPLETE_DIAGNOSTIC",
                "mode": "calibration",
                "source_count": len(plan["sources"]),
                "configuration_count": len(plan["configurations"]),
                "processed_records": 2_000_000,
                "measured_records": 1_900_000,
                "chart_mode": "on_demand",
                "charts_available": False,
                "fingerprint": dict(fingerprint),
                "configurations": plan["configurations"],
                "batch_source_sha256_verified": True,
                "timings": {
                    "replay_seconds": seconds,
                    "measured_replay_seconds": seconds - 1,
                    "wall_seconds": seconds + 2,
                },
                "resources": {
                    "threads": 32,
                    "scheduler": variant["scheduler"],
                    "block_bytes": variant["block_bytes"],
                    "reader_buffer_records": variant["reader_buffer_records"],
                    "audit_threads": plan["audit_threads"],
                    "max_timestamp_groups_per_block": variant["max_timestamp_groups"],
                    "measured_filled_blocks": 2,
                },
            }
            runner.write_json(child / "summary.json", summary)
            members.append(
                {
                    "id": variant["id"],
                    "repeat": variant["repeat"],
                    "result_dir": str(child),
                    "summary_path": str(child / "summary.json"),
                    "fingerprint": dict(fingerprint),
                }
            )
        report = {
            "schema": "grid-research-sweep-benchmark-batch-v1",
            "status": "COMPLETE_DIAGNOSTIC",
            "mode": "calibration",
            "source_count": len(plan["sources"]),
            "configuration_count": len(plan["configurations"]),
            "build_sha256": runner.sha256(binary),
            "source_sha256_verified": True,
            "source_audit_passes": 2,
            "fingerprint": dict(fingerprint),
            "variants": members,
        }
        if "mutate" in controls:
            controls["mutate"](engine, report)
        runner.write_json(engine / "batch-summary.json", report)
        outcome = {
            "exit_code": 0,
            "result_dir": str(engine),
            "wall_seconds": 500,
            "summary": report,
        }
        runner.write_json(directory / "execution.json", outcome)
        return outcome

    monkeypatch.setattr(runner, "engine_run", execute)
    monkeypatch.setattr(benchmark, "result_fingerprint", lambda _path: {"pair_rows": 7 * 540})
    return binary, calls, controls


def test_current_batch_uses_one_engine_and_three_rotated_full_roster_repeats(
    tmp_path: Path, monkeypatch: Any
) -> None:
    binary, calls, _controls = setup_candidate_batch(tmp_path, monkeypatch)
    output = tmp_path / "benchmark"
    result = benchmark.compare_current(
        binary, sources(), output, warmup_until_ms=runner.DAY_MS + 60_000
    )
    assert len(calls) == 1
    plan = calls[0]
    assert plan["threads"] == 32
    assert plan["chart_mode"] == "on_demand"
    assert len(plan["sources"]) == 7 and len(plan["configurations"]) == 540
    variants = plan["benchmark_variants"]
    assert len(variants) == 36 and len({row["id"] for row in variants}) == 36
    assert sum(row["keep_results"] for row in variants) == 1
    expected = {
        (scheduler, size * benchmark.MIB, reader)
        for scheduler in ("static", "dynamic")
        for size in (1, 4, 16)
        for reader in (4096, 16384)
    }
    orders = []
    for repeat in range(3):
        ordered = [
            (row["scheduler"], row["block_bytes"], row["reader_buffer_records"])
            for row in variants
            if row["repeat"] == repeat
        ]
        assert set(ordered) == expected and len(ordered) == 12
        orders.append(ordered)
    assert orders[0] != orders[1] != orders[2]
    assert len(result["measurements"]) == 36
    # The 4% slower static candidate wins the agreed five-percent tie.
    assert result["settings"]["scheduler"] == "static"
    assert result["settings"]["block_bytes"] == benchmark.MIB
    assert result["settings"]["reader_buffer_records"] == 4096
    assert result["settings"]["median_seconds"] == 10.4
    assert len(list(output.glob("batch/engine/*/pairs.jsonl"))) == 1
    assert Path(result["retained_full_result"]).joinpath("pairs.jsonl").is_file()


@pytest.mark.parametrize(
    "corruption, message",
    [
        ("missing_member", "coverage/order"),
        ("member_order", "coverage/order"),
        ("duplicate_member", "coverage/order"),
        ("incomplete_roster", "fingerprint|roster"),
        ("changed_fingerprint", "fingerprint|parity"),
        ("changed_records", "measured work|records"),
        ("zero_measured", "warmup|measured|records"),
        ("zero_processed", "warmup|measured|records"),
        ("missing_warmup", "warmup|measured|records"),
        ("one_filled_block", "two measured input blocks"),
        ("pending_member", "verification|complete"),
        ("pending_batch", "verification|complete|batch"),
        ("unverified_batch", "verification|complete|batch"),
        ("one_source_audit", "verification|complete|batch"),
        ("unverified_member", "verification|complete"),
        ("escaped_directory", "owned|outside"),
        ("reused_directory", "directory|identity|owned"),
        ("changed_pair_bytes", "pair bytes|fingerprint"),
        ("changed_retained_rollup", "rollup|fingerprint|configurations"),
        ("changed_compact_rollup", "rollup|fingerprint|configurations"),
        ("invalid_hash", "fingerprint|digest|hash"),
        ("wrong_threads", "settings|resources|threads"),
        ("wrong_buffer", "settings|resources|block_bytes"),
        ("negative_timing", "timing|duration|seconds"),
    ],
)
def test_current_batch_rejects_incomplete_or_corrupted_evidence(
    tmp_path: Path, monkeypatch: Any, corruption: str, message: str
) -> None:
    binary, _calls, controls = setup_candidate_batch(tmp_path, monkeypatch)

    def mutate(engine: Path, report: dict[str, Any]) -> None:
        members = report["variants"]
        if corruption == "missing_member":
            members.pop()
        elif corruption == "member_order":
            members[0], members[1] = members[1], members[0]
        elif corruption == "duplicate_member":
            members[1] = members[0]
        elif corruption == "incomplete_roster":
            members[0]["fingerprint"]["pair_rows"] -= 1
        elif corruption == "changed_fingerprint":
            members[1]["fingerprint"]["pairs_sha256"] = "1" * 64
        elif corruption == "invalid_hash":
            for member in members:
                member["fingerprint"]["pairs_sha256"] = "not-a-sha256"
        elif corruption == "pending_batch":
            report["status"] = "PENDING_BATCH_VERIFICATION"
        elif corruption == "unverified_batch":
            report["source_sha256_verified"] = False
        elif corruption == "one_source_audit":
            report["source_audit_passes"] = 1
        elif corruption == "escaped_directory":
            members[0]["result_dir"] = str(engine.parent)
        elif corruption == "reused_directory":
            members[1]["result_dir"] = members[0]["result_dir"]
        elif corruption == "changed_pair_bytes":
            Path(members[0]["result_dir"]).joinpath("pairs.jsonl").write_text("changed")
        else:
            index = 0 if corruption == "changed_retained_rollup" else 1
            path = Path(members[index]["result_dir"]) / "summary.json"
            summary = runner.read_json(path)
            if corruption == "changed_records":
                summary["processed_records"] -= 1
            elif corruption == "zero_measured":
                summary["measured_records"] = 0
            elif corruption == "zero_processed":
                summary["processed_records"] = 0
            elif corruption == "missing_warmup":
                summary["measured_records"] = summary["processed_records"]
            elif corruption == "one_filled_block":
                summary["resources"]["measured_filled_blocks"] = 1
            elif corruption == "pending_member":
                summary["status"] = "PENDING_BATCH_VERIFICATION"
            elif corruption == "unverified_member":
                summary["batch_source_sha256_verified"] = False
            elif corruption in ("changed_retained_rollup", "changed_compact_rollup"):
                summary["configurations"][0]["step_pct"] = "9.5"
            elif corruption == "wrong_threads":
                summary["resources"]["threads"] = 1
            elif corruption == "wrong_buffer":
                summary["resources"]["block_bytes"] = 32 * benchmark.MIB
            elif corruption == "negative_timing":
                summary["timings"]["replay_seconds"] = -1
            else:
                raise AssertionError(corruption)
            runner.write_json(path, summary)

    controls["mutate"] = mutate
    output = tmp_path / "benchmark"
    with pytest.raises(ValueError, match=message):
        benchmark.compare_current(binary, sources(), output, warmup_until_ms=runner.DAY_MS + 60_000)
    assert not (output / "benchmark.json").exists()
    # Failed validation keeps every available artifact for diagnosis.
    assert len(list(output.glob("batch/engine/*/summary.json"))) == 36


def test_current_batch_rejects_nonminute_warmup_before_engine(
    tmp_path: Path, monkeypatch: Any
) -> None:
    binary, calls, _controls = setup_candidate_batch(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="warmup|boundary"):
        benchmark.compare_current(
            binary, sources(), tmp_path / "benchmark", warmup_until_ms=runner.DAY_MS + 1
        )
    assert not calls
