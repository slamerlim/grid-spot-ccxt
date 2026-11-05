"""Unified timestamps reach the same executor; ambiguity cannot choose financial results."""

import asyncio
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import ccxt.pro as ccxtpro  # type: ignore[import-untyped]
import pytest
from persistence.conftest import SOURCES

from grid.domain.model import Context, State
from grid.exchange.order_audit import market, refuse, refuse_async
from grid.execution.client_ids import MappedSyntheticExchange
from grid.execution.executor import MockExecutor
from grid.persistence.records import Bounds, ExecutionPosition
from grid.persistence.writer import AsyncWriter

from .helpers import EVIDENCE, LIMITS, ZERO_FEES
from .test_unified import mapped

pytestmark = pytest.mark.usefixtures("async_sockets")


def fill(
    venue: MappedSyntheticExchange,
    order: str,
    trade: str,
    amount: str,
    price: str,
    timestamp: object,
) -> None:
    fact = venue.fill(order, trade, D(amount), D(price), ZERO_FEES)
    venue.trades[-1] = replace(fact, sequence=None)
    venue.wire_trades[trade]["timestamp"] = timestamp


def test_pinned_ccxt_trade_timestamps_post_in_order_without_fake_sequences(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
) -> None:
    async def run() -> None:
        parser_type = type(
            "OfflineOKX",
            (ccxtpro.okx,),
            {
                "request": refuse_async,
                "fetch": refuse_async,
                "fetch2": refuse_async,
                "sign": refuse,
            },
        )
        parser = parser_type({"number": str})
        try:
            parser.set_markets([market("okx")])
            async with mapped(tmp_path / "ledger.db", bounds, state, context) as (h, venue):
                assert await h.service() and await h.send()
                for trade, amount, stamp in (
                    ("z-first", ".4", 1700000000000),
                    ("a-second", ".6", 1700000000001),
                ):
                    fill(venue, "order-1", trade, amount, "100", stamp)
                    venue.wire_trades[trade] = parser.parse_trade(
                        {
                            "instId": "BASEQUOTE",
                            "ordId": "order-1",
                            "tradeId": trade,
                            "side": "buy",
                            "fillPx": "100",
                            "fillSz": amount,
                            "fee": "0",
                            "feeCcy": "QUOTE",
                            "ts": str(stamp),
                        },
                        parser.market("BASE/QUOTE"),
                    )
                venue.trades.reverse()  # Page order and lexicographic IDs disagree with time.
                assert await h.service(), h.executor.last_error
                positions = await h.writer.submit(
                    lambda ledger: tuple(
                        ledger.db.execute(
                            "SELECT t.external_id,p.kind,p.value FROM execution_positions p "
                            "JOIN trade_keys t USING (allocation,trade_id) ORDER BY p.value"
                        )
                    )
                )
                assert positions == (
                    ("z-first", "timestamp", 1700000000000),
                    ("a-second", "timestamp", 1700000000001),
                )
                assert (await h.current()).quote_cash == 900
                for raw in venue.wire_trades.values():
                    raw["timestamp"] = None  # A replay may omit known optional metadata.
                assert await h.service(), h.executor.last_error
                assert await h.writer.submit(
                    lambda ledger: ledger.execution_position(h.scope)
                ) == ExecutionPosition("timestamp", 1700000000001)
        finally:
            await parser.close()

    asyncio.run(run())


@pytest.mark.parametrize("sell_first", [True, False])
def test_mixed_buy_sell_recovery_uses_execution_time_for_cost_basis(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    sell_first: bool,
) -> None:
    async def run() -> None:
        async with mapped(tmp_path / "ledger.db", bounds, state, context) as (h, venue):
            assert await h.service() and await h.send()
            fill(venue, "order-1", "seed", "1", "100", 100)
            assert await h.service()
            for _ in range(2):
                assert await h.send() and await h.service()
            buy = venue.sent[2]
            fill(venue, "order-2", "sell", "1", "110", 200 if sell_first else 201)
            fill(venue, "order-3", "buy", buy.amount, buy.price, 201 if sell_first else 200)
            venue.trades.reverse()
            assert await h.service(), h.executor.last_error
            result = await h.current()
            assert result.owned_base == 1
            assert result.realized_pnl == (D(10) if sell_first else D("14.55"))
            assert result.position_cost == (D("90.9") if sell_first else D("95.45"))
            assert result.quote_cash + result.position_cost == 1000 + result.realized_pnl

    asyncio.run(run())


@pytest.mark.parametrize("stamp", [None, 100, 0, -1, True, "101", 101.0])
def test_ambiguous_or_malformed_new_times_block_the_whole_batch(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    stamp: object,
) -> None:
    async def run() -> None:
        async with mapped(tmp_path / "ledger.db", bounds, state, context) as (h, venue):
            assert await h.service() and await h.send()
            fill(venue, "order-1", "one", ".4", "100", 100)
            fill(venue, "order-1", "two", ".6", "100", stamp)
            assert not await h.service() and not await h.send()
            assert (await h.current()).quote_cash == 1000
            assert await h.writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
            ) == (0,)
            assert await h.writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM order_bindings").fetchone()
            ) == (0,)

    asyncio.run(run())


@pytest.mark.parametrize("damage", ["earlier", "equal", "changed_kind"])
def test_restart_retains_the_execution_frontier_and_allows_a_corrected_new_fact(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    damage: str,
) -> None:
    async def run() -> None:
        path = tmp_path / "ledger.db"
        async with mapped(path, bounds, state, context) as (h, venue):
            assert await h.service() and await h.send()
            fill(venue, "order-1", "first", ".4", "100", 100)
            assert await h.service()
            await h.executor.close()
            await h.writer.close()
            writer = await AsyncWriter.open(path, bounds, SOURCES, mock_storage=True)
            venue.writer = writer
            recovered = MockExecutor(writer, venue, (h.scope,), LIMITS, EVIDENCE)
            try:
                fill(venue, "order-1", "new", ".6", "100", 99 if damage == "earlier" else 100)
                if damage == "changed_kind":
                    venue.trades[-1] = replace(venue.trades[-1], sequence=101)
                assert not await recovered.service(h.scope, context)
                assert not await recovered.dispatch(h.scope, context)
                snapshot = await writer.submit(lambda ledger: ledger.snapshot(h.scope))
                assert snapshot.state is not None and snapshot.state.owned_base == D(".4")
                assert await writer.submit(
                    lambda ledger: ledger.execution_position(h.scope)
                ) == ExecutionPosition("timestamp", 100)
                venue.trades[-1] = replace(venue.trades[-1], sequence=None)
                venue.wire_trades["new"]["timestamp"] = 101
                h.advance()
                assert await recovered.service(h.scope, h.context), recovered.last_error
                assert await writer.submit(
                    lambda ledger: ledger.execution_position(h.scope)
                ) == ExecutionPosition("timestamp", 101)
                assert await writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
                ) == (2,)
            finally:
                await recovered.close()
                await writer.close()

    asyncio.run(run())


@pytest.mark.parametrize("damage", ["changed_replay", "deleted_positions"])
def test_ordering_history_cannot_be_rewritten_or_silently_lost(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    damage: str,
) -> None:
    async def run() -> None:
        async with mapped(tmp_path / "ledger.db", bounds, state, context) as (h, venue):
            assert await h.service() and await h.send()
            fill(venue, "order-1", "old", ".4", "100", 100)
            assert await h.service()
            fill(venue, "order-1", "new", ".6", "100", 102)
            if damage == "changed_replay":
                venue.wire_trades["old"]["timestamp"] = 101
            else:
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("DELETE FROM execution_positions")
                )
            assert not await h.service() and not await h.send()
            assert (await h.current()).owned_base == D(".4")

    asyncio.run(run())
