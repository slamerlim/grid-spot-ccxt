from __future__ import annotations

import copy
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from scripts import research_sweep as runner
from scripts import research_sweep_matrix as matrix
from scripts import research_sweep_sources as source_rules
from scripts import results_viewer_data as data
from scripts import results_viewer_jobs as jobs
from scripts.research_sweep_binary import engine_results
from scripts.research_sweep_reference import (
    EXPANDED_CONFIGURATION_COUNT,
    expanded_configuration,
    fixture_sources,
)
from scripts.research_sweep_statistics import calendar_fixtures, verify_performance


@pytest.fixture
def engine() -> Path:
    value = os.environ.get("GRID_SWEEP_TEST_BINARY")
    if not value or not Path(value).is_file():
        pytest.skip("explicit compiled offline sweep executable required")
    return Path(value)


@pytest.fixture
def parent_run(
    tmp_path: Path,
    engine: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, Any]]:
    mode = getattr(request, "param", "legacy")
    gap_free = mode in ("gap_free", "depth")
    sources = (
        [
            row
            for row in fixture_sources(tmp_path / "sources", count=7, minutes=48, gap_free=True)
            if row["venue"] in source_rules.GAP_FREE_VENUES
        ]
        if gap_free
        else calendar_fixtures(tmp_path / "sources")
    )
    config = (
        matrix._depth_configuration(EXPANDED_CONFIGURATION_COUNT + 29)
        if mode == "depth"
        else expanded_configuration(29)
    )
    plan = runner.make_plan(
        sources, tmp_path / "baseline/engine", "validation", configurations=[config]
    )
    plan.update(
        matrix_kind=matrix.MATRIX_KIND,
        statistics_mode="monthly_v1",
        chart_mode="on_demand",
        keep_results=False,
    )
    preparation = tmp_path / "preparation.json"
    runner.write_json(
        preparation,
        {
            "status": "READY",
            "schema": (
                source_rules.GAP_FREE_SCHEMA if gap_free else "grid-research-sweep-sources-v1"
            ),
            "sources": sources,
        },
    )
    selection = {
        "history_policy": source_rules.GAP_FREE_POLICY,
        "expected_venues": list(source_rules.GAP_FREE_VENUES),
        "source_ids": sorted(row["id"] for row in sources),
        "selected_source_ids": sorted(row["id"] for row in sources),
        "manifest_sha256": runner.sha256(preparation),
    }
    if gap_free:
        plan["source_selection"] = selection
        # Source-selector tests prove the full parent audit; this test isolates
        # preservation of its policy during a detailed replay of binary results.
        monkeypatch.setattr(source_rules, "verify_gap_free_selection", lambda _manifest: None)
    baseline = (
        engine if gap_free else Path(os.environ.get("GRID_SWEEP_BASELINE_BINARY", str(engine)))
    )
    outcome = runner.engine_run(baseline, plan, tmp_path / "baseline")
    expected = outcome["summary"]["configurations"][0]
    identity = {
        "matrix": {"canonical_ordinals": [29]},
        "preparation_sha256": runner.sha256(preparation),
        "code_sha256": "frozen-original-code",
        "settings": runner.execution_settings({"threads": 32}),
        "batch_size": 1024,
    }
    if mode == "depth":
        identity["matrix"] = {
            "configuration_plan_schema": matrix.DEPTH_PLAN_SCHEMA,
            "canonical_ordinals": [EXPANDED_CONFIGURATION_COUNT + 29],
            "configurations": [config],
        }
    if gap_free:
        identity["source_selection"] = selection
    parent = tmp_path / "parent"
    parent.mkdir()
    with closing(matrix.ResultCatalog(parent / "results.sqlite", identity, create=True)) as catalog:
        catalog.append(0, [expected], {"fixture": True})
        catalog.verify(1, complete=True)
    runner.write_json(
        parent / "matrix-study.json",
        {
            "identity": identity,
            "study_sha256": matrix._digest(identity),
            "preparation_path": str(preparation),
        },
    )
    publication = parent / "final-publication.json"
    runner.write_json(
        publication,
        {
            "status": "COMPLETE",
            "configuration_count": 1,
            "source_count": len(sources),
            "binary_results": str(parent / "results.bin"),
            "index": str(parent / "results.sqlite"),
            "binary_bytes": (parent / "results.bin").stat().st_size,
            "binary_sha256": runner.sha256(parent / "results.bin"),
            "index_sha256": runner.sha256(parent / "results.sqlite"),
            "study_sha256": matrix._digest(identity),
            "stage_observations": [{"wall_seconds": 1}],
        },
    )
    job_request = {
        "action": "matrix-detail",
        "publication": str(publication),
        "source_output": str(parent),
        "config_id": config["id"],
        "binary": str(engine),
        "replay_code_sha256": jobs.code_identity(),
        "replay_binary_sha256": runner.sha256(engine),
    }
    return publication, job_request


@pytest.mark.parametrize("parent_run", ["legacy", "gap_free", "depth"], indirect=True)
def test_detail_matches_original_and_fraction_reference_and_keeps_only_binary_finances(
    parent_run: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    engine: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication, request = parent_run
    monkeypatch.setenv("GRID_RESEARCH_HEAVY_LOCK", str(tmp_path / "heavy.lock"))
    before = {p.name: runner.sha256(p) for p in publication.parent.iterdir()}
    output = tmp_path / "detail"
    receipt = runner.worker(request, output)
    assert receipt["status"] == "COMPLETE", receipt
    assert receipt["verified_original_results"] is True
    assert not (output / "engine/pairs.jsonl").exists()
    assert not (output / "engine/series.jsonl").exists()
    with closing(jobs.DetailCatalog(output)) as catalog:
        result = list(catalog.rows("configuration"))[0]
        pairs, series = list(catalog.rows("pair")), list(catalog.rows("series"))
        archive = data.open_archive(publication)
        assert len(pairs) == archive.source_count
        assert {row["venue"] for row in pairs} == set(archive.venues)
        assert series and all("detail" in row for row in series)
        jobs.verify_drawdowns(result)
        assert catalog.metadata("complete") is True
    # Independent oracle reconstructs inventory, fees and both ordinary/forced exits.
    plan = runner.read_json(output / "plan.json")
    summary = runner.read_json(output / "engine/summary.json")
    summary["configurations"] = [result]
    oracle = tmp_path / "oracle"
    oracle.mkdir()
    runner.write_json(oracle / "summary.json", summary)
    checked = verify_performance(plan, oracle)["monthly_scope_rows_checked"]
    assert checked == (6 if "source_selection" in plan else 64)
    study = runner.read_json(publication.parent / "matrix-study.json")
    config = matrix.configuration_at(study["identity"], 0)
    assert plan["configurations"] == [config]
    data.check_configuration_depth(result, config)
    assert plan.get("source_selection") == study["identity"].get("source_selection")
    assert before == {p.name: runner.sha256(p) for p in publication.parent.iterdir()}
    bad = copy.deepcopy(result)
    bad["detail"]["total"][0]["max_drawdown"] = "999999/1"
    with pytest.raises(ValueError):
        jobs.verify_drawdowns(bad)


def test_binary_detail_rejects_truncation_corruption_and_uncommitted_tail(tmp_path: Path) -> None:
    with closing(jobs.DetailCatalog(tmp_path, create=True)) as catalog:
        catalog.put("pair", "okx", 0, {"value": "10000000000000000000000000001/7", "empty": None})
        catalog.commit({"fixture": True}, complete=True)
    with closing(jobs.DetailCatalog(tmp_path)) as catalog:
        assert list(catalog.rows("pair"))[0]["value"] == "10000000000000000000000000001/7"
    binary = tmp_path / "details.bin"
    raw = binary.read_bytes()
    for damaged in (raw[:-1], raw[:-1] + bytes([raw[-1] ^ 255])):
        binary.write_bytes(damaged)
        with closing(jobs.DetailCatalog(tmp_path)) as catalog, pytest.raises(ValueError):
            list(catalog.rows("pair"))
    binary.write_bytes(raw + b"uncommitted tail")
    with closing(jobs.DetailCatalog(tmp_path)) as catalog:
        assert catalog.metadata("committed_bytes") == len(raw)


def test_detail_stop_and_resume_preserve_months_series_order_and_hashes(
    parent_run: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    engine: Path,
) -> None:
    publication, request = parent_run
    archive, expected, study, sources = jobs._parent(publication, request["config_id"])
    plan = runner.make_plan(
        sources,
        tmp_path / "paused/engine",
        "validation",
        configurations=[expanded_configuration(29)],
    )
    plan.update(
        matrix_kind=matrix.MATRIX_KIND,
        detail_mode="monthly_drawdown_v1",
        statistics_mode="monthly_v1",
        result_output="framed_msgpack_v1",
        chart_mode="eager",
        max_timestamp_groups=1,
        block_bytes=4096,
        stop_after_blocks=4,
        checkpoint_seconds=0,
    )
    runner.engine_run(engine, plan, tmp_path / "paused", expected_codes=(3,))
    continuous_plan = dict(plan, output_dir=str(tmp_path / "continuous/engine"))
    continuous_plan.pop("stop_after_blocks")
    complete = runner.engine_run(engine, continuous_plan, tmp_path / "continuous")
    with (tmp_path / "paused/engine/series.bin").open("ab") as handle:
        handle.write(b"uncommitted junk")
    resumed = tmp_path / "resumed"
    resumed.mkdir()
    resumed_plan = runner.copy_checkpoint(engine, tmp_path / "paused", resumed)
    outcome = runner.engine_run(engine, resumed_plan, resumed)
    assert list(engine_results(Path(outcome["result_dir"]), outcome["summary"])) == list(
        engine_results(Path(complete["result_dir"]), complete["summary"])
    )
    assert (resumed / "engine/series.bin").read_bytes() == (
        tmp_path / "continuous/engine/series.bin"
    ).read_bytes()


def test_single_flight_submission_stop_and_explicit_retry(
    parent_run: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    publication, request = parent_run
    archive = data.open_archive(publication)
    root = tmp_path / "jobs-root"
    root.mkdir()
    runner.write_json(
        root / "worker.json",
        {"publication": str(publication), "code_sha256": "c", "binary_sha256": "b"},
    )
    job = jobs.submit(root, archive, request["config_id"])
    assert jobs.submit(root, archive, request["config_id"]) == job
    assert jobs.status(job)["status"] == "QUEUED"
    jobs.stop(job)
    assert jobs.progress(job)["status"] == "STOPPING"
    runner.write_json(job / "status.json", {"status": "INTERRUPTED", "attempts": []})
    with pytest.raises(runner.Blocked, match="явного"):
        jobs.submit(root, archive, request["config_id"])
    assert jobs.submit(root, archive, request["config_id"], resume=True) == job
    assert not (job / "stop-request.json").exists()
    with pytest.raises(ValueError):
        jobs.submit(root, archive, "../../../etc/passwd")


def test_confirmed_parent_or_replay_build_change_blocks_before_engine(
    parent_run: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, request = parent_run
    monkeypatch.setenv("GRID_RESEARCH_HEAVY_LOCK", str(tmp_path / "heavy.lock"))
    for index, changed in enumerate(
        (
            dict(request, parent_hashes=["changed"] * 3),
            dict(request, replay_binary_sha256="changed"),
            dict(request, replay_code_sha256="changed"),
        )
    ):
        output = tmp_path / f"blocked{index}"
        assert runner.worker(changed, output)["status"] == "BLOCKED"
        assert not (output / "execution-start.json").exists()
        assert not (output / "details.bin").exists()


def test_source_damage_or_financial_mismatch_never_publishes_details(
    parent_run: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication, request = parent_run
    monkeypatch.setenv("GRID_RESEARCH_HEAVY_LOCK", str(tmp_path / "heavy.lock"))
    original = jobs._parent

    def different_result(*args: Any) -> Any:
        archive, expected, study, sources = original(*args)
        expected["total"]["fees"] = "123456789/1"
        return archive, expected, study, sources

    monkeypatch.setattr(jobs, "_parent", different_result)
    output = tmp_path / "different"
    receipt = runner.worker(request, output)
    assert receipt["status"] == "FAIL" and "не совпал" in receipt["error"]
    assert not (output / "details.bin").exists()
    monkeypatch.setattr(jobs, "_parent", original)
    source = original(publication, request["config_id"])[3][0]
    path = Path(source["path"])
    raw = path.read_bytes()
    path.write_bytes(raw[:-1])
    output = tmp_path / "corrupt-source"
    receipt = runner.worker(request, output)
    assert receipt["status"] == "FAIL" and not (output / "details.bin").exists()


def test_python_publication_resource_limit_is_not_success(
    parent_run: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, request = parent_run
    monkeypatch.setenv("GRID_RESEARCH_HEAVY_LOCK", str(tmp_path / "heavy.lock"))
    measure = runner.resource_observation

    def constrained(root: Path, pid: int = 0) -> dict[str, int]:
        observed = measure(root, pid)
        if (root / "details.bin").exists():
            observed["artifact_bytes"] = runner.LIMITS["max_output_bytes"] + 1
        return observed

    monkeypatch.setattr(runner, "resource_observation", constrained)
    output = tmp_path / "resource-limited"
    assert runner.worker(request, output)["status"] == "RESOURCE_LIMIT"
    with closing(jobs.DetailCatalog(output)) as catalog:
        assert catalog.metadata("complete") is False


def test_verified_result_reopens_without_replay_and_rejects_corruption(
    parent_run: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication, request = parent_run
    monkeypatch.setenv("GRID_RESEARCH_HEAVY_LOCK", str(tmp_path / "heavy.lock"))
    job = tmp_path / "job"
    output = job / "attempts/0"
    receipt = runner.worker(request, output)
    assert receipt["status"] == "COMPLETE"
    runner.write_json(
        job / "status.json",
        {"status": "COMPLETE", "config_id": request["config_id"], "result_dir": str(output)},
    )
    archive = data.open_archive(publication)
    assert jobs.verified_receipt(job, archive) == receipt
    with closing(jobs.DetailCatalog(output)) as catalog:
        result = list(catalog.rows("configuration"))[0]
    totals, monthly = data.read_configuration(archive, request["config_id"])
    data.apply_drawdowns(totals, monthly, result["detail"])
    assert all(row["median_drawdown_status"] == "Рассчитано" for row in totals)
    assert all("max_drawdown_pct" in row for rows in monthly.values() for row in rows)
    before = (output / "execution-start.json").read_bytes()
    assert jobs.verified_receipt(job, archive) == receipt
    assert (output / "execution-start.json").read_bytes() == before
    with (output / "details.bin").open("ab") as handle:
        handle.write(b"uncommitted")
    with pytest.raises(ValueError, match="сумма"):
        jobs.verified_receipt(job, archive)


def test_retry_uses_earlier_checkpoint_and_fresh_restart_is_explicit(
    parent_run: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    publication, request = parent_run
    archive = data.open_archive(publication)
    root = tmp_path / "job-root"
    root.mkdir()
    runner.write_json(
        root / "worker.json",
        {"publication": str(publication), "code_sha256": "c", "binary_sha256": "b"},
    )
    job = jobs.job_for(root, archive, request["config_id"])
    first, failed = job / "attempts/0", job / "attempts/1"
    (first / "engine").mkdir(parents=True)
    failed.mkdir()
    (first / "engine/checkpoint.previous.json").write_text("fixture")
    saved = {
        "status": "RESOURCE_LIMIT",
        "config_id": request["config_id"],
        "attempts": [str(first), str(failed)],
    }
    runner.write_json(job / "status.json", saved)
    assert jobs.progress(job)["checkpoint_available"] is True
    jobs.submit(root, archive, request["config_id"], resume=True)
    assert runner.read_json(job / "request.json")["resume_from"] == str(first)
    runner.write_json(job / "status.json", saved)
    jobs.submit(root, archive, request["config_id"], resume=True, restart=True)
    assert runner.read_json(job / "request.json")["resume_from"] is None
    assert (first / "engine/checkpoint.previous.json").exists()


def test_sqlite_disk_full_remains_a_resource_failure(
    parent_run: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, request = parent_run
    monkeypatch.setenv("GRID_RESEARCH_HEAVY_LOCK", str(tmp_path / "heavy.lock"))

    def full(*args: Any) -> None:
        error = sqlite3.OperationalError("database or disk is full")
        error.sqlite_errorcode = sqlite3.SQLITE_FULL
        raise error

    monkeypatch.setattr(jobs, "_publish_details", full)
    output = tmp_path / "sql-full"
    receipt = runner.worker(request, output)
    assert receipt["status"] == "RESOURCE_LIMIT"
    assert "места" in receipt["error"]
    assert not (output / "details.bin").exists()
