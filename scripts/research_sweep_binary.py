"""Bounded engine result frames. Financial strings are never converted to float."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import msgpack  # type: ignore[import-untyped]

ENGINE_MAGIC = b"GRIDMP1\n"
FRAME_HEADER = struct.Struct("<Q32s")
MAX_FRAME_BYTES = 16 * 1024**2


def engine_results(
    directory: Path, summary: dict[str, Any], pulse: Callable[[], None] | None = None
) -> Iterator[dict[str, Any]]:
    """Verify each frame, whole file and canonical report hash while streaming."""
    if "binary_configurations" not in summary:
        yield from summary["configurations"]
        return
    descriptor = summary["binary_configurations"]
    if (
        descriptor.get("format") != "framed-msgpack-v1"
        or descriptor.get("file") != "configurations.bin"
    ):
        raise ValueError("unsupported engine binary results")
    path = directory / descriptor["file"]
    if path.is_symlink() or path.stat().st_size != descriptor["bytes"]:
        raise ValueError("engine binary result identity/size mismatch")
    file_hash = hashlib.sha256()
    canonical = hashlib.sha256(b"[")
    import json

    with path.open("rb") as handle:
        magic = handle.read(len(ENGINE_MAGIC))
        if magic != ENGINE_MAGIC:
            raise ValueError("invalid engine binary result header")
        file_hash.update(magic)
        for index in range(summary["configuration_count"]):
            if pulse:
                pulse()
            header = handle.read(FRAME_HEADER.size)
            if len(header) != FRAME_HEADER.size:
                raise ValueError("truncated engine binary result frame")
            length, checksum = FRAME_HEADER.unpack(header)
            if not 0 < length <= MAX_FRAME_BYTES:
                raise ValueError("oversized engine binary result frame")
            payload = handle.read(length)
            if len(payload) != length or hashlib.sha256(payload).digest() != checksum:
                raise ValueError("engine binary result frame checksum mismatch")
            file_hash.update(header)
            file_hash.update(payload)
            try:
                row = msgpack.unpackb(payload, raw=False, strict_map_key=True)
            except (ValueError, msgpack.UnpackException) as error:
                raise ValueError("invalid engine MessagePack result") from error
            if not isinstance(row, dict):
                raise ValueError("engine configuration result is not a map")
            if index:
                canonical.update(b",")
            canonical.update(
                json.dumps(
                    row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
            )
            yield row
        if handle.read(1):
            raise ValueError("engine binary results have trailing frames/data")
    canonical.update(b"]")
    if file_hash.hexdigest() != descriptor["sha256"]:
        raise ValueError("engine binary results file checksum mismatch")
    if canonical.hexdigest() != summary["fingerprint"]["configurations_sha256"]:
        raise ValueError("engine binary results canonical checksum mismatch")
