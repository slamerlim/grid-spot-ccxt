"""Single bounded mock scheduler; controls remain responsive during gateway waits."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from grid.domain.model import Context, Scope
from grid.domain.numbers import DomainError, bounded_int
from grid.exchange.models import ReadError
from grid.execution.executor import MockExecutor
from grid.execution.models import Evidence, ExecutionError, Notice
from grid.execution.models import Limits as ExecutionLimits
from grid.execution.synthetic import SyntheticExchange
from grid.operations.model import (
    Action,
    Alert,
    AlertCode,
    AllocationStatus,
    BackupStamp,
    Command,
    Health,
    Limits,
    Operations,
    StoragePlan,
)
from grid.persistence import codec, storage_jobs
from grid.persistence import operations as records
from grid.persistence import portfolio as portfolio_records
from grid.persistence.ledger import Ledger
from grid.persistence.records import (
    Backpressure,
    Backup,
    Bounds,
    LedgerError,
    Outcome,
    StorageFault,
)
from grid.persistence.storage import restore
from grid.persistence.writer import AsyncWriter
from grid.portfolio.controller import PortfolioController
from grid.portfolio.model import Cycle, CyclePhase, ExitApproval, ValuationFacts
from grid.portfolio.valuation import fresh
from grid.universe.controller import UniverseController
from grid.universe.controls import UniverseEdit
from grid.universe.model import MarketKey


def identity(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex


def resident_bytes() -> int:
    # Linux current RSS, not a Python allocation estimate or lifetime high-water mark.
    return int(Path("/proc/self/statm").read_text().split()[1]) * int(os.sysconf("SC_PAGE_SIZE"))


class MockSupervisor:
    def __init__(
        self,
        writer: AsyncWriter,
        executor: MockExecutor,
        limits: Limits,
        clock: Callable[[], tuple[int, int]],
        path: Path,
        bounds: Bounds,
        sources: frozenset[str],
    ) -> None:
        if executor.operational_admission is not None:
            raise ExecutionError("executor already has an operational owner")
        self.writer, self.executor, self.limits, self.clock = writer, executor, limits, clock
        self.path, self.bounds, self.sources = path, bounds, sources
        self.scopes = executor.scopes
        self.universe = UniverseController(executor, len(self.scopes), clock)
        self.portfolio = PortfolioController(executor, clock)
        self.contexts: dict[Scope, Context] = {}
        self.facts: ValuationFacts | None = None
        self._alerts: deque[Alert] = deque(maxlen=limits.alert_capacity)
        self._alert_sequence = self._evicted = 0
        self._local_halt = True
        self._closed = self._closing = self._running = False
        self._tick: asyncio.Task[object] | None = None
        self._shutdown: asyncio.Task[None] | None = None
        self._backup: asyncio.Task[BackupStamp] | None = None
        self._storage: asyncio.Task[storage_jobs.StorageJob] | None = None
        self._last_ms = self._last_ns = self._step_ns = 0
        self._next: dict[Scope, int] = dict.fromkeys(self.scopes, 0)
        self._failures: dict[Scope, int] = dict.fromkeys(self.scopes, 0)
        self._last: Health | None = None
        executor.operational_admission = self._admit

    @classmethod
    async def open(
        cls,
        path: Path,
        bounds: Bounds,
        sources: frozenset[str],
        scopes: tuple[Scope, ...],
        gateway: SyntheticExchange,
        execution_limits: ExecutionLimits,
        limits: Limits,
        clock: Callable[[], tuple[int, int]],
        prepare: Callable[[Ledger], None] | None = None,
    ) -> MockSupervisor:
        if type(gateway) is not SyntheticExchange:
            raise ExecutionError("mock supervisor requires the synthetic gateway")
        writer = await AsyncWriter.open(path, bounds, sources, mock_storage=True)
        executor: MockExecutor | None = None
        try:
            if prepare is not None:
                await writer.submit(prepare)
            executor = MockExecutor(
                writer, gateway, scopes, execution_limits, Evidence(True, True, True)
            )
            owner = cls(writer, executor, limits, clock, path, bounds, sources)
            ms, _ = owner._now()

            def start(ledger: Ledger) -> Outcome:
                previous = records.current(ledger)
                return records.commit(
                    ledger,
                    identity("startup"),
                    previous.revision if previous else 0,
                    Command(
                        Action.START, scopes, "mock-supervisor", "startup reconciliation", ms, False
                    ),
                    scopes,
                    lambda: None,
                )

            await writer.submit(start)
            return owner
        except BaseException:
            if executor is not None:
                await executor.close()
            await writer.close()
            raise

    def _now(self) -> tuple[int, int]:
        ms, ns = self.clock()
        bounded_int(ms, 0, 2**53 - 1)
        bounded_int(ns, 0, 2**63 - 1)
        if ms < self._last_ms or ns < self._last_ns:
            self._local_halt = True
            self._alert(AlertCode.CLOCK)
            raise ExecutionError("supervisor clock regressed")
        self._last_ms, self._last_ns = ms, ns
        return ms, ns

    def _alert(self, code: AlertCode, scope: Scope | None = None) -> None:
        # Fixed codes only: never publish arbitrary exception messages or payloads.
        if self._alerts and (self._alerts[-1].code, self._alerts[-1].scope) == (code, scope):
            return
        self._alert_sequence += 1
        self._evicted += int(len(self._alerts) == self.limits.alert_capacity)
        self._alerts.append(Alert(self._alert_sequence, self._last_ms, code, scope))

    def _admit(self, ledger: Ledger, scope: Scope) -> int:
        current = records.current(ledger)
        if self._local_halt or self._closing or self._closed or current is None:
            raise ExecutionError("operational admission halted")
        if current.halted or scope in current.held:
            raise ExecutionError("durable operational control blocks placement")
        return current.revision

    async def observe(self, facts: ValuationFacts) -> None:
        self._now()
        if self._closing or self._closed:
            raise ExecutionError("supervisor is closing")
        mapping = {MarketKey.of(s): s for s in self.scopes}
        if {f.key for f in facts.markets} != set(mapping):
            raise DomainError("complete supervised evidence packet required")
        # The portfolio revision is the outer admission boundary. Partial universe
        # publication can only deny a claim; it cannot bypass either independent guard.
        await self.portfolio.observe(facts)
        for row in facts.markets:
            await self.universe.observe(mapping[row.key], row)
        self.contexts = {mapping[f.key]: f.context for f in facts.markets}
        self.facts = facts

    async def control(
        self,
        request_id: str,
        expected: int,
        command: Command,
        *,
        exit_approval: ExitApproval | None = None,
        universe_edit: UniverseEdit | None = None,
    ) -> Outcome:
        ms, ns = self._now()
        if self._closing or self._closed or command.observed_ms > ms:
            raise ExecutionError("closed supervisor or future control")
        if command.action in (Action.START, Action.SHUTDOWN):
            raise DomainError("lifecycle actions are owned by the supervisor")
        if command.action is Action.HALT:
            self._local_halt = True  # Retain this protection if persistence fails.

        def apply(ledger: Ledger) -> tuple[Outcome, bool]:
            def validate_resume() -> None:
                cycle = ledger.portfolio()
                if cycle is None or self.facts is None:
                    raise LedgerError("resume needs a cycle and fresh evidence")
                portfolio_records.nav(
                    ledger,
                    portfolio_records.load(ledger, cycle.policy),
                    self.facts,
                    cycle.policy,
                    ms,
                    ns,
                )

            first = ledger.lookup(request_id) is None
            exit_evidence = None
            if exit_approval is not None and self.facts is not None and universe_edit is None:
                exit_evidence = (self.facts, *self._now())

            def configure() -> None:
                if universe_edit is None:
                    raise DomainError("universe edit missing")
                self.universe.edit(
                    ledger,
                    request_id,
                    universe_edit,
                    command.actor,
                    command.reason,
                    command.observed_ms,
                    exit_approval,
                    self.facts,
                )

            return records.commit(
                ledger,
                request_id,
                expected,
                command,
                self.scopes,
                validate_resume,
                exit_approval=exit_approval,
                exit_evidence=exit_evidence,
                universe_edit=universe_edit,
                configure=configure if universe_edit is not None else None,
            ), first

        try:
            result, first = await self.writer.submit(apply)
        except (OSError, sqlite3.Error, StorageFault):
            await self.trip(AlertCode.STORAGE)
            raise
        if first and command.action in (Action.RECONCILE, Action.RESUME):
            for scope in command.scopes:
                self._next[scope], self._failures[scope] = 0, 0
        if first and command.action is Action.RESUME and set(command.scopes) == set(self.scopes):
            self._local_halt = False
        return result

    async def trip(self, code: AlertCode) -> None:
        self._local_halt = True
        self._alert(code)
        try:

            def halt(ledger: Ledger) -> Outcome:
                previous = records.current(ledger)
                if previous is None:
                    raise LedgerError("operational ownership missing")
                return records.commit(
                    ledger,
                    identity("trip"),
                    previous.revision,
                    Command(
                        Action.HALT,
                        self.scopes,
                        "mock-supervisor",
                        code.value,
                        self._last_ms,
                        False,
                    ),
                    self.scopes,
                    lambda: None,
                )

            await self.writer.submit(halt)
        except (OSError, sqlite3.Error, LedgerError):
            self._alert(AlertCode.STORAGE)

    async def offer(self, notice: Notice) -> None:
        self._now()
        try:
            self.executor.offer(notice)
        except (Backpressure, ExecutionError):
            await self.trip(AlertCode.OVERLOAD)
            raise

    async def status(self) -> Health:
        ms, ns = self._now()
        if self._closed:
            if self._last is None:
                raise ExecutionError("no closed supervisor snapshot")
            return replace(
                self._last,
                closed=True,
                admission_halted=True,
                alerts=tuple(self._alerts),
                alerts_evicted=self._evicted,
            )

        def inspect(
            ledger: Ledger,
        ) -> tuple[Operations, tuple[AllocationStatus, ...], Cycle | None]:
            current = records.current(ledger)
            if current is None:
                raise LedgerError("operational record missing")
            return current, records.allocations(ledger, self.scopes), ledger.portfolio()

        operations, allocations, cycle = await self.writer.submit(inspect)
        if operations.halted or self._local_halt:
            self._alert(AlertCode.HALTED)
        if (
            operations.backup is None
            or not 0 <= ms - operations.backup.observed_ms <= self.limits.backup_age_ms
        ):
            self._alert(AlertCode.BACKUP)
        try:
            if cycle is None or self.facts is None:
                raise DomainError("no valuation evidence")
            fresh(self.facts, cycle.policy, ms, ns)
        except DomainError:
            self._alert(AlertCode.STALE)
        for row in allocations:
            if row.unknown:
                self._alert(AlertCode.UNKNOWN, row.scope)
            if row.recovery:
                self._alert(AlertCode.RECONCILE, row.scope)
            if self._failures[row.scope] >= self.limits.recovery_attempts:
                self._alert(AlertCode.RETRIES, row.scope)
        if cycle is not None and cycle.phase is CyclePhase.BLOCKED:
            self._alert(AlertCode.EXIT)
        if cycle is not None and cycle.phase is CyclePhase.ACTIVE:
            for entry in cycle.allocation_exits:
                if entry.phase is CyclePhase.BLOCKED:
                    self._alert(AlertCode.EXIT, entry.slot.scope)
        wal = self.path.with_name(self.path.name + "-wal")
        result = Health(
            ms,
            operations,
            cycle,
            cycle.final_return if cycle else None,
            cycle.exit_shortfall if cycle else None,
            allocations,
            tuple(self._alerts),
            self._evicted,
            operations.halted or self._local_halt or self._closing,
            self.writer.depth,
            self.executor.intake_depth,
            resident_bytes(),
            wal.stat().st_size if wal.exists() else 0,
            time.process_time_ns(),
            self._step_ns,
            len(asyncio.all_tasks()),
            False,
        )
        self._last = result
        return result

    async def step(self) -> Health:
        if self._tick is not None:
            raise Backpressure("supervisor tick already active; no task queued")
        if self._closing or self._closed:
            raise ExecutionError("supervisor is closing")
        self._tick = asyncio.current_task()
        began = time.monotonic_ns()
        try:
            ms, _ = self._now()
            health = await self.status()
            if (
                health.rss_bytes > self.limits.max_rss_bytes
                or health.wal_bytes > self.limits.max_wal_bytes
            ):
                await self.trip(AlertCode.RESOURCE)
                return await self.status()
            candidates = sorted(
                (
                    row
                    for row in health.allocations
                    if self._next[row.scope] <= ms
                    and self._failures[row.scope] < self.limits.recovery_attempts
                ),
                key=lambda row: (
                    0 if row.unknown or row.recovery else 1 if row.working else 2,
                    self._next[row.scope],
                    codec.digest(row.scope),
                ),
            )[: self.limits.scopes_per_tick]
            ready: list[Scope] = []
            for row in candidates:
                if (time.monotonic_ns() - began) // 1000000 >= self.limits.max_step_ms:
                    await self.trip(AlertCode.RESOURCE)
                    break
                context = self.contexts.get(row.scope)
                if context is None:
                    self._alert(AlertCode.STALE, row.scope)
                    continue
                context = replace(context, now_ms=ms)
                ok = await self.executor.service(row.scope, context)
                if not ok and self.executor.last_error == "ledger or gateway failure":
                    await self.trip(AlertCode.STORAGE)
                self._next[row.scope] = ms + (
                    self.limits.tick_ms if ok else self.limits.recovery_backoff_ms
                )
                self._failures[row.scope] = 0 if ok else self._failures[row.scope] + 1
                if ok:
                    ready.append(row.scope)
            cycle = await self.portfolio.status()
            if cycle is not None and self.facts is not None:
                try:
                    if (
                        cycle.phase is CyclePhase.ACTIVE
                        and ms - cycle.last_evaluation_ms >= cycle.policy.evaluation_interval_ms
                    ):
                        await self.portfolio.evaluate(
                            identity("sample"), cycle.revision, "mock-supervisor", "scheduled NAV"
                        )
                    elif cycle.phase not in (CyclePhase.ACTIVE, CyclePhase.COMPLETE):
                        await self.portfolio.advance(
                            identity("exit"), cycle.revision, "mock-supervisor", "scheduled exit"
                        )
                except StorageFault:
                    raise
                except (DomainError, LedgerError, ExecutionError, ReadError):
                    self._alert(AlertCode.STALE)
                # Scoped exits use a separate cadence; they must neither reset the
                # global NAV sampling clock nor depend on unrelated fresh books.
                cycle = await self.portfolio.status()
                if (
                    cycle is not None
                    and cycle.phase is CyclePhase.ACTIVE
                    and any(e.phase is not CyclePhase.COMPLETE for e in cycle.allocation_exits)
                ):
                    try:
                        await self.portfolio.advance(
                            identity("allocation-exit"),
                            cycle.revision,
                            "mock-supervisor",
                            "scheduled allocation exit",
                        )
                    except StorageFault:
                        raise
                    except (DomainError, LedgerError, ExecutionError, ReadError):
                        self._alert(AlertCode.STALE)
            latest = await self.status()
            for scope in ready:
                row = next(r for r in latest.allocations if r.scope == scope)
                if (
                    scope in latest.operations.held
                    or latest.admission_halted
                    and row.queued_cancels == 0
                ):
                    continue
                if (time.monotonic_ns() - began) // 1000000 >= self.limits.max_step_ms:
                    await self.trip(AlertCode.RESOURCE)
                    break
                await self.executor.dispatch(scope, replace(self.contexts[scope], now_ms=ms))
            self._step_ns = time.monotonic_ns() - began
            return await self.status()
        except asyncio.CancelledError:
            self._local_halt = True
            self._alert(AlertCode.RUNTIME)
            raise
        except (OSError, sqlite3.Error, LedgerError):
            await self.trip(AlertCode.STORAGE)
            raise ExecutionError("supervisor storage operation failed; admission halted") from None
        except Exception:
            await self.trip(AlertCode.RUNTIME)
            raise ExecutionError("supervisor failed; admission halted") from None
        finally:
            self._step_ns = time.monotonic_ns() - began
            self._tick = None

    async def run(self, steps: int) -> Health:
        bounded_int(steps, 1, 10000)
        if self._running:
            raise Backpressure("supervisor run already active")
        self._running = True
        try:
            for _ in range(steps):
                await self.step()
                await asyncio.sleep(self.limits.tick_ms / 1000)
            return await self.status()
        except asyncio.CancelledError:
            await self.close()
            raise
        finally:
            self._running = False

    async def backup(self, path: Path, verification_path: Path) -> BackupStamp:
        if self._closing or self._closed:
            raise ExecutionError("supervisor is closing")
        if self._backup is not None or self._storage is not None:
            raise Backpressure("backup already active; no task queued")
        task = asyncio.create_task(self._make_backup(path, verification_path))
        self._backup = task

        def finished(done: asyncio.Task[BackupStamp]) -> None:
            if not done.cancelled():
                done.exception()  # Also retrieve failure after caller cancellation.
            self._backup = None

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def storage_job(self, request_id: str, plan: StoragePlan) -> storage_jobs.StorageJob:
        ms, _ = self._now()
        if self._closing or self._closed or plan.command.observed_ms > ms:
            raise ExecutionError("closed supervisor or future storage request")
        if self._storage is not None or self._backup is not None:
            raise Backpressure("storage operation already active; no task queued")
        task = asyncio.create_task(self._run_storage(request_id, plan, ms))
        self._storage = task

        def finished(done: asyncio.Task[storage_jobs.StorageJob]) -> None:
            if not done.cancelled():
                done.exception()
            self._storage = None

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _run_storage(
        self, request_id: str, plan: StoragePlan, ms: int
    ) -> storage_jobs.StorageJob:
        try:
            first = await self.writer.submit(
                lambda ledger: storage_jobs.prepare(ledger, request_id, plan)
            )
        except (OSError, sqlite3.Error, StorageFault):
            await self.trip(AlertCode.STORAGE)
            raise
        if first:
            try:
                await self.writer.submit(
                    lambda ledger: ledger.storage.fault("storage_job:after_prepare")
                )
                if plan.command.action is Action.BACKUP:
                    backup = await self.writer.submit(
                        lambda ledger: ledger.backup(Path(plan.source))
                    )
                else:
                    if plan.digest is None or plan.requests is None:
                        raise DomainError("restore proof missing")
                    backup = Backup(plan.source, plan.digest, plan.requests)
                checked = await asyncio.to_thread(
                    restore,
                    backup,
                    Path(plan.destination),
                    self.bounds,
                    self.sources,
                    mock_storage=True,
                    fault=await self.writer.submit(lambda ledger: ledger.storage.fault),
                )
                await self.writer.submit(
                    lambda ledger: ledger.storage.fault("storage_job:after_verify")
                )
                stamp = BackupStamp(ms, checked.semantic_hash, checked.request_count)
                await self.writer.submit(
                    lambda ledger: storage_jobs.finish(ledger, request_id, stamp)
                )
            except Exception:

                def failed(ledger: Ledger) -> None:
                    job = storage_jobs.lookup(ledger, request_id)
                    if job is not None and job.result is None:
                        storage_jobs.finish(ledger, request_id, None)

                try:
                    await self.writer.submit(failed)
                finally:
                    await self.trip(AlertCode.STORAGE)
                raise ExecutionError(
                    "storage verification failed; inspect the saved request"
                ) from None
        job = await self.writer.submit(lambda ledger: storage_jobs.lookup(ledger, request_id))
        if job is None:
            raise LedgerError("storage job evidence missing")
        return job

    async def _make_backup(self, path: Path, verification_path: Path) -> BackupStamp:
        ms, _ = self._now()
        try:
            backup = await self.writer.submit(lambda ledger: ledger.backup(path))
            checked = await asyncio.to_thread(
                restore, backup, verification_path, self.bounds, self.sources, mock_storage=True
            )
            stamp = BackupStamp(ms, checked.semantic_hash, checked.request_count)

            def record(ledger: Ledger) -> Outcome:
                previous = records.current(ledger)
                if previous is None:
                    raise LedgerError("backup owner missing")
                return records.backup_verified(ledger, identity("backup"), previous.revision, stamp)

            await self.writer.submit(record)
            return stamp
        except Exception:
            await self.trip(AlertCode.STORAGE)
            raise ExecutionError(
                "backup or restore verification failed; admission halted"
            ) from None

    async def _finish(self) -> None:
        try:
            if self._tick is not None and self._tick is not asyncio.current_task():
                self._tick.cancel()
                await asyncio.gather(self._tick, return_exceptions=True)
            if self._backup is not None:
                await asyncio.gather(self._backup, return_exceptions=True)
            if self._storage is not None:
                await asyncio.gather(self._storage, return_exceptions=True)

            def shutdown(ledger: Ledger) -> Outcome:
                previous = records.current(ledger)
                if previous is None:
                    raise LedgerError("shutdown owner missing")
                return records.commit(
                    ledger,
                    identity("shutdown"),
                    previous.revision,
                    Command(
                        Action.SHUTDOWN,
                        self.scopes,
                        "mock-supervisor",
                        "explicit shutdown",
                        self._last_ms,
                        False,
                    ),
                    self.scopes,
                    lambda: None,
                )

            try:
                await self.writer.submit(shutdown)
                await self.status()
            except Exception:
                self._alert(AlertCode.STORAGE)
            for shutdown_owner in (self.portfolio.close, self.universe.close, self.executor.close):
                try:
                    await shutdown_owner()
                except Exception:
                    self._alert(AlertCode.RUNTIME)
        finally:
            await self.writer.close()
            self._closed = True

    async def close(self) -> None:
        self._local_halt = self._closing = True
        if self._shutdown is None:
            self._shutdown = asyncio.create_task(self._finish())
        try:
            await asyncio.wait_for(asyncio.shield(self._shutdown), self.limits.shutdown_ms / 1000)
        except TimeoutError:
            self._alert(AlertCode.RUNTIME)
            raise ExecutionError(
                "shutdown pending; ownership retained until cleanup finishes"
            ) from None
