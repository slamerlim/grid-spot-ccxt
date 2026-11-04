"""Distinct durable fills, bounded overload and supervised research lifecycle."""

import asyncio
import json
import os
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from persistence.conftest import SOURCE_ID, SOURCES

from grid.domain.numbers import DomainError
from grid.operations import contention
from grid.operations.__main__ import main
from grid.operations.contention import CAPS, PASSES, Worker, case, history
from grid.research.artifacts import digest


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize("venues", [1, 7])
def test_distinct_partial_fills_survive_pressure_duplicates_and_reopen(
    tmp_path: Path,
    venues: int,
) -> None:
    before_affinity = os.sched_getaffinity(0)
    result: Any = asyncio.run(case(tmp_path, SOURCES, venues, "test-revision"))
    assert result["distinct_fills"] == venues * 40
    assert all(
        value for name, value in result["checks"].items() if name != "within_declared_fill_lag"
    )
    assert result["rejected_hints"] == 48
    assert result["intake_peak"] == result["intake_capacity"] == 16
    assert len(result["fill_lag_ns"]) == venues * 40
    assert result["checks"]["within_declared_fill_lag"] is (
        max(result["fill_lag_ns"]) <= result["fill_lag_limit_ns"]
    )
    assert result["worker_stopped"]["state"] == result["worker_paused_after_drain"]["state"] == "T"
    assert result["worker_stopped"]["cpu_ticks"] == result["worker_paused_after_drain"]["cpu_ticks"]
    done = result["worker_done"]
    assert done["logical_read_bytes"] == CAPS.total_read_bytes
    assert done["result_hashes"] == [result["unpaced_result_sha256"]] * PASSES
    assert done["requested_read_wait_ns"] > 0
    assert done["cpu_ns"] > 0 and done["lifetime_rss_peak_bytes"] > 0
    enforced = result["worker_ready"]["enforced"]
    assert enforced["address_space_bytes"] == [CAPS.address_space_bytes] * 2
    assert enforced["cpu_seconds"] == [CAPS.cpu_seconds] * 2
    assert enforced["parent_death_signal"] == signal.SIGKILL
    assert enforced["rss_quota"].startswith("NOT_APPLIED")
    assert enforced["device_io_quota"].startswith("NOT_APPLIED")
    assert result["financial_sha256"] == result["reopened_financial_sha256"]
    assert result["external_api_calls"] == 0
    assert os.sched_getaffinity(0) == before_affinity


@pytest.mark.usefixtures("async_sockets")
def test_stopped_worker_cleanup_reaps_child(tmp_path: Path) -> None:
    async def run() -> None:
        worker = await Worker.start(history(tmp_path, "test-revision"))
        try:
            await worker.frame("ready")
            await worker.go()
            await worker.frame("active")
            await worker.pause()
            assert Path(f"/proc/{worker.process.pid}").exists()
        finally:
            await worker.close()
        assert worker.process.returncode is not None
        assert not Path(f"/proc/{worker.process.pid}").exists()

    asyncio.run(run())


def test_killed_parent_cannot_leave_stopped_research_orphan(tmp_path: Path) -> None:
    config = history(tmp_path, "test-revision")
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("contention_crash_child.py")),
            "supervisor",
            str(config),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"died_with_parent": True, "worker_exit": -signal.SIGKILL}


@pytest.mark.usefixtures("async_sockets")
def test_read_budget_failure_has_no_done_result_or_orphan(tmp_path: Path) -> None:
    async def run() -> None:
        worker = await Worker.start(
            history(tmp_path, "test-revision"), replace(CAPS, total_read_bytes=24)
        )
        try:
            await worker.frame("ready")
            await worker.go()
            with pytest.raises(DomainError, match="incomplete"):
                await worker.frame("active")
        finally:
            await worker.close()
        assert worker.process.returncode == 2
        assert not Path(f"/proc/{worker.process.pid}").exists()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_cancel_during_child_launch_cannot_orphan_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        created: list[asyncio.subprocess.Process] = []
        original = asyncio.create_subprocess_exec
        ready, release = asyncio.Event(), asyncio.Event()

        async def delayed(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
            process = await original(*args, **kwargs)
            created.append(process)
            ready.set()
            await release.wait()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
        task = asyncio.create_task(Worker.start(history(tmp_path, "test-revision")))
        await ready.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(created) == 1 and created[0].returncode is not None
        assert not Path(f"/proc/{created[0].pid}").exists()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize("timing_pass", [True, False])
def test_cli_contention_envelope_and_exclusive_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timing_pass: bool
) -> None:
    if not timing_pass:
        monkeypatch.setattr(contention, "FILL_LAG_LIMIT_NS", 0)
    output = tmp_path / "contention.json"
    args = [
        "contention",
        "--code-revision",
        "a" * 40,
        "--sqlite-source-id",
        SOURCE_ID,
        "--output",
        str(output),
    ]
    code = main(args)
    assert code in ((0, 1) if timing_pass else (1,))
    report = json.loads(output.read_bytes())
    assert report["schema"] == "grid-research-run-v1"
    assert report["result"]["schema"] == "grid-resource-contention-v1"
    assert report["result"]["resource_checks_passed"] is (code == 0)
    assert report["operation"] == "resource-contention"
    assert report["result_payload_sha256"] == digest(report["result"])
    assert [case["distinct_fills"] for case in report["result"]["cases"]] == [40, 280]
    original = output.read_bytes()
    assert main(args) == 2 and output.read_bytes() == original


@pytest.mark.usefixtures("async_sockets")
def test_failed_case_does_not_publish_partial_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = contention.case

    async def fail(
        directory: Path, sources: frozenset[str], venues: int, revision: str
    ) -> dict[str, object]:
        if venues == 7:
            raise DomainError("invented failed second case")
        return await original(directory, sources, venues, revision, fills_per_scope=10)

    monkeypatch.setattr(contention, "case", fail)
    output = tmp_path / "failed.json"
    assert (
        main(
            [
                "contention",
                "--code-revision",
                "a" * 40,
                "--sqlite-source-id",
                SOURCE_ID,
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()
