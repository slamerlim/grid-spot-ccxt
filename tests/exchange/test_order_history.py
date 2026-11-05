"""Bounded unified order reads, including real pinned CCXT with offline replies."""

import asyncio
from copy import deepcopy
from decimal import Decimal, localcontext
from typing import Any

import ccxt.pro as ccxtpro  # type: ignore[import-untyped]
import pytest

from grid.exchange.models import Failure, ReadError
from grid.exchange.order_audit import MARKET_ID, SYMBOL, market, refuse, refuse_async
from grid.exchange.private import read_order_history

pytestmark = pytest.mark.usefixtures("async_sockets")


def order() -> dict[str, object]:
    return {"id": "owned-1", "symbol": SYMBOL, "status": "closed", "filled": "1"}


def trade() -> dict[str, object]:
    return {
        "id": "fill-1",
        "order": "owned-1",
        "symbol": SYMBOL,
        "side": "buy",
        "amount": "1",
        "price": "100.12345678901234567890123456789",
        "fee": {"currency": "QUOTE", "cost": "0"},
    }


class Replies:
    def __init__(self) -> None:
        self.before: object = order()
        self.after: object = order()
        self.rows: object = [trade()]
        self.calls: list[tuple[object, ...]] = []

    async def fetch_order(self, id: str, symbol: str) -> object:
        self.calls.append(("order", id, symbol))
        return deepcopy(self.before if len(self.calls) == 1 else self.after)

    async def fetch_order_trades(self, id: str, symbol: str, since: None, limit: int) -> object:
        self.calls.append(("trades", id, symbol, since, limit))
        return deepcopy(self.rows)


@pytest.mark.parametrize("case", ["normal", "optional", "duplicates", "zero", "exact_sum"])
def test_quantity_matched_history_preserves_unknowns_and_exact_values(case: str) -> None:
    async def run() -> None:
        source = Replies()
        if case == "optional":
            source.before = order() | {"side": "buy", "price": "100"}
            source.rows = [trade() | {"side": None, "fee": None}]
        elif case == "duplicates":
            source.rows = [trade(), trade()]
        elif case == "zero":
            source.before = source.after = order() | {"status": "open", "filled": "0"}
            source.rows = []
        elif case == "exact_sum":
            source.rows = [
                trade() | {"amount": "0.12345678901234567890123456789"},
                trade() | {"id": "fill-2", "amount": "0.87654321098765432109876543211"},
            ]
        with localcontext() as ambient:
            ambient.prec = 3
            result = await read_order_history(
                source, "owned-1", SYMBOL, max_trades=2, timeout_ms=1000
            )
            assert ambient.prec == 3
        assert source.calls == [
            ("order", "owned-1", SYMBOL),
            ("trades", "owned-1", SYMBOL, None, 2),
            ("order", "owned-1", SYMBOL),
        ]
        assert result.order.filled == (0 if case == "zero" else 1)
        assert len(result.trades) == (0 if case == "zero" else 2 if case == "exact_sum" else 1)
        if result.trades:
            assert result.trades[0].price == Decimal("100.12345678901234567890123456789")
        if case == "optional":
            assert result.order.side is None and result.order.price is None
            assert result.trades[0].fees is None and result.trades[0].side is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "case,failure,calls",
    [
        ("first_id", Failure.INCOMPLETE, 1),
        ("last_id", Failure.INCOMPLETE, 3),
        ("missing_status", Failure.INCOMPLETE, 1),
        ("missing_cumulative", Failure.INCOMPLETE, 1),
        ("changed_status", Failure.INCOMPLETE, 3),
        ("changed_cumulative", Failure.INCOMPLETE, 3),
        ("changed_terms", Failure.INCOMPLETE, 3),
        ("short_history", Failure.INCOMPLETE, 3),
        ("over_total", Failure.INCOMPLETE, 3),
        ("duplicate_hides_gap", Failure.INCOMPLETE, 3),
        ("conflict", Failure.MALFORMED, 3),
        ("foreign_order", Failure.MALFORMED, 3),
        ("foreign_symbol", Failure.MALFORMED, 2),
        ("wrong_side", Failure.MALFORMED, 3),
        ("float", Failure.MALFORMED, 2),
        ("oversize", Failure.BUDGET, 2),
    ],
)
def test_incomplete_changed_or_conflicting_reads_produce_no_result(
    case: str, failure: Failure, calls: int
) -> None:
    async def run() -> None:
        source = Replies()
        match case:
            case "first_id":
                source.before = order() | {"id": "foreign"}
            case "last_id":
                source.after = order() | {"id": "foreign"}
            case "missing_status":
                source.before = order() | {"status": None}
            case "missing_cumulative":
                source.before = order() | {"filled": None}
            case "changed_status":
                source.after = order() | {"status": "open"}
            case "changed_cumulative":
                source.after = order() | {"filled": "2"}
            case "changed_terms":
                source.before = order() | {"price": "100"}
                source.after = order() | {"price": "101"}
            case "short_history":
                source.rows = []
            case "over_total":
                source.rows = [trade() | {"amount": "2"}]
            case "duplicate_hides_gap":
                source.rows = [trade() | {"amount": "0.5"}] * 2
            case "conflict":
                source.rows = [trade(), trade() | {"price": "99"}]
            case "foreign_order":
                source.rows = [trade() | {"order": "foreign"}]
            case "foreign_symbol":
                source.rows = [trade() | {"symbol": "OTHER/QUOTE"}]
            case "wrong_side":
                source.after = order() | {"side": "sell"}
            case "float":
                source.rows = [trade() | {"amount": 1.0}]
            case "oversize":
                source.rows = [trade()] * 3
        with pytest.raises(ReadError) as caught:
            await read_order_history(source, "owned-1", SYMBOL, max_trades=2, timeout_ms=1000)
        assert caught.value.failure is failure and len(source.calls) == calls

    asyncio.run(run())


@pytest.mark.parametrize("stage", [1, 2, 3])
@pytest.mark.parametrize("failure", ["timeout", "cancel", "error"])
def test_interrupted_read_never_retries_or_swallows_cancellation(
    stage: int, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        source = Replies()
        entered = asyncio.Event()
        original_order = source.fetch_order
        original_trades = source.fetch_order_trades

        async def stop() -> None:
            if len(source.calls) == stage:
                entered.set()
                if failure == "error":
                    raise RuntimeError("private response must not reach the caller")
                await asyncio.Event().wait()

        async def fetch_order(id: str, symbol: str) -> object:
            result = await original_order(id, symbol)
            await stop()
            return result

        async def fetch_trades(id: str, symbol: str, since: None, limit: int) -> object:
            result = await original_trades(id, symbol, since, limit)
            await stop()
            return result

        monkeypatch.setattr(source, "fetch_order", fetch_order)
        monkeypatch.setattr(source, "fetch_order_trades", fetch_trades)
        task = asyncio.create_task(
            read_order_history(source, "owned-1", SYMBOL, max_trades=2, timeout_ms=20)
        )
        await entered.wait()
        if failure == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ReadError) as caught:
                await task
            expected = Failure.TIMEOUT if failure == "timeout" else Failure.UNAVAILABLE
            assert caught.value.failure is expected
            assert str(caught.value) == expected.value
        assert len(source.calls) == stage

    asyncio.run(run())


@pytest.mark.parametrize("max_trades,timeout", [(0, 1000), (101, 1000), (1, 0), (1, 30001)])
def test_invalid_bounds_make_no_calls(max_trades: int, timeout: int) -> None:
    async def run() -> None:
        source = Replies()
        with pytest.raises(ReadError):
            await read_order_history(
                source, "owned-1", SYMBOL, max_trades=max_trades, timeout_ms=timeout
            )
        assert source.calls == []

    asyncio.run(run())


@pytest.mark.parametrize("short_history", [False, True])
def test_actual_okx_unified_methods_use_order_filter_and_bounded_calls(short_history: bool) -> None:
    async def run() -> None:
        calls: list[dict[str, object]] = []
        price = "100.12345678901234567890123456789"
        raw_order = {
            "instId": MARKET_ID,
            "instType": "SPOT",
            "tdMode": "cash",
            "ordId": "owned-1",
            "ordType": "limit",
            "side": "buy",
            "px": price,
            "sz": "1",
            "accFillSz": "1",
            "avgPx": price,
            "state": "filled",
        }
        raw_trades = [
            {
                "instId": MARKET_ID,
                "ordId": "owned-1",
                "tradeId": identity,
                "side": "buy",
                "fillSz": amount,
                "fillPx": price,
                "fee": "-0.01",
                "feeCcy": "QUOTE",
                "ts": "1000",
            }
            for identity, amount in (("later-id", "0.75"), ("earlier-id", "0.25"))
        ]

        async def request(
            self: Any,
            path: str,
            api: object = None,
            method: str = "GET",
            params: object = None,
            headers: object = None,
            body: object = None,
            config: object = None,
        ) -> object:
            assert len(calls) < 3
            assert api == ["private"] and method == "GET" and headers is None and body is None
            calls.append({"path": path, "params": deepcopy(params)})
            if len(calls) in (1, 3):
                assert path == "trade/order"
                assert params == {"instId": MARKET_ID, "ordId": "owned-1"}
                return {"code": "0", "data": [deepcopy(raw_order)]}
            assert path == "trade/fills-history"
            assert params == {
                "instId": MARKET_ID,
                "instType": "SPOT",
                "ordId": "owned-1",
                "limit": 2,
            }
            return {"code": "0", "data": deepcopy(raw_trades[:1] if short_history else raw_trades)}

        client_class = type(
            "OfflineOrderReads",
            (ccxtpro.okx,),
            {"request": request, "fetch": refuse_async, "fetch2": refuse_async, "sign": refuse},
        )
        client = client_class({"number": str, "options": {"defaultType": "spot"}})
        try:
            client.set_markets([market("okx")])
            if short_history:
                with pytest.raises(ReadError) as caught:
                    await read_order_history(
                        client, "owned-1", SYMBOL, max_trades=2, timeout_ms=1000
                    )
                assert caught.value.failure is Failure.INCOMPLETE
            else:
                result = await read_order_history(
                    client, "owned-1", SYMBOL, max_trades=2, timeout_ms=1000
                )
                assert result.order.status == "closed" and result.order.filled == 1
                assert result.order.price == Decimal(price)
                assert {t.trade_id for t in result.trades} == {"earlier-id", "later-id"}
                assert all(t.price == Decimal(price) for t in result.trades)
                assert all(t.fees == (("QUOTE", Decimal("0.01")),) for t in result.trades)
            assert len(calls) == 3
        finally:
            await client.close()

    asyncio.run(run())
