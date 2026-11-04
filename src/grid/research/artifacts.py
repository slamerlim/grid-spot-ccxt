"""Canonical research JSON and exclusive, outside-repository publication."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path

from grid.domain.numbers import DomainError
from grid.persistence.codec import amount


def plain(value: object) -> object:
    if isinstance(value, Decimal):
        return amount(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    raise DomainError(f"unsupported research artifact type: {type(value).__name__}")


def canonical(value: object) -> bytes:
    return json.dumps(plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def output_directory(path: Path) -> Path:
    resolved = path.resolve()
    protected = [
        Path("/opt/datasets"),
        Path("/opt/module_1_multi"),
        Path("/opt/grid-runs/i8-public-hour"),
    ]
    protected.extend(p for p in Path(__file__).resolve().parents if (p / ".git").exists())
    if any(resolved == root or root in resolved.parents for root in protected):
        raise DomainError("research outputs must be outside Git and original data trees")
    if any((p / ".git").exists() for p in (resolved, *resolved.parents)):
        raise DomainError("research outputs must be outside Git")
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    return resolved


def publish(path: Path, value: object) -> None:
    """No overwrite, symlink following, or incomplete result labeled complete."""
    parent = output_directory(path.parent)
    payload = canonical(value) + b"\n"
    # An exclusive temporary sibling is linked only after a successful fsync.
    import tempfile

    descriptor, temporary = tempfile.mkstemp(prefix=".grid-research-", dir=parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, parent / path.name)
    finally:
        os.unlink(temporary)
