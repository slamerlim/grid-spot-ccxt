import asyncio
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from persistence.conftest import SOURCES

from grid.domain.numbers import DomainError
from grid.exchange.models import ReadError
from grid.paper.engine import PaperEngine
from grid.paper.journal import Journal, clock
from grid.paper.recovery import claim_replacement, interrupted
from grid.paper.runner import LIMITS, Observer
from grid.paper.wire import ObservedWire
from paper.test_journal_wire import BOOK, MARKET, Wire


@pytest.mark.usefixtures("async_sockets")
def test_interrupted_ledger_verified_and_replacement_claim_is_single_use(tmp_path: Path) -> None:
    async def run() -> None:
        prior, child = tmp_path / "prior", tmp_path / "child"
        prior.mkdir(mode=0o700)
        child.mkdir(mode=0o700)
        journal = Journal(prior)
        journal.append("start", {"observed_clock": True}, *clock())
        engine = await PaperEngine.open(journal, SOURCES)
        await engine.close()
        original = (prior / "observations.jsonl").read_bytes()
        journal.close()
        await interrupted(prior, SOURCES)
        assert (prior / "observations.jsonl").read_bytes().startswith(original)
        report = json.loads((prior / "report.json").read_text())
        assert report["result"] == "partial-interrupted"
        assert report["paper"]["unallocated_synthetic_usdt"] == "1000"
        credit = claim_replacement(prior, child)
        assert credit["prior_attempts"] == 0
        with pytest.raises(FileExistsError):
            claim_replacement(prior, child)
        with pytest.raises(DomainError):
            await interrupted(prior, SOURCES)

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_replacement_spends_original_global_budget_and_slows_polling(tmp_path: Path) -> None:
    async def run() -> None:
        now = [10000]
        journal = Journal(tmp_path)
        journal.append("start", {"prior_attempts": 748, "prior_instruments": 2}, 10000, 10**10)
        wire = Wire()
        observed = ObservedWire(journal, wire, lambda: (now[0], now[0] * 1000000))
        await observed.get(MARKET, LIMITS)
        now[0] += 5000
        with pytest.raises(ReadError, match="RATE_LIMITED"):
            await observed.get(BOOK, LIMITS)
        now[0] += 2500
        with pytest.raises(ReadError, match="BUDGET_EXHAUSTED"):
            await observed.get(MARKET, LIMITS)
        await observed.get(BOOK, LIMITS)
        now[0] += 7500
        with pytest.raises(ReadError, match="BUDGET_EXHAUSTED"):
            await observed.get(BOOK, LIMITS)
        assert wire.calls == 2
        journal.close()

    asyncio.run(run())


@pytest.mark.parametrize("exit_codes", [(75, 0), (1,), (0,), (75, 75)])
def test_coordinator_only_restarts_one_explicit_75_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_codes: tuple[int, ...],
) -> None:
    values = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/supervise_paper.py"))
    commands: list[list[str]] = []

    class Child:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            self.pid = 100 + len(commands)
            self.returncode: int | None = None
            commands.append(command)
            self.code = exit_codes[len(commands) - 1]

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: int) -> int:
            self.returncode = self.code
            return self.code

    monkeypatch.setattr(subprocess, "Popen", Child)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "observer",
            "--directory",
            str(tmp_path),
            "--approved-sqlite-source",
            "mock",
            "--replacement-for",
            str(tmp_path / "prior"),
        ],
    )
    code = values["main"]()
    assert len(commands) == len(exit_codes)
    assert "--resume" not in commands[0]
    if len(commands) == 2:
        assert "--resume" in commands[1]
    assert code == (1 if exit_codes[-1] == 75 else exit_codes[-1])


@pytest.mark.usefixtures("async_sockets")
def test_worker_stops_before_network_when_observed_parent_dies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        journal = Journal(tmp_path)
        journal.append("start", {"observed_clock": False}, *clock())
        monkeypatch.setattr(os, "getppid", lambda: child.pid)
        wire = Wire()
        observer = Observer(journal, SOURCES, wire, observer_pid=child.pid)
        try:
            child.terminate()
            child.wait(timeout=5)
            assert await observer.run() == 1
            assert wire.calls == 0
            assert json.loads((tmp_path / "report.json").read_text())["result"] == "partial-fault"
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            journal.close()

    asyncio.run(run())
