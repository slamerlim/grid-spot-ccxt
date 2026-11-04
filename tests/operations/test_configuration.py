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

from grid.domain.model import Mode, Scope, Side
from grid.domain.numbers import DomainError
from grid.operations.model import Action, Command
from grid.operations.supervisor import MockSupervisor
from grid.operations.synthetic import STORAGE, fixture
from grid.persistence import codec
from grid.persistence import operations as operations_records
from grid.persistence.ledger import Ledger
from grid.persistence.records import Conflict, LedgerError, Outcome
from grid.persistence.storage import restore
from grid.persistence.universe import current
from grid.portfolio.model import CyclePhase, ExitApproval
from grid.universe.controls import EditAction, Retirement, UniverseEdit
from grid.universe.model import MarketKey, SelectionMode

from .test_allocation_exit import boot_many, liquidate, seed, tick
from .test_liquidation import FEES
from .test_supervisor import command


async def edit(owner: MockSupervisor, change: UniverseEdit, request: str) -> Outcome:
    health = await owner.status()
    assert health.cycle
    return await owner.control(
        request,
        health.operations.revision,
        Command(
            Action.CONFIGURE_UNIVERSE,
            owner.scopes,
            "tester",
            "reviewed universe edit",
            health.observed_ms,
            True,
        ),
        universe_edit=change,
        exit_approval=ExitApproval(health.cycle.id, codec.digest(health.cycle.policy))
        if change.retirement is Retirement.LIMIT
        else None,
    )


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize("retirement", list(Retirement))
def test_denial_modes_preserve_inventory_and_other_venue(
    tmp_path: Path, retirement: Retirement
) -> None:
    async def run() -> None:
        now, case = [10000], fixture(2, 2)
        owner = await boot_many(tmp_path / "mock.db", now, case)
        try:
            await seed(owner, case, now)
            first, second = case.scopes
            assert await owner.executor.dispatch(first, owner.contexts[first])
            assert await owner.executor.service(first, owner.contexts[first])
            venue = owner.executor.gateway
            assert len(venue.sent) == 3 and venue.sent[-1].side is Side.SELL
            original = await owner.status()
            await edit(
                owner,
                UniverseEdit(
                    1, EditAction.DENY, keys=(MarketKey.of(first),), retirement=retirement
                ),
                "deny",
            )
            health = await owner.status()
            universe, scopes, _ = await owner.universe.status()
            assert universe and universe.revision == 2 and set(scopes) == set(case.scopes)
            assert not next(m for m in universe.members if m.scope == first).selected
            assert next(m for m in universe.members if m.scope == second).selected
            assert health.allocations[1].quote_cash == original.allocations[1].quote_cash
            assert health.allocations[1].base == 0 and health.allocations[1].mode is Mode.ACTIVE
            mode = {
                Retirement.MANAGE: Mode.RETIRING,
                Retirement.HOLD: Mode.HOLD,
                Retirement.LIMIT: Mode.PORTFOLIO_EXIT,
            }[retirement]
            assert health.allocations[0].mode is mode and health.allocations[0].base == 1
            assert health.cycle and health.cycle.phase is CyclePhase.ACTIVE
            assert bool(health.cycle.allocation_exits) == (retirement is Retirement.LIMIT)
            await tick(owner, case, now)
            if retirement is Retirement.MANAGE:
                assert "order-3" not in venue.cancels
                assert len(venue.sent) == 3
                return
            assert "order-3" in venue.cancels and len(venue.sent) == 3
            venue.fill("order-3", "late-sell", D("0.3"), D(110), FEES)
            venue.settle_cancel("order-3")
            await tick(owner, case, now, ms=1001)
            health = await owner.status()
            assert health.allocations[0].base == D("0.7")
            if retirement is Retirement.HOLD:
                assert len(venue.sent) == 3 and health.allocations[0].working == 0
                await edit(owner, UniverseEdit(2, EditAction.REFRESH), "refresh")
                assert (await owner.status()).allocations[0].mode is Mode.HOLD
            else:
                assert len(venue.sent) == 4 and D(venue.sent[-1].amount) == D("0.7")
                assert venue.sent[-1].order_type == "limit"
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_all_policy_actions_preserve_halt_and_do_not_override_exit(tmp_path: Path) -> None:
    async def run() -> None:
        now, case = [10000], fixture(2, 2)
        owner = await boot_many(tmp_path / "mock.db", now, case)
        try:
            a, b = (MarketKey.of(s) for s in case.scopes)
            await edit(owner, UniverseEdit(1, EditAction.MODE, mode=SelectionMode.MANUAL), "manual")
            universe, _, _ = await owner.universe.status()
            assert universe and not any(m.selected for m in universe.members)
            await tick(owner, case, now)
            await edit(owner, UniverseEdit(2, EditAction.ALLOW, keys=(a,)), "allow")
            universe, _, _ = await owner.universe.status()
            assert universe and universe.policy.allow == (a,)
            assert {MarketKey.of(m.scope) for m in universe.members if m.selected} == {a}
            await edit(owner, UniverseEdit(3, EditAction.MODE, mode=SelectionMode.HYBRID), "hybrid")
            await edit(owner, UniverseEdit(4, EditAction.PIN, keys=(b,)), "pin")
            await edit(owner, UniverseEdit(5, EditAction.CAPACITY, capacity=1), "capacity")
            universe, _, _ = await owner.universe.status()
            assert universe and {MarketKey.of(m.scope) for m in universe.members if m.selected} == {
                b
            }
            await edit(
                owner,
                UniverseEdit(6, EditAction.DENY, keys=(b,), retirement=Retirement.LIMIT),
                "deny-pin",
            )
            await edit(owner, UniverseEdit(7, EditAction.UNPIN, keys=(b,)), "unpin")
            await edit(
                owner,
                UniverseEdit(
                    8, EditAction.DISABLE, venues=(a.venue,), retirement=Retirement.MANAGE
                ),
                "disable",
            )
            await tick(owner, case, now)
            await edit(owner, UniverseEdit(9, EditAction.ENABLE, venues=(a.venue,)), "enable")
            await edit(owner, UniverseEdit(10, EditAction.ALLOW, keys=(b,)), "allow-exiting")
            health = await owner.status()
            universe, _, _ = await owner.universe.status()
            assert universe and universe.revision == 11 and b not in universe.policy.deny
            assert health.operations.revision == 11 and health.admission_halted
            assert health.allocations[1].mode is Mode.PORTFOLIO_EXIT
            assert not next(m for m in universe.members if m.scope == case.scopes[1]).selected
            assert not owner.executor.gateway.sent
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_disable_unknown_order_retains_supervision_and_never_resubmits(tmp_path: Path) -> None:
    async def run() -> None:
        now, case = [10000], fixture(1, 1)
        owner = await boot_many(tmp_path / "mock.db", now, case)
        try:
            await command(owner, Action.RESUME, "resume", confirmed=True)
            await tick(owner, case, now)
            assert (await owner.status()).allocations[0].unknown == 1
            await edit(
                owner,
                UniverseEdit(
                    1,
                    EditAction.DISABLE,
                    venues=(case.scopes[0].venue,),
                    retirement=Retirement.MANAGE,
                ),
                "disable",
            )
            status = await owner.status()
            assert (
                status.allocations[0].unknown == 1 and status.allocations[0].mode is Mode.RETIRING
            )
            venue = owner.executor.gateway
            venue.fill("order-1", "late-buy", D("0.4"), D(100), FEES)
            await tick(owner, case, now)
            assert "order-1" in venue.cancels
            venue.settle_cancel("order-1")
            await tick(owner, case, now, ms=1001)
            status = await owner.status()
            assert (
                status.allocations[0].base == D("0.4") and status.allocations[0].quote_cash == 960
            )
            assert sum(r.side is Side.BUY for r in venue.sent) == 1
            assert status.cycle and status.cycle.baseline.net == 1000
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_replay_and_backup_preserve_two_revisions_and_operator_halt(tmp_path: Path) -> None:
    async def run() -> None:
        now, case = [10000], fixture(1, 1)
        owner = await boot_many(tmp_path / "mock.db", now, case)
        try:
            status = await owner.status()
            change = UniverseEdit(
                1, EditAction.DENY, keys=(MarketKey.of(case.scopes[0]),), retirement=Retirement.HOLD
            )
            cmd = Command(
                Action.CONFIGURE_UNIVERSE, case.scopes, "tester", "retain hold", now[0], True
            )
            receipt = await owner.control(
                "edit", status.operations.revision, cmd, universe_edit=change
            )
            await command(owner, Action.HALT, "halt")
            assert (
                await owner.control("edit", status.operations.revision, cmd, universe_edit=change)
                == receipt
            )
            assert (await owner.status()).admission_halted
            with pytest.raises(Conflict):
                await owner.control(
                    "edit",
                    status.operations.revision,
                    cmd,
                    universe_edit=replace(change, retirement=Retirement.MANAGE),
                )
            backup = await owner.writer.submit(lambda ledger: ledger.backup(tmp_path / "backup.db"))
            recovered = restore(
                backup, tmp_path / "restored.db", STORAGE, SOURCES, mock_storage=True
            )
            ledger = Ledger(Path(recovered.path), STORAGE, SOURCES, mock_storage=True)
            try:
                universe = current(ledger)
                assert universe and universe.revision == 2 and universe.policy.deny == change.keys
                assert ledger.lookup("edit") == receipt
                state = ledger.snapshot(case.scopes[0]).state
                assert state and state.mode is Mode.HOLD
                ledger.storage.integrity()
            finally:
                ledger.close()
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_configuration_hold_at_send_claim_blocks_a_prepared_sell(tmp_path: Path) -> None:
    async def run() -> None:
        now, case = [10000], fixture(2, 2)
        owner = await boot_many(tmp_path / "mock.db", now, case)
        try:
            await seed(owner, case, now)
            health = await owner.status()
            change = UniverseEdit(
                1, EditAction.DENY, keys=(MarketKey.of(case.scopes[0]),), retirement=Retirement.HOLD
            )
            cmd = Command(
                Action.CONFIGURE_UNIVERSE, case.scopes, "tester", "race at claim", now[0], True
            )
            original = owner.executor.operational_admission
            assert original is not None

            def admit(ledger: Ledger, scope: Scope) -> int:
                operations_records.commit(
                    ledger,
                    "racing-edit",
                    health.operations.revision,
                    cmd,
                    case.scopes,
                    lambda: None,
                    universe_edit=change,
                    configure=lambda: owner.universe.edit(
                        ledger,
                        "racing-edit",
                        change,
                        cmd.actor,
                        cmd.reason,
                        now[0],
                        None,
                        owner.facts,
                    ),
                )
                return original(ledger, scope)

            owner.executor.operational_admission = admit
            assert not await owner.executor.dispatch(case.scopes[0], owner.contexts[case.scopes[0]])
            assert len(owner.executor.gateway.sent) == 2
            status = await owner.status()
            assert status.allocations[0].mode is Mode.HOLD and status.allocations[0].base == 1
            assert (
                await owner.writer.submit(lambda ledger: ledger.lookup("racing-edit")) is not None
            )
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_confirmed_enable_restarts_empty_owned_retirement_without_clearing_halt(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        now, case = [10000], fixture(1, 1)
        owner = await boot_many(tmp_path / "mock.db", now, case)
        try:
            await edit(
                owner,
                UniverseEdit(
                    1,
                    EditAction.DISABLE,
                    venues=(case.scopes[0].venue,),
                    retirement=Retirement.HOLD,
                ),
                "disable",
            )
            await tick(owner, case, now)
            before = await owner.writer.submit(lambda ledger: ledger.snapshot(case.scopes[0]))
            assert before.state and not before.state.working
            await edit(
                owner, UniverseEdit(2, EditAction.ENABLE, venues=(case.scopes[0].venue,)), "enable"
            )
            after = await owner.writer.submit(lambda ledger: ledger.snapshot(case.scopes[0]))
            assert after.state and after.state.round_id == before.state.round_id + 1
            assert after.state.mode is Mode.ACTIVE and after.state.seed_id is None
            assert (await owner.status()).admission_halted and not owner.executor.gateway.sent
            await command(owner, Action.RESUME, "resume", confirmed=True)
            await tick(owner, case, now)
            sent = owner.executor.gateway.sent
            assert len(sent) == 1 and sent[0].side is Side.BUY and D(sent[0].amount) == 1
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_global_exit_supersedes_universe_hold_and_policy_edit_cannot_undo_it(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        now, case = [10000], fixture(1, 1)
        owner = await boot_many(tmp_path / "mock.db", now, case)
        try:
            await seed(owner, case, now)
            key = MarketKey.of(case.scopes[0])
            await edit(
                owner,
                UniverseEdit(1, EditAction.DENY, keys=(key,), retirement=Retirement.HOLD),
                "hold",
            )
            await tick(owner, case, now)
            assert (await owner.status()).allocations[0].mode is Mode.HOLD
            await liquidate(owner, all_scopes=True)
            await edit(owner, UniverseEdit(2, EditAction.ALLOW, keys=(key,)), "allow-global")
            await tick(owner, case, now)
            status = await owner.status()
            assert status.cycle and status.cycle.phase is not CyclePhase.ACTIVE
            assert status.allocations[0].mode is Mode.PORTFOLIO_EXIT
            venue = owner.executor.gateway
            assert len(venue.sent) == 2 and venue.sent[-1].side is Side.SELL
            assert venue.sent[-1].order_type == "limit"
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "retirement,boundary",
    [
        (retirement, boundary)
        for retirement in (Retirement.HOLD, Retirement.LIMIT)
        for boundary in (
            "after_configuration_exit",
            "after_configuration",
            "after_posting",
            "before_commit",
            "after_commit",
        )
        if retirement is Retirement.LIMIT or boundary != "after_configuration_exit"
    ],
)
def test_sigkill_configuration_exit_and_audit_are_one_transaction(
    tmp_path: Path, retirement: Retirement, boundary: str
) -> None:
    path = tmp_path / "mock.db"
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("configuration_crash_child.py"))],
        input=json.dumps(
            {
                "path": str(path),
                "sources": sorted(SOURCES),
                "retirement": retirement,
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
        case = fixture(2, 2)
        committed = boundary == "after_commit"
        universe, cycle, operations = current(ledger), ledger.portfolio(), ledger.operations()
        assert universe and cycle and operations
        assert universe.revision == operations.revision == (2 if committed else 1)
        assert bool(universe.policy.deny) == committed
        assert bool(cycle.allocation_exits) == (committed and retirement is Retirement.LIMIT)
        assert (ledger.lookup("configure") is not None) == committed
        state = ledger.snapshot(case.scopes[0]).state
        other = ledger.snapshot(case.scopes[1]).state
        assert state and other
        assert (
            state.mode is (Mode.PORTFOLIO_EXIT if retirement is Retirement.LIMIT else Mode.HOLD)
            if committed
            else state.mode is Mode.ACTIVE
        )
        assert state.orders[0].terminal == committed
        assert (
            state.quote_cash == other.quote_cash == 1000
            and state.owned_base == other.owned_base == 0
        )
        assert other.mode is Mode.ACTIVE and not other.orders[0].terminal
        assert operations.halted and cycle.phase is CyclePhase.ACTIVE
        ledger.storage.integrity()
    finally:
        ledger.close()


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize(
    "failure",
    [
        "operational_revision",
        "universe_revision",
        "capacity",
        "venue",
        "scope",
        "confirmation",
        "cycle",
        "policy",
        "stale",
    ],
)
def test_config_refusal_cannot_partially_commit_policy_or_exit(
    tmp_path: Path, failure: str
) -> None:
    async def run() -> None:
        now, case = [10000], fixture(2, 2)
        owner = await boot_many(tmp_path / "mock.db", now, case)
        try:
            before = await owner.status()
            universe, _, _ = await owner.universe.status()
            assert before.cycle
            approval: ExitApproval | None = ExitApproval(
                before.cycle.id, codec.digest(before.cycle.policy)
            )
            change = UniverseEdit(
                1,
                EditAction.DENY,
                keys=(MarketKey.of(case.scopes[0]),),
                retirement=Retirement.LIMIT,
            )
            cmd = Command(
                Action.CONFIGURE_UNIVERSE, case.scopes, "tester", "refused edit", now[0], True
            )
            revision = before.operations.revision
            with pytest.raises((DomainError, LedgerError)):
                if failure == "operational_revision":
                    revision += 1
                elif failure == "universe_revision":
                    change = replace(change, revision=2)
                elif failure == "capacity":
                    change = UniverseEdit(1, EditAction.CAPACITY, capacity=3)
                    approval = None
                elif failure == "venue":
                    change = UniverseEdit(1, EditAction.ENABLE, venues=("unknown",))
                    approval = None
                elif failure == "scope":
                    change = replace(change, keys=(replace(change.keys[0], account="unknown"),))
                elif failure == "confirmation":
                    cmd = replace(cmd, confirmed=False)
                elif failure == "cycle":
                    assert approval is not None
                    approval = replace(approval, cycle_id="wrong")
                elif failure == "policy":
                    assert approval is not None
                    approval = replace(approval, policy_sha256="0" * 64)
                elif failure == "stale":
                    assert owner.facts
                    owner.facts = replace(owner.facts, observed_ms=0)
                await owner.control(
                    "refused", revision, cmd, universe_edit=change, exit_approval=approval
                )
            after = await owner.status()
            current, _, _ = await owner.universe.status()
            assert (
                current == universe
                and after.operations == before.operations
                and after.cycle == before.cycle
            )
            assert after.allocations == before.allocations
            assert await owner.writer.submit(lambda ledger: ledger.lookup("refused")) is None
        finally:
            await owner.close()

    asyncio.run(run())
