"""Logical I/O accounting and effective ceilings, never implied Linux RSS quotas."""

import ctypes
import math
import os
import resource
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from grid.domain.numbers import DomainError
from grid.research.history import RECORD, records, validate
from grid.research.resources import Caps, ReadBudget, guard_parent, reading

from .conftest import MakeReplay


def test_integer_bucket_paces_before_read_and_enforces_total() -> None:
    now = [0]
    sleeps: list[int] = []

    def sleep(seconds: float) -> None:
        elapsed = math.ceil(seconds * 1_000_000_000)
        sleeps.append(elapsed)
        now[0] += elapsed

    caps = Caps(1, 128 * 1024**2, 1024, 48, 96)
    budget = ReadBudget(caps, clock=lambda: now[0], sleep=sleep)
    for _ in range(4):
        budget.charge(24)
    assert budget.total == 96
    assert sleeps == [23_437_500, 23_437_500]
    assert budget.wait_ns == sum(sleeps)
    with pytest.raises(DomainError, match="exhausted"):
        budget.charge(1)
    assert budget.total == 96 and len(sleeps) == 2


def test_pause_credit_is_capped_and_regressing_clock_refuses() -> None:
    now = [0]
    budget = ReadBudget(Caps(1, 128 * 1024**2, 1024, 48, 200), clock=lambda: now[0])
    budget.charge(48)
    now[0] += 1_000_000_000_000
    budget.charge(24)
    assert budget.credit == 24 * 1_000_000_000  # Not unlimited credit after a long pause.
    now[0] -= 1
    with pytest.raises(DomainError, match="regressed"):
        budget.charge(24)


@pytest.mark.parametrize("mode", ["sequential", "mmap"])
def test_history_budget_refuses_before_next_record_and_restores_context(
    make_replay: MakeReplay,
    mode: str,
) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 2)
    source = spec.allocations[0].source
    original = source.path.read_bytes()
    outer = ReadBudget(Caps(1, 128 * 1024**2, 1024, 48, 24))
    inner = ReadBudget(replace(outer.caps, total_read_bytes=48))
    with reading(outer):
        with reading(inner):
            assert validate(source).accepted == 2
        iterator = records(source, mode)  # type: ignore[arg-type]
        assert len(next(iterator)) == RECORD.size
        with pytest.raises(DomainError, match="exhausted"):
            next(iterator)
    assert len(tuple(records(source))) == 2
    assert outer.total == 24 and inner.total == 48
    assert source.path.read_bytes() == original


@pytest.mark.parametrize("kind", ["cpu", "address_space"])
def test_child_os_limits_actually_stop_excess_consumption(kind: str, tmp_path: Path) -> None:
    code = """
from grid.research.resources import Caps
caps=Caps(1,128*1024**2,1024,24,24)
evidence=caps.apply_process()
assert evidence['cpu_seconds']==(1,1)
assert evidence['address_space_bytes']==(128*1024**2,128*1024**2)
assert evidence['nice']==19
"""
    if kind == "cpu":
        code += "\nwhile True: pass\n"
    else:
        code += """
try:
    data=bytearray(128*1024**2)
except MemoryError:
    print('memory refused')
else:
    raise SystemExit(7)
"""
    before = os.sched_getaffinity(0)
    process = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        timeout=10,
        env={"PATH": os.defpath, "LANG": "C.UTF-8"},
    )
    if kind == "cpu":
        assert process.returncode in (-signal.SIGKILL, -signal.SIGXCPU)
    else:
        assert process.returncode == 0, process.stderr
        assert process.stdout == b"memory refused\n"
    assert os.sched_getaffinity(0) == before
    assert not list(tmp_path.iterdir())  # No core file or generated artifacts.


def test_internal_worker_does_not_echo_invalid_arguments() -> None:
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "grid.research.resource_worker",
            "sensitive-placeholder",
            "--unknown-sensitive-value",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert process.returncode == 2
    assert "sensitive" not in process.stderr and not process.stdout


def test_parent_guard_refuses_foreign_parent_before_any_kernel_change() -> None:
    with pytest.raises(DomainError, match="parent changed before"):
        guard_parent(os.getpid())


def test_parent_guard_closes_death_before_signal_installation_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parents = iter((1000, 1001))

    def prctl(operation: int, argument: int, *unused: int) -> int:
        if operation == 2:
            ctypes.c_int.from_address(argument).value = signal.SIGKILL
        return 0

    def library(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(prctl=prctl)

    monkeypatch.setattr(os, "getppid", lambda: next(parents))
    monkeypatch.setattr(ctypes, "CDLL", library)
    with pytest.raises(DomainError, match="while installing"):
        guard_parent(1000)


def test_worker_never_relaxes_inherited_process_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    inherited = {
        resource.RLIMIT_CPU: (1, 60),
        resource.RLIMIT_AS: (256 * 1024**2, 512 * 1024**2),
        resource.RLIMIT_CORE: (0, 0),
        resource.RLIMIT_FSIZE: (4096, 4096),
        resource.RLIMIT_NOFILE: (16, 64),
    }
    affinity = [{3, 7}]

    def getlimit(kind: int) -> tuple[int, int]:
        return inherited[kind]

    def setlimit(kind: int, values: tuple[int, int]) -> None:
        inherited[kind] = values

    def setaffinity(pid: int, values: set[int]) -> None:
        affinity[0] = values

    monkeypatch.setattr(resource, "getrlimit", getlimit)
    monkeypatch.setattr(resource, "setrlimit", setlimit)
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: affinity[0])
    monkeypatch.setattr(os, "sched_setaffinity", setaffinity)
    monkeypatch.setattr(os, "setpriority", lambda *args: None)
    monkeypatch.setattr(os, "getpriority", lambda *args: 19)
    evidence = Caps(30, 512 * 1024**2, 1024, 24, 24).apply_process()
    assert evidence["cpu_seconds"] == (1, 1)
    assert evidence["address_space_bytes"] == (256 * 1024**2,) * 2
    assert evidence["open_files"] == (16, 16)
    assert evidence["file_size_bytes"] == (0, 0)
    assert evidence["affinity"] == (3,)
