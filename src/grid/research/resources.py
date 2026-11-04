"""Explicit process ceilings and paced logical archive reads for offline workers."""

from __future__ import annotations

import os
import resource
import signal
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from grid.domain.numbers import DomainError, bounded_int


def guard_parent(expected: int) -> int:
    """Kill even a stopped child when its creating parent thread dies (Linux only)."""
    import ctypes

    bounded_int(expected, 1, 2**31 - 1)
    if os.getppid() != expected:
        raise DomainError("research parent changed before lifecycle guard")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    libc.prctl.restype = ctypes.c_int
    observed = ctypes.c_int()
    if (
        libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0  # PR_SET_PDEATHSIG
        or libc.prctl(2, ctypes.addressof(observed), 0, 0, 0) != 0  # PR_GET_PDEATHSIG
        or observed.value != signal.SIGKILL
    ):
        raise DomainError("research parent-death guard unavailable")
    # Close the race where the parent died before prctl installed the signal.
    if os.getppid() != expected:
        raise DomainError("research parent changed while installing lifecycle guard")
    return observed.value


@dataclass(frozen=True)
class Caps:
    cpu_seconds: int
    address_space_bytes: int
    read_bytes_per_second: int
    read_burst_bytes: int
    total_read_bytes: int

    def __post_init__(self) -> None:
        bounded_int(self.cpu_seconds, 1, 60)
        bounded_int(self.address_space_bytes, 128 * 1024**2, 1024**3)
        bounded_int(self.read_bytes_per_second, 1024, 64 * 1024**2)
        bounded_int(self.read_burst_bytes, 24, 1024**2)
        bounded_int(self.total_read_bytes, 24, 64 * 1024**2)

    def apply_process(self) -> dict[str, object]:
        """Call only inside a disposable worker, never in the financial owner."""
        for kind, maximum in (
            (resource.RLIMIT_CPU, self.cpu_seconds),
            (resource.RLIMIT_AS, self.address_space_bytes),
            (resource.RLIMIT_CORE, 0),
            (resource.RLIMIT_FSIZE, 0),
            (resource.RLIMIT_NOFILE, 32),
        ):
            inherited = resource.getrlimit(kind)
            effective = min(
                (maximum, *(value for value in inherited if value != resource.RLIM_INFINITY))
            )
            resource.setrlimit(kind, (effective, effective))
            if resource.getrlimit(kind) != (effective, effective):
                raise DomainError("worker resource ceiling was not applied")
        os.setpriority(os.PRIO_PROCESS, 0, 19)
        cpu = min(os.sched_getaffinity(0))
        os.sched_setaffinity(0, {cpu})
        if os.getpriority(os.PRIO_PROCESS, 0) != 19 or os.sched_getaffinity(0) != {cpu}:
            raise DomainError("worker scheduling priority was not applied")
        return {
            "cpu_seconds": resource.getrlimit(resource.RLIMIT_CPU),
            "address_space_bytes": resource.getrlimit(resource.RLIMIT_AS),
            "core_bytes": resource.getrlimit(resource.RLIMIT_CORE),
            "file_size_bytes": resource.getrlimit(resource.RLIMIT_FSIZE),
            "open_files": resource.getrlimit(resource.RLIMIT_NOFILE),
            "nice": os.getpriority(os.PRIO_PROCESS, 0),
            "affinity": (cpu,),
            "rss_quota": "NOT_APPLIED; address-space ceiling and observed RSS are distinct",
            "device_io_quota": "NOT_APPLIED; archive reads use logical-byte pacing",
        }


class ReadBudget:
    """Integer token bucket. Bounds requested archive bytes, not OS readahead/page cache."""

    def __init__(
        self,
        caps: Caps,
        *,
        clock: Callable[[], int] = time.monotonic_ns,
        sleep: Callable[[float], None] = time.sleep,
        progress: Callable[[int], None] | None = None,
    ) -> None:
        self.caps, self.clock, self.sleep, self.progress = caps, clock, sleep, progress
        self.total = self.wait_ns = 0
        self.credit = caps.read_burst_bytes * 1_000_000_000
        self.last = clock()

    def charge(self, count: int) -> None:
        bounded_int(count, 1, self.caps.read_burst_bytes)
        if self.total + count > self.caps.total_read_bytes:
            raise DomainError("research logical read budget exhausted")
        needed = count * 1_000_000_000
        while True:
            now = self.clock()
            if now < self.last:
                raise DomainError("research budget clock regressed")
            self.credit = min(
                self.caps.read_burst_bytes * 1_000_000_000,
                self.credit + (now - self.last) * self.caps.read_bytes_per_second,
            )
            self.last = now
            if self.credit >= needed:
                break
            wait = (needed - self.credit + self.caps.read_bytes_per_second - 1) // (
                self.caps.read_bytes_per_second
            )
            self.sleep(wait / 1_000_000_000)
            self.wait_ns += wait
        self.credit -= needed
        self.total += count
        if self.progress is not None:
            self.progress(self.total)


_READ_BUDGET: ContextVar[ReadBudget | None] = ContextVar("research_read_budget", default=None)


@contextmanager
def reading(budget: ReadBudget) -> Iterator[None]:
    token = _READ_BUDGET.set(budget)
    try:
        yield
    finally:
        _READ_BUDGET.reset(token)


def before_read(size: int) -> None:
    budget = _READ_BUDGET.get()
    if budget is not None:
        budget.charge(size)
