"""Bounded read-only <qffff validation; filenames never establish market identity."""

from __future__ import annotations

import hashlib
import math
import mmap
import os
import re
import stat
import struct
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from grid.domain.numbers import DomainError, arithmetic, bounded_int, decimal, identifier
from grid.research.resources import before_read

RECORD = struct.Struct("<qffff")
MINUTE = 60_000
MAX_RECORDS = 1_000_000
MAX_TIMESTAMP = 253402300740000  # Last representable complete UTC minute in year 9999.


@dataclass(frozen=True)
class Source:
    path: Path
    venue: str
    symbol: str
    offset: int
    count: int
    format_evidence: str | None = None
    identity_evidence: str | None = None
    collection_evidence: str | None = None
    unleveraged_spot: bool = False

    def __post_init__(self) -> None:
        identifier(self.venue)
        identifier(self.symbol)
        bounded_int(self.offset, 0, 2**53 - 1)
        bounded_int(self.count, 1, MAX_RECORDS)
        if len(self.symbol.split("/")) != 2 or any(not p for p in self.symbol.split("/")):
            raise DomainError("explicit BASE/QUOTE mapping required")
        for evidence in (self.format_evidence, self.identity_evidence, self.collection_evidence):
            if evidence is not None and (not evidence.strip() or len(evidence) > 4096):
                raise DomainError("bounded provenance references required")
        if type(self.unleveraged_spot) is not bool:
            raise DomainError("explicit spot identity required")


@dataclass(frozen=True)
class Bar:
    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    raw_hex: str


@dataclass(frozen=True)
class Rejection:
    record: int
    reason: str
    raw_hex: str


@dataclass(frozen=True)
class Manifest:
    source: Source
    schema: str
    source_bytes: int
    file_identity: tuple[int, int, int, int, int]
    selected_bytes: int
    window_sha256: str
    whole_file_sha256: str | None
    accepted: int
    rejected: int
    reasons: dict[str, int]
    examples: tuple[Rejection, ...]
    first_ms: int | None
    last_ms: int | None
    missing_minutes: int
    complete_file: bool
    blockers: tuple[str, ...]
    precision_note: str = (
        "Exact float32 binary prices/raw bytes retained; original decimal precision is unknown. "
        "Nearest-rounding cells are assumptions, not recovered source prices."
    )

    @property
    def replay_eligible(self) -> bool:
        return not self.blockers


def identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def records(source: Source, mode: Literal["sequential", "mmap"] = "sequential") -> Iterator[bytes]:
    """Map only the requested window; guard regular-file identity before and after."""
    if mode not in ("sequential", "mmap"):
        raise DomainError("unsupported reader")
    descriptor = os.open(source.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise DomainError("regular historical file required")
        start = source.offset * RECORD.size
        size = source.count * RECORD.size
        if start + size > before.st_size:
            raise DomainError("selected record window exceeds file")
        if mode == "mmap":
            aligned = start - start % mmap.ALLOCATIONGRANULARITY
            with mmap.mmap(
                stream.fileno(), size + start - aligned, access=mmap.ACCESS_READ, offset=aligned
            ) as mapping:
                for index in range(source.count):
                    before_read(RECORD.size)
                    position = start - aligned + index * RECORD.size
                    yield mapping[position : position + RECORD.size]
        else:
            stream.seek(start)
            for _ in range(source.count):
                before_read(RECORD.size)
                raw = stream.read(RECORD.size)
                if len(raw) != RECORD.size:
                    raise DomainError("source truncated during read")
                yield raw
        if identity(before) != identity(os.fstat(stream.fileno())) or identity(before) != identity(
            source.path.stat(follow_symlinks=False)
        ):
            raise DomainError("source changed during read; discard result")


def decode(raw: bytes) -> Bar:
    if len(raw) != RECORD.size:
        raise DomainError("malformed_record_length")
    timestamp, *prices = RECORD.unpack(raw)
    if timestamp <= 0 or timestamp > MAX_TIMESTAMP or timestamp % MINUTE:
        raise DomainError("invalid_utc_minute")
    if any(not math.isfinite(value) for value in prices):
        raise DomainError("nonfinite_price")
    if any(value <= 0 for value in prices):
        raise DomainError("nonpositive_price")
    opened, high, low, close = (
        decimal(Decimal.from_float(value), positive=True) for value in prices
    )
    if not low <= min(opened, close) <= max(opened, close) <= high:
        raise DomainError("impossible_ohlc")
    return Bar(timestamp, opened, high, low, close, raw.hex())


@arithmetic
def rounding_radius(bar: Bar, field: int) -> Decimal:
    """Conservative half-ULP assuming nearest float32 rounding at collection."""
    bits = struct.unpack_from("<I", bytes.fromhex(bar.raw_hex), 8 + field * 4)[0]
    center = Decimal.from_float(struct.unpack("<f", struct.pack("<I", bits))[0])
    before = Decimal.from_float(struct.unpack("<f", struct.pack("<I", bits - 1))[0])
    after = struct.unpack("<f", struct.pack("<I", bits + 1))[0]
    upper = Decimal.from_float(after) - center if math.isfinite(after) else center - before
    return max(center - before, upper) / Decimal(2)


def validate(source: Source, mode: Literal["sequential", "mmap"] = "sequential") -> Manifest:
    before = source.path.stat(follow_symlinks=False)
    hasher = hashlib.sha256()
    reasons: Counter[str] = Counter()
    examples: list[Rejection] = []
    accepted = missing = 0
    first: int | None = None
    last: int | None = None
    for index, raw in enumerate(records(source, mode)):
        hasher.update(raw)
        try:
            bar = decode(raw)
            if last is not None and bar.timestamp <= last:
                raise DomainError("duplicate_minute" if bar.timestamp == last else "out_of_order")
            if last is not None:
                missing += (bar.timestamp - last) // MINUTE - 1
            first = bar.timestamp if first is None else first
            last = bar.timestamp
            accepted += 1
        except DomainError as exc:
            reason = str(exc)
            reasons[reason] += 1
            if len(examples) < 32:
                examples.append(Rejection(source.offset + index, reason, raw.hex()))
    if identity(before) != identity(source.path.stat(follow_symlinks=False)):
        raise DomainError("source changed across validation")
    blockers: list[str] = []
    if before.st_size % RECORD.size:
        blockers.append("malformed_file_length")
    if reasons:
        blockers.append("rejected_records")
    if missing:
        blockers.append("missing_minutes")
    for name, evidence in (
        ("format", source.format_evidence),
        ("identity", source.identity_evidence),
        ("collection", source.collection_evidence),
    ):
        if evidence is None:
            blockers.append(f"unknown_{name}_provenance")
    if not source.unleveraged_spot:
        blockers.append("unverified_unleveraged_spot")
    if re.search(r"(?:[235][LS]|BULL|BEAR|UP|DOWN)$", source.symbol.split("/")[0]):
        blockers.append("suspicious_leveraged_token_symbol")
    complete = source.offset == 0 and source.count * RECORD.size == before.st_size
    checksum = hasher.hexdigest()
    return Manifest(
        source,
        "little-endian:<qffff:24:UTC-ms:float32-no-volume",
        before.st_size,
        identity(before),
        source.count * RECORD.size,
        checksum,
        checksum if complete else None,
        accepted,
        sum(reasons.values()),
        dict(sorted(reasons.items())),
        tuple(examples),
        first,
        last,
        missing,
        complete,
        tuple(blockers),
    )


def validated_bars(manifest: Manifest) -> Iterator[Bar]:
    """Second streaming pass; reject changed input instead of publishing a replay."""
    if not manifest.replay_eligible:
        raise DomainError(f"history is not replay eligible: {manifest.blockers}")
    if identity(manifest.source.path.stat(follow_symlinks=False)) != manifest.file_identity:
        raise DomainError("source changed since validation")
    hasher = hashlib.sha256()
    for raw in records(manifest.source):
        hasher.update(raw)
        yield decode(raw)
    if hasher.hexdigest() != manifest.window_sha256:
        raise DomainError("validated source checksum changed")
