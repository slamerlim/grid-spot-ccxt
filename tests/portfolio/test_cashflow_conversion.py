import asyncio
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest
from execution.helpers import ZERO_FEES
from persistence.conftest import SOURCES

from grid.domain.market import Money
from grid.domain.model import Context, State, TransferredLot
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds, Conflict, LedgerError
from grid.persistence.storage import restore
from grid.portfolio.model import Cashflow, Conversion, CyclePhase, PoolKey, PortfolioPolicy

from .conftest import prices
from .helpers import Harness, harness


@pytest.mark.usefixtures("async_sockets")
def test_external_deposit_is_classified_and_not_profit(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, policy: PortfolioPolicy
) -> None:
    async def scenario() -> None:
        pool = PoolKey(state.scope.venue, state.scope.account, "QUOTE")
        configured = replace(policy, pools=(pool,), cashflow_rule="additive_baseline")
        async with harness(tmp_path / "ledger.db", bounds, {state.scope: context}, configured) as h:
            await h.owner.begin("begin", 0, "cycle", configured, "tester", "baseline")
            flow = Cashflow(
                "external-1", pool, D(500), 10000, "unclassified receipt", "unclassified"
            )
            await h.owner.cashflow("notice", 1, flow, "tester", "balance change")
            assert not await h.executor.dispatch(state.scope, context)
            assert not h.venue.sent
            h.tick()
            await h.observe()
            with pytest.raises(LedgerError):
                await h.owner.evaluate("blocked", 2, "tester", "unknown cashflow")
            with pytest.raises(LedgerError):
                await h.writer.submit(
                    lambda ledger: ledger.credit(
                        "bypass", pool.venue, pool.account, Money("QUOTE", D(500))
                    )
                )
            adjusted = replace(flow, evidence_id="reconciled deposit", disposition="adjusted")
            outcome = await h.owner.cashflow(
                "classify", 2, adjusted, "tester", "explicit additive baseline"
            )
            assert (
                await h.owner.cashflow(
                    "classify", 2, adjusted, "tester", "explicit additive baseline"
                )
                == outcome
            )
            with pytest.raises(Conflict):
                await h.owner.cashflow("double", 3, adjusted, "tester", "duplicate")
            await h.owner.evaluate("after", 3, "tester", "deposit is not a win")
            cycle = await h.status()
            assert cycle.phase is CyclePhase.ACTIVE and cycle.adjustment == 500
            assert not cycle.pending_flows and cycle.trigger is None
            assert (
                await h.writer.submit(lambda ledger: ledger.pool(pool.venue, pool.account, "QUOTE"))
                == 500
            )
            withdrawal = Cashflow(
                "external-2", pool, D(-200), h.now[0], "withdrawal", "unclassified"
            )
            await h.owner.cashflow(
                "notice2", cycle.revision, withdrawal, "tester", "observed withdrawal"
            )
            cycle = await h.status()
            await h.owner.cashflow(
                "classify2",
                cycle.revision,
                replace(withdrawal, disposition="adjusted"),
                "tester",
                "classified withdrawal",
            )
            assert (await h.status()).adjustment == 300

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_no_transfers_policy_refuses_baseline_adjustment(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, policy: PortfolioPolicy
) -> None:
    async def scenario() -> None:
        pool = PoolKey(state.scope.venue, state.scope.account, "QUOTE")
        configured = replace(policy, pools=(pool,))
        async with harness(tmp_path / "ledger.db", bounds, {state.scope: context}, configured) as h:
            await h.owner.begin("begin", 0, "cycle", configured, "tester", "baseline")
            flow = Cashflow("observation", pool, D(10), 10000, "unclassified", "unclassified")
            await h.owner.cashflow("notice", 1, flow, "tester", "investigate")
            with pytest.raises(LedgerError):
                await h.owner.cashflow(
                    "forbidden",
                    2,
                    replace(flow, disposition="adjusted"),
                    "tester",
                    "not authorized by policy",
                )
            await h.owner.cashflow(
                "resolve",
                2,
                replace(
                    flow, evidence_id="proved duplicate observation", disposition="not_external"
                ),
                "tester",
                "no external contribution occurred",
            )
            assert not (await h.status()).pending_flows
            assert (
                await h.writer.submit(lambda ledger: ledger.pool(pool.venue, pool.account, "QUOTE"))
                == 0
            )

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_fee_assets_move_to_explicit_same_account_sink_and_exit_with_actual_fills(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, policy: PortfolioPolicy
) -> None:
    async def scenario() -> None:
        sink = replace(state.scope, symbol="TOKEN/QUOTE", allocation="fee-liquidation")
        token_context = prices(
            replace(context, market=replace(context.market, symbol=sink.symbol, base="TOKEN")),
            D(1),
            10000,
        )
        configured = replace(
            policy, exits=(*policy.exits, replace(policy.exits[0], scope=sink)), sinks=(sink,)
        )

        async def observe(h: Harness) -> None:
            row = Conversion(
                PoolKey(state.scope.venue, state.scope.account, "TOKEN"),
                "QUOTE",
                D(1),
                D(1),
                D(1000),
                h.now[0],
                h.now[0] * 1000000,
                "verified direct TOKEN quote",
            )
            await h.owner.observe(replace(await h.facts(), conversions=(row,)))

        async with harness(
            tmp_path / "ledger.db",
            bounds,
            {state.scope: context, sink: token_context},
            configured,
            token=D(10),
        ) as h:
            await observe(h)
            await h.owner.begin(
                "begin", 0, "cycle", configured, "tester", "baseline includes fee assets"
            )
            await h.seed(state.scope)
            h.tick()
            h.contexts[state.scope] = prices(context, D(201), h.now[0])
            assert await h.service()
            await observe(h)
            await h.owner.evaluate("trigger", 1, "tester", "all holdings threshold")
            assert (await h.status()).phase is CyclePhase.REQUESTED
            assert await h.service()
            await observe(h)
            await h.owner.advance("primary-plan", 2, "tester", "liquidate principal first")
            assert await h.executor.dispatch(state.scope, h.contexts[state.scope]), (
                h.executor.last_error
            )
            h.venue.fill("order-2", "primary-exit", D(1), D(201), ZERO_FEES)
            assert await h.service()
            await observe(h)
            await h.owner.advance("sweep", 3, "tester", "move owned fee inventory")
            original, converted = await h.state(state.scope), await h.state(sink)
            assert original.fee_balances == (Money("TOKEN", D(0)),)
            assert converted.owned_base == 10 and isinstance(converted.lots[0], TransferredLot)
            assert not converted.fills  # Ownership transfer never creates a fake trade.
            backup = await h.writer.submit(lambda ledger: ledger.backup(tmp_path / "transfer.db"))
            restored = restore(backup, tmp_path / "restored.db", bounds, SOURCES, mock_storage=True)
            recovered = Ledger(Path(restored.path), bounds, SOURCES, mock_storage=True)
            try:
                restored_state = recovered.snapshot(sink).state
                assert restored_state is not None and restored_state.lots == converted.lots
                assert recovered.portfolio() == await h.status()
                assert not restored_state.fills and restored_state.reconcile_reason
            finally:
                recovered.close()
            assert not (await h.status()).final
            assert await h.service()
            await observe(h)
            await h.owner.advance("token-plan", 4, "tester", "convert fee inventory")
            assert await h.executor.dispatch(sink, h.contexts[sink]), h.executor.last_error
            h.venue.fill("order-3", "token-exit", D(10), D(1), ZERO_FEES)
            assert await h.service()
            await observe(h)
            await h.owner.advance("complete", 5, "tester", "all bot assets reconciled")
            cycle = await h.status()
            assert cycle.phase is CyclePhase.COMPLETE and cycle.final is not None
            assert cycle.baseline.net == 1010 and cycle.final.net == 1111
            assert all(request.order_type == "limit" for request in h.venue.sent)
            assert (await h.state(sink)).owned_base == 0

    asyncio.run(scenario())
