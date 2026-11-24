"""Small atomic progress messages shared by the two offline research workers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def publish(folder: Path, stage: str, message: str, **details: Any) -> None:
    value = {
        "stage": stage,
        "message": message,
        "updated_at": datetime.now(UTC).isoformat(),
        **details,
    }
    temporary = folder / "progress.next.json"
    try:
        temporary.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
        temporary.replace(folder / "progress.json")
    except OSError:
        # Observability must not change a replay's accounting or completion status.
        pass


def engine_error(folder: Path, prefix: str) -> str:
    """Keep a bounded, useful engine explanation instead of hiding it."""
    path = folder / "stderr.txt"
    if not path.is_file():
        return prefix
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 2000))
        detail = stream.read(2000).decode("utf-8", errors="replace").strip()
    detail = " ".join(detail.split())[-500:]
    return f"{prefix}: {detail}" if detail else prefix
