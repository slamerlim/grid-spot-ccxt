"""Runtime, ownership, durable schema, strict decoding and fault refusal."""

import os
import sqlite3
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from grid.domain.model import Context, State
from grid.domain.numbers import DomainError
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds, LedgerError
from grid.persistence.storage import check_runtime, no_fault

from .conftest import SOURCE_ID, SOURCES


@pytest.mark.parametrize("number", ["0", "-1.001", "1E-96", "9" * 96, "1E+96", "0.00000001"])
def test_decimal_roundtrip_is_canonical_and_exact(number: str) -> None:
    value = D(number)
    assert codec.parse_amount(codec.amount(value)) == value
    assert codec.loads(codec.dumps(value), D) == value


@pytest.mark.parametrize("bad", ["1.0", "1e2", "NaN", "Infinity", "-0", "01", " 1", 1.5, None])
def test_decimal_rejects_noncanonical_storage(bad: object) -> None:
    with pytest.raises(DomainError):
        codec.parse_amount(bad)


def test_typed_snapshot_roundtrip_rejects_type_or_field_corruption(
    state: State, context: Context
) -> None:
    assert codec.loads(codec.dumps(state), State) == state
    assert codec.loads(codec.dumps(context), Context) == context
    for broken in (
        codec.dumps(state).replace('"next_id":1', '"next_id":true'),
        codec.dumps(state).replace('"next_id":1', '"next_id":1.0'),
        codec.dumps(state).replace('"next_id":1', '"next_id":1,"next_id":2'),
        codec.dumps(state).replace('"type":"State"', '"type":"Unknown"'),
        codec.dumps(state).replace('"next_id":1,', ""),
        codec.dumps(state) + " ",
    ):
        with pytest.raises(DomainError):
            codec.loads(broken, State)
    with pytest.raises(DomainError):
        codec.dumps(1.5)
    with pytest.raises(DomainError):
        codec.loads(codec.dumps(state), State, limit=16)


@pytest.mark.parametrize("version", [(3, 45, 1), (3, 51, 2), (3, 50, 6), (3, 44, 5)])
def test_unpatched_runtime_is_refused(version: tuple[int, ...]) -> None:
    with pytest.raises(LedgerError, match="patched"):
        check_runtime(version, SOURCE_ID, SOURCES)


def test_version_is_insufficient_without_exact_build_approval() -> None:
    with pytest.raises(LedgerError):
        check_runtime((3, 53, 1), "unapproved", SOURCES)
    for version in ((3, 53, 1), (3, 50, 7), (3, 44, 6)):
        check_runtime(version, SOURCE_ID, SOURCES)


def test_storage_reads_back_pragmas_and_owner_only_files(ledger: Ledger) -> None:
    ledger.storage.check_pragmas()
    for path in ledger.storage.path.parent.iterdir():
        assert path.stat().st_mode & 0o077 == 0
    assert ledger.db.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert ledger.db.execute("PRAGMA synchronous").fetchone() == (2,)
    ledger.db.execute("PRAGMA foreign_keys=OFF")
    with pytest.raises(LedgerError, match="pragma"):
        ledger.storage.check_pragmas()
    ledger.db.execute("PRAGMA foreign_keys=ON")


def test_same_process_and_parent_alias_cannot_acquire_owner(
    ledger: Ledger, bounds: Bounds, tmp_path: Path
) -> None:
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    for path in (ledger.storage.path, alias / "ledger.db"):
        with pytest.raises(LedgerError, match="controller"):
            Ledger(path, bounds, SOURCES, mock_storage=True)
    linked = tmp_path / "hardlink.db"
    os.link(ledger.storage.path, linked)
    with pytest.raises(LedgerError, match="aliases"):
        Ledger(linked, bounds, SOURCES, mock_storage=True)
    linked.unlink()
    alias.unlink()


def test_runtime_storage_permissions_and_schema_fail_closed(
    tmp_path: Path, bounds: Bounds, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr("grid.persistence.storage.filesystem", lambda path: "nfs")
        with pytest.raises(LedgerError, match="filesystem"):
            Ledger(tmp_path / "remote.db", bounds, SOURCES, mock_storage=True)
    assert not (tmp_path / "remote.db").exists()
    with pytest.raises(LedgerError, match="approved"):
        Ledger(tmp_path / "unapproved.db", bounds, frozenset(), mock_storage=True)
    tmp_path.chmod(0o755)
    with pytest.raises(LedgerError, match="owner-only"):
        Ledger(tmp_path / "public.db", bounds, SOURCES, mock_storage=True)
    tmp_path.chmod(0o700)
    damaged = tmp_path / "damaged.db"
    damaged.write_bytes(b"not a SQLite database")
    damaged.chmod(0o600)
    with pytest.raises(sqlite3.DatabaseError):
        Ledger(damaged, bounds, SOURCES, mock_storage=True)


@pytest.mark.parametrize(
    "damage",
    [
        "UPDATE migrations SET checksum='wrong'",
        "PRAGMA user_version=999",
        "DROP INDEX one_unresolved_sell",
        "ALTER TABLE pools ADD COLUMN unchecked TEXT",
    ],
)
def test_schema_checksum_and_constraints_are_verified(
    ledger: Ledger, bounds: Bounds, damage: str
) -> None:
    path = ledger.storage.path
    ledger.db.execute(damage)
    ledger.close()
    with pytest.raises(LedgerError, match="schema"):
        Ledger(path, bounds, SOURCES, mock_storage=True)


def test_storage_error_rolls_back_and_lost_ack_remains_queryable(
    ledger: Ledger, state: State
) -> None:
    from grid.domain.market import Money

    def fail_before(stage: str) -> None:
        if stage == "credit:before_commit":
            raise OSError("injected I/O failure")

    ledger.storage.fault = fail_before
    with pytest.raises(OSError):
        ledger.credit("credit", state.scope.venue, state.scope.account, Money("QUOTE", D(100)))
    assert ledger.lookup("credit") is None
    assert ledger.pool(state.scope.venue, state.scope.account, "QUOTE") == 0

    def fail_after(stage: str) -> None:
        if stage == "credit:after_commit":
            raise OSError("injected lost acknowledgement")

    ledger.storage.fault = fail_after
    with pytest.raises(OSError):
        ledger.credit("credit", state.scope.venue, state.scope.account, Money("QUOTE", D(100)))
    assert ledger.lookup("credit") is not None
    ledger.storage.fault = no_fault
    ledger.credit("credit", state.scope.venue, state.scope.account, Money("QUOTE", D(100)))
    assert ledger.pool(state.scope.venue, state.scope.account, "QUOTE") == 100


def test_bounded_integer_configuration() -> None:
    with pytest.raises(DomainError):
        replace(Bounds(50, 2, 4096, 10, 16, 1, 1000), queue_size=0)
