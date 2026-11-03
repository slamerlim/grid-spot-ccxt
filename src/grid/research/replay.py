"""Finite-wallet Tier-A replay. Synthetic execution assumptions are never market facts."""

from __future__ import annotations

import hashlib
import heapq
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from decimal import Decimal
from importlib.metadata import version
from typing import Literal

from grid.domain.market import FeeRule, Money
from grid.domain.model import (
    ActualFee,
    Book,
    Cancel,
    Context,
    Event,
    Fill,
    Order,
    OrderReport,
    PlaceLimit,
    Reconcile,
    Scope,
    Side,
    Start,
    State,
)
from grid.domain.numbers import ONE, ZERO, DomainError, arithmetic, bounded_int, decimal
from grid.domain.reducer import reduce, reserved
from grid.persistence import codec
from grid.persistence.ledger import compact
from grid.research.artifacts import canonical, digest
from grid.research.history import (
    MINUTE,
    Bar,
    Manifest,
    Source,
    rounding_radius,
    validate,
    validated_bars,
)

type Model = Literal["strict_skip_ambiguous", "touch_buy_priority"]


@dataclass(frozen=True)
class Allocation:
    scope: Scope
    source: Source
    context: Context
    quote_cash: Decimal
    fee_balances: tuple[Money, ...] = ()


@dataclass(frozen=True)
class Capital:
    venue: str
    account: str
    currency: str
    amount: Decimal


@dataclass(frozen=True)
class Replay:
    allocations: tuple[Allocation, ...]
    capital: tuple[Capital, ...]
    start_ms: int
    end_ms: int
    code_revision: str
    model: Model = "strict_skip_ambiguous"
    latency_bars: int = 1
    interval_bars: int = 60
    synthetic_spread: Decimal = Decimal("0.0001")

    @arithmetic
    def __post_init__(self) -> None:
        bounded_int(len(self.allocations), 1, 64)
        bounded_int(len(self.capital), 1, 192)
        bounded_int(self.latency_bars, 1, 60)
        bounded_int(self.interval_bars, 1, 1000000)
        if self.model not in ("strict_skip_ambiguous", "touch_buy_priority"):
            raise DomainError("unsupported OHLC execution model")
        if not self.code_revision.strip() or len(self.code_revision) > 256:
            raise DomainError("explicit code revision required")
        for timestamp in (self.start_ms, self.end_ms):
            bounded_int(timestamp, MINUTE, 253402300740000)
            if timestamp % MINUTE:
                raise DomainError("exact UTC minute boundaries required")
        count = (self.end_ms - self.start_ms) // MINUTE
        bounded_int(count, 2, 1000000)
        if (count + self.interval_bars - 1) // self.interval_bars > 10000:
            raise DomainError("report interval capacity exceeded")
        decimal(self.synthetic_spread, positive=True)
        if self.synthetic_spread > Decimal("0.1"):
            raise DomainError("unsupported synthetic spread")
        pools: dict[tuple[str, str, str], Decimal] = {}
        for pool in self.capital:
            decimal(pool.amount)
            key = pool.venue, pool.account, pool.currency
            if key in pools:
                raise DomainError("duplicate capital pool")
            pools[key] = pool.amount
        used: dict[tuple[str, str, str], Decimal] = {}
        scopes: set[Scope] = set()
        quotes: set[str] = set()
        for allocation in self.allocations:
            scope, source, context = allocation.scope, allocation.source, allocation.context
            if scope in scopes:
                raise DomainError("duplicate replay scope")
            scopes.add(scope)
            quotes.add(context.market.quote)
            if (source.venue, source.symbol) != (scope.venue, scope.symbol):
                raise DomainError("source/allocation identity mismatch")
            if context.market.symbol != scope.symbol or source.count != count:
                raise DomainError("market or exact replay coverage mismatch")
            if not context.allow_new_exposure:
                raise DomainError("research strategy exposure must be explicit")
            if self.synthetic_spread > context.policy.max_spread:
                raise DomainError("synthetic spread exceeds configured strategy admission")
            currencies = {context.market.quote}
            for money in (
                Money(context.market.quote, allocation.quote_cash),
                *allocation.fee_balances,
            ):
                decimal(money.amount)
                key = scope.venue, scope.account, money.currency
                used[key] = used.get(key, ZERO) + money.amount
            for money in allocation.fee_balances:
                if money.currency in currencies or money.currency == context.market.base:
                    raise DomainError("fee-token balance overlaps or duplicates principal")
                currencies.add(money.currency)
                token_value(context, money.currency)
            for rule in (context.buy_fee, context.sell_fee):
                if rule.currency not in (context.market.base, context.market.quote):
                    token_value(context, rule.currency)
        if len(quotes) != 1:
            raise DomainError("unlike quote currencies require separate replay reports")
        if any(value > pools.get(key, ZERO) for key, value in used.items()):
            raise DomainError("allocations exceed finite account capital")


def token_value(context: Context, currency: str) -> Decimal:
    values = {
        rule.quote_per_token
        for rule in (context.buy_fee, context.sell_fee)
        if rule.currency == currency
    }
    if len(values) != 1 or None in values:
        raise DomainError("explicit consistent research fee-token valuation required")
    value = next(iter(values))
    if value is None:
        raise DomainError("missing token value")
    return value


@arithmetic
def token_nav(context: Context, balances: tuple[Money, ...]) -> Decimal:
    return sum((m.amount * token_value(context, m.currency) for m in balances), ZERO)


@arithmetic
def context_at(context: Context, bar: Bar, spread: Decimal) -> Context:
    # This is a declared pricing proxy for strategy research, NOT observed L1.
    return replace(
        context,
        now_ms=bar.timestamp + MINUTE,
        rules_observed_ms=bar.timestamp + MINUTE,
        fees_observed_ms=bar.timestamp + MINUTE,
        book=Book(
            bar.close, bar.close * (ONE + spread), bar.timestamp + MINUTE, context.market.generation
        ),
    )


@arithmetic
def modeled_fee(rule: FeeRule, context: Context, price: Decimal, quantity: Decimal) -> ActualFee:
    single = replace(rule, max_settlements=1)
    fee = single.estimate(context.market, price, quantity)
    value = single.quote_cost(context.market, fee, price)
    return ActualFee(fee.currency, fee.amount, value)


@dataclass(frozen=True)
class Candidates:
    orders: tuple[Order, ...]
    ambiguous: bool


@arithmetic
def candidates(orders: tuple[Order, ...], bar: Bar, model: Model) -> Candidates:
    low_ceiling = bar.low + rounding_radius(bar, 2)
    high_floor = bar.high - rounding_radius(bar, 1)
    crossed = tuple(
        order
        for order in orders
        if (
            (low_ceiling < order.price if order.side is Side.BUY else high_floor > order.price)
            if model == "strict_skip_ambiguous"
            else (bar.low <= order.price if order.side is Side.BUY else bar.high >= order.price)
        )
    )
    ambiguous = len({order.side for order in crossed}) > 1
    if ambiguous:
        crossed = (
            ()
            if model == "strict_skip_ambiguous"
            else tuple(order for order in crossed if order.side is Side.BUY)
        )
    # Deterministic price priority is another explicit model assumption.
    return Candidates(
        tuple(sorted(crossed, key=lambda o: (-o.price if o.side is Side.BUY else o.price, o.id))),
        ambiguous,
    )


@dataclass
class Simulator:
    """Immediate synthetic acceptance; fills/cancel finality delayed by >=1 bar."""

    state: State
    context: Context
    latency: int
    eligible: dict[int, int] = field(default_factory=dict)
    cancel_due: dict[int, int] = field(default_factory=dict)
    notices: Counter[str] = field(default_factory=Counter)
    fill_count: int = 0
    sell_count: int = 0
    fees_quote: Decimal = ZERO
    fees: dict[str, Decimal] = field(default_factory=dict)
    turnover: Decimal = ZERO
    gross_profit: Decimal = ZERO
    gross_loss: Decimal = ZERO
    ambiguous_bars: int = 0
    trace: str = field(default_factory=lambda: hashlib.sha256(b"synthetic-ohlc-v1").hexdigest())

    def event(self, event: Event, index: int) -> None:
        queue = [event]
        processed = 0
        while queue:
            processed += 1
            if processed > self.context.policy.max_open_orders * 4 + 16:
                raise DomainError("synthetic command cascade bound exceeded")
            current = queue.pop(0)
            transition = reduce(self.state, current, self.context)
            if not transition.accepted or transition.state.reconcile_reason:
                raise DomainError(f"replay needs unsupported reconciliation: {transition.notices}")
            self.state = transition.state
            self.notices.update(transition.notices)
            if len(self.notices) > 128:
                raise DomainError("research notice capacity exceeded")
            self.trace = hashlib.sha256(
                bytes.fromhex(self.trace) + canonical((index, current))
            ).hexdigest()
            for command in transition.commands:
                if isinstance(command, PlaceLimit):
                    self.eligible[command.order.id] = index + self.latency
                    queue.append(
                        OrderReport(self.state.scope, command.order.id, "open", ZERO, False)
                    )
                elif isinstance(command, Cancel):
                    self.cancel_due.setdefault(command.order_id, index + self.latency)
                elif isinstance(command, Reconcile):
                    raise DomainError("OHLC cannot supply reconciliation evidence")
        balances = {
            self.context.market.quote: self.state.quote_cash,
            self.context.market.base: self.state.owned_base,
            **{m.currency: m.amount for m in self.state.fee_balances},
        }
        if any(reserved(self.state, currency) > amount for currency, amount in balances.items()):
            raise DomainError("replay reservation exceeds finite ownership")

    @arithmetic
    def step(self, bar: Bar, index: int, spec: Replay) -> None:
        for field_index, price in enumerate((bar.open, bar.high, bar.low, bar.close)):
            precision = self.context.market.price
            distance = min(
                abs(price - precision.round(price, up=False)),
                abs(price - precision.round(price, up=True)),
            )
            if distance > rounding_radius(bar, field_index):
                raise DomainError("float32 price incompatible with supplied market precision")
        # Boundary cancels use the PREVIOUS completed bar's pricing proxy.
        for order_id, due in sorted(tuple(self.cancel_due.items())):
            if due <= index:
                del self.cancel_due[order_id]
                order = next((o for o in self.state.working if o.id == order_id), None)
                if order is not None:
                    self.event(
                        OrderReport(self.state.scope, order.id, "canceled", order.filled, True),
                        index,
                    )
        snapshot = tuple(
            o for o in self.state.working if self.eligible.get(o.id, index + 1) <= index
        )
        selected = candidates(snapshot, bar, spec.model)
        self.ambiguous_bars += int(selected.ambiguous)
        self.context = context_at(self.context, bar, spec.synthetic_spread)
        if index == 0:
            self.event(Start(self.state.scope), index)
        for order in selected.orders:
            # Every candidate was already active before this bar. No newly emitted
            # SELL/BUY can enter the snapshot. Pending cancellations may still fill.
            fee = modeled_fee(order.fee_rule, self.context, order.price, order.remaining)
            event = Fill(
                self.state.scope,
                f"ohlc:{index}:{order.id}",
                order.id,
                order.remaining,
                order.price,
                (fee,),
            )
            before = self.state.realized_pnl
            self.event(event, index)
            self.fill_count += 1
            self.turnover += event.amount * event.price
            self.fees[fee.currency] = self.fees.get(fee.currency, ZERO) + fee.amount
            self.fees_quote += fee.quote_value or ZERO
            if order.side is Side.SELL:
                self.sell_count += 1
                pnl = self.state.realized_pnl - before
                self.gross_profit += max(ZERO, pnl)
                self.gross_loss += max(ZERO, -pnl)
        self.state = compact(self.state, self.context)
        working = {o.id for o in self.state.working}
        self.eligible = {key: value for key, value in self.eligible.items() if key in working}
        self.cancel_due = {key: value for key, value in self.cancel_due.items() if key in working}

    @arithmetic
    def mark(self, price: Decimal) -> Decimal:
        return (
            self.state.quote_cash
            + self.state.owned_base * price
            + token_nav(self.context, self.state.fee_balances)
        )


@dataclass
class Hold:
    cash: Decimal
    base: Decimal
    fees: tuple[Money, ...]
    entry_fee_quote: Decimal
    reason: str | None

    @arithmetic
    def mark(self, context: Context, price: Decimal) -> Decimal:
        return self.cash + self.base * price + token_nav(context, self.fees)


@arithmetic
def buy_hold(allocation: Allocation, first: Bar, spread: Decimal) -> Hold:
    """All-in benchmark at first completed close + assumed spread; no pre-window data."""
    context = allocation.context
    market, fee_rule = context.market, context.buy_fee
    price = market.price.round(first.close * (ONE + spread), up=True)
    budget = max(ZERO, allocation.quote_cash - context.policy.quote_reserve)
    quantity = market.amount.round(budget / price, up=False)
    if market.buy_amount.maximum is not None:
        quantity = min(quantity, market.amount.round(market.buy_amount.maximum, up=False))
    if market.buy_cost.maximum is not None:
        quantity = min(quantity, market.amount.round(market.buy_cost.maximum / price, up=False))
    # Binary search precision quanta for a funded quantity including rounded fees.
    lower, upper = ZERO, quantity
    chosen = ZERO
    for _ in range(1024):
        fee = modeled_fee(fee_rule, context, price, quantity)
        needed = quantity * price + (fee.amount if fee.currency == market.quote else ZERO)
        token_funds = next(
            (m.amount for m in allocation.fee_balances if m.currency == fee.currency), ZERO
        )
        funded = needed <= budget and (
            fee.currency in (market.base, market.quote) or fee.amount <= token_funds
        )
        if funded:
            chosen = quantity
            lower = quantity
        else:
            upper = quantity
        midpoint = market.amount.round((lower + upper) / Decimal(2), up=False)
        if midpoint <= lower or midpoint >= upper:
            break
        quantity = midpoint
    else:
        raise DomainError("baseline funding search exceeded bound")
    if chosen <= ZERO or not market.valid(price, chosen, buy=True):
        return Hold(allocation.quote_cash, ZERO, allocation.fee_balances, ZERO, "minimum_or_budget")
    fee = modeled_fee(fee_rule, context, price, chosen)
    base = chosen - (fee.amount if fee.currency == market.base else ZERO)
    if base <= ZERO:
        return Hold(
            allocation.quote_cash, ZERO, allocation.fee_balances, ZERO, "base_fee_consumes_lot"
        )
    cash = (
        allocation.quote_cash
        - chosen * price
        - (fee.amount if fee.currency == market.quote else ZERO)
    )
    fees = tuple(
        Money(m.currency, m.amount - (fee.amount if m.currency == fee.currency else ZERO))
        for m in allocation.fee_balances
    )
    return Hold(cash, base, fees, fee.quote_value or ZERO, None)


def merged(manifests: tuple[Manifest, ...]) -> Iterator[tuple[int, Bar]]:
    """One cursor per market; manifest order is canonical scope order."""
    streams = [iter(validated_bars(m)) for m in manifests]
    heap: list[tuple[int, int, Bar]] = []
    for index, stream in enumerate(streams):
        bar = next(stream)
        heapq.heappush(heap, (bar.timestamp, index, bar))
    while heap:
        _, index, bar = heapq.heappop(heap)
        yield index, bar
        following = next(streams[index], None)
        if following is not None:
            heapq.heappush(heap, (following.timestamp, index, following))


@arithmetic
def run(spec: Replay) -> dict[str, object]:
    allocations = tuple(sorted(spec.allocations, key=lambda a: codec.dumps(a.scope)))
    manifests = tuple(validate(a.source) for a in allocations)
    for manifest in manifests:
        if not manifest.replay_eligible:
            raise DomainError(f"history is not replay eligible: {manifest.blockers}")
        if (manifest.first_ms, manifest.last_ms) != (spec.start_ms, spec.end_ms - MINUTE):
            raise DomainError("exact UTC coverage does not match requested interval")
    simulators = [
        Simulator(
            State(
                a.scope,
                a.quote_cash,
                a.fee_balances,
                a.context.config_revision,
                a.context.market.generation,
            ),
            a.context,
            spec.latency_bars,
        )
        for a in allocations
    ]
    initial = sum((a.quote_cash + token_nav(a.context, a.fee_balances) for a in allocations), ZERO)
    if initial <= ZERO:
        raise DomainError("positive allocated research capital required")
    holds: dict[int, Hold] = {}
    last: dict[int, Bar] = {}
    intervals: list[dict[str, object]] = []
    peak = initial
    drawdown = utilization = inventory_time = ZERO
    nav = hold_nav = initial
    trace = hashlib.sha256(b"chronological-ohlc-v1")
    count = (spec.end_ms - spec.start_ms) // MINUTE
    for allocation_index, bar in merged(manifests):
        index = (bar.timestamp - spec.start_ms) // MINUTE
        simulator = simulators[allocation_index]
        if index == 0:
            holds[allocation_index] = buy_hold(
                allocations[allocation_index], bar, spec.synthetic_spread
            )
        simulator.step(bar, index, spec)
        last[allocation_index] = bar
        trace.update(
            canonical((bar.timestamp, allocations[allocation_index].scope, simulator.trace))
        )
        if allocation_index == len(simulators) - 1:
            nav = sum((sim.mark(last[i].close) for i, sim in enumerate(simulators)), ZERO)
            hold_nav = sum(
                (hold.mark(allocations[i].context, last[i].close) for i, hold in holds.items()),
                ZERO,
            )
            peak = max(peak, nav)
            drawdown = max(drawdown, (peak - nav) / peak)
            committed = sum(
                (
                    sim.state.position_cost + reserved(sim.state, sim.context.market.quote)
                    for sim in simulators
                ),
                ZERO,
            )
            utilization += committed / initial
            inventory_time += Decimal(any(sim.state.owned_base > ZERO for sim in simulators))
            if (index + 1) % spec.interval_bars == 0 or index == count - 1:
                intervals.append(
                    {
                        "end_ms_exclusive": bar.timestamp + MINUTE,
                        "partial": (index + 1) % spec.interval_bars != 0,
                        "nav": nav,
                        "cash_baseline_nav": initial,
                        "buy_hold_nav": hold_nav,
                    }
                )
    reports: list[dict[str, object]] = []
    for i, sim in enumerate(simulators):
        state, context, price = sim.state, sim.context, last[i].close
        liquidation_amount = context.market.amount.round(state.owned_base, up=False)
        # Mark-to-close is NOT an executable exit. Report untradeable remainder separately.
        sellable = liquidation_amount > ZERO and context.market.valid(
            context.market.price.round(price, up=False), liquidation_amount, buy=False
        )
        modeled_exit_fee = modeled_fee(context.sell_fee, context, price, liquidation_amount)
        if modeled_exit_fee.currency == context.market.base:
            sellable = sellable and liquidation_amount + modeled_exit_fee.amount <= state.owned_base
        reports.append(
            {
                "scope": state.scope,
                "final_state": state,
                "state_sha256": codec.digest(state),
                "synthetic_trace_sha256": sim.trace,
                "fills": sim.fill_count,
                "sell_fills": sim.sell_count,
                "ambiguous_bars": sim.ambiguous_bars,
                "fees_by_currency": sim.fees,
                "fees_quote_at_modeled_conversion": sim.fees_quote,
                "turnover_quote": sim.turnover,
                "realized_pnl": state.realized_pnl,
                "unrealized_pnl_at_close": state.owned_base * price - state.position_cost,
                "nav_at_close": sim.mark(price),
                "unfinished_orders": state.working,
                "dust_or_untradeable_base": state.owned_base - liquidation_amount
                if sellable
                else state.owned_base,
                "dust_note": "Conservative at-close amount/minimum/fee screen; not exit execution.",
                "profit_factor": sim.gross_profit / sim.gross_loss
                if sim.sell_count >= 2 and sim.gross_loss > ZERO
                else None,
                "profit_factor_note": "undefined without >=2 SELL fills and positive gross losses",
                "buy_hold": holds[i],
                "notices": dict(sim.notices),
            }
        )
    result: dict[str, object] = {
        "schema": "grid-research-v1",
        "model": spec.model,
        "assumptions": (
            "Synthetic fills only; no volume, queue, L2, actual spread or execution evidence.",
            "Full fills at LIMIT after strict crossing or alternative touch; no improvement.",
            "Orders and cancel finality wait latency_bars after their creation bar.",
            "At most one side fills per bar; ambiguous strict-model bars do not fill.",
            "Mark-to-close NAV excludes exit fees/slippage; inventory is NOT liquidated.",
            "Fees, static constraints and close-based spread are supplied research assumptions.",
            "Alternative models are sensitivity scenarios, not mathematical PnL bounds.",
        ),
        "config": replace(
            spec,
            allocations=allocations,
            capital=tuple(sorted(spec.capital, key=lambda p: (p.venue, p.account, p.currency))),
        ),
        "config_sha256": digest(
            replace(
                spec,
                allocations=allocations,
                capital=tuple(sorted(spec.capital, key=lambda p: (p.venue, p.account, p.currency))),
            )
        ),
        "ccxt_version": version("ccxt"),
        "inputs": manifests,
        "quote": allocations[0].context.market.quote,
        "initial_allocated_nav": initial,
        "final_nav_at_close": nav,
        "net_change_at_close": nav - initial,
        "cash_baseline_nav": initial,
        "buy_hold_nav_at_close": hold_nav,
        "max_drawdown_fraction_at_bar_close": drawdown,
        "mean_committed_capital_fraction": utilization / Decimal(count),
        "fraction_bars_holding_inventory": inventory_time / Decimal(count),
        "chronology_sha256": trace.hexdigest(),
        "allocations": reports,
        "intervals": intervals,
        "unallocated_capital": "Excluded from allocation-matched comparisons; no transfers.",
    }
    result["result_sha256"] = digest(result)
    return result
