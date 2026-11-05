"""Recent unified windows use durable fills without erasing old financial history."""

import asyncio
import re
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

import pytest

from grid.domain.market import FeeRule, Money
from grid.domain.model import ActualFee, Context, Fill, State
from grid.exchange.models import Failure, ReadError
from grid.exchange.private import read_account_history, read_order_history
from grid.execution.models import ExecutionError
from grid.execution.recovery import (
    RecoveryInspection,
    inspect_account_orders,
    inspect_orders,
    recovery_plan,
)
from grid.persistence import codec
from grid.persistence.ledger import Ledger, client_identity, trade_identity
from grid.persistence.records import Bounds

from .helpers import ZERO_FEES, Harness
from .test_recovery_plan import Reads, setup

pytestmark = pytest.mark.usefixtures("async_sockets")


class Recent(Reads):
    allowed: set[str] | None = None

    def __init__(self, source: Reads) -> None:
        super().__init__(source.venue)
        self.overrides: dict[str, dict[str, object]] = {}

    def rows(self, maximum: int, order: str | None = None) -> list[dict[str, object]]:
        return [
            deepcopy(row) | self.overrides.get(identity, {})
            for identity, row in self.venue.wire_trades.items()
            if (self.allowed is None or identity in self.allowed)
            and (order is None or row["order"] == order)
        ][:maximum]

    async def fetch_order_trades(self, id: str, symbol: str, since: None, limit: int) -> object:
        self.calls.append(("trades", id))
        return self.rows(limit, id)

    async def fetch_my_trades(self, symbol: str, since: None, limit: int) -> object:
        self.calls.append(("account_trades", symbol))
        return self.rows(limit)


async def inspect(h: Harness, reads: Recent, account: bool, **limits: int) -> RecoveryInspection:
    arguments = {
        "max_orders": 8,
        "max_trades": 8,
        "max_calls": 24,
        "timeout_ms": 1000,
        "max_committed_trades": 8,
    }
    arguments.update(limits)
    function = inspect_account_orders if account else inspect_orders
    return await function(h.writer, h.scope, reads, **arguments)


async def partial(h: Harness, reads: Recent) -> None:
    assert await h.service() and await h.send() and await h.service()
    reads.venue.fill("order-1", "old-1", D(".2"), D(100), ZERO_FEES)
    reads.venue.fill("order-1", "old-2", D(".3"), D(100), ZERO_FEES)
    assert await h.service()


@pytest.mark.parametrize("account", [False, True])
def test_recent_page_can_omit_posted_fills_but_must_cover_all_new_quantity(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    account: bool,
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, source):
            reads = Recent(source)
            await partial(h, reads)
            reads.venue.fill("order-1", "new", D(".1"), D(100), ZERO_FEES)
            reads.allowed = {"new"}
            with pytest.raises(ReadError) as caught:
                if account:
                    await read_account_history(
                        reads, ("order-1",), h.scope.symbol, max_trades=1, timeout_ms=1000
                    )
                else:
                    await read_order_history(
                        reads, "order-1", h.scope.symbol, max_trades=1, timeout_ms=1000
                    )
            assert caught.value.failure is Failure.INCOMPLETE
            h.executor.degrade("inspection cannot grant dispatch")
            before = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            for _ in range(2):
                with localcontext() as ambient:
                    ambient.prec = 3
                    result = await inspect(h, reads, account, max_trades=1, max_committed_trades=2)
                    assert ambient.prec == 3
                assert len(result.plan.targets[0].committed) == 2
                assert [t.trade_id for t in result.new_trades] == ["new"]
                assert not result.committed_trades and not result.unmatched_trades
                assert result.histories[0].order.filled == D(".6")
                assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == before
                assert not await h.send() and h.executor.degraded
            assert await h.service()  # Only the existing executor posts actual fills.
            result = await inspect(h, reads, account, max_trades=1)
            assert not result.new_trades and [t.trade_id for t in result.committed_trades] == [
                "new"
            ]
            reads.allowed = set()
            result = await inspect(h, reads, account, max_trades=1)
            assert not result.new_trades and not result.committed_trades
            assert result.histories[0].order.filled == D(".6")
            assert (
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()[0]
                )
                == 3
            )

    asyncio.run(run())


@pytest.mark.parametrize("account", [False, True])
@pytest.mark.parametrize(
    "fault",
    [
        "missing_new",
        "relabel_old",
        "amount",
        "price",
        "fees",
        "order",
        "collision",
        "limit",
        "type",
        "side",
    ],
)
def test_committed_conflicts_or_missing_new_fills_cannot_return_a_delta(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    account: bool,
    fault: str,
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, source):
            reads = Recent(source)
            await partial(h, reads)
            reads.venue.fill("order-1", "new", D(".1"), D(100), ZERO_FEES)
            if fault == "missing_new":
                reads.allowed = {"old-1", "old-2"}
            else:
                changes: dict[str, dict[str, object]] = {
                    "relabel_old": {"id": "renamed"},
                    "amount": {"amount": ".1", "cost": "10"},
                    "price": {"price": "99", "cost": "19.8"},
                    "fees": {"fees": [{"currency": "QUOTE", "cost": ".01"}]},
                    "order": {"order": "other"},
                    "collision": {"id": "old-1"},
                    "limit": {"price": "101", "cost": "10.1"},
                    "type": {"type": "market"},
                    "side": {"side": "sell"},
                }
                reads.overrides[
                    "new" if fault in ("collision", "limit", "type", "side") else "old-1"
                ] = changes[fault]
            before = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            with pytest.raises((ExecutionError, ReadError)):
                await inspect(h, reads, account)
            assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == before
            assert (
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()[0]
                )
                == 2
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault",
    ["bound", "missing_key", "payload", "fee_projection", "retained", "total", "extra_fill"],
)
def test_inconsistent_or_oversized_committed_baseline_refuses_before_network_reads(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    fault: str,
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, source):
            reads = Recent(source)
            await partial(h, reads)
            current = await h.current()
            allocation = codec.digest(h.scope)
            first = current.fills[0]
            if fault == "missing_key":
                await h.writer.submit(
                    lambda ledger: ledger.db.execute(
                        "DELETE FROM trade_keys WHERE allocation=? AND trade_id=?",
                        (allocation, first.trade_id),
                    )
                )
            elif fault == "payload":
                await h.writer.submit(
                    lambda ledger: ledger.db.execute(
                        "UPDATE fills SET payload=? WHERE allocation=? AND trade_id=?",
                        (codec.dumps(replace(first, price=D(99))), allocation, first.trade_id),
                    )
                )
            elif fault == "fee_projection":
                await h.writer.submit(
                    lambda ledger: ledger.db.execute(
                        "UPDATE fees SET quote_value='1' WHERE allocation=? AND trade_id=?",
                        (allocation, first.trade_id),
                    )
                )
            elif fault == "retained":
                await h.writer.submit(
                    lambda ledger: ledger.db.execute(
                        "UPDATE allocations SET state=? WHERE id=?",
                        (codec.dumps(replace(current, fills=())), allocation),
                    )
                )
            elif fault == "total":
                changed = replace(current.orders[0], gross_cost=D(49))
                await h.writer.submit(
                    lambda ledger: ledger.db.execute(
                        "UPDATE allocations SET state=? WHERE id=?",
                        (codec.dumps(replace(current, orders=(changed,))), allocation),
                    )
                )
                await h.writer.submit(
                    lambda ledger: ledger.db.execute(
                        "UPDATE orders SET payload=? WHERE allocation=? AND id=?",
                        (codec.dumps(changed), allocation, changed.id),
                    )
                )
            elif fault == "extra_fill":
                extra = replace(first, trade_id="extra")
                await h.writer.submit(
                    lambda ledger: ledger.db.execute(
                        "INSERT INTO fills VALUES (?,?,?,?)",
                        (allocation, extra.trade_id, extra.order_id, codec.dumps(extra)),
                    )
                )
            with pytest.raises(ExecutionError):
                await inspect(h, reads, True, max_committed_trades=1 if fault == "bound" else 2)
            assert reads.calls == []

    asyncio.run(run())


@pytest.mark.parametrize("account", [False, True])
def test_missing_optional_fee_and_order_fields_preserve_committed_settlement_values(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    account: bool,
) -> None:
    async def run() -> None:
        configured = replace(context, buy_fee=FeeRule("TOKEN", D(".01"), D(".0001"), D(20), 64))
        async with setup(tmp_path / "ledger.db", bounds, state, configured) as (h, source):
            reads = Recent(source)
            assert await h.service() and await h.send() and await h.service()
            reads.venue.fill(
                "order-1", "paid", D(".5"), D(100), (ActualFee("TOKEN", D(".01"), D(".2")),)
            )
            assert await h.service()
            reads.overrides["paid"] = {"fees": None, "fee": None, "side": None, "type": None}
            before = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            result = await inspect(h, reads, account)
            assert result.committed_trades[0].fees is None and not result.new_trades
            assert result.plan.targets[0].committed[0].fill.fees == (
                ActualFee("TOKEN", D(".01"), D(".2")),
            )
            assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == before

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["none", "price", "amount", "fees", "order", "unattributed"])
def test_unmatched_terminal_replays_are_checked_without_scanning_terminal_history(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    fault: str,
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, source):
            reads = Recent(source)
            await h.seed()
            assert await h.send() and await h.service()
            snap = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            assert snap.version is not None
            version = snap.version
            await h.writer.submit(lambda ledger: ledger.compact("compact", h.scope, version))
            changes: dict[str, dict[str, object]] = {
                "none": {},
                "price": {"price": "99", "cost": "99"},
                "amount": {"amount": ".5", "cost": "50"},
                "fees": {"fees": [{"currency": "QUOTE", "cost": "1"}]},
                "order": {"order": "unknown"},
                "unattributed": {"order": None},
            }
            reads.overrides["seed"] = changes[fault]
            before = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            if fault in ("none", "unattributed"):
                result = await inspect(h, reads, True, max_trades=1)
                assert not result.new_trades
                assert len(result.unmatched_trades) == len(result.committed_trades) == 1
                assert not result.plan.targets[0].committed
                assert result.unmatched_trades[0].order_id == (
                    None if fault == "unattributed" else "order-1"
                )
            else:
                with pytest.raises(ExecutionError):
                    await inspect(h, reads, True, max_trades=1)
            assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == before
            assert (
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()[0]
                )
                == 1
            )

    asyncio.run(run())


def test_populated_baseline_and_returned_trade_comparisons_use_bounded_indexed_reads(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, source):
            reads = Recent(source)
            await partial(h, reads)
            working = {order.id for order in (await h.current()).working}
            statements: list[str] = []
            await h.writer.submit(lambda ledger: ledger.db.set_trace_callback(statements.append))
            try:
                result = await inspect(h, reads, True, max_committed_trades=2)
            finally:
                await h.writer.submit(lambda ledger: ledger.db.set_trace_callback(None))
            assert len(result.committed_trades) == 2
            assert statements and all(sql.startswith("SELECT") for sql in statements)
            for sql in statements:

                def explain(ledger: Ledger, query: str = sql) -> tuple[str, ...]:
                    return tuple(
                        str(row[3]) for row in ledger.db.execute("EXPLAIN QUERY PLAN " + query)
                    )

                details = await h.writer.submit(explain)
                assert details and all(
                    "SEARCH" in detail and "SCAN" not in detail for detail in details
                )
                if "FROM fills f" in sql:
                    order_match = re.search(r"f\.order_id=(\d+)", sql)
                    limit_match = re.search(r"LIMIT (\d+)$", sql)
                    assert order_match and int(order_match[1]) in working
                    assert limit_match and 1 <= int(limit_match[1]) <= 3
            assert (
                await h.writer.submit(
                    lambda ledger: recovery_plan(
                        ledger, h.scope, max_orders=8, max_committed_trades=2
                    )
                )
                == result.plan
            )

    asyncio.run(run())


@pytest.mark.parametrize("account", [False, True])
def test_old_terminal_trade_id_cannot_be_reassigned_to_a_new_working_order(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    account: bool,
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, source):
            reads = Recent(source)
            await h.seed()
            assert await h.send() and await h.service()
            assert await h.send() and await h.service()
            reads.venue.fill("order-3", "fresh", D(".1"), D(90), ZERO_FEES)
            reads.allowed = {"fresh"}
            reads.overrides["fresh"] = {"id": "seed"}
            before = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            with pytest.raises(ExecutionError, match="another order or allocation"):
                await inspect(h, reads, account)
            assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == before
            assert (
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()[0]
                )
                == 1
            )

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["fee_projection", "identity"])
def test_committed_evidence_is_rechecked_after_reads_even_without_a_version_change(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, source):
            reads = Recent(source)
            await partial(h, reads)
            before = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            original = reads.fetch_order

            async def changed(id: str, symbol: str) -> object:
                row = await original(id, symbol)
                if len(reads.calls) == 1:
                    if fault == "fee_projection":
                        await h.writer.submit(
                            lambda ledger: ledger.db.execute("UPDATE fees SET quote_value='1'")
                        )
                    else:
                        await h.writer.submit(
                            lambda ledger: ledger.db.execute(
                                "UPDATE trade_keys SET external_id='changed' "
                                "WHERE external_id='old-1'"
                            )
                        )
                return row

            monkeypatch.setattr(reads, "fetch_order", changed)
            with pytest.raises(ExecutionError):
                await inspect(h, reads, True)
            assert len(reads.calls) == 3
            assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == before
            assert (
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()[0]
                )
                == 2
            )

    asyncio.run(run())


@pytest.mark.parametrize("reassigned", [False, True])
def test_trade_identity_is_checked_across_allocations_without_adopting_foreign_fills(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    reassigned: bool,
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, source):
            reads = Recent(source)
            assert await h.service() and await h.send() and await h.service()
            other = replace(h.scope, allocation="other-allocation")

            def other_fill(ledger: Ledger) -> None:
                ledger.credit("other-funding", other.venue, other.account, Money("QUOTE", D(1000)))
                started = ledger.start("other-start", other, (Money("QUOTE", D(1000)),), context)
                assert started.version is not None
                intent = ledger.intents(other)[0]
                attempted = ledger.attempt("other-attempt", other, started.version, intent.id)
                assert attempted.version is not None
                ledger.bind("other-bind", other, 1, "other-order", client_identity(other, 1))
                result = ledger.apply(
                    "other-fill",
                    attempted.version,
                    Fill(
                        other,
                        trade_identity(other, "foreign-trade"),
                        1,
                        D(".25"),
                        D(100),
                        ZERO_FEES,
                    ),
                    context,
                    external=("other-order", "foreign-trade"),
                )
                assert result.accepted

            await h.writer.submit(other_fill)
            if reassigned:
                reads.venue.fill("order-1", "own", D(".25"), D(100), ZERO_FEES)
                reads.overrides["own"] = {"id": "foreign-trade"}
            else:
                reads.venue.wire_trades["foreign-trade"] = {
                    "id": "foreign-trade",
                    "order": "other-order",
                    "symbol": h.scope.symbol,
                    "side": "buy",
                    "type": "limit",
                    "amount": ".25",
                    "price": "100",
                    "cost": "25",
                    "fees": [{"currency": "QUOTE", "cost": "0"}],
                }
            before = await h.writer.submit(
                lambda ledger: (ledger.snapshot(h.scope), ledger.snapshot(other))
            )
            if reassigned:
                with pytest.raises(ExecutionError, match="another order or allocation"):
                    await inspect(h, reads, True)
            else:
                result = await inspect(h, reads, True)
                assert not result.new_trades
                assert len(result.committed_trades) == len(result.unmatched_trades) == 1
                assert result.unmatched_trades[0].order_id == "other-order"
            assert (
                await h.writer.submit(
                    lambda ledger: (ledger.snapshot(h.scope), ledger.snapshot(other))
                )
                == before
            )

    asyncio.run(run())
