"""Research-only fixed-anchor grid: shared funds, isolated minimum-lot round reducers."""

from collections import Counter
from dataclasses import replace
from decimal import Decimal
from itertools import groupby

from grid.domain.market import Money, grid_prices, minimum_buy
from grid.domain.model import Book, Order, Side, Start, State
from grid.domain.numbers import ZERO, DomainError, arithmetic, exact
from grid.domain.reducer import reserved
from grid.persistence import codec
from grid.persistence.ledger import compact
from grid.research.artifacts import canonical, digest
from grid.research.comparison import manifests as result_manifests
from grid.research.history import MINUTE, Bar, identity, validate
from grid.research.performance import realized
from grid.research.replay import (
    Allocation,
    Replay,
    Simulator,
    candidates,
    check_precision,
    context_at,
    merged,
    token_nav,
)
from grid.research.replay import run as run_basket

MAX_POCKETS = 4096
MAX_RESULT_BYTES = 64 * 1024**2
CONVENTION = "fixed-anchor-independent-lots-v1"


class Reference:
    def __init__(self, allocation: Allocation, spec: Replay) -> None:
        self.allocation, self.spec = allocation, spec
        self.context = allocation.context
        self.cash = allocation.quote_cash
        self.tokens = {m.currency: m.amount for m in allocation.fee_balances}
        self.pockets: dict[int, Simulator] = {}
        self.generations: dict[int, int] = {}
        self.anchor: Decimal | None = None
        self.fills = self.sells = self.ambiguous = 0
        self.turnover = self.fee_quote = self.pnl = self.wins = self.losses = ZERO
        self.fees: dict[str, Decimal] = {}
        self.notices: Counter[str] = Counter()
        self.trace = digest(CONVENTION)

    def tokens_owned(self) -> tuple[Money, ...]:
        return tuple(Money(k, v) for k, v in sorted(self.tokens.items()))

    @arithmetic
    def committed(self) -> Decimal:
        return sum(
            (
                s.state.position_cost
                + sum(
                    (
                        o.price * o.remaining + o.acquisition_fee
                        for o in s.state.working
                        if o.side is Side.BUY
                    ),
                    ZERO,
                )
                for s in self.pockets.values()
            ),
            ZERO,
        )

    @arithmetic
    def owned(self) -> Decimal:
        return sum((s.state.owned_base for s in self.pockets.values()), ZERO)

    @arithmetic
    def mark(self, price: Decimal) -> Decimal:
        return (
            self.cash
            + token_nav(self.context, self.tokens_owned())
            + sum((s.mark(price) for s in self.pockets.values()), ZERO)
        )

    @exact
    def admit(self, level: int, price: Decimal, index: int) -> bool:
        context, policy = self.context, self.context.policy
        if not policy.lower_price <= price <= policy.upper_price:
            self.notices["configured price range"] += 1
            return False
        try:
            plan = minimum_buy(
                price,
                context.market,
                context.buy_fee,
                context.sell_fee,
                policy.spacing,
                policy.net_target,
                max(ZERO, self.cash - policy.quote_reserve),
            )
        except DomainError as exc:
            self.notices[str(exc)] += 1
            return False
        if self.committed() + plan.acquisition_cost > policy.max_quote_committed:
            self.notices["committed acquisition cost cap"] += 1
            return False
        escrow: dict[str, Decimal] = {}
        for money in (plan.fee, plan.prospective_sell.fee):
            if money.currency not in (context.market.base, context.market.quote):
                escrow[money.currency] = escrow.get(money.currency, ZERO) + money.amount
        if any(value > self.tokens.get(currency, ZERO) for currency, value in escrow.items()):
            self.notices["prospective lot fee-token funding"] += 1
            return False
        generation = self.generations.get(level, 0) + 1
        scope = replace(
            self.allocation.scope,
            allocation=f"ref-{codec.digest(self.allocation.scope)}-{level}-{generation}",
            strategy="independent-lot-reference",
        )
        # A pocket can own one entry only. The parent keeps the shared risk limits.
        entry = replace(
            context,
            policy=replace(policy, max_lots=1, quote_reserve=ZERO, seed_at="bid"),
            book=Book(
                price,
                price * (1 + self.spec.synthetic_spread),
                context.now_ms,
                context.market.generation,
            ),
        )
        sim = Simulator(
            State(
                scope,
                plan.quote_required,
                tuple(Money(k, v) for k, v in sorted(escrow.items())),
                context.config_revision,
                context.market.generation,
            ),
            entry,
            self.spec.latency_bars,
        )
        sim.event(Start(scope), index)
        if len(sim.state.working) != 1 or sim.state.working[0].side is not Side.BUY:
            raise DomainError("reference minimum-lot admission disagrees with shared reducer")
        order = sim.state.working[0]
        if (order.price, order.amount) != (price, plan.amount):
            raise DomainError("reference changed the executable minimum lot")
        sim.context = replace(sim.context, allow_new_exposure=False)
        self.cash -= plan.quote_required
        for currency, amount in escrow.items():
            self.tokens[currency] -= amount
        self.pockets[level], self.generations[level] = sim, generation
        self.trace = digest((self.trace, index, "admit", level, generation, sim.trace))
        return True

    @exact
    def sweep(self) -> None:
        for level, sim in tuple(self.pockets.items()):
            sim.state = compact(sim.state, sim.context)
            refund = sim.state.quote_cash - reserved(sim.state, self.context.market.quote)
            if refund < ZERO:
                raise DomainError("reference quote reservation exceeds ownership")
            self.cash += refund
            sim.state = replace(sim.state, quote_cash=sim.state.quote_cash - refund)
            if not sim.state.working and sim.state.owned_base == ZERO:
                for money in sim.state.fee_balances:
                    self.tokens[money.currency] = (
                        self.tokens.get(money.currency, ZERO) + money.amount
                    )
                del self.pockets[level]
        if self.cash < ZERO or any(v < ZERO for v in self.tokens.values()):
            raise DomainError("negative reference pool")

    def plan(self, index: int) -> None:
        policy, market = self.context.policy, self.context.market
        if self.anchor is None:
            if not self.pockets and index == 0:
                book = self.context.book
                if book is None:
                    raise DomainError("reference pricing proxy missing")
                price = market.price.round(
                    book.bid if policy.seed_at == "bid" else book.ask, up=False
                )
                self.admit(0, price, index)
            return
        buys = sum(o.side is Side.BUY for s in self.pockets.values() for o in s.state.working)
        orders = sum(len(s.state.working) for s in self.pockets.values())
        previous = self.anchor
        for level in range(policy.max_lots):
            if buys >= policy.ladder_size or orders >= policy.max_open_orders:
                break
            try:
                price = (
                    self.anchor
                    if level == 0
                    else grid_prices(self.anchor, policy.spacing, (level,), market)[0]
                )
                if level and price >= previous:
                    raise DomainError("reference grid collapses after quantization")
            except DomainError as exc:
                self.notices[str(exc)] += 1
                break
            previous = price
            if level in self.pockets:
                continue
            if not self.admit(level, price, index):
                break  # Deterministic shallow-level priority; never pick a winning future path.
            buys += 1
            orders += 1

    @arithmetic
    def step(self, bar: Bar, index: int) -> None:
        check_precision(bar, self.context)
        self.context = context_at(self.context, bar, self.spec.synthetic_spread)
        snapshot: dict[int, tuple[int, Simulator, Order]] = {}
        for level, sim in sorted(self.pockets.items()):
            sim.context = context_at(sim.context, bar, self.spec.synthetic_spread)
            if sim.cancel_due:
                raise DomainError(
                    "independent-lot reference cannot model an unexpected replacement"
                )
            for order in sorted(sim.state.working, key=lambda o: o.id):
                if sim.eligible.get(order.id, index + 1) <= index:
                    snapshot[len(snapshot) + 1] = (level, sim, order)
        selected = candidates(
            tuple(replace(o, id=key) for key, (_, _, o) in snapshot.items()), bar, self.spec.model
        )
        self.ambiguous += int(selected.ambiguous)
        for proxy in selected.orders:
            level, sim, order = snapshot[proxy.id]
            before = sim.state.realized_pnl
            event = sim.fill(order, index)
            if sim.cancel_due:
                raise DomainError(
                    "independent-lot reference cannot model an unexpected replacement"
                )
            self.fills += 1
            self.sells += int(order.side is Side.SELL)
            self.turnover += event.amount * event.price
            for fee in event.fees or ():
                self.fees[fee.currency] = self.fees.get(fee.currency, ZERO) + fee.amount
                self.fee_quote += fee.quote_value or ZERO
            delta = sim.state.realized_pnl - before
            self.pnl += delta
            self.wins += max(ZERO, delta)
            self.losses += max(ZERO, -delta)
            if self.anchor is None:
                if level != 0 or sim.state.anchor is None:
                    raise DomainError("reference anchor requires a terminal seed fill")
                self.anchor = sim.state.anchor
            self.trace = digest((self.trace, index, "fill", level, sim.trace))
        self.sweep()
        if index == 0 or selected.orders:
            self.plan(index)
        policy = self.context.policy
        if (
            len(self.pockets) > policy.max_lots
            or sum(len(s.state.working) for s in self.pockets.values()) > policy.max_open_orders
            or self.committed() > policy.max_quote_committed
            or len(self.notices) > 128
        ):
            raise DomainError("reference shared risk bound exceeded")

    @arithmetic
    def report(self, close: Decimal) -> dict[str, object]:
        return {
            "scope": self.allocation.scope,
            "anchor": self.anchor,
            "free_quote": self.cash,
            "free_fee_tokens": self.tokens_owned(),
            "pockets": tuple(
                {
                    "level": level,
                    "generation": self.generations[level],
                    "state": sim.state,
                    "trace_sha256": sim.trace,
                }
                for level, sim in sorted(self.pockets.items())
            ),
            "fills": self.fills,
            "sell_fills": self.sells,
            "ambiguous_bars": self.ambiguous,
            "realized_pnl": self.pnl,
            "unrealized_pnl_at_close": self.owned() * close
            - sum((s.state.position_cost for s in self.pockets.values()), ZERO),
            "nav_at_close": self.mark(close),
            "turnover_quote": self.turnover,
            "fees_by_currency": self.fees,
            "fees_quote_at_modeled_conversion": self.fee_quote,
            **realized(self.wins, self.losses, self.sells),
            "synthetic_trace_sha256": self.trace,
            "notices": dict(self.notices),
        }


@arithmetic
def run(spec: Replay) -> dict[str, object]:
    spec = replace(
        spec,
        allocations=tuple(sorted(spec.allocations, key=lambda a: codec.dumps(a.scope))),
        capital=tuple(sorted(spec.capital, key=lambda p: (p.venue, p.account, p.currency))),
    )
    if sum(a.context.policy.max_lots for a in spec.allocations) > MAX_POCKETS:
        raise DomainError("independent-lot reference pocket capacity exceeded")
    manifests = tuple(validate(a.source) for a in spec.allocations)
    if any(
        not m.replay_eligible or (m.first_ms, m.last_ms) != (spec.start_ms, spec.end_ms - MINUTE)
        for m in manifests
    ):
        raise DomainError("reference requires eligible exact UTC coverage")
    refs = [Reference(a, spec) for a in spec.allocations]
    initial = sum((r.mark(ZERO) for r in refs), ZERO)
    if initial <= ZERO:
        raise DomainError("positive reference funding required")
    peak = nav = initial
    drawdown = utilization = inventory_time = ZERO
    intervals = []
    count = (spec.end_ms - spec.start_ms) // MINUTE
    last: tuple[Bar, ...] = ()
    for index, (_, group) in enumerate(
        groupby(merged(manifests), key=lambda row: row[1].timestamp)
    ):
        rows = tuple(group)
        if tuple(i for i, _ in rows) != tuple(range(len(refs))):
            raise DomainError("incomplete reference bar")
        last = tuple(bar for _, bar in rows)
        for ref, bar in zip(refs, last, strict=True):
            ref.step(bar, index)
        nav = sum((r.mark(b.close) for r, b in zip(refs, last, strict=True)), ZERO)
        peak = max(peak, nav)
        drawdown = max(drawdown, (peak - nav) / peak)
        utilization += sum((r.committed() for r in refs), ZERO) / initial
        inventory_time += Decimal(any(r.owned() for r in refs))
        if (index + 1) % spec.interval_bars == 0 or index == count - 1:
            intervals.append(
                {
                    "end_ms_exclusive": last[0].timestamp + MINUTE,
                    "partial": (index + 1) % spec.interval_bars != 0,
                    "nav": nav,
                }
            )
    result: dict[str, object] = {
        "schema": "grid-independent-lot-research-v1",
        "convention": CONVENTION,
        "config": spec,
        "config_sha256": digest((spec, CONVENTION)),
        "inputs": manifests,
        "quote": spec.allocations[0].context.market.quote,
        "initial_allocated_nav": initial,
        "final_nav_at_close": nav,
        "net_change_at_close": nav - initial,
        "max_drawdown_fraction_at_bar_close": drawdown,
        "mean_committed_capital_fraction": utilization / Decimal(count),
        "fraction_bars_holding_inventory": inventory_time / Decimal(count),
        "allocations": tuple(r.report(b.close) for r, b in zip(refs, last, strict=True)),
        "intervals": intervals,
        "assumptions": (
            "Research counterfactual only; production retains one aggregate SELL per allocation.",
            "One initial minimum-lot seed; fixed anchor from its terminal weighted fill.",
            "Fixed levels 0..max_lots-1; shallow vacant levels replenish first after fills.",
            "Each lot has its own TP using the shared reducer, fees and minimum-lot math.",
            "Shared allocation cash, quote reserve, committed-cost and open-order caps.",
            "Prospective BUY/SELL fee tokens remain escrowed per lot until it closes.",
            "At most one side per original allocation/bar; new orders wait configured latency.",
            "Full hypothetical fills at limits; no queue, volume or observed execution evidence.",
            "Mark-to-close excludes estimated exit costs; no forced exit or portfolio cycle.",
            "Unallocated capital excluded identically to the ordinary basket replay.",
        ),
    }
    result["result_sha256"] = digest(result)
    if len(canonical(result)) > MAX_RESULT_BYTES:
        raise DomainError("independent-lot report byte cap exceeded")
    return result


@arithmetic
def compare(spec: Replay) -> dict[str, object]:
    # Reject an oversized reference before running either engine.
    if sum(a.context.policy.max_lots for a in spec.allocations) > MAX_POCKETS:
        raise DomainError("independent-lot reference pocket capacity exceeded")
    basket = run_basket(spec)
    reference = run(spec)
    inputs = result_manifests(basket)
    if inputs != result_manifests(reference):
        raise DomainError("history changed between strategy references")
    for manifest in inputs:
        if identity(manifest.source.path.stat(follow_symlinks=False)) != manifest.file_identity:
            raise DomainError("history changed during strategy comparison")
    if basket["initial_allocated_nav"] != reference["initial_allocated_nav"]:
        raise DomainError("strategy reference funding mismatch")
    first, second = basket["final_nav_at_close"], reference["final_nav_at_close"]
    if not isinstance(first, Decimal) or not isinstance(second, Decimal):
        raise DomainError("strategy reference ending NAV missing")
    result: dict[str, object] = {
        "schema": "grid-lot-comparison-v1",
        "inputs": inputs,
        "config": reference["config"],
        "config_sha256": digest((reference["config"], CONVENTION)),
        "initial_allocated_nav": basket["initial_allocated_nav"],
        "end_ms_exclusive": spec.end_ms,
        "valuation_basis": "allocated_mark_to_close_without_exit_costs",
        "cash_baseline_nav": basket["cash_baseline_nav"],
        "buy_hold_nav_at_close": basket["buy_hold_nav_at_close"],
        "reference_minus_basket_nav": second - first,
        "basket_single_sell": basket,
        "independent_lots": reference,
        "interpretation": "Predetermined research conventions on common inputs; no policy ranking, "
        "live selection, portfolio exit or execution guarantee.",
    }
    result["result_sha256"] = digest(result)
    if len(canonical(result)) > MAX_RESULT_BYTES:
        raise DomainError("strategy comparison result byte cap exceeded")
    return result
