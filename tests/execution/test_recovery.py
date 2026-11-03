"""Bounded REST failures, inconsistent snapshots and admission invalidation."""

import asyncio
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from grid.domain.model import Context, Scope, State
from grid.execution.models import LimitRequest, Notice, Page, RateLimited
from grid.persistence.records import Bounds

from .helpers import LIMITS, ZERO_FEES, harness


def test_send_429_preserves_unknown_and_honors_retry_after_for_recovery(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            assert await h.service()

            async def limited(request: LimitRequest) -> None:
                raise RateLimited(500)

            monkeypatch.setattr(h.venue, "submit_limit", limited)
            assert not await h.send()
            reads = h.venue.reads
            assert not await h.service() and h.venue.reads == reads
            assert (await h.current()).orders[0].unknown
            h.advance(501)
            assert not await h.service() and h.venue.reads == reads + 1
            assert not await h.send()
            assert h.venue.sent == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "damage",
    [
        "incomplete",
        "cursor",
        "token",
        "epoch",
        "overflow",
        "missing_order",
        "missing_trade",
        "conflict",
        "foreign",
        "wrong_side",
        "wrong_client",
        "empty_page",
    ],
)
def test_bad_snapshot_never_posts_partial_pages_or_authorizes_sends(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            assert await h.service() and await h.send()
            trade = h.venue.fill("order-1", "fill", D(1), D(100), ZERO_FEES)
            fact = h.venue.orders["order-1"]
            calls = 0

            async def page(scope: Scope, epoch: int, cursor: str | None, size: int) -> Page:
                nonlocal calls
                calls += 1
                result = Page(epoch, "snapshot", (fact,), (trade,), None, True)
                match damage:
                    case "incomplete":
                        return replace(result, complete=False)
                    case "cursor":
                        return replace(result, next_cursor="same", complete=False)
                    case "token":
                        return replace(
                            result, token=str(calls), next_cursor=str(calls), complete=False
                        )
                    case "epoch":
                        return replace(result, epoch=epoch + 1)
                    case "overflow":
                        return replace(result, trades=(trade,) * (size + 1))
                    case "missing_order":
                        return replace(result, orders=())
                    case "missing_trade":
                        return replace(result, trades=())
                    case "conflict":
                        return replace(result, trades=(trade, replace(trade, price=D(99))))
                    case "foreign":
                        return replace(
                            result, trades=(replace(trade, scope=replace(scope, account="other")),)
                        )
                    case "wrong_side":
                        return replace(result, trades=(replace(trade, side=trade.side.SELL),))
                    case "wrong_client":
                        return replace(
                            result,
                            orders=(replace(fact, request=replace(fact.request, price="99")),),
                        )
                    case "empty_page":
                        return replace(
                            result, orders=(), trades=(), next_cursor="1", complete=False
                        )
                    case _:
                        raise AssertionError(damage)

            monkeypatch.setattr(h.venue, "page", page)
            assert not await h.service()
            assert not await h.send()
            after = await h.current()
            assert after.quote_cash == 1000 and after.owned_base == 0
            assert len(h.venue.sent) == 1
            assert calls <= 2

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["429", "timeout", "disconnect"])
def test_rest_failures_backoff_and_retained_inventory(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    async def run() -> None:
        limits = replace(LIMITS, timeout_ms=10)
        async with harness(tmp_path / "ledger.db", bounds, state, context, limits=limits) as h:
            await h.seed()
            calls = 0
            original = h.venue.page

            async def broken(scope: Scope, epoch: int, cursor: str | None, size: int) -> Page:
                nonlocal calls
                calls += 1
                if failure == "429":
                    raise RateLimited(500)
                if failure == "timeout":
                    await asyncio.Event().wait()
                raise ConnectionError("synthetic disconnect")

            monkeypatch.setattr(h.venue, "page", broken)
            h.executor.degrade("private stream disconnected")
            assert not await h.send()
            assert not await h.service()
            for _ in range(20):
                assert not await h.service()
            assert calls == 1
            assert (await h.current()).owned_base == 1
            monkeypatch.setattr(h.venue, "page", original)
            h.advance()
            assert await h.service() and await h.send()

    asyncio.run(run())


def test_quiet_stream_rate_budget_and_generation_do_not_authorize_exposure(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
) -> None:
    async def run() -> None:
        async with harness(
            tmp_path / "ledger.db",
            bounds,
            state,
            context,
            limits=replace(LIMITS, calls_per_window=2),
        ) as h:
            assert await h.service()
            h.advance()
            assert not await h.send()  # Silence is not no fills.
            assert await h.service() and await h.send()
            assert not await h.service()  # Shared read/write request budget exhausted.
            reads = h.venue.reads
            assert not await h.service() and h.venue.reads == reads
            h.advance()
            assert await h.service()
        async with harness(tmp_path / "stale.db", bounds, state, context) as h:
            assert await h.service()
            h.context = replace(context, market=replace(context.market, generation=2))
            assert not await h.send()
            assert h.venue.sent == []

    asyncio.run(run())


def test_reconnect_during_snapshot_and_caller_cancellation_do_not_resume(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            original = h.venue.page

            async def reconnect(scope: Scope, epoch: int, cursor: str | None, size: int) -> Page:
                result = await original(scope, epoch, cursor, size)
                h.executor.degrade("reconnected private stream")
                return result

            monkeypatch.setattr(h.venue, "page", reconnect)
            assert not await h.service() and not await h.send()
            h.advance()
            entered = asyncio.Event()

            async def stall(scope: Scope, epoch: int, cursor: str | None, size: int) -> Page:
                entered.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

            monkeypatch.setattr(h.venue, "page", stall)
            task = asyncio.create_task(h.service())
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not await h.send()
            monkeypatch.setattr(h.venue, "page", original)
            assert await h.service()
            h.executor.offer(Notice(h.scope, h.executor.epoch, "heartbeat"))
            assert not await h.send()

    asyncio.run(run())


def test_traversal_page_and_record_bounds_are_hard_limits(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
) -> None:
    async def run() -> None:
        async with harness(
            tmp_path / "ledger.db",
            bounds,
            state,
            context,
            limits=replace(LIMITS, page_size=1, max_pages=1),
        ) as h:
            assert await h.service() and await h.send()
            h.venue.fill("order-1", "fill", D(1), D(100), ZERO_FEES)
            assert not await h.service()
            assert h.executor.last_error == "reconciliation page bound exceeded"
            assert (await h.current()).quote_cash == 1000
        async with harness(
            tmp_path / "records.db", bounds, state, context, limits=replace(LIMITS, max_records=2)
        ) as h:
            assert await h.service() and await h.send()
            h.venue.fill("order-1", "fill1", D("0.5"), D(100), ZERO_FEES)
            h.venue.fill("order-1", "fill2", D("0.5"), D(100), ZERO_FEES)
            assert not await h.service()
            assert h.executor.last_error == "reconciliation record bound exceeded"

    asyncio.run(run())
