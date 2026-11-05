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
    return await inspect_account_orders(h.writer, h.scope, reads, lookup=None, **arguments)


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


class LookupReads(AccountReads):
    opened: list[dict[str, object]] | None = None
    closed: list[dict[str, object]] | None = None

    async def fetch_open_orders(self, symbol: str, since: None, limit: int) -> object:
        self.calls.append(("open", symbol))
        return deepcopy(
            self.opened
            if self.opened is not None
            else [row for row in self.venue.wire_orders.values() if row["status"] == "open"][:limit]
        )

    async def fetch_closed_orders(self, symbol: str, since: None, limit: int) -> object:
        self.calls.append(("closed", symbol))
        return deepcopy(
            self.closed
            if self.closed is not None
            else [row for row in self.venue.wire_orders.values() if row["status"] != "open"][:limit]
        )


async def locate(h: Harness, reads: LookupReads, **limits: int) -> RecoveryInspection:
    arguments = {"max_orders": 8, "max_trades": 8, "max_calls": 24, "timeout_ms": 1000}
    arguments.update(limits)
    return await inspect_account_orders(h.writer, h.scope, reads, lookup=reads, **arguments)


@pytest.mark.parametrize("filled", ["0", "0.5", "1"])
def test_lost_ack_is_located_by_saved_client_id_and_inspected_without_binding_or_resend(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, filled: str
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = LookupReads(original.venue)
            assert not (await locate(h, reads)).plan.targets and not reads.calls
            assert await h.service() and await h.send()
            if D(filled):
                reads.venue.fill("order-1", "unobserved", D(filled), D(100), ZERO_FEES)
            foreign = reads.venue.wire_orders["order-1"] | {
                "id": "external-order",
                "clientOrderId": "external-client",
            }
            reads.opened = [foreign] + (
                [reads.venue.wire_orders["order-1"]] if filled != "1" else []
            )
            before = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            with pytest.raises(ExecutionError, match="budget"):
                await locate(h, reads, max_calls=4)
            assert not reads.calls
            result = await locate(h, reads, max_calls=5)
            assert len(result.unresolved) == len(result.discovered_orders) == 1
            assert result.plan.targets[0].external_id is None
            assert result.discovered_orders[0].external_id == "order-1"
            assert result.histories[0].order.filled == D(filled)
            assert len(result.new_trades) == int(bool(D(filled)))
            assert [row.order_id for row in result.unmatched_orders] == ["external-order"]
            assert [call[0] for call in reads.calls] == [
                "open",
                "closed",
                "order",
                "account_trades",
                "order",
            ]
            assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == before
            assert await h.writer.submit(lambda ledger: ledger.binding(h.scope, 1)) is None
            assert not await h.send()
            assert len(reads.venue.sent) == 1

    asyncio.run(run())


@pytest.mark.parametrize("case", ["absent", "missing_client", "unknown_client", "submitted_id"])
def test_lookup_never_adopts_by_terms_or_infers_rejection_from_absence(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, case: str
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = LookupReads(original.venue)
            assert await h.service() and await h.send()
            row = reads.venue.wire_orders["order-1"]
            if case == "absent":
                reads.opened = []
            else:
                row["clientOrderId"] = (
                    None
                    if case == "missing_client"
                    else reads.venue.submitted_clients["order-1"]
                    if case == "submitted_id"
                    else "other"
                )
            result = await locate(h, reads, max_calls=5)
            assert result.unresolved and not result.histories and not result.discovered_orders
            assert len(result.unmatched_orders) == int(case != "absent")
            assert [call[0] for call in reads.calls] == ["open", "closed"]
            assert not await h.send() and (await h.current()).quote_cash == 1000

    asyncio.run(run())


@pytest.mark.parametrize("case", ["duplicate_client", "terms", "conflict_after_null", "oversize"])
def test_lookup_conflicts_refuse_before_followup_reads(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, case: str
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = LookupReads(original.venue)
            assert await h.service() and await h.send()
            row = reads.venue.wire_orders["order-1"]
            if case == "duplicate_client":
                reads.closed = [row | {"id": "duplicate-order"}]
            elif case == "terms":
                reads.closed = [row | {"amount": "2"}]
            elif case == "conflict_after_null":
                reads.closed = [row | {"clientOrderId": None}, row | {"clientOrderId": "other"}]
            else:
                reads.closed = [row] * 9
            before = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            with pytest.raises((ExecutionError, ReadError)):
                await locate(h, reads)
            assert [call[0] for call in reads.calls] == ["open", "closed"]
            assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == before
            assert not await h.send()

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["lookup", "history"])
def test_discovery_rechecks_the_plan_after_each_read_phase(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = LookupReads(original.venue)
            assert await h.service() and await h.send()
            method = "fetch_closed_orders" if stage == "lookup" else "fetch_my_trades"
            fetch = getattr(reads, method)

            async def changed(symbol: str, since: None, limit: int) -> object:
                rows = await fetch(symbol, since, limit)
                await h.writer.submit(
                    lambda ledger: ledger.hold("operator-hold", h.scope, "review")
                )
                return rows

            monkeypatch.setattr(reads, method, changed)
            with pytest.raises(ExecutionError, match="plan changed"):
                await locate(h, reads)
            assert len(reads.calls) == (2 if stage == "lookup" else 5)
            assert await h.writer.submit(lambda ledger: ledger.binding(h.scope, 1)) is None
            assert (await h.current()).quote_cash == 1000 and not await h.send()

    asyncio.run(run())


def test_lookup_requires_the_same_account_client_and_ignores_registration_without_attempt(
    tmp_path: Path, bounds: Bounds, state: State, context: Context
) -> None:
    from grid.persistence import client_ids

    from .test_recovery_plan import POLICY

    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads, other = LookupReads(original.venue), LookupReads(original.venue)
            await h.writer.submit(
                lambda ledger: client_ids.register(ledger, "registered", h.scope, 1, POLICY)
            )
            result = await locate(h, reads)
            assert not result.discovered_orders and not result.plan.targets and not reads.calls
            assert await h.service() and await h.send()
            with pytest.raises(ExecutionError, match="same account"):
                await inspect_account_orders(
                    h.writer,
                    h.scope,
                    reads,
                    lookup=other,
                    max_orders=8,
                    max_trades=8,
                    max_calls=24,
                    timeout_ms=1000,
                )
            assert not reads.calls and not other.calls
            assert not await h.send()

    asyncio.run(run())


@pytest.mark.parametrize("case", ["mixed", "claimed_binding", "rebound_known"])
def test_known_and_discovered_orders_share_the_same_identity_and_quantity_checks(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, case: str
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = LookupReads(original.venue)
            await h.seed()
            assert await h.send() and await h.service()  # Bound SELL.
            assert await h.send()  # BUY accepted, acknowledgement not reconciled.
            reads.venue.fill("order-3", "new-buy", D(".25"), D(90), ZERO_FEES)
            unknown = reads.venue.wire_orders["order-3"]
            if case == "claimed_binding":
                reads.opened = [unknown | {"id": "order-2"}]
                reads.closed = []
            elif case == "rebound_known":
                reads.opened = [reads.venue.wire_orders["order-2"] | {"id": "another-sell"}]
                reads.closed = []
            before = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            if case == "mixed":
                with pytest.raises(ExecutionError, match="budget"):
                    await locate(h, reads, max_calls=6)
                assert not reads.calls
                result = await locate(h, reads, max_calls=7)
                assert [row.order.order_id for row in result.histories] == ["order-2", "order-3"]
                assert result.discovered_orders[0].order.id == 3
                assert [row.trade_id for row in result.new_trades] == ["new-buy"]
                assert len(result.committed_trades) == 1  # Previously posted seed.
                assert len(reads.calls) == 7
            else:
                with pytest.raises(ExecutionError, match="binding|another intent"):
                    await locate(h, reads)
                assert len(reads.calls) == 2
            assert await h.writer.submit(lambda ledger: ledger.snapshot(h.scope)) == before
            assert await h.writer.submit(lambda ledger: ledger.binding(h.scope, 3)) is None
            assert not await h.send()

    asyncio.run(run())
