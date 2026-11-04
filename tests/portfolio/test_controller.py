import asyncio
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest
from execution.helpers import ZERO_FEES

from grid.domain.model import Context, Control, Mode, Side, State
from grid.domain.numbers import DomainError
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds, Conflict, LedgerError
from grid.portfolio.model import CyclePhase, PortfolioPolicy

from .helpers import harness


@pytest.mark.usefixtures("async_sockets")
def test_threshold_latches_once_and_realized_exit_may_be_below_ten_percent(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, policy: PortfolioPolicy
) -> None:
    async def scenario() -> None:
        async with harness(tmp_path / "ledger.db", bounds, {state.scope: context}, policy) as h:
            await h.owner.begin("begin", 0, "cycle-1", policy, "tester", "explicit baseline")
            await h.seed(state.scope)
            await h.trigger()
            cycle = await h.status()
            assert cycle.phase is CyclePhase.REQUESTED and cycle.trigger is not None
            assert cycle.trigger.net == 1100 and cycle.baseline.net == 1000
            with pytest.raises(DomainError, match="cleared exit latch"):
                replace(cycle, phase=CyclePhase.ACTIVE)
            with pytest.raises(Conflict):
                await h.owner.evaluate("again", cycle.revision, "tester", "do not retrigger")
            h.tick(bid=D(150))
            assert await h.service()
            cycle = await h.advance("plan")
            assert cycle.phase is CyclePhase.EXITING
            assert await h.executor.dispatch(state.scope, h.contexts[state.scope]), (
                h.executor.last_error
            )
            request = h.venue.sent[-1]
            assert (
                request.side is Side.SELL
                and request.order_type == "limit"
                and D(request.price) == 150
            )
            h.venue.fill("order-2", "exit", D(1), D(150), ZERO_FEES)
            assert await h.service()
            cycle = await h.advance("finish")
            assert cycle.phase is CyclePhase.COMPLETE and cycle.final is not None
            assert (
                cycle.final.net == 1050 and cycle.trigger is not None and cycle.trigger.net == 1100
            )
            assert cycle.trigger_baseline == 1000 and cycle.final_return == D("0.05")
            assert cycle.exit_shortfall == 50
            assert not (await h.state(state.scope)).working
            assert not await h.executor.dispatch(state.scope, h.contexts[state.scope])
            with pytest.raises(Conflict):
                await h.owner.advance("finish-again", cycle.revision, "tester", "already complete")
            # A fresh cycle is an explicit action. No background restart exists.
            h.tick()
            assert await h.service()
            await h.observe()
            await h.owner.begin(
                "next",
                cycle.revision,
                "cycle-2",
                policy,
                "tester",
                "explicit next cycle",
                (state.scope,),
            )
            assert (await h.status()).phase is CyclePhase.ACTIVE
            assert (await h.state(state.scope)).mode is Mode.ACTIVE

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["stale", "invalidate", "removed", "future_context"])
@pytest.mark.usefixtures("async_sockets")
def test_queued_buy_needs_current_global_nav(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: PortfolioPolicy,
    failure: str,
) -> None:
    async def scenario() -> None:
        async with harness(tmp_path / "ledger.db", bounds, {state.scope: context}, policy) as h:
            await h.owner.begin("begin", 0, "cycle", policy, "tester", "baseline")
            if failure == "stale":
                h.tick(1001)
                assert await h.service()
            elif failure == "invalidate":
                await h.owner.invalidate()
            elif failure == "removed":
                h.executor.portfolio_admission = None
            else:
                h.contexts[state.scope] = replace(context, now_ms=10001)
            assert not await h.executor.dispatch(state.scope, h.contexts[state.scope])
            assert not h.venue.sent

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_partial_exit_cancel_ack_and_old_sell_fill_do_not_open_second_sell(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, policy: PortfolioPolicy
) -> None:
    async def scenario() -> None:
        async with harness(tmp_path / "ledger.db", bounds, {state.scope: context}, policy) as h:
            await h.owner.begin("begin", 0, "cycle", policy, "tester", "baseline")
            await h.seed(state.scope)
            await h.trigger()
            await h.advance("plan")
            assert await h.executor.dispatch(state.scope, h.contexts[state.scope])
            h.venue.fill("order-2", "half", D("0.5"), D(200), ZERO_FEES)
            assert await h.service()
            h.tick(101)
            assert await h.service()
            await h.advance("expire")
            assert await h.executor.dispatch(state.scope, h.contexts[state.scope])
            assert h.venue.cancels == ["order-2"]
            assert not await h.service()
            await h.advance("pending")
            assert len(h.venue.sent) == 2
            h.venue.fill("order-2", "late-half", D("0.5"), D(200), ZERO_FEES)
            h.tick(1001)
            assert await h.service()
            cycle = await h.advance("complete")
            assert cycle.phase is CyclePhase.COMPLETE and len(h.venue.sent) == 2
            assert (await h.state(state.scope)).owned_base == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["budget", "unreachable", "outage", "liability"])
@pytest.mark.usefixtures("async_sockets")
def test_exit_blockers_never_fabricate_completion_or_restart(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: PortfolioPolicy,
    failure: str,
) -> None:
    async def scenario() -> None:
        configured = (
            replace(policy, exits=(replace(policy.exits[0], floor=D(1000)),))
            if failure == "unreachable"
            else policy
        )
        async with harness(tmp_path / "ledger.db", bounds, {state.scope: context}, configured) as h:
            await h.owner.begin("begin", 0, "cycle", configured, "tester", "baseline")
            await h.seed(state.scope)
            await h.trigger()
            if failure == "budget":
                h.tick(10001)
                assert await h.service()
            await h.observe()
            if failure == "outage":
                await h.owner.observe(replace(await h.facts(), markets=()))
            elif failure == "liability":
                from grid.portfolio.model import Liability, PoolKey

                await h.owner.observe(
                    replace(
                        await h.facts(),
                        liabilities=(
                            Liability(
                                PoolKey(state.scope.venue, state.scope.account, "QUOTE"),
                                D(1),
                                "debt",
                            ),
                        ),
                    )
                )
            cycle = await h.status()
            await h.owner.advance("blocked", cycle.revision, "tester", "blocked fixture")
            cycle = await h.status()
            assert cycle.phase is not CyclePhase.COMPLETE and cycle.final is None
            with pytest.raises(Conflict):
                await h.owner.begin(
                    "bad-restart", cycle.revision, "new", configured, "tester", "unresolved"
                )
            if failure != "liability":
                assert not await h.executor.dispatch(state.scope, h.contexts[state.scope])
                assert len(h.venue.sent) == 1

            def override(ledger: Ledger) -> None:
                snapshot = ledger.snapshot(state.scope)
                assert snapshot.state is not None and snapshot.version is not None
                ledger.apply(
                    "override",
                    snapshot.version,
                    Control(state.scope, snapshot.state.control_revision + 1, Mode.ACTIVE),
                    h.contexts[state.scope],
                )

            with pytest.raises(LedgerError):
                await h.writer.submit(override)

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_revision_and_idempotency_guards(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, policy: PortfolioPolicy
) -> None:
    async def scenario() -> None:
        async with harness(tmp_path / "ledger.db", bounds, {state.scope: context}, policy) as h:
            first = await h.owner.begin("begin", 0, "cycle", policy, "tester", "baseline")
            assert await h.owner.begin("begin", 0, "cycle", policy, "tester", "baseline") == first
            with pytest.raises(Conflict):
                await h.owner.begin("begin", 0, "different", policy, "tester", "baseline")
            with pytest.raises(LedgerError):
                await h.owner.evaluate("fast", 1, "tester", "budget")
            h.tick()
            await h.observe()
            outcome = await h.owner.evaluate("sample", 1, "tester", "below target")
            assert await h.owner.evaluate("sample", 1, "tester", "below target") == outcome
            with pytest.raises(Conflict):
                await h.owner.evaluate("old", 1, "tester", "stale revision")
            with pytest.raises(DomainError):
                await h.owner.observe(replace(await h.facts(), observed_ms=h.now[0] + 1))

    asyncio.run(scenario())
