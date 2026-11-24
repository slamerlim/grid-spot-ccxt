"""Integrity audit must retain bad data, exact gap boundaries and every source."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import pytest

from scripts.audit_archive_integrity import export_gaps, run, scan_file

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("numpy") is None,
    reason="standalone archive audit uses the existing audited-simulator NumPy environment",
)
RECORD = struct.Struct("<qffff")
START = 1_600_000_020_000


def source(root: Path, name: str, stamps: list[int], *, trailer: bytes = b"") -> Path:
    path = root / name
    path.write_bytes(b"".join(RECORD.pack(stamp, 10, 11, 9, 10) for stamp in stamps) + trailer)
    return path


def test_exact_gap_crossing_read_blocks_and_whole_source_hash(tmp_path: Path) -> None:
    path = source(tmp_path, "gate_USDT_BTC.bin", [START, START + 60_000, START + 300_000])
    gaps = tmp_path / "gaps"
    gaps.mkdir()
    result = scan_file(path, gaps, audit_ms=START + 900_000, chunk_rows=2, gap_format="csv")
    assert result["classification"] == "VALID_WITH_GAPS"
    assert result["complete_records"] == 3
    assert result["gap_count"] == 1
    assert result["missing_minutes"] == result["largest_gap_minutes"] == 3
    assert result["span_minutes"] == 6
    assert result["coverage_pct"] == 50
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["source_unchanged"] is True
    with (gaps / (path.name + ".gaps.csv")).open() as stream:
        entries = list(csv.DictReader(stream))
    assert len(entries) == 1
    assert int(entries[0]["previous_ms"]) == START + 60_000
    assert int(entries[0]["next_ms"]) == START + 300_000


def test_nonadjacent_duplicates_and_disorder_have_exact_sorted_gaps(tmp_path: Path) -> None:
    path = source(tmp_path, "htx_BTC_ETH.bin", [START, START + 180_000, START, START + 60_000])
    gaps = tmp_path / "gaps"
    gaps.mkdir()
    result = scan_file(path, gaps, audit_ms=START + 900_000, chunk_rows=2)
    assert result["classification"] == "INVALID"
    assert result["error_counts"]["descending_adjacent_rows"] == 1
    assert result["duplicate_timestamp_rows"] == 1
    assert result["unique_valid_timestamp_rows"] == 3
    assert result["gap_count"] == result["missing_minutes"] == 1
    assert result["span_minutes"] == 4
    assert not list(gaps.glob("timestamp-index-*"))


def test_bad_values_trailer_and_future_are_retained(tmp_path: Path) -> None:
    path = tmp_path / "gate_USDT_BAD.bin"
    raw = (
        b"".join(
            [
                RECORD.pack(START, 0, 0, 0, 0),
                RECORD.pack(START + 60_000, float("nan"), 1, 1, 1),
                RECORD.pack(START + 120_000, 10, 9, 11, 10),
                RECORD.pack(START + 180_001, 10, 11, 9, 10),
                RECORD.pack(-(2**63), 10, 11, 9, 10),
                RECORD.pack(2**63 - 1, 10, 11, 9, 10),
            ]
        )
        + b"bad"
    )
    path.write_bytes(raw)
    gaps = tmp_path / "gaps"
    gaps.mkdir()
    result = scan_file(path, gaps, audit_ms=START + 180_000, chunk_rows=2)
    assert result["complete_records"] == 6
    assert result["trailing_bytes"] == 3
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["classification"] == "INVALID"
    assert result["error_counts"]["invalid_value_rows"] == 6
    assert result["error_counts"]["nonfinite_price_rows"] == 1
    assert result["error_counts"]["nonpositive_price_rows"] == 1
    assert result["error_counts"]["invalid_ohlc_rows"] == 1
    assert result["error_counts"]["timestamp_out_of_range_rows"] == 2
    assert result["error_counts"]["timestamp_after_audit_rows"] == 2
    assert result["gap_count"] == 0
    assert result["unique_valid_timestamp_rows"] == 3
    json.dumps(result, allow_nan=False)


def test_all_matching_sources_including_empty_and_aliases_are_reported(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source(root, "htx_USDT_BTC.bin", [START, START + 60_000])
    source(root, "huobi_USDT_ETH.bin", [])
    source(root, "gate_USDT_BTC3L.bin", [START])
    source(root, "gateio_BTC_ETH.bin", [START])
    source(root, "binance_USDT_BTC.bin", [START])
    summary = run(root, tmp_path / "output")
    assert summary["files_in_inventory"] == summary["files_in_results"] == 4
    assert summary["all_sources_read"] is True
    assert summary["roster_unchanged"] is True
    assert summary["totals"]["htx"]["classifications"] == {"VALID_CONTIGUOUS": 1, "INVALID": 1}
    assert summary["totals"]["gate"]["suspect_leveraged_name"] == 1
    assert summary["totals"]["gate"]["files"] == 2
    with pytest.raises(ValueError, match="outside"):
        run(root, root / "unsafe-output")


def test_compact_ranges_export_exactly_matches_csv_across_blocks(tmp_path: Path) -> None:
    path = source(tmp_path, "gate_USDT_BTC.bin", [START + index * 180_000 for index in range(257)])
    binary, text = tmp_path / "binary", tmp_path / "text"
    binary.mkdir()
    text.mkdir()
    compact = scan_file(path, binary, audit_ms=START + 999_000_000, chunk_rows=17)
    original = scan_file(path, text, audit_ms=START + 999_000_000, chunk_rows=29, gap_format="csv")
    for key in ("sha256", "gap_count", "missing_minutes", "largest_gap_minutes", "coverage_pct"):
        assert compact[key] == original[key]
    assert Path(compact["gap_artifact"]).stat().st_size == compact["gap_count"] * 16
    exported = tmp_path / "export.csv"
    assert export_gaps(Path(compact["gap_artifact"]), exported) == compact["gap_count"]
    assert exported.read_bytes() == Path(original["gap_artifact"]).read_bytes()


def test_checkpoint_retains_complete_sources_but_rescans_partial_result(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source(root, "gate_USDT_BTC.bin", [START, START + 180_000])
    second = source(root, "htx_USDT_ETH.bin", [START, START + 60_000])
    prior = tmp_path / "prior"
    run(root, prior)
    lines = (prior / "files.jsonl").read_bytes().splitlines(keepends=True)
    (prior / "files.jsonl").write_bytes(lines[0] + lines[1][:20])
    target = tmp_path / "resumed"
    summary = run(root, target, resume=prior)
    assert summary["checkpoint_files_reused"] == 1
    assert summary["files_in_results"] == 2
    assert summary["all_sources_read"] is True
    rows = [json.loads(line) for line in (target / "files.jsonl").read_text().splitlines()]
    assert rows[0]["checkpoint_origin"] == str(prior)
    assert rows[1]["gap_artifact"].startswith(str(target))
    second.write_bytes(second.read_bytes() + RECORD.pack(START + 120_000, 10, 11, 9, 10))
    with pytest.raises(ValueError, match="checkpoint inventory differs"):
        run(root, tmp_path / "changed", resume=prior)


def test_bad_filename_and_nonadjacent_invalid_timestamp_duplicates(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source(root, "gate_bad.bin", [-60_000, START, -60_000, START + 180_000])
    target = tmp_path / "report"
    summary = run(root, target)
    assert summary["totals"]["gate"]["files"] == 1
    row = json.loads((target / "files.jsonl").read_text())
    assert row["classification"] == "INVALID"
    assert row["duplicate_timestamp_rows"] == 1
    assert row["unique_valid_timestamp_rows"] == 2
    assert row["gap_count"] == 1
    assert row["missing_minutes"] == 2
