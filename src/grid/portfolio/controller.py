"""One mock portfolio supervisor sharing the ledger's mutation/send-claim lane."""

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

from grid.domain.model import Context, Order, Scope, Side
from grid.domain.numbers import DomainError, arithmetic, bounded_int
from grid.execution.executor import MockExecutor
from grid.execution.models import ExecutionError
from grid.persistence import codec
from grid.persistence import portfolio as records
from grid.persistence.ledger import Ledger
from grid.persistence.records import Outcome
from grid.portfolio.model import (
    Cashflow,
    Cycle,
    CyclePhase,
    PortfolioPermit,
    PortfolioPolicy,
    ValuationFacts,
)
from grid.portfolio.valuation import Conversions, fresh, market_facts


class PortfolioController:
    def __init__(self, executor: MockExecutor, clock: Callable[[], tuple[int, int]]) -> None:
        if executor.portfolio_admission is not None:
            raise ExecutionError("executor already has a portfolio supervisor")
        self.executor, self.writer, self._clock = executor, executor.writer, clock
        self._facts: ValuationFacts | None = None
        self._revision = self._last_ms = self._last_ns = 0
        self._closed = False
        executor.portfolio_admission = self._admit

    def _now(self) -> tuple[int, int]:
        ms, ns = self._clock()
        bounded_int(ms, 0, 2**53 - 1)
        bounded_int(ns, 0, 2**63 - 1)
        if self._closed or ms < self._last_ms or ns < self._last_ns:
            raise ExecutionError("portfolio closed or clock regressed")
        self._last_ms, self._last_ns = ms, ns
        return ms, ns

    def _current(self, ledger: Ledger) -> Cycle | None:
        cycle = records.current(ledger)
        if cycle is not None and self._last_ms < cycle.last_evaluation_ms:
            raise ExecutionError("portfolio clock precedes durable observation")
        if cycle is not None and {r.scope for r in cycle.policy.exits} != set(self.executor.scopes):
            raise ExecutionError("portfolio supervision roster cannot shrink")
        return cycle

    def _evidence(self) -> ValuationFacts:
        if self._facts is None:
            raise ExecutionError("fresh portfolio observations required")
        return self._facts

    async def observe(self, facts: ValuationFacts) -> None:
        def store(ledger: Ledger) -> None:
            ms, ns = self._now()
            self._current(ledger)
            if facts.revision <= self._revision or facts.observed_ms > ms or facts.received_ns > ns:
                raise DomainError("stale or future portfolio evidence revision")
            self._facts, self._revision = facts, facts.revision

        await self.writer.submit(store)

    async def invalidate(self) -> int:
        def drop(ledger: Ledger) -> int:
            self._now()
            self._facts = None
            self._revision += 1
            return self._revision

        return await self.writer.submit(drop)

    async def begin(
        self,
        request_id: str,
        expected: int,
        cycle_id: str,
        policy: PortfolioPolicy,
        actor: str,
        reason: str,
        resume: tuple[Scope, ...] = (),
    ) -> Outcome:
        def start(ledger: Ledger) -> Outcome:
            ms, ns = self._now()
            self._current(ledger)
            if {r.scope for r in policy.exits} != set(self.executor.scopes):
                raise DomainError("cycle must own the complete executor roster")
            return records.begin(
                ledger,
                request_id,
                expected,
                cycle_id,
                policy,
                self._evidence(),
                ms,
                ns,
                actor,
                reason,
                resume,
            )

        return await self.writer.submit(start)

    async def evaluate(self, request_id: str, expected: int, actor: str, reason: str) -> Outcome:
        def update(ledger: Ledger) -> Outcome:
            ms, ns = self._now()
            self._current(ledger)
            return records.evaluate(
                ledger, request_id, expected, self._evidence(), ms, ns, actor, reason
            )

        return await self.writer.submit(update)

    async def advance(self, request_id: str, expected: int, actor: str, reason: str) -> Outcome:
        def update(ledger: Ledger) -> Outcome:
            ms, ns = self._now()
            self._current(ledger)
            return records.advance(
                ledger, request_id, expected, self._evidence(), ms, ns, actor, reason
            )

        return await self.writer.submit(update)

    async def cashflow(
        self, request_id: str, expected: int, flow: Cashflow, actor: str, reason: str
    ) -> Outcome:
        def update(ledger: Ledger) -> Outcome:
            ms, _ = self._now()
            self._current(ledger)
            if flow.observed_ms > ms:
                raise DomainError("cashflow observation in the future")
            return records.cashflow(ledger, request_id, expected, flow, actor, reason)

        return await self.writer.submit(update)

    async def status(self) -> Cycle | None:
        def inspect(ledger: Ledger) -> Cycle | None:
            self._now()
            return self._current(ledger)

        return await self.writer.submit(inspect)

    @arithmetic
    def _admit(
        self, ledger: Ledger, scope: Scope, order: Order, context: Context
    ) -> PortfolioPermit:
        ms, ns = self._now()
        cycle, facts = self._current(ledger), self._evidence()
        if cycle is None:
            raise ExecutionError("portfolio cycle not initialized")
        rows = records.load(ledger, cycle.policy)
        state, stored, _, recovery = rows[scope]
        if recovery or state.reconcile_reason is not None:
            raise ExecutionError("portfolio send requires reconciled ownership")
        fresh(facts, cycle.policy, ms, ns)
        observed = market_facts(state, stored, facts, cycle.policy, ms, ns)
        if (
            replace(context, now_ms=ms, allow_new_exposure=False)
            != replace(observed.context, now_ms=ms, allow_new_exposure=False)
            or not 0 <= ms - context.now_ms <= cycle.policy.nav_age_ms
        ):
            raise ExecutionError("send context differs from portfolio evidence")
        if order.side is Side.BUY:
            if (
                cycle.phase is not CyclePhase.ACTIVE
                or cycle.pending_flows
                or scope in cycle.policy.sinks
            ):
                raise ExecutionError("portfolio freezes new BUYs")
            value = records.nav(ledger, rows, facts, cycle.policy, ms, ns)
            if value.net <= 0 or facts.liabilities:
                raise ExecutionError("portfolio liabilities or nonpositive equity block BUYs")
            if value.net >= (cycle.baseline.net + cycle.adjustment) * Decimal("1.10"):
                records.evaluate(
                    ledger,
                    "claim-threshold-"
                    + codec.digest((cycle.id, cycle.revision, facts.revision, ms)),
                    cycle.revision,
                    facts,
                    ms,
                    ns,
                    "portfolio-supervisor",
                    "threshold observed before BUY claim",
                    at_claim=True,
                )
                raise ExecutionError("NAV threshold latched portfolio exit before BUY claim")
        else:
            slot = next((s for s in cycle.slots if s.scope == scope), None)
            rule = next(r for r in cycle.policy.exits if r.scope == scope)
            if (
                cycle.phase in (CyclePhase.ACTIVE, CyclePhase.COMPLETE)
                or cycle.pending_flows
                or cycle.started_exit_ms is None
                or slot is None
                or slot.order_id != order.id
                or slot.created_ms is None
                or not 0 <= ms - slot.created_ms < rule.ttl_ms
                or ms >= cycle.started_exit_ms + cycle.policy.exit_duration_ms
                or any(
                    recovery
                    or s.reconcile_reason is not None
                    or any(o.role != "portfolio_exit" or o.unknown for o in s.working)
                    for s, _, _, recovery in rows.values()
                )
            ):
                raise ExecutionError("exit order no longer authorized")
            Conversions(facts, cycle.policy, ms, ns).check_fee(state, context)
        return PortfolioPermit(
            cycle.id,
            cycle.revision,
            scope,
            order.id,
            ms,
            facts.revision,
            codec.digest(
                (
                    context.market,
                    context.policy,
                    context.buy_fee,
                    context.sell_fee,
                    context.config_revision,
                )
            ),
        )

    async def close(self) -> None:
        def stop(ledger: Ledger) -> None:
            self._facts, self._closed = None, True

        await self.writer.submit(stop)
