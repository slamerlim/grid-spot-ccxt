"""Variable BUY depth is part of immutable configuration and binary identity."""

from __future__ import annotations

import copy
import json
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from scripts import research_sweep as runner
from scripts import research_sweep_matrix as matrix
from scripts.research_sweep_reference import EXPANDED_CONFIGURATION_COUNT, expanded_configuration

from .test_research_sweep_gap_matrix import _gap_study
from .test_research_sweep_matrix import result


def small_depth_selection() -> dict[str, Any]:
    base = matrix._base_ordinal(12, 10, 8, 12)
    plan = {
        "schema": matrix.DEPTH_PLAN_SCHEMA,
        "grid_depth_pct_values": [str(value) for value in matrix.GRID_DEPTHS],
        "configurations": [
            matrix._depth_configuration(block * EXPANDED_CONFIGURATION_COUNT + base)
            for block in range(7)
        ],
    }
    return matrix._configuration_selection(plan, "a" * 64)


def test_depth_matrix_exact_factorial_probes_and_legacy_identity() -> None:
    plan = matrix.depth_configuration_plan()
    selected = matrix._configuration_selection(plan, matrix._digest(plan))
    rows = selected["configurations"]
    assert len(rows) == len({row["id"] for row in rows}) == 59_367
    assert Counter(row["grid_depth_pct"] for row in rows) == {
        str(depth): 8481 for depth in matrix.GRID_DEPTHS
    }
    base_count = 0
    extras: dict[str, set[tuple[Fraction, ...]]] = {}
    for ordinal, row in enumerate(rows):
        assert matrix.configuration_at({"matrix": selected}, ordinal) == row
        parameters = tuple(
            Fraction(row[field])
            for field in ("step_pct", "tp_pct", "exchange_tp_pct", "global_tp_pct")
        )
        assert parameters[2] >= 2 and parameters[3] >= 2
        assert parameters[2] <= 12 and parameters[3] <= 12
        if all(value.denominator == 1 for value in parameters):
            base_count += 1
        else:
            extras.setdefault(row["grid_depth_pct"], set()).add(parameters)
        if row["grid_depth_pct"] == "60":
            original = expanded_configuration(selected["canonical_ordinals"][ordinal])
            assert {key: value for key, value in row.items() if key != "grid_depth_pct"} == original
    assert base_count == 59_290
    expected_extras = {
        *(
            (Fraction(6), Fraction(5), Fraction(4), Fraction(value))
            for value in ("5.5", "6.5", "7.5")
        ),
        *((Fraction(6), Fraction(5), Fraction(value), Fraction(6)) for value in ("3.5", "4.5")),
        *((Fraction(value), Fraction(5), Fraction(4), Fraction(6)) for value in ("5.5", "6.5")),
        *((Fraction(6), Fraction(value), Fraction(4), Fraction(6)) for value in ("4.5", "5.5")),
        *((Fraction(value), Fraction(4), Fraction(5), Fraction(4)) for value in ("0.5", "1.5")),
    }
    assert all(values == expected_extras for values in extras.values())
    assert len(extras) == 7 and len(expected_extras) == 11
    assert [len(rows[first : first + 1024]) for first in range(0, len(rows), 1024)] == [
        *([1024] * 57),
        999,
    ]
    # No mutation or newly implied depth field in the existing approved matrix.
    legacy = matrix.selected_configuration_plan()
    assert len(legacy["configurations"]) == 4480
    assert all("grid_depth_pct" not in row for row in legacy["configurations"])


@pytest.mark.parametrize("change", ("depth", "missing", "id", "duplicate", "order", "axis"))
def test_depth_selection_rejects_ambiguous_changed_or_duplicate_inputs(change: str) -> None:
    selected = small_depth_selection()
    plan = {
        "schema": matrix.DEPTH_PLAN_SCHEMA,
        "grid_depth_pct_values": selected["grid_depth_pct_values"],
        "configurations": copy.deepcopy(selected["configurations"]),
    }
    rows = plan["configurations"]
    if change == "depth":
        rows[0]["grid_depth_pct"] = "30"
    elif change == "missing":
        del rows[0]["grid_depth_pct"]
    elif change == "id":
        rows[0]["id"] += "-d60"
    elif change == "duplicate":
        rows[1] = rows[0]
    elif change == "order":
        rows.reverse()
    else:
        rows[0]["global_tp_pct"] = None
    with pytest.raises(ValueError):
        matrix._configuration_selection(plan, "a" * 64)


def test_depth_configuration_hashes_and_helper_cannot_ignore_changed_depth() -> None:
    identity = {"matrix": small_depth_selection()}
    previous = matrix._policy(identity)
    changed = copy.deepcopy(identity)
    changed["matrix"]["configurations"][1]["grid_depth_pct"] = "35"
    assert matrix._digest(changed) != matrix._digest(identity)
    assert (
        matrix._policy(changed)["configuration_parameters_sha256"]
        != previous["configuration_parameters_sha256"]
    )
    with pytest.raises(ValueError, match="identity changed"):
        matrix.configuration_at(changed, 1)
    for ordinal in (-1, 7, True):
        with pytest.raises(ValueError, match="ordinal"):
            matrix.configuration_at(identity, ordinal)


def test_depth_binary_roundtrip_resume_rejects_changed_or_missing_depth(tmp_path: Path) -> None:
    selection = small_depth_selection()
    identity = {"matrix": selection}
    base = selection["canonical_ordinals"][0]
    rows = [
        {**result(base), **configuration, "grid_depth_pct": f"{configuration['grid_depth_pct']}/1"}
        for configuration in selection["configurations"]
    ]
    catalog = matrix.ResultCatalog(tmp_path / "results.sqlite", identity, create=True)
    try:
        catalog.append(0, rows[:2], {})
        for bad_depth in ("60/1", None):
            bad = dict(rows[2], grid_depth_pct=bad_depth)
            with pytest.raises(ValueError, match="grid_depth_pct"):
                catalog.append(2, [bad], {})
            assert catalog.count() == 2
        assert catalog.verify(2)["configuration_count"] == 2
    finally:
        catalog.close()
    catalog = matrix.ResultCatalog(tmp_path / "results.sqlite", identity)
    try:
        catalog.append(2, rows[2:], {})
        assert catalog.verify(7, complete=True)["configuration_count"] == 7
        assert matrix.page_results(tmp_path)["configurations"] == rows
    finally:
        catalog.close()
    altered = copy.deepcopy(identity)
    altered["matrix"]["configurations"][0]["grid_depth_pct"] = "30"
    with pytest.raises(ValueError, match="identity differs"):
        matrix.ResultCatalog(tmp_path / "results.sqlite", altered)


def test_variable_depth_summary_requires_the_exact_configured_boundary() -> None:
    config = small_depth_selection()["configurations"][1]
    row = {**result(), **config, "grid_depth_pct": "30/1"}
    sources = [{"id": "okx:X/USDT", "venue": "okx", "records": 1}]
    summary = {
        "status": "COMPLETE_BATCH",
        "source_count": 1,
        "processed_records": 1,
        "audit": {"source_sha256_verified": True, "records": 1},
        "configurations": [row],
    }
    matrix._verify_summary(summary, [config], sources, full_history=True)
    row["grid_depth_pct"] = "35/1"
    with pytest.raises(ValueError, match="grid_depth_pct"):
        matrix._verify_summary(summary, [config], sources, full_history=True)


def test_five_venue_preparation_accepts_exact_new_matrix_and_rejects_subset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, binary = _gap_study(tmp_path, monkeypatch)
    path = tmp_path / "depth-plan.json"
    plan = matrix.depth_configuration_plan()
    runner.write_json(path, plan)
    output = tmp_path / "new-study"
    output.mkdir()
    request = {"source_output": str(tmp_path / "parent"), "configuration_plan": str(path)}
    study, _ = matrix._study(request, output, binary)
    assert matrix._configuration_count(study) == 59_367
    assert study["assumptions"]["grid_depth_pct_values"] == [str(x) for x in matrix.GRID_DEPTHS]
    plan["configurations"].pop()
    runner.write_json(path, plan)
    denied = tmp_path / "denied"
    denied.mkdir()
    with pytest.raises(runner.Blocked, match="approved selected configurations"):
        matrix._study(request, denied, binary)


def test_matrix_plan_cli_only_creates_immutable_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "chosen.json"
    monkeypatch.setattr(sys, "argv", ["research_sweep", "matrix-plan", "--output", str(path)])

    def no_run(*args: Any, **kwargs: Any) -> None:
        pytest.fail("plan generation cannot start a backtest")

    monkeypatch.setattr(runner, "engine_run", no_run)
    assert runner.main() == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["configuration_count"] == 59_367
    assert receipt["backtest_started"] is False
    assert receipt["sha256"] == runner.sha256(path)
    assert matrix._selection(path)["configuration_count"] == 59_367
    with pytest.raises(FileExistsError):
        runner.main()
    assert runner.sha256(path) == receipt["sha256"]
