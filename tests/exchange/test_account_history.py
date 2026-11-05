"""One bounded unified personal-trade query covers multiple known order IDs."""

import asyncio
from copy import deepcopy
from decimal import Decimal, localcontext
from typing import Any

import ccxt.pro as ccxtpro  # type: ignore[import-untyped]
import pytest

from grid.exchange.models import Failure, ReadError
from grid.exchange.order_audit import MARKET_ID, SYMBOL, market, refuse, refuse_async
from grid.exchange.private import read_account_history, read_order_candidates

from .test_order_history import order, trade

pytestmark = pytest.mark.usefixtures("async_sockets")
IDS = ("owned-1", "owned-2")


class Replies:
    def __init__(self) -> None:
        self.before = {
            IDS[0]: order(),
            IDS[1]: order() | {"id": IDS[1], "status": "open", "filled": "0.5"},
        }
        self.after = deepcopy(self.before)
        self.rows: object = [
            trade(),
            trade() | {"id": "fill-2", "order": IDS[1], "amount": "0.5"},
            trade() | {"id": "other-fill", "order": "other-order", "fee": None},
        ]
        self.calls: list[tuple[object, ...]] = []

    async def fetch_order(self, id: str, symbol: str) -> object:
        first = ("order", id, symbol) not in self.calls
        self.calls.append(("order", id, symbol))
        return deepcopy((self.before if first else self.after)[id])

    async def fetch_my_trades(self, symbol: str, since: None, limit: int) -> object:
        self.calls.append(("trades", symbol, since, limit))
        return deepcopy(self.rows)


@pytest.mark.parametrize(
    "case", ["normal", "duplicates", "unknowns", "unattributed", "exact_sum", "zero"]
)
def test_one_page_matches_each_order_and_retains_unmatched_observations(case: str) -> None:
    async def run() -> None:
        source = Replies()
        if case == "duplicates":
            assert isinstance(source.rows, list)
            source.rows += deepcopy(source.rows)
        elif case == "unknowns":
            source.before[IDS[0]]["side"] = "buy"
            source.rows = [
                trade() | {"side": None, "fee": None},
                trade() | {"id": "fill-2", "order": IDS[1], "amount": "0.5"},
            ]
        elif case == "exact_sum":
            assert isinstance(source.rows, list)
            source.rows[0] = trade() | {"amount": "0.12345678901234567890123456789"}
            source.rows.append(
                trade() | {"id": "remainder", "amount": "0.87654321098765432109876543211"}
            )
        elif case == "unattributed":
            assert isinstance(source.rows, list)
            source.rows[2]["order"] = None
        elif case == "zero":
            for row in (*source.before.values(), *source.after.values()):
                row.update(filled="0", status="open")
            source.rows = []
        with localcontext() as ambient:
            ambient.prec = 3
            result = await read_account_history(source, IDS, SYMBOL, max_trades=6, timeout_ms=1000)
            assert ambient.prec == 3
        assert source.calls == [
            ("order", IDS[0], SYMBOL),
            ("order", IDS[1], SYMBOL),
            ("trades", SYMBOL, None, 6),
            ("order", IDS[0], SYMBOL),
            ("order", IDS[1], SYMBOL),
        ]
        assert tuple(h.order.order_id for h in result.histories) == IDS
        assert tuple(h.order.filled for h in result.histories) == (
            (Decimal(0), Decimal(0)) if case == "zero" else (Decimal(1), Decimal("0.5"))
        )
        if case not in ("zero", "unknowns"):
            assert len(result.unmatched_trades) == 1
            assert result.unmatched_trades[0].order_id == (
                None if case == "unattributed" else "other-order"
            )
            assert result.unmatched_trades[0].fees is None
        else:
            assert result.unmatched_trades == ()
        if case != "zero":
            assert result.histories[0].trades[0].price == Decimal(
                "100.12345678901234567890123456789"
            )
        if case == "unknowns":
            assert result.histories[0].order.side is None
            assert result.histories[0].trades[0].fees is None
        if case == "duplicates":
            assert tuple(len(h.trades) for h in result.histories) == (1, 1)

    asyncio.run(run())


@pytest.mark.parametrize(
    "case,failure",
    [
        ("first_id", Failure.INCOMPLETE),
        ("last_id", Failure.INCOMPLETE),
        ("missing_status", Failure.INCOMPLETE),
        ("missing_cumulative", Failure.INCOMPLETE),
        ("changed_status", Failure.INCOMPLETE),
        ("changed_cumulative", Failure.INCOMPLETE),
        ("short_history", Failure.INCOMPLETE),
        ("duplicate_gap", Failure.INCOMPLETE),
        ("foreign_conflict", Failure.MALFORMED),
        ("cross_order_conflict", Failure.MALFORMED),
        ("foreign_symbol", Failure.MALFORMED),
        ("missing_target_order", Failure.INCOMPLETE),
        ("foreign_float", Failure.MALFORMED),
        ("oversize", Failure.BUDGET),
    ],
)
def test_incomplete_or_conflicting_batch_returns_no_partial_result(
    case: str, failure: Failure
) -> None:
    async def run() -> None:
        source = Replies()
        assert isinstance(source.rows, list)
        match case:
            case "first_id":
                source.before[IDS[1]]["id"] = "wrong"
            case "last_id":
                source.after[IDS[1]]["id"] = "wrong"
            case "missing_status":
                source.before[IDS[1]]["status"] = None
            case "missing_cumulative":
                source.before[IDS[1]]["filled"] = None
            case "changed_status":
                source.after[IDS[1]]["status"] = "canceled"
            case "changed_cumulative":
                source.after[IDS[1]]["filled"] = "0.75"
            case "short_history":
                source.rows.pop(1)
            case "duplicate_gap":
                source.rows[1] = trade() | {"id": "fill-2", "order": IDS[1], "amount": "0.25"}
                source.rows.append(deepcopy(source.rows[1]))
            case "foreign_conflict":
                source.rows.append(source.rows[2] | {"price": "99"})
            case "cross_order_conflict":
                source.rows[1]["id"] = "fill-1"
            case "foreign_symbol":
                source.rows[2]["symbol"] = "OTHER/QUOTE"
            case "missing_target_order":
                source.rows[1]["order"] = None
            case "foreign_float":
                source.rows[2]["price"] = 100.0
            case "oversize":
                source.rows *= 3
        with pytest.raises(ReadError) as caught:
            await read_account_history(source, IDS, SYMBOL, max_trades=4, timeout_ms=1000)
        assert caught.value.failure is failure
        assert len(source.calls) <= 5
        assert sum(call[0] == "trades" for call in source.calls) <= 1

    asyncio.run(run())


@pytest.mark.parametrize("stage", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("failure", ["timeout", "cancel", "error"])
def test_each_interrupted_batch_stage_preserves_cancellation_and_never_retries(
    stage: int, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        source = Replies()
        entered = asyncio.Event()
        original_order, original_trades = source.fetch_order, source.fetch_my_trades

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

        async def fetch_trades(symbol: str, since: None, limit: int) -> object:
            result = await original_trades(symbol, since, limit)
            await stop()
            return result

        monkeypatch.setattr(source, "fetch_order", fetch_order)
        monkeypatch.setattr(source, "fetch_my_trades", fetch_trades)
        task = asyncio.create_task(
            read_account_history(source, IDS, SYMBOL, max_trades=4, timeout_ms=30)
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
            assert caught.value.failure is expected and str(caught.value) == expected.value
        assert len(source.calls) == stage

    asyncio.run(run())


@pytest.mark.parametrize(
    "ids,maximum,timeout",
    [
        ((), 1, 1000),
        (IDS * 51, 1, 1000),
        (IDS * 2, 1, 1000),
        (("",), 1, 1000),
        (IDS, 0, 1000),
        (IDS, 101, 1000),
        (IDS, 1, 0),
        (IDS, 1, 30001),
    ],
)
def test_invalid_bounds_or_id_rosters_make_no_calls(
    ids: tuple[str, ...], maximum: int, timeout: int
) -> None:
    async def run() -> None:
        source = Replies()
        with pytest.raises(ReadError):
            await read_account_history(source, ids, SYMBOL, max_trades=maximum, timeout_ms=timeout)
        assert source.calls == []

    asyncio.run(run())


@pytest.mark.parametrize("venue", ["xt", "bingx"])
@pytest.mark.parametrize("short_history", [False, True])
def test_pinned_sdk_fallback_reads_spot_orders_and_personal_trades(
    venue: str, short_history: bool
) -> None:
    async def run() -> None:
        calls: list[tuple[str, object]] = []
        price = "100.12345678901234567890123456789"
        raw_orders = {
            identity: {
                "symbol": MARKET_ID,
                "orderId": identity,
                "type": "LIMIT",
                "side": "BUY",
                "price": price,
                "origQty": "1",
                "executedQty": "1",
                "leavingQty": "0",
                "state": "FILLED",
                "status": "FILLED",
                "avgPrice": price,
                "time": 1000,
                "updatedTime": 1000,
            }
            for identity in IDS
        }
        raw_trades = [
            (
                {
                    "symbol": MARKET_ID,
                    "tradeId": identity,
                    "orderId": order_id,
                    "orderSide": "BUY",
                    "orderType": "LIMIT",
                    "bizType": "SPOT",
                    "time": 1000,
                    "price": price,
                    "quantity": "1",
                    "fee": "0.01",
                    "feeCurrency": "QUOTE",
                }
                if venue == "xt"
                else {
                    "symbol": MARKET_ID,
                    "id": identity,
                    "orderId": order_id,
                    "price": price,
                    "qty": "1",
                    "commission": "-0.01",
                    "commissionAsset": "QUOTE",
                    "time": 1000,
                    "isBuyer": True,
                    "isMaker": False,
                }
            )
            for identity, order_id in (("f1", IDS[0]), ("f2", IDS[1]), ("f3", "other"))
            if not short_history or order_id != IDS[1]
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
            assert len(calls) < 5 and method == "GET" and headers is None and body is None
            calls.append((path, deepcopy(params)))
            assert api == (["private", "spot"] if venue == "xt" else ["spot", "v1", "private"])
            if len(calls) != 3:
                identity = IDS[(len(calls) - 1) % 3]
                expected = {"orderId": identity}
                if venue == "bingx":
                    expected["symbol"] = MARKET_ID
                assert path == ("order/{orderId}" if venue == "xt" else "trade/query")
                assert params == expected
                return {"result" if venue == "xt" else "data": deepcopy(raw_orders[identity])}
            expected_trade: dict[str, object] = {"symbol": MARKET_ID, "limit": 3}
            if venue == "xt":
                expected_trade["bizType"] = "SPOT"
            assert path == ("trade" if venue == "xt" else "trade/myTrades")
            assert params == expected_trade
            return (
                {"result": {"items": deepcopy(raw_trades), "hasNext": False}}
                if venue == "xt"
                else {"data": {"fills": deepcopy(raw_trades)}}
            )

        client_class = type(
            "OfflineAccountReads",
            (getattr(ccxtpro, venue),),
            {
                "request": request,
                "fetch": refuse_async,
                "fetch2": refuse_async,
                "sign": refuse,
                "fetch_order_trades": refuse_async,
            },
        )
        client = client_class({"number": str, "options": {"defaultType": "spot"}})
        try:
            client.set_markets([market(venue) | {"swap": False, "linear": False}])
            assert not client.has.get("fetchOrderTrades")
            if short_history:
                with pytest.raises(ReadError) as caught:
                    await read_account_history(client, IDS, SYMBOL, max_trades=3, timeout_ms=1000)
                assert caught.value.failure is Failure.INCOMPLETE
            else:
                result = await read_account_history(
                    client, IDS, SYMBOL, max_trades=3, timeout_ms=1000
                )
                assert tuple(h.order.order_id for h in result.histories) == IDS
                assert all(
                    h.order.status == "closed" and h.order.filled == 1 for h in result.histories
                )
                observed = (
                    tuple(t for h in result.histories for t in h.trades) + result.unmatched_trades
                )
                assert len(observed) == 3 and len(result.unmatched_trades) == 1
                assert result.unmatched_trades[0].order_id == "other"
                assert all(t.price == Decimal(price) for t in observed)
                assert all(t.fees == (("QUOTE", Decimal("0.01")),) for t in observed)
            assert len(calls) == 5
        finally:
            await client.close()

    asyncio.run(run())


def test_pinned_okx_open_and_closed_queries_preserve_unified_client_identity() -> None:
    async def run() -> None:
        calls: list[str] = []

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
            assert api == ["private"] and method == "GET" and headers is None and body is None
            assert path in ("trade/orders-pending", "trade/orders-history")
            expected: dict[str, object] = {"instId": MARKET_ID, "limit": 2}
            if path == "trade/orders-history":
                expected.update(instType="SPOT", state="filled")
            assert params == expected
            calls.append(path)
            return {
                "code": "0",
                "data": [
                    {
                        "instId": MARKET_ID,
                        "ordId": "open-id" if len(calls) == 1 else "closed-id",
                        "clOrdId": "saved-client",
                        "ordType": "limit",
                        "side": "buy",
                        "px": "100.12345678901234567890123456789",
                        "sz": "1",
                        "accFillSz": "0" if len(calls) == 1 else "1",
                        "state": "live" if len(calls) == 1 else "filled",
                        "cTime": "1000",
                        "uTime": "1000",
                    }
                ],
            }

        client_class = type(
            "OfflineOrderLookup",
            (ccxtpro.okx,),
            {
                "request": request,
                "fetch": refuse_async,
                "fetch2": refuse_async,
                "sign": refuse,
            },
        )
        client = client_class({"number": str, "options": {"defaultType": "spot"}})
        try:
            client.set_markets([market("okx")])
            result = await read_order_candidates(client, SYMBOL, max_orders=2, timeout_ms=1000)
            assert calls == ["trade/orders-pending", "trade/orders-history"]
            assert [row.order_id for row in result] == ["open-id", "closed-id"]
            assert [row.status for row in result] == ["open", "closed"]
            assert all(row.client_id == "saved-client" for row in result)
            assert all(row.price == Decimal("100.12345678901234567890123456789") for row in result)
        finally:
            await client.close()

    asyncio.run(run())


@pytest.mark.parametrize("stage", [1, 2])
@pytest.mark.parametrize("failure", ["timeout", "cancel", "error"])
def test_candidate_query_failures_are_bounded_sanitized_and_not_retried(
    stage: int, failure: str
) -> None:
    async def run() -> None:
        entered = asyncio.Event()

        class Lookup:
            calls = 0

            async def fetch_open_orders(self, symbol: str, since: None, limit: int) -> object:
                self.calls += 1
                if self.calls == stage:
                    entered.set()
                    if failure == "error":
                        raise RuntimeError("private-response-that-must-not-escape")
                    await asyncio.Event().wait()
                return []

            fetch_closed_orders = fetch_open_orders

        source = Lookup()
        task = asyncio.create_task(
            read_order_candidates(
                source, SYMBOL, max_orders=2, timeout_ms=30 if failure == "timeout" else 1000
            )
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
            assert caught.value.failure is expected and str(caught.value) == expected.value
        assert source.calls == stage

    asyncio.run(run())
