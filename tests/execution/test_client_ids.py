"""Mapped mock reports run through the real executor and committed identity lookup."""

import asyncio
import json
import signal
import subprocess
import sys
from dataclasses import astuple, replace
from decimal import Decimal as D
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from persistence.conftest import SOURCES

from grid.domain.model import Context, Side, State
from grid.execution.client_ids import MappedSyntheticExchange, normalize
from grid.execution.executor import MockExecutor
from grid.execution.models import ExecutionError
from grid.persistence import client_ids, codec
from grid.persistence.records import Bounds, ClientIdPolicy
from grid.persistence.storage import no_fault
from grid.persistence.writer import AsyncWriter

from .helpers import EVIDENCE, LIMITS, ZERO_FEES, harness

POLICY = ClientIdPolicy("mock-prefix-v1", "t-")


def test_mapped_seed_ladder_and_fill_replay_keep_original_financial_identity(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, async_sockets: None
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            await h.executor.close()
            venue = MappedSyntheticExchange(100, h.writer, POLICY)
            executor = MockExecutor(h.writer, venue, (h.scope,), LIMITS, EVIDENCE)
            h.venue, h.executor = venue, executor
            try:
                await h.seed()
                for _ in range(4):
                    assert await h.send() and await h.service(), executor.last_error
                assert len(venue.sent) == len(venue.wire_orders) == 5
                for external, wire in venue.wire_orders.items():
                    assert len(venue.submitted_clients[external]) == 26
                    assert wire["clientOrderId"] == "t-" + venue.submitted_clients[external]
                    assert wire["type"] == "limit"
                    normalized = await h.writer.submit(partial(normalize, scope=h.scope, wire=wire))
                    assert len(normalized.request.client_id) == 64
                before = await h.current()
                for _ in range(3):
                    assert await h.service()
                after = await h.current()
                assert (after.quote_cash, after.owned_base, after.realized_pnl) == (
                    before.quote_cash,
                    before.owned_base,
                    before.realized_pnl,
                )
                assert (
                    after.owned_base == 1 and sum(o.side is Side.SELL for o in after.working) == 1
                )
                assert (
                    await h.writer.submit(
                        lambda ledger: ledger.db.execute(
                            "SELECT count(*) FROM client_ids"
                        ).fetchone()[0]
                    )
                    == 5
                )
                lower = venue.sent[2]
                venue.fill("order-3", "lower", D(lower.amount), D(lower.price), ZERO_FEES)
                assert await h.service() and await h.send()
                venue.fill("order-2", "partial-sell", D("0.25"), D(110), ZERO_FEES)
                assert not await h.service() and not await h.send()
                venue.settle_cancel("order-2")
                h.advance()
                assert await h.service() and await h.send()
                assert venue.sent[-1].side is Side.SELL and venue.sent[-1].amount == "1.75"
                assert await h.service()
                final = await h.current()
                assert final.quote_cash + final.position_cost == 1000 + final.realized_pnl
                assert sum(o.side is Side.SELL for o in final.working) == 1
                assert venue.cancels == ["order-2"]
            finally:
                await executor.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "boundary",
    [
        "client-id:after_posting",
        "client-id:before_commit",
        "client-id:after_commit",
        "executor:after_client_id",
        "attempt:before_commit",
        "attempt:after_commit",
        "executor:after_claim",
        "fake:after_effect",
        "executor:after_send",
    ],
)
def test_sigkill_mapped_submit_recovers_by_only_the_returned_wire_identity(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    boundary: str,
) -> None:
    path = tmp_path / "killed.db"
    data = {
        "path": str(path),
        "scope": codec.dumps(state.scope),
        "context": codec.dumps(context),
        "bounds": astuple(bounds),
        "sources": sorted(SOURCES),
        "limits": astuple(LIMITS),
        "boundary": boundary,
        "mapped": True,
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("crash_child.py"))],
        input=json.dumps(data),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    mapped = boundary not in {"client-id:after_posting", "client-id:before_commit"}
    claimed = boundary in {
        "attempt:after_commit",
        "executor:after_claim",
        "fake:after_effect",
        "executor:after_send",
    }
    receipt = path.with_suffix(".receipt")
    effect = receipt.exists()
    assert effect is (boundary in {"fake:after_effect", "executor:after_send"})

    async def recover() -> None:
        writer = await AsyncWriter.open(path, bounds, SOURCES, mock_storage=True)
        venue = MappedSyntheticExchange(100, writer, POLICY)
        executor = MockExecutor(writer, venue, (state.scope,), LIMITS, EVIDENCE)
        try:
            record = await writer.submit(lambda ledger: client_ids.lookup(ledger, state.scope, 1))
            assert (record is not None) is mapped
            if effect:
                values = json.loads(receipt.read_text())
                assert "order_id" not in values and "client_id" not in values
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
                venue.submitted_clients[fact.external_id] = values["submitted_client_id"]
                venue.sent.append(fact.request)
                venue.revision = 1
            assert not await executor.dispatch(state.scope, context)
            assert await executor.service(state.scope, context) is (not claimed or effect)
            assert await executor.dispatch(state.scope, context) is (not claimed)
            assert len(venue.sent) == (1 if effect or not claimed else 0)
            for _ in range(3):
                assert not await executor.dispatch(state.scope, context)
            current = await writer.submit(lambda ledger: client_ids.lookup(ledger, state.scope, 1))
            assert current is not None
            if record is not None:
                assert current == record
            snapshot = await writer.submit(lambda ledger: ledger.snapshot(state.scope))
            assert snapshot.state is not None
            assert snapshot.state.quote_cash == 1000 and snapshot.state.owned_base == 0
        finally:
            await executor.close()
            await writer.close()

    asyncio.run(recover())


@pytest.mark.parametrize(
    "fault", ["id", "scope", "symbol", "type", "side", "price", "float", "amount"]
)
def test_changed_wire_fact_cannot_bind_or_post(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, async_sockets: None, fault: str
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            await h.executor.close()
            venue = MappedSyntheticExchange(100, h.writer, POLICY)
            executor = MockExecutor(h.writer, venue, (h.scope,), LIMITS, EVIDENCE)
            try:
                assert await executor.service(h.scope, context)
                assert await executor.dispatch(h.scope, context)
                wire = venue.wire_orders["order-1"]
                changes: dict[str, Any] = {
                    "id": {"clientOrderId": "unregistered"},
                    "symbol": {"symbol": "OTHER/QUOTE"},
                    "type": {"type": "market"},
                    "side": {"side": "sell"},
                    "price": {"price": "101"},
                    "float": {"price": 100.0},
                    "amount": {"amount": "2"},
                }
                if fault == "scope":
                    with pytest.raises(ExecutionError):
                        await h.writer.submit(
                            lambda ledger: normalize(
                                ledger, replace(h.scope, account="other"), wire
                            )
                        )
                else:
                    venue.wire_orders["order-1"] = wire | changes[fault]
                    assert not await executor.service(h.scope, context)
                    assert not await executor.dispatch(h.scope, context)
                assert await h.writer.submit(lambda ledger: ledger.binding(h.scope, 1)) is None
                after = await h.current()
                assert after.quote_cash == 1000 and after.owned_base == 0 and len(venue.sent) == 1
            finally:
                await executor.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "boundary", ["executor:after_client_id", "executor:after_claim", "executor:after_send"]
)
def test_restart_reconciles_mapped_identity_without_blind_resend(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    boundary: str,
) -> None:
    async def run() -> None:
        path = tmp_path / "ledger.db"
        async with harness(path, bounds, state, context) as h:
            await h.executor.close()
            venue = MappedSyntheticExchange(100, h.writer, POLICY)

            def fail(stage: str) -> None:
                if stage == boundary:
                    raise OSError("injected interrupted boundary")

            executor = MockExecutor(h.writer, venue, (h.scope,), LIMITS, EVIDENCE, fault=fail)
            try:
                assert await executor.service(h.scope, context)
                assert not await executor.dispatch(h.scope, context)
                record = await h.writer.submit(lambda ledger: client_ids.lookup(ledger, h.scope, 1))
                assert record is not None
            finally:
                executor.fault = no_fault
                await executor.close()
        writer = await AsyncWriter.open(path, bounds, SOURCES, mock_storage=True)
        venue.writer = writer  # The fake exchange retained its external state across owner restart.
        recovered = MockExecutor(writer, venue, (state.scope,), LIMITS, EVIDENCE)
        try:
            assert not await recovered.dispatch(state.scope, context)
            known = await recovered.service(state.scope, context)
            assert known is (boundary != "executor:after_claim"), recovered.last_error
            sent = await recovered.dispatch(state.scope, context)
            assert sent is (boundary == "executor:after_client_id")
            assert len(venue.sent) == (0 if boundary == "executor:after_claim" else 1)
            for _ in range(3):
                assert not await recovered.dispatch(state.scope, context)
            assert (
                await writer.submit(lambda ledger: client_ids.lookup(ledger, state.scope, 1))
                == record
            )
            if venue.sent:
                venue.fill("order-1", "late-seed", D(1), D(100), ZERO_FEES)
                assert await recovered.service(state.scope, context)
                snapshot = await writer.submit(lambda ledger: ledger.snapshot(state.scope))
                assert snapshot.state is not None and snapshot.state.owned_base == 1
        finally:
            await recovered.close()
            await writer.close()

    asyncio.run(run())
