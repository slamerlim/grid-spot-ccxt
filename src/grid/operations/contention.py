"""Finite synthetic fills beside a disposable paced replay process; no live owner."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from grid.domain.model import ActualFee, Scope
from grid.domain.numbers import DomainError, bounded_int
from grid.execution.executor import MockExecutor
from grid.execution.models import Evidence, Notice
from grid.execution.synthetic import SyntheticExchange
from grid.operations.experiment import host
from grid.operations.synthetic import EXECUTION, STORAGE, fixture
from grid.persistence import codec
from grid.persistence.ledger import Ledger, trade_identity
from grid.persistence.records import Backpressure
from grid.persistence.writer import AsyncWriter
from grid.research.artifacts import digest, publish
from grid.research.config import bundle, load
from grid.research.demo import create
from grid.research.history import MINUTE, RECORD
from grid.research.provenance import Observation
from grid.research.replay import run as replay
from grid.research.resources import Caps

PASSES = 3
BARS = 1280
CAPS = Caps(30, 512 * 1024**2, 65536, 4096, 2 * RECORD.size * BARS * PASSES)
CASE_TIMEOUT_SECONDS = 60
FILL_LAG_LIMIT_NS = 10_000_000_000  # Declared synthetic check, never a live SLO.


def process_sample(pid: int) -> dict[str, int | str]:
    raw = Path(f"/proc/{pid}/stat").read_text()
    fields = raw[raw.rfind(")") + 2 :].split()
    return {
        "observed_monotonic_ns": time.monotonic_ns(),
        "state": fields[0],
        "cpu_ticks": int(fields[11]) + int(fields[12]),
        "start_ticks": int(fields[19]),
        "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
    }


class Worker:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.paused = False
        self.identity = process_sample(process.pid)["start_ticks"]

    @classmethod
    async def start(cls, config: Path, caps: Caps = CAPS) -> Worker:
        launch = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-B",
                "-m",
                "grid.research.resource_worker",
                str(config),
                "--parent-pid",
                str(os.getpid()),
                "--passes",
                str(PASSES),
                "--cpu-seconds",
                str(caps.cpu_seconds),
                "--address-space-bytes",
                str(caps.address_space_bytes),
                "--read-rate",
                str(caps.read_bytes_per_second),
                "--read-burst",
                str(caps.read_burst_bytes),
                "--read-total",
                str(caps.total_read_bytes),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=8192,
                start_new_session=True,
                env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            )
        )
        try:
            process = await asyncio.shield(launch)
        except BaseException:
            process = await launch
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        try:
            return cls(process)
        except BaseException:
            process.kill()
            await process.wait()
            raise

    def sample(self) -> dict[str, int | str]:
        if self.process.returncode is not None:
            raise DomainError("research worker ended before expected handshake")
        result = process_sample(self.process.pid)
        if result["start_ticks"] != self.identity or result["state"] in ("Z", "X"):
            raise DomainError("research worker identity or lifecycle changed")
        return result

    async def frame(self, kind: str) -> dict[str, Any]:
        if self.process.stdout is None:
            raise DomainError("worker output unavailable")
        async with asyncio.timeout(45):
            raw = await self.process.stdout.readline()
        if not raw.endswith(b"\n") or len(raw) > 8192:
            raise DomainError("incomplete or oversized worker response")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("kind") != kind:
            raise DomainError("unexpected worker protocol state")
        return data

    async def go(self) -> None:
        if self.process.stdin is None:
            raise DomainError("worker input unavailable")
        self.process.stdin.write(b"go\n")
        await self.process.stdin.drain()

    async def pause(self) -> dict[str, int | str]:
        self.sample()
        started = time.monotonic_ns()
        self.process.send_signal(signal.SIGSTOP)
        async with asyncio.timeout(2):
            while (sample := self.sample())["state"] != "T":
                await asyncio.sleep(0.001)
        self.paused = True
        return {**sample, "pause_ack_ns": time.monotonic_ns() - started}

    def resume(self) -> None:
        if not self.paused or self.sample()["state"] != "T":
            raise DomainError("worker is not confirmed paused")
        self.process.send_signal(signal.SIGCONT)
        self.paused = False

    async def close(self) -> None:
        async def finish() -> None:
            if self.process.returncode is None:
                # SIGKILL also terminates a stopped child; never resume orphan research on failure.
                self.process.kill()
            async with asyncio.timeout(5):
                await self.process.wait()
            if self.process.stdin is not None:
                self.process.stdin.close()

        task = asyncio.create_task(finish())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise


def financial(ledger: Ledger, scopes: tuple[Scope, ...]) -> dict[str, object]:
    states = tuple(ledger.snapshot(scope).state for scope in scopes)
    if any(state is None for state in states):
        raise DomainError("contention allocation missing")
    money = tuple(
        (state.scope, state.quote_cash, state.owned_base, state.position_cost, state.realized_pnl)
        for state in states
        if state is not None
    )
    fills = tuple(
        ledger.db.execute(
            "SELECT allocation,trade_id,payload FROM fills ORDER BY allocation,trade_id"
        )
    )
    return {"money": money, "fills": fills, "sha256": digest((money, fills))}


def history(directory: Path, revision: str) -> Path:
    original = load(create(directory / "demo", revision))
    path = directory / "history.bin"
    with path.open("xb") as stream:
        for i in range(BARS):
            # Invented alternating moves produce real reducer work and repeatable results.
            close = 100 if i % 8 < 4 else 110
            stream.write(
                RECORD.pack(original.start_ms + i * MINUTE, close, close + 2, close - 2, close)
            )
    allocation = original.allocations[0]
    spec = replace(
        original,
        end_ms=original.start_ms + BARS * MINUTE,
        interval_bars=64,
        allocations=(
            replace(allocation, source=replace(allocation.source, path=path, count=BARS)),
        ),
    )
    config = directory / "replay.json"
    publish(config, bundle(spec))
    return config


async def case(
    directory: Path,
    sources: frozenset[str],
    venues: int,
    revision: str,
    *,
    fills_per_scope: int = 40,
) -> dict[str, object]:
    bounded_int(venues, 1, 7)
    # Decimal exact equal partials of a one-unit seed, each meeting the amount quantum.
    if fills_per_scope not in (10, 20, 40, 50):
        raise DomainError("unsupported synthetic partial-fill count")
    config = history(directory, revision)
    baseline_result = replay(load(config))
    baseline = baseline_result["result_sha256"]
    sample = fixture(venues, venues)
    # These are partial executions of one minimum entry, never enlarged new entries.
    contexts = dict(sample.contexts)
    scopes = sample.scopes
    venue = SyntheticExchange(1000)
    path = directory / "ledger.db"
    limits = replace(EXECUTION, page_size=128, max_records=1000, max_pages=20)
    writer = await AsyncWriter.open(path, STORAGE, sources, mock_storage=True)
    executor: MockExecutor | None = None
    worker: Worker | None = None
    fills_created: dict[str, int] = {}
    lags: list[int] = []
    service_ns: list[int] = []
    max_depth = max_writer = rejected = 0
    try:
        await writer.submit(sample.prepare)
        executor = MockExecutor(writer, venue, scopes, limits, Evidence(True, True, True))
        for scope in scopes:
            if not await executor.service(scope, contexts[scope]) or not await executor.dispatch(
                scope, contexts[scope]
            ):
                raise DomainError("synthetic seed setup refused")
        bindings = {fact.request.scope: key for key, fact in venue.orders.items()}
        if len(bindings) != venues or any(codec.parse_amount(r.amount) != 1 for r in venue.sent):
            raise DomainError("contention fixture requires one minimum seed per scope")
        worker = await Worker.start(config)
        ready = await worker.frame("ready")
        await worker.go()
        await worker.frame("active")
        overlap_before = worker.sample()

        def fill(scope: Scope, index: int) -> None:
            name = f"fill-{scope.venue}-{index}"
            venue.fill(
                bindings[scope],
                name,
                D(1) / D(fills_per_scope),
                D(100),
                (ActualFee("QUOTE", D(0), None),),
            )
            fills_created[name] = time.monotonic_ns()

        async def service(scope: Scope) -> None:
            nonlocal max_writer
            started = time.monotonic_ns()
            if executor is None or not await executor.service(scope, contexts[scope]):
                raise DomainError("synthetic fill reconciliation failed")
            finished = time.monotonic_ns()
            service_ns.append(finished - started)
            max_writer = max(max_writer, writer.depth)
            for trade in venue.trades:
                if trade.scope == scope and trade.external_id in fills_created:
                    lags.append(finished - fills_created.pop(trade.external_id))

        # Ordinary fill handling genuinely overlaps an in-flight research job before overload.
        for scope in scopes:
            fill(scope, 0)
            executor.offer(Notice(scope, executor.epoch, "trade"))
            await service(scope)
        overlap_after = worker.sample()
        for scope in scopes:
            for i in range(1, fills_per_scope):
                fill(scope, i)
        # Duplicate notifications deliberately exceed bounded intake; facts stay in the fake venue.
        for index in range(limits.queue_size * 4):
            scope = scopes[index % venues]
            try:
                executor.offer(Notice(scope, executor.epoch, "trade"))
            except Backpressure:
                rejected += 1
            max_depth = max(max_depth, executor.intake_depth)
        if not rejected or not executor.degraded:
            raise DomainError("synthetic pressure was not exercised")
        stopped = await worker.pause()
        for scope in scopes:
            if await executor.dispatch(scope, contexts[scope]):
                raise DomainError("overload allowed new order submission")
            await service(scope)
        paused_end = worker.sample()
        if (
            paused_end["state"] != "T"
            or paused_end["cpu_ticks"] != stopped["cpu_ticks"]
            or executor.degraded
            or executor.intake_depth
            or writer.depth
            or fills_created
        ):
            raise DomainError("research pause or fill drain evidence incomplete")
        before = await writer.submit(lambda ledger: financial(ledger, scopes))
        expected_count = venues * fills_per_scope
        if len(before["fills"]) != expected_count:
            raise DomainError("durable fill count differs from distinct venue facts")
        if {row[1] for row in before["fills"]} != {
            trade_identity(trade.scope, trade.external_id) for trade in venue.trades
        }:
            raise DomainError("durable fill identities differ from distinct venue facts")
        for scope in scopes:
            state = (await writer.submit(lambda ledger, scope=scope: ledger.snapshot(scope))).state
            if state is None or (state.quote_cash, state.owned_base, state.position_cost) != (
                D(900),
                D(1),
                D(100),
            ):
                raise DomainError("partial-fill funding conservation failed")
        worker.resume()
        done = await worker.frame("done")
        async with asyncio.timeout(5):
            code = await worker.process.wait()
        if (
            code != 0
            or done.get("result_hashes") != [baseline] * PASSES
            or done.get("logical_read_bytes") != CAPS.total_read_bytes
        ):
            raise DomainError("bounded worker did not preserve replay accounting/read count")
        # Duplicate reconciliation and a fresh writer open must retain every exact financial row.
        for scope in scopes:
            await service(scope)
        if await writer.submit(lambda ledger: financial(ledger, scopes)) != before:
            raise DomainError("duplicate reconciliation changed financial rows")
        await executor.close()
        executor = None
        await writer.close()
        writer = await AsyncWriter.open(path, STORAGE, sources, mock_storage=True)
        restored = await writer.submit(lambda ledger: financial(ledger, scopes))
        if restored != before or len(venue.sent) != venues:
            raise DomainError("restart lost fills or unexpected placement occurred")
        return {
            "venues": venues,
            "funded_allocations": venues,
            "distinct_fills": expected_count,
            "rejected_hints": rejected,
            "intake_capacity": limits.queue_size,
            "intake_peak": max_depth,
            "post_service_writer_depth_peak": max_writer,
            "fill_lag_ns": tuple(lags),
            "service_ns": tuple(service_ns),
            "fill_lag_limit_ns": FILL_LAG_LIMIT_NS,
            "overlap_worker_before": overlap_before,
            "overlap_worker_after": overlap_after,
            "worker_stopped": stopped,
            "worker_paused_after_drain": paused_end,
            "worker_ready": ready,
            "worker_done": done,
            "research_config": baseline_result["config"],
            "research_inputs": baseline_result["inputs"],
            "financial_fixture_sha256": digest(sample),
            "unpaced_result_sha256": baseline,
            "financial_sha256": before["sha256"],
            "reopened_financial_sha256": restored["sha256"],
            "checks": {
                "within_declared_fill_lag": max(lags) <= FILL_LAG_LIMIT_NS,
                "distinct_fills_durable": True,
                "duplicate_reconciliation_idempotent": True,
                "clean_writer_restart_exact": True,
                "conservation": True,
                "overload_blocks_new_orders": True,
                "research_stopped_during_drain": True,
                "research_resumed_same_results": True,
                "limit_only": all(request.order_type == "limit" for request in venue.sent),
            },
            "synthetic_page_calls": venue.reads,
            "external_api_calls": 0,
            "ledger_bytes_before_cleanup": path.stat().st_size,
        }
    finally:
        try:
            if worker is not None:
                await worker.close()
        finally:
            try:
                if executor is not None:
                    await executor.close()
            finally:
                await writer.close()


async def experiment(revision: str, source_id: str) -> dict[str, object]:
    observation = Observation.start(revision)
    rows = []
    with TemporaryDirectory(prefix="grid-contention-") as temporary:
        root = Path(temporary)
        profile = host(root)
        for venues in (1, 7):
            directory = root / f"venues-{venues}"
            directory.mkdir(mode=0o700)
            async with asyncio.timeout(CASE_TIMEOUT_SECONDS):
                rows.append(await case(directory, frozenset({source_id}), venues, revision))
    passed = True
    for row in rows:
        checks = row["checks"]
        if not isinstance(checks, dict) or not all(
            type(value) is bool for value in checks.values()
        ):
            raise DomainError("contention checks missing or malformed")
        passed = passed and all(checks.values())
    result = {
        "schema": "grid-resource-contention-v1",
        "mode": "synthetic_offline",
        "live": "DISABLED",
        "host": profile,
        "caps": CAPS,
        "research_bars": BARS,
        "research_passes": PASSES,
        "case_wall_timeout_seconds": CASE_TIMEOUT_SECONDS,
        "cases": rows,
        "resource_checks_passed": passed,
        "limitations": (
            "No real exchange, deployed owner, 48-hour soak or on-NVMe acceptance.",
            "CPU time/address-space ceilings are not CPU bandwidth or exact RSS quotas.",
            "Logical archive-byte pacing is not a kernel device-I/O quota or cold-cache test.",
            "Worker runs on one low-priority CPU; parent affinity and host cgroups are unchanged.",
            "Pause uses a verified owned child SIGSTOP; in-flight OS I/O may still finish.",
            "Only BUY seed partials and duplicate hints; mixed-side rates remain unmeasured.",
            "Writer reopens cleanly here; crash/power-loss evidence remains separate tests.",
            "Temporary inputs/ledgers are deleted on normal cleanup; reports retain hashes.",
        ),
    }
    return observation.finish("resource-contention", result)
