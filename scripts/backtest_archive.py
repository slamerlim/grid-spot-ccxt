"""Build a read-only, exploratory single-quote manifest from archive filenames.

This is separate from the frozen Module 1 selection. A full sequential scan
establishes every selected file's structure and digest before a replay begins;
the C++ replay independently verifies the same digest and complete structure.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from scripts.backtest_legacy_n import ArchiveCatalog
from scripts.module1_selection.audit import RECORD, row_errors
from scripts.module1_verify_sources import identity, utc_ms

MAX_SOURCES = 64  # The current C++ replay parser has the same bound.
MINUTE_MS = 60_000
CHUNK_ROWS = 65_536
SUSPECT_LEVERAGED = re.compile(r"(?:[235][LS]|BULL|BEAR|UP|DOWN)$", re.I)
SOURCE_STATUS = "UNVERIFIED SOURCE / ASSUMPTION-BASED"


def _stamp(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat()


def _scan(
    path: Path,
    start_ms: int,
    end_ms: int,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Validate the entire immutable OHLC stream with bounded memory."""
    before = identity(path)
    if before[2] < RECORD.size or before[2] % RECORD.size:
        raise ValueError(f"Archive source has incomplete 24-byte records: {path.name}")
    digest = hashlib.sha256()
    count = aligned = 0
    first: int | None = None
    last: int | None = None
    previous: int | None = None
    read_bytes = 0
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            != before
        ):
            raise ValueError(f"Archive source changed before scan: {path.name}")
        while raw := stream.read(RECORD.size * CHUNK_ROWS):
            read_bytes += len(raw)
            if len(raw) % RECORD.size or read_bytes > before[2]:
                raise ValueError(f"Archive source changed during scan: {path.name}")
            digest.update(raw)
            for record in RECORD.iter_unpack(raw):
                count += 1
                timestamp = record[0]
                errors = row_errors(record)
                if previous is not None and timestamp <= previous:
                    errors.append("duplicate_or_out_of_order")
                if errors:
                    raise ValueError(
                        f"Archive source {path.name} record {count} invalid: {errors[0]}"
                    )
                if first is None:
                    first = timestamp
                last = previous = timestamp
                if start_ms <= timestamp < end_ms:
                    aligned += 1
            if progress is not None:
                progress(count, before[2] // RECORD.size)
        info = os.fstat(stream.fileno())
        if (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        ) != before or read_bytes != before[2]:
            raise ValueError(f"Archive source changed during scan: {path.name}")
    if identity(path) != before:
        raise ValueError(f"Archive source changed after scan: {path.name}")
    if first is None or last is None or not first <= start_ms < end_ms <= last + MINUTE_MS:
        raise ValueError(f"Declared window exceeds archive coverage: {path.name}")
    if aligned == 0:
        raise ValueError(f"No observations in declared window: {path.name}")
    return {
        "bytes": read_bytes,
        "rows": count,
        "aligned_rows": aligned,
        "first_utc": _stamp(first),
        "last_utc": _stamp(last),
        "sha256": digest.hexdigest(),
        "file_identity_before_and_after": list(before),
        "structural_errors": 0,
    }


def build_manifest(
    catalog: ArchiveCatalog,
    market_ids: list[str],
    start_utc: str,
    end_utc: str,
    *,
    assert_spot: bool,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    """Prepare a validated current-engine roster, with explicitly assumed spot identity.

    The current C++ engine supports at most 64 sources and one common quote.
    A filename alone cannot prove that a historical instrument was spot.
    """
    if assert_spot is not True:
        raise ValueError("Confirm the unverified spot-market assumption")
    if (
        not isinstance(market_ids, list)
        or not 1 <= len(market_ids) <= MAX_SOURCES
        or any(not isinstance(key, str) or key not in catalog.rows for key in market_ids)
        or len(set(market_ids)) != len(market_ids)
    ):
        raise ValueError(f"Choose 1–{MAX_SOURCES} distinct archive markets")
    if not isinstance(start_utc, str) or not isinstance(end_utc, str):
        raise ValueError("UTC date boundaries are required")
    start_ms, end_ms = utc_ms(start_utc), utc_ms(end_utc)
    if start_ms % MINUTE_MS or end_ms % MINUTE_MS or start_ms >= end_ms:
        raise ValueError("Choose an increasing whole-minute UTC window")
    quotes = {catalog.rows[key]["market"].partition("/")[2] for key in market_ids}
    if quotes != {"USDT"}:
        raise ValueError("USDT-only policy: backtests require USDT-quoted spot markets")
    common_quote = next(iter(quotes))
    root = catalog.root.resolve(strict=True)
    selected = []
    for key in sorted(market_ids):
        entry = catalog.rows[key]
        venue, market, filename = (entry[name] for name in ("venue", "market", "file"))
        base, separator, quote = market.partition("/")
        if not separator or quote != common_quote:
            raise ValueError("Current engine requires one common quote currency per run")
        if SUSPECT_LEVERAGED.search(base):
            raise ValueError(f"Likely leveraged token is outside spot-only research: {key}")
        if (
            filename != f"{venue}_{quote}_{base}.bin"
            or key != f"{venue}:{market}"
            or not key.isprintable()
            or len(key.encode("utf-8")) > 128
        ):
            raise ValueError("Archive filename and market identity disagree")
        path = root / filename
        # Neither the catalog nor a request body may supply an arbitrary path.
        source = _scan(
            path,
            start_ms,
            end_ms,
            partial(progress, key) if progress is not None else None,
        )
        selected.append(
            {
                "venue": venue,
                "market": market,
                "file": filename,
                "source_path": str(path),
                "source_status": SOURCE_STATUS,
                **source,
            }
        )
    return {
        "schema": "module1-exploratory-archive-v1",
        "source_root": str(root),
        "source_status": SOURCE_STATUS,
        "selection_method": "explicit_archive_filenames",
        "spot_identity": "USER_ASSUMPTION_NOT_VERIFIED",
        "quote": common_quote,
        "selected": selected,
        "coverage": {
            "common_start_utc": _stamp(start_ms),
            "common_end_utc_exclusive": _stamp(end_ms),
        },
    }
