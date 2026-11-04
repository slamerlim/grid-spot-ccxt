import asyncio
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.domain.model import Control, Mode, Side
from grid.domain.numbers import DomainError
from grid.execution.models import LimitRequest, OrderFact
from grid.execution.synthetic import SyntheticExchange
from grid.operations.model import Action, Command
from grid.operations.supervisor import MockSupervisor
from grid.operations.synthetic import EXECUTION, OPERATIONS, STORAGE, Fixture, fixture
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.persistence.records import Conflict, LedgerError
from grid.persistence.storage import restore
from grid.portfolio.model import CyclePhase, ExitApproval
from grid.universe.model import MarketKey

from .test_liquidation import FEES
from .test_supervisor import command


async def boot_many(path: Path, now: list[int], case: Fixture) -> MockSupervisor:
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


async def tick(
    owner: MockSupervisor, case: Fixture, now: list[int], *, bid: D = D(100), ms: int = 10
) -> None:
    now[0] += ms
    assert owner.facts is not None
    await owner.observe(case.facts(now[0], owner.facts.revision + 1, bid))
    await owner.step()


async def liquidate(
    owner: MockSupervisor, *, all_scopes: bool = False
) -> tuple[int, Command, ExitApproval]:
    status = await owner.status()
    assert status.cycle is not None
    cmd = Command(
        Action.LIMIT_LIQUIDATE if all_scopes else Action.LIQUIDATE_ALLOCATION,
        owner.scopes if all_scopes else owner.scopes[:1],
        "tester",
        "scoped liquidation",
        status.observed_ms,
        True,
    )
    approval = ExitApproval(status.cycle.id, codec.digest(status.cycle.policy))
    await owner.control(
        "global" if all_scopes else "scoped",
        status.operations.revision,
        cmd,
        exit_approval=approval,
    )
    return status.operations.revision, cmd, approval


async def seed(owner: MockSupervisor, case: Fixture, now: list[int]) -> None:
    await command(owner, Action.RESUME, "resume", confirmed=True)
    now[0] += 10
    assert owner.facts
    await owner.observe(case.facts(now[0], owner.facts.revision + 1))
    # Reconcile each accepted submit before the next portfolio-wide BUY admission.
    for scope in case.scopes:
        assert await owner.executor.service(scope, owner.contexts[scope])
        await owner.executor.dispatch(scope, owner.contexts[scope])
        assert await owner.executor.service(scope, owner.contexts[scope])
    venue = owner.executor.gateway
    assert len(venue.sent) == len(case.scopes)
    index = next(i for i, r in enumerate(venue.sent, 1) if r.scope == case.scopes[0])
    venue.fill(f"order-{index}", "seed", D(1), D(100), FEES)
    now[0] += 10
    assert owner.facts is not None
    await owner.observe(case.facts(now[0], owner.facts.revision + 1))
    assert await owner.executor.service(case.scopes[0], owner.contexts[case.scopes[0]])


@pytest.mark.usefixtures("async_sockets")
def test_scoped_partial_exit_preserves_other_venue_and_requires_explicit_resume(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        now, case = [10000], fixture(2, 2)
        owner = await boot_many(tmp_path / "mock.db", now, case)
        try:
            await seed(owner, case, now)
            before = await owner.writer.submit(lambda ledger: ledger.snapshot(case.scopes[1]))
            revision, cmd, approval = await liquidate(owner)
            after = await owner.writer.submit(lambda ledger: ledger.snapshot(case.scopes[1]))
            assert before == after
            cycle = (await owner.status()).cycle
            assert cycle and cycle.phase is CyclePhase.ACTIVE and cycle.trigger is None
            assert cycle.allocation_exits[0].approval == approval
            assert cycle.baseline.net == 2000 and cycle.final is None
            await tick(owner, case, now)
            venue = owner.executor.gateway
            sell = venue.sent[-1]
            assert sell.scope == case.scopes[0] and sell.side is Side.SELL
            assert sell.order_type == "limit" and len(venue.sent) == 3
            venue.fill("order-3", "partial", D("0.4"), D(100), FEES)
            await tick(owner, case, now, ms=1001)
            assert venue.cancels == ["order-3"]
            await tick(owner, case, now)
            assert len(venue.sent) == 3
            venue.fill("order-3", "late", D("0.2"), D(100), FEES)
            venue.settle_cancel("order-3")
            await tick(owner, case, now, ms=1001)
            assert len(venue.sent) == 4 and D(venue.sent[-1].amount) == D("0.4")
            venue.fill("order-4", "rest", D("0.4"), D(100), FEES)
            await tick(owner, case, now)
            status = await owner.status()
            assert status.cycle and status.cycle.phase is CyclePhase.ACTIVE
            assert status.cycle.allocation_exits[0].phase is CyclePhase.COMPLETE
            assert status.allocations[0].base == 0 and status.allocations[0].quote_cash == 1000
            assert status.allocations[0].mode is Mode.PORTFOLIO_EXIT
            assert status.allocations[1].mode is Mode.ACTIVE
            assert venue.sent[1].scope == case.scopes[1] and "order-2" not in venue.cancels
            receipt = await owner.writer.submit(lambda ledger: ledger.lookup("scoped"))
            assert await owner.control("scoped", revision, cmd, exit_approval=approval) == receipt
            backup = await owner.writer.submit(lambda ledger: ledger.backup(tmp_path / "backup.db"))
            restored = restore(
                backup, tmp_path / "restored.db", STORAGE, SOURCES, mock_storage=True
            )
            ledger = Ledger(Path(restored.path), STORAGE, SOURCES, mock_storage=True)
            try:
                assert ledger.portfolio() == status.cycle and ledger.lookup("scoped") == receipt
                ledger.storage.integrity()
            finally:
                ledger.close()
            await owner.control(
                "resume-one",
                status.operations.revision,
                Command(
                    Action.RESUME,
                    case.scopes[:1],
                    "tester",
                    "restart empty allocation",
                    now[0],
                    True,
                ),
            )
            released = await owner.status()
            assert released.cycle and not released.cycle.allocation_exits
            assert released.allocations[0].mode is Mode.ACTIVE
            await tick(owner, case, now)
            assert venue.sent[-1].scope == case.scopes[0] and venue.sent[-1].side is Side.BUY
            assert len(venue.sent) == 5
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize("automatic", [False, True])
def test_global_handoff_retains_sell_anchor_budget_and_original_deadline(
    tmp_path: Path, automatic: bool
) -> None:
    async def run() -> None:
        now, case = [10000], fixture(2, 2)
        case = replace(
            case,
            portfolio=replace(
                case.portfolio,
                exit_duration_ms=2000,
                exits=tuple(replace(r, max_orders=1, ttl_ms=10000) for r in case.portfolio.exits),
            ),
        )
        owner = await boot_many(tmp_path / "mock.db", now, case)
        try:
            await seed(owner, case, now)
            await liquidate(owner)
            await tick(owner, case, now)
            local = (await owner.status()).cycle
            assert local and local.allocation_exits[0].slot.orders == 1
            slot = local.allocation_exits[0].slot
            if automatic:
                await tick(owner, case, now, bid=D(300), ms=500)
            else:
                await tick(owner, case, now, ms=500)
                await liquidate(owner, all_scopes=True)
            cycle = (await owner.status()).cycle
            assert cycle and cycle.phase is CyclePhase.REQUESTED
            assert cycle.exit_cause == ("nav_target" if automatic else "operator")
            handed = cycle.exit_slot(case.scopes[0])
            assert handed and replace(handed, reason=slot.reason) == slot
            assert cycle.deadline_ms(case.scopes[0]) == local.allocation_exits[0].started_ms + 2000
            venue = owner.executor.gateway
            assert "order-3" not in venue.cancels and len(venue.sent) == 3
            await tick(owner, case, now, bid=D(300) if automatic else D(100))
            assert "order-2" in venue.cancels  # Ordinary order on the other allocation.
            assert "order-3" not in venue.cancels  # Valid SELL survives the global barrier.
            await tick(owner, case, now, ms=1500)
            assert "order-3" in venue.cancels
            venue.settle_cancel("order-2")
            venue.settle_cancel("order-3")
            await tick(owner, case, now, ms=1001)
            cycle = (await owner.status()).cycle
            assert cycle and cycle.phase is CyclePhase.BLOCKED
            final_slot = cycle.exit_slot(case.scopes[0])
            assert final_slot and final_slot.orders == 1
            assert len(venue.sent) == 3  # Neither handoff nor terminal cancel resets the budget.
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_scoped_advancement_does_not_starve_nav_or_depend_on_unrelated_books(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        now, case = [10000], fixture(2, 2)
        case = replace(case, portfolio=replace(case.portfolio, evaluation_interval_ms=1000))
        owner = await boot_many(tmp_path / "mock.db", now, case)
        try:
            await seed(owner, case, now)
            await liquidate(owner)
            cycle = (await owner.status()).cycle
            assert cycle and cycle.last_evaluation_ms == 10000
            for _ in range(3):
                await tick(owner, case, now)
            cycle = (await owner.status()).cycle
            assert cycle and cycle.last_evaluation_ms == 10000
            assert cycle.observation_ms == now[0]
            # Global NAV cannot use the other venue's stale book; the selected exit can.
            now[0] += 1000
            assert owner.facts
            facts = case.facts(now[0], owner.facts.revision + 1)
            stale = replace(facts.markets[1], book=replace(facts.markets[1].book, source_ms=0))
            await owner.observe(replace(facts, markets=(facts.markets[0], stale)))
            await owner.step()
            assert "order-3" in owner.executor.gateway.cancels
            cycle = (await owner.status()).cycle
            assert cycle and cycle.last_evaluation_ms == 10000
            owner.executor.gateway.settle_cancel("order-3")
            await tick(owner, case, now, bid=D(300), ms=1001)
            cycle = (await owner.status()).cycle
            assert (
                cycle and cycle.phase is CyclePhase.REQUESTED and cycle.exit_cause == "nav_target"
            )
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_controls_and_universe_cannot_override_unresolved_scoped_exit(tmp_path: Path) -> None:
    async def run() -> None:
        now, case = [10000], fixture(2, 2)
        owner = await boot_many(tmp_path / "mock.db", now, case)
        try:
            await seed(owner, case, now)
            await liquidate(owner)
            await tick(owner, case, now)
            await tick(owner, case, now)
            for action in (Action.RESUME, Action.PAUSE, Action.RETIRE):
                await command(owner, action, "control-" + action, confirmed=True)
                assert (await owner.status()).allocations[0].mode is Mode.PORTFOLIO_EXIT

            def override(ledger: Ledger) -> None:
                result = ledger.snapshot(case.scopes[0])
                assert result.state and result.version
                ledger.apply(
                    "override",
                    result.version,
                    Control(case.scopes[0], result.state.control_revision + 1, Mode.ACTIVE),
                    owner.contexts[case.scopes[0]],
                )

            with pytest.raises(LedgerError, match="cannot be overridden"):
                await owner.writer.submit(override)
            await owner.universe.configure(
                "deny",
                1,
                replace(case.universe, deny=(MarketKey.of(case.scopes[0]),)),
                "tester",
                "deny scoped exit",
                now[0],
            )
            assert (await owner.status()).allocations[0].mode is Mode.PORTFOLIO_EXIT
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize(
    "failure",
    ["confirmation", "cycle", "policy", "revision", "stale_second", "unknown_second", "repeat"],
)
def test_selected_roster_preflight_is_atomic(tmp_path: Path, failure: str) -> None:
    async def run() -> None:
        now, case = [10000], fixture(2, 2)
        owner = await boot_many(tmp_path / "mock.db", now, case)
        try:
            if failure == "repeat":
                await liquidate(owner)
            elif failure == "unknown_second":
                await command(owner, Action.RESUME, "resume", confirmed=True)
                await tick(owner, case, now)
            before = await owner.status()
            assert before.cycle and owner.facts
            cmd = Command(
                Action.LIQUIDATE_ALLOCATION, case.scopes, "tester", "selected targets", now[0], True
            )
            approval = ExitApproval(before.cycle.id, codec.digest(before.cycle.policy))
            revision = before.operations.revision
            with pytest.raises((DomainError, LedgerError)):
                if failure == "confirmation":
                    cmd = replace(cmd, confirmed=False)
                elif failure == "cycle":
                    approval = replace(approval, cycle_id="other")
                elif failure == "policy":
                    approval = replace(approval, policy_sha256="0" * 64)
                elif failure == "revision":
                    revision += 1
                elif failure == "stale_second":
                    second = replace(owner.facts.markets[1], account_observed_ms=0)
                    owner.facts = replace(owner.facts, markets=(owner.facts.markets[0], second))
                await owner.control("refused", revision, cmd, exit_approval=approval)
            after = await owner.status()
            assert (before.cycle, before.operations, before.allocations) == (
                after.cycle,
                after.operations,
                after.allocations,
            )
            assert await owner.writer.submit(lambda ledger: ledger.lookup("refused")) is None
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_unknown_scoped_sell_recovers_without_resubmit_or_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        now, case = [10000], fixture(1, 1)
        path = tmp_path / "mock.db"
        owner = await boot_many(path, now, case)
        venue = owner.executor.gateway
        original = venue.submit_limit
        try:
            await seed(owner, case, now)
            revision, cmd, approval = await liquidate(owner)
            receipt = await owner.writer.submit(lambda ledger: ledger.lookup("scoped"))

            async def lost_ack(request: LimitRequest) -> OrderFact:
                await original(request)
                raise TimeoutError("lost synthetic response")

            monkeypatch.setattr(venue, "submit_limit", lost_ack)
            await tick(owner, case, now)
            assert len(venue.sent) == 2 and (await owner.status()).allocations[0].unknown == 1
        finally:
            await owner.close()
        monkeypatch.setattr(venue, "submit_limit", original)
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
            await recovered.observe(case.facts(now[0], 1))
            await tick(recovered, case, now)
            status = await recovered.status()
            assert status.admission_halted and status.allocations[0].unknown == 0
            assert status.cycle and status.cycle.allocation_exits[0].slot.orders == 1
            assert (
                await recovered.control("scoped", revision, cmd, exit_approval=approval) == receipt
            )
            assert (await recovered.status()).admission_halted
            await command(recovered, Action.RESUME, "resume-existing-exit", confirmed=True)
            await tick(recovered, case, now)
            assert len(venue.sent) == 2
            assert (await recovered.status()).allocations[0].mode is Mode.PORTFOLIO_EXIT
            with pytest.raises(Conflict):
                await recovered.control(
                    "scoped",
                    revision,
                    replace(cmd, action=Action.LIMIT_LIQUIDATE),
                    exit_approval=approval,
                )
        finally:
            await recovered.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_scoped_dust_remains_owned_and_blocks_automatic_reentry(tmp_path: Path) -> None:
    async def run() -> None:
        now, case = [10000], fixture(1, 1)
        owner = await boot_many(tmp_path / "mock.db", now, case)
        try:
            await seed(owner, case, now)
            await liquidate(owner)
            await tick(owner, case, now)
            venue = owner.executor.gateway
            venue.fill("order-2", "almost", D("0.999"), D(100), FEES)
            await tick(owner, case, now, ms=1001)
            venue.settle_cancel("order-2")
            await tick(owner, case, now, ms=1001)
            status = await owner.status()
            assert status.cycle and status.cycle.allocation_exits[0].phase is CyclePhase.BLOCKED
            assert status.allocations[0].base == D("0.001")
            assert status.cycle.allocation_exits[0].slot.reason == "unsellable residual"
            await command(owner, Action.RESUME, "resume-dust", confirmed=True)
            await tick(owner, case, now)
            assert len(venue.sent) == 2
            assert (await owner.status()).allocations[0].mode is Mode.PORTFOLIO_EXIT
        finally:
            await owner.close()

    asyncio.run(run())
