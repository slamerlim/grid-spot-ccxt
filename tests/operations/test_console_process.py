import json
import select
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from persistence.conftest import SOURCES

from grid.cli import main


def command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "grid", "operator", *args],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def start(directory: Path, *, resume: bool) -> tuple[subprocess.Popen[str], dict[str, Any]]:
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "grid",
            "operator",
            "serve-mock",
            "--directory",
            str(directory),
            "--sqlite-source-id",
            next(iter(SOURCES)),
            "--seconds",
            "30",
            *(["--resume"] if resume else []),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert child.stdout is not None
        assert select.select([child.stdout], [], [], 10)[0], "mock owner did not become ready"
        line = child.stdout.readline()
        assert line, "mock owner exited before readiness"
        return child, json.loads(line)
    except BaseException:
        child.kill()
        child.communicate(timeout=5)
        raise


def test_separate_cli_processes_and_sigkill_preserve_one_mock_order(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root = tmp_path_factory.mktemp("console")
    directory, packet = root / "session", root / "resume.json"
    child, ready = start(directory, resume=False)
    try:
        result = command("status", "--socket", ready["socket"])
        assert result.returncode == 0, result.stderr
        status = json.loads(result.stdout)
        args = [
            "resume",
            "--socket",
            ready["socket"],
            "--revision",
            str(status["data"]["revision"]),
            "--all",
            "--reason",
            "process-test",
            "--request",
            str(packet),
        ]
        assert command(*args).returncode == 3
        assert command(*args, "--confirm").returncode == 0
        world = directory / "world/observations.jsonl"

        def accepted_orders() -> int:
            return sum(
                json.loads(line)["kind"] == "venue"
                and json.loads(line)["payload"]["kind"] == "submit"
                for line in world.read_text().splitlines()
            )

        for _ in range(20):
            state = json.loads(command("status", "--socket", ready["socket"]).stdout)["data"]
            if accepted_orders() == 1:
                break
        assert state["allocations"][0]["working"] == 1
        assert accepted_orders() == 1
        child.kill()
        assert child.wait(timeout=5) < 0
        child.communicate(timeout=5)
        child, resumed = start(directory, resume=True)
        assert resumed["session_id"] == ready["session_id"]
        assert resumed["socket"] != ready["socket"]
        args[2] = resumed["socket"]
        replay = command(*args, "--confirm")
        assert replay.returncode == 0, replay.stderr
        assert json.loads(replay.stdout)["data"]["admission_halted"] is True
        state = json.loads(command("status", "--socket", resumed["socket"]).stdout)["data"]
        assert state["allocations"][0]["working"] == 1 and state["live"] == "DISABLED"
        assert accepted_orders() == 1
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_operator_parser_does_not_echo_untrusted_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as error:
        main(["operator", "do-not-reflect-this-input"])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "do-not-reflect-this-input" not in output.out + output.err


@pytest.mark.parametrize("scoped", [False, True])
def test_process_limit_liquidation_requires_reviewed_policy_and_second_confirmation(
    tmp_path_factory: pytest.TempPathFactory,
    scoped: bool,
) -> None:
    root = tmp_path_factory.mktemp("liquidate")
    child, ready = start(root / "session", resume=False)
    try:
        status = json.loads(command("status", "--socket", ready["socket"]).stdout)["data"]
        args = [
            "limit-liquidate",
            "--socket",
            ready["socket"],
            "--revision",
            str(status["revision"]),
            *(["--allocation", "allocation-0"] if scoped else ["--all"]),
            "--reason",
            "process-test",
            "--request",
            str(root / "exit.json"),
        ]
        assert command(*args).returncode == 2  # No cycle/policy approval supplied.
        assert not (root / "exit.json").exists()
        args += ["--cycle", status["cycle"]["id"], "--policy-sha256", status["cycle_policy_sha256"]]
        assert command(*args, "--confirm").returncode == 2
        assert not (root / "exit.json").exists()
        assert command(*args).returncode == 3
        prepared = (root / "exit.json").read_bytes()
        for _ in range(10):
            result = command(*args, "--confirm")
            if result.returncode == 0:
                break
            # A concurrent reconciliation may correctly refuse fresh exit admission.
            # Inspect status and explicitly repeat the SAME saved request, never a new ID.
            assert json.loads(result.stdout)["error"] == "refused", result.stderr
            inspected = command("status", "--socket", ready["socket"])
            assert inspected.returncode == 0, inspected.stderr
            assert json.loads(inspected.stdout)["data"]["revision"] in (
                status["revision"],
                status["revision"] + 1,
            )
        assert result.returncode == 0, result.stderr
        accepted = json.loads(result.stdout)["data"]
        assert accepted["exit_cause"] == (None if scoped else "operator")
        assert accepted["admission_halted"]
        replay = command(*args, "--confirm")
        assert replay.returncode == 0, replay.stderr
        assert json.loads(replay.stdout)["data"]["receipt"] == accepted["receipt"]
        assert (root / "exit.json").read_bytes() == prepared
        final = json.loads(command("status", "--socket", ready["socket"]).stdout)["data"]
        assert final["exit_cause"] == (None if scoped else "operator")
        assert final["target_reached_at_exit"] is (None if scoped else False)
        assert bool(final["cycle"]["allocation_exits"]) == scoped
        if scoped:
            assert final["cycle"]["phase"] == "active"
            assert final["last_action"] == "liquidate_allocation"
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
