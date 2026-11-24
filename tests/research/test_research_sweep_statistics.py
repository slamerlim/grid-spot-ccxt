from __future__ import annotations

import copy
import hashlib
import os
import struct
from datetime import UTC, datetime
from fractions import Fraction as F
from pathlib import Path
from typing import Any

import pytest

from scripts import research_sweep as runner
from scripts import research_sweep_matrix as matrix
from scripts.research_sweep_reference import expanded_configuration, verify_result
from scripts.research_sweep_statistics import calendar_fixtures, month_end, verify_performance


@pytest.fixture
def engine() -> Path:
    value = os.environ.get("GRID_SWEEP_TEST_BINARY")
    if not value or not Path(value).is_file():
        pytest.skip("explicit compiled offline sweep executable required")
    return Path(value)


def test_policy_binds_the_accepted_monthly_definitions() -> None:
    policy = matrix.STATISTICS_POLICY
    assert policy["annualization_periods"] == 12
    assert policy["monthly_risk"] == "cumulative_from_history_start"
    assert policy["risk_observations"] == "complete_calendar_month_changes_in_net_marked_pnl"
    assert "both_fees" in str(policy["profit_factor"])
    assert policy["calendar_scope"] == "venue_own_observed_span_v1"


@pytest.mark.parametrize("number", [0, 1, -1, 127, 128, -128, -129, 2**127, -(2**127), 10**300])
def test_binary_monthly_integers_never_narrow_or_round(number: int) -> None:
    encoded = matrix._integer_bytes(str(number))
    assert int.from_bytes(encoded, "little", signed=True) == number


def test_full_batch_load_recovery_preserves_exact_monthly_results(
    tmp_path: Path, engine: Path
) -> None:
    sources = calendar_fixtures(tmp_path / "fixtures")
    study = {
        "study_sha256": "load-test",
        "identity": {
            "settings": runner.execution_settings(
                {"threads": 32, "max_timestamp_groups": 1, "block_bytes": 4096}
            ),
            "batch_size": 4,
            "runtime_limits": dict(runner.LIMITS),
        },
    }
    proof = matrix._load_recovery(engine, study, sources, tmp_path / "recovery")
    assert proof["passed"]
    assert proof["configuration_count"] == 4
    assert proof["source_count"] == 8
    assert proof["checkpoint_count"] == 2
    assert len(proof["results_sha256"]) == 64
    assert set(proof["checkpoints"]) == {
        "checkpoint.previous.json",
        "checkpoint.latest.json",
    }
    assert proof["processed_records"] == sum(row["records"] for row in sources)


def test_calendar_statistics_exact_parallel_codec_and_recovery(
    tmp_path: Path, engine: Path
) -> None:
    sources = calendar_fixtures(tmp_path / "fixtures")
    configs: list[dict[str, Any]] = [
        dict(expanded_configuration(index)) for index in (0, 1, 28, 29, 423359)
    ]
    plan = runner.make_plan(
        sources, tmp_path / "serial/engine", "validation", configurations=configs, threads=1
    )
    plan.update(
        matrix_kind=matrix.MATRIX_KIND,
        statistics_mode="monthly_v1",
        trace=True,
        chart_mode="on_demand",
        max_timestamp_groups=1,
        block_bytes=4096,
    )
    serial = runner.engine_run(engine, plan, tmp_path / "serial")
    assert verify_result(plan, Path(plan["output_dir"]))["pairs_checked"] == 40
    assert verify_performance(plan, Path(plan["output_dir"]))["monthly_scope_rows_checked"] == 320
    for row in serial["summary"]["configurations"]:
        payload, checksum = matrix.encode_result(row)
        assert matrix.decode_result(payload, checksum) == row
        total = row["performance"]["total"]
        assert len(total["monthly"]) == 8
        assert total["overall"]["sample_months"] == 7
        assert not total["monthly"][-1]["complete_month"]
        assert total["monthly"][-1]["sample_months"] == 7
        assert F(total["overall"]["total_pnl"]) == F(row["total"]["total_pnl"])
        assert total["overall"]["spent"] == row["total"]["spent"]
        assert total["overall"]["peak_spent"] == row["total"]["peak_spent"]
    parallel_plan = dict(
        plan,
        threads=32,
        scheduler="dynamic",
        block_bytes=16 * 1024**2,
        output_dir=str(tmp_path / "parallel/engine"),
    )
    parallel = runner.engine_run(engine, parallel_plan, tmp_path / "parallel")
    assert parallel["summary"]["configurations"] == serial["summary"]["configurations"]
    stopped_plan = dict(
        plan,
        threads=32,
        stop_after_blocks=3,
        checkpoint_seconds=0,
        output_dir=str(tmp_path / "stopped/engine"),
    )
    runner.engine_run(engine, stopped_plan, tmp_path / "stopped", expected_codes=(3,))
    resumed = tmp_path / "resumed"
    resumed.mkdir()
    resumed_plan = runner.copy_checkpoint(engine, tmp_path / "stopped", resumed)
    restored = runner.engine_run(engine, resumed_plan, resumed)
    assert restored["summary"]["configurations"] == serial["summary"]["configurations"]
    # Corrupt an actual saved month, not its hash: the independent auditor must
    # reject changed accounting even when the rest of the engine result matches.
    summary_path = Path(plan["output_dir"]) / "summary.json"
    bad = copy.deepcopy(serial["summary"])
    bad["configurations"][0]["performance"]["total"]["monthly"][0]["fees"] = "0/1"
    runner.write_json(summary_path, bad)
    with pytest.raises(ValueError, match="monthly mismatch"):
        verify_performance(plan, Path(plan["output_dir"]))


def test_empty_window_has_zero_finances_and_explicit_unavailable_ratios(
    tmp_path: Path, engine: Path
) -> None:
    sources = calendar_fixtures(tmp_path / "fixtures")
    start = month_end(sources[0]["last_ms"])
    plan = runner.make_plan(
        sources,
        tmp_path / "empty/engine",
        "validation",
        configurations=[dict[str, Any](expanded_configuration(0))],
        threads=1,
    )
    plan.update(
        matrix_kind=matrix.MATRIX_KIND,
        statistics_mode="monthly_v1",
        start_ms=start,
        end_ms=month_end(month_end(start)),
    )
    outcome = runner.engine_run(engine, plan, tmp_path / "empty")
    assert verify_performance(plan, Path(plan["output_dir"]))["monthly_scope_rows_checked"] == 16
    total = outcome["summary"]["configurations"][0]["performance"]["total"]["overall"]
    assert total["total_pnl"] == total["spent"] == total["peak_spent"] == "0/1"
    assert total["profit_factor"] is None and total["profit_factor_status"] == "no_closures"
    assert total["sharpe"] is None and total["sharpe_status"] == "zero_variance"
    assert total["sortino"] is None and total["sortino_status"] == "no_downside"
    for venue in outcome["summary"]["configurations"][0]["performance"]["venues"]:
        assert len(venue["monthly"]) == 2
        assert venue["overall"]["sample_months"] == 2
        assert venue["overall"]["sharpe_status"] == "zero_variance"


def test_venue_risk_uses_only_its_own_observed_calendar(tmp_path: Path, engine: Path) -> None:
    all_sources = calendar_fixtures(tmp_path / "fixtures")

    def timestamp(value: str) -> int:
        return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000)

    selected_dates = {
        "okx": "2024-01-01T00:00",
        "htx": "2024-01-31T23:59",
        "gate": "2024-03-01T00:00",
        "kucoin": "2024-04-01T00:00",
        "bingx": "2024-08-15T12:00",
    }
    sources = [
        source
        for source in all_sources
        if source["venue"] in selected_dates and source["symbol"] != "Calendar7/USDT"
    ]
    for source in sources:
        records = list(struct.iter_unpack("<qffff", Path(source["path"]).read_bytes()))
        records = [row for row in records if row[0] == timestamp(selected_dates[source["venue"]])]
        assert len(records) == 1
        payload = b"".join(struct.pack("<qffff", *row) for row in records)
        Path(source["path"]).write_bytes(payload)
        source.update(
            sha256=hashlib.sha256(payload).hexdigest(),
            records=len(records),
            first_ms=records[0][0],
            last_ms=records[-1][0],
        )
    plan = runner.make_plan(
        sources,
        tmp_path / "scoped/engine",
        "validation",
        configurations=[dict[str, Any](expanded_configuration(0))],
        threads=1,
    )
    source_ids = sorted(source["id"] for source in sources)
    plan.update(
        matrix_kind=matrix.MATRIX_KIND,
        statistics_mode="monthly_v1",
        trace=True,
        source_selection={
            "history_policy": "gap_free_own_span",
            "expected_venues": ["bingx", "gate", "htx", "kucoin", "okx"],
            "manifest_sha256": "a" * 64,
            "source_ids": source_ids,
            "selected_source_ids": source_ids,
        },
    )
    outcome = runner.engine_run(engine, plan, tmp_path / "scoped")
    assert verify_performance(plan, Path(plan["output_dir"]))["configurations"] == 1
    summary = outcome["summary"]
    assert summary["statistics_calendar_scope"] == "venue_own_observed_span_v1"
    performance = summary["configurations"][0]["performance"]
    assert performance["total"]["overall"]["sample_months"] == 7
    by_venue = {row["venue"]: row for row in performance["venues"]}
    assert by_venue["gate"]["overall"]["sample_months"] == 0
    assert by_venue["gate"]["monthly"][0]["start_ms"] == timestamp("2024-03-01T00:00")
    assert by_venue["gate"]["monthly"][-1]["end_ms"] == timestamp("2024-03-01T00:01")
    assert by_venue["bingx"]["overall"]["sample_months"] == 0
    assert by_venue["bingx"]["monthly"][-1]["end_ms"] == timestamp("2024-08-15T12:01")

    limited = copy.deepcopy(plan)
    limited["output_dir"] = str(tmp_path / "limited/engine")
    limited["max_records"] = 1
    limited_outcome = runner.engine_run(engine, limited, tmp_path / "limited")
    assert verify_performance(limited, Path(limited["output_dir"]))["configurations"] == 1
    early = limited_outcome["summary"]["configurations"][0]["performance"]
    for venue in early["venues"]:
        if venue["venue"] != "okx":
            assert venue["monthly"] == []
            assert venue["overall"]["start_ms"] == venue["overall"]["end_ms"]


def test_binary_period_rejects_invalid_denominator_and_field_loss() -> None:
    row: dict[str, Any] = {name: None for name in matrix.PERIOD_FIELDS}
    row["spent"] = "1/0"
    encoded = matrix._pack_period(row)
    with pytest.raises(ValueError, match="denominator"):
        matrix._unpack_period(encoded)
    row["spent"] = "1/1"
    row["unknown_financial_field"] = "1/1"
    with pytest.raises(ValueError, match="unknown monthly"):
        matrix._pack_period(row)
