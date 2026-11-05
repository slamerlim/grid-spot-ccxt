"""Durable external IDs cannot replace, shorten or retroactively invent internal IDs."""

import json
import secrets
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
from grid.persistence import client_ids, codec, schema
from grid.persistence.ledger import Ledger, client_identity
from grid.persistence.records import Backup, Bounds, ClientIdPolicy, Conflict, LedgerError
from grid.persistence.storage import restore, semantic_hash, verify_schema

from .conftest import SOURCES

POLICY = ClientIdPolicy("mock-prefix-v1", "t-")


def test_registration_is_durable_idempotent_and_distinct_from_internal_id(
    funded: Ledger, state: State
) -> None:
    before = funded.snapshot(state.scope)
    record = client_ids.register(funded, "client", state.scope, 1, POLICY)
    assert len(record.submitted_id) == 26 and record.returned_id == "t-" + record.submitted_id
    assert record.internal_id == client_identity(state.scope, 1) and len(record.internal_id) == 64
    assert not funded.attempted(state.scope, 1)
    assert funded.snapshot(state.scope) == before
    assert client_ids.register(funded, "client", state.scope, 1, POLICY) == record
    assert client_ids.register(funded, "client-again", state.scope, 1, POLICY) == record
    assert (
        client_ids.resolve(funded, state.scope.venue, state.scope.account, record.returned_id)
        == record
    )
    assert client_ids.resolve(funded, state.scope.venue, "other", record.returned_id) is None
    assert (
        client_ids.resolve(funded, state.scope.venue, state.scope.account, record.submitted_id)
        is None
    )
    assert (
        client_ids.resolve(funded, state.scope.venue, state.scope.account, record.internal_id)
        is None
    )
    with pytest.raises(Conflict):
        client_ids.register(funded, "policy-change", state.scope, 1, ClientIdPolicy("v2", ""))
    assert funded.lookup("policy-change") is None
    assert funded.db.execute("SELECT count(*) FROM client_ids").fetchone() == (1,)


def test_cannot_register_after_attempt_or_after_unsent_terminal(
    funded: Ledger, state: State
) -> None:
    funded.attempt_once("claim", state.scope, 1, funded.intents(state.scope)[0].id)
    with pytest.raises(Conflict, match="unattempted"):
        client_ids.register(funded, "late", state.scope, 1, POLICY)
    assert client_ids.lookup(funded, state.scope, 1) is None


def test_unmapped_terminal_order_cannot_acquire_an_external_id(
    funded: Ledger, state: State, context: Context
) -> None:
    funded.revoke_unsent("revoke", state.scope, 1, 1, context)
    with pytest.raises(Conflict, match="unattempted"):
        client_ids.register(funded, "late", state.scope, 1, POLICY)
    assert client_ids.lookup(funded, state.scope, 1) is None


@pytest.mark.parametrize("name,prefix", [("", ""), ("Bad Name", ""), ("v1", "t/"), ("v1", "x" * 7)])
def test_policy_has_bounded_literal_fields(name: str, prefix: str) -> None:
    with pytest.raises(LedgerError):
        ClientIdPolicy(name, prefix)


def test_stored_identity_rejects_noncanonical_encoding(funded: Ledger, state: State) -> None:
    record = client_ids.register(funded, "client", state.scope, 1, POLICY)
    invalid = "a" * 25 + "b"  # Nonzero padding bits cannot encode exactly 128 bits.
    with pytest.raises(LedgerError, match="noncanonical"):
        replace(record, submitted_id=invalid, returned_id="t-" + invalid)


def test_terminal_order_keeps_its_registered_identity(
    funded: Ledger, state: State, context: Context
) -> None:
    record = client_ids.register(funded, "client", state.scope, 1, POLICY)
    funded.revoke_unsent("revoke", state.scope, 1, 1, context)
    assert client_ids.register(funded, "retained", state.scope, 1, POLICY) == record
    assert (
        client_ids.resolve(funded, state.scope.venue, state.scope.account, record.returned_id)
        == record
    )


def test_collision_bound_cannot_reassign_another_allocation(
    funded: Ledger, state: State, context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def same(size: int) -> bytes:
        calls.append(size)
        return bytes(16)

    monkeypatch.setattr(secrets, "token_bytes", same)
    first = client_ids.register(funded, "first", state.scope, 1, POLICY)
    other = replace(state.scope, allocation="second")
    funded.credit("fund-other", other.venue, other.account, Money("QUOTE", D(1000)))
    funded.start("start-other", other, (Money("QUOTE", D(1000)),), context)
    with pytest.raises(Conflict, match="collision"):
        client_ids.register(funded, "collision", other, 1, POLICY)
    assert len(calls) == 9 and funded.lookup("collision") is None
    assert client_ids.lookup(funded, other, 1) is None
    assert client_ids.resolve(funded, other.venue, other.account, first.returned_id) == first
    monkeypatch.setattr(secrets, "token_bytes", lambda size: bytes([1]) * size)
    second = client_ids.register(funded, "second", other, 1, POLICY)
    assert second.submitted_id != first.submitted_id


def test_mapping_and_receipt_survive_backup_restore(
    funded: Ledger, state: State, tmp_path: Path, bounds: Bounds
) -> None:
    record = client_ids.register(funded, "client", state.scope, 1, POLICY)
    funded.attempt_once("claim", state.scope, 1, funded.intents(state.scope)[0].id)
    backup = funded.backup(tmp_path / "backup.db")
    restored = restore(backup, tmp_path / "restored.db", bounds, SOURCES, mock_storage=True)
    assert restored.semantic_hash == backup.semantic_hash
    recovered = Ledger(Path(restored.path), bounds, SOURCES, mock_storage=True)
    try:
        assert client_ids.lookup(recovered, state.scope, 1) == record
        assert recovered.lookup("client") == funded.lookup("client")
        assert recovered.attempted(state.scope, 1)
        snapshot = recovered.snapshot(state.scope)
        assert snapshot.state is not None and snapshot.state.reconcile_reason is not None
        with pytest.raises(LedgerError):
            recovered.attempt_once("retry", state.scope, 3, funded.intents(state.scope)[0].id)
    finally:
        recovered.close()


def test_startup_rejects_payload_rewrite_even_when_projection_agrees(
    funded: Ledger, state: State, bounds: Bounds
) -> None:
    record = client_ids.register(funded, "client", state.scope, 1, POLICY)
    changed = replace(record, policy=ClientIdPolicy("rewritten", "t-"))
    funded.db.execute("UPDATE client_ids SET payload=?", (codec.dumps(changed),))
    path = funded.storage.path
    funded.close()
    with pytest.raises(LedgerError, match="receipt"):
        Ledger(path, bounds, SOURCES, mock_storage=True)


def test_v5_backup_migrates_without_rewriting_existing_receipts(
    funded: Ledger, state: State, bounds: Bounds, tmp_path: Path
) -> None:
    receipt = funded.lookup("seed")
    backup = funded.backup(tmp_path / "v5.db")
    raw = sqlite3.connect(backup.path, autocommit=True)
    try:
        raw.execute("DROP TABLE execution_positions")
        raw.execute("DROP TABLE client_ids")
        raw.execute("DELETE FROM migrations WHERE version>=6")
        raw.execute("PRAGMA user_version=5")
        verify_schema(raw, 5)
        digest = semantic_hash(raw)
        raw.execute("UPDATE metadata SET value=? WHERE key='backup_hash'", (digest,))
    finally:
        raw.close()
    old = Backup(backup.path, digest, backup.request_count)
    restored = restore(old, tmp_path / "restored.db", bounds, SOURCES, mock_storage=True)
    upgraded = Ledger(Path(restored.path), bounds, SOURCES, mock_storage=True)
    try:
        verify_schema(upgraded.db, schema.VERSION)
        assert upgraded.lookup("seed") == receipt
        assert client_ids.lookup(upgraded, state.scope, 1) is None
        assert upgraded.db.execute("SELECT count(*) FROM client_ids").fetchone() == (0,)
    finally:
        upgraded.close()


@pytest.mark.parametrize("boundary", ["after_posting", "before_commit", "after_commit"])
def test_sigkill_registration_never_leaves_half_an_identity(
    tmp_path: Path, bounds: Bounds, state: State, context: Context, boundary: str
) -> None:
    path = tmp_path / "crash.db"
    data = {
        "path": str(path),
        "scope": codec.dumps(state.scope),
        "context": codec.dumps(context),
        "bounds": astuple(bounds),
        "sources": sorted(SOURCES),
        "action": "client-id",
        "boundary": "client-id:" + boundary,
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
    recovered = Ledger(path, bounds, SOURCES, mock_storage=True)
    try:
        record = client_ids.lookup(recovered, state.scope, 1)
        assert (record is not None) == (boundary == "after_commit")
        assert (recovered.lookup("subject") is not None) == (record is not None)
        assert not recovered.attempted(state.scope, 1)
        if record is not None:
            assert client_ids.register(recovered, "subject", state.scope, 1, POLICY) == record
    finally:
        recovered.close()
