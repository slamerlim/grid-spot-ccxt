import asyncio
import json
import signal
import sqlite3
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.domain.model import Mode
from grid.execution.models import ExecutionError
from grid.operations.model import Action, AlertCode, Command
from grid.operations.supervisor import MockSupervisor
from grid.operations.synthetic import EXECUTION, OPERATIONS, STORAGE, fixture
from grid.persistence import schema
from grid.persistence.ledger import Ledger
from grid.persistence.records import Backup
from grid.persistence.storage import restore, semantic_hash, verify_schema

from .test_supervisor import boot, command


@pytest.mark.usefixtures("async_sockets")
def test_database_full_halts_without_misattributing_or_repeating_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        now = [10000]
        owner = await boot(tmp_path / "mock.db", now)
        try:
            await command(owner, Action.RESUME, "resume", confirmed=True)

            def full(ledger: Ledger) -> None:
                pages = ledger.db.execute("PRAGMA page_count").fetchone()[0]
                ledger.db.execute(f"PRAGMA max_page_count={pages}")

            await owner.writer.submit(full)
            failed = False
            for index in range(100):
                health = await owner.status()
                try:
                    await owner.control(
                        f"fill-pages-{index}",
                        health.operations.revision,
                        Command(
                            Action.RECONCILE,
                            owner.scopes,
                            "tester",
                            "mock bounded page fill",
                            now[0],
                            False,
                        ),
                    )
                except sqlite3.OperationalError as exc:
                    assert exc.sqlite_errorcode == sqlite3.SQLITE_FULL
                    failed = True
                    break
            assert failed
            now[0] += 10
            await owner.observe(fixture(1, 1).facts(now[0], 2))
            with suppress(ExecutionError):
                await owner.step()
            assert owner._local_halt and not owner.executor.gateway.sent
            assert AlertCode.STORAGE in {a.code for a in owner._alerts}
            await owner.writer.submit(
                lambda ledger: ledger.db.execute("PRAGMA max_page_count=10000").fetchone()
            )
            health = await owner.status()
            assert health.admission_halted and health.allocations[0].quote_cash == 1000
        finally:
            await owner.close()

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_failed_backup_permission_is_not_reported_fresh(tmp_path: Path) -> None:
    async def scenario() -> None:
        owner = await boot(tmp_path / "mock.db", [10000])
        try:
            await command(owner, Action.RESUME, "resume", confirmed=True)
            public = tmp_path / "unsafe-backup"
            public.mkdir(mode=0o755)
            public.chmod(0o755)
            with pytest.raises(ExecutionError, match="backup or restore"):
                await owner.backup(public / "backup.db", tmp_path / "verify.db")
            health = await owner.status()
            assert health.admission_halted and health.operations.backup is None
            assert not (public / "backup.db").exists()
        finally:
            await owner.close()

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_bounded_recovery_exhaustion_and_exception_text_never_leaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from grid.execution.models import Page

    async def scenario() -> None:
        now = [10000]
        owner = await boot(tmp_path / "mock.db", now)
        calls = 0

        async def broken(scope: object, epoch: int, cursor: str | None, size: int) -> Page:
            nonlocal calls
            calls += 1
            raise ExecutionError("injected confidential diagnostic text")

        monkeypatch.setattr(owner.executor.gateway, "page", broken)
        try:
            await command(owner, Action.RESUME, "resume", confirmed=True)
            for index in range(10):
                now[0] += 1001
                await owner.observe(fixture(1, 1).facts(now[0], index + 2))
                await owner.step()
            health = await owner.status()
            assert calls == OPERATIONS.recovery_attempts
            assert AlertCode.RETRIES in {a.code for a in health.alerts}
            assert "confidential" not in repr(health) and not owner.executor.gateway.sent
            await command(owner, Action.RECONCILE, "reviewed-recovery")
            now[0] += 1001
            await owner.step()
            assert calls == OPERATIONS.recovery_attempts + 1
        finally:
            await owner.close()

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_resume_receipt_does_not_clear_later_local_halt(tmp_path: Path) -> None:
    async def scenario() -> None:
        owner = await boot(tmp_path / "mock.db", [10000])
        try:
            before = await owner.status()
            cmd = Command(Action.RESUME, owner.scopes, "tester", "resume", 10000, True)
            first = await owner.control("resume", before.operations.revision, cmd)
            owner._local_halt = True
            assert await owner.control("resume", before.operations.revision, cmd) == first
            assert (await owner.status()).admission_halted
        finally:
            await owner.close()

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_restart_reconciles_existing_synthetic_order_and_never_auto_resumes(tmp_path: Path) -> None:
    async def scenario() -> None:
        now = [10000]
        path = tmp_path / "mock.db"
        owner = await boot(path, now)
        case = fixture(1, 1)
        venue = owner.executor.gateway
        await command(owner, Action.RESUME, "resume", confirmed=True)
        now[0] += 10
        await owner.observe(case.facts(now[0], 2))
        await owner.step()
        assert len(venue.sent) == 1
        await owner.close()
        recovered = await MockSupervisor.open(
            path,
            STORAGE,
            SOURCES,
            case.scopes,
            venue,
            EXECUTION,
            OPERATIONS,
            lambda: (now[0], now[0] * 1000000),
        )
        try:
            health = await recovered.status()
            assert health.admission_halted and health.allocations[0].recovery
            await recovered.observe(case.facts(now[0], 3))
            await recovered.step()
            health = await recovered.status()
            assert health.admission_halted and not health.allocations[0].recovery
            assert len(venue.sent) == 1
        finally:
            await recovered.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["after_posting", "before_commit", "after_commit"])
def test_sigkill_operational_revision_and_buy_revocation_commit_together(
    tmp_path: Path, boundary: str
) -> None:
    path = tmp_path / "crash.db"
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("crash_child.py"))],
        input=json.dumps({"path": str(path), "sources": sorted(SOURCES), "boundary": boundary}),
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    ledger = Ledger(path, STORAGE, SOURCES, mock_storage=True)
    try:
        operations = ledger.operations()
        assert operations is not None
        committed = boundary == "after_commit"
        assert (operations.command.action is Action.CANCEL_BUYS) == committed
        assert (ledger.lookup("cancel") is not None) == committed
        state = ledger.snapshot(fixture(1, 1).scopes[0]).state
        assert state is not None and state.quote_cash == 1000
        assert (state.mode is Mode.PAUSED) == committed
        assert state.orders[0].terminal == committed
        ledger.storage.integrity()
    finally:
        ledger.close()


@pytest.mark.usefixtures("async_sockets")
def test_v4_to_v5_and_backup_restore_preserve_operational_halt(tmp_path: Path) -> None:
    async def scenario() -> None:
        owner = await boot(tmp_path / "mock.db", [10000])
        try:
            await command(owner, Action.HALT, "halt")
            before = await owner.status()
            backup = await owner.writer.submit(lambda ledger: ledger.backup(tmp_path / "backup.db"))
            restored = restore(backup, tmp_path / "restore.db", STORAGE, SOURCES, mock_storage=True)
            ledger = Ledger(Path(restored.path), STORAGE, SOURCES, mock_storage=True)
            assert ledger.operations() == before.operations
            ledger.close()
            raw = sqlite3.connect(backup.path, autocommit=True)
            try:
                raw.execute("DROP TABLE operations_revisions")
                raw.execute("DELETE FROM migrations WHERE version=5")
                raw.execute("PRAGMA user_version=4")
                verify_schema(raw, 4)
                digest = semantic_hash(raw)
                raw.execute("UPDATE metadata SET value=? WHERE key='backup_hash'", (digest,))
            finally:
                raw.close()
            migrated = restore(
                Backup(backup.path, digest, backup.request_count),
                tmp_path / "v5.db",
                STORAGE,
                SOURCES,
                mock_storage=True,
            )
            ledger = Ledger(Path(migrated.path), STORAGE, SOURCES, mock_storage=True)
            try:
                assert ledger.operations() is None
                assert ledger.portfolio() == before.cycle
                assert ledger.db.execute("PRAGMA user_version").fetchone() == (schema.VERSION,)
                ledger.storage.integrity()
            finally:
                ledger.close()
        finally:
            await owner.close()

    asyncio.run(scenario())
