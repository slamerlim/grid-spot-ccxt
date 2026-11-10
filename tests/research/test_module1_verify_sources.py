"""Source verification compares evidence, retains failure, and bounds child output."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts import module1_verify_sources as verify


def expected() -> dict[str, Any]:
    return {
        "bytes": 24,
        "rows": 1,
        "first_utc": "2024-01-01T00:00:00+00:00",
        "last_utc": "2024-01-01T00:00:00+00:00",
        "missing_minutes": 0,
        "gap_count": 0,
        "sha256": "f" * 64,
    }


def result() -> dict[str, Any]:
    return {
        "schema": "grid-source-scan-v1",
        "bytes": 24,
        "records": 1,
        "accepted": 1,
        "rejected": 0,
        "first_ms": 1704067200000,
        "last_ms": 1704067200000,
        "missing_minutes": 0,
        "gap_count": 0,
        "sha256": "f" * 64,
        "complete": True,
        "reasons": {},
        "rejection_examples": [],
    }


def test_comparison_requires_full_matching_evidence() -> None:
    actual = result()
    assert verify.compare_scan(actual, expected()) == []
    actual["complete"] = 1
    actual["sha256"] = "0" * 64
    actual["rejected"] = 1
    assert set(verify.compare_scan(actual, expected())) == {"complete", "sha256", "rejected"}
    del actual["bytes"]
    assert "bytes" in verify.compare_scan(actual, expected())


def test_child_status_resources_and_exclusive_outputs(tmp_path: Path) -> None:
    command = [sys.executable, "-c", "print('small result')"]
    receipt = verify.run_child(command, tmp_path, 5)
    assert receipt["command"] == command
    assert receipt["exit_code"] == 0
    assert receipt["peak_rss_kib"] > 0
    assert receipt["user_cpu_seconds"] >= 0
    assert receipt["output_bytes"] == len(b"small result\n")
    with pytest.raises(FileExistsError):
        verify.run_child(command, tmp_path, 5)


def test_child_timeout_reaps_only_its_owned_process(tmp_path: Path) -> None:
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    receipt = verify.run_child(command, tmp_path, 0.05)
    assert receipt["timed_out"]
    assert receipt["exit_code"] == -9


def test_output_size_is_bounded(tmp_path: Path) -> None:
    command = [sys.executable, "-c", "import os; os.write(1,b'x'*2000000); os.write(1,b'x')"]
    receipt = verify.run_child(command, tmp_path, 5)
    assert receipt["exit_code"] != 0
    assert (tmp_path / "stdout.json").stat().st_size <= verify.MAX_LOG_BYTES


def test_manifest_count_and_utc_guards(tmp_path: Path) -> None:
    manifest = tmp_path / "selection.json"
    manifest.write_text(json.dumps({"selected": []}))
    with pytest.raises(ValueError, match="exactly ten"):
        verify.verify(Path(sys.executable), manifest, tmp_path / "out")
    with pytest.raises(ValueError, match="UTC"):
        verify.utc_ms("2024-01-01T00:00:00+01:00")
    assert verify.utc_ms("2024-01-01T00:00:00+00:00") == 1704067200000
