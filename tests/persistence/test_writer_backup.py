"""Async ownership/admission, cancellation and consistent separate restore."""

import asyncio
import socket
import sqlite3
import threading
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest
import pytest_socket

from grid.domain.market import Money
from grid.domain.model import Context, State
from grid.domain.numbers import DomainError
from grid.persistence.ledger import Ledger
from grid.persistence.records import Backpressure, Bounds, LedgerError
from grid.persistence.storage import no_fault, restore, semantic_hash
from grid.persistence.writer import AsyncWriter

from .conftest import SOURCES
from .test_ledger import current, version


@pytest.fixture
def unix_only() -> None:
    # asyncio's local wakeup uses socketpair. Internet sockets remain prohibited.
    # Reset the plugin's earlier wrapper before installing the narrower policy;
    # wrapping it twice would leave the inner wrapper blocking socketpair.
    pytest_socket.enable_socket()
    pytest_socket.disable_socket(allow_unix_socket=True)
    with (
        pytest.warns(UserWarning, match="socket.socket"),
        pytest.raises(pytest_socket.SocketBlockedError),
    ):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


@pytest.mark.usefixtures("unix_only")
def test_one_thread_serializes_concurrent_last_funds_race(
    tmp_path: Path, bounds: Bounds, state: State, context: Context
) -> None:
    async def run() -> None:
        writer = await AsyncWriter.open(tmp_path / "async.db", bounds, SOURCES, mock_storage=True)
        try:
            identity = await writer.submit(lambda ledger: threading.get_ident())
            assert identity != threading.get_ident()
            await writer.submit(
                lambda ledger: ledger.credit(
                    "fund", state.scope.venue, state.scope.account, Money("QUOTE", D(200))
                )
            )
            other = replace(state.scope, allocation="other", symbol="OTHER/QUOTE")
            other_context = replace(
                context, market=replace(context.market, symbol="OTHER/QUOTE", base="OTHER")
            )
            results = await asyncio.gather(
                writer.submit(
                    lambda ledger: ledger.start(
                        "first", state.scope, (Money("QUOTE", D(200)),), context
                    )
                ),
                writer.submit(
                    lambda ledger: ledger.start(
                        "second", other, (Money("QUOTE", D(200)),), other_context
                    )
                ),
                return_exceptions=True,
            )
            assert sum(isinstance(result, DomainError) for result in results) == 1
            assert (
                await writer.submit(
                    lambda ledger: ledger.pool(state.scope.venue, state.scope.account, "QUOTE")
                )
                == 0
            )
            assert await writer.submit(lambda ledger: threading.get_ident()) == identity
        finally:
            await writer.close()

    asyncio.run(run())


@pytest.mark.usefixtures("unix_only")
def test_queue_overflow_and_caller_cancel_never_drop_admitted_commit(
    tmp_path: Path, bounds: Bounds, state: State
) -> None:
    entered, release = threading.Event(), threading.Event()

    def pause(stage: str) -> None:
        if stage == "credit:after_commit":
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test barrier timeout")

    async def run() -> None:
        writer = await AsyncWriter.open(
            tmp_path / "queue.db",
            replace(bounds, queue_size=1),
            SOURCES,
            mock_storage=True,
            fault=pause,
        )
        try:
            committed = asyncio.create_task(
                writer.submit(
                    lambda ledger: ledger.credit(
                        "fund", state.scope.venue, state.scope.account, Money("QUOTE", D(200))
                    )
                )
            )
            assert await asyncio.to_thread(entered.wait, 5)
            queued = asyncio.create_task(writer.submit(lambda ledger: ledger.lookup("fund")))
            await asyncio.sleep(0)
            with pytest.raises(Backpressure):
                await writer.submit(lambda ledger: ledger.lookup("not-admitted"))
            committed.cancel()
            with pytest.raises(asyncio.CancelledError):
                await committed
            release.set()
            receipt = await queued
            assert receipt is not None and receipt.accepted
            replay = await writer.submit(
                lambda ledger: ledger.credit(
                    "fund", state.scope.venue, state.scope.account, Money("QUOTE", D(200))
                )
            )
            assert replay == receipt
            assert (
                await writer.submit(
                    lambda ledger: ledger.pool(state.scope.venue, state.scope.account, "QUOTE")
                )
                == 200
            )
        finally:
            release.set()
            await writer.close()
        with pytest.raises(LedgerError, match="closed"):
            await writer.submit(lambda ledger: ledger.lookup("fund"))

    asyncio.run(run())


def test_connection_rejects_cross_thread_use(ledger: Ledger) -> None:
    errors: list[Exception] = []

    def misuse() -> None:
        try:
            ledger.db.execute("SELECT 1")
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=misuse)
    worker.start()
    worker.join(timeout=5)
    assert len(errors) == 1 and isinstance(errors[0], sqlite3.ProgrammingError)


def test_online_backup_restore_preserves_unknown_and_checks_semantics(
    funded: Ledger, state: State, bounds: Bounds, tmp_path: Path
) -> None:
    intent = funded.intents(state.scope)[0]
    funded.attempt("attempt", state.scope, 1, intent.id)
    snapshot = funded.snapshot(state.scope)
    expected_hash = funded.storage.semantic_hash()
    backup = funded.backup(tmp_path / "backup.db")
    assert backup.semantic_hash == expected_hash
    with pytest.raises(LedgerError, match="exists"):
        funded.backup(tmp_path / "backup.db")
    destination = tmp_path / "restored"
    destination.mkdir(mode=0o700)
    restored_backup = restore(backup, destination / "ledger.db", bounds, SOURCES, mock_storage=True)
    raw = sqlite3.connect(restored_backup.path, autocommit=True)
    try:
        assert semantic_hash(raw) == expected_hash
        assert raw.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert raw.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        raw.close()
    recovered = Ledger(Path(restored_backup.path), bounds, SOURCES, mock_storage=True)
    try:
        assert current(recovered.snapshot(state.scope)) == current(snapshot)
        assert version(recovered.snapshot(state.scope)) == version(snapshot)
        assert any(intent.status == "uncertain" for intent in recovered.intents(state.scope))
        assert recovered.lookup("attempt") == funded.lookup("attempt")
        with pytest.raises(LedgerError, match="reconciliation"):
            recovered.attempt("again", state.scope, version(snapshot), intent.id)
    finally:
        recovered.close()
    # The restored ledger's recovery flag differs, but its original backup stays immutable.
    raw = sqlite3.connect(backup.path, autocommit=True)
    try:
        assert semantic_hash(raw) == expected_hash
        raw.execute("UPDATE pools SET amount='999'")
    finally:
        raw.close()
    with pytest.raises(LedgerError, match="semantic"):
        restore(backup, destination / "corrupt.db", bounds, SOURCES, mock_storage=True)
    assert not (destination / "corrupt.db").exists()


def test_backup_and_restore_faults_are_reported_without_overwriting(
    funded: Ledger, tmp_path: Path, bounds: Bounds
) -> None:
    def fail(stage: str) -> None:
        if stage in {"backup:page", "restore:page"}:
            raise OSError("injected backup I/O failure")

    funded.storage.fault = fail
    with pytest.raises(OSError):
        funded.backup(tmp_path / "failed.db")
    funded.storage.fault = no_fault
    good = funded.backup(tmp_path / "good.db")
    with pytest.raises(OSError):
        restore(good, tmp_path / "bad-restore.db", bounds, SOURCES, mock_storage=True, fault=fail)
    with pytest.raises(LedgerError, match="exists"):
        restore(good, tmp_path / "good.db", bounds, SOURCES, mock_storage=True)
    assert funded.storage.semantic_hash() == good.semantic_hash


def test_backup_deadline_is_bounded(
    ledger: Ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    times = iter([0.0, 10.0])
    monkeypatch.setattr("grid.persistence.storage.time.monotonic", lambda: next(times, 10.0))
    with pytest.raises(LedgerError, match="deadline"):
        ledger.backup(tmp_path / "deadline.db")


def test_restore_rejects_missing_schema_constraint_before_creating_destination(
    funded: Ledger,
    tmp_path: Path,
    bounds: Bounds,
) -> None:
    backup = funded.backup(tmp_path / "schema.db")
    raw = sqlite3.connect(backup.path, autocommit=True)
    try:
        raw.execute("DROP INDEX one_unresolved_sell")
    finally:
        raw.close()
    with pytest.raises(LedgerError, match="schema"):
        restore(backup, tmp_path / "refused.db", bounds, SOURCES, mock_storage=True)
    assert not (tmp_path / "refused.db").exists()
