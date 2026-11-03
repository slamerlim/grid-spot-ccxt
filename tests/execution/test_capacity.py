import asyncio
from dataclasses import replace
from decimal import Decimal as D
from functools import partial
from pathlib import Path

import pytest

from grid.domain.market import Money
from grid.domain.model import Context, Scope, State
from grid.execution.executor import MockExecutor
from grid.execution.models import Notice, Page, RateLimited
from grid.persistence.ledger import Ledger
from grid.persistence.records import Backpressure, Bounds

from .helpers import EVIDENCE, LIMITS, ZERO_FEES, harness


def test_busy_executor_rejects_waiters_and_burst_rest_recovers_actual_fill(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            assert await h.service() and await h.send()
            h.venue.fill("order-1", "fill", D(1), D(100), ZERO_FEES)
            admitted = rejected = 0
            for _ in range(1000):
                try:
                    h.executor.offer(Notice(h.scope, h.executor.epoch, "trade"))
                    admitted += 1
                except Backpressure:
                    rejected += 1
            assert admitted == LIMITS.queue_size and rejected == 1000 - LIMITS.queue_size
            entered, release = asyncio.Event(), asyncio.Event()
            original = h.venue.page

            async def stalled(scope: Scope, epoch: int, cursor: str | None, size: int) -> Page:
                entered.set()
                await release.wait()
                return await original(scope, epoch, cursor, size)

            monkeypatch.setattr(h.venue, "page", stalled)
            task = asyncio.create_task(h.service())
            await entered.wait()
            for _ in range(100):
                with pytest.raises(Backpressure, match="busy"):
                    await h.send()
                with pytest.raises(Backpressure, match="busy"):
                    await h.service()
            release.set()
            assert await task
            after = await h.current()
            assert after.quote_cash == 900 and after.owned_base == 1
            assert not h.executor._queue
            assert not h.executor.degraded

    asyncio.run(run())


@pytest.mark.parametrize("count", [1, 7])
def test_venue_backoff_does_not_starve_other_accounts(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    monkeypatch: pytest.MonkeyPatch,
    count: int,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            scopes = tuple(
                replace(h.scope, venue="synthetic-" + str(index)) for index in range(count)
            )
            for index, scope in enumerate(scopes):
                await h.writer.submit(
                    partial(
                        Ledger.credit,
                        request_id="fund" + str(index),
                        venue=scope.venue,
                        account=scope.account,
                        money=Money("QUOTE", D(1000)),
                    )
                )
                await h.writer.submit(
                    partial(
                        Ledger.start,
                        request_id="start" + str(index),
                        scope=scope,
                        funds=(Money("QUOTE", D(1000)),),
                        context=context,
                    )
                )
            await h.executor.close()
            executor = MockExecutor(h.writer, h.venue, scopes, LIMITS, EVIDENCE)
            original = h.venue.page

            async def page(scope: Scope, epoch: int, cursor: str | None, size: int) -> Page:
                if scope == scopes[0]:
                    raise RateLimited(60000)
                return await original(scope, epoch, cursor, size)

            monkeypatch.setattr(h.venue, "page", page)
            try:
                assert not await executor.service(scopes[0], context)
                for scope in scopes[1:]:
                    assert await executor.service(scope, context)
                    assert await executor.dispatch(scope, context)
                assert executor.degraded  # First account is still unresolved.
                assert len(h.venue.sent) == count - 1
                assert not await executor.dispatch(scopes[0], context)
            finally:
                await executor.close()

    asyncio.run(run())
