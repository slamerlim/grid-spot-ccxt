"""Explicit bounds and immutable results for the ledger API."""

import base64
import re
from dataclasses import dataclass
from typing import Literal

from grid.domain.model import Scope, State
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
class ExecutionPosition:
    """An evidenced source sequence or a distinct unified trade timestamp."""

    kind: Literal["sequence", "timestamp"]
    value: int

    def __post_init__(self) -> None:
        if self.kind not in ("sequence", "timestamp"):
            raise LedgerError("invalid execution ordering kind")
        bounded_int(self.value, 1, 2**63 - 1)


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


@dataclass(frozen=True)
class ClientIdPolicy:
    """Declared mock text-ID convention; no venue/account acceptance is implied."""

    name: str
    returned_prefix: str

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or re.fullmatch(r"[a-z0-9_-]{1,64}", self.name) is None
            or type(self.returned_prefix) is not str
            or re.fullmatch(r"[A-Za-z0-9_-]{0,6}", self.returned_prefix) is None
        ):
            raise LedgerError("invalid client-ID policy")


@dataclass(frozen=True)
class ClientIdentity:
    scope: Scope
    order_id: int
    internal_id: str
    submitted_id: str
    returned_id: str
    policy: ClientIdPolicy

    def __post_init__(self) -> None:
        bounded_int(self.order_id, 1, 2**63 - 1)
        if (
            type(self.scope) is not Scope
            or type(self.policy) is not ClientIdPolicy
            or type(self.internal_id) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.internal_id) is None
            or type(self.submitted_id) is not str
            or re.fullmatch(r"[a-z2-7]{26}", self.submitted_id) is None
            or type(self.returned_id) is not str
            or self.returned_id != self.policy.returned_prefix + self.submitted_id
        ):
            raise LedgerError("invalid durable client identity")
        raw = base64.b32decode(self.submitted_id.upper() + "======")
        if (
            len(raw) != 16
            or base64.b32encode(raw).decode().rstrip("=").lower() != self.submitted_id
        ):
            raise LedgerError("noncanonical client identity")
