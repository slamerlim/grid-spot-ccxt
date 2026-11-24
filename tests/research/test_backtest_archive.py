"""Selected archive files become immutable, bounded current-engine inputs."""

from __future__ import annotations

import struct
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts import backtest_archive as archive
from scripts.backtest_legacy_n import ArchiveCatalog
from scripts.module1_study import source_token, token
from scripts.module1_verify_sources import identity, sha256

FIRST = 1_735_689_600_000  # 2025-01-01 00:00 UTC


def stamp(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def source(root: Path, filename: str, offsets: tuple[int, ...] = (0, 1, 3)) -> Path:
    path = root / filename
    path.write_bytes(
        b"".join(struct.pack("<qffff", FIRST + i * 60_000, 10, 11, 9, 10) for i in offsets)
    )
    return path


def catalog(tmp_path: Path) -> ArchiveCatalog:
    root = tmp_path / "data"
    root.mkdir()
    source(root, "gate_USDT_ABC.bin")
    source(root, "okx_USDT_XYZ.bin")
    return ArchiveCatalog(root, tmp_path / "no-snapshots")


def build(
    inventory: ArchiveCatalog,
    ids: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return archive.build_manifest(
        inventory,
        ids if ids is not None else ["gate:ABC/USDT"],
        start or stamp(FIRST),
        end or stamp(FIRST + 4 * 60_000),
        assert_spot=kwargs.get("assert_spot", True),
    )


def test_builds_multi_source_manifest_without_changing_archive(tmp_path: Path) -> None:
    inventory = catalog(tmp_path)
    paths = [inventory.root / row["file"] for row in inventory.rows.values()]
    original = {p: p.read_bytes() for p in paths}
    result = build(inventory, ["okx:XYZ/USDT", "gate:ABC/USDT"])
    assert result["spot_identity"] == "USER_ASSUMPTION_NOT_VERIFIED"
    assert result["quote"] == "USDT"
    assert result["coverage"]["common_end_utc_exclusive"] == stamp(FIRST + 4 * 60_000)
    assert [(row["venue"], row["market"]) for row in result["selected"]] == [
        ("gate", "ABC/USDT"),
        ("okx", "XYZ/USDT"),
    ]
    for row in result["selected"]:
        path = Path(row["source_path"])
        assert row["sha256"] == sha256(path)
        assert row["bytes"] == len(original[path])
        assert row["rows"] == row["aligned_rows"] == 3
        assert row["file_identity_before_and_after"] == list(identity(path))
        assert row["structural_errors"] == 0
        assert path.read_bytes() == original[path]


@pytest.mark.parametrize(
    ("ids", "start", "end", "assert_spot", "message"),
    [
        (["gate:ABC/USDT"], None, None, False, "spot-market assumption"),
        (["gate:ABC/USDT", "gate:ABC/USDT"], None, None, True, "distinct"),
        (["unknown:ABC/USDT"], None, None, True, "archive markets"),
        (["gate:ABC/USDT"], stamp(FIRST + 1), None, True, "whole-minute"),
        (["gate:ABC/USDT"], stamp(FIRST + 60_000), stamp(FIRST), True, "increasing"),
        (["gate:ABC/USDT"], None, stamp(FIRST + 5 * 60_000), True, "coverage"),
    ],
)
def test_rejects_invalid_selection_or_window(
    tmp_path: Path,
    ids: list[str],
    start: str | None,
    end: str | None,
    assert_spot: bool,
    message: str,
) -> None:
    inventory = catalog(tmp_path)
    with pytest.raises(ValueError, match=message):
        build(inventory, ids, start, end, assert_spot=assert_spot)


def test_non_usdt_and_suspect_leveraged_names_are_refused(tmp_path: Path) -> None:
    inventory = catalog(tmp_path)
    source(inventory.root, "gate_USD_ABC.bin")
    source(inventory.root, "okx_USD_XYZ.bin")
    source(inventory.root, "gate_USDT_BULL.bin")
    source(inventory.root, "gate_USDT_XYZ3L.bin")
    inventory = ArchiveCatalog(inventory.root, tmp_path / "no-snapshots")
    with pytest.raises(ValueError, match="USDT-only"):
        build(inventory, ["gate:ABC/USD", "okx:XYZ/USD"])
    with pytest.raises(ValueError, match="USDT-only"):
        build(inventory, ["gate:ABC/USD", "gate:ABC/USDT"])
    for key in ("gate:BULL/USDT", "gate:XYZ3L/USDT"):
        with pytest.raises(ValueError, match="leveraged"):
            build(inventory, [key])


def test_source_utf8_gate_does_not_relax_other_tsv_fields() -> None:
    assert source_token("果蝇/USDT", 128) == "果蝇/USDT"
    for invalid in ("A\tB", "A\nB", "A\udcffB", "A" * 129):
        with pytest.raises(ValueError):
            source_token(invalid, 128)
    with pytest.raises(ValueError, match="ASCII"):
        token("果蝇")


@pytest.mark.parametrize(
    "offsets, message",
    [
        ((0, 1, 1), "duplicate_or_out_of_order"),
        ((0, 2, 1), "duplicate_or_out_of_order"),
        ((0, 1, 3), "invalid_ohlc"),
    ],
)
def test_full_file_validation_rejects_bad_interior_record(
    tmp_path: Path, offsets: tuple[int, ...], message: str
) -> None:
    inventory = catalog(tmp_path)
    path = source(inventory.root, "gate_USDT_ABC.bin", offsets)
    if message == "invalid_ohlc":
        data = bytearray(path.read_bytes())
        data[24 + 16 : 24 + 20] = struct.pack("<f", 12)  # Low above the open/close.
        path.write_bytes(data)
    with pytest.raises(ValueError, match=message):
        build(inventory)


def test_missing_window_rows_and_incomplete_record_are_rejected(tmp_path: Path) -> None:
    inventory = catalog(tmp_path)
    path = source(inventory.root, "gate_USDT_ABC.bin", (0, 10))
    with pytest.raises(ValueError, match="No observations"):
        build(inventory, start=stamp(FIRST + 60_000), end=stamp(FIRST + 2 * 60_000))
    with path.open("ab") as stream:
        stream.write(b"x")
    with pytest.raises(ValueError, match="incomplete 24-byte records"):
        build(inventory)


def test_current_cpp_source_bound_is_enforced_before_scan(tmp_path: Path) -> None:
    inventory = catalog(tmp_path)
    keys = ["gate:ABC/USDT"]
    for index in range(64):
        base = f"T{index:02}"
        source(inventory.root, f"gate_USDT_{base}.bin")
        keys.append(f"gate:{base}/USDT")
    inventory = ArchiveCatalog(inventory.root, tmp_path / "no-snapshots")
    with pytest.raises(ValueError, match="1–64"):
        build(inventory, keys)
