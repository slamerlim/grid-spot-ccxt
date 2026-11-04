"""Explicit bounds and immutable results for the ledger API."""

from dataclasses import dataclass

from grid.domain.model import State
from grid.domain.numbers import bounded_int


class LedgerError(RuntimeError):
    """A refused or failed operation; no caller may infer a successful commit."""


class Conflict(LedgerError):
    """A stale version or a reused request identity with different contents."""


class Backpressure(LedgerError):
    """Admission failed; retain the input and reconcile before retrying."""


class StorageFault(LedgerError):
    """Storage safety failed; halt placement until explicit reviewed recovery."""


@dataclass(frozen=True)
class Bounds:
    busy_ms: int
    queue_size: int
    snapshot_bytes: int
    read_rows: int
    wal_pages: int
    backup_pages: int
    backup_ms: int

    def __post_init__(self) -> None:
        for value, lower, upper in (
            (self.busy_ms, 1, 10000),
            (self.queue_size, 1, 10000),
            (self.snapshot_bytes, 1024, 16 * 1024 * 1024),
            (self.read_rows, 1, 100000),
            (self.wal_pages, 16, 100000),
            (self.backup_pages, 1, 1024),
            (self.backup_ms, 1, 60000),
        ):
            bounded_int(value, lower, upper)


@dataclass(frozen=True)
class Outcome:
    state: State | None
    version: int | None
    accepted: bool
    intent_ids: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Intent:
    id: str
    payload: str
    status: str


@dataclass(frozen=True)
class Checkpoint:
    busy: int
    log_pages: int
    checkpointed_pages: int


@dataclass(frozen=True)
class Backup:
    path: str
    semantic_hash: str
    request_count: int
