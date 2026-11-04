import asyncio
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from grid.domain.market import Money
from grid.domain.model import Context, State
from grid.domain.numbers import DomainError
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds
from grid.portfolio.model import Conversion, CyclePhase, PoolKey, PortfolioPolicy

from .conftest import prices
from .helpers import Harness, harness


@pytest.mark.usefixtures("async_sockets")
def test_failed_internal_conversion_rolls_back_pool_debit_and_journal(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: PortfolioPolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        key = PoolKey(state.scope.venue, state.scope.account, "TOKEN")
        sink = replace(state.scope, symbol="TOKEN/QUOTE", allocation="sink")
        token = prices(
            replace(context, market=replace(context.market, symbol=sink.symbol, base="TOKEN")),
            D(1),
            10000,
        )
        configured = replace(
            policy,
            exits=(*policy.exits, replace(policy.exits[0], scope=sink)),
            sinks=(sink,),
            pools=(key,),
        )

        async def observe(h: Harness, price: D) -> None:
            fx = Conversion(key, "QUOTE", price, price, D(1000), h.now[0], h.now[0] * 1000000, "FX")
            await h.owner.observe(replace(await h.facts(), conversions=(fx,)))

        async with harness(
            tmp_path / "ledger.db", bounds, {state.scope: context, sink: token}, configured
        ) as h:
            await h.writer.submit(
                lambda ledger: ledger.credit("pool", key.venue, key.account, Money("TOKEN", D(10)))
            )
            await observe(h, D(1))
            await h.owner.begin("begin", 0, "cycle", configured, "tester", "owned pool")
            h.tick()
            h.contexts[sink] = prices(token, D(12), h.now[0])
            assert await h.service()
            await observe(h, D(12))
            await h.owner.evaluate("trigger", 1, "tester", "pool appreciation")
            original = Ledger._pool_delta

            def fail_after_debit(
                ledger: Ledger, request_id: str, venue: str, account: str, money: Money
            ) -> None:
                original(ledger, request_id, venue, account, money)
                raise DomainError("injected debit failure")

            with monkeypatch.context() as patch:
                patch.setattr(Ledger, "_pool_delta", fail_after_debit)
                await h.owner.advance("failed", 2, "tester", "fault injection")
            assert (await h.status()).phase is CyclePhase.BLOCKED
            assert (
                await h.writer.submit(lambda ledger: ledger.pool(key.venue, key.account, "TOKEN"))
                == 10
            )
            assert (await h.state(sink)).owned_base == 0
            await h.owner.advance("retry", 3, "tester", "fault removed")
            assert (
                await h.writer.submit(lambda ledger: ledger.pool(key.venue, key.account, "TOKEN"))
                == 0
            )
            assert (await h.state(sink)).owned_base == 10
            assert not (await h.state(sink)).fills

    asyncio.run(scenario())
