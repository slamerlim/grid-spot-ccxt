"""Outstanding-order inspection retains unknown obligations without posting money."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from grid.domain.model import Context, PlaceLimit, State
from grid.execution.client_ids import MappedSyntheticExchange
from grid.execution.executor import MockExecutor
from grid.execution.models import ExecutionError
from grid.execution.recovery import RecoveryInspection, inspect_orders, recovery_plan
from grid.persistence import client_ids, codec
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds, ClientIdPolicy

from .helpers import EVIDENCE, LIMITS, ZERO_FEES, Harness, harness

pytestmark = pytest.mark.usefixtures("async_sockets")
POLICY = ClientIdPolicy("recovery-plan-mock", "t-")


class Reads:
    def __init__(self, venue: MappedSyntheticExchange) -> None:
        self.venue = venue
        self.calls: list[tuple[str, str]] = []

    async def fetch_order(self, id: str, symbol: str) -> object:
        self.calls.append(("order", id))
        return deepcopy(self.venue.wire_orders[id])

    async def fetch_order_trades(self, id: str, symbol: str, since: None, limit: int) -> object:
        self.calls.append(("trades", id))
        return deepcopy([t for t in self.venue.wire_trades.values() if t["order"] == id][:limit])


@asynccontextmanager
async def setup(
    path: Path, bounds: Bounds, state: State, context: Context
) -> AsyncIterator[tuple[Harness, Reads]]:
    async with harness(path, bounds, state, context) as h:
        await h.executor.close()
        venue = MappedSyntheticExchange(100, h.writer, POLICY)
        h.venue = venue
        h.executor = MockExecutor(h.writer, venue, (h.scope,), LIMITS, EVIDENCE)
        try:
            yield h, Reads(venue)
        finally:
            await h.executor.close()


async def inspect(h: Harness, reads: Reads, **limits: int) -> RecoveryInspection:
    arguments = {"max_orders": 8, "max_trades": 8, "max_calls": 24, "timeout_ms": 1000}
    arguments.update(limits)
    return await inspect_orders(h.writer, h.scope, reads, **arguments)


def test_registration_unknown_submission_partial_fill_and_terminal_history(
    tmp_path: Path, bounds: Bounds, state: State, context: Context
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, reads):
            identity = await h.writer.submit(
                lambda ledger: client_ids.register(ledger, "register", h.scope, 1, POLICY)
            )
            initial = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            empty = await inspect(h, reads)
            assert not empty.plan.targets and not empty.histories and not empty.unresolved
            assert not reads.calls
            assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == initial
            assert await h.service() and await h.send()
            # Acceptance by the fake is not a durable binding or proof of rejection.
            pending = await inspect(h, reads)
            assert len(pending.unresolved) == 1 and not pending.histories and not reads.calls
            assert pending.unresolved[0].client == identity
            assert pending.unresolved[0].order.unknown
            assert not await h.send()
            assert await h.service()
            reads.venue.fill("order-1", "partial", D("0.5"), D(100), ZERO_FEES)
            h.executor.degrade("inspection cannot grant admission")
            before = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            observed = await inspect(h, reads)
            assert observed.unresolved == () and len(observed.histories) == 1
            assert observed.histories[0].order.filled == D("0.5")
            assert observed.plan.targets[0].order.filled == 0
            assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == before
            assert not await h.send() and h.executor.degraded
            assert reads.calls == [
                ("order", "order-1"),
                ("trades", "order-1"),
                ("order", "order-1"),
            ]
            assert await h.service()
            # Current-order projections may change; immutable original terms must not.
            assert (await inspect(h, reads)).plan.targets[0].order.filled == D("0.5")
            reads.venue.fill("order-1", "rest", D("0.5"), D(100), ZERO_FEES)
            assert await h.service()
            snapshot = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            assert snapshot.version is not None
            version = snapshot.version
            await h.writer.submit(lambda ledger: ledger.compact("compact", h.scope, version))
            count = len(reads.calls)
            assert (await inspect(h, reads)).plan.targets == ()
            assert len(reads.calls) == count
            assert (
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()[0]
                )
                == 2
            )

    asyncio.run(run())


@pytest.mark.parametrize("limit", ["orders", "calls"])
def test_roster_and_unified_call_budgets_preflight_before_reads(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, limit: str
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, reads):
            await h.seed()
            for _ in range(4):
                assert await h.send() and await h.service()
            with pytest.raises(ExecutionError, match="bound|budget"):
                await inspect(
                    h, reads, **({"max_orders": 3} if limit == "orders" else {"max_calls": 11})
                )
            assert reads.calls == []
            result = await inspect(h, reads, max_orders=4, max_calls=12)
            assert len(result.plan.targets) == len(result.histories) == 4
            assert len(reads.calls) == 12 and all(id != "order-1" for _, id in reads.calls)

    asyncio.run(run())


def test_known_orders_are_read_while_unbound_submissions_remain_explicit(
    tmp_path: Path, bounds: Bounds, state: State, context: Context
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, reads):
            await h.seed()
            assert await h.send() and await h.service()  # SELL bound.
            assert await h.send()  # Lower BUY attempted, not yet bound.
            result = await inspect(h, reads, max_calls=3)
            assert {target.order.id for target in result.plan.targets} == {2, 3}
            assert len(result.histories) == len(result.unresolved) == 1
            assert result.histories[0].order.order_id == "order-2"
            assert result.unresolved[0].order.id == 3
            assert len(reads.calls) == 3 and all(id == "order-2" for _, id in reads.calls)
            assert not await h.send()

    asyncio.run(run())


@pytest.mark.parametrize(
    "change", ["version", "binding", "missing_intent", "kind", "payload", "lost_attempt"]
)
def test_changed_or_corrupt_plan_cannot_return_a_recovery_inspection(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, reads):
            assert await h.service() and await h.send() and await h.service()
            key = codec.digest((codec.digest(h.scope), "place", 1, None))
            if change in ("version", "binding"):
                original = reads.fetch_order

                async def changed(id: str, symbol: str) -> object:
                    result = await original(id, symbol)
                    if len(reads.calls) == 1:
                        if change == "version":
                            await h.writer.submit(
                                lambda ledger: ledger.hold("other", h.scope, "other owner action")
                            )
                        else:
                            await h.writer.submit(
                                lambda ledger: ledger.db.execute(
                                    "UPDATE order_bindings SET external_id='changed'"
                                )
                            )
                    return result

                monkeypatch.setattr(reads, "fetch_order", changed)
            elif change == "missing_intent":
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("DELETE FROM intents WHERE id=?", (key,))
                )
            elif change == "kind":
                await h.writer.submit(
                    lambda ledger: ledger.db.execute(
                        "UPDATE intents SET kind='cancel' WHERE id=?", (key,)
                    )
                )
            elif change == "payload":
                await h.writer.submit(
                    lambda ledger: ledger.db.execute(
                        "UPDATE intents SET payload=? WHERE id=?",
                        (
                            codec.dumps(
                                PlaceLimit(h.scope, replace(ledger.order(h.scope, 1), price=D(99)))
                            ),
                            key,
                        ),
                    )
                )
            else:
                await h.writer.submit(
                    lambda ledger: ledger.db.execute(
                        "UPDATE intents SET attempt_run=NULL WHERE id=?", (key,)
                    )
                )
            before = await h.current()
            with pytest.raises(ExecutionError):
                await inspect(h, reads)
            assert len(reads.calls) == (3 if change in ("version", "binding") else 0)
            after = await h.current()
            assert (after.quote_cash, after.owned_base) == (before.quote_cash, before.owned_base)

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("clientOrderId", "wrong"),
        ("type", "market"),
        ("side", "sell"),
        ("amount", "2"),
        ("price", "99"),
        ("remaining", "0"),
    ],
)
def test_stable_reports_must_still_match_the_owned_order(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, field: str, value: str
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, reads):
            assert await h.service() and await h.send() and await h.service()
            reads.venue.wire_orders["order-1"][field] = value
            with pytest.raises(ExecutionError, match="contradicts"):
                await inspect(h, reads)
            assert (await h.current()).quote_cash == 1000
            assert (
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()[0]
                )
                == 0
            )

    asyncio.run(run())


def test_missing_optional_terms_use_existing_binding_without_rewriting_observations(
    tmp_path: Path, bounds: Bounds, state: State, context: Context
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, reads):
            assert await h.service() and await h.send() and await h.service()
            for key in ("clientOrderId", "type", "side", "amount", "price", "remaining"):
                reads.venue.wire_orders["order-1"].pop(key)
            result = await inspect(h, reads)
            assert result.histories[0].order.client_id is None
            assert result.histories[0].order.price is None
            assert result.plan.targets[0].external_id == "order-1"

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_batch_timeout_and_cancellation_leave_ledger_and_unknowns_unchanged(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    cancel: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, reads):
            assert await h.service() and await h.send() and await h.service()
            before = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            entered = asyncio.Event()

            async def stall(id: str, symbol: str) -> object:
                entered.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

            monkeypatch.setattr(reads, "fetch_order", stall)
            task = asyncio.create_task(inspect(h, reads, timeout_ms=1000 if cancel else 30))
            await entered.wait()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(TimeoutError):
                    await task
            assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == before

    asyncio.run(run())


def test_actual_planning_queries_use_indexes_and_do_not_read_lifetime_history(
    tmp_path: Path, bounds: Bounds, state: State, context: Context
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, reads):
            assert await h.service() and await h.send() and await h.service()

            statements: list[str] = []
            await h.writer.submit(lambda ledger: ledger.db.set_trace_callback(statements.append))
            try:
                result = await h.writer.submit(
                    lambda ledger: recovery_plan(ledger, h.scope, max_orders=8)
                )
            finally:
                await h.writer.submit(lambda ledger: ledger.db.set_trace_callback(None))
            assert len(result.targets) == 1
            assert statements and all(sql.startswith("SELECT") for sql in statements)
            for sql in statements:

                def explain(ledger: Ledger, query: str = sql) -> tuple[str, ...]:
                    return tuple(
                        str(row[3]) for row in ledger.db.execute("EXPLAIN QUERY PLAN " + query)
                    )

                details = await h.writer.submit(explain)
                assert details and all(
                    "SCAN" not in detail and "SEARCH" in detail for detail in details
                )
                if "FROM fills" in sql:
                    assert "f.order_id=" in sql and " LIMIT " in sql
                assert "FROM trade_keys" not in sql
            assert any("FROM intents WHERE id=" in sql for sql in statements)

    asyncio.run(run())


@pytest.mark.parametrize("case", ["regressed", "overfilled", "closed_partial", "rejected_fills"])
def test_quantity_matched_source_cannot_override_committed_quantity_or_order_size(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, case: str
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, reads):
            assert await h.service() and await h.send() and await h.service()
            reads.venue.fill("order-1", "partial", D("0.5"), D(100), ZERO_FEES)
            assert await h.service()
            if case == "regressed":
                reads.venue.wire_orders["order-1"].update(filled="0", remaining="1")
                reads.venue.wire_trades.clear()
            elif case == "overfilled":
                reads.venue.wire_orders["order-1"].update(filled="2", remaining="0")
                reads.venue.wire_trades["partial"]["amount"] = "2"
            else:
                reads.venue.wire_orders["order-1"]["status"] = (
                    "closed" if case == "closed_partial" else "rejected"
                )
            before = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            with pytest.raises(ExecutionError, match="cumulative"):
                await inspect(h, reads)
            assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == before

    asyncio.run(run())
