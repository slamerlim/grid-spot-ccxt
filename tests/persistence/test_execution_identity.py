"""I4 identity claims share the financial transaction and survive schema upgrades."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from grid.domain.market import Money
from grid.domain.model import ActualFee, Context, Fill, State
from grid.persistence import schema
from grid.persistence.ledger import Ledger, client_identity, trade_identity
from grid.persistence.records import Backup, Bounds, Conflict, ExecutionPosition, LedgerError
from grid.persistence.storage import no_fault, restore, semantic_hash, verify_schema

from .conftest import SOURCES


def old_schema(path: Path) -> str:
    """Construct a genuine populated v1 fixture, with no v2 identity rows."""
    connection = sqlite3.connect(path, autocommit=True)
    try:
        connection.execute("DROP TABLE trade_keys")
        connection.execute("DROP TABLE order_bindings")
        connection.execute("DROP TABLE universe_revisions")
        connection.execute("DROP TABLE portfolio_revisions")
        connection.execute("DROP TABLE portfolio_flows")
        connection.execute("DROP TABLE execution_positions")
        connection.execute("DROP TABLE client_ids")
        connection.execute("DROP TABLE operations_revisions")
        connection.execute("DELETE FROM migrations WHERE version>=2")
        connection.execute("PRAGMA user_version=1")
        verify_schema(connection, 1)
        digest = semantic_hash(connection)
        connection.execute("UPDATE metadata SET value=? WHERE key='backup_hash'", (digest,))
        return digest
    finally:
        connection.close()


def test_v1_migration_and_backup_restore_preserve_finances(
    funded: Ledger,
    state: State,
    bounds: Bounds,
    tmp_path: Path,
) -> None:
    before = funded.snapshot(state.scope)
    receipt = funded.lookup("seed")
    backup = funded.backup(tmp_path / "backup.db")
    backup = Backup(backup.path, old_schema(Path(backup.path)), backup.request_count)
    restored = restore(backup, tmp_path / "restored.db", bounds, SOURCES, mock_storage=True)
    assert restored.semantic_hash == backup.semantic_hash
    path = funded.storage.path
    funded.close()
    old_schema(path)
    for candidate in (path, Path(restored.path)):
        ledger = Ledger(candidate, bounds, SOURCES, mock_storage=True)
        try:
            verify_schema(ledger.db)
            assert ledger.db.execute("PRAGMA user_version").fetchone() == (schema.VERSION,)
            assert tuple(
                ledger.db.execute("SELECT version,checksum FROM migrations ORDER BY version")
            ) == tuple((number, checksum) for number, checksum, _ in schema.MIGRATIONS)
            after = ledger.snapshot(state.scope)
            assert after.state is not None and before.state is not None
            assert (after.state.quote_cash, after.state.owned_base, after.state.orders) == (
                before.state.quote_cash,
                before.state.owned_base,
                before.state.orders,
            )
            assert after.state.reconcile_reason is not None
            assert ledger.lookup("seed") == receipt
        finally:
            ledger.close()


@pytest.mark.parametrize("boundary", ["migration:before_commit", "migration:after_commit"])
def test_v2_migration_failure_has_a_recognized_atomic_schema(
    funded: Ledger,
    state: State,
    bounds: Bounds,
    boundary: str,
) -> None:
    path = funded.storage.path
    funded.close()
    old_schema(path)

    def fail(stage: str) -> None:
        if stage == boundary:
            raise OSError("injected migration failure")

    with pytest.raises(OSError):
        Ledger(path, bounds, SOURCES, mock_storage=True, fault=fail)
    connection = sqlite3.connect(path, autocommit=True)
    try:
        verify_schema(connection, 2 if boundary.endswith("after_commit") else 1)
    finally:
        connection.close()
    recovered = Ledger(path, bounds, SOURCES, mock_storage=True)
    try:
        after = recovered.snapshot(state.scope)
        assert after.state is not None and after.state.quote_cash == 1000
    finally:
        recovered.close()


def test_receipt_replay_is_not_send_permission(funded: Ledger, state: State) -> None:
    intent = funded.intents(state.scope)[0]
    outcome = funded.attempt_once("claim", state.scope, 1, intent.id)
    assert outcome.accepted
    with pytest.raises(Conflict, match="receipt replay"):
        funded.attempt_once("claim", state.scope, 1, intent.id)
    assert funded.lookup("claim") == outcome


def test_identity_cannot_cross_allocations_or_reuse_actual_trade(
    funded: Ledger,
    state: State,
    context: Context,
) -> None:
    scope = state.scope
    other = replace(scope, allocation="other")
    funded.credit("other-funds", scope.venue, scope.account, Money("QUOTE", D(1000)))
    funded.start("other-start", other, (Money("QUOTE", D(1000)),), context)
    for index, owner in enumerate((scope, other)):
        funded.attempt_once("claim" + str(index), owner, 1, funded.intents(owner)[0].id)
        funded.bind(
            "bind" + str(index), owner, 1, "external-" + str(index), client_identity(owner, 1)
        )
    with pytest.raises(Conflict):
        funded.bind("bad-binding", other, 1, "external-0", client_identity(other, 1))
    fees = (ActualFee("QUOTE", D(0), None),)
    first = Fill(scope, trade_identity(scope, "real-trade"), 1, D(1), D(100), fees)
    funded.apply("fill", 2, first, context, external=("external-0", "real-trade"))
    second = replace(first, scope=other, trade_id=trade_identity(other, "real-trade"))
    with pytest.raises(Conflict, match="another allocation"):
        funded.apply("other-fill", 2, second, context, external=("external-1", "real-trade"))
    snapshot = funded.snapshot(other)
    assert snapshot.state is not None and snapshot.state.quote_cash == 1000
    assert funded.db.execute("SELECT count(*) FROM trade_keys").fetchone() == (1,)


@pytest.mark.parametrize(
    "boundary", ["event:after_posting", "event:before_commit", "event:after_commit"]
)
def test_fill_trade_key_and_cash_commit_together(
    funded: Ledger,
    state: State,
    context: Context,
    boundary: str,
) -> None:
    scope = state.scope
    funded.attempt_once("claim", scope, 1, funded.intents(scope)[0].id)
    funded.bind("bind", scope, 1, "external", client_identity(scope, 1))

    def fail(stage: str) -> None:
        if stage == boundary:
            raise OSError("injected posting failure")

    funded.storage.fault = fail
    fill = Fill(
        scope, trade_identity(scope, "trade"), 1, D(1), D(100), (ActualFee("QUOTE", D(0), None),)
    )
    with pytest.raises(OSError):
        funded.apply(
            "fill",
            2,
            fill,
            context,
            external=("external", "trade"),
            position=ExecutionPosition("timestamp", 100),
        )
    funded.storage.fault = no_fault
    committed = boundary == "event:after_commit"
    assert funded.db.execute("SELECT count(*) FROM trade_keys").fetchone() == (int(committed),)
    assert funded.db.execute("SELECT count(*) FROM fills").fetchone() == (int(committed),)
    assert funded.db.execute("SELECT count(*) FROM execution_positions").fetchone() == (
        int(committed),
    )
    snapshot = funded.snapshot(scope)
    assert snapshot.state is not None and snapshot.state.quote_cash == (900 if committed else 1000)


def test_identity_corruption_blocks_owner_recovery(
    funded: Ledger,
    state: State,
    bounds: Bounds,
) -> None:
    scope = state.scope
    funded.attempt_once("claim", scope, 1, funded.intents(scope)[0].id)
    funded.bind("bind", scope, 1, "external", client_identity(scope, 1))
    funded.db.execute("UPDATE order_bindings SET client_id='corrupt'")
    path = funded.storage.path
    funded.close()
    with pytest.raises(LedgerError, match="identity"):
        Ledger(path, bounds, SOURCES, mock_storage=True)


def test_backup_restores_external_bindings_and_trade_deduplication(
    funded: Ledger,
    state: State,
    context: Context,
    bounds: Bounds,
    tmp_path: Path,
) -> None:
    scope = state.scope
    funded.attempt_once("claim", scope, 1, funded.intents(scope)[0].id)
    funded.bind("bind", scope, 1, "external", client_identity(scope, 1))
    fill = Fill(
        scope, trade_identity(scope, "trade"), 1, D(1), D(100), (ActualFee("QUOTE", D(0), None),)
    )
    funded.apply(
        "fill",
        2,
        fill,
        context,
        external=("external", "trade"),
        position=ExecutionPosition("timestamp", 100),
    )
    backup = funded.backup(tmp_path / "backup.db")
    restored = restore(backup, tmp_path / "restored.db", bounds, SOURCES, mock_storage=True)
    ledger = Ledger(Path(restored.path), bounds, SOURCES, mock_storage=True)
    try:
        snapshot = ledger.snapshot(scope)
        assert snapshot.version is not None
        assert ledger.binding(scope, 1) == "external"
        duplicate = ledger.apply(
            "replayed-fill",
            snapshot.version,
            fill,
            context,
            external=("external", "trade"),
            position=ExecutionPosition("timestamp", 100),
        )
        assert duplicate.state is not None
        assert duplicate.state.quote_cash == 900 and duplicate.state.owned_base == 1
        assert ledger.db.execute("SELECT count(*) FROM fills").fetchone() == (1,)
        assert ledger.db.execute("SELECT count(*) FROM trade_keys").fetchone() == (1,)
        assert duplicate.state.reconcile_reason is not None
        assert ledger.execution_position(scope) == ExecutionPosition("timestamp", 100)
    finally:
        ledger.close()


def test_v6_migration_preserves_legacy_fills_without_inventing_ordering(
    funded: Ledger,
    state: State,
    context: Context,
    bounds: Bounds,
    tmp_path: Path,
) -> None:
    scope = state.scope
    funded.attempt_once("claim", scope, 1, funded.intents(scope)[0].id)
    funded.bind("bind", scope, 1, "external", client_identity(scope, 1))
    fill = Fill(
        scope, trade_identity(scope, "old"), 1, D(".4"), D(100), (ActualFee("QUOTE", D(0), None),)
    )
    receipt = funded.apply("old-fill", 2, fill, context, external=("external", "old"))
    backup = funded.backup(tmp_path / "v6.db")
    with closing(sqlite3.connect(backup.path, autocommit=True)) as raw:
        raw.execute("DROP TABLE execution_positions")
        raw.execute("DELETE FROM migrations WHERE version=7")
        raw.execute("PRAGMA user_version=6")
        verify_schema(raw, 6)
        digest = semantic_hash(raw)
        raw.execute("UPDATE metadata SET value=? WHERE key='backup_hash'", (digest,))
    restored = restore(
        Backup(backup.path, digest, backup.request_count),
        tmp_path / "upgraded.db",
        bounds,
        SOURCES,
        mock_storage=True,
    )
    with_position = Ledger(Path(restored.path), bounds, SOURCES, mock_storage=True)
    try:
        assert with_position.lookup("old-fill") == receipt
        assert with_position.execution_position(scope) is None
        with pytest.raises(Conflict, match="baseline"):
            with_position.check_execution_position(scope, ExecutionPosition("timestamp", 100))
        assert (
            with_position.db.execute("SELECT payload FROM fills").fetchone()
            == funded.db.execute("SELECT payload FROM fills").fetchone()
        )
    finally:
        with_position.close()


@pytest.mark.parametrize("boundary", ["migration:before_commit", "migration:after_commit"])
def test_ordering_migration_is_atomic(
    funded: Ledger,
    bounds: Bounds,
    boundary: str,
) -> None:
    path = funded.storage.path
    funded.close()
    with closing(sqlite3.connect(path, autocommit=True)) as raw:
        raw.execute("DROP TABLE execution_positions")
        raw.execute("DELETE FROM migrations WHERE version=7")
        raw.execute("PRAGMA user_version=6")

    def fail(stage: str) -> None:
        if stage == boundary:
            raise OSError("injected migration failure")

    with pytest.raises(OSError):
        Ledger(path, bounds, SOURCES, mock_storage=True, fault=fail)
    with closing(sqlite3.connect(path, autocommit=True)) as raw:
        verify_schema(raw, 7 if boundary.endswith("after_commit") else 6)
    recovered = Ledger(path, bounds, SOURCES, mock_storage=True)
    try:
        verify_schema(recovered.db, 7)
    finally:
        recovered.close()


@pytest.mark.parametrize("damage", ["value", "kind", "receipt", "missing_one"])
def test_corrupt_ordering_receipts_refuse_writer_reopen(
    funded: Ledger,
    state: State,
    context: Context,
    bounds: Bounds,
    damage: str,
) -> None:
    scope = state.scope
    funded.attempt_once("claim", scope, 1, funded.intents(scope)[0].id)
    funded.bind("bind", scope, 1, "external", client_identity(scope, 1))
    for index in range(2):
        snapshot = funded.snapshot(scope)
        assert snapshot.version is not None
        trade = str(index)
        fill = Fill(
            scope,
            trade_identity(scope, trade),
            1,
            D(".2"),
            D(100),
            (ActualFee("QUOTE", D(0), None),),
        )
        funded.apply(
            "fill-" + trade,
            snapshot.version,
            fill,
            context,
            external=("external", trade),
            position=ExecutionPosition("timestamp", 100 + index),
        )
    if damage == "value":
        funded.db.execute("UPDATE execution_positions SET value=value+100")
    elif damage == "kind":
        funded.db.execute("UPDATE execution_positions SET kind='sequence'")
    elif damage == "receipt":
        funded.db.execute("UPDATE execution_positions SET request_id='claim' WHERE value=100")
    else:
        funded.db.execute("DELETE FROM execution_positions WHERE value=100")
    path = funded.storage.path
    funded.close()
    with pytest.raises(LedgerError, match="execution|receipt"):
        Ledger(path, bounds, SOURCES, mock_storage=True)
