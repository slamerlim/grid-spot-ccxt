"""Repeated synthetic grid rounds under bounded research and reconciliation pressure."""

from __future__ import annotations

import asyncio
import os
import resource
import time
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory

from grid.domain.model import ActualFee, Context, Scope, Side, State
from grid.domain.numbers import ZERO, DomainError, arithmetic, bounded_int
from grid.domain.reducer import available_quote
from grid.execution.executor import MockExecutor
from grid.execution.models import Evidence, LimitRequest, Notice, OrderFact
from grid.execution.synthetic import SyntheticExchange
from grid.operations.contention import (
    CAPS,
    FILL_LAG_LIMIT_NS,
    PASSES,
    Worker,
    financial,
    history,
    process_sample,
)
from grid.operations.experiment import host
from grid.operations.synthetic import EXECUTION, STORAGE, Fixture, fixture
from grid.persistence import codec
from grid.persistence.ledger import Ledger, trade_identity
from grid.persistence.records import Backpressure
from grid.persistence.writer import AsyncWriter
from grid.research.artifacts import digest
from grid.research.config import load
from grid.research.provenance import Observation
from grid.research.replay import run as replay

MAX_ROUNDS = 8
CASE_TIMEOUT_SECONDS = 240
ZERO_FEES = (ActualFee("QUOTE", ZERO, None),)


def fill_rows(snapshot: dict[str, object]) -> tuple[tuple[str, str, str], ...]:
    rows = snapshot.get("fills")
    if not isinstance(rows, tuple):
        raise DomainError("mixed financial fill receipt missing")
    result = []
    for row in rows:
        if not isinstance(row, tuple) or len(row) != 3 or not all(isinstance(v, str) for v in row):
            raise DomainError("mixed financial fill receipt malformed")
        result.append((str(row[0]), str(row[1]), str(row[2])))
    return tuple(result)


class Mixed:
    def __init__(self, sample: Fixture, writer: AsyncWriter) -> None:
        self.sample, self.writer = sample, writer
        self.contexts = dict(sample.contexts)
        self.now = 10000
        self.pending: dict[str, tuple[Scope, int]] = {}
        self.lags: list[int] = []
        self.services: list[int] = []
        self.intake_peak = self.rejected = self.cancel_blocks = 0
        self.sell_peak = self.tasks_peak = self.wal_peak = 0
        self.samples: list[dict[str, int | str]] = []
        self.venue = SyntheticExchange(1000, record=self.record)
        self.executor = MockExecutor(
            writer,
            self.venue,
            sample.scopes,
            replace(EXECUTION, page_size=128, max_records=1000, max_pages=20),
            Evidence(True, True, True),
        )

    def record(self, kind: str, payload: object) -> None:
        if kind != "submit":
            return
        if not isinstance(payload, LimitRequest) or payload.order_type != "limit":
            raise DomainError("mixed fixture attempted a non-limit order")
        if payload.side is Side.BUY and codec.parse_amount(payload.amount) != 1:
            raise DomainError("mixed fixture enlarged a minimum buy lot")
        sells = len(self.open(payload.scope, Side.SELL)) + int(payload.side is Side.SELL)
        if sells > 1:
            raise DomainError("mixed fixture attempted overlapping external sells")
        self.sell_peak = max(self.sell_peak, sells)

    def context(self, scope: Scope, allow: bool = True) -> Context:
        # Explicit synthetic freshness/rate clock, distinct from measured monotonic latency.
        self.now += 1001
        original = self.contexts[scope]
        if original.book is None:
            raise DomainError("mixed fixture book missing")
        return replace(
            original,
            now_ms=self.now,
            rules_observed_ms=self.now,
            fees_observed_ms=self.now,
            book=replace(original.book, observed_ms=self.now),
            allow_new_exposure=allow,
        )

    def open(self, scope: Scope, side: Side) -> tuple[OrderFact, ...]:
        return tuple(
            fact
            for fact in self.venue.orders.values()
            if fact.request.scope == scope and fact.request.side is side and fact.status == "open"
        )

    def fill(self, fact: OrderFact, amount: D | None = None) -> None:
        name = f"mixed-fill-{len(self.venue.trades)}"
        remaining = codec.parse_amount(fact.request.amount) - fact.cumulative
        self.pending[name] = fact.request.scope, time.monotonic_ns()
        self.venue.fill(
            fact.external_id,
            name,
            remaining if amount is None else amount,
            codec.parse_amount(fact.request.price),
            ZERO_FEES,
        )

    async def inspect(self, scope: Scope) -> State:
        @arithmetic
        def inspect(ledger: Ledger) -> tuple[State, set[str]]:
            state = ledger.snapshot(scope).state
            if state is None:
                raise DomainError("mixed allocation missing")
            posted = {
                row[0]
                for row in ledger.db.execute(
                    "SELECT external_id FROM trade_keys WHERE allocation=?", (codec.digest(scope),)
                )
            }
            trades = [t for t in self.venue.trades if t.scope == scope and t.external_id in posted]
            cash, base = D(1000), ZERO
            for trade in trades:
                sign = D(-1) if trade.side is Side.BUY else D(1)
                cash += sign * trade.amount * trade.price
                base -= sign * trade.amount
            if (
                state.quote_cash != cash
                or state.owned_base != base
                or state.quote_cash + state.position_cost != D(1000) + state.realized_pnl
                or available_quote(state, self.contexts[scope]) < ZERO
                or sum(o.side is Side.SELL for o in state.working) > 1
            ):
                raise DomainError("mixed independent conservation or one-sell check failed")
            return state, posted

        state, posted = await self.writer.submit(inspect)
        finished = time.monotonic_ns()
        for name, (owner, created) in tuple(self.pending.items()):
            if owner == scope and name in posted:
                self.lags.append(finished - created)
                del self.pending[name]
        self.tasks_peak = max(self.tasks_peak, len(asyncio.all_tasks()))
        return state

    async def service(self, scope: Scope, *, expected: bool = True, allow: bool = True) -> None:
        started = time.monotonic_ns()
        accepted = await self.executor.service(scope, self.context(scope, allow))
        self.services.append(time.monotonic_ns() - started)
        if accepted != expected:
            raise DomainError(f"mixed reconciliation differs: {self.executor.last_error}")
        if not expected:
            if self.executor.last_error != "unresolved cancellation or submission":
                raise DomainError("mixed cancellation was blocked for an unexpected reason")
            self.cancel_blocks += 1
        await self.inspect(scope)

    async def send(self, scope: Scope, *, allow: bool = True) -> bool:
        return await self.executor.dispatch(scope, self.context(scope, allow))

    async def flush(self, scope: Scope) -> None:
        for _ in range(16):
            await self.service(scope)
            if not await self.send(scope):
                if self.executor.last_error is not None:
                    raise DomainError("mixed queued placement refused")
                return
        raise DomainError("mixed placement work cap exceeded")

    async def cancel_race(self, scope: Scope) -> str:
        sells, buys = self.open(scope, Side.SELL), self.open(scope, Side.BUY)
        if len(sells) != 1 or len(buys) != 3:
            raise DomainError("mixed round requires one sell and three minimum lower buys")
        sell = sells[0]
        lower = max(buys, key=lambda fact: codec.parse_amount(fact.request.price))
        self.fill(lower)
        await self.service(scope)
        if not await self.send(scope) or self.venue.cancels[-1] != sell.external_id:
            raise DomainError("mixed lower fill did not request old-sell cancellation")
        await self.service(scope, expected=False)
        if await self.send(scope):
            raise DomainError("mixed unknown cancellation allowed a new send")
        self.fill(sell, codec.parse_amount(sell.request.amount) / 2)
        return sell.external_id

    def pressure(self) -> None:
        for index in range(self.executor.limits.queue_size * 4):
            scope = self.sample.scopes[index % len(self.sample.scopes)]
            try:
                self.executor.offer(Notice(scope, self.executor.epoch, "trade"))
            except Backpressure:
                self.rejected += 1
            self.intake_peak = max(self.intake_peak, self.executor.intake_depth)
        if not self.executor.degraded or not self.rejected:
            raise DomainError("mixed pressure did not halt exposure")

    async def drain(self, scope: Scope, *, late_buy: bool) -> None:
        buys = {fact.external_id for fact in self.open(scope, Side.BUY)}
        filled_late = False
        for _ in range(16):
            remaining = {key for key in buys if self.venue.orders[key].status == "open"}
            if not remaining:
                if late_buy != filled_late:
                    raise DomainError("mixed late-buy path missing")
                return
            before = len(self.venue.cancels)
            if not await self.send(scope, allow=late_buy) or len(self.venue.cancels) != before + 1:
                raise DomainError("mixed drain did not issue one cancellation")
            external = self.venue.cancels[-1]
            if external not in remaining:
                raise DomainError("mixed drain canceled an unexpected order")
            await self.service(scope, expected=False, allow=late_buy)
            if await self.send(scope, allow=late_buy):
                raise DomainError("mixed drain replaced an uncertain order")
            if late_buy and not filled_late:
                self.fill(self.venue.orders[external])
                filled_late = True
            self.venue.settle_cancel(external)
            await self.service(scope, allow=late_buy)
        raise DomainError("mixed drain work cap exceeded")


async def case(
    directory: Path, sources: frozenset[str], venues: int, revision: str, *, rounds: int = 3
) -> dict[str, object]:
    bounded_int(venues, 1, 7)
    bounded_int(rounds, 1, MAX_ROUNDS)
    config = history(directory, revision)
    baseline = replay(load(config))
    sample = fixture(venues, venues)
    path = directory / "ledger.db"
    writer = await AsyncWriter.open(path, STORAGE, sources, mock_storage=True)
    mixed: Mixed | None = None
    worker: Worker | None = None
    jobs: list[dict[str, object]] = []
    rounds_report: list[dict[str, object]] = []
    caps = replace(CAPS, read_bytes_per_second=CAPS.read_bytes_per_second // venues)
    try:
        await writer.submit(sample.prepare)
        mixed = Mixed(sample, writer)
        for scope in sample.scopes:
            await mixed.service(scope)
            if not await mixed.send(scope):
                raise DomainError("mixed initial seed refused")
            seed = mixed.open(scope, Side.BUY)
            if len(seed) != 1 or codec.parse_amount(seed[0].request.amount) != 1:
                raise DomainError("mixed seed is not one minimum lot")
            mixed.fill(seed[0])
            await mixed.service(scope)
        for index in range(rounds):
            started = time.monotonic_ns()
            for scope in sample.scopes:
                await mixed.flush(scope)
            worker = await Worker.start(config, caps)
            ready = await worker.frame("ready")
            await worker.go()
            await worker.frame("active")
            before = worker.sample()
            cancels = [await mixed.cancel_race(scope) for scope in sample.scopes]
            overlap = worker.sample()
            mixed.pressure()
            stopped = await worker.pause()
            for scope in sample.scopes:
                if await mixed.send(scope):
                    raise DomainError("mixed overload allowed an order")
            for scope, external in zip(sample.scopes, cancels, strict=True):
                mixed.venue.settle_cancel(external)
                await mixed.service(scope)
            drained = worker.sample()
            if (
                drained["state"] != "T"
                or drained["cpu_ticks"] != stopped["cpu_ticks"]
                or mixed.pending
                or mixed.executor.degraded
                or mixed.executor.intake_depth
            ):
                raise DomainError("mixed pressure drain or worker pause incomplete")
            worker.resume()
            for scope in sample.scopes:
                await mixed.flush(scope)
                sells = mixed.open(scope, Side.SELL)
                if len(sells) != 1 or sells[0].external_id in cancels:
                    raise DomainError("mixed successor sell missing")
                successor = sells[0]
                mixed.fill(successor, codec.parse_amount(successor.request.amount) / 2)
                await mixed.service(scope)
                mixed.fill(mixed.venue.orders[successor.external_id])
                await mixed.service(scope, allow=index + 1 < rounds)
                await mixed.drain(scope, late_buy=index + 1 < rounds)
            current = await writer.submit(lambda ledger: financial(ledger, sample.scopes))
            for scope in sample.scopes:
                await mixed.service(scope, allow=index + 1 < rounds)
            if await writer.submit(lambda ledger: financial(ledger, sample.scopes)) != current:
                raise DomainError("mixed duplicate reconciliation changed financial rows")
            done = await worker.frame("done")
            async with asyncio.timeout(5):
                code = await worker.process.wait()
            if (
                code != 0
                or done.get("result_hashes") != [baseline["result_sha256"]] * PASSES
                or done.get("logical_read_bytes") != caps.total_read_bytes
            ):
                raise DomainError("mixed research results or read work differ")
            jobs.append(
                {
                    "ready": ready,
                    "before": before,
                    "overlap": overlap,
                    "stopped": stopped,
                    "drained": drained,
                    "done": done,
                    "exit_code": code,
                }
            )
            await worker.close()
            worker = None
            rounds_report.append(
                {
                    "round": index + 1,
                    "elapsed_ns": time.monotonic_ns() - started,
                    "financial_sha256": current["sha256"],
                    "distinct_fills": len(fill_rows(current)),
                    "late_buy": index + 1 < rounds,
                }
            )
            mixed.samples.append(process_sample(os.getpid()))
            wal = Path(str(path) + "-wal")
            mixed.wal_peak = max(mixed.wal_peak, wal.stat().st_size if wal.exists() else 0)
        final = await writer.submit(lambda ledger: financial(ledger, sample.scopes))
        for scope in sample.scopes:
            state = await mixed.inspect(scope)
            if state.owned_base or state.position_cost or state.working:
                raise DomainError("mixed final drain left inventory or an unresolved order")
        trades = mixed.venue.trades
        if (
            mixed.pending
            or len(fill_rows(final)) != len(trades)
            or {row[1] for row in fill_rows(final)}
            != {trade_identity(t.scope, t.external_id) for t in trades}
        ):
            raise DomainError("mixed durable trade identities differ")
        requests = tuple(mixed.venue.sent)
        seed_count = await writer.submit(
            lambda ledger: sum(
                ledger.order(scope=request.scope, order_id=request.order_id).role == "seed"
                for request in requests
            )
        )
        if seed_count != venues or any(f.status == "open" for f in mixed.venue.orders.values()):
            raise DomainError("mixed final state has duplicate seeds or live external orders")
        await mixed.executor.close()
        await writer.close()
        writer = await AsyncWriter.open(path, STORAGE, sources, mock_storage=True)
        restored = await writer.submit(lambda ledger: financial(ledger, sample.scopes))
        if restored != final:
            raise DomainError("mixed reopen changed financial rows")
        return {
            "venues": venues,
            "funded_allocations": venues,
            "rounds": rounds_report,
            "caps_per_job": caps,
            "jobs": jobs,
            "worker_concurrency": 1,
            "distinct_fills": len(trades),
            "buy_fills": sum(t.side is Side.BUY for t in trades),
            "sell_fills": sum(t.side is Side.SELL for t in trades),
            "late_buy_fills": (rounds - 1) * venues,
            "seed_submissions": seed_count,
            "rejected_hints": mixed.rejected,
            "intake_capacity": mixed.executor.limits.queue_size,
            "intake_peak": mixed.intake_peak,
            "cancel_ambiguity_blocks": mixed.cancel_blocks,
            "external_sell_peak_per_allocation": mixed.sell_peak,
            "sampled_active_tasks_peak": mixed.tasks_peak,
            "sampled_wal_peak_bytes": mixed.wal_peak,
            "parent_samples": mixed.samples,
            "parent_lifetime_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * 1024,
            "fill_lag_ns": mixed.lags,
            "service_ns": mixed.services,
            "fill_lag_limit_ns": FILL_LAG_LIMIT_NS,
            "financial_sha256": final["sha256"],
            "reopened_financial_sha256": restored["sha256"],
            "final_money": final["money"],
            "financial_fixture_sha256": digest(sample),
            "research_inputs": baseline["inputs"],
            "research_config": baseline["config"],
            "unpaced_result_sha256": baseline["result_sha256"],
            "synthetic_page_calls": mixed.venue.reads,
            "limit_submissions": len(mixed.venue.sent),
            "cancel_requests": len(mixed.venue.cancels),
            "external_api_calls": 0,
            "checks": {
                "within_declared_fill_lag": max(mixed.lags) <= FILL_LAG_LIMIT_NS,
                "conservation": True,
                "minimum_buy_lots": True,
                "limit_only": True,
                "late_inventory_without_duplicate_seed": True,
                "one_sell": True,
                "distinct_fills_durable": True,
                "duplicates_idempotent": True,
                "reopen_exact": True,
                "final_drain_complete": True,
                "overload_blocks_orders": True,
                "cancel_finality_before_replacement": True,
                "research_paused_and_reaped": True,
                "research_results_unchanged": True,
            },
        }
    finally:
        try:
            if worker is not None:
                await worker.close()
        finally:
            try:
                if mixed is not None:
                    await mixed.executor.close()
            finally:
                await writer.close()


async def experiment(revision: str, source_id: str, rounds: int) -> dict[str, object]:
    bounded_int(rounds, 1, MAX_ROUNDS)
    observation = Observation.start(revision)
    rows = []
    with TemporaryDirectory(prefix="grid-mixed-contention-") as temporary:
        root = Path(temporary)
        profile = host(root)
        for venues in (1, 7):
            directory = root / str(venues)
            directory.mkdir(mode=0o700)
            async with asyncio.timeout(CASE_TIMEOUT_SECONDS):
                rows.append(
                    await case(directory, frozenset({source_id}), venues, revision, rounds=rounds)
                )
    passed = all(
        isinstance(row["checks"], dict) and all(value is True for value in row["checks"].values())
        for row in rows
    )
    return observation.finish(
        "resource-contention",
        {
            "schema": "grid-mixed-contention-v1",
            "mode": "synthetic_offline",
            "live": "DISABLED",
            "host": profile,
            "rounds_per_case": rounds,
            "cases": rows,
            "case_wall_timeout_seconds": CASE_TIMEOUT_SECONDS,
            "resource_checks_passed": passed,
            "limitations": (
                "Finite 1/7-allocation rounds; not 1,000 funded allocations or an observed soak.",
                "Isolated mock executor workload; no portfolio supervisor or external alerting.",
                "Invented zero-fee limit fills; no exchange, queue or target-venue evidence.",
                "Synthetic freshness/rate clock advances per operation; lag uses monotonic time.",
                "One bounded child per round, reaped before the next; no fixed-rate fill stream.",
                "CPU/address-space ceilings and logical-read pacing are not RSS/device-I/O quotas.",
                "Worker overlap precedes pressure; it may finish before later round phases.",
                "Clean writer reopen is distinct from crash/power-loss recovery tests.",
                "Parent samples miss transient peaks; lifetime RSS is not per-case memory.",
                "Temporary inputs/ledgers are cleaned normally; reports retain manifests/hashes.",
            ),
        },
    )
