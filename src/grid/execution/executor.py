"""One mock sender, bounded private-event hints and authoritative fake REST recovery.

No background retry loop. The caller schedules service ticks; each tick and send
is serialized, bounded and observable. Private hints never manufacture fills.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from functools import partial
from weakref import WeakKeyDictionary

from grid.domain.model import (
    Cancel,
    Context,
    Fill,
    Mode,
    Order,
    OrderReport,
    Phase,
    PlaceLimit,
    Reconcile,
    Reconciled,
    Scope,
    Side,
)
from grid.domain.numbers import ZERO, exact, identifier
from grid.execution.models import (
    Evidence,
    ExecutionError,
    Formatter,
    Limits,
    Notice,
    OrderFact,
    Page,
    RateLimited,
    TradeFact,
    exact_format,
    limit_request,
)
from grid.execution.synthetic import SyntheticExchange
from grid.persistence import codec
from grid.persistence.ledger import Ledger, client_identity, trade_identity
from grid.persistence.records import Backpressure, Intent, Outcome
from grid.persistence.storage import Fault, no_fault
from grid.persistence.writer import AsyncWriter
from grid.portfolio.model import PortfolioPermit
from grid.universe.model import BuyAdmission

_OWNERS: WeakKeyDictionary[AsyncWriter, object] = WeakKeyDictionary()


@dataclass(frozen=True)
class SnapshotPlan:
    """One bounded validated page set; immutable committed facts need no new posting."""

    facts: dict[str, OrderFact]
    trades: tuple[TradeFact, ...]
    fee_breach: bool
    bound: frozenset[str]
    posted: frozenset[str]
    terminal: frozenset[str]


def request_id() -> str:
    return "executor-" + uuid.uuid4().hex


def state_version(outcome: Outcome) -> tuple[int, int]:
    if outcome.state is None or outcome.version is None:
        raise ExecutionError("allocation snapshot missing")
    return outcome.version, outcome.state.reconcile_revision


def decode_intent(intent: Intent) -> PlaceLimit | Cancel | Reconcile:
    # Canonical, bounded allowlisted decoder; never trust a stored import name.
    try:
        return codec.loads(intent.payload, PlaceLimit)
    except ValueError:
        try:
            return codec.loads(intent.payload, Cancel)
        except ValueError:
            return codec.loads(intent.payload, Reconcile)


class MockExecutor:
    def __init__(
        self,
        writer: AsyncWriter,
        gateway: SyntheticExchange,
        scopes: tuple[Scope, ...],
        limits: Limits,
        evidence: Evidence,
        *,
        formatter: Formatter = exact_format,
        fault: Fault = no_fault,
    ) -> None:
        if type(gateway) is not SyntheticExchange:
            raise ExecutionError("only the in-memory synthetic gateway is admitted")
        if not 1 <= len(scopes) <= 1000 or len(set(scopes)) != len(scopes):
            raise ExecutionError("bounded unique allocation scopes required")
        if len({(scope.venue, scope.account, scope.symbol) for scope in scopes}) != len(scopes):
            raise ExecutionError("mock admission requires one allocation per account/symbol")
        if writer in _OWNERS:
            raise ExecutionError("this writer already has an executor")
        self.writer, self.gateway, self.scopes = writer, gateway, scopes
        self.limits, self.evidence, self.formatter, self.fault = limits, evidence, formatter, fault
        self._owner = object()
        _OWNERS[writer] = self._owner
        self._lock = asyncio.Lock()
        self._queue: deque[Notice] = deque()
        self._ready: dict[Scope, int] = {}
        self._calls: dict[tuple[str, str], deque[int]] = {}
        self._next_read: dict[tuple[str, str], int] = {}
        self._failures: dict[tuple[str, str], int] = {}
        self._last_clock = 0
        self.epoch = 1
        self.last_error: str | None = "startup reconciliation required"
        self._closed = False
        self.buy_admission: Callable[[Ledger, Scope, Order, Context], BuyAdmission] | None = None
        self.operational_admission: Callable[[Ledger, Scope], int] | None = None
        self.portfolio_admission: (
            Callable[[Ledger, Scope, Order, Context], PortfolioPermit] | None
        ) = None

    @property
    def intake_depth(self) -> int:
        return len(self._queue)

    @property
    def degraded(self) -> bool:
        return bool(self._queue) or any(
            scope not in self._ready
            or self._last_clock - self._ready[scope] >= self.limits.quiet_ms
            for scope in self.scopes
        )

    def _check(self, scope: Scope, now: int) -> None:
        if (
            self._closed
            or scope not in self.scopes
            or type(now) is not int
            or now < self._last_clock
        ):
            raise ExecutionError("closed executor, foreign scope or regressing clock")
        self._last_clock = now

    def degrade(self, reason: str) -> None:
        identifier(reason)
        self.epoch += 1
        self._ready.clear()
        self.last_error = reason

    def offer(self, notice: Notice) -> None:
        if self._closed or type(notice) is not Notice or notice.scope not in self.scopes:
            raise ExecutionError("private event outside owned scopes")
        if notice.epoch != self.epoch:
            self.degrade("stale private stream epoch")
            raise ExecutionError("stale private stream epoch")
        if len(self._queue) >= self.limits.queue_size:
            self.degrade("private intake overloaded")
            raise Backpressure("private event was not admitted; retain input and reconcile")
        self._queue.append(notice)
        # Even a heartbeat proves no absence of fills.
        self._ready.pop(notice.scope, None)

    def _budget(self, scope: Scope, now: int) -> None:
        calls = self._calls.setdefault((scope.venue, scope.account), deque())
        while calls and now - calls[0] >= self.limits.window_ms:
            calls.popleft()
        if len(calls) >= self.limits.calls_per_window:
            raise ExecutionError("fake REST rate budget exhausted")
        calls.append(now)

    async def _pages(
        self, scope: Scope, now: int, epoch: int
    ) -> tuple[tuple[OrderFact, ...], tuple[TradeFact, ...]]:
        orders: list[OrderFact] = []
        trades: list[TradeFact] = []
        cursor = None
        cursors: set[str] = set()
        token = None
        async with asyncio.timeout(self.limits.timeout_ms / 1000):
            for _ in range(self.limits.max_pages):
                self._budget(scope, now)
                page = await self.gateway.page(scope, epoch, cursor, self.limits.page_size)
                if type(page) is not Page or page.epoch != epoch or self.epoch != epoch:
                    raise ExecutionError("reconciliation epoch changed")
                identifier(page.token)
                if token is not None and token != page.token:
                    raise ExecutionError("snapshot changed during pagination")
                token = page.token
                if (
                    type(page.orders) is not tuple
                    or type(page.trades) is not tuple
                    or type(page.complete) is not bool
                    or len(page.orders) + len(page.trades) > self.limits.page_size
                    or any(type(fact) is not OrderFact for fact in page.orders)
                    or any(type(fact) is not TradeFact for fact in page.trades)
                ):
                    raise ExecutionError("malformed or oversized snapshot page")
                orders.extend(page.orders)
                trades.extend(page.trades)
                if len(orders) + len(trades) > self.limits.max_records:
                    raise ExecutionError("reconciliation record bound exceeded")
                if page.next_cursor is None:
                    if not page.complete:
                        raise ExecutionError("snapshot completeness is unknown")
                    return tuple(orders), tuple(trades)
                identifier(page.next_cursor)
                if (
                    page.complete
                    or page.next_cursor in cursors
                    or not page.orders
                    and not page.trades
                ):
                    raise ExecutionError("invalid or nonadvancing pagination")
                cursor = page.next_cursor
                cursors.add(cursor)
        raise ExecutionError("reconciliation page bound exceeded")

    @staticmethod
    @exact
    def _validate_snapshot(
        ledger: Ledger,
        scope: Scope,
        orders: tuple[OrderFact, ...],
        trades: tuple[TradeFact, ...],
    ) -> SnapshotPlan:
        facts: dict[str, OrderFact] = {}
        internal: dict[int, Order] = {}
        allocation = codec.digest(scope)
        bound: set[str] = set()
        posted: set[str] = set()
        terminal: set[str] = set()
        for fact in orders:
            request = fact.request
            if (
                request.scope != scope
                or request.client_id != client_identity(scope, request.order_id)
                or request.order_type != "limit"
            ):
                raise ExecutionError("foreign order or mismatched client identity")
            if fact.external_id in facts:
                if facts[fact.external_id] != fact:
                    raise ExecutionError("conflicting duplicate order")
                continue
            if request.order_id in internal:
                raise ExecutionError("multiple external orders for one internal order")
            order = ledger.order(scope, request.order_id)
            if not ledger.attempted(scope, order.id) or (
                order.side,
                order.amount,
                order.price,
                order.config_revision,
                order.market_generation,
            ) != (
                request.side,
                codec.parse_amount(request.amount),
                codec.parse_amount(request.price),
                request.config_revision,
                request.market_generation,
            ):
                raise ExecutionError("external order contradicts durable intent")
            expected_binding = (
                allocation,
                order.id,
                scope.venue,
                scope.account,
                scope.symbol,
                fact.external_id,
                request.client_id,
            )
            bindings = ledger.db.execute(
                "SELECT * FROM order_bindings WHERE allocation=? AND order_id=? "
                "OR venue=? AND account=? AND symbol=? AND external_id=?",
                (allocation, order.id, scope.venue, scope.account, scope.symbol, fact.external_id),
            ).fetchall()
            if bindings:
                if bindings != [expected_binding]:
                    raise ExecutionError("external order contradicts durable binding")
                bound.add(fact.external_id)
            if (
                order.terminal
                and fact.status != "open"
                and fact.cumulative == order.filled
                and (fact.status != "filled" or fact.cumulative == order.amount)
                and (fact.status != "rejected" or fact.cumulative == ZERO)
            ):
                terminal.add(fact.external_id)
            facts[fact.external_id] = fact
            internal[order.id] = order
        snapshot = ledger.snapshot(scope)
        if snapshot.state is None:
            raise ExecutionError("allocation missing")
        for order in snapshot.state.working:
            if ledger.attempted(scope, order.id) and order.id not in internal:
                raise ExecutionError("attempted order unresolved by complete snapshot")
        unique: dict[str, TradeFact] = {}
        sequences: dict[int, str] = {}
        totals: dict[str, Decimal] = {}
        fees: dict[str, dict[str, Decimal]] = {}
        counts: dict[str, int] = {}
        for trade in trades:
            if trade.external_id in unique:
                if unique[trade.external_id] != trade:
                    raise ExecutionError("conflicting duplicate actual trade")
                continue
            if (
                trade.scope != scope
                or trade.external_order not in facts
                or trade.fees is None
                or trade.side != facts[trade.external_order].request.side
                or trade.sequence in sequences
            ):
                raise ExecutionError("trade lacks matching order, fee evidence or unique sequence")
            unique[trade.external_id] = trade
            trade_id = trade_identity(scope, trade.external_id)
            order_id = facts[trade.external_order].request.order_id
            key = ledger.db.execute(
                "SELECT allocation,trade_id FROM trade_keys "
                "WHERE venue=? AND account=? AND symbol=? AND external_id=?",
                (scope.venue, scope.account, scope.symbol, trade.external_id),
            ).fetchone()
            stored = ledger.db.execute(
                "SELECT order_id,payload FROM fills WHERE allocation=? AND trade_id=?",
                (allocation, trade_id),
            ).fetchone()
            if key is not None or stored is not None:
                fill = Fill(scope, trade_id, order_id, trade.amount, trade.price, trade.fees)
                if (
                    key != (allocation, trade_id)
                    or stored != (order_id, codec.dumps(fill, limit=ledger.bounds.snapshot_bytes))
                    or trade.external_order not in bound
                ):
                    raise ExecutionError(
                        "external trade contradicts committed fill identity or payload"
                    )
                posted.add(trade.external_id)
            sequences[trade.sequence] = trade.external_id
            totals[trade.external_order] = totals.get(trade.external_order, ZERO) + trade.amount
            counts[trade.external_order] = counts.get(trade.external_order, 0) + 1
            order_fees = fees.setdefault(trade.external_order, {})
            for fee in trade.fees:
                order_fees[fee.currency] = order_fees.get(fee.currency, ZERO) + fee.amount
        # This mock contract is a complete history, not an unproved rolling
        # window. Previously posted orders/trades cannot disappear on reconnect.
        for (external_id,) in ledger.db.execute(
            "SELECT external_id FROM order_bindings WHERE allocation=?",
            (allocation,),
        ):
            if external_id not in facts:
                raise ExecutionError("complete snapshot omitted a bound order")
        for (external_id,) in ledger.db.execute(
            "SELECT external_id FROM trade_keys WHERE allocation=?",
            (allocation,),
        ):
            if external_id not in unique:
                raise ExecutionError("complete snapshot omitted a posted trade")
        fee_breach = False
        _, context, _, _ = ledger._load(codec.digest(scope))
        for external, fact in facts.items():
            if totals.get(external, ZERO) != fact.cumulative:
                raise ExecutionError("order cumulative differs from complete actual trades")
            order = internal[fact.request.order_id]
            forecast = order.fee_rule.estimate(context.market, order.price, order.amount)
            fee_breach |= counts.get(external, 0) > order.fee_rule.max_settlements or any(
                amount > (forecast.amount if currency == forecast.currency else ZERO)
                for currency, amount in fees.get(external, {}).items()
            )
        return SnapshotPlan(
            facts,
            tuple(sorted(unique.values(), key=lambda trade: trade.sequence)),
            fee_breach,
            frozenset(bound),
            frozenset(posted),
            frozenset(terminal),
        )

    async def _apply(
        self,
        scope: Scope,
        context: Context,
        event: Fill | OrderReport | Reconciled,
        external: tuple[str, str] | None = None,
    ) -> Outcome:
        key = request_id()

        def post(ledger: Ledger) -> Outcome:
            version, _ = state_version(ledger.snapshot(scope))
            return ledger.apply(key, version, event, context, external=external)

        outcome = await self.writer.submit(post)
        if not outcome.accepted:
            raise ExecutionError("ledger rejected reconciled fact")
        return outcome

    async def _reconcile(self, scope: Scope, context: Context) -> None:
        epoch = self.epoch
        await self.writer.submit(
            lambda ledger: ledger.hold(request_id(), scope, "executor reconciliation")
        )
        orders, trades = await self._pages(scope, context.now_ms, epoch)
        plan = await self.writer.submit(
            lambda ledger: self._validate_snapshot(ledger, scope, orders, trades)
        )
        # These sets describe immutable SQLite rows checked in the sole writer, not TTL state.
        # Full pages, identities, quantities, fee bounds and omissions were validated above.
        for fact in plan.facts.values():
            if fact.external_id in plan.bound:
                continue
            await self.writer.submit(
                partial(
                    Ledger.bind,
                    request_id=request_id(),
                    scope=scope,
                    order_id=fact.request.order_id,
                    external_id=fact.external_id,
                    client_id=fact.request.client_id,
                )
            )
        intake_context = replace(context, allow_new_exposure=False)
        for trade in plan.trades:
            if trade.external_id in plan.posted:
                continue
            order_id = plan.facts[trade.external_order].request.order_id
            fill = Fill(
                scope,
                trade_identity(scope, trade.external_id),
                order_id,
                trade.amount,
                trade.price,
                trade.fees,
            )
            await self._apply(
                scope, intake_context, fill, (trade.external_order, trade.external_id)
            )
        for fact in plan.facts.values():
            if fact.external_id in plan.terminal:
                continue
            await self._apply(
                scope,
                intake_context,
                OrderReport(
                    scope,
                    fact.request.order_id,
                    fact.status,
                    fact.cumulative,
                    True,
                ),
            )
        # The reducer cannot know whether an unacknowledged intent ever crossed
        # the send boundary. The ledger can prove that a queued intent did not.
        current = await self.writer.submit(lambda ledger: ledger.snapshot(scope))
        if current.state is None:
            raise ExecutionError("allocation snapshot missing")
        for order in current.state.working:
            needs_cancel = (
                order.side is Side.BUY
                and (
                    current.state.phase is Phase.DRAINING
                    or current.state.mode in (Mode.RETIRING, Mode.PORTFOLIO_EXIT, Mode.EXIT_BLOCKED)
                )
                or order.side is Side.SELL
                and current.state.sell_dirty
            )
            if needs_cancel:

                def revoke(ledger: Ledger, order_id: int = order.id) -> None:
                    if not ledger.attempted(scope, order_id):
                        version, _ = state_version(ledger.snapshot(scope))
                        outcome = ledger.revoke_unsent(
                            request_id(), scope, version, order_id, intake_context
                        )
                        if not outcome.accepted:
                            raise ExecutionError("unsent revocation was rejected")

                await self.writer.submit(revoke)
        if plan.fee_breach:
            raise ExecutionError("actual fees exceed the reserved forecast")
        if epoch != self.epoch:
            raise ExecutionError("stream changed while posting recovery")
        snapshot = await self.writer.submit(lambda ledger: ledger.snapshot(scope))
        version, revision = state_version(snapshot)
        if snapshot.state is None:
            raise ExecutionError("allocation snapshot missing")
        # A cancel acknowledgment + OPEN snapshot leaves the cancel uncertain.
        intents = await self._intents(scope)
        if any(intent.status == "uncertain" for intent in intents):
            raise ExecutionError("unresolved cancellation or submission")
        event = Reconciled(
            scope,
            context.config_revision,
            context.market.generation,
            snapshot.state.reconcile_reason,
            revision,
        )
        result = await self.writer.submit(
            lambda ledger: ledger.apply(
                request_id(),
                version,
                event,
                context,
            )
        )
        if not result.accepted or result.state is None or result.state.reconcile_reason is not None:
            raise ExecutionError("ledger recovery remains blocked")
        if epoch != self.epoch:
            raise ExecutionError("stream epoch changed before admission")

    async def _intents(self, scope: Scope) -> tuple[Intent, ...]:
        result: list[Intent] = []
        after = ""
        read_rows = await self.writer.submit(lambda ledger: ledger.bounds.read_rows)
        for _ in range(self.limits.max_pages):
            batch = await self.writer.submit(partial(Ledger.intents, scope=scope, after=after))
            if not batch:
                return tuple(result)
            result.extend(batch)
            if len(result) > self.limits.max_records:
                raise ExecutionError("intent admission bound exceeded")
            if len(batch) < read_rows:
                return tuple(result)
            after = batch[-1].id
        raise ExecutionError("intent page bound exceeded")

    async def service(self, scope: Scope, context: Context) -> bool:
        """One explicit fake REST pass, with finite budget/backoff and no resend."""
        if self._lock.locked():
            raise Backpressure("executor busy; no extra task admitted")
        async with self._lock:
            self._check(scope, context.now_ms)
            self._ready.pop(scope, None)
            self._queue = deque(notice for notice in self._queue if notice.scope != scope)
            account = (scope.venue, scope.account)
            if context.now_ms < self._next_read.get(account, 0):
                return False
            try:
                await self._reconcile(scope, context)
            except asyncio.CancelledError:
                self.degrade("reconciliation caller canceled")
                raise
            except Exception as exc:
                self.last_error = (
                    str(exc) if isinstance(exc, ExecutionError) else "ledger or gateway failure"
                )
                self._failures[account] = min(self._failures.get(account, 0) + 1, 16)
                # Stable bounded jitter avoids synchronized mock recovery schedules.
                jitter = int(codec.digest(scope)[:4], 16) % self.limits.backoff_ms
                delay = min(
                    self.limits.max_backoff_ms,
                    self.limits.backoff_ms * 2 ** (self._failures[account] - 1) + jitter,
                )
                if isinstance(exc, RateLimited):
                    delay = max(delay, exc.retry_after_ms)
                self._next_read[account] = context.now_ms + delay
                return False
            self._failures.pop(account, None)
            self._next_read.pop(account, None)
            self.last_error = None
            self._ready[scope] = context.now_ms
            return True

    async def dispatch(self, scope: Scope, context: Context) -> bool:
        """At most one durable first attempt. Unknown outcomes require service()."""
        if self._lock.locked():
            raise Backpressure("executor busy; no extra task admitted")
        async with self._lock:
            self._check(scope, context.now_ms)
            reconciled = self._ready.get(scope)
            if (
                reconciled is None
                or context.now_ms - reconciled >= self.limits.quiet_ms
                or self._queue
            ):
                self.last_error = "fresh reconciliation required before send"
                return False
            try:
                candidates = [
                    (intent, decode_intent(intent))
                    for intent in await self._intents(scope)
                    if intent.status == "queued"
                ]
                candidates.sort(
                    key=lambda pair: (
                        0
                        if isinstance(pair[1], Cancel)
                        else 1
                        if isinstance(pair[1], PlaceLimit) and pair[1].order.side is Side.SELL
                        else 2,
                        pair[1].order.id if isinstance(pair[1], PlaceLimit) else 0,
                    )
                )
                for intent, command in candidates:
                    if isinstance(command, Reconcile):
                        continue
                    order_id = (
                        command.order.id if isinstance(command, PlaceLimit) else command.order_id
                    )
                    order = await self.writer.submit(
                        partial(Ledger.order, scope=scope, order_id=order_id)
                    )
                    if order.terminal:
                        continue
                    payload = (
                        limit_request(scope, order, context, self.evidence, self.formatter)
                        if isinstance(command, PlaceLimit)
                        else None
                    )
                    external = await self.writer.submit(
                        partial(Ledger.binding, scope=scope, order_id=order_id)
                    )
                    if isinstance(command, Cancel) and not await self.writer.submit(
                        partial(Ledger.attempted, scope=scope, order_id=order_id)
                    ):
                        # Durable proof of no first attempt permits local revocation.
                        await self._apply(
                            scope, context, OrderReport(scope, order_id, "canceled", ZERO, True)
                        )
                        return True
                    if isinstance(command, Cancel) and external is None:
                        raise ExecutionError("cancellation lacks an external order binding")
                    snapshot = await self.writer.submit(lambda ledger: ledger.snapshot(scope))
                    version, _ = state_version(snapshot)
                    if snapshot.state is None:
                        raise ExecutionError("allocation snapshot missing")
                    current = snapshot.state
                    if payload is not None and (
                        current.mode is Mode.HOLD
                        or (current.config_revision, current.market_generation)
                        != (context.config_revision, context.market.generation)
                        or current.mode in (Mode.PORTFOLIO_EXIT, Mode.EXIT_BLOCKED)
                        and order.role != "portfolio_exit"
                        or order.side is Side.BUY
                        and (
                            current.mode is not Mode.ACTIVE
                            or current.phase in (Phase.DRAINING, Phase.SELL_REPLACING)
                            or order.cancel_requested
                        )
                    ):
                        raise ExecutionError("current control state blocks the queued placement")
                    self.fault("executor:before_claim")
                    self._budget(scope, context.now_ms)
                    self._ready.pop(scope, None)
                    claim_id = request_id()

                    def claim(
                        ledger: Ledger,
                        order: Order = order,
                        placement: bool = payload is not None,
                        claim_id: str = claim_id,
                        version: int = version,
                        intent_id: str = intent.id,
                    ) -> Outcome:
                        operations_revision = (
                            self.operational_admission(ledger, scope)
                            if placement and self.operational_admission is not None
                            else None
                        )
                        admission = (
                            self.buy_admission(ledger, scope, order, context)
                            if placement
                            and order.side is Side.BUY
                            and self.buy_admission is not None
                            else None
                        )
                        portfolio = (
                            self.portfolio_admission(ledger, scope, order, context)
                            if placement
                            and (order.side is Side.BUY or order.role == "portfolio_exit")
                            and self.portfolio_admission is not None
                            else None
                        )
                        return ledger.attempt_once(
                            claim_id,
                            scope,
                            version,
                            intent_id,
                            admission,
                            portfolio,
                            operations_revision,
                        )

                    await self.writer.submit(claim)
                    self.fault("executor:after_claim")
                    async with asyncio.timeout(self.limits.timeout_ms / 1000):
                        if payload is not None:
                            await self.gateway.submit_limit(payload)
                        else:
                            if external is None:
                                raise ExecutionError("cancel identity missing")
                            await self.gateway.cancel_limit(scope, external)
                    self.fault("executor:after_send")
                    # An ack, including a cancel ack, is not terminal evidence.
                    return True
                return False
            except asyncio.CancelledError:
                self.degrade("send caller canceled; reconcile before any next send")
                raise
            except Exception as exc:
                self._ready.pop(scope, None)
                if isinstance(exc, RateLimited):
                    account = (scope.venue, scope.account)
                    self._next_read[account] = max(
                        self._next_read.get(account, 0),
                        context.now_ms + exc.retry_after_ms,
                    )
                self.last_error = (
                    str(exc)
                    if isinstance(exc, ExecutionError)
                    else "send outcome requires reconciliation"
                )
                return False

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            self._ready.clear()
            if _OWNERS.get(self.writer) is self._owner:
                del _OWNERS[self.writer]
