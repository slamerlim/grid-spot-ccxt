"""Approved residuals stay owned, share caps and need fresh evidence on resume."""

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

from grid.domain.model import Mode
from grid.domain.numbers import DomainError
from grid.operations.model import Action, Command
from grid.operations.supervisor import MockSupervisor
from grid.operations.synthetic import STORAGE, Fixture, fixture
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.persistence.portfolio import check_dust, unavoidable_dust
from grid.persistence.records import LedgerError
from grid.persistence.storage import restore
from grid.portfolio.model import (
    AllocationExit,
    CyclePhase,
    DustRule,
    ExitApproval,
    ExitSlot,
    PoolKey,
    Residual,
)

from .test_allocation_exit import boot_many, liquidate, seed, tick
from .test_liquidation import FEES
from .test_supervisor import command


def configured(*, count: int = 1, limit: D = D("0.1")) -> Fixture:
    case = fixture(count, count)
    rules = tuple(
        DustRule(PoolKey(scope.venue, scope.account, context.market.base), D("0.001"), D(1))
        for scope, context in case.contexts
    )
    return replace(case, portfolio=replace(case.portfolio, dust=rules, dust_limit=limit))


async def residual_owner(
    path: Path, now: list[int], case: Fixture, *, finish: bool = True
) -> MockSupervisor:
    owner = await boot_many(path, now, case)
    try:
        await seed(owner, case, now)
        if len(case.scopes) > 1:
            owner.executor.gateway.fill("order-2", "second-seed", D(1), D(100), FEES)
            now[0] += 10
            assert owner.facts
            await owner.observe(case.facts(now[0], owner.facts.revision + 1))
            assert await owner.executor.service(case.scopes[1], owner.contexts[case.scopes[1]])
        status = await owner.status()
        assert status.cycle
        await owner.control(
            "dust-exit",
            status.operations.revision,
            Command(
                Action.LIQUIDATE_ALLOCATION,
                case.scopes,
                "tester",
                "dust exit",
                status.observed_ms,
                True,
            ),
            exit_approval=ExitApproval(status.cycle.id, codec.digest(case.portfolio)),
        )
        await tick(owner, case, now)
        venue = owner.executor.gateway
        ids = [
            key
            for key, order in venue.orders.items()
            if order.status == "open" and order.request.side == "sell"
        ]
        assert len(ids) == len(case.scopes)
        for order_id in ids:
            venue.fill(order_id, "almost-" + order_id, D("0.999"), D(100), FEES)
        await tick(owner, case, now, ms=1001)
        for order_id in ids:
            venue.settle_cancel(order_id)
        if finish:
            await tick(owner, case, now, ms=1001)
        return owner
    except BaseException:
        await owner.close()
        raise


@pytest.mark.usefixtures("async_sockets")
def test_approved_dust_completes_scoped_exit_and_confirmed_resume_keeps_cost(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        now, case = [10000], configured()
        owner = await residual_owner(tmp_path / "ledger.db", now, case)
        try:
            health = await owner.status()
            assert health.cycle and health.cycle.phase is CyclePhase.ACTIVE
            entry = health.cycle.allocation_exits[0]
            assert entry.phase is CyclePhase.COMPLETE and entry.residual
            assert entry.residual.amount == D("0.001") and entry.residual.value == D("0.1")
            before = await owner.writer.submit(lambda ledger: ledger.snapshot(case.scopes[0]))
            assert before.state and before.state.position_cost == D("0.1")
            assert before.state.quote_cash == D("999.9")
            backup = await owner.writer.submit(lambda ledger: ledger.backup(tmp_path / "backup.db"))
            copy = restore(backup, tmp_path / "restored.db", STORAGE, SOURCES, mock_storage=True)
            recovered = Ledger(Path(copy.path), STORAGE, SOURCES, mock_storage=True)
            try:
                cycle = recovered.portfolio()
                assert cycle and cycle.allocation_exits[0] == entry
            finally:
                recovered.close()
            await command(owner, Action.RESUME, "resume-dust", confirmed=True)
            after = await owner.writer.submit(lambda ledger: ledger.snapshot(case.scopes[0]))
            assert after.state and after.state.lots == before.state.lots
            assert after.state.position_cost == before.state.position_cost
            assert after.state.quote_cash == before.state.quote_cash
            assert after.state.round_id == before.state.round_id + 1
            assert after.state.mode is Mode.ACTIVE and after.state.anchor == D(100)
            receipt = await owner.writer.submit(lambda ledger: ledger.lookup("resume-dust"))
            assert receipt and receipt.notices[0] == "retained_dust"
            assert codec.loads(receipt.notices[1], tuple) == (entry.residual,)
            await tick(owner, case, now)
            health = await owner.status()
            assert health.cycle and health.cycle.allocation_exits[0].released
            assert health.allocations[0].base == D("0.001")
            assert len(owner.executor.gateway.sent) == 2  # No enlarged or duplicate seed.
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize("invalid", ["absent", "amount", "value", "total"])
def test_explicit_approval_and_each_dust_cap_are_required(tmp_path: Path, invalid: str) -> None:
    async def run() -> None:
        case = configured()
        rule = case.portfolio.dust[0]
        policy = replace(
            case.portfolio,
            dust=()
            if invalid == "absent"
            else (
                replace(
                    rule,
                    max_amount=D("0.0009") if invalid == "amount" else rule.max_amount,
                    max_value=D("0.09") if invalid == "value" else rule.max_value,
                ),
            ),
            dust_limit=D("0.09") if invalid == "total" else case.portfolio.dust_limit,
        )
        now = [10000]
        owner = await residual_owner(tmp_path / "ledger.db", now, replace(case, portfolio=policy))
        try:
            health = await owner.status()
            assert health.cycle and health.cycle.allocation_exits[0].phase is CyclePhase.BLOCKED
            assert health.cycle.allocation_exits[0].residual is None
            assert health.allocations[0].base == D("0.001")
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_scoped_dust_revalues_shared_portfolio_cap_and_blocks_stale_resume(tmp_path: Path) -> None:
    async def run() -> None:
        now, case = [10000], configured(count=2, limit=D("0.2"))
        owner = await residual_owner(tmp_path / "ledger.db", now, case)
        try:
            health = await owner.status()
            assert health.cycle and all(e.residual for e in health.cycle.allocation_exits)
            await owner.control(
                "resume-one",
                health.operations.revision,
                Command(
                    Action.RESUME,
                    case.scopes[:1],
                    "tester",
                    "keep counting resumed dust",
                    health.observed_ms,
                    True,
                ),
            )
            await tick(owner, case, now, bid=D(150))
            health = await owner.status()
            assert health.cycle and all(
                e.phase is CyclePhase.BLOCKED for e in health.cycle.allocation_exits
            )
            assert all(e.residual is None for e in health.cycle.allocation_exits)
            await tick(owner, case, now)
            health = await owner.status()
            assert health.cycle and all(e.residual for e in health.cycle.allocation_exits)
            now[0] += 1001
            with pytest.raises((DomainError, LedgerError)):
                await command(owner, Action.RESUME, "stale-dust", confirmed=True)
            assert await owner.writer.submit(lambda ledger: ledger.lookup("stale-dust")) is None
            await owner.step()  # Stale proof is cleared, including previously complete entries.
            health = await owner.status()
            assert health.cycle and all(
                e.phase is CyclePhase.BLOCKED for e in health.cycle.allocation_exits
            )
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize("resumed", [False, True])
def test_global_takeover_counts_scoped_dust_once(tmp_path: Path, resumed: bool) -> None:
    async def run() -> None:
        now, case = [10000], configured()
        owner = await residual_owner(tmp_path / "ledger.db", now, case)
        try:
            if resumed:
                await command(owner, Action.RESUME, "resume-before-global", confirmed=True)
            await liquidate(owner, all_scopes=True)
            await tick(owner, case, now)
            cycle = (await owner.status()).cycle
            assert cycle and cycle.phase is CyclePhase.COMPLETE and cycle.final
            assert cycle.final.net == D("999.9") and cycle.final_return == D("-0.0001")
            assert len(cycle.residuals) == 1 and cycle.residuals[0].amount == D("0.001")
            assert cycle.allocation_exits[0].residual == cycle.residuals[0]
            assert len(owner.executor.gateway.sent) == 2
        finally:
            await owner.close()

    asyncio.run(run())


def test_existing_scoped_exit_codec_bytes_are_unchanged() -> None:
    case = fixture(1, 1)
    entry = AllocationExit(
        ExitSlot(case.scopes[0], D(100), 0, None, None, "cancel and reconcile"),
        ExitApproval("cycle", codec.digest(case.portfolio)),
        CyclePhase.REQUESTED,
        10000,
        10000,
        "tester",
        "scoped exit",
    )
    # Captured from the unmodified 759342d implementation.
    # pragma: allowlist nextline secret
    assert codec.digest(entry) == "f6b72df80248da4ed79e7c095040d20aab17f311a7a966ae1edc92b0f53b1434"
    assert "residual" not in codec.dumps(entry)
    assert codec.loads(codec.dumps(entry), AllocationExit) == entry


def test_repeated_asset_dust_shares_amount_and_value_allowances() -> None:
    case = configured()
    key = case.portfolio.dust[0].key
    residual = Residual(key, D("0.0006"), D("0.04"))
    check_dust((residual,), case.portfolio)
    with pytest.raises(DomainError, match="approval"):
        check_dust((residual, residual), case.portfolio)


@pytest.mark.usefixtures("async_sockets")
def test_sellable_inventory_cannot_be_approved_as_dust_after_deadline(tmp_path: Path) -> None:
    async def run() -> None:
        now, case = [10000], configured(limit=D(1000))
        case = replace(
            case,
            portfolio=replace(
                case.portfolio,
                dust=(replace(case.portfolio.dust[0], max_amount=D(10), max_value=D(1000)),),
            ),
        )
        owner = await boot_many(tmp_path / "ledger.db", now, case)
        try:
            await seed(owner, case, now)
            await liquidate(owner)
            await tick(owner, case, now, ms=11000)
            health = await owner.status()
            assert health.cycle and health.cycle.allocation_exits[0].phase is CyclePhase.BLOCKED
            entry = health.cycle.allocation_exits[0]
            state = await owner.writer.submit(lambda ledger: ledger.snapshot(case.scopes[0]).state)
            assert state and state.owned_base == 1 and entry.residual is None
            assert not unavoidable_dust(
                state, owner.contexts[state.scope], health.cycle, entry.slot
            )
            assert len(owner.executor.gateway.sent) == 1
        finally:
            await owner.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "boundary",
    [
        "allocation_exit_advance:after_posting",
        "allocation_exit_advance:before_commit",
        "allocation_exit_advance:after_commit",
        "operations:after_release",
        "operations:after_posting",
        "operations:before_commit",
        "operations:after_commit",
    ],
)
def test_sigkill_dust_completion_and_release(tmp_path: Path, boundary: str) -> None:
    path = tmp_path / "ledger.db"
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("dust_crash_child.py"))],
        input=json.dumps({"path": str(path), "boundary": boundary}),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    ledger = Ledger(path, STORAGE, SOURCES, mock_storage=True)
    try:
        cycle = ledger.portfolio()
        state = ledger.snapshot(configured().scopes[0]).state
        assert cycle and state and state.owned_base == D("0.001")
        assert state.position_cost == D("0.1") and state.quote_cash == D("999.9")
        release = boundary.startswith("operations:")
        committed = boundary.endswith(":after_commit")
        if release and committed:
            assert cycle.allocation_exits[0].released and state.mode is Mode.ACTIVE
            receipt = ledger.lookup("release-dust")
            assert receipt and receipt.notices[0] == "retained_dust"
        else:
            entry = cycle.allocation_exits[0]
            assert (entry.residual is not None) == (release or committed)
            assert state.mode is Mode.PORTFOLIO_EXIT
        ledger.storage.integrity()
    finally:
        ledger.close()
