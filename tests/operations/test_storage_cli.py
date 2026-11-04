"""Local protocol and separate CLI processes exercise the audited storage path."""

import asyncio
import json

import pytest
from persistence.conftest import SOURCES

from grid.domain.numbers import DomainError
from grid.operations.console import execute, load_request
from grid.operations.local import call
from grid.operations.session import MockSession

from .test_console_process import command, start
from .test_local import arguments


@pytest.mark.usefixtures("async_sockets")
def test_storage_confirmation_audit_and_standalone_verification(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    async def run() -> None:
        root = tmp_path_factory.mktemp("storage-cli")
        session = await MockSession.open(root / "session", SOURCES, resume=False)
        assert session.owner and session.control and session.control.path
        path = session.control.path
        owner = session.owner
        try:
            args = arguments(
                path, root / "backup.json", "backup", (await owner.status()).operations.revision
            )
            args.source, args.destination = root / "backup.db", root / "verified.db"
            args.confirm = True
            with pytest.raises(DomainError, match="prepare"):
                await execute(args)
            args.confirm = False
            assert (await execute(args))[0] == 3
            assert not args.source.exists() and not args.destination.exists()
            packet = load_request(args.request)
            unconfirmed = await call(path, dict(packet, confirmed=False))
            assert unconfirmed["error"] == "refused"
            args.confirm = True
            code, response = await execute(args)
            assert code == 0 and isinstance(response, dict)
            assert response["data"]["status"] == "verified"
            notes = response["data"]["job"]["result"]["notices"]
            status = await call(
                path, {"version": 1, "op": "storage-status", "request_id": packet["request_id"]}
            )
            assert status["data"] == response["data"]
            assert status["data"]["job"]["plan"]["command"]["reason"] == "operator-test"
            restore = arguments(
                path,
                root / "restore.json",
                "restore-verify",
                (await owner.status()).operations.revision,
            )
            restore.source, restore.destination = args.source, root / "again.db"
            restore.digest, restore.requests = notes[0], int(notes[1])
            assert (await execute(restore))[0] == 3
            original = (await owner.status()).operations.backup
            restore.confirm = True
            assert (await execute(restore))[0] == 0
            assert (await owner.status()).operations.backup == original
            assert not owner.executor.gateway.sent
            assert (
                await call(path, {"version": 1, "op": "storage-status", "request_id": "absent"})
            )["data"]["status"] == "not-found"
        finally:
            await session.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize("mutation", ["relative", "targets", "extra", "count", "digest"])
def test_bad_storage_packets_have_no_files_or_audit(
    tmp_path_factory: pytest.TempPathFactory, mutation: str
) -> None:
    async def run() -> None:
        root = tmp_path_factory.mktemp("storage-wire")
        session = await MockSession.open(root / "session", SOURCES, resume=False)
        assert session.owner and session.control and session.control.path
        try:
            args = arguments(
                session.control.path,
                root / "request.json",
                "restore-verify",
                (await session.owner.status()).operations.revision,
            )
            args.source, args.destination = root / "source.db", root / "destination.db"
            args.digest, args.requests = "0" * 64, 0
            assert (await execute(args))[0] == 3
            packet = load_request(args.request)
            if mutation == "relative":
                packet["destination"] = "relative.db"
            elif mutation == "targets":
                packet["targets"] = [session.owner.scopes[0].allocation]
            elif mutation == "extra":
                packet["overwrite"] = True
            elif mutation == "count":
                packet["requests"] = True
            else:
                packet["digest"] = "invalid"
            result = await call(session.control.path, packet)
            assert result["error"] == "refused"
            assert not args.destination.exists()
            assert (await session.owner.status()).operations.revision == args.revision
        finally:
            await session.close()

    asyncio.run(run())


def test_storage_cli_process_restart_keeps_receipt_and_never_recopies(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root = tmp_path_factory.mktemp("storage-process")
    directory = root / "session"
    child, ready = start(directory, resume=False)
    try:
        status = json.loads(command("status", "--socket", ready["socket"]).stdout)
        args = [
            "backup",
            "--socket",
            ready["socket"],
            "--revision",
            str(status["data"]["revision"]),
            "--reason",
            "process storage drill",
            "--request",
            str(root / "backup.json"),
            "--output",
            str(root / "backup.db"),
            "--verify-to",
            str(root / "verified.db"),
        ]
        assert command(*args).returncode == 3
        completed = command(*args, "--confirm")
        assert completed.returncode == 0, completed.stderr + completed.stdout
        receipt = json.loads(completed.stdout)["data"]
        retained = (root / "backup.db").read_bytes()
        child.kill()
        assert child.wait(timeout=5) < 0
        child.communicate(timeout=5)
        child, ready = start(directory, resume=True)
        args[2] = ready["socket"]
        assert json.loads(command(*args, "--confirm").stdout)["data"] == receipt
        request = json.loads((root / "backup.json").read_text())
        inspected = command(
            "storage-status", "--socket", ready["socket"], "--request-id", request["request_id"]
        )
        assert inspected.returncode == 0 and json.loads(inspected.stdout)["data"] == receipt
        assert (root / "backup.db").read_bytes() == retained
        status = json.loads(command("status", "--socket", ready["socket"]).stdout)
        assert status["data"]["admission_halted"]
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
