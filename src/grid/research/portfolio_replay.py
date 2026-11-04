"""OHLC adapter over the real mock ledger/executor/portfolio controller contracts."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from decimal import Decimal
from itertools import groupby
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir

from grid.domain.market import Money
from grid.domain.model import Scope, State
from grid.domain.numbers import ZERO, DomainError, arithmetic, bounded_int
from grid.domain.reducer import reserved
from grid.exchange.models import BookObservation, ReadError
from grid.execution.executor import MockExecutor
from grid.execution.models import Evidence, LimitRequest, Limits
from grid.execution.synthetic import SyntheticExchange
from grid.persistence import codec
from grid.persistence import portfolio as records
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds
from grid.persistence.storage import Fault, no_fault
from grid.persistence.writer import AsyncWriter
from grid.portfolio.controller import PortfolioController
from grid.portfolio.model import Cycle, CyclePhase, Nav, ValuationFacts
from grid.research.artifacts import canonical, digest, output_directory
from grid.research.history import MINUTE, Bar, validate
from grid.research.performance import posted
from grid.research.portfolio_benchmarks import Benchmarks
from grid.research.portfolio_config import Settings
from grid.research.portfolio_risk import MAX_INTERVAL_POINTS, Movement, Risk, transfers
from grid.research.replay import (
    Replay,
    candidates,
    check_precision,
    context_at,
    merged,
    modeled_fee,
)
from grid.universe.model import Facts, MarketKey

# Finite local simulator limits, never production budgets. The settings further bound work/disk.
STORAGE = Bounds(50, 8, 4 * 1024**2, 10000, 256, 8, 5000)
EXECUTION = Limits(64, 1000, 100, 10000, 1000, 60000, 1000, 60000, 1, 1000)
MAX_CYCLES = 32


class Engine:
    def __init__(
        self, spec: Replay, settings: Settings, writer: AsyncWriter, max_cycles: int = 1
    ) -> None:
        self.spec, self.settings, self.writer = spec, settings, writer
        self.max_cycles, self.cycle_number = max_cycles, 1
        self.scopes = tuple(a.scope for a in spec.allocations)
        self.contexts = {a.scope: a.context for a in spec.allocations}
        self.liquidity = {r.scope: r for r in settings.liquidity}
        self.index = self.now = 0
        self.eligible: dict[tuple[Scope, int], int] = {}
        self.cancel_due: dict[str, int] = {}
        self.trace = hashlib.sha256(b"portfolio-ohlc-v1").hexdigest()
        self.events: list[tuple[int, str, object]] = []
        self.ambiguous = 0
        self.fees: dict[str, Decimal] = {}
        self.fee_quote = self.turnover = ZERO
        self.venue = SyntheticExchange(settings.venue_capacity, record=self.record)
        self.executor = MockExecutor(
            writer, self.venue, self.scopes, EXECUTION, Evidence(True, True, True)
        )
        self.owner = PortfolioController(self.executor, lambda: (self.now, self.now * 1000000))
        self.facts: ValuationFacts | None = None
        self.before_actions: Nav | None = None
        self.before_states: tuple[State, ...] = ()
        self.movements: tuple[Movement, ...] = ()

    def record(self, kind: str, payload: object) -> None:
        self.events.append((self.index, kind, payload))
        self.trace = hashlib.sha256(
            bytes.fromhex(self.trace) + canonical((self.index, kind, payload))
        ).hexdigest()
        if kind == "submit" and isinstance(payload, LimitRequest):
            self.eligible[payload.scope, payload.order_id] = self.index + self.spec.latency_bars
        elif kind == "cancel" and isinstance(payload, str):
            self.cancel_due.setdefault(payload, self.index + self.spec.latency_bars)

    def prepare(self, ledger: Ledger) -> None:
        for index, pool in enumerate(self.spec.capital):
            if pool.amount:
                ledger.credit(
                    f"historical-credit-{index}",
                    pool.venue,
                    pool.account,
                    Money(pool.currency, pool.amount),
                )
        for index, allocation in enumerate(self.spec.allocations):
            scope = allocation.scope
            if scope in self.settings.policy.sinks:
                records.register_sink(
                    ledger, f"historical-sink-{index}", scope, self.contexts[scope]
                )
            else:
                funds = (
                    Money(allocation.context.market.quote, allocation.quote_cash),
                    *allocation.fee_balances,
                )
                ledger.start(
                    f"historical-start-{index}",
                    scope,
                    tuple(m for m in funds if m.amount),
                    self.contexts[scope],
                )

    async def states(self) -> tuple[State, ...]:
        def read(ledger: Ledger) -> tuple[State, ...]:
            rows = []
            for scope in self.scopes:
                state = ledger.snapshot(scope).state
                if state is None:
                    raise DomainError("historical allocation disappeared")
                rows.append(state)
            return tuple(rows)

        return await self.writer.submit(read)

    async def cycle(self) -> Cycle:
        value = await self.owner.status()
        if value is None:
            raise DomainError("historical cycle missing")
        return value

    async def observe(self, bars: tuple[Bar, ...]) -> None:
        rows = []
        for allocation, bar in zip(self.spec.allocations, bars, strict=True):
            scope = allocation.scope
            check_precision(bar, allocation.context)
            context = context_at(allocation.context, bar, self.spec.synthetic_spread)
            self.contexts[scope] = context
            book = context.book
            if book is None:
                raise DomainError("synthetic pricing proxy missing")
            depth = self.liquidity[scope].nav_depth
            observed = BookObservation(
                scope.symbol,
                context.market.generation,
                ((book.bid, depth),),
                ((book.ask, depth),),
                self.now,
                self.now,
                self.now * 1000000,
            )
            rows.append(
                Facts(
                    MarketKey.of(scope),
                    context,
                    observed,
                    self.index + 1,
                    self.now,
                    self.now * 1000000,
                    f"assumed-ohlc-{self.index}",
                    True,
                    True,
                    True,
                )
            )
        self.facts = ValuationFacts(
            self.index + 1,
            self.now,
            self.now * 1000000,
            f"synthetic-portfolio-bar-{self.index}",
            True,
            tuple(rows),
            tuple(
                replace(row, observed_ms=self.now, received_ns=self.now * 1000000)
                for row in self.settings.conversions
            ),
            (),
        )
        await self.owner.observe(self.facts)

    async def cross(self, bars: tuple[Bar, ...]) -> None:
        # Boundary cancellations settle before this bar; later ones can still race its fills.
        for external, due in sorted(tuple(self.cancel_due.items())):
            if due <= self.index:
                self.venue.settle_cancel(external)
                del self.cancel_due[external]
        snapshots = await self.states()
        for state, bar in zip(snapshots, bars, strict=True):
            scope = state.scope
            live = {
                fact.request.order_id: external
                for external, fact in self.venue.orders.items()
                if fact.request.scope == scope
                and fact.status == "open"
                and self.eligible[scope, fact.request.order_id] <= self.index
            }
            selected = candidates(
                tuple(o for o in state.working if o.id in live), bar, self.spec.model
            )
            self.ambiguous += int(selected.ambiguous)
            capacity = self.liquidity[scope].fill_per_bar
            context = self.contexts[scope]
            for order in selected.orders:
                fact = self.venue.orders[live[order.id]]
                remaining = codec.parse_amount(fact.request.amount) - fact.cumulative
                quantity = context.market.amount.round(
                    min(order.remaining, remaining, capacity), up=False
                )
                if quantity <= ZERO:
                    continue
                fee = modeled_fee(order.fee_rule, context, order.price, quantity)
                self.venue.fill(
                    live[order.id],
                    f"historical-{self.index}-{len(self.venue.trades)}",
                    quantity,
                    order.price,
                    (fee,),
                )
                capacity -= quantity
                self.turnover += quantity * order.price
                self.fees[fee.currency] = self.fees.get(fee.currency, ZERO) + fee.amount
                self.fee_quote += fee.quote_value or ZERO

    async def service(self, scope: Scope) -> bool:
        ready = await self.executor.service(scope, self.contexts[scope])
        if not ready and not (
            self.executor.last_error in (None, "unresolved cancellation or submission")
            and any(
                (
                    self.venue.orders[e].request.scope.venue,
                    self.venue.orders[e].request.scope.account,
                )
                == (scope.venue, scope.account)
                and due > self.index
                for e, due in self.cancel_due.items()
            )
        ):
            raise DomainError("historical reconciliation failed; no successful report")
        return ready

    async def nav(self) -> Nav | None:
        def value(ledger: Ledger) -> Nav | None:
            if self.facts is None:
                raise DomainError("historical valuation facts missing")
            rows = records.load(ledger, self.settings.policy)
            if any(recovery for _, _, _, recovery in rows.values()):
                return None
            try:
                return records.nav(
                    ledger, rows, self.facts, self.settings.policy, self.now, self.now * 1000000
                )
            except (DomainError, ReadError):
                return None

        return await self.writer.submit(value)

    async def step(self, bars: tuple[Bar, ...]) -> Nav | None:
        self.movements = ()
        for bar, allocation in zip(bars, self.spec.allocations, strict=True):
            check_precision(bar, allocation.context)
        self.now = bars[0].timestamp + MINUTE
        if self.index:
            await self.cross(bars)
        await self.observe(bars)
        if self.index == 0:
            await self.writer.submit(self.prepare)
        for scope in self.scopes:
            await self.service(scope)
        if self.index == 0:
            await self.owner.begin(
                "historical-begin",
                0,
                "historical-cycle",
                self.settings.policy,
                "research",
                "synthetic baseline",
            )
        cycle = await self.cycle()
        if cycle.phase is CyclePhase.COMPLETE and self.cycle_number < self.max_cycles:
            # Only a later bar reaches this path, with fresh observations and reconciliation.
            # Reuse the owner and all funds/IDs; begin refuses unresolved orders or NAV.
            number = self.cycle_number + 1
            await self.owner.begin(
                f"historical-begin-{number}",
                cycle.revision,
                f"historical-cycle-{number}",
                self.settings.policy,
                "research",
                "explicit bounded historical continuation",
                tuple(scope for scope in self.scopes if scope not in self.settings.policy.sinks),
            )
            self.cycle_number = number
            cycle = await self.cycle()
        value = await self.nav()
        self.before_actions = value
        self.before_states = await self.states()
        if cycle.phase is CyclePhase.COMPLETE:
            # Fixed-horizon research keeps valuing retained ownership after the cycle cap.
            # The completion receipt is immutable; fresh marks must use this bar's clock.
            return value
        if cycle.phase is CyclePhase.ACTIVE:
            if (
                value is not None
                and self.now - cycle.last_evaluation_ms >= cycle.policy.evaluation_interval_ms
            ):
                await self.owner.evaluate(
                    f"historical-evaluate-{self.index}",
                    cycle.revision,
                    "research",
                    "synthetic close valuation",
                )
        else:
            request_id = f"historical-advance-{self.index}"
            await self.owner.advance(
                request_id,
                cycle.revision,
                "research",
                "synthetic bounded exit",
            )
            facts = self.facts
            if facts is None:
                raise DomainError("historical transfer evidence missing")
            self.movements = await self.writer.submit(
                lambda ledger: transfers(ledger, request_id, self.settings.policy, facts)
            )
        for scope in self.scopes:
            for _ in range(self.settings.actions_per_bar):
                if not await self.service(scope):
                    break
                context = replace(self.contexts[scope], allow_new_exposure=value is not None)
                sent = await self.executor.dispatch(scope, context)
                if not sent:
                    if self.executor.last_error not in (
                        None,
                        "new exposure is paused",
                        "current control state blocks the queued placement",
                        "NAV threshold latched portfolio exit before BUY claim",
                    ):
                        raise DomainError("historical submission failed; no successful report")
                    break
            await self.service(scope)
        cycle = await self.cycle()
        return cycle.final if cycle.phase is CyclePhase.COMPLETE else await self.nav()

    async def close(self) -> None:
        try:
            await self.owner.close()
        finally:
            await self.executor.close()


async def execute(
    spec: Replay,
    settings: Settings,
    directory: Path,
    fault: Fault,
    max_cycles: int = 1,
    through_end: bool = False,
) -> dict[str, object]:
    manifests = tuple(validate(a.source) for a in spec.allocations)
    if any(
        not m.replay_eligible or (m.first_ms, m.last_ms) != (spec.start_ms, spec.end_ms - MINUTE)
        for m in manifests
    ):
        raise DomainError("portfolio replay requires eligible exact UTC coverage")
    writer = await AsyncWriter.open(
        directory / "mock.db",
        STORAGE,
        frozenset({settings.sqlite_source_id}),
        mock_storage=True,
        fault=fault,
    )
    engine: Engine | None = None
    intervals: list[dict[str, object]] = []
    peak = drawdown = utilization = inventory_time = ZERO
    unknown = simulated = consumed = 0
    complete = False
    value: Nav | None = None
    benchmarks: Benchmarks | None = None
    risk: Risk | None = None
    benchmark_point: dict[str, object] = {}
    cycles: list[Cycle] = []
    held_bars = 0
    try:
        engine = Engine(spec, settings, writer, max_cycles)
        for index, (_, grouped) in enumerate(
            groupby(merged(manifests), key=lambda row: row[1].timestamp)
        ):
            rows = tuple(grouped)
            if tuple(i for i, _ in rows) != tuple(range(len(spec.allocations))):
                raise DomainError("incomplete synchronous portfolio bar")
            consumed += 1
            if complete and not through_end:
                # Still exhaust/validate each immutable source and its final checksum.
                for allocation, (_, bar) in zip(spec.allocations, rows, strict=True):
                    check_precision(bar, allocation.context)
                continue
            held_bars += int(complete)
            engine.index = index
            bars = tuple(bar for _, bar in rows)
            value = await engine.step(bars)
            cycle, states = await engine.cycle(), await engine.states()
            just_completed = cycle.phase is CyclePhase.COMPLETE and (
                not cycles
                or cycles[-1].id != cycle.id
                or cycles[-1].phase is not CyclePhase.COMPLETE
            )
            if not cycles or cycles[-1].id != cycle.id:
                cycles.append(cycle)
            else:
                cycles[-1] = cycle
            initial = cycles[0].baseline
            if risk is None:
                risk = Risk(spec, cycle.baseline)
            risk_before, risk_after = risk.observe(
                engine.before_states,
                engine.before_actions,
                states,
                value,
                engine.movements,
                engine.now,
            )
            if benchmarks is None:
                benchmarks = Benchmarks(spec, settings, bars)
            if engine.facts is None:
                raise DomainError("benchmark facts unavailable")
            benchmark_point = benchmarks.mark(engine.facts, initial.net)
            simulated += 1
            cycle_complete = cycle.phase is CyclePhase.COMPLETE
            complete = cycle_complete and len(cycles) == max_cycles
            peak = max(peak, initial.net)
            if value is None:
                unknown += 1
            # Cancel uncertainty must not erase a known pre-action/trigger valuation.
            for observed in (engine.before_actions, value):
                if observed is not None:
                    peak = max(peak, observed.net)
                    drawdown = max(drawdown, (peak - observed.net) / peak)
            utilization += (
                sum(
                    (
                        s.position_cost + reserved(s, c.context.market.quote)
                        for s, c in zip(states, spec.allocations, strict=True)
                    ),
                    ZERO,
                )
                / initial.net
            )
            inventory_time += Decimal(any(s.owned_base > ZERO for s in states))
            if (index + 1) % spec.interval_bars == 0 or engine.now == spec.end_ms or just_completed:
                intervals.append(
                    {
                        "end_ms_exclusive": engine.now,
                        "partial": (index + 1) % spec.interval_bars != 0,
                        "nav": value,
                        "nav_before_actions": engine.before_actions,
                        "phase": cycle.phase,
                        "benchmarks": benchmark_point,
                        "allocation_risk_before_actions": risk_before,
                        "allocation_risk": risk_after,
                    }
                )
                if max_cycles > 1 or through_end:
                    intervals[-1]["cycle_id"] = cycle.id
            if (
                sum(path.stat().st_size for path in directory.iterdir() if path.is_file())
                > settings.max_database_bytes
            ):
                raise DomainError("historical temporary database byte cap exceeded")
        cycle, states = await engine.cycle(), await engine.states()
        pools = await writer.submit(
            lambda ledger: tuple(
                (key, ledger.pool(key.venue, key.account, key.currency))
                for key in settings.policy.pools
            )
        )
        count = (spec.end_ms - spec.start_ms) // MINUTE
        if consumed != count:
            raise DomainError("historical stream length changed")
        if benchmarks is None or risk is None or not cycles:
            raise DomainError("historical benchmarks missing")
        initial = cycles[0].baseline
        performance = await writer.submit(
            lambda ledger: posted(ledger, states, settings.policy.reporting)
        )
        result: dict[str, object] = {
            "schema": "grid-portfolio-research-v1",
            "config": spec,
            "portfolio_settings": settings,
            "config_sha256": digest((spec, settings)),
            "inputs": manifests,
            "assumptions": (
                "Synthetic OHLC fills only; no observed depth, queue, volume or account evidence.",
                "One side per bar; new orders and cancel finality wait configured bars.",
                "Per-market fill capacity and NAV depth are explicit static assumptions.",
                "FX and fee assumptions are synthetic and refreshed on the replay clock.",
                "NAV is conditional on those assumptions; no guaranteed execution or return.",
                "One consolidated cycle; no automatic new cycle or forced end-of-history exit."
                if max_cycles == 1
                else "Explicit bounded continuation after EXIT_COMPLETE on a later bar; "
                "same funds/ledger, no refill, forced EOF exit or unresolved-cycle restart.",
                "Metrics end at completion or EOF; later bars only validate source integrity."
                if not through_end
                else "Metrics use the fixed history endpoint; after the cycle cap, only "
                "reconcile/value retained ownership with fresh facts, without new orders.",
                "Temporary financial ledger removed on normal return/error; restart from inputs.",
            ),
            "cycle": cycle,
            "simulated_bars": simulated,
            "consumed_source_minutes": consumed,
            "simulation_end_ms_exclusive": engine.now,
            "final_return": cycle.final_return,
            "exit_shortfall": cycle.exit_shortfall,
            "last_nav": value,
            "last_nav_before_actions": engine.before_actions,
            "nav_unknown_bars": unknown,
            "max_drawdown_fraction_observed_nav": drawdown,
            "drawdown_complete": unknown == 0,
            "mean_committed_capital_fraction": utilization / Decimal(simulated),
            "fraction_bars_holding_inventory": inventory_time / Decimal(simulated),
            "fees_by_currency": dict(engine.fees),
            "fees_quote": engine.fee_quote,
            "turnover_quote": engine.turnover,
            "posted_performance": performance,
            "allocation_risk": risk.report(),
            "posted_performance_basis": (
                "Committed unique fills and realized journal deltas only; synthetic venue "
                "fills may still await reconciliation. Gross sums use net realized PnL."
            ),
            "benchmarks": benchmarks.report(initial.net, benchmark_point),
            "ambiguous_bars": engine.ambiguous,
            "final_states": states,
            "final_unallocated_pools": pools,
            "orders": tuple(engine.venue.sent),
            "fills": tuple(engine.venue.trades),
            "cancellations": tuple(engine.venue.cancels),
            "events": tuple(engine.events),
            "intervals": intervals,
            "synthetic_trace_sha256": engine.trace,
        }
        if max_cycles > 1 or through_end:
            del result["cycle"], result["final_return"], result["exit_shortfall"]
            result.update(
                schema="grid-portfolio-series-v1",
                config_sha256=digest((spec, settings, max_cycles)),
                requested_cycles=max_cycles,
                completed_cycles=sum(c.phase is CyclePhase.COMPLETE for c in cycles),
                cycle_outcomes=tuple(cycles),
                last_cycle=cycle,
                last_cycle_final_return=cycle.final_return,
                last_cycle_exit_shortfall=cycle.exit_shortfall,
                initial_nav=initial,
                observed_full_run_return_fraction=value.net / initial.net - 1
                if value is not None
                else None,
                completed_full_run_return_fraction=value.net / initial.net - 1
                if complete and value is not None
                else None,
                stop_reason="cycle_limit_reached"
                if complete and not through_end
                else "history_exhausted",
                series_metric_basis=(
                    "Original funding baseline for returns, drawdown, utilization, allocation "
                    "risk and cash/buy-hold references; realized postings accumulate unchanged. "
                    "Each cycle outcome has its own reconciled baseline/trigger/final NAV. "
                    "Completed full-run return requires all requested cycles to complete."
                ),
            )
        if through_end:
            result.update(
                config_sha256=digest((spec, settings, max_cycles, "history_end")),
                evaluation_horizon="history_end",
                post_completion_valuation_bars=held_bars,
                execution_complete_ms_exclusive=cycle.final.now_ms
                if complete and cycle.final is not None
                else None,
                completed_cycles_return_fraction_at_exit=cycle.final.net / initial.net - 1
                if complete and cycle.final is not None
                else None,
                horizon_metric_basis=(
                    "Window-end NAV includes retained ownership and may be unknown or differ "
                    "from completed exit NAV. Post-completion holdings are never refilled, "
                    "liquidated or reclassified as a new cycle; completion returns remain "
                    "in immutable cycle receipts. Full-run returns are marked, not guaranteed "
                    "realized cash returns."
                ),
            )
        result["result_sha256"] = digest(result)
        if len(canonical(result)) > 32 * 1024**2:
            raise DomainError("portfolio report byte cap exceeded")
        return result
    finally:
        try:
            if engine is not None:
                await engine.close()
        finally:
            await writer.close()


def checked(
    spec: Replay, settings: Settings, max_cycles: int, *, through_end: bool = False
) -> tuple[Replay, Settings]:
    """Normalize and bound a run before any history reads or temporary ledger creation."""
    if type(through_end) is not bool:
        raise DomainError("explicit portfolio horizon flag required")
    bounded_int(max_cycles, 1, MAX_CYCLES)
    spec = replace(
        spec,
        allocations=tuple(sorted(spec.allocations, key=lambda a: codec.dumps(a.scope))),
        capital=tuple(sorted(spec.capital, key=lambda p: (p.venue, p.account, p.currency))),
    )
    settings = settings.checked(spec)
    minutes = (spec.end_ms - spec.start_ms) // MINUTE
    interval_count = min(
        minutes,
        (minutes + spec.interval_bars - 1) // spec.interval_bars
        + max_cycles
        - int(not through_end),
    )
    if 2 * len(spec.allocations) * interval_count > MAX_INTERVAL_POINTS:
        raise DomainError("portfolio allocation risk interval point cap exceeded")
    return spec, settings


@arithmetic
def run(
    spec: Replay,
    settings: Settings,
    *,
    max_cycles: int = 1,
    through_end: bool = False,
    fault: Fault = no_fault,
) -> dict[str, object]:
    spec, settings = checked(spec, settings, max_cycles, through_end=through_end)
    scratch = output_directory(Path(gettempdir()))
    with TemporaryDirectory(prefix="grid-portfolio-replay-", dir=scratch) as temporary:
        return asyncio.run(execute(spec, settings, Path(temporary), fault, max_cycles, through_end))
