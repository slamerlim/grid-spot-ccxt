"""Real process death and SQLite BUSY/FULL/corruption, without external trading."""

import json
import signal
import sqlite3
import subprocess
import sys
from dataclasses import astuple, replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from grid.domain.market import Money
from grid.domain.model import Context, State
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds, LedgerError

from .conftest import SOURCES
from .test_ledger import current


@pytest.mark.parametrize("action", ["credit", "start", "event", "release", "attempt", "compact"])
@pytest.mark.parametrize("boundary", ["after_posting", "before_commit", "after_commit"])
def test_sigkill_at_every_financial_commit_is_all_or_nothing(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    action: str,
    boundary: str,
) -> None:
    path = tmp_path / "crash.db"
    payload = {
        "path": str(path),
        "scope": codec.dumps(state.scope),
        "context": codec.dumps(context),
        "bounds": astuple(bounds),
        "sources": sorted(SOURCES),
        "action": action,
        "boundary": f"{action}:{boundary}",
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
    committed = boundary == "after_commit"
    recovered = Ledger(path, bounds, SOURCES, mock_storage=True)
    try:
        assert (recovered.lookup("subject") is not None) == committed
        recovered.storage.integrity()
        if action == "credit":
            assert recovered.pool(state.scope.venue, state.scope.account, "QUOTE") == (
                100 if committed else 0
            )
        elif action == "start" and not committed:
            assert recovered.db.execute("SELECT count(*) FROM allocations").fetchone() == (0,)
            assert recovered.pool(state.scope.venue, state.scope.account, "QUOTE") == 1000
        else:
            restored = current(recovered.snapshot(state.scope))
            assert restored.reconcile_reason is not None
            if action == "event":
                assert restored.quote_cash == (D("949.9") if committed else 1000)
                assert restored.owned_base == (D("0.499") if committed else 0)
                assert restored.position_cost == (D("50.3") if committed else 0)
                assert restored.fee_balances == (Money("TOKEN", D("9.99") if committed else D(10)),)
                assert recovered.db.execute("SELECT count(*) FROM fees").fetchone() == (
                    3 if committed else 0,
                )
                assert recovered.db.execute("SELECT count(*) FROM fills").fetchone() == (
                    int(committed),
                )
            elif action == "release":
                assert restored.quote_cash == (100 if committed else 1000)
                assert recovered.pool(state.scope.venue, state.scope.account, "QUOTE") == (
                    900 if committed else 0
                )
            elif action == "attempt":
                assert restored.orders[0].unknown == committed
                assert (
                    any(intent.status == "uncertain" for intent in recovered.intents(state.scope))
                    == committed
                )
            elif action == "compact":
                assert bool(restored.fills) != committed
                assert restored.quote_cash == 900 and restored.owned_base == 1
            recovered._verify_projection(codec.digest(state.scope), restored, context)
    finally:
        recovered.close()


@pytest.mark.parametrize("boundary", ["fake:before_effect", "fake:after_effect"])
def test_sigkill_around_fake_send_never_makes_unknown_intent_unsent(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, boundary: str
) -> None:
    path = tmp_path / "fake.db"
    payload = {
        "path": str(path),
        "scope": codec.dumps(state.scope),
        "context": codec.dumps(context),
        "bounds": astuple(bounds),
        "sources": sorted(SOURCES),
        "action": "attempt",
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
    assert path.with_name("fake-effect").exists() == (boundary == "fake:after_effect")
    recovered = Ledger(path, bounds, SOURCES, mock_storage=True)
    try:
        assert recovered.lookup("subject") is not None
        assert current(recovered.snapshot(state.scope)).orders[0].unknown
        assert any(intent.status == "uncertain" for intent in recovered.intents(state.scope))
        assert recovered.db.execute("SELECT count(*) FROM orders").fetchone() == (1,)
    finally:
        recovered.close()


def test_second_process_cannot_acquire_locked_path(ledger: Ledger, bounds: Bounds) -> None:
    script = (
        "import sys; from pathlib import Path; from grid.persistence.ledger import Ledger; "
        "from grid.persistence.records import Bounds; "
        "Ledger(Path(sys.argv[1]),Bounds(50,2,4194304,100,256,8,5000),"
        "frozenset([sys.argv[2]]),mock_storage=True)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(ledger.storage.path), next(iter(SOURCES))],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode != 0 and "another controller" in result.stderr


def test_old_runtime_refuses_before_database_creation(
    tmp_path: Path,
    bounds: Bounds,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("grid.persistence.storage.sqlite3.sqlite_version_info", (3, 45, 1))
    with pytest.raises(LedgerError, match="unpatched linked SQLite"):
        Ledger(tmp_path / "old.db", bounds, SOURCES, mock_storage=True)
    assert not (tmp_path / "old.db").exists()


def test_real_sqlite_busy_does_not_ack_or_retry(ledger: Ledger, state: State) -> None:
    other = sqlite3.connect(ledger.storage.path, autocommit=True)
    try:
        other.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError) as failure:
            ledger.credit("busy", state.scope.venue, state.scope.account, Money("QUOTE", D(100)))
        assert failure.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
        assert ledger.lookup("busy") is None
        other.execute("ROLLBACK")
        ledger.credit("busy", state.scope.venue, state.scope.account, Money("QUOTE", D(100)))
        assert ledger.pool(state.scope.venue, state.scope.account, "QUOTE") == 100
    finally:
        other.close()


def test_real_sqlite_full_rolls_back_instead_of_acknowledging(ledger: Ledger, state: State) -> None:
    pages = int(ledger.db.execute("PRAGMA page_count").fetchone()[0])
    ledger.db.execute(f"PRAGMA max_page_count={pages}")
    # Bound the database itself, not the host disk. Stop on the first refused commit.
    failed = False
    for index in range(100):
        request = f"fund-{index}"
        try:
            ledger.credit(request, state.scope.venue, state.scope.account, Money("QUOTE", D(1)))
        except sqlite3.OperationalError as failure:
            assert failure.sqlite_errorcode == sqlite3.SQLITE_FULL
            assert ledger.lookup(request) is None
            assert ledger.pool(state.scope.venue, state.scope.account, "QUOTE") == index
            failed = True
            break
    assert failed
    ledger.db.execute("PRAGMA max_page_count=10000")
    ledger.storage.integrity()


def test_pinned_reader_hits_wal_bound_then_requires_explicit_recovery(
    ledger: Ledger, state: State
) -> None:
    ledger.storage.checkpoint()
    ledger.storage.bounds = replace(ledger.bounds, wal_pages=16)
    ledger.db.execute("PRAGMA wal_autocheckpoint=16")
    reader = sqlite3.connect(ledger.storage.path, autocommit=True)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM pools").fetchall()
        refused = False
        for index in range(100):
            try:
                ledger.credit(
                    f"fund-{index}", state.scope.venue, state.scope.account, Money("QUOTE", D(1))
                )
            except LedgerError as exc:
                assert "WAL bound" in str(exc)
                assert ledger.lookup(f"fund-{index}") is None
                refused = True
                break
        assert refused
        reader.execute("ROLLBACK")
        ledger.storage.checkpoint()
    finally:
        reader.close()


@pytest.mark.parametrize(
    "damage",
    [
        "UPDATE balances SET amount='999' WHERE currency='QUOTE'",
        "UPDATE positions SET cost='1.5'",
        "UPDATE reservations SET principal='0'",
        "UPDATE orders SET payload='{}'",
    ],
)
def test_projection_corruption_refuses_recovery(
    funded: Ledger, bounds: Bounds, damage: str
) -> None:
    path = funded.storage.path
    funded.db.execute(damage)
    funded.close()
    with pytest.raises(LedgerError, match="projection"):
        Ledger(path, bounds, SOURCES, mock_storage=True)
