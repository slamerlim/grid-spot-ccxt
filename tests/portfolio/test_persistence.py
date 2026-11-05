import asyncio
import json
import signal
import sqlite3
import subprocess
import sys
from dataclasses import astuple, replace
from decimal import Decimal as D
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.domain.model import Context, Mode, State
from grid.persistence import codec, schema
from grid.persistence.ledger import Ledger
from grid.persistence.records import Backup, Bounds, LedgerError
from grid.persistence.storage import restore, semantic_hash, verify_schema
from grid.portfolio.model import CyclePhase, PoolKey, PortfolioPolicy

from .helpers import harness


@pytest.mark.parametrize("boundary", ["after_posting", "before_commit", "after_commit"])
@pytest.mark.parametrize("operation", ["begin", "latch", "plan", "fill", "complete", "cashflow"])
def test_sigkill_preserves_atomic_portfolio_history(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: PortfolioPolicy,
    operation: str,
    boundary: str,
) -> None:
    if operation == "cashflow":
        policy = replace(
            policy,
            pools=(PoolKey(state.scope.venue, state.scope.account, "QUOTE"),),
            cashflow_rule="additive_baseline",
        )
    label = {
        "latch": "portfolio_evaluate",
        "plan": "portfolio_advance",
        "complete": "portfolio_advance",
        "fill": "event",
    }.get(operation, "portfolio_" + operation)
    path = tmp_path / "killed.db"
    payload = {
        "path": str(path),
        "state": codec.dumps(state),
        "context": codec.dumps(context),
        "policy": codec.dumps(policy),
        "bounds": astuple(bounds),
        "operation": operation,
        "boundary": label + ":" + boundary,
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("crash_child.py"))],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=25,
        check=False,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    ledger = Ledger(path, bounds, SOURCES, mock_storage=True)
    try:
        committed = boundary == "after_commit"
        cycle = ledger.portfolio()
        snapshot = ledger.snapshot(state.scope)
        assert snapshot.state is not None and snapshot.state.reconcile_reason is not None
        if operation == "begin":
            assert (cycle is not None) == committed
        else:
            assert cycle is not None
            if operation == "latch":
                assert (cycle.phase is CyclePhase.REQUESTED) == committed
                assert (snapshot.state.mode is Mode.PORTFOLIO_EXIT) == committed
                assert snapshot.state.owned_base == 1 and snapshot.state.quote_cash == 900
                assert (cycle.trigger is not None) == committed
            elif operation == "plan":
                assert cycle.phase is (CyclePhase.EXITING if committed else CyclePhase.REQUESTED)
                assert sum(o.role == "portfolio_exit" for o in snapshot.state.working) == int(
                    committed
                )
            elif operation == "fill":
                assert cycle.phase is CyclePhase.EXITING and cycle.final is None
                assert snapshot.state.owned_base == (0 if committed else 1)
                assert snapshot.state.quote_cash == (1100 if committed else 900)
            elif operation == "complete":
                assert (cycle.phase is CyclePhase.COMPLETE) == committed
                assert (cycle.final is not None) == committed
                assert snapshot.state.owned_base == 0 and snapshot.state.quote_cash == 1100
            else:
                assert cycle.adjustment == (500 if committed else 0)
                assert (
                    ledger.pool(state.scope.venue, state.scope.account, "QUOTE") == cycle.adjustment
                )
                assert bool(cycle.pending_flows) != committed
        ledger.storage.integrity()
    finally:
        ledger.close()


@pytest.mark.usefixtures("async_sockets")
def test_blocked_backup_restore_and_corrupt_cycle_refusal(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: PortfolioPolicy,
) -> None:
    async def scenario() -> None:
        async with harness(tmp_path / "ledger.db", bounds, {state.scope: context}, policy) as h:
            await h.owner.begin("begin", 0, "cycle", policy, "tester", "baseline")
            await h.seed(state.scope)
            await h.trigger()
            h.tick(10001)
            assert await h.service()
            before = await h.advance("blocked")
            assert before.phase is CyclePhase.BLOCKED
            backup = await h.writer.submit(lambda ledger: ledger.backup(tmp_path / "backup.db"))
        restored = restore(backup, tmp_path / "restored.db", bounds, SOURCES, mock_storage=True)
        recovered = Ledger(Path(restored.path), bounds, SOURCES, mock_storage=True)
        assert recovered.portfolio() == before
        snapshot = recovered.snapshot(state.scope)
        assert snapshot.state is not None and snapshot.state.owned_base == 1
        assert snapshot.state.mode is Mode.PORTFOLIO_EXIT and snapshot.state.reconcile_reason
        assert recovered.lookup("blocked") is not None
        recovered.db.execute("UPDATE portfolio_revisions SET payload='{}'")
        recovered.close()
        with pytest.raises((ValueError, LedgerError)):
            Ledger(Path(restored.path), bounds, SOURCES, mock_storage=True)

    asyncio.run(scenario())


def test_v3_backup_additive_migration_preserves_funds_and_requests(
    ledger: Ledger,
    state: State,
    context: Context,
    bounds: Bounds,
    tmp_path: Path,
) -> None:
    from grid.domain.market import Money

    ledger.credit("fund", state.scope.venue, state.scope.account, Money("QUOTE", D(1000)))
    ledger.start("start", state.scope, (Money("QUOTE", D(1000)),), context)
    before = ledger.snapshot(state.scope)
    backup = ledger.backup(tmp_path / "v3.db")
    raw = sqlite3.connect(backup.path, autocommit=True)
    try:
        raw.execute("DROP TABLE portfolio_revisions")
        raw.execute("DROP TABLE portfolio_flows")
        raw.execute("DROP TABLE client_ids")
        raw.execute("DROP TABLE operations_revisions")
        raw.execute("DELETE FROM migrations WHERE version>=4")
        raw.execute("PRAGMA user_version=3")
        verify_schema(raw, 3)
        digest = semantic_hash(raw)
        raw.execute("UPDATE metadata SET value=? WHERE key='backup_hash'", (digest,))
    finally:
        raw.close()
    migrated = restore(
        Backup(backup.path, digest, backup.request_count),
        tmp_path / "migrated.db",
        bounds,
        SOURCES,
        mock_storage=True,
    )
    recovered = Ledger(Path(migrated.path), bounds, SOURCES, mock_storage=True)
    try:
        assert recovered.portfolio() is None
        assert recovered.db.execute("PRAGMA user_version").fetchone() == (schema.VERSION,)
        after = recovered.snapshot(state.scope)
        assert after.state is not None and before.state is not None
        assert (
            after.state.orders == before.state.orders
            and after.state.quote_cash == before.state.quote_cash
        )
        assert recovered.lookup("start") == ledger.lookup("start")
        verify_schema(recovered.db)
    finally:
        recovered.close()
