import asyncio
import json
import signal
import subprocess
import sys
from dataclasses import astuple
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.domain.model import Context, Side, State
from grid.execution.executor import MockExecutor
from grid.execution.models import LimitRequest
from grid.execution.synthetic import SyntheticExchange
from grid.persistence import codec
from grid.persistence.records import Bounds
from grid.persistence.writer import AsyncWriter

from .helpers import EVIDENCE, LIMITS, ZERO_FEES


@pytest.mark.parametrize(
    "boundary",
    [
        "executor:before_claim",
        "attempt:after_posting",
        "attempt:before_commit",
        "attempt:after_commit",
        "executor:after_claim",
        "fake:before_effect",
        "fake:after_effect",
        "executor:after_send",
    ],
)
def test_sigkill_around_executor_claim_and_effect_never_duplicates(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    boundary: str,
) -> None:
    path = tmp_path / "ledger.db"
    payload = {
        "path": str(path),
        "scope": codec.dumps(state.scope),
        "context": codec.dumps(context),
        "bounds": astuple(bounds),
        "sources": sorted(SOURCES),
        "limits": astuple(LIMITS),
        "boundary": boundary,
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
    claim = boundary not in {
        "executor:before_claim",
        "attempt:after_posting",
        "attempt:before_commit",
    }
    receipt = path.with_suffix(".receipt")
    effect = receipt.exists()
    assert effect == (boundary in {"fake:after_effect", "executor:after_send"})

    async def run() -> None:
        venue = SyntheticExchange(100)
        if effect:
            values = json.loads(receipt.read_text())
            await venue.submit_limit(
                LimitRequest(
                    state.scope,
                    values["order_id"],
                    values["client_id"],
                    Side.BUY,
                    values["amount"],
                    values["price"],
                    context.config_revision,
                    context.market.generation,
                )
            )
        writer = await AsyncWriter.open(path, bounds, SOURCES, mock_storage=True)
        executor = MockExecutor(writer, venue, (state.scope,), LIMITS, EVIDENCE)
        try:
            assert not await executor.dispatch(state.scope, context)
            recovered = await executor.service(state.scope, context)
            assert recovered == (not claim or effect), executor.last_error
            sent = await executor.dispatch(state.scope, context)
            assert sent == (not claim)
            assert len(venue.sent) == (1 if effect or not claim else 0)
            for _ in range(3):
                assert not await executor.dispatch(state.scope, context)
            snapshot = await writer.submit(lambda ledger: ledger.snapshot(state.scope))
            assert snapshot.state is not None
            assert snapshot.state.quote_cash == 1000 and snapshot.state.owned_base == 0
        finally:
            await executor.close()
            await writer.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["bind", "event"])
@pytest.mark.parametrize("boundary", ["after_posting", "before_commit", "after_commit"])
def test_sigkill_during_identity_or_fill_posting_recovers_exactly_once(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    kind: str,
    boundary: str,
) -> None:
    path = tmp_path / "ledger.db"
    payload = {
        "path": str(path),
        "scope": codec.dumps(state.scope),
        "context": codec.dumps(context),
        "bounds": astuple(bounds),
        "sources": sorted(SOURCES),
        "limits": astuple(LIMITS),
        "boundary": kind + ":" + boundary,
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
        values = json.loads(path.with_suffix(".receipt").read_text())
        venue = SyntheticExchange(100)
        await venue.submit_limit(
            LimitRequest(
                state.scope,
                values["order_id"],
                values["client_id"],
                Side.BUY,
                values["amount"],
                values["price"],
                context.config_revision,
                context.market.generation,
            )
        )
        from decimal import Decimal

        venue.fill("order-1", "fill", Decimal(1), Decimal(100), ZERO_FEES)
        writer = await AsyncWriter.open(path, bounds, SOURCES, mock_storage=True)
        executor = MockExecutor(writer, venue, (state.scope,), LIMITS, EVIDENCE)
        try:
            before = await writer.submit(lambda ledger: ledger.snapshot(state.scope))
            assert before.state is not None
            committed = kind == "event" and boundary == "after_commit"
            assert before.state.quote_cash == (900 if committed else 1000)
            keys = await writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM trade_keys").fetchone()
            )
            assert keys == (int(committed),)
            assert await writer.submit(
                lambda ledger: ledger.db.execute(
                    "SELECT count(*) FROM execution_positions"
                ).fetchone()
            ) == (int(committed),)
            assert await executor.service(state.scope, context), executor.last_error
            assert await executor.service(state.scope, context)
            after = await writer.submit(lambda ledger: ledger.snapshot(state.scope))
            assert after.state is not None
            assert after.state.quote_cash == 900 and after.state.owned_base == 1
            assert len(venue.sent) == 1
            assert await writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()
            ) == (1,)
            assert await writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM trade_keys").fetchone()
            ) == (1,)
            assert await writer.submit(
                lambda ledger: ledger.db.execute(
                    "SELECT kind,value FROM execution_positions"
                ).fetchone()
            ) == ("sequence", 1)
        finally:
            await executor.close()
            await writer.close()

    asyncio.run(run())
