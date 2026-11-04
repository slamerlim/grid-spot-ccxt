"""Bounded same-user Unix control channel for an existing mock owner only."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import struct
from pathlib import Path
from typing import Any

from grid.domain.model import Scope
from grid.domain.numbers import DomainError, bounded_int, identifier
from grid.exchange.models import ReadError
from grid.execution.models import ExecutionError
from grid.execution.synthetic import SyntheticExchange
from grid.operations.model import Action, Command, StoragePlan
from grid.operations.supervisor import MockSupervisor
from grid.persistence import codec, storage_jobs
from grid.persistence.records import Backpressure, Conflict, LedgerError
from grid.portfolio.model import ExitApproval
from grid.research.artifacts import canonical, plain
from grid.universe.controls import EditAction, Retirement, UniverseEdit
from grid.universe.model import MarketKey, SelectionMode

REQUEST_BYTES = 16 * 1024
RESPONSE_BYTES = 1024 * 1024
MAX_CLIENTS = 4
READ_SECONDS = 2
OPERATION_SECONDS = 10
VIEWS = frozenset({"status", "allocations", "venues", "symbols", "markets", "universe"})
ACTIONS = {
    "pause": Action.PAUSE,
    "cancel-buys": Action.CANCEL_BUYS,
    "stop": Action.STOP,
    "reconcile": Action.RECONCILE,
    "retire": Action.RETIRE,
    "resume": Action.RESUME,
    "halt": Action.HALT,
    "limit-liquidate": Action.LIMIT_LIQUIDATE,
}
CONFIG_FIELDS = {
    "enable-venue": {"venues"},
    "disable-venue": {"venues", "retirement"},
    "set-symbol-mode": {"mode"},
    "allow": set(),
    "deny": {"retirement"},
    "pin": set(),
    "unpin": set(),
    "set-capacity": {"capacity"},
    "refresh-universe": set(),
}
KEY_EDITS = frozenset({"allow", "deny", "pin", "unpin"})
STORAGE_FIELDS = {
    "backup": {"source", "destination"},
    "restore-verify": {"source", "destination", "digest", "requests"},
}
STORAGE_VIEW = "storage-status"
SENSITIVE = (
    frozenset({"resume", "cancel-buys", "retire", "limit-liquidate"})
    | CONFIG_FIELDS.keys()
    | STORAGE_FIELDS.keys()
)


def universe_edit(request: dict[str, Any], scopes: tuple[Scope, ...]) -> UniverseEdit:
    op = request["op"]
    venues = request.get("venues", [])
    if not isinstance(venues, list) or not all(isinstance(v, str) for v in venues):
        raise DomainError("explicit venue identifiers required")
    if op not in KEY_EDITS and request["targets"] != "all":
        raise DomainError("universe-wide edit requires the complete roster")
    return UniverseEdit(
        request["universe_revision"],
        EditAction(op),
        tuple(MarketKey.of(s) for s in scopes) if op in KEY_EDITS else (),
        tuple(venues),
        SelectionMode(request["mode"]) if "mode" in request else None,
        request.get("capacity"),
        Retirement(request["retirement"]) if "retirement" in request else None,
    )


def decode(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise DomainError("duplicate local protocol field")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise DomainError("nonfinite local protocol value")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise DomainError("local protocol object required")
    return value


def socket_path(path: Path, *, existing: bool) -> Path:
    parent = path.parent.resolve(strict=True)
    metadata = parent.stat()
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise DomainError("private local control directory required")
    target = parent / path.name
    if len(os.fsencode(target)) >= 108 or "\0" in str(target):
        raise DomainError("unsupported Unix socket path")
    if existing:
        metadata = target.lstat()
        if (
            not stat.S_ISSOCK(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise DomainError("private owned Unix socket required")
    elif target.exists() or target.is_symlink():
        raise DomainError("existing local endpoint is never replaced")
    return target


def peer_uid(connection: socket.socket) -> int:
    _, uid, _ = struct.unpack(
        "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    )
    return int(uid)


async def receive(connection: socket.socket, maximum: int) -> dict[str, Any]:
    async def exact(size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            part = await asyncio.get_running_loop().sock_recv(connection, size - len(data))
            if not part:
                raise DomainError("incomplete local frame")
            data.extend(part)
        return bytes(data)

    length = int.from_bytes(await exact(4), "big")
    if not 1 <= length <= maximum:
        raise DomainError("local frame bound exceeded")
    return decode(await exact(length))


async def send(connection: socket.socket, value: object, maximum: int) -> None:
    payload = canonical(value)
    if len(payload) > maximum:
        raise DomainError("local response bound exceeded")
    await asyncio.get_running_loop().sock_sendall(
        connection, len(payload).to_bytes(4, "big") + payload
    )


async def call(path: Path, request: dict[str, Any]) -> dict[str, Any]:
    target = socket_path(path, existing=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.setblocking(False)
        async with asyncio.timeout(READ_SECONDS + OPERATION_SECONDS + 3):
            await asyncio.get_running_loop().sock_connect(connection, str(target))
            if peer_uid(connection) != os.geteuid():
                raise DomainError("local peer identity refused")
            await send(connection, request, REQUEST_BYTES)
            response = await receive(connection, RESPONSE_BYTES)
    if (
        type(response.get("version")) is not int
        or response["version"] != 1
        or response.get("mode") != "mock"
        or type(response.get("ok")) is not bool
    ):
        raise DomainError("only the mock operator protocol is admitted")
    return response


class LocalControl:
    def __init__(self, owner: MockSupervisor, session_id: str) -> None:
        if (
            type(owner) is not MockSupervisor
            or type(owner.executor.gateway) is not SyntheticExchange
        ):
            raise DomainError("local operator endpoint requires the mock supervisor")
        self.owner, self.session_id = owner, identifier(session_id)
        self.listener: socket.socket | None = None
        self.acceptor: asyncio.Task[None] | None = None
        self.clients: set[asyncio.Task[None]] = set()
        self.failed = False
        self.path: Path | None = None
        self.inode: tuple[int, int] | None = None

    def envelope(self, data: object, *, error: str | None = None) -> dict[str, object]:
        return {
            "version": 1,
            "mode": "mock",
            "session_id": self.session_id,
            "ok": error is None,
            "error": error,
            "data": data,
        }

    async def dispatch(self, request: dict[str, Any], uid: int) -> dict[str, object]:
        if (
            uid != os.geteuid()
            or type(request.get("version")) is not int
            or request["version"] != 1
        ):
            raise DomainError("local peer or protocol refused")
        op = request.get("op")
        if not isinstance(op, str):
            raise DomainError("local operation required")
        if op == STORAGE_VIEW:
            if set(request) != {"version", "op", "request_id"}:
                raise DomainError("unknown storage status fields")
            identifier(request["request_id"])
            job = await self.owner.writer.submit(
                lambda ledger: storage_jobs.lookup(ledger, request["request_id"])
            )
            return self.envelope({"status": job.status if job else "not-found", "job": plain(job)})
        if op in VIEWS:
            if set(request) != {"version", "op"}:
                raise DomainError("unknown read fields")
            health = await self.owner.status()
            if op == "status":
                universe, _, _ = await self.owner.universe.status()
                data: object = {
                    "observed_ms": health.observed_ms,
                    "revision": health.operations.revision,
                    "admission_halted": health.admission_halted,
                    "last_action": health.operations.command.action,
                    "backup": health.operations.backup,
                    "held": health.operations.held,
                    "cycle": health.cycle,
                    "universe": universe,
                    "cycle_policy_sha256": (
                        codec.digest(health.cycle.policy) if health.cycle else None
                    ),
                    "exit_cause": health.cycle.exit_cause if health.cycle else None,
                    "target_reached_at_exit": (
                        health.cycle.target_reached_at_exit if health.cycle else None
                    ),
                    "allocations": health.allocations,
                    "alerts": health.alerts,
                    "alerts_evicted": health.alerts_evicted,
                    "writer_depth": health.writer_depth,
                    "intake_depth": health.intake_depth,
                    "rss_bytes": health.rss_bytes,
                    "wal_bytes": health.wal_bytes,
                    "closed": health.closed,
                    "live": "DISABLED",
                }
            elif op == "allocations":
                data = health.allocations
            elif op == "universe":
                data, _, _ = await self.owner.universe.status()
            elif op == "venues":
                data = sorted({scope.venue for scope in self.owner.scopes})
            elif op == "symbols":
                data = [
                    {"venue": s.venue, "account": s.account, "symbol": s.symbol}
                    for s in self.owner.scopes
                ]
            else:
                data = [{"scope": s, "market": c.market} for s, c in self.owner.contexts.items()]
            return self.envelope(plain(data))
        fields = {
            "version",
            "op",
            "session_id",
            "request_id",
            "revision",
            "targets",
            "reason",
            "observed_ms",
            "confirmed",
        }
        configuring = op in CONFIG_FIELDS
        storing = op in STORAGE_FIELDS
        if configuring:
            fields |= CONFIG_FIELDS[op] | {"universe_revision"}
        if storing:
            fields |= STORAGE_FIELDS[op]
        needs_exit = (
            op == "limit-liquidate" or configuring and request.get("retirement") == Retirement.LIMIT
        )
        if needs_exit:
            fields |= {"cycle_id", "policy_sha256"}
        if op not in ACTIONS and not configuring and not storing or set(request) != fields:
            raise DomainError("unknown local command fields")
        if request["session_id"] != self.session_id:
            raise DomainError("command belongs to a different mock session")
        identifier(request["request_id"])
        identifier(request["reason"])
        bounded_int(request["revision"], 1, 2**63 - 2)
        bounded_int(request["observed_ms"], 0, 2**53 - 1)
        if type(request["confirmed"]) is not bool or op in SENSITIVE and not request["confirmed"]:
            raise DomainError("explicit command confirmation required")
        targets = request["targets"]
        if targets == "all":
            scopes = self.owner.scopes
        elif (
            isinstance(targets, list)
            and 1 <= len(targets) <= 1000
            and all(isinstance(t, str) for t in targets)
            and len(set(targets)) == len(targets)
        ):
            scopes = tuple(s for s in self.owner.scopes if s.allocation in targets)
            if len(scopes) != len(targets):
                raise DomainError("unknown or ambiguous allocation target")
        else:
            raise DomainError("explicit bounded targets required")
        if storing:
            if targets != "all":
                raise DomainError("storage operates on the complete ledger")
            job = await self.owner.storage_job(
                request["request_id"],
                StoragePlan(
                    request["revision"],
                    Command(
                        Action.BACKUP if op == "backup" else Action.RESTORE_VERIFY,
                        self.owner.scopes,
                        f"local-uid-{uid}",
                        request["reason"],
                        request["observed_ms"],
                        request["confirmed"],
                    ),
                    request["source"],
                    request["destination"],
                    request.get("digest"),
                    request.get("requests"),
                ),
            )
            return self.envelope(
                {"status": job.status, "job": plain(job)},
                error=None if job.status == "verified" else job.status,
            )
        receipt = await self.owner.control(
            request["request_id"],
            request["revision"],
            Command(
                Action.CONFIGURE_UNIVERSE
                if configuring
                else Action.LIQUIDATE_ALLOCATION
                if op == "limit-liquidate" and targets != "all"
                else ACTIONS[op],
                self.owner.scopes if configuring else scopes,
                f"local-uid-{uid}",
                request["reason"],
                request["observed_ms"],
                request["confirmed"],
            ),
            exit_approval=(
                ExitApproval(request["cycle_id"], request["policy_sha256"]) if needs_exit else None
            ),
            universe_edit=universe_edit(request, scopes) if configuring else None,
        )
        health = await self.owner.status()
        return self.envelope(
            plain(
                {
                    "receipt": receipt,
                    "current_revision": health.operations.revision,
                    "admission_halted": health.admission_halted,
                    "cycle_phase": health.cycle.phase if health.cycle else None,
                    "exit_cause": health.cycle.exit_cause if health.cycle else None,
                }
            )
        )

    async def start(self, path: Path) -> None:
        if self.listener is not None:
            raise DomainError("local endpoint already started")
        target = socket_path(path, existing=False)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.setblocking(False)
            listener.bind(str(target))
            info = target.lstat()
            self.path, self.inode = target, (info.st_dev, info.st_ino)
            target.chmod(0o600)
            listener.listen(MAX_CLIENTS)
            self.listener = listener
            self.acceptor = asyncio.create_task(self._accept())
        except BaseException:
            listener.close()
            self._unlink()
            raise

    async def _accept(self) -> None:
        listener = self.listener
        if listener is None:
            raise DomainError("local listener is unavailable")
        while True:
            if len(self.clients) >= MAX_CLIENTS:
                await asyncio.wait(self.clients, return_when=asyncio.FIRST_COMPLETED)
                continue
            connection, _ = await asyncio.get_running_loop().sock_accept(listener)
            task = asyncio.create_task(self._handle(connection))
            self.clients.add(task)
            task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self.clients.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.failed = True

    async def _handle(self, connection: socket.socket) -> None:
        with connection:
            try:
                uid = peer_uid(connection)
                if uid != os.geteuid():
                    return
                async with asyncio.timeout(READ_SECONDS):
                    request = await receive(connection, REQUEST_BYTES)
                async with asyncio.timeout(OPERATION_SECONDS):
                    response = await self.dispatch(request, uid)
            except Conflict:
                response = self.envelope(None, error="conflict")
            except Backpressure:
                response = self.envelope(None, error="busy")
            except TimeoutError:
                response = self.envelope(None, error="outcome-unknown")
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                RecursionError,
                LedgerError,
                ExecutionError,
                ReadError,
            ):
                response = self.envelope(None, error="refused")
            try:
                async with asyncio.timeout(READ_SECONDS):
                    await send(connection, response, RESPONSE_BYTES)
            except (OSError, TimeoutError, ValueError):
                pass  # Lost ACK never retries or rolls back an admitted command.

    def _unlink(self) -> None:
        if self.path is not None and self.path.exists():
            info = self.path.lstat()
            if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self.inode:
                self.path.unlink()

    async def close(self) -> None:
        if self.acceptor is not None:
            self.acceptor.cancel()
            await asyncio.gather(self.acceptor, return_exceptions=True)
        tasks = tuple(self.clients)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.listener is not None:
            self.listener.close()
        self._unlink()
