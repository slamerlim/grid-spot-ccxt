"""Storage controls retain intent across interruption and never replace a ledger."""

import asyncio
import hashlib
import json
import signal
import sqlite3
import subprocess
import sys
import threading
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.domain.model import ActualFee
from grid.domain.numbers import DomainError
from grid.execution.models import ExecutionError
from grid.operations.model import Action, Command, StoragePlan
from grid.operations.supervisor import MockSupervisor
from grid.operations.synthetic import STORAGE, fixture
from grid.persistence import storage_jobs
from grid.persistence.ledger import Ledger
from grid.persistence.records import Backpressure, Backup, Bounds, Conflict, LedgerError
from grid.persistence.storage import Fault, no_fault, restore

from .test_supervisor import boot, command


async def plan(owner: MockSupervisor, root: Path) -> StoragePlan:
    health = await owner.status()
    return StoragePlan(
        health.operations.revision,
        Command(Action.BACKUP, owner.scopes, "tester", "storage drill", health.observed_ms, True),
        str(root / "backup.db"),
        str(root / "verified.db"),
    )


@pytest.mark.usefixtures("async_sockets")
def test_backup_snapshot_restores_unresolved_order_and_replay_preserves_later_halt(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        now, case = [10000], fixture(1, 1)
        owner = await boot(tmp_path / "active.db", now)
        try:
            await command(owner, Action.RESUME, "resume", confirmed=True)
            now[0] += 10
            await owner.observe(case.facts(now[0], 2))
            await owner.step()
            before = await owner.writer.submit(lambda ledger: ledger.snapshot(case.scopes[0]))
            assert before.state and before.state.working
            prepared = await plan(owner, tmp_path)
            result = await owner.storage_job("storage", prepared)
            assert result.status == "verified" and result.result
            stamp = (await owner.status()).operations.backup
            assert stamp and result.result.notices == (stamp.digest, str(stamp.requests))
            hashes = {
                p: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (Path(prepared.source), Path(prepared.destination))
            }
            recovered = Ledger(Path(prepared.destination), STORAGE, SOURCES, mock_storage=True)
            try:
                restored = recovered.snapshot(case.scopes[0])
                assert restored.state
                assert restored.state.orders == before.state.orders
                assert restored.state.quote_cash == before.state.quote_cash == 1000
                assert restored.state.reconcile_reason == before.state.reconcile_reason
                assert restored.state.reconcile_reason is not None
                retained = storage_jobs.lookup(recovered, "storage")
                ops = recovered.operations()
                assert retained and retained.status == "incomplete"
                assert ops and ops.backup is None
            finally:
                recovered.close()
            assert (
                hashlib.sha256(Path(prepared.source).read_bytes()).hexdigest()
                == hashes[Path(prepared.source)]
            )
            await command(owner, Action.HALT, "halt")
            revision = (await owner.status()).operations.revision
            assert await owner.storage_job("storage", prepared) == result
            assert (await owner.status()).operations.revision == revision
            assert (await owner.status()).admission_halted
            with pytest.raises(Conflict):
                await owner.storage_job(
                    "storage", replace(prepared, destination=str(tmp_path / "changed.db"))
                )
            with pytest.raises(Conflict):
                await owner.storage_job(
                    "stale", replace(prepared, source=str(tmp_path / "stale.db"))
                )
            assert not (tmp_path / "changed.db").exists() and not (tmp_path / "stale.db").exists()
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_incomplete_job_never_recopies_and_receipt_collision_is_not_success(tmp_path: Path) -> None:
    async def run() -> None:
        owner = await boot(tmp_path / "active.db", [10000])
        try:
            await command(owner, Action.HALT, storage_jobs.result_id("collision"))
            p = await plan(owner, tmp_path)
            with pytest.raises(Conflict):
                await owner.storage_job("collision", p)
            assert not Path(p.source).exists()
            assert await owner.writer.submit(
                lambda ledger: storage_jobs.prepare(ledger, "incomplete", p)
            )
            replay = await owner.storage_job("incomplete", p)
            assert replay.status == "incomplete" and replay.result is None
            assert not Path(p.source).exists() and not Path(p.destination).exists()
            assert (await owner.status()).operations.backup is None
            with pytest.raises(LedgerError):
                await owner.control("bypass", (await owner.status()).operations.revision, p.command)
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_restore_verification_does_not_refresh_current_backup_and_detects_tampering(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        now = [10000]
        owner = await boot(tmp_path / "active.db", now)
        try:
            saved = await plan(owner, tmp_path)
            backup = await owner.storage_job("backup", saved)
            assert backup.result
            original = (await owner.status()).operations.backup
            now[0] += 10
            p = replace(
                await plan(owner, tmp_path),
                command=replace(saved.command, action=Action.RESTORE_VERIFY, observed_ms=now[0]),
                destination=str(tmp_path / "another.db"),
                digest=backup.result.notices[0],
                requests=int(backup.result.notices[1]),
            )
            checked = await owner.storage_job("restore", p)
            assert checked.status == "verified"
            assert (await owner.status()).operations.backup == original
            db = sqlite3.connect(saved.source, autocommit=True)
            try:
                db.execute("UPDATE pools SET amount='999999'")
            finally:
                db.close()
            revision = (await owner.status()).operations.revision
            with pytest.raises(ExecutionError):
                await owner.storage_job(
                    "corrupt",
                    replace(p, revision=revision, destination=str(tmp_path / "corrupt.db")),
                )
            failed = await owner.writer.submit(
                lambda ledger: storage_jobs.lookup(ledger, "corrupt")
            )
            assert failed and failed.status == "failed"
            assert not (tmp_path / "corrupt.db").exists()
            assert (await owner.status()).admission_halted
            assert (await owner.status()).operations.backup == original
            assert await owner.storage_job("corrupt", failed.plan) == failed
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize(
    "invalid", ["existing", "symlink", "hardlink", "public", "sidecar", "owner", "active", "git"]
)
def test_storage_destinations_refused_before_intent_or_copy(tmp_path: Path, invalid: str) -> None:
    async def run() -> None:
        owner = await boot(tmp_path / "active.db", [10000])
        try:
            p = await plan(owner, tmp_path)
            destination = Path(p.destination)
            sentinel = tmp_path / "sentinel"
            sentinel.write_text("preserve")
            sentinel.chmod(0o600)
            if invalid == "existing":
                destination.write_text("preserve")
            elif invalid == "symlink":
                destination.symlink_to(sentinel)
            elif invalid == "hardlink":
                destination.hardlink_to(sentinel)
            elif invalid == "public":
                directory = tmp_path / "public"
                directory.mkdir(mode=0o755)
                p = replace(p, destination=str(directory / "new.db"))
            elif invalid in ("sidecar", "owner"):
                destination.with_name(
                    destination.name + ("-wal" if invalid == "sidecar" else ".owner")
                ).write_text("preserve")
            elif invalid == "active":
                p = replace(p, destination=str(owner.path))
            else:
                (tmp_path / ".git").mkdir()
            with pytest.raises((LedgerError, DomainError)):
                await owner.storage_job("refused", p)
            assert await owner.writer.submit(lambda ledger: ledger.lookup("refused")) is None
            assert (await owner.status()).operations.revision == p.revision
            assert not Path(p.source).exists() and sentinel.read_text() == "preserve"
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_detached_backup_is_bounded_and_concurrent_halt_and_fill_are_not_reverted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        now, case = [10000], fixture(1, 1)
        owner = await boot(tmp_path / "active.db", now)
        entered, release = threading.Event(), threading.Event()
        original_restore = restore

        def blocked_restore(
            backup: Backup,
            destination: Path,
            bounds: Bounds,
            sources: frozenset[str],
            *,
            mock_storage: bool,
            fault: Fault = no_fault,
        ) -> Backup:
            entered.set()
            assert release.wait(5)
            return original_restore(
                backup, destination, bounds, sources, mock_storage=mock_storage, fault=fault
            )

        monkeypatch.setattr("grid.operations.supervisor.restore", blocked_restore)
        try:
            await command(owner, Action.RESUME, "resume", confirmed=True)
            now[0] += 10
            await owner.observe(case.facts(now[0], 2))
            await owner.step()
            assert len(owner.executor.gateway.sent) == 1
            p = await plan(owner, tmp_path)
            task = asyncio.create_task(owner.storage_job("detached", p))
            assert await asyncio.to_thread(entered.wait, 5)
            with pytest.raises(Backpressure):
                await owner.storage_job("duplicate", p)
            with pytest.raises(Backpressure):
                await owner.backup(tmp_path / "other.db", tmp_path / "other-verify.db")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            owner.executor.gateway.fill(
                "order-1", "during-backup", D(1), D(100), (ActualFee("QUOTE", D(0), None),)
            )
            now[0] += 10
            await owner.observe(case.facts(now[0], 3))
            await owner.executor.service(case.scopes[0], owner.contexts[case.scopes[0]])
            await command(owner, Action.HALT, "during-restore")
            release.set()
            assert owner._storage is not None
            result = await asyncio.wait_for(asyncio.shield(owner._storage), 5)
            assert result.status == "verified"
            health = await owner.status()
            assert health.admission_halted and health.operations.command.action is Action.HALT
            assert health.allocations[0].base == 1 and health.allocations[0].quote_cash == 900
            assert len(owner.executor.gateway.sent) == 1
            restored = Ledger(Path(p.destination), STORAGE, SOURCES, mock_storage=True)
            try:
                state = restored.snapshot(case.scopes[0]).state
                assert state and state.owned_base == 0 and state.quote_cash == 1000
            finally:
                restored.close()
            assert await owner.storage_job("detached", p) == result
        finally:
            release.set()
            await owner.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "boundary",
    [
        "storage_prepare:after_posting",
        "storage_prepare:before_commit",
        "storage_prepare:after_commit",
        "storage_job:after_prepare",
        "backup:page",
        "restore:page",
        "storage_job:after_verify",
        "storage_result:after_posting",
        "storage_result:before_commit",
        "storage_result:after_commit",
    ],
)
def test_sigkill_storage_boundaries_preserve_truth(tmp_path: Path, boundary: str) -> None:
    path = tmp_path / "active.db"
    killed = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("storage_crash_child.py"))],
        input=json.dumps({"path": str(path), "sources": sorted(SOURCES), "boundary": boundary}),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert killed.returncode == -signal.SIGKILL, killed.stderr
    ledger = Ledger(path, STORAGE, SOURCES, mock_storage=True)
    try:
        job = storage_jobs.lookup(ledger, "crash-storage")
        before_prepare = boundary in (
            "storage_prepare:after_posting",
            "storage_prepare:before_commit",
        )
        finished = boundary == "storage_result:after_commit"
        assert (job is None) == before_prepare
        if job:
            assert job.status == ("verified" if finished else "incomplete")
            assert not storage_jobs.prepare(ledger, "crash-storage", job.plan)
        ops = ledger.operations()
        assert ops and (ops.backup is not None) == finished
        state = ledger.snapshot(fixture(1, 1).scopes[0]).state
        assert state and state.quote_cash == 1000 and state.owned_base == 0
        ledger.storage.integrity()
    finally:
        ledger.close()


@pytest.mark.usefixtures("async_sockets")
def test_result_ack_loss_returns_committed_receipt_on_replay(tmp_path: Path) -> None:
    async def run() -> None:
        owner = await boot(tmp_path / "active.db", [10000])
        try:
            p = await plan(owner, tmp_path)

            def install(ledger: Ledger) -> None:
                def fail(stage: str) -> None:
                    if stage == "storage_result:after_commit":
                        raise OSError("lost result acknowledgement")

                ledger.storage.fault = fail

            await owner.writer.submit(install)
            with pytest.raises(ExecutionError):
                await owner.storage_job("lost-ack", p)
            await owner.writer.submit(lambda ledger: setattr(ledger.storage, "fault", no_fault))
            replay = await owner.storage_job("lost-ack", p)
            assert replay.status == "verified"
            assert (await owner.status()).admission_halted
        finally:
            await owner.close()

    asyncio.run(run())
