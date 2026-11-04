"""Mock universe owner. Configuration, facts and send admission share the writer lane."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace

from grid.domain.model import Context, Mode, Order, Scope, State
from grid.domain.numbers import DomainError, bounded_int
from grid.execution.executor import MockExecutor
from grid.execution.models import ExecutionError
from grid.persistence import allocation_exit, codec
from grid.persistence.ledger import Ledger
from grid.persistence.records import Conflict, LedgerError, Outcome
from grid.persistence.universe import commit, current, mutate
from grid.portfolio.model import CyclePhase, ExitApproval, ValuationFacts
from grid.universe.controls import Retirement, UniverseEdit
from grid.universe.model import BuyAdmission, Facts, MarketKey, UniversePolicy, UniverseSnapshot
from grid.universe.selection import Eligibility, evaluate, select


class UniverseController:
    """No background tasks, funding, exchange calls or forgotten retired scopes.

    Clock callback returns (UTC ms, monotonic ns), supplied by the mock caller.
    All mutable caches are accessed only in operations on the sole writer.
    """

    def __init__(
        self, executor: MockExecutor, cache_size: int, clock: Callable[[], tuple[int, int]]
    ) -> None:
        bounded_int(cache_size, 1, 1000)
        if executor.buy_admission is not None:
            raise DomainError("executor already has a universe owner")
        self.executor, self.writer, self.scopes = executor, executor.writer, executor.scopes
        self._clock, self._capacity = clock, cache_size
        self._facts: OrderedDict[Scope, Facts] = OrderedDict()
        self._revisions: dict[Scope, int] = {}
        self._last_ms = self._last_ns = 0
        self._closed = False
        executor.buy_admission = self._admit

    def _now(self) -> tuple[int, int]:
        ms, ns = self._clock()
        bounded_int(ms, 0, 2**53 - 1)
        bounded_int(ns, 0, 2**63 - 1)
        if self._closed or ms < self._last_ms or ns < self._last_ns:
            raise ExecutionError("universe closed or clock regressed")
        self._last_ms, self._last_ns = ms, ns
        return ms, ns

    def _snapshot(self, ledger: Ledger) -> UniverseSnapshot | None:
        snapshot = current(ledger)
        if snapshot is not None and {m.scope for m in snapshot.members} != set(self.scopes):
            raise ExecutionError("persisted supervised roster must be retained")
        return snapshot

    async def observe(self, scope: Scope, facts: Facts) -> None:
        def store(ledger: Ledger) -> None:
            ms, ns = self._now()
            if scope not in self.scopes or facts.key != MarketKey.of(scope):
                raise DomainError("facts outside supervised ownership")
            if facts.revision <= self._revisions.get(scope, 0):
                raise DomainError("stale or repeated facts revision")
            if facts.received_ns > ns or facts.account_observed_ms > ms:
                raise DomainError("future evidence")
            snapshot = self._snapshot(ledger)
            capacity = (
                min(self._capacity, snapshot.policy.cache_size) if snapshot else self._capacity
            )
            self._facts[scope] = facts
            self._facts.move_to_end(scope)
            self._revisions[scope] = facts.revision
            while len(self._facts) > capacity:
                self._facts.popitem(last=False)

        await self.writer.submit(store)

    async def invalidate(self, scope: Scope) -> int:
        def drop(ledger: Ledger) -> int:
            self._now()
            self._snapshot(ledger)
            if scope not in self.scopes:
                raise DomainError("scope outside universe owner")
            self._facts.pop(scope, None)
            # Tombstone survives LRU eviction; an old response cannot restore admission.
            self._revisions[scope] = self._revisions.get(scope, 0) + 1
            return self._revisions[scope]

        return await self.writer.submit(drop)

    def _assessment(
        self,
        ledger: Ledger,
        scope: Scope,
        policy: UniversePolicy,
        previous: UniverseSnapshot | None,
        now: tuple[int, int],
    ) -> tuple[State, Eligibility]:
        snapshot = ledger.snapshot(scope)
        if snapshot.state is None:
            raise DomainError("pre-funded supervised allocation required")
        state = snapshot.state
        if state.mode in (Mode.RETIRING, Mode.HOLD):
            owned = previous is not None and any(
                m.scope == scope
                and m.managed_retirement
                and m.control_revision == state.control_revision
                for m in previous.members
            )
            if not owned:
                return state, Eligibility(False, "external_retirement")
        facts = self._facts.get(scope)
        if facts is None:
            return state, Eligibility(False, "missing_or_invalidated_facts")
        assessed = replace(state, mode=Mode.RETIRING) if state.mode is Mode.HOLD else state
        result = evaluate(assessed, facts, policy, *now)
        return state, result

    def edit(
        self,
        ledger: Ledger,
        request_id: str,
        change: UniverseEdit,
        actor: str,
        reason: str,
        observed_ms: int,
        approval: ExitApproval | None,
        facts: ValuationFacts | None,
    ) -> None:
        """Join the supervisor's audited transaction; never perform network I/O."""
        if not ledger.db.in_transaction:
            raise LedgerError("universe edit requires an operational transaction")
        now = self._now()
        previous = self._snapshot(ledger)
        if previous is None or previous.revision != change.revision:
            raise Conflict("stale universe configuration revision")
        if observed_ms > now[0]:
            raise DomainError("configuration time is in the future")
        policy = change.apply(previous.policy, self.scopes)
        targets = change.targets(self.scopes)
        exits: tuple[Scope, ...] = ()
        if change.retirement is Retirement.LIMIT:
            cycle = ledger.portfolio()
            if (
                cycle is None
                or cycle.phase is not CyclePhase.ACTIVE
                or approval is None
                or approval.cycle_id != cycle.id
                or approval.policy_sha256 != codec.digest(cycle.policy)
                or facts is None
            ):
                raise LedgerError("bounded retirement needs reviewed active cycle and fresh facts")
            exits = tuple(s for s in targets if not cycle.protects(s))
            if exits:
                allocation_exit.request(
                    ledger, request_id, exits, approval, facts, *now, actor, reason
                )
                ledger.storage.fault("operations:after_configuration_exit")
        elif approval is not None:
            raise DomainError("exit approval only belongs to bounded-limit-exit")

        def choose(before: UniverseSnapshot | None) -> tuple[Scope, ...]:
            rows = tuple(self._assessment(ledger, s, policy, before, now) for s in self.scopes)
            return tuple(
                s.scope
                for s in select(policy, rows, () if before is None else before.members, now[0])
            )

        mutate(
            ledger,
            request_id,
            change.revision,
            policy,
            self.scopes,
            actor,
            reason,
            now[0],
            choose,
            retirements={s: change.retirement for s in targets}
            if change.retirement is not None
            else {},
            untouched=frozenset(exits),
            restart_empty=True,
        )
        ledger.storage.fault("operations:after_configuration")

    async def configure(
        self,
        request_id: str,
        expected: int,
        policy: UniversePolicy,
        actor: str,
        reason: str,
        observed_ms: int,
    ) -> Outcome:
        def update(ledger: Ledger) -> Outcome:
            now = self._now()
            if observed_ms > now[0]:
                raise DomainError("configuration time is in the future")

            def choose(previous: UniverseSnapshot | None) -> tuple[Scope, ...]:
                rows = tuple(
                    self._assessment(ledger, scope, policy, previous, now) for scope in self.scopes
                )
                selected = select(
                    policy, rows, () if previous is None else previous.members, now[0]
                )
                return tuple(state.scope for state in selected)

            result = commit(
                ledger,
                request_id,
                expected,
                policy,
                self.scopes,
                actor,
                reason,
                observed_ms,
                choose,
            )
            while len(self._facts) > min(self._capacity, policy.cache_size):
                self._facts.popitem(last=False)
            return result

        return await self.writer.submit(update)

    def _admit(self, ledger: Ledger, scope: Scope, order: Order, context: Context) -> BuyAdmission:
        now = self._now()
        snapshot = self._snapshot(ledger)
        if snapshot is None or not any(m.scope == scope and m.selected for m in snapshot.members):
            raise ExecutionError("scope not admitted by current universe")
        state, assessment = self._assessment(ledger, scope, snapshot.policy, snapshot, now)
        facts = self._facts.get(scope)
        if not assessment.eligible or facts is None or state.mode is not Mode.ACTIVE:
            raise ExecutionError("universe admission blocked: " + assessment.reason)
        expected = replace(facts.context, now_ms=context.now_ms)
        if context != expected or not 0 <= now[0] - context.now_ms <= snapshot.policy.freshness_ms:
            raise ExecutionError("caller context differs from current universe evidence")
        if (
            order.config_revision != context.config_revision
            or order.market_generation != context.market.generation
        ):
            raise ExecutionError("order generation obsolete")
        expires = min(
            facts.account_observed_ms + snapshot.policy.freshness_ms,
            context.rules_observed_ms + context.policy.max_source_age_ms,
            context.fees_observed_ms + context.policy.max_source_age_ms,
            (facts.book.source_ms or 0)
            + min(snapshot.policy.freshness_ms, context.policy.max_source_age_ms),
        )
        return BuyAdmission(
            scope,
            snapshot.revision,
            facts.revision,
            facts.evidence_id,
            now[0],
            expires,
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

    async def status(self) -> tuple[UniverseSnapshot | None, tuple[Scope, ...], int]:
        def inspect(ledger: Ledger) -> tuple[UniverseSnapshot | None, tuple[Scope, ...], int]:
            self._now()
            return self._snapshot(ledger), self.scopes, len(self._facts)

        return await self.writer.submit(inspect)

    async def close(self) -> None:
        def stop(ledger: Ledger) -> None:
            self._facts.clear()
            self._closed = True
            # Keep the refusing guard installed; removing it must not bypass admission.

        await self.writer.submit(stop)
