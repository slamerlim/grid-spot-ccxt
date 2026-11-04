"""Bounded operational controls and public, credential-free health contracts."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from grid.domain.market import Money
from grid.domain.model import Mode, Scope
from grid.domain.numbers import DomainError, bounded_int, identifier
from grid.portfolio.model import Cycle


class Action(StrEnum):
    START = "startup_halt"
    PAUSE = "pause_new_buys"
    CANCEL_BUYS = "cancel_buys"
    STOP = "stop_and_reconcile"
    RECONCILE = "reconcile"
    RETIRE = "retire"
    RESUME = "resume"
    HALT = "emergency_halt"
    LIMIT_LIQUIDATE = "limit_liquidate"
    LIQUIDATE_ALLOCATION = "liquidate_allocation"
    CONFIGURE_UNIVERSE = "configure_universe"
    BACKUP = "backup"
    RESTORE_VERIFY = "restore_verify"
    SHUTDOWN = "shutdown_halt"


class AlertCode(StrEnum):
    HALTED = "admission_halted"
    STALE = "stale_or_missing_evidence"
    UNKNOWN = "unknown_order"
    RECONCILE = "reconciliation_required"
    RETRIES = "recovery_budget_exhausted"
    EXIT = "exit_blocked"
    OVERLOAD = "queue_overload"
    STORAGE = "storage_failure"
    BACKUP = "backup_missing_or_stale"
    RESOURCE = "resource_limit"
    CLOCK = "clock_regression"
    RUNTIME = "supervisor_failure"


@dataclass(frozen=True)
class Command:
    action: Action
    scopes: tuple[Scope, ...]
    actor: str
    reason: str
    observed_ms: int
    confirmed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.action, Action) or type(self.confirmed) is not bool:
            raise DomainError("explicit operational action required")
        identifier(self.actor)
        identifier(self.reason)
        bounded_int(self.observed_ms, 0, 2**53 - 1)
        if not isinstance(self.scopes, tuple) or not 1 <= len(self.scopes) <= 1000:
            raise DomainError("bounded explicit control scopes required")
        if len(set(self.scopes)) != len(self.scopes):
            raise DomainError("duplicate control scope")
        if self.action is Action.RESUME and not self.confirmed:
            raise DomainError("resume requires explicit confirmation")
        if self.action is Action.CONFIGURE_UNIVERSE and not self.confirmed:
            raise DomainError("universe configuration requires explicit confirmation")
        if self.action in (Action.BACKUP, Action.RESTORE_VERIFY) and not self.confirmed:
            raise DomainError("storage operation requires explicit confirmation")
        if (
            self.action in (Action.LIMIT_LIQUIDATE, Action.LIQUIDATE_ALLOCATION)
            and not self.confirmed
        ):
            raise DomainError("limit liquidation requires explicit confirmation")


@dataclass(frozen=True)
class BackupStamp:
    observed_ms: int
    digest: str
    requests: int

    def __post_init__(self) -> None:
        bounded_int(self.observed_ms, 0, 2**53 - 1)
        bounded_int(self.requests, 0, 2**63 - 1)
        if len(self.digest) != 64 or any(c not in "0123456789abcdef" for c in self.digest):
            raise DomainError("verified backup digest required")


@dataclass(frozen=True)
class StoragePlan:
    revision: int
    command: Command
    source: str
    destination: str
    digest: str | None = None
    requests: int | None = None

    def __post_init__(self) -> None:
        bounded_int(self.revision, 1, 2**63 - 2)
        if self.command.action not in (Action.BACKUP, Action.RESTORE_VERIFY):
            raise DomainError("explicit storage action required")
        for value in (self.source, self.destination):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 4096
                or not value.isprintable()
                or not Path(value).is_absolute()
                or str(Path(value)) != value
                or ".." in Path(value).parts
            ):
                raise DomainError("canonical absolute artifact paths required")
        if self.source == self.destination:
            raise DomainError("separate backup and verification paths required")
        if self.command.action is Action.RESTORE_VERIFY:
            if self.digest is None or self.requests is None:
                raise DomainError("restore requires a reviewed hash and request watermark")
            BackupStamp(0, self.digest, self.requests)
        elif self.digest is not None or self.requests is not None:
            raise DomainError("backup computes its own hash and watermark")


@dataclass(frozen=True)
class Operations:
    revision: int
    scopes: tuple[Scope, ...]
    halted: bool
    held: tuple[Scope, ...]
    command: Command
    backup: BackupStamp | None

    def __post_init__(self) -> None:
        bounded_int(self.revision, 1, 2**63 - 1)
        if type(self.halted) is not bool or not 1 <= len(self.scopes) <= 1000:
            raise DomainError("invalid operational ownership")
        if len(set(self.scopes)) != len(self.scopes) or len(set(self.held)) != len(self.held):
            raise DomainError("duplicate operational ownership")
        if not set(self.held) <= set(self.scopes) or not set(self.command.scopes) <= set(
            self.scopes
        ):
            raise DomainError("operational scopes cannot be orphaned")


@dataclass(frozen=True)
class Limits:
    scopes_per_tick: int
    tick_ms: int
    recovery_attempts: int
    recovery_backoff_ms: int
    alert_capacity: int
    backup_age_ms: int
    max_rss_bytes: int
    max_wal_bytes: int
    max_step_ms: int
    shutdown_ms: int

    def __post_init__(self) -> None:
        for value, lower, upper in (
            (self.scopes_per_tick, 1, 1000),
            (self.tick_ms, 1, 60000),
            (self.recovery_attempts, 1, 100),
            (self.recovery_backoff_ms, 1, 60000),
            (self.alert_capacity, 1, 10000),
            (self.backup_age_ms, 1, 86400000),
            (self.max_rss_bytes, 1048576, 2**50),
            (self.max_wal_bytes, 4096, 2**40),
            (self.max_step_ms, 1, 300000),
            (self.shutdown_ms, 1, 60000),
        ):
            bounded_int(value, lower, upper)


@dataclass(frozen=True)
class Alert:
    sequence: int
    observed_ms: int
    code: AlertCode
    scope: Scope | None


@dataclass(frozen=True)
class AllocationStatus:
    scope: Scope
    mode: Mode
    version: int
    recovery: bool
    quote_cash: Decimal
    base: Decimal
    working: int
    unknown: int
    queued_cancels: int
    retained_fill_fees: tuple[Money, ...]


@dataclass(frozen=True)
class Health:
    observed_ms: int
    operations: Operations
    cycle: Cycle | None
    final_return: Decimal | None
    exit_shortfall: Decimal | None
    allocations: tuple[AllocationStatus, ...]
    alerts: tuple[Alert, ...]
    alerts_evicted: int
    admission_halted: bool
    writer_depth: int
    intake_depth: int
    rss_bytes: int
    wal_bytes: int
    cpu_ns: int
    measured_step_ns: int
    active_tasks: int
    closed: bool
