"""Single-owner, fsynced hash chain for public budgets and fake venue events.

This records observations and the synthetic external world, never ledger balances.
The existing SQLite writer remains the sole financial authority. A truncated or
altered record refuses restart; it is never silently repaired or discarded.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from grid.domain.numbers import DomainError
from grid.persistence.storage import private_path
from grid.research.artifacts import canonical

CAP_BYTES = 512 * 1024**2
RECORD_BYTES = 256 * 1024
MAX_RECORDS = 10000


class Journal:
    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve(strict=True)
        path = private_path(self.directory / "observations.jsonl", new=False)
        self.fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self.rows: list[dict[str, Any]] = []
        self.previous = "0" * 64
        self.closed = False
        self.poisoned = False
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.fstat(self.fd).st_size > CAP_BYTES:
                raise DomainError("paper journal exceeds byte cap")
            with os.fdopen(os.dup(self.fd), "rb") as stream:
                while raw := stream.readline(RECORD_BYTES + 1):
                    if len(raw) > RECORD_BYTES or not raw.endswith(b"\n"):
                        raise DomainError("incomplete or oversized paper journal record")
                    row = json.loads(raw)
                    if not isinstance(row, dict):
                        raise DomainError("invalid paper journal row")
                    checksum = row.pop("sha256")
                    if (
                        row.get("previous") != self.previous
                        or row.get("sequence") != len(self.rows) + 1
                        or hashlib.sha256(canonical(row)).hexdigest() != checksum
                        or len(self.rows) >= MAX_RECORDS
                    ):
                        raise DomainError("paper journal chain mismatch")
                    self.rows.append(row)
                    self.previous = checksum
            os.lseek(self.fd, 0, os.SEEK_END)
        except BaseException:
            os.close(self.fd)
            self.closed = True
            raise

    def append(self, kind: str, payload: object, ms: int, ns: int) -> dict[str, Any]:
        if self.closed or self.poisoned or len(self.rows) >= MAX_RECORDS:
            raise DomainError("paper journal closed or full")
        if self.rows and (ms < self.rows[-1]["ms"] or ns < self.rows[-1]["ns"]):
            raise DomainError("paper clock regressed")
        row = dict(
            sequence=len(self.rows) + 1,
            previous=self.previous,
            kind=kind,
            ms=ms,
            ns=ns,
            payload=payload,
        )
        checksum = hashlib.sha256(canonical(row)).hexdigest()
        raw = canonical(dict(row, sha256=checksum)) + b"\n"
        if len(raw) > RECORD_BYTES or self.bytes() + len(raw) > CAP_BYTES:
            raise DomainError("paper artifact capacity reached")
        # A partial write is fatal and leaves evidence; no subsequent request may run.
        self.poisoned = True
        if os.write(self.fd, raw) != len(raw):
            raise OSError("incomplete paper journal write")
        os.fsync(self.fd)
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        # Normalize to exactly the same representation used after restart.
        normalized: dict[str, Any] = json.loads(canonical(row))
        self.rows.append(normalized)
        self.previous = checksum
        self.poisoned = False
        return normalized

    def bytes(self) -> int:
        files = list(self.directory.iterdir())
        if len(files) > 100 or any(p.is_symlink() or not p.is_file() for p in files):
            raise DomainError("unexpected paper directory entry")
        return sum(p.stat().st_size for p in files)

    def kinds(self, kind: str) -> list[dict[str, Any]]:
        return [row for row in self.rows if row["kind"] == kind]

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            os.close(self.fd)


def clock() -> tuple[int, int]:
    return time.time_ns() // 1000000, time.monotonic_ns()
