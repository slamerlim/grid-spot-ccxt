import asyncio
import json
import signal
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.domain.model import ActualFee, Mode, Side
from grid.domain.numbers import DomainError
from grid.execution.models import LimitRequest, OrderFact
from grid.execution.synthetic import SyntheticExchange
from grid.operations.model import Action, Command
from grid.operations.supervisor import MockSupervisor
from grid.operations.synthetic import EXECUTION, OPERATIONS, STORAGE, fixture
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.persistence.records import Conflict, LedgerError
from grid.persistence.storage import restore
from grid.portfolio.model import CyclePhase, ExitApproval

from .test_supervisor import boot, command

FEES = (ActualFee("QUOTE", D(0), None),)


async def refresh(owner: MockSupervisor, now: list[int], bid: D = D(90), ms: int = 10) -> None:
    now[0] += ms
    revision = owner.facts.revision + 1 if owner.facts else 1
    await owner.observe(fixture(1, 1).facts(now[0], revision, bid))


async def intent(owner: MockSupervisor) -> tuple[int, Command, ExitApproval]:
    status = await owner.status()
    assert status.cycle is not None
    return (
        status.operations.revision,
        Command(
            Action.LIMIT_LIQUIDATE,
            owner.scopes,
            "tester",
            "reviewed loss exit",
            status.observed_ms,
            True,
        ),
        ExitApproval(status.cycle.id, codec.digest(status.cycle.policy)),
    )


async def funded_position(owner: MockSupervisor, now: list[int]) -> None:
    await command(owner, Action.RESUME, "resume", confirmed=True)
    await refresh(owner, now, D(100))
    await owner.step()
    assert len(owner.executor.gateway.sent) == 1
    owner.executor.gateway.fill("order-1", "seed", D(1), D(100), FEES)
    await refresh(owner, now)
    assert await owner.executor.service(owner.scopes[0], owner.contexts[owner.scopes[0]])
    assert (await owner.status()).allocations[0].base == 1


@pytest.mark.usefixtures("async_sockets")
def test_manual_loss_exit_partial_fill_cancel_race_and_backup(tmp_path: Path) -> None:
    async def run() -> None:
        now = [10000]
        owner = await boot(tmp_path / "mock.db", now)
        try:
            await funded_position(owner, now)
            revision, cmd, approval = await intent(owner)
            receipt = await owner.control("manual", revision, cmd, exit_approval=approval)
            status = await owner.status()
            assert status.cycle and status.cycle.phase is CyclePhase.REQUESTED
            assert status.cycle.exit_cause == "operator"
            assert status.cycle.target_reached_at_exit is False
            assert status.cycle.trigger and status.cycle.trigger.net == 990
            assert status.operations.command == cmd
            assert status.allocations[0].mode is Mode.PORTFOLIO_EXIT
            await refresh(owner, now)
            await owner.step()
            venue = owner.executor.gateway
            assert len(venue.sent) == 2
            assert venue.sent[-1].side is Side.SELL and D(venue.sent[-1].price) == 90
            venue.fill("order-2", "partial", D("0.4"), D(90), FEES)
            await refresh(owner, now)
            await owner.step()
            assert (await owner.status()).allocations[0].base == D("0.6")
            await refresh(owner, now, ms=1001)
            await owner.step()
            assert venue.cancels == ["order-2"]
            await refresh(owner, now)
            await owner.step()
            assert len(venue.sent) == 2  # An ACK with OPEN status cannot free the sell slot.
            venue.fill("order-2", "late", D("0.2"), D(90), FEES)
            venue.settle_cancel("order-2")
            await refresh(owner, now, ms=1001)
            await owner.step()
            assert len(venue.sent) == 3 and D(venue.sent[-1].amount) == D("0.4")
            assert all(r.order_type == "limit" for r in venue.sent)
            venue.fill("order-3", "remaining", D("0.4"), D(90), FEES)
            await refresh(owner, now)
            await owner.step()
            status = await owner.status()
            assert status.cycle and status.cycle.phase is CyclePhase.COMPLETE
            assert status.cycle.final_return == D("-0.01")
            assert status.cycle.exit_shortfall == 0 and status.allocations[0].base == 0
            assert status.allocations[0].quote_cash == 990
            assert await owner.control("manual", revision, cmd, exit_approval=approval) == receipt
            assert len(venue.sent) == 3
            backup = await owner.writer.submit(lambda ledger: ledger.backup(tmp_path / "backup.db"))
            restored = restore(
                backup, tmp_path / "restored.db", STORAGE, SOURCES, mock_storage=True
            )
            ledger = Ledger(Path(restored.path), STORAGE, SOURCES, mock_storage=True)
            try:
                assert ledger.portfolio() == status.cycle
                assert ledger.lookup("manual") == receipt
                ledger.storage.integrity()
                assert status.cycle.manual_exit is not None
                altered = replace(
                    status.cycle,
                    manual_exit=replace(status.cycle.manual_exit, policy_sha256="0" * 64),
                )
                ledger.db.execute(
                    "UPDATE portfolio_revisions SET payload=? WHERE revision=?",
                    (codec.dumps(altered), altered.revision),
                )
                with pytest.raises(LedgerError, match="operator approval"):
                    ledger.portfolio()
            finally:
                ledger.close()
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize(
    "failure", ["confirmation", "cycle", "policy", "revision", "stale", "missing"]
)
def test_refused_liquidation_never_mutates_portfolio_or_audit(tmp_path: Path, failure: str) -> None:
    async def run() -> None:
        owner = await boot(tmp_path / "mock.db", [10000])
        try:
            revision, cmd, approval = await intent(owner)
            before = await owner.status()
            with pytest.raises((DomainError, LedgerError)):
                if failure == "confirmation":
                    cmd = replace(cmd, confirmed=False)
                elif failure == "cycle":
                    approval = replace(approval, cycle_id="different")
                elif failure == "policy":
                    approval = replace(approval, policy_sha256="0" * 64)
                elif failure == "revision":
                    revision += 1
                elif failure == "stale":
                    assert owner.facts is not None
                    owner.facts = replace(owner.facts, observed_ms=0)
                await owner.control(
                    "refused",
                    revision,
                    cmd,
                    exit_approval=None if failure == "missing" else approval,
                )
            after = await owner.status()
            assert after.cycle == before.cycle and after.operations == before.operations
            assert after.allocations == before.allocations
            assert await owner.writer.submit(lambda ledger: ledger.lookup("refused")) is None
            assert not owner.executor.gateway.sent
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_manual_request_survives_nav_samples_but_never_clears_halt(tmp_path: Path) -> None:
    async def run() -> None:
        now = [10000]
        owner = await boot(tmp_path / "mock.db", now)
        try:
            revision, cmd, approval = await intent(owner)
            await refresh(owner, now)
            await owner.step()  # Portfolio revision changes; approved identity/policy does not.
            first = await owner.control("manual", revision, cmd, exit_approval=approval)
            status = await owner.status()
            assert status.admission_halted and status.cycle and status.cycle.manual_exit == approval
            owner.facts = None  # Receipt replay does not need a new valuation or rerun the exit.
            assert await owner.control("manual", revision, cmd, exit_approval=approval) == first
            assert (await owner.status()).admission_halted
            with pytest.raises(Conflict):
                await owner.control(
                    "manual",
                    revision,
                    cmd,
                    exit_approval=replace(approval, policy_sha256="0" * 64),
                )
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize("guard", ["floor", "net_proceeds", "deadline"])
def test_manual_exit_retains_pricing_and_time_guards(tmp_path: Path, guard: str) -> None:
    async def run() -> None:
        now = [10000]
        policy = fixture(1, 1).portfolio
        if guard == "floor":
            policy = replace(policy, exits=(replace(policy.exits[0], floor=D(1000)),))
        elif guard == "net_proceeds":
            policy = replace(policy, exits=(replace(policy.exits[0], minimum_net_quote=D(1000)),))
        owner = await boot(tmp_path / "mock.db", now, policy)
        try:
            await funded_position(owner, now)
            revision, cmd, approval = await intent(owner)
            await owner.control("manual", revision, cmd, exit_approval=approval)
            await refresh(owner, now, ms=10001 if guard == "deadline" else 10)
            await owner.step()
            status = await owner.status()
            assert status.cycle and status.cycle.phase is CyclePhase.BLOCKED
            assert status.cycle.final is None and status.cycle.exit_cause == "operator"
            assert len(owner.executor.gateway.sent) == 1
            assert status.allocations[0].base == 1 and status.allocations[0].quote_cash == 900
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_partial_roster_cannot_expand_into_global_liquidation(tmp_path: Path) -> None:
    async def run() -> None:
        case = fixture(2, 2)
        owner = await MockSupervisor.open(
            tmp_path / "mock.db",
            STORAGE,
            SOURCES,
            case.scopes,
            SyntheticExchange(100),
            EXECUTION,
            OPERATIONS,
            lambda: (10000, 10000000000),
            case.prepare,
        )
        try:
            await owner.observe(case.facts(10000, 1))
            await owner.step()
            await owner.portfolio.begin("begin", 0, "cycle", case.portfolio, "tester", "fixture")
            revision, cmd, approval = await intent(owner)
            with pytest.raises(LedgerError, match="full roster"):
                await owner.control(
                    "partial",
                    revision,
                    replace(cmd, scopes=case.scopes[:1]),
                    exit_approval=approval,
                )
            status = await owner.status()
            assert status.cycle and status.cycle.phase is CyclePhase.ACTIVE
            assert all(row.mode is Mode.ACTIVE for row in status.allocations)
            assert not owner.executor.gateway.sent
            await owner.control("all", revision, cmd, exit_approval=approval)
            status = await owner.status()
            assert status.cycle and {slot.scope for slot in status.cycle.slots} == set(case.scopes)
            assert all(
                row.mode is Mode.PORTFOLIO_EXIT and row.working == 0 for row in status.allocations
            )
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_unknown_manual_exit_survives_restart_without_resubmission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        now = [10000]
        path = tmp_path / "mock.db"
        owner = await boot(path, now)
        venue = owner.executor.gateway
        try:
            await funded_position(owner, now)
            revision, cmd, approval = await intent(owner)
            receipt = await owner.control("manual", revision, cmd, exit_approval=approval)
            original = venue.submit_limit

            async def lost_ack(request: LimitRequest) -> OrderFact:
                await original(request)
                raise TimeoutError("lost mock exit acknowledgement")

            monkeypatch.setattr(venue, "submit_limit", lost_ack)
            await refresh(owner, now)
            await owner.step()
            assert len(venue.sent) == 2 and (await owner.status()).allocations[0].unknown == 1
        finally:
            await owner.close()
        monkeypatch.setattr(venue, "submit_limit", original)
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
            await refresh(recovered, now)
            await recovered.step()
            status = await recovered.status()
            assert status.admission_halted and status.allocations[0].unknown == 0
            assert status.cycle and status.cycle.manual_exit == approval
            assert len(venue.sent) == 2
            assert (
                await recovered.control("manual", revision, cmd, exit_approval=approval) == receipt
            )
            assert (await recovered.status()).admission_halted
            assert len(venue.sent) == 2
        finally:
            await recovered.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "boundary", ["after_liquidation", "after_posting", "before_commit", "after_commit"]
)
@pytest.mark.parametrize("scoped", [False, True])
def test_sigkill_keeps_manual_latch_revocations_and_control_audit_atomic(
    tmp_path: Path, boundary: str, scoped: bool
) -> None:
    path = tmp_path / "crash.db"
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("crash_child.py"))],
        input=json.dumps(
            {
                "path": str(path),
                "sources": sorted(SOURCES),
                "action": "allocation-exit" if scoped else "limit-liquidate",
                "boundary": boundary,
            }
        ),
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    ledger = Ledger(path, STORAGE, SOURCES, mock_storage=True)
    try:
        cycle, operations = ledger.portfolio(), ledger.operations()
        state = ledger.snapshot(fixture(1, 1).scopes[0]).state
        assert cycle is not None and operations is not None and state is not None
        committed = boundary == "after_commit"
        assert (cycle.phase is CyclePhase.REQUESTED) == (committed and not scoped)
        assert (cycle.manual_exit is not None) == (committed and not scoped)
        assert bool(cycle.allocation_exits) == (committed and scoped)
        action = Action.LIQUIDATE_ALLOCATION if scoped else Action.LIMIT_LIQUIDATE
        assert (operations.command.action is action) == committed
        assert (ledger.lookup("liquidate") is not None) == committed
        assert (state.mode is Mode.PORTFOLIO_EXIT) == committed
        assert state.orders[0].terminal == committed
        assert state.quote_cash == 1000 and state.owned_base == 0
        assert operations.halted  # Liquidation never impersonates resume.
        ledger.storage.integrity()
    finally:
        ledger.close()


@pytest.mark.parametrize(
    "boundary", ["after_release", "after_posting", "before_commit", "after_commit"]
)
def test_sigkill_keeps_scoped_release_and_resume_audit_atomic(
    tmp_path: Path, boundary: str
) -> None:
    path = tmp_path / "release.db"
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("crash_child.py"))],
        input=json.dumps(
            {
                "path": str(path),
                "sources": sorted(SOURCES),
                "action": "resume-allocation",
                "boundary": boundary,
            }
        ),
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    ledger = Ledger(path, STORAGE, SOURCES, mock_storage=True)
    try:
        cycle, operations = ledger.portfolio(), ledger.operations()
        state = ledger.snapshot(fixture(1, 1).scopes[0]).state
        assert cycle and operations and state
        committed = boundary == "after_commit"
        assert cycle.phase is CyclePhase.ACTIVE
        assert bool(cycle.allocation_exits) is not committed
        assert (state.mode is Mode.ACTIVE) == committed
        assert (operations.command.action is Action.RESUME) == committed
        assert operations.halted is not committed
        assert (ledger.lookup("release") is not None) == committed
        assert ledger.lookup("scoped") is not None
        assert not state.working and state.owned_base == 0 and state.quote_cash == 1000
        ledger.storage.integrity()
    finally:
        ledger.close()
