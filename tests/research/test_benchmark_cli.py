import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from grid.domain.numbers import DomainError
from grid.research.__main__ import main
from grid.research.artifacts import output_directory, publish
from grid.research.benchmark import MAX_BENCH_RECORDS, benchmark, columnar_records, device_evidence
from grid.research.config import bundle, load
from grid.research.history import RECORD
from grid.research.replay import run

from .conftest import MakeReplay


def test_bounded_formats_byte_exact_and_same_financial_replay(
    make_replay: MakeReplay,
    tmp_path: Path,
) -> None:
    spec = make_replay(
        (
            (100.12, 101.12, 99.12, 100.12),
            (100, 101, 99, 100),
            (110, 115, 100, 110),
            (100, 101, 99, 100),
        )
    )
    source = spec.allocations[0].source
    original = source.path.read_bytes()
    original_stat = source.path.stat()
    result = benchmark(source, tmp_path / "derived", repeats=1)
    timings: Any = result["timings"]
    assert set(timings) == {"raw_sequential", "raw_mmap", "arrow", "parquet"}
    expected = hashlib.sha256(original).hexdigest()
    assert all(t["roundtrip_sha256"] == expected for t in timings.values())
    assert "uncontrolled" in str(result["cache"])
    assert "status" in str(result["source_device"])
    reference = run(spec)
    for kind in ("arrow", "parquet"):
        derived_path = Path(str(result["sample_directory"])) / f"sample.{kind}"
        recovered = b"".join(columnar_records(derived_path, kind))
        assert recovered == original
        reconstructed = tmp_path / f"{kind}.bin"
        reconstructed.write_bytes(recovered)
        a = spec.allocations[0]
        rerun = run(
            replace(spec, allocations=(replace(a, source=replace(source, path=reconstructed)),))
        )
        assert rerun["allocations"] == reference["allocations"]
        assert rerun["chronology_sha256"] == reference["chronology_sha256"]
    assert source.path.read_bytes() == original
    assert source.path.stat().st_mtime_ns == original_stat.st_mtime_ns
    with pytest.raises(DomainError):
        benchmark(replace(source, count=MAX_BENCH_RECORDS + 1), tmp_path, repeats=1)


def test_cli_demo_validate_replay_and_exclusive_outputs(tmp_path: Path) -> None:
    output = tmp_path / "demo"
    assert main(["demo", "--output-directory", str(output), "--code-revision", "test"]) == 0
    config = output / "replay.json"
    assert main(["replay", "--config", str(config), "--output", str(output / "result.json")]) == 0
    assert main(["replay", "--config", str(config), "--output", str(output / "result.json")]) == 2
    arguments = [
        "validate",
        str(output / "synthetic.bin"),
        "--venue",
        "synthetic",
        "--symbol",
        "SYNTH/USD",
        "--count",
        "7",
        "--output",
        str(output / "validation.json"),
        "--code-revision",
        "test",
    ]
    assert main(arguments) == 1  # Filenames do not magically establish provenance.
    manifest = json.loads((output / "validation.json").read_bytes())
    assert not manifest["replay_eligible"]
    assert manifest["manifest"]["accepted"] == 7


@pytest.mark.parametrize("change", ["unknown_field", "duplicate_key", "bad_decimal", "oversize"])
def test_strict_configuration(make_replay: MakeReplay, tmp_path: Path, change: str) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 3)
    data: Any = bundle(spec)
    if change == "unknown_field":
        data["live"] = True
    elif change == "bad_decimal":
        data["synthetic_spread"] = 0.001
    raw = json.dumps(data)
    if change == "duplicate_key":
        raw = raw.replace('"schema":', '"schema":"wrong","schema":', 1)
    elif change == "oversize":
        raw = " " * (1024 * 1024 + 1)
    path = tmp_path / "bad.json"
    path.write_text(raw)
    with pytest.raises(DomainError):
        load(path)


def test_no_original_or_git_output_and_symlink_overwrite(tmp_path: Path) -> None:
    for path in (
        Path("/opt/datasets/derived"),
        Path("/opt/module_1_multi/reports"),
        Path(__file__).resolve().parents[2] / "generated",
    ):
        with pytest.raises(DomainError, match="outside Git"):
            output_directory(path)
    original = tmp_path / "original"
    original.write_bytes(RECORD.pack(60000, 1, 1, 1, 1))
    link = tmp_path / "result.json"
    link.symlink_to(original)
    with pytest.raises(FileExistsError):
        publish(link, {"synthetic": True})
    assert original.read_bytes() == RECORD.pack(60000, 1, 1, 1, 1)


def test_unknown_hardware_is_not_certified(tmp_path: Path) -> None:
    assert device_evidence(tmp_path, tmp_path / "missing-sysfs")["status"] == "BLOCKED"
