"""The new five-venue policy is explicit and cannot weaken older admissions."""

from __future__ import annotations

import copy
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts import research_sweep as runner
from scripts import research_sweep_matrix as matrix
from scripts import research_sweep_sources as sources
from scripts.research_sweep_reference import VENUES, expanded_configuration, fixture_sources


def test_source_dispatch_requires_known_schema_and_verifies_new_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {"id": "okx:X/USDT", "venue": "okx", "rules": {"min_cost": "1"}}
    called: list[str] = []
    monkeypatch.setattr(sources, "verify_selection", lambda _value: called.append("old"))
    monkeypatch.setattr(sources, "verify_gap_free_selection", lambda _value: called.append("new"))
    monkeypatch.setattr(
        sources, "verify_reviewed_selection", lambda _value: called.append("reviewed")
    )
    monkeypatch.setattr(
        sources, "verify_current_rules_selection", lambda _value: called.append("current")
    )
    monkeypatch.setattr(
        sources, "verify_flat_hour_selection", lambda _value: called.append("flat_hour")
    )
    for schema, expected in (
        ("grid-research-sweep-sources-v1", []),
        ("grid-research-sweep-selection-v1", ["old"]),
        (sources.GAP_FREE_SCHEMA, ["old", "new"]),
        (sources.REVIEWED_SCHEMA, ["old", "new", "reviewed"]),
        (sources.CURRENT_RULES_SCHEMA, ["old", "new", "reviewed", "current"]),
        (sources.FLAT_HOUR_SCHEMA, ["old", "new", "reviewed", "current", "flat_hour"]),
    ):
        assert runner._engine_sources({"schema": schema, "status": "READY", "sources": [row]}) == [
            row
        ]
        assert called == expected
    for unknown_schema in (None, "grid-research-sweep-selection-v99"):
        with pytest.raises(runner.Blocked, match="schema"):
            runner._engine_sources({"schema": unknown_schema, "status": "READY", "sources": [row]})


def _gap_study(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    schema: str = sources.GAP_FREE_SCHEMA,
) -> tuple[dict[str, Any], list[dict[str, Any]], Path]:
    rows = [
        row
        for row in fixture_sources(tmp_path / "prices", count=7, minutes=5, gap_free=True)
        if row["venue"] in sources.GAP_FREE_VENUES
    ]
    preparation = tmp_path / "parent"
    manifest: dict[str, Any] = {
        "schema": schema,
        "status": "READY",
        "sources": rows,
        "research_readiness": (
            sources.CURRENT_RULES_READINESS
            if schema in (sources.CURRENT_RULES_SCHEMA, sources.FLAT_HOUR_SCHEMA)
            else "PENDING_HISTORICAL_RULES_AND_STATISTICS_REVIEW"
        ),
    }
    if schema in (sources.CURRENT_RULES_SCHEMA, sources.FLAT_HOUR_SCHEMA):
        manifest["rules_collection"] = {"ccxt_version": sources.RESEARCH_CCXT_VERSION}
    if schema == sources.FLAT_HOUR_SCHEMA:
        manifest["flat_audit"] = {"threshold_minutes": 60, "sha256": "a" * 64}
    runner.write_json(preparation / "manifest.json", manifest)
    # Source-selector tests independently verify the signed parent, audit and
    # inclusion/exclusion evidence. These tests isolate downstream policy binding.
    monkeypatch.setattr(sources, "verify_gap_free_selection", lambda _value: None)
    monkeypatch.setattr(sources, "verify_reviewed_selection", lambda _value: None)
    monkeypatch.setattr(sources, "verify_current_rules_selection", lambda _value: None)
    monkeypatch.setattr(sources, "verify_flat_hour_selection", lambda _value: None)
    binary = tmp_path / "binary"
    binary.write_bytes(b"not an executable")
    output = tmp_path / "study"
    output.mkdir()
    study, actual = matrix._study({"source_output": str(preparation)}, output, binary)
    return study, actual, binary


@pytest.mark.parametrize(
    "schema",
    (
        sources.GAP_FREE_SCHEMA,
        sources.REVIEWED_SCHEMA,
        sources.CURRENT_RULES_SCHEMA,
        sources.FLAT_HOUR_SCHEMA,
    ),
)
def test_gap_study_preserves_selected_matrix_and_exact_source_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema: str
) -> None:
    study, rows, binary = _gap_study(tmp_path, monkeypatch, schema)
    assert matrix._configuration_count(study) == 4480
    assert (
        study["matrix"]["configurations"] == matrix.selected_configuration_plan()["configurations"]
    )
    binding = study["identity"]["source_selection"]
    assert binding == study["source_selection"]
    assert binding["expected_venues"] == list(sources.GAP_FREE_VENUES)
    assert (
        binding["source_ids"] == binding["selected_source_ids"] == sorted(row["id"] for row in rows)
    )
    assert binding["manifest_sha256"] == runner.sha256(Path(study["preparation_path"]))
    assert study["assumptions"]["common_exit_price"] == "current_observed_price_only"
    assert study["assumptions"]["historical_rules_verified"] is False
    assert study["identity"]["statistics_policy"]["calendar_scope"] == (
        "venue_own_observed_span_v1"
    )
    assert study["assumptions"]["common_tp_requires_current_price_for_every_open_position"] is True
    if schema in (sources.CURRENT_RULES_SCHEMA, sources.FLAT_HOUR_SCHEMA):
        assert study["assumptions"]["rules_policy"] == (
            "current_public_ccxt_snapshot_applied_to_full_history"
        )
    if schema == sources.FLAT_HOUR_SCHEMA:
        assert study["assumptions"]["flat_price_screen"] == (
            "exclude_pair_with_60_consecutive_identical_flat_ohlc_minutes"
        )
    plan = matrix._batch_plan(study, rows, [expanded_configuration(0)], tmp_path / "batch")
    assert plan["source_selection"] == binding
    assert plan["mode"] == "matrix_batch"
    for altered in (rows[:-1], [*rows, rows[0]], [dict(row, venue="xt") for row in rows]):
        with pytest.raises(runner.Blocked, match="source selection"):
            matrix._batch_plan(study, altered, [expanded_configuration(0)], tmp_path / "bad")
    # A source ID change affects study identity and invalidates old evidence.
    changed = copy.deepcopy(study["identity"])
    changed["source_selection"]["selected_source_ids"][0] += "-changed"
    assert matrix._digest(changed) != study["study_sha256"]
    changed_output = tmp_path / "next"
    changed_output.mkdir()
    saved = runner.read_json(tmp_path / "study/matrix-study.json")
    saved["identity"] = changed
    runner.write_json(tmp_path / "study/matrix-study.json", saved)
    with pytest.raises(runner.Blocked, match="changed"):
        matrix._study({"source_output": str(tmp_path / "study")}, changed_output, binary)


def test_new_policy_diagnostics_bind_effective_and_complete_rosters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study, rows, _ = _gap_study(tmp_path, monkeypatch)
    invented = [dict(row, id=row["id"] + "-fixture") for row in rows]
    with pytest.raises(runner.Blocked, match="source selection"):
        matrix._batch_plan(study, invented, [expanded_configuration(0)], tmp_path / "full")
    plan = matrix._batch_plan(
        study, invented, [expanded_configuration(0)], tmp_path / "diagnostic", diagnostic=True
    )
    assert plan["mode"] == "validation"
    assert plan["source_selection"]["source_ids"] == sorted(row["id"] for row in invented)
    assert plan["source_selection"]["selected_source_ids"] == sorted(row["id"] for row in rows)
    with pytest.raises(runner.Blocked, match="source selection"):
        matrix._batch_plan(
            study, invented[:-1], [expanded_configuration(0)], tmp_path / "bad", diagnostic=True
        )


def test_gap_study_rejects_changed_configuration_list_and_old_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study, _, binary = _gap_study(tmp_path, monkeypatch)
    chosen = matrix.selected_configuration_plan()
    chosen["configurations"].pop()
    selection = tmp_path / "changed-configurations.json"
    runner.write_json(selection, chosen)
    output = tmp_path / "bad-selection"
    output.mkdir()
    with pytest.raises(runner.Blocked, match="approved selected configurations"):
        matrix._study(
            {"source_output": str(tmp_path / "parent"), "configuration_plan": str(selection)},
            output,
            binary,
        )
    previous = tmp_path / "old-proof"
    runner.write_json(
        previous / "receipt.json",
        {
            "status": "PASS",
            "matrix_kind": matrix.MATRIX_KIND,
            "action": "matrix-validate",
            "study_sha256": "0" * 64,
        },
    )
    assert matrix._evidence(previous, study) == {}


@pytest.mark.parametrize("damage", ("ordinals", "count", "study_hash"))
@pytest.mark.parametrize("rehash", (False, True))
def test_parent_matrix_cannot_shrink_through_stale_or_rehashed_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str, rehash: bool
) -> None:
    study, _, binary = _gap_study(tmp_path, monkeypatch)
    parent = tmp_path / "study"
    if damage == "ordinals":
        study["identity"]["matrix"]["canonical_ordinals"] = [0]
    elif damage == "count":
        study["identity"]["matrix"]["configuration_count"] = 1
    else:
        study["study_sha256"] = "0" * 64
    if rehash and damage != "study_hash":
        study["study_sha256"] = matrix._digest(study["identity"])
    runner.write_json(parent / "matrix-study.json", study)
    output = tmp_path / "child"
    output.mkdir()
    with pytest.raises(runner.Blocked, match="identity changed|approved selected configurations"):
        matrix._study({"source_output": str(parent)}, output, binary)
    assert not (output / "matrix-study.json").exists()


def test_legacy_study_keeps_seven_venues_and_stale_exit_assumption(
    tmp_path: Path,
) -> None:
    rows = fixture_sources(tmp_path / "prices", count=7, minutes=2)
    parent = tmp_path / "parent"
    runner.write_json(
        parent / "manifest.json",
        {"schema": "grid-research-sweep-sources-v1", "status": "READY", "sources": rows},
    )
    binary = tmp_path / "binary"
    binary.write_bytes(b"not an executable")
    output = tmp_path / "study"
    output.mkdir()
    study, actual = matrix._study({"source_output": str(parent)}, output, binary)
    assert {row["venue"] for row in actual} == set(VENUES)
    assert "source_selection" not in study["identity"]
    assert study["assumptions"]["common_exit_price"] == "last_known_including_stale"
    assert study["identity"]["statistics_policy"]["calendar_scope"] == ("shared_portfolio_span_v1")
    runner.write_json(
        parent / "manifest.json",
        {"schema": "grid-research-sweep-sources-v1", "status": "READY", "sources": rows[:-1]},
    )
    with pytest.raises(runner.Blocked, match="all seven"):
        matrix._study({"source_output": str(parent)}, output, binary)


@pytest.mark.parametrize(
    "schema", (sources.REVIEWED_SCHEMA, sources.CURRENT_RULES_SCHEMA, sources.FLAT_HOUR_SCHEMA)
)
def test_only_current_rule_based_selections_pass_full_admission_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema: str
) -> None:
    _, _, binary = _gap_study(tmp_path, monkeypatch, schema)
    output = tmp_path / "full"
    output.mkdir()
    with pytest.raises(runner.Blocked) as error:
        matrix._run_matrix(
            {
                "action": "matrix-full",
                "source_output": str(tmp_path / "parent"),
                "binary": str(binary),
            },
            output,
        )
    if schema in (sources.CURRENT_RULES_SCHEMA, sources.FLAT_HOUR_SCHEMA):
        assert "missing matching expanded-matrix stages" in str(error.value)
    else:
        assert "unresolved source/rule/statistics review" in str(error.value)


def test_flat_hour_full_stage_uses_three_matching_checks_without_rehearsal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study, rows, binary = _gap_study(tmp_path, monkeypatch, sources.FLAT_HOUR_SCHEMA)
    observed: list[tuple[str, int]] = []
    monkeypatch.setattr(matrix, "_study", lambda *_args: (study, rows))
    monkeypatch.setattr(
        matrix,
        "_evidence",
        lambda *_args: {
            stage: {"status": "PASS"}
            for stage in ("matrix-validate", "matrix-calibrate", "matrix-endurance")
        },
    )

    def replay(
        _binary: Path,
        active: dict[str, Any],
        selected: list[dict[str, Any]],
        _output: Path,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        observed.append((active["study_sha256"], len(selected)))
        return {"status": "COMPLETE"}

    monkeypatch.setattr(matrix, "_execute_batches", replay)
    output = tmp_path / "full"
    output.mkdir()
    result = matrix._run_matrix(
        {
            "action": "matrix-full",
            "source_output": str(tmp_path / "parent"),
            "binary": str(binary),
        },
        output,
    )
    assert result["status"] == "COMPLETE"
    assert result["completed_stage"] == "matrix-full"
    assert observed == [(study["study_sha256"], len(rows))]


def test_flat_hour_audit_binding_invalidates_prior_matrix_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study, _, binary = _gap_study(tmp_path, monkeypatch, sources.FLAT_HOUR_SCHEMA)
    preparation = Path(study["preparation_path"])
    manifest = runner.read_json(preparation)
    manifest["flat_audit"]["sha256"] = "b" * 64
    runner.write_json(preparation, manifest)
    next_output = tmp_path / "changed-audit"
    next_output.mkdir()
    changed, _ = matrix._study({"source_output": str(tmp_path / "parent")}, next_output, binary)
    assert changed["study_sha256"] != study["study_sha256"]
    assert changed["source_selection"]["manifest_sha256"] == runner.sha256(preparation)


def test_explicit_hour_filter_endurance_waiver_preserves_validation_and_calibration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study, rows, binary = _gap_study(tmp_path, monkeypatch, sources.FLAT_HOUR_SCHEMA)
    monkeypatch.setattr(matrix, "_study", lambda *_args: (study, rows))
    monkeypatch.setattr(
        matrix,
        "_evidence",
        lambda *_args: {
            stage: {"status": "PASS"} for stage in ("matrix-validate", "matrix-calibrate")
        },
    )
    monkeypatch.setattr(
        matrix, "_execute_batches", lambda *_args, **_kwargs: {"status": "COMPLETE"}
    )
    request = {
        "action": "matrix-full",
        "source_output": str(tmp_path / "parent"),
        "binary": str(binary),
    }
    blocked_output = tmp_path / "without-waiver"
    blocked_output.mkdir()
    with pytest.raises(runner.Blocked, match="matrix-endurance"):
        matrix._run_matrix(request, blocked_output)
    output = tmp_path / "with-waiver"
    output.mkdir()
    result = matrix._run_matrix({**request, "skip_endurance": True}, output)
    assert result["status"] == "COMPLETE"
    assert result["waived_stages"] == {"matrix-endurance": "WAIVED_BY_OWNER"}
    assert "matrix-endurance" not in result["evidence"]
    assert runner.read_json(output / "admission.json")["waived_stages"] == result["waived_stages"]

    parent = tmp_path / "interrupted"
    runner.write_json(
        parent / "admission.json",
        {"action": "matrix-full", "window": None, "waived_stages": result["waived_stages"]},
    )
    resumed_output = tmp_path / "resume"
    resumed_output.mkdir()
    resumed = matrix._run_matrix(
        {"action": "matrix-resume", "source_output": str(parent), "binary": str(binary)},
        resumed_output,
    )
    assert resumed["status"] == "COMPLETE"
    assert resumed["waived_stages"] == result["waived_stages"]
    with pytest.raises(runner.Blocked, match="differs from original"):
        matrix._run_matrix(
            {
                "action": "matrix-resume",
                "source_output": str(parent),
                "binary": str(binary),
                "skip_endurance": False,
            },
            tmp_path / "mismatch",
        )


def test_endurance_waiver_rejects_other_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study, rows, binary = _gap_study(tmp_path, monkeypatch, sources.CURRENT_RULES_SCHEMA)
    monkeypatch.setattr(matrix, "_study", lambda *_args: (study, rows))
    with pytest.raises(runner.Blocked, match="hour-filter selection"):
        matrix._run_matrix(
            {
                "action": "matrix-full",
                "source_output": str(tmp_path / "parent"),
                "binary": str(binary),
                "skip_endurance": True,
            },
            tmp_path / "blocked",
        )


def test_cli_select_propagates_only_explicit_history_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def worker(request: dict[str, Any], _output: Path) -> dict[str, Any]:
        calls.append(request)
        return {"status": "READY"}

    monkeypatch.setattr(runner, "worker", worker)
    base = [
        "research_sweep",
        "select",
        "--output",
        str(tmp_path / "out"),
        "--source-output",
        str(tmp_path / "parent"),
    ]
    monkeypatch.setattr(sys, "argv", base)
    assert runner.main() == 0
    assert "history_policy" not in calls[-1]
    monkeypatch.setattr(
        sys, "argv", [*base, "--history-policy", sources.GAP_FREE_POLICY, "--audit-threads", "8"]
    )
    assert runner.main() == 0
    assert calls[-1]["history_policy"] == sources.GAP_FREE_POLICY
    assert calls[-1]["audit_threads"] == 8
    monkeypatch.setattr(
        sys,
        "argv",
        [*base, "--history-policy", sources.GAP_FREE_POLICY, "--exclusion-review", "/review.json"],
    )
    assert runner.main() == 0
    assert calls[-1]["exclusion_review"] == "/review.json"
    monkeypatch.setattr(sys, "argv", [*base, "--exclusion-review", "/review.json"])
    with pytest.raises(SystemExit):
        runner.main()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "research_sweep",
            "matrix-prepare",
            *base[2:],
            "--history-policy",
            sources.GAP_FREE_POLICY,
        ],
    )
    with pytest.raises(SystemExit):
        runner.main()


def test_cli_endurance_waiver_is_explicit_and_full_stage_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def worker(request: dict[str, Any], _output: Path) -> dict[str, Any]:
        seen.append(request)
        return {"status": "READY"}

    monkeypatch.setattr(runner, "worker", worker)
    base = ["research_sweep", "--output", str(tmp_path / "output")]
    for action in ("matrix-full", "matrix-resume"):
        monkeypatch.setattr(sys, "argv", [base[0], action, *base[1:], "--skip-endurance"])
        assert runner.main() == 0
        assert seen[-1]["skip_endurance"] is True
    monkeypatch.setattr(sys, "argv", [base[0], "matrix-calibrate", *base[1:], "--skip-endurance"])
    with pytest.raises(SystemExit):
        runner.main()


def test_worker_new_selection_is_source_only_and_reports_reaudit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRID_RESEARCH_HEAVY_LOCK", str(tmp_path / "heavy.lock"))
    monkeypatch.setattr(runner, "check_resources", lambda *_args: None)
    parent = tmp_path / "parent"
    runner.write_json(parent / "manifest.json", {"schema": "parent fixture"})
    output = tmp_path / "selection"
    seen: list[int] = []

    def select(
        parent_path: Path,
        directory: Path,
        *,
        audit_threads: int,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, Any]:
        assert parent_path == parent / "manifest.json"
        assert audit_threads == 8
        assert progress is not None
        progress(1, 7, "okx:X/USDT")
        observed = runner.read_json(output / "progress.json")
        assert observed["phase"] == "gap_free_source_audit"
        assert observed["audited_sources"] == 1
        seen.append(observed["total_sources"])
        manifest = {
            "schema": sources.GAP_FREE_SCHEMA,
            "status": "READY",
            "blockers": [],
            "totals": {"selected": 5},
            "source_manifest_sha256": runner.sha256(parent_path),
        }
        runner.write_json(directory / "manifest.json", manifest)
        return manifest

    monkeypatch.setattr(sources, "select_gap_free_sources", select)
    receipt = runner.worker(
        {
            "action": "select",
            "source_output": str(parent),
            "history_policy": sources.GAP_FREE_POLICY,
            "audit_threads": 8,
        },
        output,
    )
    assert seen == [7]
    assert receipt["status"] == "READY"
    assert receipt["research_admission"] == "PENDING"
    assert receipt["preparation_sha256"] == runner.sha256(output / "preparation/manifest.json")
    assert "study_manifest" not in receipt
    assert not (output / "study-manifest.json").exists()


@pytest.mark.parametrize("schema", (sources.GAP_FREE_SCHEMA, sources.REVIEWED_SCHEMA))
@pytest.mark.parametrize("action", ("matrix-full", "matrix-resume"))
def test_pending_review_blocks_full_even_with_matching_technical_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema: str, action: str
) -> None:
    study, rows, binary = _gap_study(tmp_path, monkeypatch, schema)
    parent = tmp_path / "old-full"
    runner.write_json(parent / "receipt.json", {"action": "matrix-full", "window": None})
    monkeypatch.setattr(matrix, "_study", lambda *_args: (study, rows))
    monkeypatch.setattr(
        matrix,
        "_evidence",
        lambda *_args: {
            stage: {"status": "PASS"}
            for stage in (
                "matrix-validate",
                "matrix-calibrate",
                "matrix-endurance",
                "matrix-rehearsal",
            )
        },
    )

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("pending source/statistics review must prevent replay launch")

    monkeypatch.setattr(matrix, "_execute_batches", forbidden)
    with pytest.raises(runner.Blocked, match="unresolved source/rule/statistics review"):
        matrix.run_matrix(
            {"action": action, "binary": str(binary), "source_output": str(parent)},
            tmp_path / "blocked",
        )


@pytest.mark.parametrize("window", (None, [], [2, 1], [1], [1, "2"]))
def test_rehearsal_resume_cannot_turn_missing_window_into_full_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, window: Any
) -> None:
    study, rows, binary = _gap_study(tmp_path, monkeypatch, sources.REVIEWED_SCHEMA)
    parent = tmp_path / "interrupted-preflight"
    runner.write_json(parent / "admission.json", {"action": "matrix-rehearsal", "window": window})
    monkeypatch.setattr(matrix, "_study", lambda *_args: (study, rows))
    monkeypatch.setattr(matrix, "_evidence", lambda *_args: {})
    with pytest.raises(runner.Blocked, match="bounded replay window"):
        matrix.run_matrix(
            {"action": "matrix-resume", "binary": str(binary), "source_output": str(parent)},
            tmp_path / "blocked",
        )
