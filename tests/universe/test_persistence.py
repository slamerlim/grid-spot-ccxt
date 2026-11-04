import json
import signal
import sqlite3
import subprocess
import sys
from dataclasses import astuple, replace
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.domain.model import Context, Mode, State
from grid.persistence import codec, schema
from grid.persistence.ledger import Ledger
from grid.persistence.records import Backup, Bounds, Conflict, LedgerError
from grid.persistence.storage import restore, semantic_hash, verify_schema
from grid.persistence.universe import commit, current
from grid.universe.model import UniversePolicy


@pytest.mark.parametrize("boundary", ["after_posting", "before_commit", "after_commit"])
def test_sigkill_policy_and_retirement_commit_together(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    policy: UniversePolicy,
    boundary: str,
) -> None:
    path = tmp_path / "crash.db"
    payload = {
        "path": str(path),
        "scope": codec.dumps(state.scope),
        "context": codec.dumps(context),
        "policy": codec.dumps(policy),
        "bounds": astuple(bounds),
        "sources": sorted(SOURCES),
        "boundary": "universe:" + boundary,
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
    ledger = Ledger(path, bounds, SOURCES, mock_storage=True)
    try:
        committed = boundary == "after_commit"
        snapshot = ledger.snapshot(state.scope)
        assert snapshot.state is not None
        assert snapshot.state.quote_cash == 1000 and snapshot.state.reconcile_reason is not None
        assert (snapshot.state.mode is Mode.RETIRING) == committed
        assert snapshot.state.orders[0].terminal == committed
        assert (current(ledger) is not None) == committed
        assert (ledger.lookup("retire") is not None) == committed
        ledger.storage.integrity()
    finally:
        ledger.close()


def test_retirement_backup_restore_keeps_inventory_policy_and_audit(
    funded: Ledger,
    state: State,
    context: Context,
    policy: UniversePolicy,
    bounds: Bounds,
    tmp_path: Path,
) -> None:
    from decimal import Decimal as D

    from grid.domain.model import Fill

    funded.attempt("sent", state.scope, 1, funded.intents(state.scope)[0].id)
    funded.apply("partial", 2, Fill(state.scope, "fill", 1, D("0.5"), D(100), ()), context)
    commit(
        funded,
        "retire",
        0,
        replace(policy, enabled_venues=()),
        (state.scope,),
        "test-owner",
        "disabled with inventory",
        10000,
        lambda previous: (),
    )
    before = current(funded)
    backup = funded.backup(tmp_path / "backup.db")
    restored = restore(backup, tmp_path / "restored.db", bounds, SOURCES, mock_storage=True)
    recovered = Ledger(Path(restored.path), bounds, SOURCES, mock_storage=True)
    try:
        assert current(recovered) == before
        assert recovered.lookup("retire") == funded.lookup("retire")
        snapshot = recovered.snapshot(state.scope)
        assert snapshot.state is not None and snapshot.state.owned_base == D("0.5")
        assert snapshot.state.mode is Mode.RETIRING and snapshot.state.reconcile_reason is not None
        assert len(snapshot.state.working) >= 1
        with pytest.raises(Conflict):
            commit(
                recovered,
                "remove",
                1,
                policy,
                (replace(state.scope, allocation="other"),),
                "test-owner",
                "remove owner",
                10000,
                lambda previous: (),
            )
    finally:
        recovered.close()


def test_v2_backup_migrates_additively_and_preserves_financial_identity(
    funded: Ledger, state: State, bounds: Bounds, tmp_path: Path
) -> None:
    before = funded.snapshot(state.scope)
    backup = funded.backup(tmp_path / "v2.db")
    raw = sqlite3.connect(backup.path, autocommit=True)
    try:
        raw.execute("DROP TABLE universe_revisions")
        raw.execute("DROP TABLE portfolio_revisions")
        raw.execute("DROP TABLE portfolio_flows")
        raw.execute("DELETE FROM migrations WHERE version>=3")
        raw.execute("PRAGMA user_version=2")
        verify_schema(raw, 2)
        digest = semantic_hash(raw)
        raw.execute("UPDATE metadata SET value=? WHERE key='backup_hash'", (digest,))
    finally:
        raw.close()
    restored = restore(
        Backup(backup.path, digest, backup.request_count),
        tmp_path / "migrated.db",
        bounds,
        SOURCES,
        mock_storage=True,
    )
    ledger = Ledger(Path(restored.path), bounds, SOURCES, mock_storage=True)
    try:
        assert current(ledger) is None
        assert ledger.db.execute("PRAGMA user_version").fetchone() == (schema.VERSION,)
        after = ledger.snapshot(state.scope)
        assert after.state is not None and before.state is not None
        assert (
            after.state.orders == before.state.orders
            and after.state.quote_cash == before.state.quote_cash
        )
        assert ledger.lookup("seed") == funded.lookup("seed")
        verify_schema(ledger.db)
    finally:
        ledger.close()


def test_corrupt_universe_refuses_startup(
    funded: Ledger, state: State, policy: UniversePolicy, bounds: Bounds
) -> None:
    commit(
        funded,
        "retire",
        0,
        policy,
        (state.scope,),
        "test-owner",
        "retire",
        10000,
        lambda previous: (),
    )
    funded.db.execute("UPDATE universe_revisions SET payload='{}'")
    path = funded.storage.path
    funded.close()
    with pytest.raises((ValueError, LedgerError)):
        Ledger(path, bounds, SOURCES, mock_storage=True)
