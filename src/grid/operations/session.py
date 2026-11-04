"""Finite, restartable invented-account session for the local operator CLI."""

from __future__ import annotations

import asyncio
import os
import stat
import time
import uuid
from itertools import islice
from pathlib import Path

from grid.domain.numbers import DomainError, bounded_int
from grid.execution.replay import replay_gateway
from grid.operations.experiment import source_digest
from grid.operations.local import LocalControl
from grid.operations.supervisor import MockSupervisor
from grid.operations.synthetic import EXECUTION, OPERATIONS, STORAGE, fixture
from grid.paper.journal import Journal
from grid.research.artifacts import canonical, output_directory


class MockSession:
    def __init__(self, directory: Path, journal: Journal, origin_ns: int, session_id: str) -> None:
        self.directory, self.journal = directory, journal
        self.origin_ns, self.session_id = origin_ns, session_id
        self.owner: MockSupervisor | None = None
        self.control: LocalControl | None = None
        self.case = fixture(1, 1)
        self.revision = 0
        self.closed = False

    def clock(self) -> tuple[int, int]:
        elapsed = time.monotonic_ns() - self.origin_ns
        if elapsed < 0:
            raise DomainError("mock session clock regressed")
        ms = 10000 + elapsed // 1000000
        return ms, ms * 1000000  # Explicit synthetic clock, not UTC observation.

    @classmethod
    async def open(cls, directory: Path, sources: frozenset[str], *, resume: bool) -> MockSession:
        if resume and not directory.is_dir():
            raise DomainError("mock resume directory is missing")
        directory = output_directory(directory)
        info = directory.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise DomainError("private mock session directory required")
        if not resume:
            if any(directory.iterdir()):
                raise DomainError("new mock session requires an empty directory")
            (directory / "world").mkdir(mode=0o700)
            (directory / "control").mkdir(mode=0o700)
        if any((directory / name).is_symlink() for name in ("world", "control")):
            raise DomainError("mock session symlink refused")
        if sum(1 for _ in islice((directory / "control").iterdir(), 64)) >= 64:
            raise DomainError("retained local endpoint capacity exceeded")
        journal = Journal(directory / "world")
        session: MockSession | None = None
        try:
            starts = journal.kinds("operator-session")
            source = source_digest()
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            if resume:
                if (
                    len(starts) != 1
                    or journal.rows[0]["kind"] != "operator-session"
                    or starts[0]["payload"]["source_sha256"] != source
                    or starts[0]["payload"]["boot_id"] != boot
                    or starts[0]["payload"]["uid"] != os.geteuid()
                    or starts[0]["payload"]["mode"] != "mock"
                ):
                    raise DomainError("resume requires the same mock source, user and host boot")
                metadata = starts[0]["payload"]
            else:
                metadata = {
                    "mode": "mock",
                    "session_id": uuid.uuid4().hex,
                    "origin_ns": time.monotonic_ns(),
                    "boot_id": boot,
                    "source_sha256": source,
                    "uid": os.geteuid(),
                    "fixture": "one-invented-spot-allocation-1000-QUOTE-zero-fee",
                }
                journal.append("operator-session", metadata, 10000, 10**10)
            session = cls(directory, journal, metadata["origin_ns"], metadata["session_id"])
            gateway = await replay_gateway(journal.kinds("venue"))
            session_clock = session.clock

            def record(kind: str, value: object) -> None:
                journal.append("venue", {"kind": kind, "value": value}, *session_clock())

            gateway.record = record
            owner = await MockSupervisor.open(
                directory / "world/mock.db",
                STORAGE,
                sources,
                session.case.scopes,
                gateway,
                EXECUTION,
                OPERATIONS,
                session.clock,
                session.case.prepare,
            )
            session.owner = owner
            await session.step()
            if await owner.writer.submit(lambda ledger: ledger.lookup("operator-universe")) is None:
                await owner.universe.configure(
                    "operator-universe",
                    0,
                    session.case.universe,
                    "mock-session",
                    "invented fixture",
                    session.clock()[0],
                )
            if await owner.portfolio.status() is None:
                await owner.portfolio.begin(
                    "operator-cycle",
                    0,
                    "operator-cycle",
                    session.case.portfolio,
                    "mock-session",
                    "invented fixture",
                )
            endpoint = LocalControl(owner, session.session_id)
            session.control = endpoint
            await endpoint.start(directory / "control" / ("c-" + uuid.uuid4().hex[:10] + ".sock"))
            return session
        except BaseException:
            if session is not None:
                await session.close()
            else:
                journal.close()
            raise

    async def step(self) -> None:
        owner = self.owner
        if owner is None:
            raise DomainError("mock owner is unavailable")
        # Synthetic cancellation ACKs settle on a later tick; no fabricated fills.
        for external in owner.executor.gateway.cancels:
            owner.executor.gateway.settle_cancel(external)
        self.revision += 1
        await owner.observe(self.case.facts(self.clock()[0], self.revision))
        await owner.step()

    async def run(self, seconds: int) -> None:
        bounded_int(seconds, 1, 300)
        if self.control is None or self.owner is None:
            raise DomainError("mock operator endpoint is unavailable")
        end = time.monotonic() + seconds
        try:
            print(
                canonical(
                    {
                        "mode": "mock",
                        "live": "DISABLED",
                        "session_id": self.session_id,
                        "socket": self.control.path,
                        "seconds": seconds,
                    }
                ).decode(),
                flush=True,
            )
            while time.monotonic() < end:
                if (
                    self.control.failed
                    or self.control.acceptor is None
                    or self.control.acceptor.done()
                ):
                    raise DomainError("local endpoint stopped")
                await self.step()
                await asyncio.sleep(min(0.25, max(0, end - time.monotonic())))
        finally:
            await self.close()

    async def close(self) -> None:
        if self.closed:
            return
        try:
            if self.control is not None:
                await self.control.close()
        finally:
            if self.owner is not None:
                await self.owner.close()
            # A failed endpoint close must not leak a successfully closed owner.
            # If financial cleanup remains pending, retain journal ownership too.
            self.journal.close()
        self.closed = True
