import argparse
import asyncio
import os
import socket
import stat
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.domain.numbers import DomainError
from grid.operations.console import execute, load_request
from grid.operations.local import (
    ACTIONS,
    MAX_CLIENTS,
    REQUEST_BYTES,
    SENSITIVE,
    call,
    receive,
    send,
)
from grid.operations.session import MockSession
from grid.research.artifacts import canonical


@pytest.fixture
def short_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # Unix pathname sockets have a kernel length bound; preserve it in production.
    return tmp_path_factory.mktemp("operator")


def arguments(
    path: Path, packet: Path, op: str, revision: int, *, confirm: bool = False
) -> argparse.Namespace:
    return argparse.Namespace(
        socket=path,
        request=packet,
        op=op,
        revision=revision,
        confirm=confirm,
        reason="operator-test",
        all=True,
        allocation=None,
    )


@pytest.mark.usefixtures("async_sockets")
def test_confirmed_controls_replay_without_clearing_a_later_halt(short_path: Path) -> None:
    async def run() -> None:
        session = await MockSession.open(short_path / "session", SOURCES, resume=False)
        assert session.owner and session.control and session.control.path
        path = session.control.path
        owner = session.owner
        try:
            status = await call(path, {"version": 1, "op": "status"})
            assert status["ok"] and status["mode"] == "mock"
            assert status["data"]["live"] == "DISABLED" and status["data"]["admission_halted"]
            revision = status["data"]["revision"]
            for op in ("allocations", "venues", "symbols", "markets"):
                assert (await call(path, {"version": 1, "op": op}))["data"]
            packet = short_path / "resume.json"
            args = arguments(path, packet, "resume", revision, confirm=True)
            with pytest.raises(DomainError, match="prepare"):
                await execute(args)
            assert not packet.exists()
            args.confirm = False
            code, _ = await execute(args)
            assert code == 3 and (await owner.status()).operations.revision == revision
            assert stat.S_IMODE(packet.stat().st_mode) == 0o600
            original = packet.read_bytes()
            args.confirm = True
            code, _ = await execute(args)
            assert code == 0 and not (await owner.status()).admission_halted
            assert (await owner.status()).operations.command.actor == f"local-uid-{os.geteuid()}"
            assert (await execute(args))[0] == 0
            assert (await owner.status()).operations.revision == revision + 1
            halt = arguments(path, short_path / "halt.json", "halt", revision + 1)
            assert (await execute(halt))[0] == 0
            assert (await execute(args))[0] == 0  # Receipt replay is not another resume.
            assert (await owner.status()).admission_halted
            assert (await owner.status()).operations.revision == revision + 2
            assert packet.read_bytes() == original
            args.reason = "different-command"
            with pytest.raises(DomainError, match="differs"):
                await execute(args)
            stale = arguments(path, short_path / "stale.json", "pause", revision)
            code, response = await execute(stale)
            assert code == 2 and isinstance(response, dict) and response["error"] == "conflict"
            assert (await owner.status()).operations.revision == revision + 2
            with pytest.raises(DomainError, match="peer"):
                await session.control.dispatch({"version": 1, "op": "status"}, os.geteuid() + 1)
            assert not owner.executor.gateway.sent
        finally:
            await session.close()
        assert not path.exists()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_owner_lock_and_restart_retain_fake_orders_and_start_halted(short_path: Path) -> None:
    async def run() -> None:
        directory = short_path / "session"
        session = await MockSession.open(directory, SOURCES, resume=False)
        assert session.owner and session.control and session.control.path
        packet = short_path / "resume.json"
        try:
            with pytest.raises(BlockingIOError):
                await MockSession.open(directory, SOURCES, resume=True)
            args = arguments(
                session.control.path,
                packet,
                "resume",
                (await session.owner.status()).operations.revision,
            )
            assert (await execute(args))[0] == 3
            args.confirm = True
            assert (await execute(args))[0] == 0
            await session.step()
            assert len(session.owner.executor.gateway.sent) == 1
        finally:
            await session.close()
        session = await MockSession.open(directory, SOURCES, resume=True)
        assert session.owner and session.control and session.control.path
        try:
            assert len(session.owner.executor.gateway.sent) == 1
            assert (await session.owner.status()).admission_halted
            args.socket = session.control.path
            assert (await execute(args))[0] == 0
            await session.step()
            assert (await session.owner.status()).admission_halted
            assert len(session.owner.executor.gateway.sent) == 1
            assert session.owner.executor.gateway.sent[0].order_type == "limit"
        finally:
            await session.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize(
    "payload",
    [
        None,
        b'{"version":1,"op":"status","op":"resume"}',
        b'{"version":1,"op":"do-not-reflect"}',
        b'{"version":NaN,"op":"status"}',
    ],
)
def test_invalid_frames_are_bounded_and_sanitized(short_path: Path, payload: bytes | None) -> None:
    async def run() -> None:
        session = await MockSession.open(short_path / "session", SOURCES, resume=False)
        assert session.owner and session.control and session.control.path
        revision = (await session.owner.status()).operations.revision
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.setblocking(False)
                await asyncio.get_running_loop().sock_connect(client, str(session.control.path))
                frame = (
                    (REQUEST_BYTES + 1).to_bytes(4, "big")
                    if payload is None
                    else len(payload).to_bytes(4, "big") + payload
                )
                await asyncio.get_running_loop().sock_sendall(client, frame)
                async with asyncio.timeout(3):
                    response = await receive(client, 1024)
                assert response["error"] == "refused"
                assert "do-not-reflect" not in canonical(response).decode()
            assert (await session.owner.status()).operations.revision == revision
        finally:
            await session.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_slow_clients_are_bounded_and_endpoint_checks_permissions(
    short_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        session = await MockSession.open(short_path / "session", SOURCES, resume=False)
        assert session.control and session.control.path
        path = session.control.path
        connections: list[socket.socket] = []
        try:
            path.chmod(0o666)
            with pytest.raises(DomainError, match="private"):
                await call(path, {"version": 1, "op": "status"})
            path.chmod(0o600)
            alias = path.with_suffix(".alias")
            alias.symlink_to(path)
            with pytest.raises(DomainError, match="private"):
                await call(alias, {"version": 1, "op": "status"})
            monkeypatch.setattr("grid.operations.local.READ_SECONDS", 0.2)
            for _ in range(MAX_CLIENTS + 1):
                connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connection.setblocking(False)
                connections.append(connection)
                await asyncio.get_running_loop().sock_connect(connection, str(path))
            for _ in range(100):
                if len(session.control.clients) == MAX_CLIENTS:
                    break
                await asyncio.sleep(0.001)
            assert len(session.control.clients) == MAX_CLIENTS
            await send(connections[-1], {"version": 1, "op": "status"}, REQUEST_BYTES)
            async with asyncio.timeout(3):
                assert (await receive(connections[-1], 1024 * 1024))["ok"]
            assert len(session.control.clients) <= MAX_CLIENTS
        finally:
            for connection in connections:
                connection.close()
            await session.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_lost_ack_reuses_exact_durable_command_identity(short_path: Path) -> None:
    async def run() -> None:
        session = await MockSession.open(short_path / "session", SOURCES, resume=False)
        assert session.owner and session.control and session.control.path
        try:
            owner, path = session.owner, session.control.path
            revision = (await owner.status()).operations.revision
            packet = short_path / "resume.json"
            args = arguments(path, packet, "resume", revision)
            assert (await execute(args))[0] == 3
            request = load_request(packet)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.setblocking(False)
                await asyncio.get_running_loop().sock_connect(client, str(path))
                await send(client, request, REQUEST_BYTES)
                # Disconnect without receiving the command's acknowledgement.
            for _ in range(100):
                receipt = await owner.writer.submit(
                    lambda ledger: ledger.lookup(request["request_id"])
                )
                if receipt is not None:
                    break
                await asyncio.sleep(0.005)
            assert receipt is not None
            response = await call(path, request)
            assert response["ok"] and response["data"]["current_revision"] == revision + 1
            assert (await owner.status()).operations.revision == revision + 1
        finally:
            await session.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_endpoint_cleanup_never_removes_a_replacement_file(short_path: Path) -> None:
    async def run() -> None:
        session = await MockSession.open(short_path / "session", SOURCES, resume=False)
        assert session.control and session.control.path
        path = session.control.path
        path.rename(path.with_suffix(".retained"))
        path.write_text("retain-this-file")
        await session.close()
        assert path.read_text() == "retain-this-file"

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize("op", sorted(ACTIONS))
def test_each_operator_action_reaches_the_existing_audited_controller(
    short_path: Path, op: str
) -> None:
    async def run() -> None:
        session = await MockSession.open(short_path / "session", SOURCES, resume=False)
        assert session.owner and session.control and session.control.path
        try:
            before = await session.owner.status()
            args = arguments(
                session.control.path, short_path / "command.json", op, before.operations.revision
            )
            code, _ = await execute(args)
            if op in SENSITIVE:
                assert code == 3
                args.confirm = True
                code, _ = await execute(args)
            assert code == 0
            after = await session.owner.status()
            assert after.operations.command.action is ACTIONS[op]
            assert after.operations.command.reason == "operator-test"
            assert after.operations.revision == before.operations.revision + 1
            assert after.allocations[0].quote_cash == before.allocations[0].quote_cash
        finally:
            await session.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_concurrent_revision_claims_have_one_winner(short_path: Path) -> None:
    async def run() -> None:
        session = await MockSession.open(short_path / "session", SOURCES, resume=False)
        assert session.owner and session.control and session.control.path
        try:
            status = await call(session.control.path, {"version": 1, "op": "status"})
            request = {
                "version": 1,
                "op": "pause",
                "session_id": session.session_id,
                "revision": status["data"]["revision"],
                "targets": "all",
                "reason": "race",
                "observed_ms": status["data"]["observed_ms"],
                "confirmed": False,
            }
            responses = await asyncio.gather(
                *[
                    call(session.control.path, dict(request, request_id=name))
                    for name in ("first", "second")
                ]
            )
            assert sorted(r["ok"] for r in responses) == [False, True]
            assert (await session.owner.status()).operations.revision == request["revision"] + 1
        finally:
            await session.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize("financial_pending", [False, True])
def test_close_releases_journal_only_after_financial_owner_closes(
    short_path: Path, monkeypatch: pytest.MonkeyPatch, financial_pending: bool
) -> None:
    async def run() -> None:
        session = await MockSession.open(short_path / "session", SOURCES, resume=False)
        assert session.owner and session.control
        target = session.owner if financial_pending else session.control
        original = target.close

        async def failed_close() -> None:
            raise TimeoutError("injected cleanup failure")

        monkeypatch.setattr(target, "close", failed_close)
        with pytest.raises(TimeoutError):
            await session.close()
        assert session.journal.closed is not financial_pending
        assert not session.closed  # Incomplete endpoint cleanup must remain retryable.
        monkeypatch.setattr(target, "close", original)
        if financial_pending:
            with pytest.raises(BlockingIOError):
                await MockSession.open(short_path / "session", SOURCES, resume=True)
        await session.close()
        assert session.closed and session.journal.closed

    asyncio.run(run())
