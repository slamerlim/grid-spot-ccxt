import asyncio
from decimal import Decimal as D
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.domain.model import ActualFee, Mode
from grid.execution.models import LimitRequest, Notice, OrderFact
from grid.execution.synthetic import SyntheticExchange
from grid.operations.model import Action, AlertCode, Command
from grid.operations.supervisor import MockSupervisor
from grid.operations.synthetic import EXECUTION, OPERATIONS, STORAGE, fixture
from grid.persistence.records import Backpressure, Conflict
from grid.portfolio.model import CyclePhase


async def boot(path: Path, now: list[int]) -> MockSupervisor:
    case = fixture(1, 1)
    owner = await MockSupervisor.open(
        path,
        STORAGE,
        SOURCES,
        case.scopes,
        SyntheticExchange(1000),
        EXECUTION,
        OPERATIONS,
        lambda: (now[0], now[0] * 1000000),
        case.prepare,
    )
    await owner.observe(case.facts(now[0], 1))
    await owner.step()
    await owner.universe.configure("universe", 0, case.universe, "tester", "fixture", now[0])
    await owner.portfolio.begin("cycle", 0, "cycle", case.portfolio, "tester", "fixture")
    return owner


async def command(
    owner: MockSupervisor, action: Action, request: str, *, confirmed: bool = False
) -> None:
    health = await owner.status()
    await owner.control(
        request,
        health.operations.revision,
        Command(action, owner.scopes, "tester", "fixture", health.observed_ms, confirmed),
    )


@pytest.mark.usefixtures("async_sockets")
def test_supervised_lifecycle_has_explicit_resume_and_limit_exit(tmp_path: Path) -> None:
    async def scenario() -> None:
        now = [10000]
        case = fixture(1, 1)
        owner = await boot(tmp_path / "mock.db", now)
        try:
            health = await owner.status()
            assert health.admission_halted and not owner.executor.gateway.sent
            with pytest.raises(ValueError):
                await command(owner, Action.RESUME, "unconfirmed")
            await command(owner, Action.RESUME, "resume", confirmed=True)
            now[0] += 10
            await owner.observe(case.facts(now[0], 2))
            await owner.step()
            venue = owner.executor.gateway
            assert len(venue.sent) == 1
            venue.fill("order-1", "seed", D(1), D(100), (ActualFee("QUOTE", D(0), None),))
            now[0] += 10
            await owner.observe(case.facts(now[0], 3, D(200)))
            await owner.step()
            health = await owner.status()
            assert health.cycle is not None and health.cycle.phase is CyclePhase.REQUESTED
            now[0] += 10
            await owner.observe(case.facts(now[0], 4, D(200)))
            await owner.step()
            assert len(venue.sent) == 2 and venue.sent[-1].order_type == "limit"
            venue.fill("order-2", "exit", D(1), D(200), (ActualFee("QUOTE", D(0), None),))
            now[0] += 10
            await owner.observe(case.facts(now[0], 5, D(200)))
            health = await owner.step()
            assert health.cycle is not None and health.cycle.phase is CyclePhase.COMPLETE
            assert health.cycle.final_return == D("0.1") and health.allocations[0].base == 0
            await owner.backup(tmp_path / "backup.db", tmp_path / "verified.db")
            assert (await owner.status()).operations.backup is not None
        finally:
            await owner.close()
        assert (await owner.status()).closed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "action", [Action.PAUSE, Action.CANCEL_BUYS, Action.STOP, Action.RETIRE, Action.HALT]
)
@pytest.mark.usefixtures("async_sockets")
def test_controls_keep_distinct_semantics_and_replay_atomically(
    tmp_path: Path, action: Action
) -> None:
    async def scenario() -> None:
        now = [10000]
        owner = await boot(tmp_path / "mock.db", now)
        try:
            await command(owner, Action.RESUME, "resume", confirmed=True)
            health = await owner.status()
            cmd = Command(action, owner.scopes, "tester", "intent", now[0], False)
            first = await owner.control("action", health.operations.revision, cmd)
            assert await owner.control("action", health.operations.revision, cmd) == first
            with pytest.raises(Conflict):
                await owner.control("changed", health.operations.revision, cmd)
            status = await owner.status()
            row = status.allocations[0]
            assert row.working == (0 if action in (Action.CANCEL_BUYS, Action.RETIRE) else 1)
            assert row.quote_cash == 1000 and row.base == 0
            if action is Action.PAUSE:
                assert row.mode is Mode.PAUSED and not status.admission_halted
            if action is Action.STOP:
                assert status.operations.held == owner.scopes
            if action is Action.HALT:
                assert status.admission_halted and row.mode is Mode.ACTIVE
            now[0] += 10
            await owner.observe(fixture(1, 1).facts(now[0], 2))
            await owner.step()
            assert not owner.executor.gateway.sent
        finally:
            await owner.close()

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_halt_during_claimed_io_preserves_unknown_and_writer_responsiveness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        now = [10000]
        owner = await boot(tmp_path / "mock.db", now)
        entered, release = asyncio.Event(), asyncio.Event()
        original = owner.executor.gateway.submit_limit

        async def delayed(request: LimitRequest) -> OrderFact:
            entered.set()
            await release.wait()
            return await original(request)

        monkeypatch.setattr(owner.executor.gateway, "submit_limit", delayed)
        try:
            await command(owner, Action.RESUME, "resume", confirmed=True)
            now[0] += 10
            await owner.observe(fixture(1, 1).facts(now[0], 2))
            sending = asyncio.create_task(owner.step())
            await asyncio.wait_for(entered.wait(), 2)
            with pytest.raises(Backpressure):
                await owner.step()
            await asyncio.wait_for(command(owner, Action.HALT, "halt"), 2)
            health = await owner.status()
            assert health.admission_halted and health.allocations[0].unknown == 1
            release.set()
            await sending
            assert len(owner.executor.gateway.sent) == 1
            now[0] += 10
            await owner.step()
            assert len(owner.executor.gateway.sent) == 1
        finally:
            release.set()
            await owner.close()

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_overflow_is_visible_bounded_and_requires_explicit_resume(tmp_path: Path) -> None:
    async def scenario() -> None:
        owner = await boot(tmp_path / "mock.db", [10000])
        try:
            await command(owner, Action.RESUME, "resume", confirmed=True)
            for _ in range(EXECUTION.queue_size):
                await owner.offer(Notice(owner.scopes[0], owner.executor.epoch, "trade"))
            with pytest.raises(Backpressure):
                await owner.offer(Notice(owner.scopes[0], owner.executor.epoch, "trade"))
            health = await owner.status()
            assert health.admission_halted and health.intake_depth == EXECUTION.queue_size
            assert AlertCode.OVERLOAD in {a.code for a in health.alerts}
            for index in range(100):
                owner._alert(AlertCode.UNKNOWN if index % 2 else AlertCode.RECONCILE)
            health = await owner.status()
            assert len(health.alerts) <= OPERATIONS.alert_capacity and health.alerts_evicted > 0
        finally:
            await owner.close()

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_stale_evidence_and_removed_operational_guard_cannot_send(tmp_path: Path) -> None:
    async def scenario() -> None:
        now = [10000]
        owner = await boot(tmp_path / "mock.db", now)
        try:
            await command(owner, Action.RESUME, "resume", confirmed=True)
            owner.executor.operational_admission = None
            now[0] += 10
            await owner.observe(fixture(1, 1).facts(now[0], 2))
            await owner.step()
            assert not owner.executor.gateway.sent
            owner.executor.operational_admission = owner._admit
            now[0] += 1001
            health = await owner.step()
            assert not owner.executor.gateway.sent
            assert AlertCode.STALE in {a.code for a in health.alerts}
        finally:
            await owner.close()

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_resource_limit_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        owner = await boot(tmp_path / "mock.db", [10000])
        try:
            await command(owner, Action.RESUME, "resume", confirmed=True)
            monkeypatch.setattr(
                "grid.operations.supervisor.resident_bytes", lambda: OPERATIONS.max_rss_bytes + 1
            )
            health = await owner.step()
            assert health.admission_halted and not owner.executor.gateway.sent
            assert AlertCode.RESOURCE in {a.code for a in health.alerts}
        finally:
            await owner.close()

    asyncio.run(scenario())
