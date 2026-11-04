import asyncio
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest
from execution.helpers import ZERO_FEES
from universe.conftest import policy as universe_policy_fixture

from grid.domain.model import ActualFee, Context, Mode, Side, State
from grid.execution.models import LimitRequest, OrderFact
from grid.persistence.records import Bounds
from grid.portfolio.model import CyclePhase, DustRule, PoolKey, PortfolioPolicy
from grid.universe.controller import UniverseController
from grid.universe.model import UniversePolicy

from .helpers import harness

universe_policy = universe_policy_fixture


@pytest.mark.usefixtures("async_sockets")
def test_expired_unsent_exit_is_revoked_and_consumes_its_order_budget(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: PortfolioPolicy,
) -> None:
    async def scenario() -> None:
        configured = replace(policy, exits=(replace(policy.exits[0], max_orders=1),))
        async with harness(tmp_path / "ledger.db", bounds, {state.scope: context}, configured) as h:
            await h.owner.begin("begin", 0, "cycle", configured, "tester", "one attempt budget")
            await h.seed(state.scope)
            await h.trigger()
            await h.advance("plan")
            h.tick(101)
            assert await h.service()
            await h.observe()
            assert not await h.executor.dispatch(state.scope, h.contexts[state.scope])
            await h.advance("revoke-expired")
            cycle = await h.advance("budget-exhausted")
            assert cycle.phase is CyclePhase.BLOCKED and cycle.slots[0].orders == 1
            assert "budget" in cycle.slots[0].reason
            assert len(h.venue.sent) == 1 and not (await h.state(state.scope)).working
            assert (await h.state(state.scope)).owned_base == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("approval", ["approved", "absent", "too_small"])
@pytest.mark.usefixtures("async_sockets")
def test_terminal_partial_exit_needs_explicit_bounded_dust(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: PortfolioPolicy,
    approval: str,
) -> None:
    async def scenario() -> None:
        key = PoolKey(state.scope.venue, state.scope.account, "BASE")
        configured = replace(
            policy,
            dust_limit=D(10),
            dust=()
            if approval == "absent"
            else (DustRule(key, D("0.04") if approval == "too_small" else D("0.05"), D(10)),),
        )
        async with harness(tmp_path / "ledger.db", bounds, {state.scope: context}, configured) as h:
            await h.owner.begin("begin", 0, "cycle", configured, "tester", "baseline")
            await h.seed(state.scope)
            await h.trigger()
            await h.advance("plan")
            assert await h.executor.dispatch(state.scope, h.contexts[state.scope])
            h.venue.fill("order-2", "partial", D("0.95"), D(200), ZERO_FEES)
            assert await h.service()
            h.tick(101)
            assert await h.service()
            await h.advance("cancel")
            assert await h.executor.dispatch(state.scope, h.contexts[state.scope])
            h.venue.settle_cancel("order-2")
            assert await h.service()
            cycle = await h.advance("residual")
            assert (cycle.phase is CyclePhase.COMPLETE) == (approval == "approved")
            assert len(h.venue.sent) == 2 and (await h.state(state.scope)).owned_base == D("0.05")
            if approval == "approved":
                assert cycle.final is not None and cycle.final.net == 1090
                assert cycle.residuals[0].amount == D("0.05") and cycle.residuals[0].value == 10
                assert cycle.final.marks[-1].residual == D("0.05")

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_global_cancel_barrier_accounts_for_late_buy_and_old_sell_on_other_venue(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: PortfolioPolicy,
) -> None:
    from .conftest import prices

    async def scenario() -> None:
        other = replace(state.scope, venue="other")
        configured = replace(policy, exits=(*policy.exits, replace(policy.exits[0], scope=other)))
        async with harness(
            tmp_path / "ledger.db", bounds, {state.scope: context, other: context}, configured
        ) as h:
            await h.owner.begin("begin", 0, "cycle", configured, "tester", "two venues")
            await h.seed(state.scope)
            await h.seed(other)
            assert await h.executor.dispatch(other, h.contexts[other])  # ordinary SELL
            assert await h.service()
            assert await h.executor.dispatch(other, h.contexts[other])  # lower BUY
            buy_price = D(h.venue.sent[-1].price)
            assert await h.service()
            h.tick()
            h.contexts[state.scope] = prices(context, D(300), h.now[0])
            assert await h.service()
            await h.observe()
            await h.owner.evaluate("trigger", 1, "tester", "aggregate target")
            cycle = await h.advance("barrier")
            assert cycle.phase is CyclePhase.BLOCKED
            assert not any(o.role == "portfolio_exit" for o in (await h.state(state.scope)).orders)
            assert await h.service(), h.executor.last_error
            assert await h.executor.dispatch(other, h.contexts[other]), h.executor.last_error
            h.venue.fill("order-3", "late-old-sell", D("0.25"), D(110), ZERO_FEES)
            h.venue.fill("order-4", "late-buy", D("0.5"), buy_price, ZERO_FEES)
            h.venue.settle_cancel(h.venue.cancels[-1])
            assert await h.service(), h.executor.last_error
            assert await h.executor.dispatch(other, h.contexts[other])
            assert set(h.venue.cancels) == {"order-3", "order-4"}
            h.venue.settle_cancel(h.venue.cancels[-1])
            assert await h.service(), h.executor.last_error
            cycle = await h.advance("after-terminal-proof")
            assert cycle.phase is CyclePhase.EXITING
            a, b = await h.state(state.scope), await h.state(other)
            assert a.owned_base == 1 and b.owned_base == D("1.25")
            assert b.quote_cash == D(900) + D("27.5") - D("0.5") * buy_price
            for current in (a, b):
                assert current.mode is Mode.PORTFOLIO_EXIT and len(current.working) == 1
                assert current.working[0].side is Side.SELL
                assert current.working[0].amount == current.owned_base
            assert len(h.venue.sent) == 4

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_actual_exit_rebate_posts_once_and_threshold_claim_freezes_before_send(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: PortfolioPolicy,
) -> None:
    async def scenario() -> None:
        async with harness(tmp_path / "ledger.db", bounds, {state.scope: context}, policy) as h:
            await h.owner.begin("begin", 0, "cycle", policy, "tester", "baseline")
            await h.seed(state.scope)
            # Ordinary TP precedes queued lower BUYs. Keep it until the latch cancels it.
            assert await h.executor.dispatch(state.scope, h.contexts[state.scope])
            assert await h.service()
            h.tick(bid=D(200))
            assert await h.service()
            await h.observe()
            assert not await h.executor.dispatch(state.scope, h.contexts[state.scope])
            assert (await h.status()).phase is CyclePhase.REQUESTED
            assert len(h.venue.sent) == 2
            assert await h.service(), h.executor.last_error
            assert await h.executor.dispatch(state.scope, h.contexts[state.scope])
            h.venue.settle_cancel("order-2")
            assert await h.service()
            await h.advance("plan")
            assert await h.executor.dispatch(state.scope, h.contexts[state.scope])
            h.venue.fill("order-3", "rebate", D(1), D(200), (ActualFee("QUOTE", D("-0.1"), None),))
            assert await h.service() and await h.service()
            cycle = await h.advance("complete")
            assert cycle.phase is CyclePhase.COMPLETE and cycle.final is not None
            assert cycle.final.net == D("1100.1")

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_universe_change_during_exit_io_cannot_clear_latch_or_duplicate_sell(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: PortfolioPolicy,
    universe_policy: UniversePolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with harness(tmp_path / "ledger.db", bounds, {state.scope: context}, policy) as h:
            await h.owner.begin("begin", 0, "cycle", policy, "tester", "baseline")
            await h.seed(state.scope)
            await h.trigger()
            await h.advance("plan")
            universe = UniverseController(h.executor, 10, lambda: (h.now[0], h.now[0] * 1000000))
            entered, release = asyncio.Event(), asyncio.Event()
            original = h.venue.submit_limit

            async def delayed(request: LimitRequest) -> OrderFact:
                entered.set()
                await release.wait()
                return await original(request)

            monkeypatch.setattr(h.venue, "submit_limit", delayed)
            sending = asyncio.create_task(h.executor.dispatch(state.scope, h.contexts[state.scope]))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                # Writer/configuration remain responsive while mock gateway I/O waits.
                await asyncio.wait_for(
                    universe.configure(
                        "disable",
                        0,
                        replace(universe_policy, enabled_venues=()),
                        "tester",
                        "disable venue",
                        h.now[0],
                    ),
                    2,
                )
                current = await h.state(state.scope)
                assert current.mode is Mode.PORTFOLIO_EXIT
                assert len(current.working) == 1 and current.working[0].unknown
                assert (await h.status()).phase is CyclePhase.EXITING
            finally:
                release.set()
                await sending
                await universe.close()
            h.venue.fill("order-2", "partial", D("0.5"), D(200), ZERO_FEES)
            assert await h.service()
            await h.advance("partial")
            assert not await h.executor.dispatch(state.scope, h.contexts[state.scope])
            assert sum(o.side is Side.SELL for o in (await h.state(state.scope)).working) == 1
            assert len(h.venue.sent) == 2

    asyncio.run(scenario())
