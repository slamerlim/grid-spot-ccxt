"""Financial lifecycle tests through the actual writer, executor and fake venue."""

import asyncio
from decimal import Decimal as D
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.domain.model import ActualFee, Context, Control, Mode, Phase, Scope, Side, State
from grid.execution.executor import MockExecutor
from grid.execution.models import ExecutionError, LimitRequest, Notice
from grid.persistence.records import Backpressure, Bounds
from grid.persistence.writer import AsyncWriter

from .helpers import EVIDENCE, LIMITS, ZERO_FEES, harness


def test_startup_first_send_seed_and_ladder(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            assert not await h.send()
            assert await h.service()
            assert await h.send()
            assert len(h.venue.sent) == 1
            assert not await h.send()
            assert await h.service()
            assert not await h.send()  # Acknowledged seed is not a fill.
            before = await h.current()
            assert before.owned_base == 0 and before.anchor is None
            h.venue.fill("order-1", "part", D("0.05"), D("99"), ZERO_FEES)
            assert await h.service()
            assert not await h.send()  # Below sell minimum, no lower ladder yet.
            h.venue.fill("order-1", "rest", D("0.95"), D("100"), ZERO_FEES)
            assert await h.service()
            after = await h.current()
            assert after.anchor == D("99.95")
            assert after.owned_base == 1 and after.quote_cash == D("900.05")
            assert sum(order.side is Side.SELL for order in after.working) == 1
            assert sum(order.role == "ladder" for order in after.working) == 3
            for _ in range(4):
                assert await h.send(), h.executor.last_error
                assert await h.service(), h.executor.last_error
            assert [request.side for request in h.venue.sent] == [
                Side.BUY,
                Side.SELL,
                Side.BUY,
                Side.BUY,
                Side.BUY,
            ]
            assert all(request.order_type == "limit" for request in h.venue.sent)
            assert len({request.price for request in h.venue.sent[2:]}) == 3

    asyncio.run(run())


@pytest.mark.parametrize(
    "mode", [Mode.PAUSED, Mode.RETIRING, Mode.PORTFOLIO_EXIT, Mode.EXIT_BLOCKED]
)
def test_control_revokes_permission_for_a_previously_queued_buy(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    mode: Mode,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            assert await h.service()
            snapshot = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            assert snapshot.version is not None
            version = snapshot.version
            await h.writer.submit(
                lambda ledger: ledger.apply(
                    "control",
                    version,
                    Control(h.scope, 1, mode),
                    context,
                )
            )
            assert not await h.send()
            assert h.venue.sent == []

    asyncio.run(run())


@pytest.mark.parametrize("timeout", [False, True])
def test_partial_sell_during_cancel_then_terminal_proof_allows_one_replacement(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    monkeypatch: pytest.MonkeyPatch,
    timeout: bool,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            await h.seed()
            for _ in range(2):
                assert await h.send() and await h.service()
            lower = h.venue.sent[-1]
            h.venue.fill("order-3", "lower", D(lower.amount), D(lower.price), ZERO_FEES)
            assert await h.service()
            original = h.venue.cancel_limit

            async def cancel(scope: Scope, external_id: str) -> None:
                await original(scope, external_id)
                if timeout:
                    raise TimeoutError("cancel ack lost")

            monkeypatch.setattr(h.venue, "cancel_limit", cancel)
            assert await h.send() == (not timeout)
            h.venue.fill("order-2", "partial-sell", D("0.25"), D(110), ZERO_FEES)
            assert not await h.service()  # Still OPEN, irrespective of cancel acknowledgement.
            assert not await h.send()
            h.venue.settle_cancel("order-2")
            h.advance()
            assert await h.service(), h.executor.last_error
            assert await h.send()
            assert h.venue.sent[-1].side is Side.SELL
            assert h.venue.sent[-1].amount == "1.75"
            after = await h.current()
            assert after.quote_cash + after.position_cost == 1000 + after.realized_pnl
            assert sum(order.side is Side.SELL for order in after.working) == 1
            assert h.venue.cancels == ["order-2"]

    asyncio.run(run())


def test_actual_rebate_is_posted_once(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            assert await h.service() and await h.send()
            h.venue.fill("order-1", "rebate", D(1), D(100), (ActualFee("QUOTE", D("-0.1"), None),))
            assert await h.service() and await h.service()
            after = await h.current()
            assert after.quote_cash == D("900.1") and after.position_cost == D("99.9")
            assert after.quote_cash + after.position_cost == 1000

    asyncio.run(run())


def test_accepted_timeout_restart_reconciles_without_resending(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        path = tmp_path / "ledger.db"
        async with harness(path, bounds, state, context) as h:
            original = h.venue.submit_limit

            async def accepted_timeout(request: LimitRequest) -> None:
                await original(request)
                raise TimeoutError("ack lost")

            monkeypatch.setattr(h.venue, "submit_limit", accepted_timeout)
            assert await h.service()
            assert not await h.send()
            assert (await h.current()).orders[0].unknown
            for _ in range(5):
                assert not await h.send()
            assert len(h.venue.sent) == 1
            await h.executor.close()
            await h.writer.close()
            writer = await AsyncWriter.open(path, bounds, SOURCES, mock_storage=True)
            executor = MockExecutor(writer, h.venue, (h.scope,), LIMITS, EVIDENCE)
            try:
                assert not await executor.dispatch(h.scope, context)
                assert await executor.service(h.scope, context), executor.last_error
                assert not await executor.dispatch(h.scope, context)
                assert len(h.venue.sent) == 1
                restored = await writer.submit(lambda ledger: ledger.snapshot(h.scope))
                assert restored.state is not None and not restored.state.orders[0].unknown
            finally:
                await executor.close()
                await writer.close()

    asyncio.run(run())


def test_unknown_without_external_identity_never_becomes_retryable(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:

            async def timeout(request: LimitRequest) -> None:
                raise TimeoutError("cannot establish whether accepted")

            monkeypatch.setattr(h.venue, "submit_limit", timeout)
            assert await h.service()
            assert not await h.send()
            for _ in range(5):
                h.advance()
                assert not await h.service()
                assert not await h.send()
            assert h.venue.sent == []
            assert (await h.current()).orders[0].unknown
            assert "unresolved" in (h.executor.last_error or "") or "reconciliation" in (
                h.executor.last_error or ""
            )

    asyncio.run(run())


def test_cancel_ack_keeps_sell_slot_and_old_sell_fill_is_posted_first(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            await h.seed()
            assert await h.send()  # SELL
            assert await h.service()
            assert await h.send()  # first lower BUY
            assert await h.service()
            lower = h.venue.sent[-1]
            h.venue.fill("order-3", "lower", D(lower.amount), D(lower.price), ZERO_FEES)
            assert await h.service()
            assert await h.send()  # cancel old SELL
            assert h.venue.cancels == ["order-2"]
            assert not await h.service()
            assert not await h.send()
            assert sum(order.side is Side.SELL for order in (await h.current()).working) == 1
            h.venue.fill("order-2", "sell-old", D(1), D(h.venue.sent[1].price), ZERO_FEES)
            h.advance()
            assert await h.service(), h.executor.last_error
            # Unsent lower orders can be revoked locally; there is no fake cancel for them.
            for _ in range(8):
                if not await h.send():
                    break
                assert await h.service(), h.executor.last_error
            final = await h.current()
            assert final.quote_cash >= 0 and final.owned_base == D(lower.amount)
            # Basket accounting releases proportional aggregate cost, not FIFO cost.
            assert final.realized_pnl == D("14.55")
            assert final.quote_cash + final.position_cost == 1000 + final.realized_pnl
            assert sum(order.side is Side.SELL for order in final.working) <= 1
            assert len(h.venue.cancels) == 1

    asyncio.run(run())


def test_full_sell_then_late_buy_becomes_next_seed_without_duplicate_seed(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            await h.seed()
            for _ in range(2):
                assert await h.send()
                assert await h.service()
            h.venue.fill("order-2", "sell", D(1), D(h.venue.sent[1].price), ZERO_FEES)
            assert await h.service()
            assert (await h.current()).phase is Phase.DRAINING
            assert await h.send()  # cancel lower; delayed ack
            lower = h.venue.sent[2]
            h.venue.fill("order-3", "late", D(lower.amount), D(lower.price), ZERO_FEES)
            assert await h.service(), h.executor.last_error
            for _ in range(5):
                assert await h.send(), h.executor.last_error
                assert await h.service(), h.executor.last_error
                if (await h.current()).round_id == 2:
                    break
            after = await h.current()
            assert after.round_id == 2 and after.owned_base == D(lower.amount)
            assert after.anchor == D(lower.price)
            assert sum(order.role == "seed" for order in after.orders) == 1

    asyncio.run(run())


def test_duplicate_reordered_trades_and_stream_replay_are_financially_idempotent(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            assert await h.service() and await h.send()
            first = h.venue.fill("order-1", "one", D("0.4"), D(100), ZERO_FEES)
            second = h.venue.fill("order-1", "two", D("0.6"), D(100), ZERO_FEES)
            h.venue.trades[:] = [second, first, first, second]
            assert await h.service(), h.executor.last_error
            before = await h.current()
            for _ in range(3):
                h.executor.offer(Notice(h.scope, h.executor.epoch, "trade"))
                assert not await h.send()
                assert await h.service()
            after = await h.current()
            assert (
                after.quote_cash,
                after.owned_base,
                after.position_cost,
                after.realized_pnl,
            ) == (before.quote_cash, before.owned_base, before.position_cost, before.realized_pnl)
            assert await h.writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
            ) == (2,)
            assert await h.writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM trade_keys").fetchone()
            ) == (2,)

    asyncio.run(run())


@pytest.mark.parametrize("fees", [None, (ActualFee("TOKEN", D("0.01"), None),)])
def test_missing_fee_or_conversion_holds_without_inventing_cash(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    fees: tuple[ActualFee, ...] | None,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            assert await h.service() and await h.send()
            h.venue.fill("order-1", "trade", D(1), D(100), fees)
            assert not await h.service()
            assert not await h.send()
            assert (await h.current()).quote_cash == 1000

    asyncio.run(run())


def test_fee_breach_posts_actual_liability_but_never_auto_resumes(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            assert await h.service() and await h.send()
            h.venue.fill(
                "order-1",
                "trade",
                D(1),
                D(100),
                (
                    ActualFee("BASE", D("0.001"), None),
                    ActualFee("QUOTE", D("0.1"), None),
                    ActualFee("TOKEN", D("0.01"), D("0.2")),
                ),
            )
            for _ in range(3):
                assert not await h.service()
                assert not await h.send()
                after = await h.current()
                assert (after.quote_cash, after.owned_base, after.position_cost) == (
                    D("899.9"),
                    D("0.999"),
                    D("100.3"),
                )
                assert after.fee_balances[0].amount == D("9.99")
                h.advance()

    asyncio.run(run())


def test_single_owner_queue_overload_and_reconnect_epoch(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            with pytest.raises(ExecutionError, match="already"):
                MockExecutor(h.writer, h.venue, (h.scope,), LIMITS, EVIDENCE)
            assert await h.service()
            epoch = h.executor.epoch
            for _ in range(LIMITS.queue_size):
                h.executor.offer(Notice(h.scope, epoch, "heartbeat"))
            with pytest.raises(Backpressure):
                h.executor.offer(Notice(h.scope, epoch, "trade"))
            assert len(h.executor._queue) == LIMITS.queue_size
            assert not await h.send()
            with pytest.raises(ExecutionError, match="epoch"):
                h.executor.offer(Notice(h.scope, epoch, "order"))
            assert await h.service()
            h.executor.degrade("private WS unsupported")
            assert not await h.send()
            assert await h.service() and await h.send()
            assert len(h.venue.sent) == 1

    asyncio.run(run())
