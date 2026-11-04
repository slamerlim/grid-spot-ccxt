"""Durable storage intent/result receipts, separate from filesystem side effects."""

from dataclasses import dataclass, replace
from pathlib import Path

from grid.domain.numbers import DomainError, identifier
from grid.operations.model import Action, BackupStamp, StoragePlan
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.persistence.records import Conflict, LedgerError, Outcome
from grid.persistence.storage import new_database, private_path


@dataclass(frozen=True)
class StorageJob:
    plan: StoragePlan
    prepared: Outcome
    result: Outcome | None

    @property
    def status(self) -> str:
        return (
            "incomplete"
            if self.result is None
            else "verified"
            if self.result.accepted
            else "failed"
        )


def result_id(request_id: str) -> str:
    return "storage-result-" + codec.digest(identifier(request_id))


def lookup(ledger: Ledger, request_id: str) -> StorageJob | None:
    prepared = ledger.lookup(request_id)
    if prepared is None:
        return None
    row = ledger.db.execute(
        "SELECT payload FROM events WHERE request_id=?", (request_id,)
    ).fetchone()
    if row is None:
        raise LedgerError("storage intent evidence missing")
    plan = codec.loads(row[0], StoragePlan, limit=ledger.bounds.snapshot_bytes)
    result = ledger.lookup(result_id(request_id))
    if result is not None:
        row = ledger.db.execute(
            "SELECT payload FROM events WHERE request_id=?", (result_id(request_id),)
        ).fetchone()
        proof = codec.loads(row[0], tuple, limit=ledger.bounds.snapshot_bytes) if row else ()
        if (
            len(proof) != 4
            or proof[:3] != ("storage_result", request_id, plan)
            or proof[3] is not None
            and not isinstance(proof[3], BackupStamp)
            or result.accepted != (proof[3] is not None)
        ):
            raise LedgerError("storage result does not match its intent")
        stamp = proof[3]
        expected = (stamp.digest, str(stamp.requests)) if stamp else ("verification_failed",)
        if result.notices != expected:
            raise LedgerError("storage result proof differs from its receipt")
    return StorageJob(plan, prepared, result)


def artifact_destination(path: Path) -> Path:
    target = new_database(path)
    protected = (
        Path("/opt/datasets"),
        Path("/opt/module_1_multi"),
        Path("/opt/grid-runs/i8-public-hour"),
    )
    if any(target.is_relative_to(root) for root in protected) or any(
        (parent / ".git").exists() for parent in target.parents
    ):
        raise DomainError("storage artifacts must be outside Git and preserved evidence")
    return target


def prepare(ledger: Ledger, request_id: str, plan: StoragePlan) -> bool:
    previous_job = lookup(ledger, request_id)
    if previous_job is not None:
        if previous_job.plan != plan:
            raise Conflict("storage request identity changed")
        return False  # Incomplete work needs review, never an automatic second copy.
    if ledger.lookup(result_id(request_id)) is not None:
        raise Conflict("storage result identity is already occupied")

    def action() -> Outcome:
        previous = ledger.operations()
        if previous is None or previous.revision != plan.revision:
            raise Conflict("stale storage control revision")
        if (
            plan.command.scopes != previous.scopes
            or set(codec.digest(s) for s in previous.scopes) != set(ledger._allocation_ids())
            or plan.command.observed_ms < previous.command.observed_ms
        ):
            raise Conflict("storage scope or clock differs from operational ownership")
        source = (
            artifact_destination(Path(plan.source))
            if plan.command.action is Action.BACKUP
            else private_path(Path(plan.source), new=False)
        )
        destination = artifact_destination(Path(plan.destination))
        active = ledger.storage.path
        protected = {
            active.with_name(active.name + suffix)
            for suffix in ("", "-wal", "-shm", ".owner", "-journal")
        }
        if source == destination or source in protected or destination in protected:
            raise LedgerError("storage artifacts cannot alias the active database")
        updated = replace(previous, revision=previous.revision + 1, command=plan.command)
        ledger.db.execute(
            "INSERT INTO operations_revisions VALUES (?,?,?)",
            (updated.revision, request_id, ledger._text(updated)),
        )
        return Outcome(None, updated.revision, True)

    ledger._request(request_id, plan, "storage_prepare", None, action)
    return True


def finish(ledger: Ledger, request_id: str, stamp: BackupStamp | None) -> Outcome:
    job = lookup(ledger, request_id)
    if job is None:
        raise LedgerError("storage result needs durable intent")

    def action() -> Outcome:
        previous = ledger.operations()
        if previous is None:
            raise LedgerError("storage owner missing")
        backup = previous.backup
        if stamp is not None and job.plan.command.action is Action.BACKUP:
            if backup is not None and stamp.observed_ms < backup.observed_ms:
                raise Conflict("backup stamp regressed")
            backup = stamp
        # Never revert a concurrent pause/halt or adopt a foreign backup as our own.
        updated = replace(previous, revision=previous.revision + 1, backup=backup)
        ledger.db.execute(
            "INSERT INTO operations_revisions VALUES (?,?,?)",
            (updated.revision, result_id(request_id), ledger._text(updated)),
        )
        return Outcome(
            None,
            updated.revision,
            stamp is not None,
            notices=(stamp.digest, str(stamp.requests))
            if stamp is not None
            else ("verification_failed",),
        )

    return ledger._request(
        result_id(request_id),
        ("storage_result", request_id, job.plan, stamp),
        "storage_result",
        None,
        action,
    )
