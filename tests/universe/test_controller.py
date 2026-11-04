import asyncio
from dataclasses import replace
from decimal import Decimal as D
from functools import partial
from pathlib import Path

import pytest
from execution.helpers import EVIDENCE, LIMITS, ZERO_FEES, Harness, harness
from persistence.conftest import SOURCES

from grid.domain.market import Money
from grid.domain.model import Context, Control, Mode, Order, Scope, Side, State
from grid.domain.numbers import DomainError
from grid.execution.executor import MockExecutor
from grid.execution.models import ExecutionError, LimitRequest, Page
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds, Conflict
from grid.persistence.universe import commit
from grid.persistence.writer import AsyncWriter
from grid.universe.controller import UniverseController
from grid.universe.model import BuyAdmission, MarketKey, UniversePolicy

from .conftest import facts_for


async def own(h: Harness, policy: UniversePolicy) -> UniverseController:
    owner = UniverseController(
        h.executor, policy.cache_size, lambda: (h.context.now_ms, h.context.now_ms * 1000000)
    )
    await owner.observe(h.scope, facts_for(await h.current(), h.context))
    await owner.configure("policy-1", 0, policy, "test-owner", "initial", h.context.now_ms)
    assert await h.service(), h.executor.last_error
    return owner


@pytest.mark.usefixtures("async_sockets")
def test_unsent_denial_is_atomic_idempotent_and_retains_ownership(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, policy: UniversePolicy
) -> None:
    async def scenario() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            owner = await own(h, policy)
            denied = replace(policy, deny=(MarketKey.of(h.scope),))
            receipt = await owner.configure("deny", 1, denied, "test-owner", "denied", 10000)
            assert (
                await owner.configure("deny", 1, denied, "test-owner", "denied", 10000) == receipt
            )
            with pytest.raises(Conflict):
                await owner.configure("stale", 1, policy, "test-owner", "stale", 10000)
            with pytest.raises(Conflict):
                await owner.configure("deny", 1, policy, "test-owner", "changed", 10000)
            after = await h.current()
            assert after.mode is Mode.RETIRING and after.quote_cash == 1000
            assert not after.working and not await h.send() and not h.venue.sent
            snapshot, supervised, cached = await owner.status()
            assert snapshot is not None and snapshot.revision == 2
            assert supervised == (h.scope,) and cached == 1 and not snapshot.members[0].selected
            # Recovery needs an explicit new round after cancellation of a zero-fill seed.
            await owner.configure("readmit", 2, policy, "test-owner", "eligible again", 10000)
            assert (await h.current()).mode is Mode.ACTIVE
            assert await h.service() and not await h.send()

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_fill_arriving_during_exclusion_and_exit_is_posted_without_resuming(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: UniversePolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            owner = await own(h, policy)
            assert await h.send()
            h.venue.fill("order-1", "racing-partial", D("0.5"), D(100), ZERO_FEES)
            entered, release = asyncio.Event(), asyncio.Event()
            original = h.venue.page

            async def delayed(scope: Scope, epoch: int, cursor: str | None, size: int) -> Page:
                page = await original(scope, epoch, cursor, size)
                entered.set()
                await release.wait()
                return page

            monkeypatch.setattr(h.venue, "page", delayed)
            recovery = asyncio.create_task(h.service())
            await entered.wait()
            await owner.configure(
                "deny",
                1,
                replace(policy, enabled_venues=()),
                "test-owner",
                "excluded during fill intake",
                10000,
            )

            def exit_control(ledger: Ledger) -> None:
                snapshot = ledger.snapshot(h.scope)
                assert snapshot.state is not None and snapshot.version is not None
                ledger.apply(
                    "exit",
                    snapshot.version,
                    Control(h.scope, snapshot.state.control_revision + 1, Mode.PORTFOLIO_EXIT),
                    context,
                )

            await h.writer.submit(exit_control)
            release.set()
            await recovery
            after = await h.current()
            assert after.owned_base == D("0.5") and after.quote_cash == 950
            assert after.mode in (Mode.PORTFOLIO_EXIT, Mode.EXIT_BLOCKED)
            await owner.configure(
                "refresh", 2, policy, "test-owner", "selection during exit", 10000
            )
            assert (await h.current()).mode is not Mode.ACTIVE
            assert len(h.venue.sent) == 1

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_lru_bounds_restart_requires_all_scopes_and_fresh_evidence(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: UniversePolicy,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "ledger.db"
        async with harness(path, bounds, state, context) as h:
            await h.executor.close()
            scopes = [h.scope]
            contexts = [context]
            for index in (1, 2):
                scope = replace(h.scope, symbol=f"OTHER{index}/QUOTE", allocation=f"other{index}")
                other_context = replace(
                    context,
                    market=replace(context.market, symbol=scope.symbol, base=f"OTHER{index}"),
                )
                await h.writer.submit(
                    partial(
                        Ledger.credit,
                        request_id=f"fund-{index}",
                        venue=h.scope.venue,
                        account=h.scope.account,
                        money=Money("QUOTE", D(1000)),
                    )
                )
                await h.writer.submit(
                    partial(
                        Ledger.start,
                        request_id=f"start-{index}",
                        scope=scope,
                        funds=(Money("QUOTE", D(1000)),),
                        context=other_context,
                    )
                )
                scopes.append(scope)
                contexts.append(other_context)
            executor = MockExecutor(h.writer, h.venue, tuple(scopes), LIMITS, EVIDENCE)
            owner = UniverseController(executor, 2, lambda: (10000, 10000000000))
            for scope, ctx in zip(scopes, contexts, strict=True):
                await owner.observe(scope, facts_for(replace(state, scope=scope), ctx))
            configured = replace(policy, capacity=2, cache_size=2)
            await owner.configure("policy", 0, configured, "test-owner", "bounded cache", 10000)
            snapshot, supervised, size = await owner.status()
            assert snapshot is not None and size == 2 and supervised == tuple(scopes)
            assert {m.scope for m in snapshot.members if m.selected} == set(scopes[1:])
            with pytest.raises(DomainError):
                await owner.observe(scopes[0], facts_for(state, context))
            with pytest.raises(DomainError):
                UniverseController(executor, 2, lambda: (10000, 10000000000))
            await owner.close()
            await executor.close()

        writer = await AsyncWriter.open(path, bounds, SOURCES, mock_storage=True)
        executor = MockExecutor(writer, h.venue, tuple(scopes), LIMITS, EVIDENCE)
        owner = UniverseController(executor, 2, lambda: (10000, 10000000000))
        try:
            snapshot, supervised, size = await owner.status()
            assert snapshot is not None and size == 0 and supervised == tuple(scopes)
            assert await executor.service(scopes[1], contexts[1])
            assert not await executor.dispatch(scopes[1], contexts[1])
            assert not h.venue.sent
            await executor.close()
            missing = MockExecutor(writer, h.venue, (scopes[1],), LIMITS, EVIDENCE)
            incomplete = UniverseController(missing, 1, lambda: (10000, 10000000000))
            with pytest.raises(ExecutionError, match="roster"):
                await incomplete.status()
            await missing.close()
        finally:
            await executor.close()
            await writer.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", ["invalidate", "expire", "fees", "minimum", "book", "guard_removed", "close"]
)
@pytest.mark.usefixtures("async_sockets")
def test_queued_buy_requires_current_evidence_at_claim(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: UniversePolicy,
    failure: str,
) -> None:
    async def scenario() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            owner = await own(h, policy)
            if failure == "invalidate":
                tombstone = await owner.invalidate(h.scope)
                assert tombstone == 2
                with pytest.raises(DomainError):
                    await owner.observe(h.scope, facts_for(state, context, 2))
            elif failure == "expire":
                h.advance()
                assert await h.service()
            elif failure == "fees":
                new = replace(context, buy_fee=replace(context.buy_fee, rate=D("0.001")))
                await owner.observe(h.scope, facts_for(state, new, 2))
            elif failure == "minimum":
                new = replace(
                    context,
                    market=replace(
                        context.market,
                        generation=2,
                        buy_amount=replace(context.market.buy_amount, minimum=D(2)),
                    ),
                )
                await owner.observe(h.scope, facts_for(state, new, 2))
            elif failure == "book":
                evidence = facts_for(state, context, 2)
                await owner.observe(
                    h.scope, replace(evidence, book=replace(evidence.book, generation=2))
                )
            elif failure == "guard_removed":
                h.executor.buy_admission = None
            elif failure == "close":
                await owner.close()
            assert not await h.send()
            assert not h.venue.sent
            assert not await h.writer.submit(lambda ledger: ledger.attempted(h.scope, 1))

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_policy_race_at_before_claim_cannot_send_stale_buy(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, policy: UniversePolicy
) -> None:
    async def scenario() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            await own(h, policy)
            original = h.executor.buy_admission
            assert original is not None

            pending = False

            def fault(stage: str) -> None:
                nonlocal pending
                if stage == "executor:before_claim":
                    pending = True

            h.executor.fault = fault

            # Commit a concurrent policy change after the executor read its
            # candidate, immediately before the same writer checks admission.
            def admission(
                ledger: Ledger, scope: Scope, order: Order, supplied: Context
            ) -> BuyAdmission:
                assert pending
                commit(
                    ledger,
                    "race",
                    1,
                    replace(policy, enabled_venues=()),
                    (h.scope,),
                    "test-owner",
                    "disabled at claim",
                    10000,
                    lambda previous: (),
                )
                return original(ledger, scope, order, supplied)

            h.executor.buy_admission = admission
            assert not await h.send() and pending
            assert not h.venue.sent and (await h.current()).mode is Mode.RETIRING

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_partial_seed_deny_keeps_sell_and_late_fill_supervised(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, policy: UniversePolicy
) -> None:
    async def scenario() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            owner = await own(h, policy)
            assert await h.send(), h.executor.last_error
            h.venue.fill("order-1", "partial", D("0.5"), D(100), ZERO_FEES)
            assert await h.service()
            assert await h.send()  # Existing sellable inventory gets its aggregate SELL.
            assert h.venue.sent[-1].side is Side.SELL
            await owner.configure(
                "deny", 1, replace(policy, enabled_venues=()), "test-owner", "venue disabled", 10000
            )
            assert await h.service() and await h.send()  # Cancel known seed BUY first.
            h.venue.fill("order-2", "late-sell", D("0.5"), D(h.venue.sent[-1].price), ZERO_FEES)
            assert not await h.service()  # Cancel acknowledgement alone is not terminal.
            after = await h.current()
            assert after.mode is Mode.RETIRING and after.owned_base == 0
            assert len([o for o in after.working if o.side is Side.SELL]) <= 1
            assert len([o for o in h.venue.sent if o.side is Side.BUY]) == 1
            assert (await owner.status())[1] == (h.scope,)
            h.venue.settle_cancel("order-1")
            h.advance()
            assert await h.service(), h.executor.last_error
            assert not (await h.current()).working

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_disable_after_claim_retains_unknown_until_reconciliation(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: UniversePolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            owner = await own(h, policy)
            entered, release = asyncio.Event(), asyncio.Event()

            async def uncertain(request: LimitRequest) -> None:
                entered.set()
                await release.wait()
                raise TimeoutError("synthetic lost acknowledgement")

            monkeypatch.setattr(h.venue, "submit_limit", uncertain)
            sending = asyncio.create_task(h.send())
            await entered.wait()
            # Policy writes proceed while the fake send is awaiting I/O.
            await owner.configure(
                "disable",
                1,
                replace(policy, enabled_venues=()),
                "test-owner",
                "disabled while awaiting",
                10000,
            )
            release.set()
            assert not await sending
            after = await h.current()
            assert after.mode is Mode.RETIRING and after.orders[0].unknown
            assert not after.orders[0].terminal and after.orders[0].reserves[0].amount > 0
            assert not await h.service() and not await h.send()
            assert (await owner.status())[1] == (h.scope,)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode", [Mode.PAUSED, Mode.PORTFOLIO_EXIT, Mode.EXIT_BLOCKED, Mode.RETIRING]
)
@pytest.mark.usefixtures("async_sockets")
def test_external_control_cannot_be_resumed_by_selection(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: UniversePolicy,
    mode: Mode,
) -> None:
    async def scenario() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            owner = await own(h, policy)

            def control(ledger: Ledger) -> None:
                snapshot = ledger.snapshot(h.scope)
                assert snapshot.version is not None and snapshot.state is not None
                ledger.apply(
                    "external",
                    snapshot.version,
                    Control(h.scope, snapshot.state.control_revision + 1, mode),
                    context,
                )

            await h.writer.submit(control)
            await owner.configure("refresh", 1, policy, "test-owner", "refresh candidates", 10000)
            snapshot, _, _ = await owner.status()
            assert snapshot is not None and not snapshot.members[0].selected
            assert not snapshot.members[0].managed_retirement
            assert (await h.current()).mode is (Mode.RETIRING if mode is Mode.PAUSED else mode)
            await owner.configure("again", 2, policy, "test-owner", "refresh again", 10000)
            assert (await h.current()).mode is not Mode.ACTIVE

    asyncio.run(scenario())
