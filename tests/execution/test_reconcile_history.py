"""Bounded current recovery retains durable identity, accounting and cancellation checks."""

import asyncio
from dataclasses import replace
from decimal import Decimal as D
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from persistence.conftest import SOURCES

from grid.domain.model import ActualFee, Context, Fill, OrderReport, Reconciled, Scope, Side, State
from grid.execution.client_ids import MappedSyntheticExchange
from grid.execution.executor import MockExecutor
from grid.execution.models import Page
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds, ClientIdPolicy
from grid.persistence.writer import AsyncWriter

from .helpers import EVIDENCE, LIMITS, ZERO_FEES, harness


def test_duplicate_closed_history_needs_only_hold_and_final_reconciliation(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            await h.seed()
            snapshot = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            version = snapshot.version
            assert version is not None
            await h.writer.submit(lambda ledger: ledger.compact("compact", h.scope, version))
            assert not (await h.current()).fills  # Durable fill survives RAM-history compaction.
            count = await h.writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM requests").fetchone()[0]
            )
            events: list[object] = []
            original = Ledger.apply

            def track(self: Ledger, *args: Any, **kwargs: Any) -> Any:
                events.append(args[2])
                return original(self, *args, **kwargs)

            monkeypatch.setattr(Ledger, "apply", track)
            for _ in range(3):
                assert await h.service(), h.executor.last_error
            assert len(events) == 3 and all(isinstance(e, Reconciled) for e in events)
            assert not any(isinstance(e, (Fill, OrderReport)) for e in events)
            assert (
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM requests").fetchone()[0]
                )
                == count + 6
            )
            after = await h.current()
            assert (after.quote_cash, after.owned_base, after.position_cost) == (
                D(900),
                D(1),
                D(100),
            )
            assert (
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()[0]
                )
                == 1
            )

    asyncio.run(run())


@pytest.mark.parametrize("mapped", [False, True])
def test_rounds_outlive_the_recovery_record_cap_without_losing_history(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    mapped: bool,
) -> None:
    async def run(max_records: int) -> State:
        fixture = replace(context, policy=replace(context.policy, max_lots=1))
        limits = replace(LIMITS, page_size=4, max_records=max_records)
        async with harness(
            tmp_path / f"ledger-{max_records}.db", bounds, state, fixture, limits=limits
        ) as h:
            if mapped:
                await h.executor.close()
                h.venue = MappedSyntheticExchange(
                    1000, h.writer, ClientIdPolicy("window-rounds-v1", "w-")
                )
                h.executor = MockExecutor(h.writer, h.venue, (h.scope,), limits, EVIDENCE)
            assert await h.service()
            for cycle in range(10):
                h.advance()
                assert await h.service(), h.executor.last_error
                for side in (Side.BUY, Side.SELL):
                    assert await h.send(), h.executor.last_error
                    order = h.venue.sent[-1]
                    assert order.side is side
                    h.venue.fill(
                        f"order-{len(h.venue.sent)}",
                        f"fill-{cycle}-{side}",
                        D(order.amount),
                        D(order.price),
                        ZERO_FEES,
                    )
                    assert await h.service(), h.executor.last_error
                snapshot = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
                assert snapshot.version is not None
                version = snapshot.version
                await h.writer.submit(
                    partial(
                        Ledger.compact,
                        request_id=f"checkpoint-{cycle}",
                        scope=h.scope,
                        expected=version,
                    )
                )
                assert await h.service(), h.executor.last_error
            result = await h.current()
            assert result.round_id == 11 and result.owned_base == 0
            assert result.quote_cash == 1000 + result.realized_pnl
            assert len(h.venue.orders) + len(h.venue.trades) == 40
            assert await h.writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
            ) == (20,)
            # A current OPEN report for an old, compacted order is still visible
            # outside the recent window and must halt without changing money.
            old = h.venue.orders["order-1"]
            h.venue.orders["order-1"] = replace(old, status="open")
            if isinstance(h.venue, MappedSyntheticExchange):
                h.venue.wire_orders["order-1"]["status"] = "open"
            assert not await h.service() and not await h.send()
            assert (await h.current()).quote_cash == result.quote_cash
            assert len(h.venue.sent) == 20
            return result

    async def compare() -> None:
        assert await run(12) == await run(1000)

    asyncio.run(compare())


@pytest.mark.parametrize("mapped", [False, True])
def test_partial_fills_and_fees_survive_window_omission_and_writer_restart(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    monkeypatch: pytest.MonkeyPatch,
    mapped: bool,
) -> None:
    async def run() -> None:
        path = tmp_path / "ledger.db"
        fixture = replace(context, buy_fee=replace(context.buy_fee, rate=D(".001")))
        limits = replace(LIMITS, max_records=24)
        async with harness(path, bounds, state, fixture, limits=limits) as h:
            if mapped:
                await h.executor.close()
                h.venue = MappedSyntheticExchange(
                    1000, h.writer, ClientIdPolicy("window-partials-v1", "w-")
                )
                h.executor = MockExecutor(h.writer, h.venue, (h.scope,), limits, EVIDENCE)
            assert await h.service() and await h.send()
            original = h.venue.page
            observed: list[str] = []

            async def capture(
                scope: Scope,
                epoch: int,
                cursor: str | None,
                size: int,
                clients: tuple[str, ...],
                recent: int,
            ) -> Page:
                page = await original(scope, epoch, cursor, size, clients, recent)
                observed.extend(trade.external_id for trade in page.trades)
                return page

            monkeypatch.setattr(h.venue, "page", capture)
            for index in range(9):
                h.venue.fill(
                    "order-1",
                    f"partial-{index}",
                    D(".1"),
                    D(100),
                    (ActualFee("QUOTE", D(".01"), None),),
                )
                observed.clear()
                assert await h.service(), h.executor.last_error
            assert "partial-0" not in observed  # SQLite supplies the omitted baseline.
            assert (await h.current()).owned_base == D(".9")
            await h.executor.close()
            await h.writer.close()
            writer = await AsyncWriter.open(path, bounds, SOURCES, mock_storage=True)
            if isinstance(h.venue, MappedSyntheticExchange):
                h.venue.writer = writer
            recovered = MockExecutor(writer, h.venue, (h.scope,), limits, EVIDENCE)
            try:
                h.venue.fill(
                    "order-1",
                    "last",
                    D(".1"),
                    D(100),
                    (ActualFee("QUOTE", D(".01"), None),),
                )
                for _ in range(3):
                    assert await recovered.service(h.scope, fixture), recovered.last_error
                result = await writer.submit(lambda ledger: ledger.snapshot(h.scope))
                assert result.state is not None
                assert (
                    result.state.quote_cash,
                    result.state.owned_base,
                    result.state.position_cost,
                ) == (
                    D("899.90"),
                    D(1),
                    D("100.10"),
                )
                assert await writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
                ) == (10,)
                assert len(h.venue.sent) == 1
            finally:
                await recovered.close()
                await writer.close()

    asyncio.run(run())


@pytest.mark.parametrize("damage", ["missing_new", "fee_projection", "missing_identity"])
def test_partial_baseline_and_new_gap_are_checked_before_any_new_posting(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    damage: str,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            assert await h.service() and await h.send()
            h.venue.fill("order-1", "old", D(".2"), D(100), ZERO_FEES)
            assert await h.service()
            h.venue.fill("order-1", "new", D(".2"), D(100), ZERO_FEES)
            h.venue.trades = h.venue.trades[1:]  # Omit the committed fill in every case.
            if damage == "missing_new":
                h.venue.trades.clear()
            elif damage == "fee_projection":
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("UPDATE fees SET amount='1'")
                )
            else:
                await h.writer.submit(lambda ledger: ledger.db.execute("DELETE FROM trade_keys"))
            reads = h.venue.reads
            assert not await h.service() and not await h.send()
            if damage != "missing_new":
                assert h.venue.reads == reads  # Bad durable baselines fail before gateway reads.
            result = await h.current()
            assert (result.quote_cash, result.owned_base) == (D(980), D(".2"))
            assert await h.writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
            ) == (1,)

    asyncio.run(run())


@pytest.mark.parametrize("breach", ["fees", "settlements"])
def test_fee_stop_survives_omitted_history_compaction_and_restart(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    breach: str,
) -> None:
    async def run() -> None:
        path = tmp_path / "ledger.db"
        fixture = replace(context, buy_fee=replace(context.buy_fee, max_settlements=1))
        async with harness(path, bounds, state, fixture) as h:
            assert await h.service() and await h.send()
            if breach == "fees":
                h.venue.fill("order-1", "fee", D(1), D(100), (ActualFee("QUOTE", D(1), None),))
            else:
                h.venue.fill("order-1", "first", D(".5"), D(100), ZERO_FEES)
                h.venue.fill("order-1", "second", D(".5"), D(100), ZERO_FEES)
            assert not await h.service()
            before = await h.current()
            assert before.owned_base == 1
            snapshot = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            assert snapshot.version is not None
            version = snapshot.version
            await h.writer.submit(lambda ledger: ledger.compact("checkpoint", h.scope, version))
            h.venue.orders.clear()
            h.venue.trades.clear()  # Venue retention cannot clear a durable fee stop.
            await h.executor.close()
            await h.writer.close()
            writer = await AsyncWriter.open(path, bounds, SOURCES, mock_storage=True)
            recovered = MockExecutor(writer, h.venue, (h.scope,), LIMITS, EVIDENCE)
            try:
                for _ in range(3):
                    h.advance()
                    assert not await recovered.service(h.scope, h.context)
                    assert recovered.last_error == "actual fees exceed the reserved forecast"
                    assert not await recovered.dispatch(h.scope, h.context)
                after = await writer.submit(lambda ledger: ledger.snapshot(h.scope))
                assert after.state is not None
                assert (after.state.quote_cash, after.state.owned_base) == (before.quote_cash, D(1))
                assert len(h.venue.sent) == 1
            finally:
                await recovered.close()
                await writer.close()

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["price", "fee", "trade_key", "missing_key", "binding"])
def test_changed_committed_fact_blocks_entire_snapshot_before_new_fill_posting(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    fault: str,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            await h.seed()
            for _ in range(4):
                assert await h.send(), h.executor.last_error
                assert await h.service(), h.executor.last_error
            buy = next(
                f
                for f in h.venue.orders.values()
                if f.request.side is Side.BUY and f.status == "open"
            )
            new = h.venue.fill(buy.external_id, "new-lower", D(1), D(buy.request.price), ZERO_FEES)
            old = h.venue.trades[0]
            old = replace(old, sequence=3)  # New fill would sort before the conflicting old fact.
            if fault == "price":
                old = replace(old, price=D(99))
            elif fault == "fee":
                old = replace(old, fees=(ActualFee("QUOTE", D("-0.01"), None),))
            elif fault == "trade_key":
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("UPDATE trade_keys SET external_id='wrong'")
                )
            elif fault == "missing_key":
                await h.writer.submit(lambda ledger: ledger.db.execute("DELETE FROM trade_keys"))
            else:
                await h.writer.submit(
                    lambda ledger: ledger.db.execute(
                        "UPDATE order_bindings SET client_id='wrong' WHERE order_id=1"
                    )
                )
            h.venue.trades = [new, old]
            assert not await h.service()
            assert not await h.send()
            after = await h.current()
            assert after.reconcile_reason is not None
            assert (after.quote_cash, after.owned_base) == (D(900), D(1))
            assert (
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()[0]
                )
                == 1
            )

    asyncio.run(run())


def test_open_partial_order_still_requires_current_report_and_new_trade(
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
            h.venue.fill("order-1", "partial", D("0.5"), D(100), ZERO_FEES)
            assert await h.service()
            events: list[object] = []
            original = Ledger.apply

            def track(self: Ledger, *args: Any, **kwargs: Any) -> Any:
                events.append(args[2])
                return original(self, *args, **kwargs)

            monkeypatch.setattr(Ledger, "apply", track)
            assert await h.service()
            assert not any(isinstance(e, Fill) for e in events)
            assert any(isinstance(e, OrderReport) for e in events)
            h.venue.fill("order-1", "remaining", D("0.5"), D(100), ZERO_FEES)
            events.clear()
            assert await h.service()
            assert sum(isinstance(e, Fill) for e in events) == 1
            assert sum(isinstance(e, OrderReport) and e.order_id == 1 for e in events) == 1
            after = await h.current()
            assert (after.quote_cash, after.owned_base) == (D(900), D(1))

    asyncio.run(run())


@pytest.mark.parametrize("mapped", [False, True])
@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("contradiction", ["open", "extra_fill"])
def test_current_terminal_contradiction_blocks_replacement_and_all_new_posting(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    mapped: bool,
    archived: bool,
    contradiction: str,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            if mapped:
                await h.executor.close()
                h.venue = MappedSyntheticExchange(
                    1000, h.writer, ClientIdPolicy("mock-terminal-v1", "t-")
                )
                h.executor = MockExecutor(h.writer, h.venue, (h.scope,), LIMITS, EVIDENCE)
            try:
                await h.seed()
                for _ in range(4):
                    assert await h.send() and await h.service(), h.executor.last_error
                h.venue.fill("order-3", "lower", D(1), D(h.venue.sent[2].price), ZERO_FEES)
                assert await h.service()
                assert await h.send()  # Request cancellation of the original SELL.
                h.venue.fill("order-2", "partial-sell", D("0.25"), D(110), ZERO_FEES)
                h.venue.settle_cancel("order-2")
                assert await h.service(), h.executor.last_error
                original = h.venue.orders["order-2"]
                assert original.status == "canceled"
                if archived:
                    snapshot = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
                    assert snapshot.version is not None
                    version = snapshot.version
                    await h.writer.submit(
                        lambda ledger: ledger.compact("compact", h.scope, version)
                    )
                    assert not any(order.id == 2 for order in (await h.current()).orders)
                before = await h.current()
                sent = tuple(h.venue.sent)
                fills = await h.writer.submit(
                    lambda ledger: tuple(ledger.db.execute("SELECT * FROM fills ORDER BY trade_id"))
                )
                # A valid new BUY sorts before the impossible late SELL fill. The
                # entire recovery must be checked before any of it reaches money.
                h.venue.fill("order-4", "new-lower", D(1), D(h.venue.sent[3].price), ZERO_FEES)
                h.venue.orders["order-2"] = replace(original, status="open")
                if isinstance(h.venue, MappedSyntheticExchange):
                    h.venue.wire_orders["order-2"]["status"] = "open"
                if contradiction == "extra_fill":
                    h.venue.fill("order-2", "impossible-late", D("0.25"), D(110), ZERO_FEES)
                    h.venue.settle_cancel("order-2")
                assert not await h.service()
                assert not await h.send()
                assert tuple(h.venue.sent) == sent
                after = await h.current()
                assert after.reconcile_reason is not None
                assert (after.quote_cash, after.owned_base, after.position_cost) == (
                    before.quote_cash,
                    before.owned_base,
                    before.position_cost,
                )
                assert (
                    await h.writer.submit(
                        lambda ledger: tuple(
                            ledger.db.execute("SELECT * FROM fills ORDER BY trade_id")
                        )
                    )
                    == fills
                )
                # Removing the contradictory observation permits the real new BUY
                # exactly once and one replacement SELL for the reconciled inventory.
                h.venue.orders["order-2"] = original
                h.venue.trades = [t for t in h.venue.trades if t.external_id != "impossible-late"]
                if isinstance(h.venue, MappedSyntheticExchange):
                    h.venue.wire_orders["order-2"].update(
                        status="canceled", filled="0.25", remaining="0.75"
                    )
                h.advance()
                assert await h.service(), h.executor.last_error
                assert await h.send(), h.executor.last_error
                assert h.venue.sent[-1].side is Side.SELL
                assert h.venue.sent[-1].amount == "2.75"
                assert (
                    sum(
                        fact.request.side is Side.SELL and fact.status == "open"
                        for fact in h.venue.orders.values()
                    )
                    == 1
                )
                assert await h.service(), h.executor.last_error
                assert (await h.current()).owned_base == D("2.75")
            finally:
                await h.executor.close()

    asyncio.run(run())
