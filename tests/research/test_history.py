import hashlib
import struct
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from grid.domain.numbers import DomainError
from grid.research.history import (
    MINUTE,
    RECORD,
    Source,
    decode,
    records,
    rounding_radius,
    validate,
    validated_bars,
)

from .conftest import START, MakeReplay


@pytest.mark.parametrize("mode", ["sequential", "mmap"])
def test_bounded_window_hash_is_not_whole_file(make_replay: MakeReplay, mode: str) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 4)
    source = spec.allocations[0].source
    full = validate(source)
    assert full.whole_file_sha256 == hashlib.sha256(source.path.read_bytes()).hexdigest()
    selected = replace(source, offset=1, count=2)
    window = validate(selected, "mmap") if mode == "mmap" else validate(selected)
    assert window.accepted == 2 and window.selected_bytes == 48
    assert window.first_ms == START + MINUTE
    assert window.whole_file_sha256 is None and not window.complete_file
    assert window.window_sha256 == hashlib.sha256(source.path.read_bytes()[24:72]).hexdigest()


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (RECORD.pack(START, 0, 0, 0, 0), "nonpositive_price"),
        (RECORD.pack(START, -1, 1, -1, 1), "nonpositive_price"),
        (RECORD.pack(START, float("nan"), 101, 99, 100), "nonfinite_price"),
        (RECORD.pack(START, 100, float("inf"), 99, 100), "nonfinite_price"),
        (RECORD.pack(START, 100, 99, 98, 100), "impossible_ohlc"),
        (RECORD.pack(START + 1, 100, 101, 99, 100), "invalid_utc_minute"),
        (RECORD.pack(0, 100, 101, 99, 100), "invalid_utc_minute"),
        (struct.pack(">qffff", START, 100, 101, 99, 100), "invalid_utc_minute"),
    ],
)
def test_corruption_quarantines_raw_bytes(tmp_path: Path, raw: bytes, reason: str) -> None:
    path = tmp_path / "corrupt.bin"
    path.write_bytes(raw * 40)
    before = path.read_bytes()
    manifest = validate(Source(path, "fake", "BASE/QUOTE", 0, 40))
    assert manifest.accepted == 0 and manifest.rejected == 40
    assert manifest.reasons == {reason: 40}
    assert len(manifest.examples) == 32
    assert manifest.examples[0].raw_hex == raw.hex()
    assert path.read_bytes() == before
    assert "rejected_records" in manifest.blockers


def test_length_legacy16_and_source_bounds(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.bin"
    path.write_bytes(RECORD.pack(START, 100, 101, 99, 100) + b"!")
    source = Source(path, "fake", "BASE/QUOTE", 0, 1)
    assert "malformed_file_length" in validate(source).blockers
    with pytest.raises(DomainError, match="exceeds file"):
        validate(replace(source, count=2))
    path.write_bytes(struct.pack("<dQ", 100, START) * 3)
    assert validate(replace(source, count=2)).rejected == 2
    with pytest.raises(DomainError, match="length"):
        decode(b"short")


def test_gap_duplicate_disorder_and_unresolved_provenance(tmp_path: Path) -> None:
    path = tmp_path / "gaps.bin"
    gap = START + 468 * 24 * 60 * MINUTE
    timestamps = (START, START, START - MINUTE, gap)
    path.write_bytes(b"".join(RECORD.pack(ts, 100, 101, 99, 100) for ts in timestamps))
    manifest = validate(Source(path, "fake", "BASE/QUOTE", 0, 4))
    assert manifest.reasons == {"duplicate_minute": 1, "out_of_order": 1}
    assert manifest.missing_minutes == 468 * 24 * 60 - 1
    assert "unknown_collection_provenance" in manifest.blockers
    with pytest.raises(DomainError, match="not replay eligible"):
        list(validated_bars(manifest))


def test_raw_float32_not_invented_exchange_precision() -> None:
    raw = RECORD.pack(START, 0.1, 0.1, 0.1, 0.1)
    bar = decode(raw)
    assert bar.open != D("0.1")
    assert bar.open == D.from_float(struct.unpack_from("<f", raw, 8)[0])
    assert abs(bar.open - D("0.1")) <= rounding_radius(bar, 0)
    assert bytes.fromhex(bar.raw_hex) == raw


def test_missing_provenance_and_leveraged_symbol_flag(make_replay: MakeReplay) -> None:
    source = make_replay(((100, 101, 99, 100),) * 2).allocations[0].source
    assert validate(source).replay_eligible
    assert not validate(replace(source, identity_evidence=None)).replay_eligible
    assert (
        "suspicious_leveraged_token_symbol"
        in validate(replace(source, symbol="BTC3L/QUOTE")).blockers
    )


def test_mutation_replacement_symlink_and_fifo_refused(make_replay: MakeReplay) -> None:
    source = make_replay(((100, 101, 99, 100),) * 3).allocations[0].source
    manifest = validate(source)
    reader = records(source)
    next(reader)
    source.path.write_bytes(source.path.read_bytes() + RECORD.pack(START, 1, 1, 1, 1))
    with pytest.raises(DomainError, match="changed"):
        list(reader)
    with pytest.raises(DomainError, match="changed"):
        list(validated_bars(manifest))
    alias = source.path.parent / "alias.bin"
    alias.symlink_to(source.path)
    with pytest.raises(OSError):
        list(records(replace(source, path=alias)))


@pytest.mark.parametrize(("offset", "count"), [(-1, 2), (0, 0), (0, 1000001)])
def test_reader_bounds(tmp_path: Path, offset: int, count: int) -> None:
    with pytest.raises(DomainError):
        Source(tmp_path / "none", "fake", "BASE/QUOTE", offset, count)
