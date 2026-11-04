import asyncio
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.execution.models import ExecutionError, LimitRequest, OrderFact, Page
from grid.operations.model import Action, AlertCode
from grid.operations.supervisor import MockSupervisor
from grid.operations.synthetic import EXECUTION, OPERATIONS, STORAGE, fixture
from grid.persistence.ledger import Ledger
from grid.persistence.records import Backpressure, Backup, Bounds, LedgerError
from grid.persistence.storage import restore

from .test_supervisor import boot, command


@pytest.mark.usefixtures("async_sockets")
def test_shutdown_inflight_ack_is_reconciled_once_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        now = [10000]
        path = tmp_path / "mock.db"
        owner = await boot(path, now)
        venue = owner.executor.gateway
        original = venue.submit_limit
        accepted = asyncio.Event()

        async def lost_ack(request: LimitRequest) -> OrderFact:
            fact = await original(request)
            accepted.set()
            await asyncio.Event().wait()
            return fact

        monkeypatch.setattr(venue, "submit_limit", lost_ack)
        await command(owner, Action.RESUME, "resume", confirmed=True)
        now[0] += 10
        await owner.observe(fixture(1, 1).facts(now[0], 2))
        sending = asyncio.create_task(owner.step())
        await asyncio.wait_for(accepted.wait(), 2)
        await owner.close()
        assert sending.done() and sending.cancelled()
        assert (await owner.status()).allocations[0].unknown == 1
        case = fixture(1, 1)
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
            await recovered.observe(case.facts(now[0], 3))
            health = await recovered.step()
            assert health.admission_halted and health.allocations[0].unknown == 0
            assert len(venue.sent) == 1 and health.allocations[0].working == 1
        finally:
            await recovered.close()

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_shutdown_timeout_retains_writer_lock_until_admitted_work_finishes(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "mock.db"
        owner = await boot(path, [10000])
        owner.limits = replace(OPERATIONS, shutdown_ms=10)
        entered, release = threading.Event(), threading.Event()

        def slow(ledger: Ledger) -> None:
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test release timed out")

        job = asyncio.create_task(owner.writer.submit(slow))
        assert await asyncio.to_thread(entered.wait, 2)
        try:
            with pytest.raises(ExecutionError, match="ownership retained"):
                await owner.close()
            assert owner._local_halt and not owner._closed
            with pytest.raises(LedgerError, match="owns this ledger"):
                Ledger(path, STORAGE, SOURCES, mock_storage=True)
        finally:
            release.set()
            await job
            owner.limits = OPERATIONS
            await owner.close()
        reopened = Ledger(path, STORAGE, SOURCES, mock_storage=True)
        try:
            control = reopened.operations()
            assert control is not None and control.halted
        finally:
            reopened.close()

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_cancel_run_during_sleep_closes_all_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        owner = await boot(tmp_path / "mock.db", [10000])
        owner.limits = replace(OPERATIONS, tick_ms=1000)
        finished = asyncio.Event()
        original = owner.step

        async def observed_step() -> object:
            result = await original()
            finished.set()
            return result

        monkeypatch.setattr(owner, "step", observed_step)
        running = asyncio.create_task(owner.run(5))
        await asyncio.wait_for(finished.wait(), 2)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert owner._closed and not owner._running
        assert (await owner.status()).closed
        await owner.close()

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_backup_is_single_flight_and_shutdown_joins_detached_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        owner = await boot(tmp_path / "mock.db", [10000])
        entered, release = threading.Event(), threading.Event()

        def delayed(
            backup: Backup,
            destination: Path,
            bounds: Bounds,
            sources: frozenset[str],
            *,
            mock_storage: bool,
        ) -> Backup:
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test release timed out")
            return restore(backup, destination, bounds, sources, mock_storage=mock_storage)

        monkeypatch.setattr("grid.operations.supervisor.restore", delayed)
        pending = asyncio.create_task(owner.backup(tmp_path / "backup.db", tmp_path / "verify.db"))
        assert await asyncio.to_thread(entered.wait, 2)
        try:
            with pytest.raises(Backpressure, match="backup already active"):
                await owner.backup(tmp_path / "extra.db", tmp_path / "extra-verify.db")
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            owner.limits = replace(OPERATIONS, shutdown_ms=10)
            with pytest.raises(ExecutionError, match="ownership retained"):
                await owner.close()
            assert owner._backup is not None and not owner._closed
        finally:
            release.set()
            owner.limits = OPERATIONS
            await owner.close()
        health = await owner.status()
        assert health.closed and health.operations.backup is not None
        assert not (tmp_path / "extra.db").exists()

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_clock_regression_and_stale_resume_keep_admission_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        now = [10000]
        owner = await boot(tmp_path / "mock.db", now)
        try:
            now[0] += 1001
            with pytest.raises(ValueError):
                await command(owner, Action.RESUME, "stale-resume", confirmed=True)
            now[0] = 9999
            with pytest.raises(ExecutionError, match="clock regressed"):
                await owner.status()
            assert owner._local_halt and AlertCode.CLOCK in {a.code for a in owner._alerts}
            assert not owner.executor.gateway.sent
        finally:
            now[0] = 12000
            await owner.close()

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_unavailable_scope_exhausts_budget_without_starving_other_scopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        from grid.execution.synthetic import SyntheticExchange

        case, now = fixture(7, 7), [10000]
        owner = await MockSupervisor.open(
            tmp_path / "mock.db",
            STORAGE,
            SOURCES,
            case.scopes,
            SyntheticExchange(1000),
            EXECUTION,
            replace(OPERATIONS, scopes_per_tick=1),
            lambda: (now[0], now[0] * 1000000),
            case.prepare,
        )
        original = owner.executor.gateway.page
        failures = 0

        async def partition(scope: object, epoch: int, cursor: str | None, size: int) -> Page:
            from grid.domain.model import Scope

            nonlocal failures
            assert isinstance(scope, Scope)
            if scope == case.scopes[0]:
                failures += 1
                raise ExecutionError("synthetic partition")
            return await original(scope, epoch, cursor, size)

        monkeypatch.setattr(owner.executor.gateway, "page", partition)
        try:
            for index in range(20):
                now[0] += 101
                await owner.observe(case.facts(now[0], index + 1))
                await owner.step()
            health = await owner.status()
            assert failures == OPERATIONS.recovery_attempts
            assert all(not a.recovery for a in health.allocations[1:])
            assert health.allocations[0].recovery and not owner.executor.gateway.sent
        finally:
            await owner.close()

    asyncio.run(scenario())
