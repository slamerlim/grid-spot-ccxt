"""Account-trade fallback preserves the sole writer's inspection-only contract."""

import asyncio
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest

from grid.domain.model import Context, State
from grid.exchange.models import Failure, ReadError
from grid.execution.models import ExecutionError
from grid.execution.recovery import RecoveryInspection, inspect_account_orders, inspect_orders
from grid.persistence.records import Bounds

from .helpers import ZERO_FEES, Harness
from .test_recovery_plan import Reads, setup

pytestmark = pytest.mark.usefixtures("async_sockets")


class AccountReads(Reads):
    async def fetch_my_trades(self, symbol: str, since: None, limit: int) -> object:
        self.calls.append(("account_trades", symbol))
        return deepcopy(list(self.venue.wire_trades.values())[:limit])


async def inspect(h: Harness, reads: AccountReads, **limits: int) -> RecoveryInspection:
    arguments = {"max_orders": 8, "max_trades": 8, "max_calls": 24, "timeout_ms": 1000}
    arguments.update(limits)
    return await inspect_account_orders(h.writer, h.scope, reads, **arguments)


@pytest.mark.parametrize("attributed", [False, True])
def test_one_page_covers_working_orders_retains_old_and_external_trades_without_posting(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, attributed: bool
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = AccountReads(original.venue)
            await h.seed()
            for _ in range(4):
                assert await h.send() and await h.service()
            reads.venue.fill("order-3", "partial", D("0.25"), D(90), ZERO_FEES)
            reads.venue.wire_trades["external"] = reads.venue.wire_trades["partial"] | {
                "id": "external",
                "order": "not-in-plan" if attributed else None,
            }
            h.executor.degrade("inspection does not admit orders")
            before = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            with pytest.raises(ExecutionError, match="budget"):
                await inspect(h, reads, max_orders=4, max_calls=8)
            assert reads.calls == []
            result = await inspect(h, reads, max_orders=4, max_calls=9, max_trades=3)
            assert len(reads.calls) == 9
            assert sum(method == "account_trades" for method, _ in reads.calls) == 1
            assert all(method != "trades" for method, _ in reads.calls)
            assert len(result.histories) == 4 and not result.unresolved
            assert {trade.order_id for trade in result.unmatched_trades} == {
                "order-1",
                "not-in-plan" if attributed else None,
            }
            assert next(
                h for h in result.histories if h.order.order_id == "order-3"
            ).order.filled == D("0.25")
            per_order = await inspect_orders(
                h.writer,
                h.scope,
                original,
                max_orders=4,
                max_trades=3,
                max_calls=12,
                timeout_ms=1000,
            )
            assert (result.plan, result.histories) == (per_order.plan, per_order.histories)
            assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == before
            assert not await h.send() and h.executor.degraded

    asyncio.run(run())


def test_no_known_ids_make_no_calls_and_unknown_submissions_remain_explicit(
    tmp_path: Path, bounds: Bounds, state: State, context: Context
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = AccountReads(original.venue)
            assert not (await inspect(h, reads, max_calls=1)).plan.targets
            assert await h.service() and await h.send()
            unknown = await inspect(h, reads, max_calls=1)
            assert len(unknown.unresolved) == 1 and not unknown.histories
            assert reads.calls == [] and not await h.send()
            assert await h.service()
            reads.venue.fill("order-1", "seed", D(1), D(100), ZERO_FEES)
            assert await h.service()
            assert await h.send() and await h.service()  # SELL bound.
            assert await h.send()  # Lower BUY unbound.
            before = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            result = await inspect(h, reads, max_calls=3)
            assert len(result.unresolved) == len(result.histories) == 1
            assert result.unresolved[0].order.id == 3
            assert result.histories[0].order.order_id == "order-2"
            assert len(result.unmatched_trades) == 1
            assert len(reads.calls) == 3
            assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == before
            assert not await h.send()

    asyncio.run(run())


@pytest.mark.parametrize("change", ["version", "binding", "terms", "incomplete"])
def test_account_inspection_refuses_changed_plans_contradictions_and_truncated_history(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = AccountReads(original.venue)
            assert await h.service() and await h.send() and await h.service()
            if change == "terms":
                reads.venue.wire_orders["order-1"]["clientOrderId"] = "wrong"
            elif change == "incomplete":
                reads.venue.fill("order-1", "partial", D("0.5"), D(100), ZERO_FEES)
                own = reads.venue.wire_trades.pop("partial")
                reads.venue.wire_trades["external"] = own | {"id": "external", "order": "other"}
                reads.venue.wire_trades["partial"] = own
            else:
                original_fetch = reads.fetch_my_trades

                async def changed(symbol: str, since: None, limit: int) -> object:
                    rows = await original_fetch(symbol, since, limit)
                    if change == "version":
                        await h.writer.submit(
                            lambda ledger: ledger.hold("changed", h.scope, "other owner action")
                        )
                    else:
                        await h.writer.submit(
                            lambda ledger: ledger.db.execute(
                                "UPDATE order_bindings SET external_id='changed'"
                            )
                        )
                    return rows

                monkeypatch.setattr(reads, "fetch_my_trades", changed)
            before = await h.current()
            if change == "incomplete":
                with pytest.raises(ReadError) as caught:
                    await inspect(h, reads, max_trades=1)
                assert caught.value.failure is Failure.INCOMPLETE
            else:
                with pytest.raises(ExecutionError):
                    await inspect(h, reads)
            after = await h.current()
            assert (after.quote_cash, after.owned_base) == (before.quote_cash, before.owned_base)
            assert len(reads.calls) == 3
            assert (
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()[0]
                )
                == 0
            )

    asyncio.run(run())
