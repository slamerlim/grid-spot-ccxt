"""Linux local storage, ownership and checked SQLite operations for mock ledgers."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from grid.persistence import schema
from grid.persistence.records import Backup, Bounds, Checkpoint, LedgerError, StorageFault

type Fault = Callable[[str], None]


def no_fault(stage: str) -> None:
    """Default transaction instrumentation has no effects."""


def check_runtime(version: tuple[int, ...], source_id: str, approved: frozenset[str]) -> None:
    patched = version >= (3, 51, 3) or version in {(3, 50, 7), (3, 44, 6)}
    if not patched or not source_id or source_id not in approved:
        raise LedgerError("SQLite runtime/source is not explicitly approved and patched")


def filesystem(path: Path) -> str:
    """Use the actual mount containing the resolved parent, not a path-name guess."""
    candidates: list[tuple[int, str]] = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        before, after = line.split(" - ", 1)
        mount = before.split()[4]
        for encoded, plain in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
            mount = mount.replace(encoded, plain)
        if path.is_relative_to(Path(mount)):
            candidates.append((len(mount), after.split()[0]))
    if not candidates:
        raise LedgerError("local filesystem evidence unavailable")
    return max(candidates)[1]


def private_path(path: Path, *, new: bool) -> Path:
    """Caller supplies a private existing directory; never chmod operator directories."""
    parent = path.parent.resolve(strict=True)
    mode = parent.stat()
    if mode.st_uid != os.geteuid() or stat.S_IMODE(mode.st_mode) & 0o077:
        raise LedgerError("ledger directory must be owner-only")
    target = parent / path.name
    if target.is_symlink():
        raise LedgerError("ledger symlink refused")
    if target.exists():
        metadata = target.stat()
        if new or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise LedgerError("destination exists or aliases another file")
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise LedgerError("ledger file must be owner-only")
    return target


def _create_private(path: Path) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)


def new_database(path: Path) -> Path:
    """Refuse retained SQLite sidecars as well as an existing destination."""
    target = private_path(path, new=True)
    for suffix in ("-wal", "-shm", "-journal", ".owner"):
        private_path(target.with_name(target.name + suffix), new=True)
    return target


def verify_schema(connection: sqlite3.Connection, version: int = schema.VERSION) -> None:
    # Construct an independent expected schema, including unique/FK/check constraints.
    expected_db = sqlite3.connect(":memory:", autocommit=True, check_same_thread=True)
    try:
        for number, _, statements in schema.MIGRATIONS:
            if number <= version:
                for statement in statements:
                    expected_db.execute(statement)
        expected = tuple(
            expected_db.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    finally:
        expected_db.close()
    actual = tuple(
        connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )
    if (
        actual != expected
        or tuple(connection.execute("SELECT * FROM migrations ORDER BY version"))
        != tuple(
            (number, checksum) for number, checksum, _ in schema.MIGRATIONS if number <= version
        )
        or connection.execute("PRAGMA user_version").fetchone()[0] != version
    ):
        raise LedgerError("unrecognized schema or migration checksum")


def semantic_hash(connection: sqlite3.Connection, check: Callable[[], None] | None = None) -> str:
    hasher = hashlib.sha256()
    for table, query in schema.SEMANTIC_QUERIES:
        if (
            table == "operations_revisions"
            and connection.execute("PRAGMA user_version").fetchone()[0] < 5
        ):
            continue
        if (
            table in {"portfolio_revisions", "portfolio_flows"}
            and connection.execute("PRAGMA user_version").fetchone()[0] < 4
        ):
            continue
        if (
            table in {"order_bindings", "trade_keys"}
            and connection.execute("PRAGMA user_version").fetchone()[0] < 2
        ):
            continue
        if (
            table == "universe_revisions"
            and connection.execute("PRAGMA user_version").fetchone()[0] < 3
        ):
            continue
        if check is not None:
            check()
        hasher.update(table.encode())
        # Static queries order complete rows by their unique primary keys.
        for row in connection.execute(query):
            if check is not None:
                check()
            hasher.update(json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode())
            hasher.update(b"\n")
    return hasher.hexdigest()


def _verify_backup(
    connection: sqlite3.Connection,
    expected: Backup | None = None,
    check: Callable[[], None] | None = None,
) -> str:
    metadata = dict(
        connection.execute(
            "SELECT key,value FROM metadata WHERE key IN ('backup_hash','backup_requests')"
        )
    )
    actual = semantic_hash(connection, check)
    count = int(connection.execute("SELECT count(*) FROM requests").fetchone()[0])
    if metadata != {"backup_hash": actual, "backup_requests": str(count)} or (
        expected is not None
        and (expected.semantic_hash != actual or expected.request_count != count)
    ):
        raise LedgerError("backup semantic hash/watermark mismatch")
    return actual


def _sync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def restore(
    backup: Backup,
    destination: Path,
    bounds: Bounds,
    approved_sources: frozenset[str],
    *,
    mock_storage: bool,
    fault: Fault = no_fault,
) -> Backup:
    """Copy a verified backup through SQLite into a NEW private directory/path.

    The restored ledger still needs owner acquisition and reconciliation. The
    source stays read-only. A failed destination is retained, never reported as
    successful and never overwrites an existing operator file.
    """
    source_path = private_path(Path(backup.path), new=False)
    target_path = new_database(destination)
    allowed = {"ext4", "xfs", "btrfs"} | ({"tmpfs", "overlay"} if mock_storage else set())
    if any(filesystem(path.parent) not in allowed for path in (source_path, target_path)):
        raise LedgerError("unsupported restore filesystem")
    # Version check precedes even a read-only connection on an unsafe WAL runtime.
    if sqlite3.sqlite_version_info < (3, 51, 3) and sqlite3.sqlite_version_info not in {
        (3, 50, 7),
        (3, 44, 6),
    }:
        raise LedgerError("unpatched linked SQLite runtime")
    source = sqlite3.connect(
        source_path.as_uri() + "?mode=ro", uri=True, autocommit=True, check_same_thread=True
    )
    try:
        started = time.monotonic()

        def deadline() -> None:
            if (time.monotonic() - started) * 1000 >= bounds.backup_ms:
                raise LedgerError("restore deadline exceeded")

        source.set_progress_handler(
            lambda: int((time.monotonic() - started) * 1000 >= bounds.backup_ms),
            1000,
        )
        source_id = str(source.execute("SELECT sqlite_source_id()").fetchone()[0])
        check_runtime(sqlite3.sqlite_version_info, source_id, approved_sources)
        source.execute("BEGIN")
        version = int(source.execute("PRAGMA user_version").fetchone()[0])
        if version not in tuple(number for number, _, _ in schema.MIGRATIONS):
            raise LedgerError("unrecognized backup schema version")
        verify_schema(source, version)
        digest = _verify_backup(source, backup, deadline)
        if (
            source.execute("PRAGMA integrity_check(1)").fetchone() != ("ok",)
            or source.execute("PRAGMA foreign_key_check").fetchone() is not None
        ):
            raise LedgerError("restore source integrity failure")
        _create_private(target_path)
        target = sqlite3.connect(target_path, autocommit=True, check_same_thread=True)
        try:
            target.set_progress_handler(
                lambda: int((time.monotonic() - started) * 1000 >= bounds.backup_ms),
                1000,
            )

            def progress(status: int, remaining: int, total: int) -> None:
                fault("restore:page")
                deadline()

            target.execute("PRAGMA synchronous=FULL")
            source.backup(target, pages=bounds.backup_pages, progress=progress, sleep=0.01)
            _verify_backup(target, backup, deadline)
            if (
                target.execute("PRAGMA integrity_check(1)").fetchone() != ("ok",)
                or target.execute("PRAGMA foreign_key_check").fetchone() is not None
            ):
                raise LedgerError("restored database integrity failure")
        finally:
            target.close()
        _sync_file(target_path)
        deadline()
        return Backup(str(target_path), digest, backup.request_count)
    finally:
        source.close()


class Storage:
    """Create, use and close only in the owning thread. No automatic transaction retry."""

    def __init__(
        self,
        path: Path,
        bounds: Bounds,
        approved_sources: frozenset[str],
        *,
        mock_storage: bool,
        fault: Fault = no_fault,
    ) -> None:
        self.bounds, self.fault = bounds, fault
        self.mock_storage = mock_storage
        self.run_id = uuid.uuid4().hex
        self.path = private_path(path, new=False)
        self.fs = filesystem(self.path.parent)
        allowed = {"ext4", "xfs", "btrfs"} | ({"tmpfs", "overlay"} if mock_storage else set())
        if self.fs not in allowed:
            raise LedgerError("unsupported or nonlocal filesystem")
        if sqlite3.sqlite_version_info < (3, 51, 3) and sqlite3.sqlite_version_info not in {
            (3, 50, 7),
            (3, 44, 6),
        }:
            raise LedgerError("unpatched linked SQLite runtime")
        self._lock = -1
        self.connection: sqlite3.Connection
        self._closed = False
        self._new = not self.path.exists()
        lock_path = private_path(self.path.with_name(self.path.name + ".owner"), new=False)
        self._lock = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        connection: sqlite3.Connection | None = None
        try:
            try:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LedgerError("another controller owns this ledger") from exc
            # Recheck after taking the lock; the owner file is never unlinked.
            private_path(self.path, new=False)
            if not self.path.exists():
                _create_private(self.path)
                _sync_file(self.path)
            for suffix in ("-wal", "-shm"):
                private_path(self.path.with_name(self.path.name + suffix), new=False)
            connection = sqlite3.connect(
                self.path,
                timeout=bounds.busy_ms / 1000,
                autocommit=True,
                check_same_thread=True,
            )
            self.connection = connection
            self.source_id = str(connection.execute("SELECT sqlite_source_id()").fetchone()[0])
            check_runtime(sqlite3.sqlite_version_info, self.source_id, approved_sources)
            self.compile_options = tuple(
                row[0] for row in connection.execute("PRAGMA compile_options")
            )
            if "THREADSAFE=0" in self.compile_options or "OMIT_WAL" in self.compile_options:
                raise LedgerError("unsupported SQLite build options")
            self._configure()
            self._schema()
            self.integrity()
            if connection.execute("SELECT 1 FROM metadata WHERE key='backup_hash'").fetchone():
                _verify_backup(connection)
            with self.transaction("owner"):
                connection.execute(
                    "DELETE FROM metadata WHERE key IN ('backup_hash','backup_requests')"
                )
                connection.execute("INSERT INTO runs VALUES (?,0)", (self.run_id,))
                connection.execute("UPDATE allocations SET recovery=1")
                connection.execute(
                    "INSERT OR REPLACE INTO metadata VALUES ('runtime',?)",
                    (
                        json.dumps(
                            {
                                "source": self.source_id,
                                "filesystem": self.fs,
                                "version": sqlite3.sqlite_version,
                                "compile": self.compile_options,
                            }
                        ),
                    ),
                )
        except BaseException:
            if connection is not None:
                connection.close()
            os.close(self._lock)
            self._lock = -1
            raise

    def _configure(self) -> None:
        connection = self.connection
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute(f"PRAGMA busy_timeout={self.bounds.busy_ms}")
        connection.execute(f"PRAGMA wal_autocheckpoint={self.bounds.wal_pages}")
        connection.execute(f"PRAGMA journal_size_limit={self.bounds.wal_pages * 4096}")
        self.check_pragmas()

    def check_pragmas(self) -> None:
        expected = {
            "journal_mode": "wal",
            "synchronous": 2,
            "foreign_keys": 1,
            "busy_timeout": self.bounds.busy_ms,
            "trusted_schema": 0,
            "wal_autocheckpoint": self.bounds.wal_pages,
        }
        for name, value in expected.items():
            actual = self.connection.execute(f"PRAGMA {name}").fetchone()[0]
            if actual != value:
                raise StorageFault("required SQLite pragma mismatch")

    def _schema(self) -> None:
        connection = self.connection
        actual = tuple(
            connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        if not actual:
            if not self._new:
                raise LedgerError("existing ledger has no recognized schema")
            current = 0
        else:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current not in tuple(number for number, _, _ in schema.MIGRATIONS):
                raise LedgerError("unrecognized schema version")
            verify_schema(connection, current)
        # Validate an old backup BEFORE changing its schema or semantic hash.
        backup = (
            connection.execute("SELECT 1 FROM metadata WHERE key='backup_hash'").fetchone()
            if current
            else None
        )
        if backup:
            _verify_backup(connection)
        for number, checksum, statements in schema.MIGRATIONS:
            if number > current:
                with self.transaction("migration"):
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute("INSERT INTO migrations VALUES (?,?)", (number, checksum))
                    connection.execute(f"PRAGMA user_version={number}")
                    if backup:
                        connection.execute(
                            "UPDATE metadata SET value=? WHERE key='backup_hash'",
                            (semantic_hash(connection),),
                        )
        verify_schema(connection)

    def integrity(self) -> None:
        if self.connection.execute("PRAGMA integrity_check(1)").fetchone() != ("ok",):
            raise StorageFault("SQLite integrity check failed")
        if self.connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise StorageFault("SQLite foreign key check failed")

    @contextmanager
    def transaction(self, label: str) -> Iterator[None]:
        if self._closed:
            raise LedgerError("writer is closed")
        self.check_pragmas()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.fault(f"{label}:before_commit")
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise
        # An exception here means the acknowledgement was lost, not a rollback.
        self.fault(f"{label}:after_commit")

    def checkpoint(self) -> Checkpoint:
        row = self.connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        result = Checkpoint(*row)
        wal = self.path.with_name(self.path.name + "-wal")
        page_size = self.connection.execute("PRAGMA page_size").fetchone()[0]
        if result.log_pages > self.bounds.wal_pages or (
            wal.exists() and wal.stat().st_size > 32 + (page_size + 24) * self.bounds.wal_pages
        ):
            # TRUNCATE waits only busy_ms. A pinned reader never causes a retry loop.
            trimmed = Checkpoint(
                *self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            )
            if trimmed.busy:
                raise StorageFault("WAL bound reached; release pinned readers before admission")
            return trimmed
        return result

    def semantic_hash(self) -> str:
        return semantic_hash(self.connection)

    def backup(self, path: Path) -> Backup:
        verify_schema(self.connection)
        destination = new_database(path)
        allowed = {"ext4", "xfs", "btrfs"} | ({"tmpfs", "overlay"} if self.mock_storage else set())
        if filesystem(destination.parent) not in allowed:
            raise LedgerError("unsupported backup filesystem")
        _create_private(destination)
        started = time.monotonic()
        target = sqlite3.connect(destination, autocommit=True, check_same_thread=True)
        try:

            def deadline() -> None:
                if (time.monotonic() - started) * 1000 >= self.bounds.backup_ms:
                    raise LedgerError("backup deadline exceeded")

            for connection in (self.connection, target):
                connection.set_progress_handler(
                    lambda: int((time.monotonic() - started) * 1000 >= self.bounds.backup_ms),
                    1000,
                )
            target.execute("PRAGMA synchronous=FULL")

            def progress(status: int, remaining: int, total: int) -> None:
                self.fault("backup:page")
                deadline()

            self.connection.backup(
                target, pages=self.bounds.backup_pages, progress=progress, sleep=0.01
            )
            if (
                target.execute("PRAGMA integrity_check(1)").fetchone() != ("ok",)
                or target.execute("PRAGMA foreign_key_check").fetchone() is not None
            ):
                raise LedgerError("backup verification failed")
            expected = semantic_hash(self.connection, deadline)
            if semantic_hash(target, deadline) != expected:
                raise LedgerError("backup semantic comparison failed")
            count = int(target.execute("SELECT count(*) FROM requests").fetchone()[0])
            target.execute("INSERT OR REPLACE INTO metadata VALUES ('backup_hash',?)", (expected,))
            target.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('backup_requests',?)", (str(count),)
            )
            target.close()
            _sync_file(destination)
            deadline()
            return Backup(str(destination), expected, count)
        except BaseException:
            target.close()
            # A failed artifact is retained for inspection and is never reported as a backup.
            raise
        finally:
            self.connection.set_progress_handler(None, 0)

    def close(self) -> None:
        if not self._closed:
            try:
                with self.transaction("close"):
                    self.connection.execute("UPDATE runs SET closed=1 WHERE id=?", (self.run_id,))
            finally:
                self.connection.close()
                self._closed = True
                os.close(self._lock)
                self._lock = -1
