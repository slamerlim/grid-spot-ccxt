"""Selection uses structural quality/coverage, never strategy returns."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from scripts.module1_selection import audit

START = 1_704_067_200_000  # 2024-01-01 UTC.


def history(path: Path, *, missing: set[int] | None = None, days: int = 2) -> None:
    omitted = missing or set()
    with path.open("xb") as stream:
        for minute in range(days * 1440):
            if minute not in omitted:
                stream.write(audit.RECORD.pack(START + minute * audit.MINUTE, 1, 2, 0.5, 1.5))


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (audit.RECORD.pack(START, 0, 0, 0, 0), "invalid_price"),
        (audit.RECORD.pack(START, -1, 2, 0.5, 1), "invalid_price"),
        (audit.RECORD.pack(START, float("nan"), 2, 0.5, 1), "invalid_price"),
        (audit.RECORD.pack(START, 1, float("inf"), 0.5, 1), "invalid_price"),
        (audit.RECORD.pack(START, 2, 1, 0.5, 1), "invalid_ohlc"),
        (audit.RECORD.pack(START + 1, 1, 2, 0.5, 1), "invalid_timestamp"),
        (struct.pack(">qffff", START, 1, 2, 0.5, 1), "invalid_timestamp"),
        (audit.RECORD.pack(START, 1, 2, 0.5, 1) * 2, "duplicate"),
        (
            audit.RECORD.pack(START + audit.MINUTE, 1, 2, 0.5, 1)
            + audit.RECORD.pack(START, 1, 2, 0.5, 1),
            "disorder",
        ),
        (b"partial", "malformed_length"),
    ],
)
def test_decoder_rejections(tmp_path: Path, payload: bytes, reason: str) -> None:
    path = tmp_path / "input.bin"
    path.write_bytes(payload)
    result = audit.scan(path, tmp_path, audit.identity(path.lstat()))
    assert result["errors"][reason] == 1
    assert path.read_bytes() == payload


def test_decoder_hash_gap_float32_and_flat_rows(tmp_path: Path) -> None:
    path = tmp_path / "input.bin"
    raw = audit.RECORD.pack(START, 0.1, 0.2, 0.05, 0.15)
    raw += audit.RECORD.pack(START + 3 * audit.MINUTE, 1, 1, 1, 1)
    path.write_bytes(raw)
    result = audit.scan(path, tmp_path, audit.identity(path.lstat()))
    assert result["rows"] == 2
    assert result["missing_minutes"] == 2
    assert result["gap_count"] == 1
    assert result["flat_rows"] == 1
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    assert not result["errors"]
    assert result["complete_utc_days"] == []


def test_round_robin_distinct_bases_aliases_and_reproducibility(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    for file in ("okx_USDT_A.bin", "okx_USDT_B.bin", "huobi_USDT_A.bin", "huobi_USDT_C.bin"):
        history(root / file)
    first = audit.select(root, tmp_path / "one", count=2, minimum_days=1)
    second = audit.select(root, tmp_path / "two", count=2, minimum_days=1)
    assert first == second
    assert [item["file"] for item in first["selected"]] == ["okx_USDT_A.bin", "huobi_USDT_C.bin"]
    assert first["longest_consecutive_complete_utc_days"] == 2
    assert "duplicate_base" in (tmp_path / "one/candidate_decisions.csv").read_text()
    assert first["source_status"] == audit.STATUS
    assert audit.ALIASES["gateio"] == "gate"


def test_rejects_leveraged_quote_short_and_sparse_sources(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    history(root / "okx_USDT_A3L.bin")
    history(root / "okx_BTC_B.bin")
    history(root / "okx_USDT_C.bin", days=1)
    history(root / "okx_USDT_D.bin", missing=set(range(100, 200)))
    result = audit.select(root, tmp_path / "out", count=1, minimum_days=2)
    assert not result["requested_cohort_complete"]
    decisions = (tmp_path / "out/candidate_decisions.csv").read_text()
    for reason in ("suspect_leveraged_name", "different_quote", "under_required_days_or_rows"):
        assert reason in decisions
    assert "endpoint_density_below_99pct" in decisions


def test_full_scan_invalid_middle_rejects_candidate(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    path = root / "okx_USDT_A.bin"
    history(path)
    with path.open("r+b") as stream:
        stream.seek(20 * audit.RECORD.size)
        stream.write(audit.RECORD.pack(START + 20 * audit.MINUTE, 0, 0, 0, 0))
    history(root / "okx_USDT_B.bin")
    result = audit.select(root, tmp_path / "out", count=1, minimum_days=1)
    assert result["selected"][0]["file"] == "okx_USDT_B.bin"
    assert "full_file_structural_errors" in (tmp_path / "out/candidate_decisions.csv").read_text()


def test_shared_days_are_not_concatenated_into_contiguous_year(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    history(root / "okx_USDT_A.bin", missing={10}, days=3)
    history(root / "htx_USDT_B.bin", missing={1450}, days=3)
    result = audit.select(root, tmp_path / "out", count=2, minimum_days=2)
    assert not result["requested_cohort_complete"]
    assert (
        "insufficient_common_complete_utc_days"
        in (tmp_path / "out/candidate_decisions.csv").read_text()
    )
    assert audit.longest_run({1, 2, 4, 6, 7, 8}) == 3


def test_source_identity_symlink_and_no_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    path = root / "okx_USDT_A.bin"
    history(path)
    (root / "htx_USDT_B.bin").symlink_to(path)
    before = (hashlib.sha256(path.read_bytes()).hexdigest(), audit.identity(path.lstat()))
    result = audit.select(root, tmp_path / "out", count=1, minimum_days=1)
    assert result["requested_cohort_complete"]
    assert before == (hashlib.sha256(path.read_bytes()).hexdigest(), audit.identity(path.lstat()))
    assert "not_regular_nonsymlink" in (tmp_path / "out/candidate_decisions.csv").read_text()
    with pytest.raises(FileExistsError):
        audit.select(root, tmp_path / "out", count=1, minimum_days=1)
    with pytest.raises(ValueError, match="outside original"):
        audit.select(root, root / "outputs", count=1, minimum_days=1)
    wrong = (0, 0, 0, 0, 0)
    with pytest.raises(ValueError, match="identity mismatch"):
        audit.scan(path, tmp_path, wrong)


def test_rule_is_saved_before_scanning_and_scan_cap_is_explicit(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    for base in ("A", "B"):
        path = root / f"okx_USDT_{base}.bin"
        history(path)
        with path.open("r+b") as stream:
            stream.seek(audit.RECORD.size)
            stream.write(audit.RECORD.pack(START + audit.MINUTE, 0, 0, 0, 0))
    with pytest.raises(ValueError, match="scan limit"):
        audit.select(root, tmp_path / "out", count=1, minimum_days=1, max_scans=1)
    rule = json.loads((tmp_path / "out/selection_rule.json").read_text())
    assert rule["selection_before_strategy_returns"]
    assert rule["max_full_candidate_scans"] == 1
    assert not (tmp_path / "out/selection_manifest.json").exists()
