import asyncio
import json

import pytest
from persistence.conftest import SOURCES

from grid.domain.numbers import DomainError
from grid.operations.console import execute, load_request
from grid.operations.local import CONFIG_FIELDS, call
from grid.operations.session import MockSession
from grid.universe.controls import Retirement

from .test_console_process import command, start
from .test_local import arguments


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize("op", sorted(CONFIG_FIELDS))
def test_every_configuration_command_requires_two_step_review_and_two_revisions(
    tmp_path_factory: pytest.TempPathFactory,
    op: str,
) -> None:
    async def run() -> None:
        root = tmp_path_factory.mktemp("cfg")
        session = await MockSession.open(root / "session", SOURCES, resume=False)
        assert session.owner and session.control and session.control.path
        owner, path = session.owner, session.control.path
        try:
            status = (await call(path, {"version": 1, "op": "status"}))["data"]
            args = arguments(path, root / "edit.json", op, status["revision"])
            args.universe_revision = status["universe"]["revision"]
            args.retirement = Retirement.MANAGE
            args.venue = [owner.scopes[0].venue]
            args.mode = "manual"
            args.capacity = 0
            args.confirm = True
            with pytest.raises(DomainError, match="prepare"):
                await execute(args)
            args.confirm = False
            code, preview = await execute(args)
            assert code == 3 and isinstance(preview, dict)
            assert preview["request"]["universe_revision"] == 1
            assert (await owner.status()).operations.revision == status["revision"]
            original = args.request.read_bytes()
            args.confirm = True
            assert (await execute(args))[0] == 0
            updated = (await call(path, {"version": 1, "op": "universe"}))["data"]
            assert updated["revision"] == 2
            assert (await owner.status()).operations.revision == status["revision"] + 1
            assert (await owner.status()).admission_halted
            assert (await execute(args))[0] == 0
            assert args.request.read_bytes() == original
            args.universe_revision = 2
            with pytest.raises(DomainError, match="retained request differs"):
                await execute(args)
            assert not owner.executor.gateway.sent
        finally:
            await session.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "revision_bool",
        "venue_string",
        "unknown_venue",
        "limit_without_approval",
        "confirmation",
        "capacity_bool",
    ],
)
def test_configuration_protocol_refuses_malformed_edits_without_mutation(
    tmp_path_factory: pytest.TempPathFactory,
    mutation: str,
) -> None:
    async def run() -> None:
        root = tmp_path_factory.mktemp("cfgbad")
        session = await MockSession.open(root / "session", SOURCES, resume=False)
        assert session.owner and session.control and session.control.path
        owner, path = session.owner, session.control.path
        try:
            before = (await call(path, {"version": 1, "op": "status"}))["data"]
            args = arguments(path, root / "edit.json", "disable-venue", before["revision"])
            args.universe_revision = before["universe"]["revision"]
            args.venue = [owner.scopes[0].venue]
            args.retirement = Retirement.MANAGE
            assert (await execute(args))[0] == 3
            packet = load_request(args.request)
            if mutation == "extra":
                packet["force"] = True
            elif mutation == "revision_bool":
                packet["universe_revision"] = True
            elif mutation == "venue_string":
                packet["venues"] = owner.scopes[0].venue
            elif mutation == "unknown_venue":
                packet["venues"] = ["unknown"]
            elif mutation == "limit_without_approval":
                packet["retirement"] = Retirement.LIMIT
            elif mutation == "confirmation":
                packet["confirmed"] = False
            else:
                packet["op"], packet["capacity"] = "set-capacity", True
                del packet["venues"], packet["retirement"]
            result = await call(path, packet)
            assert not result["ok"] and result["error"] == "refused"
            after = (await call(path, {"version": 1, "op": "status"}))["data"]
            assert (
                after["revision"] == before["revision"] and after["universe"] == before["universe"]
            )
            assert after["cycle"] == before["cycle"] and not session.control.failed
        finally:
            await session.close()

    asyncio.run(run())


def test_configuration_cli_processes_replay_across_owner_restart(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root = tmp_path_factory.mktemp("cfgrun")
    child, ready = start(root / "session", resume=False)
    try:
        status = json.loads(command("status", "--socket", ready["socket"]).stdout)["data"]
        args = [
            "disable-venue",
            "--socket",
            ready["socket"],
            "--revision",
            str(status["revision"]),
            "--universe-revision",
            str(status["universe"]["revision"]),
            "--venue",
            "synthetic-0",
            "--retirement",
            Retirement.HOLD,
            "--reason",
            "process-fixture",
            "--request",
            str(root / "disable.json"),
        ]
        assert command(*args).returncode == 3
        accepted = command(*args, "--confirm")
        assert accepted.returncode == 0, accepted.stderr
        receipt = json.loads(accepted.stdout)["data"]["receipt"]
        child.kill()
        child.communicate(timeout=5)
        child, resumed = start(root / "session", resume=True)
        args[args.index("--socket") + 1] = resumed["socket"]
        replay = command(*args, "--confirm")
        assert replay.returncode == 0, replay.stderr
        assert json.loads(replay.stdout)["data"]["receipt"] == receipt
        final = json.loads(command("status", "--socket", resumed["socket"]).stdout)["data"]
        assert final["universe"]["revision"] == 2 and final["admission_halted"]
        assert final["allocations"][0]["mode"] == "hold_inventory"
        assert final["universe"]["policy"]["enabled_venues"] == []
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
