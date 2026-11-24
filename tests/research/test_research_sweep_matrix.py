from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from scripts import research_sweep as runner
from scripts import research_sweep_matrix as matrix
from scripts.research_sweep_reference import expanded_configuration, fixture_sources


def metrics(multiplier: int = 1) -> dict[str, Any]:
    return {
        "buy_count": multiplier,
        "fees": str(Fraction(multiplier, 100)),
        "longest_hold_ms": 1000,
        "max_drawdown": "0",
        "max_drawdown_ms": 0,
        "open_value": str(2 * multiplier),
        "peak_spent": str(5 * multiplier),
        "peak_spent_ms": 1,
        "realized_gross": str(3 * multiplier),
        "return_pct": "399/5",
        "sell_count": 0,
        "spent": str(multiplier),
        "total_pnl": str(Fraction(399 * multiplier, 100)),
        "unrealized_gross": str(multiplier),
    }


def result(index: int = 0, venues: tuple[str, ...] = ("okx",)) -> dict[str, Any]:
    return {
        **expanded_configuration(index),
        "total": metrics(len(venues)),
        "venues": [dict(metrics(), venue=venue) for venue in venues],
        "trade_hash": "ab" * 32,
        "pair_fingerprint": {"sha256": "cd" * 32, "rows": len(venues), "bytes": 600},
        "aggregate_cycles": {"total": {"peak_spent": "1/3", "completed_count": 2}},
    }


def test_binary_codec_preserves_every_exact_value_and_dynamic_extension() -> None:
    row = result(29)
    row["total"]["fees"] = "123456789012345678901234567890123456789/9876543210987654321"
    row["total"]["new_metric"] = {"exact": "1/7"}
    row["extension"] = [None, 2**60, "тест"]
    payload, checksum = matrix.encode_result(row)
    assert len(payload) < len(json.dumps(row).encode())
    assert not payload.startswith(b"{")
    assert matrix.decode_result(payload, checksum) == row
    matrix._verify_stored_payload(payload, checksum, row["id"])
    with pytest.raises(ValueError, match="identity"):
        matrix._verify_stored_payload(payload, checksum, "other-configuration")
    with pytest.raises(ValueError, match="checksum"):
        matrix.decode_result(payload, bytes(32))
    with pytest.raises(ValueError, match="compressed"):
        matrix.decode_result(payload + b"uncommitted tail", checksum)
    with pytest.raises(ValueError, match="compressed"):
        matrix.decode_result(payload[:-2], checksum)


def test_binary_decoder_bounded_before_unpacking() -> None:
    raw = b"x" * (matrix.MAX_PAYLOAD_BYTES + 1)
    with pytest.raises(ValueError, match="oversized"):
        matrix.decode_result(zlib.compress(raw), hashlib.sha256(raw).digest())


def test_atomic_batch_append_rollback_and_exact_pagination(tmp_path: Path) -> None:
    path = tmp_path / "results.sqlite"
    catalog = matrix.ResultCatalog(path, {"frozen": "identity"}, create=True)
    try:
        catalog.append(0, [result(0), result(1)], {"records": 100})
        changed = result(3)
        changed["tp_pct"] = "99"
        with pytest.raises(ValueError, match="configuration differs"):
            catalog.append(2, [result(2), changed], {"records": 100})
        assert catalog.count() == 2
        assert catalog.verify(2)["configuration_count"] == 2
        page = matrix.page_results(path, limit=1)
        assert page["configurations"] == [result(0)]
        assert not page["complete"]
        assert not page["study_complete"]
        assert page["next_cursor"] == 1
        last = matrix.page_results(path, cursor=page["next_cursor"], limit=1)
        assert last["configurations"] == [result(1)]
        assert last["complete"] and last["next_cursor"] is None
        assert matrix.page_results(path, config_id=result(1)["id"])["configurations"] == [result(1)]
        with pytest.raises(ValueError, match="append"):
            catalog.append(0, [result(0)], {})
    finally:
        catalog.close()
    with pytest.raises(ValueError, match="identity differs"):
        matrix.ResultCatalog(path, {"frozen": "changed"})
    with pytest.raises(ValueError, match="replace"):
        matrix.ResultCatalog(path, {"frozen": "identity"}, create=True)


def test_corrupt_catalog_payload_and_coverage_fail(tmp_path: Path) -> None:
    catalog = matrix.ResultCatalog(tmp_path / "results.sqlite", {}, create=True)
    try:
        catalog.append(0, [result()], {})
        with pytest.raises(ValueError, match="count mismatch"):
            catalog.verify(2)
        with catalog.connection:
            catalog.connection.execute("UPDATE configurations SET sha256=?", (bytes(32),))
        with pytest.raises(ValueError, match="checksum"):
            catalog.verify(1)
    finally:
        catalog.close()


def test_catalog_refuses_symlink_and_unbounded_pages(tmp_path: Path) -> None:
    catalog = matrix.ResultCatalog(tmp_path / "results.sqlite", {}, create=True)
    catalog.close()
    link = tmp_path / "link.sqlite"
    link.symlink_to(tmp_path / "results.sqlite")
    with pytest.raises(ValueError, match="symlink"):
        matrix.page_results(link)
    for value in (0, 201, True):
        with pytest.raises(ValueError, match="bounded"):
            matrix.page_results(tmp_path, limit=value)


def test_cleanup_requires_exact_owned_scratch_marker(tmp_path: Path) -> None:
    directory = tmp_path / "retained"
    directory.mkdir()
    (directory / "old-result").write_text("keep")
    with pytest.raises(ValueError, match="unowned"):
        matrix._cleanup_committed_batch(directory, "study")
    runner.write_json(directory / "matrix-scratch.json", {"study_sha256": "different"})
    with pytest.raises(ValueError, match="unowned"):
        matrix._cleanup_committed_batch(directory, "study")
    assert (directory / "old-result").read_text() == "keep"


def test_matrix_identity_binds_artifact_budget_and_batch_uses_bound_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preparation = tmp_path / "preparation.json"
    binary = tmp_path / "binary"
    preparation.write_text("frozen sources")
    binary.write_bytes(b"frozen executable")
    settings = runner.execution_settings({})
    identity = matrix._identity(preparation, binary, settings, 1024)
    assert identity["runtime_limits"]["max_output_bytes"] == 64 * 1024**3
    saved = {**identity, "runtime_limits": {**identity["runtime_limits"]}}
    monkeypatch.setattr(runner, "LIMITS", {**runner.LIMITS, "max_output_bytes": 32 * 1024**3})
    changed = matrix._identity(preparation, binary, settings, 1024)
    assert matrix._digest(identity) != matrix._digest(changed)
    study = {"identity": saved, "study_sha256": matrix._digest(saved)}
    plan = matrix._batch_plan(study, [], [expanded_configuration(0)], tmp_path / "batch")
    assert plan["runtime_limits"] == saved["runtime_limits"]
    assert plan["runtime_limits"]["max_output_bytes"] == 64 * 1024**3


def test_batch_validation_checks_accounting_and_pair_coverage() -> None:
    configs = [expanded_configuration(0)]
    sources = [{"venue": "okx", "records": 2}]
    summary: dict[str, Any] = {
        "status": "COMPLETE_BATCH",
        "configurations": [result()],
        "source_count": 1,
        "processed_records": 2,
        "audit": {"source_sha256_verified": True, "records": 2},
    }
    matrix._verify_summary(summary, configs, sources, full_history=True)
    bindings = [{**result()["pair_fingerprint"], "config_id": result()["id"]}]
    summary["fingerprint"] = {
        "pairs_hash_scope": "per_configuration",
        "pairs_fingerprints_sha256": matrix._digest(bindings),
        "pairs_bytes": bindings[0]["bytes"],
        "pair_rows": 1,
        "configuration_count": 1,
    }
    matrix._verify_summary(summary, configs, sources, full_history=True)
    summary["fingerprint"]["pairs_fingerprints_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="manifest"):
        matrix._verify_summary(summary, configs, sources, full_history=True)
    summary.pop("fingerprint")
    summary["configurations"][0]["total"]["fees"] = "2/100"
    with pytest.raises(ValueError, match="PnL"):
        matrix._verify_summary(summary, configs, sources, full_history=True)
    summary["configurations"] = [result()]
    summary["configurations"][0]["pair_fingerprint"]["rows"] = 0
    with pytest.raises(ValueError, match="fingerprint"):
        matrix._verify_summary(summary, configs, sources, full_history=True)


def test_completed_batch_not_repeated_after_interruption(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(matrix, "EXPANDED_CONFIGURATION_COUNT", 5)
    monkeypatch.setattr(runner, "LIMITS", {name: 0 for name in runner.LIMITS})
    identity = {
        "batch_size": 2,
        "settings": runner.execution_settings({}),
        "runtime_limits": dict(runner.LIMITS),
        "statistics_policy": dict(matrix.STATISTICS_POLICY),
    }
    monkeypatch.setattr(matrix, "_identity", lambda *args: identity)
    study = {"identity": identity, "study_sha256": "study", "preparation_path": "unused"}
    sources = [{"id": "okx:TEST/USDT", "venue": "okx", "records": 2}]
    calls = []

    def run(
        binary: Path, plan: dict[str, Any], directory: Path, progress_path: Path
    ) -> dict[str, Any]:
        ids = [row["id"] for row in plan["configurations"]]
        calls.append(ids)
        if len(calls) == 2:
            raise InterruptedError("test stop")
        rows = [
            result(next(i for i in range(5) if expanded_configuration(i)["id"] == key))
            for key in ids
        ]
        for row in rows:
            for metric in [row["total"], *row["venues"]]:
                spent = Fraction(metric["spent"])
                metric["realized_gross"] = "0/1"
                metric["fees"] = str(spent / 500)
                metric["total_pnl"] = str(Fraction(metric["unrealized_gross"]) - spent / 500)
                metric["return_pct"] = str(
                    Fraction(metric["total_pnl"]) * 100 / Fraction(metric["peak_spent"])
                )
                for field in ("spent", "peak_spent", "total_pnl", "realized_gross", "fees"):
                    value = Fraction(metric[field])
                    metric[field] = f"{value.numerator}/{value.denominator}"

            def scope(metrics: dict[str, Any]) -> dict[str, Any]:
                period = {
                    name: metrics[name]
                    for name in (
                        "spent",
                        "peak_spent",
                        "peak_spent_ms",
                        "total_pnl",
                        "realized_gross",
                        "fees",
                        "buy_count",
                        "sell_count",
                    )
                }
                period.update(
                    start_ms=1,
                    end_ms=2,
                    spent_open="0/1",
                    net_profit_closed="0/1",
                    net_loss_closed="0/1",
                    winning_closures=0,
                    losing_closures=0,
                    breakeven_closures=0,
                    profit_factor=None,
                    profit_factor_status="no_closures",
                    sample_months=0,
                    monthly_sum_units="0",
                    monthly_square_sum_units="0",
                    monthly_downside_square_sum_units="0",
                    sharpe=None,
                    sharpe_squared=None,
                    sharpe_status="insufficient_months",
                    sortino=None,
                    sortino_squared=None,
                    sortino_status="insufficient_months",
                )
                result = {
                    "money_scale_denominator": "500",
                    "overall": period,
                    "monthly": [dict(period, complete_month=False)],
                }
                if "venue" in metrics:
                    result["venue"] = metrics["venue"]
                return result

            row["performance"] = {
                "schema": "grid-monthly-performance-v1",
                "total": scope(row["total"]),
                "venues": [scope(item) for item in row["venues"]],
            }
        return {
            "summary": {
                "status": "COMPLETE_BATCH",
                "statistics_mode": "monthly_v1",
                "configurations": rows,
                "source_count": 1,
                "processed_records": 2,
                "audit": {"source_sha256_verified": True, "records": 2},
            },
            "exit_code": 0,
        }

    monkeypatch.setattr(runner, "engine_run", run)
    stopped, resumed = tmp_path / "stopped", tmp_path / "resumed"
    stopped.mkdir()
    resumed.mkdir()
    with pytest.raises(InterruptedError):
        matrix._execute_batches(Path("binary"), study, sources, stopped)
    original = (stopped / "results.sqlite").read_bytes()
    assert matrix.page_results(stopped)["configuration_count"] == 2
    assert not (stopped / "batches/000000").exists()
    assert (stopped / "batches/000002").is_dir()
    completed = matrix._execute_batches(
        Path("binary"), study, sources, resumed, resume_from=stopped
    )
    assert completed["configuration_count"] == 5
    assert matrix.page_results(resumed)["study_complete"]
    assert (resumed / "summary.json").is_file()
    assert calls[0] not in calls[1:]
    assert (stopped / "results.sqlite").read_bytes() == original
    assert (stopped / "batches/000002").is_dir()


def test_source_binding_and_missing_matrix_gates_block_before_engine(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    sources = fixture_sources(tmp_path / "sources", count=7, minutes=1)
    preparation = tmp_path / "preparation"
    preparation.mkdir()
    runner.write_json(
        preparation / "manifest.json",
        {"schema": "grid-research-sweep-sources-v1", "status": "READY", "sources": sources},
    )
    binary = tmp_path / "binary"
    binary.write_bytes(b"not executed")
    output = tmp_path / "job"
    output.mkdir()
    request = {"action": "matrix-full", "source_output": str(preparation), "binary": str(binary)}
    with pytest.raises(runner.Blocked, match="missing matching expanded-matrix stages"):
        matrix.run_matrix(request, output)
    assert not (output / "results.sqlite").exists()
    assert len(runner.read_json(output / "matrix-study.json")["matrix"]) < 10
    previous = tmp_path / "previous"
    previous.mkdir()
    prepared = matrix.run_matrix(dict(request, action="matrix-prepare"), previous)
    assert prepared["configuration_count"] == 423360
    binary.write_bytes(b"changed")
    next_job = tmp_path / "next"
    next_job.mkdir()
    with pytest.raises(runner.Blocked, match="changed"):
        matrix.run_matrix(dict(request, source_output=str(previous)), next_job)


def test_representatives_keep_all_local_choices_and_all_aggregate_levels() -> None:
    configs = matrix._representatives()
    assert len(configs) == 540
    assert len({(row["step_pct"], row["tp_pct"]) for row in configs}) == 540
    assert len({row["exchange_tp_pct"] for row in configs}) == 28
    assert len({row["global_tp_pct"] for row in configs}) == 28


def test_complete_matrix_load_rejects_sample_and_incomplete_work(tmp_path: Path) -> None:
    import msgpack  # type: ignore[import-untyped]

    sources = [{"id": "okx:TEST/USDT", "records": 12}]
    policy = {**matrix.MATRIX_ACCEPTANCE_POLICY, "configuration_count": 3}
    assert "configuration_count" not in matrix.MATRIX_ACCEPTANCE_POLICY
    assert matrix.MATRIX_ACCEPTANCE_POLICY["fixture_bars"] == 1024
    catalog = matrix.ResultCatalog(tmp_path / "results.sqlite", {}, create=True)
    try:
        for begin, end, seconds in ((0, 2, 301), (2, 3, 300)):
            receipt = {
                "summary": {
                    "configuration_count": end - begin,
                    "source_count": 1,
                    "processed_records": 12,
                    "duration_stopped": False,
                    "timings": {"replay_seconds": seconds},
                }
            }
            catalog.append(begin, [result(i) for i in range(begin, end)], receipt)
        with catalog.connection:
            catalog.set_metadata("complete", True)
            catalog.set_metadata("source_manifest_sha256", matrix._digest(sources))
        verified = matrix._check_complete_matrix_load(tmp_path, sources, policy)
        assert verified["configuration_count"] == 3
        assert verified["batch_count"] == 2
        assert verified["processed_records_per_configuration"] == 12
        assert verified["replay_seconds"] == 601
        # A complete small sample is never evidence for all 423,360 configurations.
        with pytest.raises(ValueError, match="complete matrix"):
            matrix._check_complete_matrix_load(
                tmp_path, sources, {**matrix.MATRIX_ACCEPTANCE_POLICY, "configuration_count": 4480}
            )
        for changes in (
            {"source_count": 0},
            {"configuration_count": 0},
            {"processed_records": 11},
            {"duration_stopped": True},
            {"timings": {"replay_seconds": 0}},
            {"timings": {"replay_seconds": float("nan")}},
        ):
            changed = {"summary": {**receipt["summary"], **changes}}
            packed = msgpack.packb(changed, use_bin_type=True)
            with catalog.connection:
                catalog.connection.execute(
                    "UPDATE batches SET receipt=?,sha256=? WHERE first_ordinal=2",
                    (packed, hashlib.sha256(packed).digest()),
                )
            with pytest.raises(ValueError):
                matrix._check_complete_matrix_load(tmp_path, sources, policy)
        with catalog.connection:
            catalog.connection.execute("DELETE FROM configurations WHERE ordinal=2")
        with pytest.raises(ValueError, match="complete matrix"):
            matrix._check_complete_matrix_load(tmp_path, sources, policy)
    finally:
        catalog.close()


def test_endurance_uses_complete_matrix_and_actual_pair_roster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = fixture_sources(tmp_path / "original", count=8, minutes=1)
    for index, source in enumerate(sources):
        source["id"] = f"{source['venue']}:ACTUAL{index}/USDT"
        source["symbol"] = f"ACTUAL{index}/USDT"
        source["rules"] = {**source["rules"], "min_cost": str(7 + index)}
    study = {
        "study_sha256": "study",
        "identity": {
            "settings": runner.execution_settings({}),
            "batch_size": 1024,
            "acceptance_policy": matrix.MATRIX_ACCEPTANCE_POLICY,
        },
    }
    calls = []

    def no_representatives() -> Any:
        pytest.fail("load must not select 540 representatives")

    def recovery(
        binary: Path, study: dict[str, Any], synthetic: list[dict[str, Any]], output: Path
    ) -> dict[str, Any]:
        calls.append("recovery")
        return {"passed": True, "checkpoint_count": 2}

    def execute(
        binary: Path, study: dict[str, Any], synthetic: list[dict[str, Any]], output: Path
    ) -> dict[str, Any]:
        calls.append("all_configurations")
        for original, actual in zip(sources, synthetic, strict=True):
            for key in ("id", "venue", "symbol", "rules"):
                assert actual[key] == original[key]
            assert actual["path"] != original["path"]
            assert actual["records"] > original["records"]
        return {"status": "COMPLETE", "configuration_count": 423360}

    monkeypatch.setattr(matrix, "_representatives", no_representatives)
    monkeypatch.setattr(matrix, "_load_recovery", recovery)
    monkeypatch.setattr(matrix, "_execute_batches", execute)
    monkeypatch.setattr(
        matrix,
        "_check_complete_matrix_load",
        lambda *args: {"configuration_count": 423360, "replay_seconds": 601},
    )
    output = tmp_path / "load"
    output.mkdir()
    checked = matrix._endurance(Path("unused"), study, sources, output)
    assert calls == ["all_configurations", "recovery"]
    assert checked["configuration_count"] == checked["configurations"] == 423360
    manifest = runner.read_json(output / "load-manifest.json")
    assert len(manifest["sources"]) == len(sources)
    assert manifest["acceptance_policy"]["configuration_count"] == 423360
    assert checked["source_kind"].startswith("synthetic_load_prices")


def test_cached_chart_reuses_only_verified_owned_artifacts(tmp_path: Path) -> None:
    previous = tmp_path / "previous"
    engine = previous / "engine"
    engine.mkdir(parents=True)
    hashes = {}
    for name in ("summary.json", "pairs.jsonl", "pairs.index.json", "series.jsonl"):
        (engine / name).write_bytes(b"test")
        hashes[name] = runner.sha256(engine / name)
    receipt = {
        "status": "COMPLETE",
        "chart_cache_key": "key",
        "parent_result_identity": "parent",
        "result_dir": str(engine),
        "config_id": "cfg",
        "chart_artifact_sha256": hashes,
    }
    runner.write_json(previous / "receipt.json", receipt)
    current = tmp_path / "current"
    current.mkdir()
    result_value = matrix._cached_chart(current, "key", "parent")
    assert result_value and result_value["cache_hit"]
    assert (current / "engine/series.jsonl").read_bytes() == b"test"
    (engine / "series.jsonl").write_bytes(b"corrupted")
    failed = tmp_path / "failed"
    failed.mkdir()
    assert matrix._cached_chart(failed, "key", "parent") is None
    assert not (failed / "engine").exists()


def test_chart_reconstructs_decimal_plan_and_keeps_full_roster(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    parent, output = tmp_path / "parent", tmp_path / "chart"
    parent.mkdir()
    output.mkdir()
    selected = result(1)
    for name in ("step_pct", "tp_pct", "global_tp_pct"):
        selected[name] = str(Fraction(selected[name]))
    catalog = matrix.ResultCatalog(parent / "results.sqlite", {}, create=True)
    catalog.append(0, [result(0), selected], {})
    catalog.close()
    runner.write_json(parent / "receipt.json", {"status": "COMPLETE", "window": None})
    sources: list[dict[str, Any]] = [{"id": "okx:TEST/USDT", "venue": "okx", "records": 1}]
    study = {
        "study_sha256": "fixed",
        "identity": {
            "settings": runner.execution_settings({}),
            "runtime_limits": dict(runner.LIMITS),
        },
    }

    def fake_engine(
        binary: Path, plan: dict[str, Any], directory: Path, progress_path: Path
    ) -> dict[str, Any]:
        assert plan["sources"] == sources
        assert plan["configurations"] == [expanded_configuration(1)]
        assert plan["configurations"][0]["step_pct"] == "0.5"
        assert plan["chart_pair_source_id"] == sources[0]["id"]
        engine = Path(plan["output_dir"])
        engine.mkdir()
        for filename in ("summary.json", "series.jsonl", "pairs.jsonl", "pairs.index.json"):
            (engine / filename).write_text("artifact")
        return {
            "result_dir": str(engine),
            "summary": {
                "status": "COMPLETE_DIAGNOSTIC",
                "configurations": [selected],
                "source_count": 1,
                "processed_records": 1,
                "audit": {"source_sha256_verified": True, "records": 1},
            },
        }

    monkeypatch.setattr(runner, "engine_run", fake_engine)
    completed = matrix.build_chart(
        Path("binary"),
        {
            "source_output": str(parent),
            "config_id": selected["id"],
            "scope": "pair:" + sources[0]["id"],
        },
        output,
        study,
        sources,
    )
    assert completed["status"] == "COMPLETE"
    assert not completed["cache_hit"]


def test_hard_crash_resume_uses_saved_admission_without_fabricating_stage_passes(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    parent, output = tmp_path / "crashed", tmp_path / "resumed"
    parent.mkdir()
    output.mkdir()
    binary = tmp_path / "binary"
    binary.write_bytes(b"not executed")
    study = {
        "study_sha256": "study",
        "preparation_path": "source-manifest.json",
        "identity": {
            "preparation_sha256": "prep",
            "binary_sha256": "binary",
            "code_sha256": "code",
            "settings": {},
            "statistics_policy": dict(matrix.STATISTICS_POLICY),
        },
    }
    evidence = {}
    for action in ("matrix-validate", "matrix-calibrate", "matrix-endurance", "matrix-rehearsal"):
        proof = tmp_path / (action + ".json")
        runner.write_json(
            proof,
            {
                "action": action,
                "status": "PASS",
                "matrix_kind": matrix.MATRIX_KIND,
                "study_sha256": "study",
            },
        )
        evidence[action] = {"receipt": str(proof), "sha256": runner.sha256(proof)}
    runner.write_json(
        parent / "admission.json",
        {
            "action": "matrix-full",
            "resume_stage": "matrix-full",
            "window": None,
            "study_sha256": "study",
            "evidence": evidence,
        },
    )
    assert not (parent / "receipt.json").exists()
    monkeypatch.setattr(matrix, "_study", lambda *args: (study, []))

    def execute(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["resume_from"] == parent
        return {"status": "COMPLETE"}

    monkeypatch.setattr(matrix, "_execute_batches", execute)
    checked = matrix.run_matrix(
        {"action": "matrix-resume", "binary": str(binary), "source_output": str(parent)}, output
    )
    assert checked["status"] == "COMPLETE"
    assert checked["completed_stage"] == "matrix-full"
    proof.write_text("changed")
    with pytest.raises(runner.Blocked, match="missing matching"):
        matrix.run_matrix(
            {"action": "matrix-resume", "binary": str(binary), "source_output": str(parent)}, output
        )


def test_independent_window_record_count_retains_gaps_and_half_open_bounds(tmp_path: Path) -> None:
    sources = fixture_sources(tmp_path / "sources", count=8, minutes=7)
    low = min(source["first_ms"] for source in sources) + 60000
    high = low + 120000
    import struct

    expected = sum(
        sum(
            low <= row[0] < high
            for row in struct.iter_unpack("<qffff", Path(source["path"]).read_bytes())
        )
        for source in sources
    )
    assert matrix._window_records(sources, (low, high)) == expected
    assert matrix._window_records(sources, None) == sum(source["records"] for source in sources)


def test_receipt_cannot_be_relabelled_as_a_different_acceptance_stage(tmp_path: Path) -> None:
    proof = tmp_path / "validation.json"
    runner.write_json(
        proof,
        {
            "action": "matrix-validate",
            "status": "PASS",
            "study_sha256": "study",
            "matrix_kind": matrix.MATRIX_KIND,
        },
    )
    runner.write_json(
        tmp_path / "admission.json",
        {
            "evidence": {
                "matrix-endurance": {"receipt": str(proof), "sha256": runner.sha256(proof)},
            }
        },
    )
    assert matrix._evidence(tmp_path, {"study_sha256": "study"}) == {}


def test_sqlite_failures_are_reportable_job_errors(tmp_path: Path, monkeypatch: Any) -> None:
    corrupt = tmp_path / "results.sqlite"
    corrupt.write_bytes(b"not a SQLite catalog")
    with pytest.raises(ValueError, match="cannot read"):
        matrix.page_results(corrupt)

    def database_error(*args: Any) -> dict[str, Any]:
        raise sqlite3.DatabaseError("damaged")

    monkeypatch.setattr(matrix, "_run_matrix", database_error)
    with pytest.raises(ValueError, match="catalog transaction or integrity"):
        matrix.run_matrix({}, tmp_path)
