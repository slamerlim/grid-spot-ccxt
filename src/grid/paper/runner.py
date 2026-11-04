"""One actual observed hour, with a supervised process boundary at minute 30."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import select
import sqlite3
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from grid.domain.numbers import DomainError
from grid.exchange.adapter import ReadLimits, ReadOnlyClient
from grid.exchange.ccxt_transport import HTTPWire, PublicCCXT, PublicWire
from grid.exchange.models import Failure, MarketObservation, ReadError
from grid.operations.experiment import host, source_digest, utc
from grid.operations.supervisor import resident_bytes
from grid.paper.engine import PaperEngine
from grid.paper.journal import CAP_BYTES, Journal, clock
from grid.paper.recovery import claim_replacement
from grid.paper.wire import ObservedWire
from grid.persistence.storage import check_runtime
from grid.research.artifacts import canonical, output_directory, publish

LIMITS = ReadLimits(750, 5000, 3000, 65536, 10, 3600000, 5000, True)
SAMPLE_INTERVAL_NS = 50 * 10**9
MAX_SAMPLE_GAP_NS = 60 * 10**9
CPU_WINDOW_NS = 60 * 10**9


class Observer:
    def __init__(
        self,
        journal: Journal,
        sources: frozenset[str],
        wire: PublicWire,
        now: Callable[[], tuple[int, int]] = clock,
        *,
        observer_pid: int | None = None,
    ) -> None:
        self.journal, self.sources, self.now = journal, sources, now
        self.interval_ms = (
            7500 if journal.kinds("start")[0]["payload"].get("prior_attempts") else 5000
        )
        limits = replace(LIMITS, min_interval_ms=self.interval_ms)
        self.transport = PublicCCXT("okx", limits, wire=ObservedWire(journal, wire, now))
        self.client = ReadOnlyClient(
            "okx",
            self.transport,
            ("BTC/USDT",),
            limits,
            monotonic_ns=lambda: now()[1],
            wall_ms=lambda: now()[0],
        )
        self.engine: PaperEngine | None = None
        self.market: MarketObservation | None = None
        self.cpu_ns, self.cpu_at = time.process_time_ns(), now()[1]
        checkpoints = journal.kinds("checkpoint")
        self.sample_at: int = (
            checkpoints[-1]["ns"] if checkpoints else journal.kinds("start")[0]["ns"]
        )
        self.monitor_fault: str | None = next(
            (
                r["payload"]["monitor_fault"]
                for r in checkpoints
                if r["payload"].get("monitor_fault")
            ),
            None,
        )
        self.observed_clock = journal.kinds("start")[0]["payload"].get("observed_clock") is True
        self.observer_fd: int | None = None
        self.observer_poll = select.poll()
        if observer_pid is not None:
            if observer_pid != os.getppid():
                raise DomainError("observer must be the current coordinator parent")
            self.observer_fd = os.pidfd_open(observer_pid)
            self.observer_poll.register(self.observer_fd, select.POLLIN)

    def record(self, kind: str, value: object) -> dict[str, Any]:
        return self.journal.append(kind, value, *self.now())

    def elapsed(self) -> int:
        ms, ns = self.now()
        start = self.journal.kinds("start")[0]
        elapsed = (ns - start["ns"]) // 1000000
        if elapsed < 0 or abs(ms - start["ms"] - elapsed) > 2000:
            raise DomainError("observation clocks diverged or regressed")
        return int(elapsed)

    async def checkpoint(self, label: str) -> dict[str, object]:
        paper = await self.engine.status() if self.engine else None
        ms, ns = self.now()
        cpu, elapsed = time.process_time_ns(), ns - self.cpu_at
        fraction = (cpu - self.cpu_ns) / elapsed if elapsed else 0.0
        cpu_complete = elapsed >= CPU_WINDOW_NS
        gap = ns - self.sample_at
        directory = self.journal.directory
        wal = directory / "paper.db-wal"
        metrics: dict[str, object] = {
            "label": label,
            "utc": utc(),
            "elapsed_ms": self.elapsed(),
            "attempts": len(self.journal.kinds("attempt")),
            "books": len(self.journal.kinds("book")),
            "rss_bytes": resident_bytes(),
            "directory_bytes": self.journal.bytes(),
            "wal_bytes": wal.stat().st_size if wal.exists() else 0,
            "tasks": len(asyncio.all_tasks()),
            "cpu_fraction": fraction,
            "cpu_window_ns": elapsed,
            "cpu_window_complete": cpu_complete,
            "checkpoint_gap_ns": gap,
            "paper": paper,
        }
        free = os.statvfs(directory)
        if elapsed < 0 or cpu < self.cpu_ns or gap < 0:
            self.monitor_fault = self.monitor_fault or "resource-clock-regressed"
        elif gap > MAX_SAMPLE_GAP_NS:
            self.monitor_fault = self.monitor_fault or "checkpoint-overdue"
        elif (
            resident_bytes() > 512 * 1024**2
            or self.journal.bytes() > CAP_BYTES
            or wal.exists()
            and wal.stat().st_size > 16 * 1024**2
            or len(asyncio.all_tasks()) > 20
            or cpu_complete
            and fraction > 0.5
            or free.f_bavail * free.f_frsize < 2 * 1024**3
        ):
            self.monitor_fault = self.monitor_fault or "resource-cap-breached"
        # Fault/backup snapshots cannot reset an unfinished CPU window.
        if cpu_complete:
            self.cpu_ns, self.cpu_at = cpu, ns
        metrics["monitor_fault"] = self.monitor_fault
        self.journal.append("checkpoint", metrics, ms, ns)
        self.sample_at = ns
        print(canonical(metrics).decode(), flush=True)
        if self.monitor_fault and label != "process-stop":
            raise DomainError(self.monitor_fault)
        return metrics

    def sample_due(self) -> int:
        return min(self.sample_at + SAMPLE_INTERVAL_NS, self.cpu_at + CPU_WINDOW_NS)

    async def checkpoint_if_due(self) -> None:
        if self.monitor_fault or self.now()[1] >= self.sample_due():
            await self.checkpoint("periodic")

    async def initialize(self) -> None:
        self.engine = await PaperEngine.open(self.journal, self.sources, self.now)
        self.record("ccxt", {"version": "4.5.91", "source_hashes": self.transport.source_hashes()})
        await self.checkpoint("process-start")

    async def scheduled(self) -> bool:
        """True requests the planned, externally supervised process restart."""
        elapsed = self.elapsed()
        engine = self.engine
        if engine is None:
            raise DomainError("missing paper engine")
        for minute, label in ((10, "disconnect"), (45, "synthetic-429")):
            if elapsed >= minute * 60000 and not any(
                r["payload"]["reason"] == label for r in self.journal.kinds("hold")
            ):
                await engine.halt(label)
                self.record("hold", {"reason": label, "until_ns": self.now()[1] + 60 * 10**9})
                await self.checkpoint(label)
        for minute in (20, 50):
            if elapsed >= minute * 60000 and not any(
                r["payload"]["number"] == minute for r in self.journal.kinds("backup")
            ):
                await self.checkpoint("before-backup")
                await engine.backup(minute)
                await self.checkpoint("verified-backup")
        if elapsed >= 30 * 60000 and not self.journal.kinds("restart-requested"):
            await engine.halt("planned-restart")
            await self.checkpoint("restart-requested")
            self.record("restart-requested", {"budget_and_deadline_preserved": True})
            return True
        return False

    async def poll(self) -> None:
        if self.engine is None:
            raise DomainError("missing paper engine")
        if self.market is None:
            markets = await self.client.load_markets()
            await self.checkpoint_if_due()
            self.market = markets["BTC/USDT"]
            self.record("market", self.market)
        else:
            book = await self.client.fetch_order_book("BTC/USDT")
            # A delayed read must not reach strategy admission before monitoring.
            await self.checkpoint_if_due()
            ms, ns = self.now()
            book.require_fresh(ns, ms, 10000)
            previous = self.journal.kinds("book")
            if (
                previous
                and book.source_ms is not None
                and (book.source_ms <= previous[-1]["payload"]["source_ms"])
            ):
                raise ReadError(Failure.STALE)
            row = self.record("book", book)
            await self.engine.observe(self.market, book, row["sequence"])

    async def run(self) -> int:
        """Bounded failures stop with evidence. No deadline reset or catch-up."""
        code = 1
        failure: str | None = None
        try:
            await self.initialize()
            while self.elapsed() < 3600000:
                if self.observer_fd is not None and self.observer_poll.poll(0):
                    raise DomainError("coordinator exited; observation stopped")
                await self.checkpoint_if_due()
                if await self.scheduled():
                    code = 75
                    break
                await self.checkpoint_if_due()
                attempts = self.journal.kinds("attempt")
                holds = self.journal.kinds("hold")
                due = max(
                    attempts[-1]["ns"] + self.interval_ms * 1000000 if attempts else 0,
                    max((r["payload"]["until_ns"] for r in holds), default=0),
                )
                wait = (due - self.now()[1]) / 10**9
                if wait > 0:
                    until_sample = max(0, (self.sample_due() - self.now()[1]) / 10**9)
                    await asyncio.sleep(min(wait, until_sample, 1.0))
                    continue
                try:
                    await self.poll()
                except ReadError as error:
                    if self.engine:
                        await self.engine.halt("feed-failure")
                    self.record("feed-failure", {"failure": error.failure.value})
                    await self.checkpoint("feed-failure")
                    if error.failure not in {
                        Failure.TIMEOUT,
                        Failure.UNAVAILABLE,
                        Failure.RATE_LIMIT,
                        Failure.STALE,
                    }:
                        raise
                    # Three failed recovery checkpoints are a terminal partial run.
                    # Every failed call halts admission and is visibly checkpointed.
                    if len(self.journal.kinds("feed-failure")) >= 3:
                        raise
                    self.record(
                        "hold", {"reason": "feed-recovery", "until_ns": self.now()[1] + 60 * 10**9}
                    )
                    self.transport.recover_observed()
            else:
                code = 0
        except (Exception, asyncio.CancelledError) as error:
            failure = error.failure.value if isinstance(error, ReadError) else type(error).__name__
            code = 1
        finally:
            # No external calls in cleanup; retain all synthetic residuals.
            cleanup: list[Callable[[], Awaitable[None]]] = [self.client.close]
            if self.engine:
                engine = self.engine
                cleanup.insert(0, lambda: engine.halt("run-stop"))
                cleanup.append(engine.close)
            for operation in cleanup:
                try:
                    await operation()
                except Exception as error:
                    code, failure = 1, "cleanup-" + type(error).__name__
            try:
                final = await self.checkpoint("process-stop")
                if self.monitor_fault:
                    code, failure = 1, self.monitor_fault
                if code != 75:
                    result = dict(
                        final,
                        result=(
                            "one-hour-observed" if self.observed_clock else "mock-clock-complete"
                        )
                        if code == 0
                        else "partial-fault",
                        failure=failure,
                        observed_soak_complete=False,
                        observed_clock=self.observed_clock,
                        private_account_evidence="NOT TESTED",
                        real_orders=0,
                        journal_prefix_sha256=self.journal.previous,
                    )
                    self.record("finished", result)
                    publish(self.journal.directory / "report.json", result)
            except Exception:
                # Cleanup failure is never hidden by an earlier successful duration.
                code = 1
                print('{"result":"cleanup-fault","observed_soak_complete":false}', flush=True)
            if self.observer_fd is not None:
                os.close(self.observer_fd)
                self.observer_fd = None
        return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--approved-sqlite-source", required=True)
    parser.add_argument("--acknowledge-public-one-hour", action="store_true", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--replacement-for", type=Path)
    parser.add_argument("--observer-pid", type=int)
    args = parser.parse_args()
    sources = frozenset({args.approved_sqlite_source})
    # The runtime is checked again by the storage owner; no auto-approval.
    check_runtime(sqlite3.sqlite_version_info, args.approved_sqlite_source, sources)
    directory = output_directory(args.directory)
    journal = Journal(directory)
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        source = source_digest()
        starts = journal.kinds("start")
        if args.resume:
            if (
                len(starts) != 1
                or not journal.kinds("restart-requested")
                or journal.kinds("resumed")
                or journal.kinds("finished")
                or starts[0]["payload"]["source_sha256"] != source
                or starts[0]["payload"]["boot_id"] != boot
                or clock()[1] - journal.kinds("restart-requested")[-1]["ns"] > 60 * 10**9
                or clock()[1] - starts[0]["ns"] >= 3600 * 10**9
            ):
                raise DomainError("restart requires the exact source, host and planned checkpoint")
            journal.append("resumed", {"reviewed_process_restart": True}, *clock())
        elif journal.rows:
            raise DomainError("existing run requires explicit reviewed resume")
        else:
            root = Path(__file__).resolve().parents[3]
            credit = (
                claim_replacement(args.replacement_for, directory) if args.replacement_for else {}
            )
            journal.append(
                "start",
                {
                    **credit,
                    "source_sha256": source,
                    "lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
                    "boot_id": boot,
                    "host": host(directory),
                    "python": sys.version,
                    "sqlite": sqlite3.sqlite_version,
                    "approved_sqlite_source": args.approved_sqlite_source,
                    "duration_ms": 3600000,
                    "observed_clock": True,
                    "monitoring_version": 2,
                    "wire_cap": 750,
                    "instrument_cap": 3,
                    "public_venue": "okx",
                    "symbol": "BTC/USDT",
                    "utc": utc(),
                    "fill_model": "strict-subsequent-book-full-depth-v1",
                    "retention": "at least seven days after review; no automatic deletion",
                    "real_account_verified": False,
                },
                *clock(),
            )
        return asyncio.run(
            Observer(journal, sources, HTTPWire(), observer_pid=args.observer_pid).run()
        )
    finally:
        journal.close()


if __name__ == "__main__":
    raise SystemExit(main())
