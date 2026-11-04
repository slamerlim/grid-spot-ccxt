"""Short, finite synthetic experiments. No real feed, credentials or soak pass."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import resource
import sqlite3
import time
from datetime import UTC, datetime
from decimal import Decimal as D
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

from grid.domain.model import ActualFee
from grid.domain.numbers import DomainError
from grid.execution.synthetic import SyntheticExchange
from grid.operations.model import Action, Command, Health
from grid.operations.supervisor import MockSupervisor
from grid.operations.synthetic import EXECUTION, OPERATIONS, STORAGE, Fixture, fixture
from grid.persistence.storage import filesystem
from grid.portfolio.model import CyclePhase
from grid.research.artifacts import digest
from grid.universe.selection import evaluate, select


def utc() -> str:
    return datetime.now(UTC).isoformat()


def source_digest() -> str:
    root = Path(__file__).resolve().parents[1]
    hasher = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        hasher.update(str(path.relative_to(root)).encode())
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def host(directory: Path) -> dict[str, object]:
    def optional(path: str) -> str | None:
        file = Path(path)
        return file.read_text().strip() if file.is_file() else None

    return {
        "os": os.uname().sysname,
        "kernel": platform.release(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "affinity_cpus": len(os.sched_getaffinity(0)),
        "memory_total": next(
            line
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemTotal:")
        ),
        "cgroup_cpu_max": optional("/sys/fs/cgroup/cpu.max"),
        "cgroup_memory_max": optional("/sys/fs/cgroup/memory.max"),
        "filesystem": filesystem(directory),
        "free_bytes_before": os.statvfs(directory).f_bavail * os.statvfs(directory).f_frsize,
    }


async def opened(
    path: Path,
    case: Fixture,
    venue: SyntheticExchange,
    now: list[int],
    sources: frozenset[str],
    *,
    prepare: bool,
) -> MockSupervisor:
    return await MockSupervisor.open(
        path,
        STORAGE,
        sources,
        case.scopes,
        venue,
        EXECUTION,
        OPERATIONS,
        lambda: (now[0], now[0] * 1000000),
        case.prepare if prepare else None,
    )


async def control(owner: MockSupervisor, action: Action, request: str) -> None:
    status = await owner.status()
    await owner.control(
        request,
        status.operations.revision,
        Command(
            action,
            owner.scopes,
            "synthetic-experiment",
            "invented fixture",
            status.observed_ms,
            action is Action.RESUME,
        ),
    )


async def initialize(owner: MockSupervisor, case: Fixture, now: list[int]) -> Health:
    await owner.observe(case.facts(now[0], 1))
    initial = await owner.step()
    await owner.universe.configure(
        "fixture-universe", 0, case.universe, "synthetic-experiment", "invented fixture", now[0]
    )
    await owner.portfolio.begin(
        "fixture-cycle",
        0,
        "fixture-cycle",
        case.portfolio,
        "synthetic-experiment",
        "invented fixture",
    )
    return initial


def metrics(samples: list[Health], venue: SyntheticExchange) -> dict[str, object]:
    return {
        "samples": len(samples),
        "sampled_rss_max_bytes": max(s.rss_bytes for s in samples),
        "process_lifetime_rss_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * 1024,
        "sampled_wal_max_bytes": max(s.wal_bytes for s in samples),
        "sampled_writer_depth_max": max(s.writer_depth for s in samples),
        "sampled_intake_depth_max": max(s.intake_depth for s in samples),
        "sampled_async_tasks_max": max(s.active_tasks for s in samples),
        "measured_step_max_ns": max(s.measured_step_ns for s in samples),
        "synthetic_page_calls": venue.reads,
        "synthetic_limit_submissions": len(venue.sent),
        "synthetic_cancel_requests": len(venue.cancels),
        "external_api_calls": 0,
        "ccxt_clients": 0,
        "synthetic_gateway_objects": 1,
        "sampling_limitation": "post-step samples can miss transient peaks; no per-venue CCXT load",
    }


async def dry_run(directory: Path, sources: frozenset[str]) -> dict[str, object]:
    case, venue, now = fixture(1, 1), SyntheticExchange(1000), [10000]
    path = directory / "mock.db"
    samples: list[Health] = []
    owner = await opened(path, case, venue, now, sources, prepare=True)
    try:
        samples.append(await initialize(owner, case, now))
        await control(owner, Action.RESUME, "resume")
        now[0] += 10
        await owner.observe(case.facts(now[0], 2))
        samples.append(await owner.step())
        owner.executor.degrade("synthetic disconnect")
        # A real process restart is separately tested. Here the writer/controllers
        # close and reopen in one process while the same finite fake venue survives.
        await owner.close()
        owner = await opened(path, case, venue, now, sources, prepare=False)
        await owner.observe(case.facts(now[0], 3))
        samples.append(await owner.step())
        if not samples[-1].admission_halted or len(venue.sent) != 1:
            raise DomainError("synthetic restart did not preserve ownership/halt")
        await control(owner, Action.RESUME, "reviewed-restart")
        venue.fill("order-1", "seed", D(1), D(100), (ActualFee("QUOTE", D(0), None),))
        for revision in (4, 5):
            now[0] += 10
            await owner.observe(case.facts(now[0], revision, D(200)))
            samples.append(await owner.step())
        venue.fill("order-2", "exit", D(1), D(200), (ActualFee("QUOTE", D(0), None),))
        now[0] += 10
        await owner.observe(case.facts(now[0], 6, D(200)))
        samples.append(await owner.step())
        final = samples[-1]
        if final.cycle is None or final.cycle.phase is not CyclePhase.COMPLETE:
            raise DomainError("synthetic cycle did not complete")
        if final.cycle.final_return != D("0.1") or final.allocations[0].base != 0:
            raise DomainError("synthetic conservation/return mismatch")
        backup = await owner.backup(directory / "backup.db", directory / "verified.db")
        await control(owner, Action.HALT, "final-halt")
        final = await owner.status()
        samples.append(final)
        return {
            "logical_venues": 1,
            "supervised_allocations": 1,
            "simulated_elapsed_ms": now[0] - 10000,
            "controller_restart_count": 1,
            "synthetic_disconnect_count": 1,
            "process_restart_count": 0,
            "synthetic_fills": tuple(venue.trades),
            "verified_backup": backup,
            "final_status": final,
            "metrics": metrics(samples, venue),
            "checks": {
                "explicit_restart_resume": True,
                "conservation": True,
                "limit_only": all(r.order_type == "limit" for r in venue.sent),
                "backup_restored_and_hashed": True,
            },
        }
    finally:
        await owner.close()


async def benchmark_case(
    directory: Path, sources: frozenset[str], venues: int, candidates: int
) -> dict[str, object]:
    # Candidate eligibility is measured separately from the funded owner roster.
    candidate_case = fixture(venues, candidates)
    began_cpu, began = time.process_time_ns(), time.monotonic_ns()
    facts = candidate_case.facts(10000, 1)
    assessed = tuple(
        (state, evaluate(state, fact, candidate_case.universe, 10000, 10000000000))
        for state, fact in zip(candidate_case.states(), facts.markets, strict=True)
    )
    selected = select(candidate_case.universe, assessed, (), 10000)
    selection_ns, selection_cpu_ns = time.monotonic_ns() - began, time.process_time_ns() - began_cpu
    case, venue, now = fixture(venues, venues), SyntheticExchange(1000), [10000]
    owner = await opened(directory / "mock.db", case, venue, now, sources, prepare=True)
    try:
        samples = [await initialize(owner, case, now)]
        await control(owner, Action.RESUME, "resume")
        for revision in range(2, 12):
            now[0] += 10
            await owner.observe(case.facts(now[0], revision))
            samples.append(await owner.step())
        return {
            "logical_venues": venues,
            "candidate_count": candidates,
            "eligible_candidates": sum(e.eligible for _, e in assessed),
            "selected_candidates": len(selected),
            "supervised_allocations": venues,
            "selection_wall_ns": selection_ns,
            "selection_cpu_ns": selection_cpu_ns,
            "simulated_elapsed_ms": now[0] - 10000,
            "metrics": metrics(samples, venue),
            "final_admission_halted": samples[-1].admission_halted,
        }
    finally:
        await owner.close()


async def experiment(kind: str, code_revision: str, source_id: str) -> dict[str, object]:
    if kind not in {"dry-run", "benchmark"} or re.fullmatch(r"[0-9a-f]{40}", code_revision) is None:
        raise DomainError("invalid synthetic experiment kind or revision")
    started, began, cpu = utc(), time.monotonic_ns(), time.process_time_ns()
    results: list[dict[str, object]] = []
    with TemporaryDirectory(prefix="grid-i8-mock-") as temporary:
        root = Path(temporary)
        profile = host(root)
        if kind == "dry-run":
            results.append(await dry_run(root, frozenset({source_id})))
        else:
            for venues in (1, 7):
                for candidates in (10, 100, 1000):
                    directory = root / f"{venues}-{candidates}"
                    directory.mkdir(mode=0o700)
                    tick, work = time.monotonic_ns(), time.process_time_ns()
                    result = await benchmark_case(
                        directory, frozenset({source_id}), venues, candidates
                    )
                    result.update(
                        observed_wall_ns=time.monotonic_ns() - tick,
                        process_cpu_ns=time.process_time_ns() - work,
                    )
                    results.append(result)
    return {
        "schema_version": 1,
        "kind": kind,
        "mode": "synthetic_offline",
        "live": "DISABLED",
        "i8_acceptance_complete": False,
        "observed_soak_complete": False,
        "started_utc": started,
        "finished_utc": utc(),
        "observed_wall_ns": time.monotonic_ns() - began,
        "process_cpu_ns": time.process_time_ns() - cpu,
        "code_revision_supplied": code_revision,
        "revision_verified_by_runner": False,
        "grid_python_source_sha256": source_digest(),
        "runtime": {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "sqlite_source_approved_for_mock": source_id,
            "ccxt_metadata": version("ccxt"),
        },
        "host": profile,
        "fixture_caps": {"operations": OPERATIONS, "storage": STORAGE, "execution": EXECUTION},
        "fixture_sha256": digest((fixture(1, 1), fixture(7, 7), OPERATIONS, STORAGE, EXECUTION)),
        "results": results,
        "retention": "temporary mock databases deleted after verification; caller retains report",
        "limitations": [
            "no real market/account/private-event evidence",
            "no 48-hour soak",
            "sampled resource metrics; not production sizing or target-host acceptance",
            "invented zero fees and deterministic fills; not execution performance",
        ],
    }
