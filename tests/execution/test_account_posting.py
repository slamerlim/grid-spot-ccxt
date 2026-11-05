"""Unified account reads enter the one executor without granting send permission."""

import asyncio
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.domain.model import ActualFee, Context, State
from grid.execution.executor import MockExecutor
from grid.persistence import codec
from grid.persistence.records import Bounds, ExecutionPosition
from grid.persistence.writer import AsyncWriter

from .helpers import EVIDENCE, LIMITS, Harness
from .test_account_recovery import LookupReads
from .test_ordering import fill
from .test_recovery_plan import setup

pytestmark = pytest.mark.usefixtures("async_sockets")


async def observe(h: Harness, reads: LookupReads) -> bool:
    return await h.executor.service(h.scope, h.context, account_reads=reads, lookup=reads)


def test_lost_ack_partial_fills_replay_and_terminal_recovery_share_the_existing_ledger(
    tmp_path: Path, bounds: Bounds, state: State, context: Context
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = LookupReads(original.venue)
            assert await h.service() and await h.send()
            fill(reads.venue, "order-1", "partial", ".4", "100", 100)
            assert not await observe(h, reads)
            assert (
                h.executor.last_error
                == "order observations applied; complete account recovery required"
            )
            assert await h.writer.submit(lambda ledger: ledger.binding(h.scope, 1)) == "order-1"
            current = await h.current()
            assert current.quote_cash == 960 and current.owned_base == D(".4")
            assert current.reconcile_reason is not None and not await h.send()
            reads.venue.wire_trades["partial"].update(timestamp=None, fees=None)
            assert not await observe(h, reads)
            assert (await h.current()).quote_cash == 960
            fill(reads.venue, "order-1", "remainder", ".6", "100", 101)
            assert not await observe(h, reads)
            assert (await h.current()).quote_cash == 900
            assert await h.writer.submit(
                lambda ledger: ledger.execution_position(h.scope)
            ) == ExecutionPosition("timestamp", 101)
            assert await h.writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
            ) == (2,)
            assert not await h.send() and len(reads.venue.sent) == 1
            # Restore the mock source's explicit fee evidence for its complete recovery pass.
            reads.venue.wire_trades["partial"]["fees"] = [{"currency": "QUOTE", "cost": "0"}]
            assert await h.service(), h.executor.last_error
            assert (await h.current()).quote_cash == 900
            assert await h.send() and len(reads.venue.sent) == 2

    asyncio.run(run())


@pytest.mark.parametrize("sell_first", [True, False])
def test_account_read_posting_preserves_mixed_buy_sell_cost_basis(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, sell_first: bool
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = LookupReads(original.venue)
            assert await h.service() and await h.send()
            fill(reads.venue, "order-1", "seed", "1", "100", 100)
            assert await h.service()
            for _ in range(2):
                assert await h.send() and await h.service()
            buy = reads.venue.sent[2]
            fill(reads.venue, "order-2", "sell", "1", "110", 200 if sell_first else 201)
            fill(reads.venue, "order-3", "buy", buy.amount, buy.price, 201 if sell_first else 200)
            assert not await observe(h, reads)
            result = await h.current()
            assert result.realized_pnl == (D(10) if sell_first else D("14.55"))
            assert result.position_cost == (D("90.9") if sell_first else D("95.45"))
            assert result.quote_cash + result.position_cost == 1000 + result.realized_pnl
            assert not await h.send()

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    [
        "missing_fee",
        "third_fee",
        "tie",
        "foreign_order",
        "foreign_trade",
        "missing_order",
        "epoch",
        "plan",
    ],
)
def test_invalid_account_batch_cannot_bind_or_post_any_fill(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = LookupReads(original.venue)
            assert await h.service() and await h.send()
            fill(reads.venue, "order-1", "one", ".4", "100", 100)
            fill(reads.venue, "order-1", "two", ".6", "100", 101)
            if case == "missing_fee":
                reads.venue.wire_trades["two"]["fees"] = None
            elif case == "third_fee":
                reads.venue.wire_trades["two"]["fees"] = [{"currency": "TOKEN", "cost": ".01"}]
            elif case == "tie":
                reads.venue.wire_trades["two"]["timestamp"] = 100
            elif case == "foreign_order":
                reads.opened = [
                    reads.venue.wire_orders["order-1"]
                    | {"id": "foreign", "clientOrderId": "foreign"}
                ]
            elif case == "foreign_trade":
                reads.venue.wire_trades["foreign"] = reads.venue.wire_trades["one"] | {
                    "id": "foreign",
                    "order": "foreign",
                }
            elif case == "missing_order":
                reads.opened, reads.closed = [], []
            else:
                fetch = reads.fetch_my_trades

                async def changed(symbol: str, since: None, limit: int) -> object:
                    rows = await fetch(symbol, since, limit)
                    if case == "epoch":
                        h.executor.degrade("stream replaced")
                    else:
                        await h.writer.submit(
                            lambda ledger: ledger.hold("operator-hold", h.scope, "review")
                        )
                    return rows

                monkeypatch.setattr(reads, "fetch_my_trades", changed)
            assert not await observe(h, reads)
            assert not await h.send() and (await h.current()).quote_cash == 1000
            assert await h.writer.submit(lambda ledger: ledger.binding(h.scope, 1)) is None
            assert await h.writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
            ) == (0,)

    asyncio.run(run())


def test_lookup_identity_survives_missing_optional_client_id_on_followup_reads(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = LookupReads(original.venue)
            assert await h.service() and await h.send()
            fill(reads.venue, "order-1", "partial", ".4", "100", 100)
            fetch = reads.fetch_order

            async def without_client(id: str, symbol: str) -> object:
                row = await fetch(id, symbol)
                assert isinstance(row, dict)
                return row | {"clientOrderId": None}

            monkeypatch.setattr(reads, "fetch_order", without_client)
            assert not await observe(h, reads)
            assert (await h.current()).quote_cash == 960
            assert reads.venue.wire_orders["order-1"]["clientOrderId"] is not None
            assert not await h.send()

    asyncio.run(run())


@pytest.mark.parametrize("case", ["rate", "records", "timestamp_replay"])
def test_account_path_preserves_budget_and_committed_ordering_checks(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, case: str
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = LookupReads(original.venue)
            assert await h.service() and await h.send()
            fill(reads.venue, "order-1", "old", ".4", "100", 100)
            if case == "rate":
                # Startup recovery and send already spent two calls; lookup needs five more.
                h.executor.limits = replace(LIMITS, calls_per_window=6)
            elif case == "records":
                h.executor.limits = replace(LIMITS, max_records=1)
            else:
                assert not await observe(h, reads)
                reads.venue.wire_trades["old"]["timestamp"] = 101
                fill(reads.venue, "order-1", "new", ".6", "100", 102)
            before = await h.current()
            assert not await observe(h, reads)
            assert (await h.current()).quote_cash == before.quote_cash and not await h.send()
            if case == "rate":
                assert not reads.calls
                assert len(h.executor._calls[(h.scope.venue, h.scope.account)]) == 2

    asyncio.run(run())


def test_writer_restart_reuses_committed_fills_before_posting_a_new_account_observation(
    tmp_path: Path, bounds: Bounds, state: State, context: Context
) -> None:
    async def run() -> None:
        path = tmp_path / "ledger.db"
        async with setup(path, bounds, state, context) as (h, original):
            reads = LookupReads(original.venue)
            assert await h.service() and await h.send()
            fill(reads.venue, "order-1", "first", ".4", "100", 100)
            assert not await observe(h, reads)
            await h.executor.close()
            await h.writer.close()
            writer = await AsyncWriter.open(path, bounds, SOURCES, mock_storage=True)
            reads.venue.writer = writer
            recovered = MockExecutor(writer, reads.venue, (h.scope,), LIMITS, EVIDENCE)
            try:
                assert not await recovered.service(
                    h.scope, context, account_reads=reads, lookup=reads
                )
                fill(reads.venue, "order-1", "second", ".6", "100", 101)
                assert not await recovered.service(
                    h.scope, context, account_reads=reads, lookup=reads
                )
                result = await writer.submit(lambda ledger: ledger.snapshot(h.scope))
                assert result.state is not None and result.state.quote_cash == 900
                assert await writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
                ) == (2,)
                assert not await recovered.dispatch(h.scope, context)
            finally:
                await recovered.close()
                await writer.close()

    asyncio.run(run())


def test_actual_fee_overrun_posts_once_and_retains_its_durable_stop(
    tmp_path: Path, bounds: Bounds, state: State, context: Context
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = LookupReads(original.venue)
            assert await h.service() and await h.send()
            fact = reads.venue.fill(
                "order-1", "fee", D(".4"), D(100), (ActualFee("QUOTE", D(5), None),)
            )
            reads.venue.trades[-1] = replace(fact, sequence=None)
            reads.venue.wire_trades["fee"]["timestamp"] = 100
            assert not await observe(h, reads)
            assert (await h.current()).quote_cash == 955
            assert h.executor.last_error == "actual fees exceed the reserved forecast"
            h.advance()
            assert not await observe(h, reads)
            assert not await h.service() and not await h.send()
            assert (await h.current()).quote_cash == 955

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["bind", "event"])
@pytest.mark.parametrize("boundary", ["after_posting", "before_commit", "after_commit"])
def test_sigkill_during_account_observation_posting_recovers_without_duplicate_fills(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, kind: str, boundary: str
) -> None:
    import json
    import signal
    import subprocess
    import sys
    from dataclasses import astuple

    from grid.execution.client_ids import MappedSyntheticExchange, normalize
    from grid.persistence.records import ClientIdPolicy

    path = tmp_path / "ledger.db"
    payload = {
        "path": str(path),
        "scope": codec.dumps(state.scope),
        "context": codec.dumps(context),
        "bounds": astuple(bounds),
        "sources": sorted(SOURCES),
        "limits": astuple(LIMITS),
        "boundary": kind + ":" + boundary,
        "mapped": True,
        "account_reads": True,
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("crash_child.py"))],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr

    async def run() -> None:
        writer = await AsyncWriter.open(path, bounds, SOURCES, mock_storage=True)
        venue = MappedSyntheticExchange(100, writer, ClientIdPolicy("mock-prefix-v1", "t-"))
        executor = MockExecutor(writer, venue, (state.scope,), LIMITS, EVIDENCE)
        try:
            values = json.loads(path.with_suffix(".receipt").read_text())
            wire = {
                "symbol": state.scope.symbol,
                "clientOrderId": values["wire_client_id"],
                "id": values["external_id"],
                "type": "limit",
                "side": "buy",
                "price": values["price"],
                "amount": values["amount"],
                "status": "open",
                "filled": "0",
            }
            fact = await writer.submit(lambda ledger: normalize(ledger, state.scope, wire))
            venue.orders[fact.external_id] = fact
            venue.wire_orders[fact.external_id] = wire
            venue.sent.append(fact.request)
            fill(venue, "order-1", "fill", "1", "100", 100)
            reads = LookupReads(venue)
            committed = kind == "event" and boundary == "after_commit"
            before = await writer.submit(lambda ledger: ledger.snapshot(state.scope))
            assert before.state is not None and before.state.quote_cash == (
                900 if committed else 1000
            )
            assert await writer.submit(
                lambda ledger: ledger.db.execute(
                    "SELECT count(*) FROM execution_positions"
                ).fetchone()
            ) == (int(committed),)
            assert not await executor.service(
                state.scope, context, account_reads=reads, lookup=reads
            )
            assert not await executor.service(
                state.scope, context, account_reads=reads, lookup=reads
            )
            after = await writer.submit(lambda ledger: ledger.snapshot(state.scope))
            assert (
                after.state is not None
                and after.state.quote_cash == 900
                and after.state.owned_base == 1
            )
            assert await writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
            ) == (1,)
            assert await writer.submit(
                lambda ledger: ledger.execution_position(state.scope)
            ) == ExecutionPosition("timestamp", 100)
            assert not await executor.dispatch(state.scope, context) and len(venue.sent) == 1
        finally:
            await executor.close()
            await writer.close()

    asyncio.run(run())


def test_fill_during_cancel_keeps_one_sell_until_terminal_account_evidence(
    tmp_path: Path, bounds: Bounds, state: State, context: Context
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = LookupReads(original.venue)
            assert await h.service() and await h.send()
            fill(reads.venue, "order-1", "seed", "1", "100", 100)
            assert await h.service()
            for _ in range(2):
                assert await h.send() and await h.service()
            fill(reads.venue, "order-3", "buy", ".1", "90", 101)
            assert not await observe(h, reads)
            assert await h.service() and await h.send()  # Request cancellation of the old SELL.
            fill(reads.venue, "order-2", "late-sell", ".4", "110", 102)
            assert not await observe(h, reads) and not await h.send()
            assert (await h.current()).owned_base == D(".7")
            assert str(reads.venue.orders["order-2"].status) == "open"
            reads.venue.settle_cancel("order-2")
            h.advance()
            assert not await observe(h, reads) and not await h.send()
            assert reads.venue.orders["order-2"].status == "canceled"
            assert await h.service() and await h.send()
            assert (
                len(
                    [
                        row
                        for row in reads.venue.orders.values()
                        if row.status == "open" and str(row.request.side) == "sell"
                    ]
                )
                == 1
            )
            assert (await h.current()).owned_base == D(".7")

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [True, False])
def test_interrupted_account_reads_leave_no_partial_financial_posting_or_send_permission(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    cancel: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        async with setup(tmp_path / "ledger.db", bounds, state, context) as (h, original):
            reads = LookupReads(original.venue)
            assert await h.service() and await h.send()
            fill(reads.venue, "order-1", "fill", "1", "100", 100)
            entered = asyncio.Event()

            async def stall(symbol: str, since: None, limit: int) -> object:
                assert not await h.writer.submit(lambda ledger: ledger.db.in_transaction)
                entered.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

            monkeypatch.setattr(reads, "fetch_my_trades", stall)
            h.executor.limits = replace(LIMITS, timeout_ms=1000 if cancel else 30)
            task = asyncio.create_task(observe(h, reads))
            await entered.wait()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                assert not await task
            assert not h.executor._lock.locked() and not await h.send()
            assert (await h.current()).quote_cash == 1000
            assert await h.writer.submit(lambda ledger: ledger.binding(h.scope, 1)) is None

    asyncio.run(run())
