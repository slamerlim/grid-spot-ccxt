import asyncio
import json
from pathlib import Path

import pytest
from persistence.conftest import SOURCE_ID

from grid.operations.__main__ import main
from grid.operations.experiment import benchmark_case


@pytest.mark.usefixtures("async_sockets")
def test_synthetic_report_distinguishes_wall_time_and_never_certifies_soak(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    args = [
        "dry-run",
        "--output",
        str(output),
        "--code-revision",
        "a" * 40,
        "--sqlite-source-id",
        SOURCE_ID,
    ]
    assert main(args) == 0
    report = json.loads(output.read_text())
    assert report["mode"] == "synthetic_offline" and report["live"] == "DISABLED"
    assert not report["observed_soak_complete"] and not report["i8_acceptance_complete"]
    assert report["observed_wall_ns"] > 0
    assert len(report["grid_python_source_sha256"]) == 64
    result = report["results"][0]
    assert result["simulated_elapsed_ms"] == 40
    assert result["process_restart_count"] == 0 and result["controller_restart_count"] == 1
    assert all(result["checks"].values())
    assert result["final_status"]["final_return"] == "0.1"
    assert result["final_status"]["exit_shortfall"] == "0"
    assert result["metrics"]["external_api_calls"] == 0
    original = output.read_bytes()
    assert main(args) == 2 and output.read_bytes() == original


@pytest.mark.usefixtures("async_sockets")
def test_benchmark_distinguishes_candidates_from_funded_allocations(tmp_path: Path) -> None:
    result = asyncio.run(benchmark_case(tmp_path, frozenset({SOURCE_ID}), 7, 10))
    assert result["candidate_count"] == 10 and result["supervised_allocations"] == 7
    assert result["eligible_candidates"] == 10 and result["selected_candidates"] == 10
    assert result["final_admission_halted"] is False
    metrics = result["metrics"]
    assert isinstance(metrics, dict) and metrics["synthetic_limit_submissions"] == 7
    assert metrics["ccxt_clients"] == 0 and metrics["synthetic_gateway_objects"] == 1


@pytest.mark.usefixtures("async_sockets")
def test_bad_source_leaves_no_successful_report_or_raw_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "bad.json"
    assert (
        main(
            [
                "dry-run",
                "--output",
                str(output),
                "--code-revision",
                "a" * 40,
                "--sqlite-source-id",
                "unapproved-sensitive-value",
            ]
        )
        == 2
    )
    assert not output.exists()
    assert "unapproved-sensitive-value" not in capsys.readouterr().err
