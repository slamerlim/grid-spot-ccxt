"""CCXT unified observations reach the real mock ledger without exchange access."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal as D
from functools import partial
from pathlib import Path

import ccxt.pro as ccxtpro  # type: ignore[import-untyped]
import pytest
from persistence.conftest import SOURCES

from grid.domain.market import FeeRule
from grid.domain.model import ActualFee, Context, State
from grid.exchange.order_audit import market, refuse, refuse_async
from grid.execution.client_ids import MappedSyntheticExchange, normalize
from grid.execution.executor import MockExecutor
from grid.execution.models import ExecutionError
from grid.persistence.records import Bounds, ClientIdPolicy
from grid.persistence.writer import AsyncWriter

from .helpers import EVIDENCE, LIMITS, ZERO_FEES, Harness, harness

pytestmark = pytest.mark.usefixtures("async_sockets")
POLICY = ClientIdPolicy("unified-mock", "")


@asynccontextmanager
async def mapped(
    path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
) -> AsyncIterator[tuple[Harness, MappedSyntheticExchange]]:
    async with harness(path, bounds, state, context) as h:
        await h.executor.close()
        venue = MappedSyntheticExchange(100, h.writer, POLICY)
        h.venue = venue
        h.executor = MockExecutor(
            h.writer, venue, (h.scope,), replace(LIMITS, page_size=1), EVIDENCE
        )
        try:
            yield h, venue
        finally:
            await h.executor.close()


@pytest.mark.parametrize(
    "currency,charge,quote_value,cash,base,cost,token_balance",
    [
        ("QUOTE", "1", None, "899", "1", "101", "10"),
        ("BASE", ".01", None, "900", ".99", "100", "10"),
        ("TOKEN", ".05", "1", "900", "1", "101", "9.95"),
        ("QUOTE", "-.1", None, "900.1", "1", "99.9", "10"),
        ("QUOTE", "0", None, "900", "1", "100", "10"),
        ("TOKEN", "0", None, "900", "1", "100", "10"),
    ],
)
def test_pinned_ccxt_fees_post_once_through_unified_pages_and_restart(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    currency: str,
    charge: str,
    quote_value: str | None,
    cash: str,
    base: str,
    cost: str,
    token_balance: str,
) -> None:
    async def run() -> None:
        fixture_context = replace(
            context, buy_fee=FeeRule(currency, D(".01"), D(".0001"), D(20), 64)
        )
        actual = ActualFee(currency, D(charge), None if quote_value is None else D(quote_value))
        parser_class = type(
            "OfflineOKX",
            (ccxtpro.okx,),
            {
                "request": refuse_async,
                "fetch": refuse_async,
                "fetch2": refuse_async,
                "sign": refuse,
            },
        )
        parser = parser_class({"number": str})
        try:
            parser.set_markets([market("okx")])
            path = tmp_path / "ledger.db"
            async with mapped(path, bounds, state, fixture_context) as (h, venue):
                assert await h.service() and await h.send(), h.executor.last_error
                client_id = venue.wire_orders["order-1"]["clientOrderId"]
                venue.fill("order-1", "fill-1", D(1), D(100), (actual,))
                raw = {
                    "instId": "BASEQUOTE",
                    "instType": "SPOT",
                    "tdMode": "cash",
                    "ordId": "order-1",
                    "clOrdId": client_id,
                    "tradeId": "fill-1",
                    "side": "buy",
                    "ordType": "limit",
                    "px": "100",
                    "sz": "1",
                    "accFillSz": "1",
                    "avgPx": "100",
                    "fillPx": "100",
                    "fillSz": "1",
                    "state": "filled",
                    "fee": str(-D(charge)),
                    "feeCcy": currency,
                    "cTime": "1700000000000",
                    "uTime": "1700000000001",
                    "ts": "1700000000001",
                }
                fixture_market = parser.market("BASE/QUOTE")
                venue.wire_orders["order-1"] = parser.parse_order(raw, fixture_market)
                venue.wire_trades["fill-1"] = parser.parse_trade(raw, fixture_market)
                assert venue.wire_trades["fill-1"]["type"] is None
                for _ in range(3):
                    assert await h.service(), h.executor.last_error
                result = await h.current()
                assert (result.quote_cash, result.owned_base, result.position_cost) == (
                    D(cash),
                    D(base),
                    D(cost),
                )
                assert next(m.amount for m in result.fee_balances if m.currency == "TOKEN") == D(
                    token_balance
                )
                assert await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
                ) == (1,)
            writer = await AsyncWriter.open(path, bounds, SOURCES, mock_storage=True)
            venue.writer = writer
            recovered = MockExecutor(
                writer, venue, (state.scope,), replace(LIMITS, page_size=1), EVIDENCE
            )
            try:
                assert await recovered.service(state.scope, fixture_context), recovered.last_error
                after = await writer.submit(lambda ledger: ledger.snapshot(state.scope))
                assert after.state is not None
                assert (
                    after.state.quote_cash,
                    after.state.owned_base,
                    after.state.position_cost,
                ) == (D(cash), D(base), D(cost))
                assert await writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
                ) == (1,)
            finally:
                await recovered.close()
                await writer.close()
        finally:
            await parser.close()

    asyncio.run(run())


def test_missing_optional_terms_use_intent_but_missing_identity_needs_prior_binding(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
) -> None:
    async def run() -> None:
        async with mapped(tmp_path / "ledger.db", bounds, state, context) as (h, venue):
            assert await h.service() and await h.send()
            full = venue.wire_orders["order-1"]
            minimal = {k: full[k] for k in ("id", "symbol", "status", "filled", "clientOrderId")}
            fact = await h.writer.submit(lambda ledger: normalize(ledger, h.scope, minimal))
            assert fact.request == venue.sent[0]
            anonymous = {k: v for k, v in minimal.items() if k != "clientOrderId"}
            with pytest.raises(ExecutionError):
                await h.writer.submit(lambda ledger: normalize(ledger, h.scope, anonymous))
            assert await h.service()  # The complete owned report commits its binding.
            venue.wire_orders["order-1"] = anonymous
            assert await h.service()
            for changed in (anonymous | {"clientOrderId": "foreign"}, anonymous | {"id": "other"}):
                with pytest.raises(ExecutionError):
                    await h.writer.submit(partial(normalize, scope=h.scope, wire=changed))
            with pytest.raises(ExecutionError):
                await h.writer.submit(
                    lambda ledger: normalize(ledger, replace(h.scope, account="other"), anonymous)
                )
            expired = await h.writer.submit(
                lambda ledger: normalize(ledger, h.scope, anonymous | {"status": "expired"})
            )
            assert expired.status == "canceled" and expired.cumulative == 0
            assert (await h.current()).owned_base == 0

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault",
    [
        "missing_status",
        "missing_filled",
        "remaining",
        "cumulative",
        "foreign_order",
        "missing_order",
        "market_trade",
        "wrong_side",
        "above_limit",
        "cost",
        "float_amount",
        "missing_fees",
        "empty_fees",
        "partial_fee",
        "missing_fx",
        "changed_fee",
        "changed_trade_id",
    ],
)
def test_unified_snapshot_refuses_incomplete_or_conflicting_money_before_binding(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    fault: str,
) -> None:
    async def run() -> None:
        async with mapped(tmp_path / "ledger.db", bounds, state, context) as (h, venue):
            assert await h.service() and await h.send()
            fees = (ActualFee("TOKEN", D(".01"), None),) if fault == "missing_fx" else ZERO_FEES
            venue.fill("order-1", "trade-1", D(1), D(100), fees)
            order_changes: dict[str, dict[str, object]] = {
                "missing_status": {"status": None},
                "missing_filled": {"filled": None},
                "remaining": {"remaining": "1"},
                "cumulative": {"filled": ".5", "remaining": ".5", "status": "open"},
            }
            trade_changes: dict[str, dict[str, object]] = {
                "foreign_order": {"order": "foreign"},
                "missing_order": {"order": None},
                "market_trade": {"type": "market"},
                "wrong_side": {"side": "sell"},
                "above_limit": {"price": "101", "cost": "101"},
                "cost": {"cost": "99"},
                "float_amount": {"amount": 1.0},
                "missing_fees": {"fees": None},
                "empty_fees": {"fees": []},
                "partial_fee": {"fees": [{"currency": "QUOTE", "cost": None}]},
                "changed_fee": {"fees": [{"currency": "QUOTE", "cost": "1"}]},
                "changed_trade_id": {"id": "different-trade"},
            }
            venue.wire_orders["order-1"].update(order_changes.get(fault, {}))
            venue.wire_trades["trade-1"].update(trade_changes.get(fault, {}))
            assert not await h.service() and not await h.send()
            assert await h.writer.submit(lambda ledger: ledger.binding(h.scope, 1)) is None
            assert await h.writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
            ) == (0,)
            after = await h.current()
            assert after.owned_base == 0 and after.quote_cash == 1000

    asyncio.run(run())


def test_reordered_and_duplicate_unified_trades_keep_explicit_mock_chronology(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
) -> None:
    async def run() -> None:
        async with mapped(tmp_path / "ledger.db", bounds, state, context) as (h, venue):
            assert await h.service() and await h.send()
            first = venue.fill("order-1", "part-1", D(".4"), D(100), ZERO_FEES)
            venue.fill("order-1", "part-2", D(".6"), D(100), ZERO_FEES)
            venue.trades.reverse()
            venue.trades.append(first)
            # The singular field is a convenience copy, not a second fee.
            for raw in venue.wire_trades.values():
                raw["fee"] = {"currency": "QUOTE", "cost": "0"}
                raw.pop("side")  # Already proved by the referenced owned order.
                raw.pop("type")
            for _ in range(3):
                assert await h.service(), h.executor.last_error
            after = await h.current()
            assert after.owned_base == 1 and after.quote_cash == 900 and after.position_cost == 100
            assert await h.writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
            ) == (2,)

    asyncio.run(run())
