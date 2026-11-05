"""Mixed order lifecycles, late inventory, overload and owned-worker cleanup."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from persistence.conftest import SOURCE_ID, SOURCES

from grid.domain.numbers import DomainError
from grid.operations import mixed_contention as mixed
from grid.operations.__main__ import main
from grid.operations.contention import CAPS, PASSES, Worker
from grid.operations.mixed_contention import Mixed, case
from grid.operations.synthetic import STORAGE
from grid.persistence.writer import AsyncWriter
from grid.research.artifacts import digest


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize("venues,rounds", [(1, 3), (7, 2)])
def test_repeated_mixed_rounds_keep_inventory_cash_and_worker_ownership(
    tmp_path: Path, venues: int, rounds: int
) -> None:
    affinity = os.sched_getaffinity(0)
    result: Any = asyncio.run(case(tmp_path, SOURCES, venues, "test", rounds=rounds))
    assert result["distinct_fills"] == 5 * venues * rounds
    assert result["buy_fills"] == 2 * venues * rounds
    assert result["sell_fills"] == 3 * venues * rounds
    assert result["late_buy_fills"] == venues * (rounds - 1)
    assert result["seed_submissions"] == venues
    assert result["rejected_hints"] == 48 * rounds
    assert result["intake_peak"] == result["intake_capacity"] == 16
    assert result["external_sell_peak_per_allocation"] == 1
    assert result["cancel_ambiguity_blocks"] >= venues * rounds * 3
    assert len(result["fill_lag_ns"]) == result["distinct_fills"]
    assert all(
        value for key, value in result["checks"].items() if key != "within_declared_fill_lag"
    )
    assert result["checks"]["within_declared_fill_lag"] is (
        max(result["fill_lag_ns"]) <= result["fill_lag_limit_ns"]
    )
    assert result["financial_sha256"] == result["reopened_financial_sha256"]
    assert result["worker_concurrency"] == 1 and len(result["jobs"]) == rounds
    for index, job in enumerate(result["jobs"]):
        assert job["stopped"]["state"] == job["drained"]["state"] == "T"
        assert job["stopped"]["cpu_ticks"] == job["drained"]["cpu_ticks"]
        assert job["before"]["start_ticks"] == job["overlap"]["start_ticks"]
        assert job["done"]["result_hashes"] == [result["unpaced_result_sha256"]] * PASSES
        assert job["done"]["logical_read_bytes"] == CAPS.total_read_bytes
        assert job["exit_code"] == 0
        assert job["ready"]["enforced"]["address_space_bytes"] == [CAPS.address_space_bytes] * 2
        assert result["rounds"][index]["late_buy"] is (index + 1 < rounds)
    for _, cash, base, cost, realized in result["final_money"]:
        assert base == cost == 0 and cash == 1000 + realized
        if rounds == 3:
            assert str(cash) == "1051.25"  # Exact invented sequence, not a return forecast.
    assert result["external_api_calls"] == 0
    assert result["sampled_active_tasks_peak"] > 0 and result["sampled_wal_peak_bytes"] > 0
    assert os.sched_getaffinity(0) == affinity


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize("rounds", [0, 9, True])
def test_round_caps_precede_input_or_ledger_creation(tmp_path: Path, rounds: int) -> None:
    with pytest.raises(DomainError):
        asyncio.run(case(tmp_path, SOURCES, 1, "test", rounds=rounds))
    assert not list(tmp_path.iterdir())


@pytest.mark.usefixtures("async_sockets")
@pytest.mark.parametrize("fault", ["exception", "cancel"])
def test_failure_while_worker_stopped_reaps_child_and_releases_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    stopped: list[Worker] = []
    original = Worker.pause

    async def run() -> None:
        ready = asyncio.Event()

        async def pause(worker: Worker) -> dict[str, int | str]:
            await original(worker)
            stopped.append(worker)
            ready.set()
            if fault == "cancel":
                await asyncio.Event().wait()
            raise DomainError("injected stopped-worker failure")

        monkeypatch.setattr(Worker, "pause", pause)
        task = asyncio.create_task(case(tmp_path, SOURCES, 1, "test", rounds=1))
        await asyncio.wait_for(ready.wait(), 20)
        if fault == "cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if fault == "cancel" else DomainError):
            await task
        assert len(stopped) == 1 and stopped[0].process.returncode is not None
        assert not Path(f"/proc/{stopped[0].process.pid}").exists()
        writer = await AsyncWriter.open(tmp_path / "ledger.db", STORAGE, SOURCES, mock_storage=True)
        await writer.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_wrong_worker_result_is_not_a_successful_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Worker.frame
    workers = []

    async def frame(worker: Worker, kind: str) -> dict[str, Any]:
        value = await original(worker, kind)
        if kind == "done":
            workers.append(worker)
            value["result_hashes"] = ["wrong"] * PASSES
        return value

    monkeypatch.setattr(Worker, "frame", frame)
    with pytest.raises(DomainError, match="research results"):
        asyncio.run(case(tmp_path, SOURCES, 1, "test", rounds=1))
    assert len(workers) == 1 and workers[0].process.returncode == 0


@pytest.mark.usefixtures("async_sockets")
def test_duplicate_seed_or_unclosed_external_order_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Mixed.drain

    async def corrupted(self: Mixed, *args: Any, **kwargs: Any) -> None:
        await original(self, *args, **kwargs)
        # Deliberately forged venue observation cannot pass the exact close/duplicate checks.
        from dataclasses import replace

        key = next(iter(self.venue.orders))
        self.venue.orders[key] = replace(self.venue.orders[key], status="open")

    monkeypatch.setattr(Mixed, "drain", corrupted)
    with pytest.raises(DomainError):
        asyncio.run(case(tmp_path, SOURCES, 1, "test", rounds=1))


@pytest.mark.usefixtures("async_sockets")
def test_cli_publishes_failed_timing_honestly_and_refuses_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mixed, "FILL_LAG_LIMIT_NS", 0)
    output = tmp_path / "report.json"
    args = [
        "contention",
        "--mixed-rounds",
        "1",
        "--code-revision",
        "a" * 40,
        "--sqlite-source-id",
        SOURCE_ID,
        "--output",
        str(output),
    ]
    assert main(args) == 1
    report = json.loads(output.read_bytes())
    assert report["schema"] == "grid-research-run-v1"
    assert report["result"]["schema"] == "grid-mixed-contention-v1"
    assert not report["result"]["resource_checks_passed"]
    assert report["result_payload_sha256"] == digest(report["result"])
    assert [row["distinct_fills"] for row in report["result"]["cases"]] == [5, 35]
    before = output.read_bytes()
    assert main(args) == 2 and output.read_bytes() == before


def test_mixed_flag_is_only_for_contention(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        main(
            [
                "dry-run",
                "--mixed-rounds",
                "1",
                "--code-revision",
                "test",
                "--sqlite-source-id",
                SOURCE_ID,
                "--output",
                str(tmp_path / "report.json"),
            ]
        )
    assert error.value.code == 2


@pytest.mark.usefixtures("async_sockets")
def test_second_case_failure_never_publishes_partial_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original = mixed.case

    async def fail(
        directory: Path, sources: frozenset[str], venues: int, revision: str, *, rounds: int = 3
    ) -> dict[str, object]:
        if venues == 7:
            raise DomainError("sensitive-failure-marker")
        return await original(directory, sources, venues, revision, rounds=rounds)

    monkeypatch.setattr(mixed, "case", fail)
    output = tmp_path / "report.json"
    assert (
        main(
            [
                "contention",
                "--mixed-rounds",
                "1",
                "--code-revision",
                "test",
                "--sqlite-source-id",
                SOURCE_ID,
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists() and "sensitive-failure-marker" not in capsys.readouterr().err
