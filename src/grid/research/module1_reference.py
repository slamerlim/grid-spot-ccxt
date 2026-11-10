"""Small offline oracle for the Module 1 C++ research contract.

Financial observations come from the existing pure reducer and market functions.
The bounded bar runner uses the existing Simulator, without changing admission
in the historical replay commands. Caller-provided bars carry no provenance proof.
"""

from __future__ import annotations

import argparse
import json

# Explicitly invoked local C++ research oracle, with no shell.
import subprocess  # nosec B404
import sys
from collections.abc import Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from decimal import Decimal as D
from pathlib import Path
from typing import Literal, cast

from grid.domain.market import (
    FeeRule,
    Limits,
    Market,
    Money,
    Precision,
    PrecisionMode,
    aggregate_take_profit,
    grid_prices,
    minimum_buy,
)
from grid.domain.model import (
    ActualFee,
    Book,
    CancelAcknowledged,
    Context,
    Event,
    Fill,
    Order,
    OrderReport,
    Policy,
    Scope,
    Side,
    Start,
    State,
)
from grid.domain.numbers import ZERO, DomainError, arithmetic, bounded_int, decimal, exact
from grid.domain.reducer import available_quote, reduce, reserved
from grid.research.artifacts import canonical, digest
from grid.research.history import MINUTE, RECORD, Bar, Source, decode
from grid.research.replay import (
    Allocation,
    Capital,
    Model,
    Replay,
    Simulator,
    modeled_fee,
)

SCHEMA = "grid-module1-python-reference-v1"
MAX_BARS = 10000


def fixture_context() -> Context:
    """Invented constraints, matching the established handwritten domain fixtures."""
    market = Market(
        "BASE/QUOTE",
        "BASE",
        "QUOTE",
        1,
        Precision(PrecisionMode.TICK, tick=D("0.01")),
        Precision(PrecisionMode.TICK, tick=D("0.001")),
        Limits(D("0.01"), D("1000000")),
        Limits(D("1"), D("10000")),
        Limits(ZERO, None),
        Limits(D("0.1"), D("10000")),
        Limits(ZERO, None),
        True,
        False,
    )
    policy = Policy(
        spacing=D("0.1"),
        net_target=D("0.01"),
        quote_reserve=D("10"),
        max_quote_committed=D("900"),
        lower_price=D("1"),
        upper_price=D("1000"),
        cost_quantum=D("0.00000001"),
        max_spread=D("0.01"),
        ladder_size=3,
        max_lots=10,
        max_open_orders=8,
        max_order_records=1000,
        max_fill_records=1000,
        max_replacements=10,
        max_source_age_ms=1000,
        seed_at="bid",
    )
    fee = FeeRule("QUOTE", ZERO, D("0.0001"), None, 64)
    return Context(
        market,
        policy,
        fee,
        fee,
        1,
        10000,
        10000,
        10000,
        Book(D("100"), D("100.01"), 10000, 1),
        True,
    )


def fixture_state(*, cash: D = D("1000")) -> State:
    return State(
        Scope("reference", "allocated-account", "BASE/QUOTE", "allocation", "grid", "cycle"),
        cash,
        (Money("TOKEN", D("10")),),
        1,
        1,
    )


def observation(state: State, context: Context) -> dict[str, object]:
    """Complete state plus independently inspectable financial/reservation fields."""
    currencies = (
        context.market.base,
        context.market.quote,
        *(m.currency for m in state.fee_balances),
    )
    return {
        "state": state,
        "owned_base": state.owned_base,
        "available_quote": available_quote(state, context),
        "reserved": {currency: reserved(state, currency) for currency in currencies},
        "working": tuple(
            {"order": order, "remaining": order.remaining, "terminal": order.terminal}
            for order in state.working
        ),
    }


class Recorder:
    """Record actual reducer transitions, including rejected or held transitions."""

    def __init__(self, context: Context, state: State | None = None) -> None:
        self.context = context
        self.initial = fixture_state() if state is None else state
        self.state = self.initial
        self.steps: list[dict[str, object]] = []

    def apply(self, event: Event) -> None:
        transition = reduce(self.state, event, self.context)
        self.steps.append(
            {
                "event_type": type(event).__name__,
                "event": event,
                "before": observation(self.state, self.context),
                "accepted": transition.accepted,
                "commands": tuple(
                    {"type": type(command).__name__, "value": command}
                    for command in transition.commands
                ),
                "notices": transition.notices,
                "after": observation(transition.state, self.context),
            }
        )
        self.state = transition.state

    def order(self, side: Side, *, role: str | None = None) -> Order:
        return next(
            order
            for order in self.state.working
            if order.side is side and (role is None or order.role == role)
        )

    def acknowledge(self) -> None:
        for order in self.state.working:
            self.apply(OrderReport(self.state.scope, order.id, "open", order.filled, False))

    def fill(
        self,
        order: Order,
        identity: str,
        amount: D | None = None,
        price: D | None = None,
        fees: tuple[ActualFee, ...] | None = (),
    ) -> None:
        self.apply(
            Fill(
                self.state.scope,
                identity,
                order.id,
                order.remaining if amount is None else amount,
                order.price if price is None else price,
                fees,
            )
        )

    def cancel(self, order: Order, cumulative: D | None = None) -> None:
        self.apply(
            OrderReport(
                self.state.scope,
                order.id,
                "canceled",
                order.filled if cumulative is None else cumulative,
                True,
            )
        )

    def result(self, name: str) -> dict[str, object]:
        return {
            "name": name,
            "context": self.context,
            "initial_state": self.initial,
            "steps": self.steps,
            "final": observation(self.state, self.context),
        }


def lifecycle_cases() -> tuple[dict[str, object], ...]:
    context = fixture_context()
    partial = Recorder(context)
    partial.apply(Start(partial.state.scope))
    seed = partial.order(Side.BUY)
    partial.fill(seed, "small", D("0.05"), D("98"))
    partial.fill(seed, "partial", D("0.35"), D("99"))
    partial.acknowledge()
    partial.fill(seed, "rest", D("0.6"), D("100"))
    partial.cancel(partial.order(Side.SELL))

    race = Recorder(context)
    race.apply(Start(race.state.scope))
    race.fill(race.order(Side.BUY), "seed")
    race.acknowledge()
    old_sell, lower = race.order(Side.SELL), race.order(Side.BUY)
    race.fill(lower, "lower")
    race.apply(CancelAcknowledged(race.state.scope, old_sell.id))
    race.fill(old_sell, "partial-sell", D("0.4"))
    race.cancel(old_sell, D("0.4"))

    drain = Recorder(context)
    drain.apply(Start(drain.state.scope))
    drain.fill(drain.order(Side.BUY), "seed")
    drain.acknowledge()
    buys = tuple(order for order in drain.state.working if order.side is Side.BUY)
    drain.fill(drain.order(Side.SELL), "sold")
    drain.fill(buys[0], "late", D("0.4"))
    for index, buy in enumerate(buys):
        drain.cancel(buy, D("0.4") if index == 0 else ZERO)

    rows = [
        partial.result("partial_seed"),
        race.result("cancel_fill_race"),
        drain.result("drain_late_buy"),
    ]
    for currency in ("BASE", "QUOTE", "TOKEN"):
        fee = FeeRule(currency, D("0.001"), D("0.0001"), D("2") if currency == "TOKEN" else None, 4)
        charged_context = replace(context, buy_fee=fee, sell_fee=fee)
        charged = Recorder(charged_context)
        charged.apply(Start(charged.state.scope))
        seed = charged.order(Side.BUY)
        actual = modeled_fee(fee, charged_context, seed.price, D("0.4"))
        charged.fill(seed, "fee-partial", D("0.4"), fees=(actual,))
        charged.acknowledge()
        actual = modeled_fee(fee, charged_context, seed.price, D("0.6"))
        charged.fill(seed, "fee-rest", D("0.6"), fees=(actual,))
        rows.append(charged.result(f"partial_fee_{currency.lower()}"))

        # Continue from the actual accumulated state: settle its stale partial
        # SELL, then execute the new aggregate SELL with its own settlement fee.
        sold = Recorder(charged_context, charged.state)
        sold.cancel(sold.order(Side.SELL))
        sold.acknowledge()
        sell = sold.order(Side.SELL)
        actual = modeled_fee(fee, charged_context, sell.price, sell.remaining)
        sold.fill(sell, "fee-sold", fees=(actual,))
        for buy in tuple(order for order in sold.state.working if order.side is Side.BUY):
            sold.cancel(buy)
        rows.append(sold.result(f"sell_fee_{currency.lower()}"))

    breach = Recorder(context)
    breach.apply(Start(breach.state.scope))
    breach.fill(breach.order(Side.BUY), "fee-overrun", fees=(ActualFee("QUOTE", D("1"), D("1")),))
    rows.append(breach.result("actual_fee_overrun_posts_then_halts"))
    return tuple(rows)


def math_cases() -> tuple[dict[str, object], ...]:
    context = fixture_context()
    rows: list[dict[str, object]] = []
    for anchor, spacing, indices in (
        (D("100"), D("0.1"), (1, 2, 3)),
        (D("0.02"), D("0.01"), (1, 2, 3)),
    ):
        arguments = {
            "anchor": anchor,
            "spacing": spacing,
            "indices": indices,
            "market": context.market,
        }
        row: dict[str, object] = {"name": f"grid-{anchor}", "operation": "grid", "input": arguments}
        try:
            row["expected"] = grid_prices(anchor, spacing, indices, context.market)
        except DomainError as exc:
            row["error"] = str(exc)
        rows.append(row)

    for currency in ("BASE", "QUOTE", "TOKEN"):
        fee = FeeRule(currency, D("0.001"), D("0.0001"), D("2") if currency == "TOKEN" else None, 4)
        for price in (D("100"), D("9.09")):
            market = replace(
                context.market,
                buy_amount=Limits(D("0.001"), D("10000")),
                buy_cost=Limits(D("10"), None),
                sell_amount=Limits(D("0.001"), D("10000")),
            )
            rows.append(
                {
                    "name": f"minimum-{currency.lower()}-{price}",
                    "operation": "buy",
                    "input": {
                        "price": price,
                        "market": market,
                        "buy_fee": fee,
                        "sell_fee": fee,
                        "spacing": D("0.1"),
                        "net_target": D("0.01"),
                        "available_quote": D("1000"),
                    },
                    "expected": minimum_buy(
                        price, market, fee, fee, D("0.1"), D("0.01"), D("1000")
                    ),
                }
            )
        rows.append(
            {
                "name": f"aggregate-{currency.lower()}",
                "operation": "take_profit",
                "input": {
                    "owned": D("1.6"),
                    "cost": D("152.72"),
                    "market": context.market,
                    "spacing": D("0.1"),
                    "net_target": D("0.01"),
                    "fee_rule": fee,
                },
                "expected": aggregate_take_profit(
                    D("1.6"), D("152.72"), context.market, D("0.1"), D("0.01"), fee
                ),
            }
        )
    # These exact geometric thresholds expose Decimal96 intermediate rounding.
    # The oracle intentionally preserves the existing Python planner's extra tick.
    boundary_fee = FeeRule("QUOTE", D("0.001"), D("0.0001"), None, 64)
    for owned, cost, spacing_text in (("103", "38", ".03"), ("3", "5", "2")):
        rows.append(
            {
                "name": f"aggregate-decimal96-boundary-{owned}-{cost}",
                "operation": "take_profit",
                "input": {
                    "owned": D(owned),
                    "cost": D(cost),
                    "market": context.market,
                    "spacing": D(spacing_text),
                    "net_target": D("0.01"),
                    "fee_rule": boundary_fee,
                },
                "expected": aggregate_take_profit(
                    D(owned), D(cost), context.market, D(spacing_text), D("0.01"), boundary_fee
                ),
            }
        )
    unsellable = replace(context.market, sell_amount=Limits(D("1"), D("10000")))
    fee = FeeRule("BASE", D("0.001"), D("0.0001"), None, 1)
    row = {
        "name": "minimum-never-enlarged-for-sell",
        "operation": "buy",
        "input": {
            "price": D("100"),
            "market": unsellable,
            "buy_fee": fee,
            "sell_fee": context.sell_fee,
            "spacing": D("0.1"),
            "net_target": D("0.01"),
            "available_quote": D("1000"),
        },
    }
    try:
        minimum_buy(D("100"), unsellable, fee, context.sell_fee, D("0.1"), D("0.01"), D("1000"))
    except DomainError as exc:
        row["error"] = str(exc)
    else:
        raise DomainError("minimum-lot oracle unexpectedly admitted unsellable quantity")
    rows.append(row)
    return tuple(rows)


@arithmetic
def run_bars(
    context: Context,
    bars: Sequence[Bar],
    *,
    quote_cash: D = D("1000"),
    fee_balances: tuple[Money, ...] = (),
    model: Model = "strict_skip_ambiguous",
    latency_bars: int = 1,
    synthetic_spread: D = D("0.0001"),
    trace: bool = True,
) -> dict[str, object]:
    """Bounded baseline parity/performance oracle; no data provenance is asserted.

    All bars must be caller-supplied and consecutive. No source file is opened.
    This does not admit unknown archival data into the strict historical runner.
    """
    bounded_int(len(bars), 2, MAX_BARS)
    if type(trace) is not bool:
        raise DomainError("explicit trace switch required")
    start = bars[0].timestamp
    if any(bar.timestamp != start + index * MINUTE for index, bar in enumerate(bars)):
        raise DomainError("reference requires consecutive caller-provided bars")
    scope = Scope(
        "reference", "allocated-account", context.market.symbol, "allocation", "grid", "cycle"
    )
    source = Source(
        Path("caller-provided-bars-not-a-source-file"), scope.venue, scope.symbol, 0, len(bars)
    )
    allocation = Allocation(scope, source, context, quote_cash, fee_balances)
    capital = tuple(
        Capital(scope.venue, scope.account, m.currency, m.amount)
        for m in (Money(context.market.quote, quote_cash), *fee_balances)
    )
    spec = Replay(
        (allocation,),
        capital,
        start,
        start + len(bars) * MINUTE,
        "python-existing-reducer-reference",
        model,
        latency_bars,
        MAX_BARS,
        synthetic_spread,
    )
    simulator = Simulator(
        State(scope, quote_cash, fee_balances, context.config_revision, context.market.generation),
        context,
        latency_bars,
    )
    observations = []
    for index, bar in enumerate(bars):
        simulator.step(bar, index, spec)
        if trace:
            observations.append(
                {"bar": bar, "after": observation(simulator.state, simulator.context)}
            )
    result: dict[str, object] = {
        "schema": SCHEMA,
        "kind": "bounded-existing-simulator",
        "model": model,
        "provenance": (
            "Caller-provided bars; no source identity or historical-performance certification."
        ),
        "bars": len(bars),
        "initial_quote": quote_cash,
        "final": observation(simulator.state, simulator.context),
        "nav_at_close_excluding_exit_cost": simulator.mark(bars[-1].close),
        "fills": simulator.fill_count,
        "sell_fills": simulator.sell_count,
        "fees_quote": simulator.fees_quote,
        "fees_by_currency": simulator.fees,
        "turnover_quote": simulator.turnover,
        "ambiguous_bars": simulator.ambiguous_bars,
        "synthetic_trace_sha256": simulator.trace,
        "observations": observations,
    }
    result["result_sha256"] = digest(result)
    return result


def golden() -> dict[str, object]:
    result: dict[str, object] = {
        "schema": SCHEMA,
        "kind": "synthetic-exact-golden-observations",
        "oracle": (
            "grid.domain.reducer.reduce",
            "grid.domain.market",
            "grid.research.replay.Simulator",
        ),
        "financial_encoding": (
            "Exact decimal strings; no float money. Domain arithmetic precision 96."
        ),
        "math": math_cases(),
        "lifecycle": lifecycle_cases(),
        "research_policies": policy_vectors(),
        "decoder_extensions": decoder_extensions(),
    }
    result["result_sha256"] = digest(result)
    return result


@arithmetic
def research_price(
    kind: Literal["no_seed_lower", "ladder_one", "reentry_one_step_below_sell"],
    market: Market,
    spacing: D,
    fresh_bid: D,
    *,
    anchor: D | None = None,
    next_index: int = 1,
    last_sell: D | None = None,
) -> D:
    """Only the explicitly declared research pricing changes; not a second reducer."""
    decimal(spacing, positive=True)
    decimal(fresh_bid, positive=True)
    if kind == "no_seed_lower":
        raw = fresh_bid / (1 + spacing)
    elif kind == "ladder_one":
        if anchor is None:
            raise DomainError("single-lower policy requires executed anchor")
        anchored = grid_prices(anchor, spacing, (next_index,), market)[0]
        raw = min(anchored, fresh_bid / (1 + spacing))
    elif kind == "reentry_one_step_below_sell":
        if last_sell is None:
            raise DomainError("one-step reentry requires last executed SELL price")
        decimal(last_sell, positive=True)
        raw = min(last_sell / (1 + spacing), fresh_bid)
    else:
        raise DomainError("unknown research pricing policy")
    price = market.price.round(raw, up=False)
    if price <= 0 or not market.prices.admits(price):
        raise DomainError("research price outside market range")
    return price


@dataclass(frozen=True)
class ResearchNav:
    """One quote-currency observation; cash ALREADY includes reserved cash."""

    allocation: str
    exchange: str
    quote_cash: D
    reserved_quote: D
    marked_base: D | None
    exit_cost: D | None
    baseline: D

    @exact
    def net(self) -> D | None:
        decimal(self.quote_cash)
        decimal(self.reserved_quote)
        decimal(self.baseline, positive=True)
        if self.reserved_quote > self.quote_cash:
            raise DomainError("reserved quote exceeds owned quote")
        if self.marked_base is None or self.exit_cost is None:
            return None
        decimal(self.marked_base)
        decimal(self.exit_cost)
        return self.quote_cash + self.marked_base - self.exit_cost


@exact
def tier_triggers(
    rows: tuple[ResearchNav, ...],
    unallocated_quote: dict[str, D],
    exchange_baselines: dict[str, D],
    global_baseline: D,
    *,
    symbol_threshold: D | None,
    exchange_threshold: D | None,
) -> tuple[tuple[str, tuple[str, ...]], ...] | None:
    """Research-only precedence decision, no liquidation/restart/authority effects.

    None means incomplete NAV, never an achieved threshold. Optional tier
    switches cannot disable the mandatory global +10% test.
    """
    bounded_int(len(rows), 1, 64)
    if len({row.allocation for row in rows}) != len(rows):
        raise DomainError("duplicate allocation NAV")
    venues = {row.exchange for row in rows}
    if set(unallocated_quote) != venues or set(exchange_baselines) != venues:
        raise DomainError("NAV and baseline must cover the exact exchange roster")
    decimal(global_baseline, positive=True)
    for threshold in (symbol_threshold, exchange_threshold):
        if threshold is not None:
            decimal(threshold, positive=True)
    for amount in unallocated_quote.values():
        decimal(amount)
    for amount in exchange_baselines.values():
        decimal(amount, positive=True)
    if sum(exchange_baselines.values(), ZERO) != global_baseline:
        raise DomainError("global baseline differs from exchange baselines")
    values = {row.allocation: row.net() for row in rows}
    if any(value is None for value in values.values()):
        return None
    known = {key: cast(D, value) for key, value in values.items()}
    if sum(known.values(), ZERO) + sum(unallocated_quote.values(), ZERO) >= global_baseline * D(
        "1.10"
    ):
        return (("global", tuple(sorted(known))),)
    chosen: list[tuple[str, tuple[str, ...]]] = []
    covered: set[str] = set()
    if exchange_threshold is not None:
        for venue in sorted(venues):
            members = tuple(sorted(row.allocation for row in rows if row.exchange == venue))
            nav = unallocated_quote[venue] + sum((known[key] for key in members), ZERO)
            if nav >= exchange_baselines[venue] * (1 + exchange_threshold):
                chosen.append((f"exchange:{venue}", members))
                covered.update(members)
    if symbol_threshold is not None:
        for row in sorted(rows, key=lambda row: row.allocation):
            if row.allocation not in covered and known[row.allocation] >= row.baseline * (
                1 + symbol_threshold
            ):
                chosen.append((f"symbol:{row.allocation}", (row.allocation,)))
    return tuple(chosen)


def policy_vectors() -> dict[str, object]:
    market = fixture_context().market
    prices: list[dict[str, object]] = []
    for kind, fresh, anchor, index, sell in (
        ("no_seed_lower", D("100"), None, 1, None),
        ("ladder_one", D("50"), D("100"), 2, None),
        ("ladder_one", D("150"), D("100"), 2, None),
        ("reentry_one_step_below_sell", D("120"), None, 1, D("110")),
        ("reentry_one_step_below_sell", D("90"), None, 1, D("110")),
    ):
        policy = cast(Literal["no_seed_lower", "ladder_one", "reentry_one_step_below_sell"], kind)
        prices.append(
            {
                "policy": kind,
                "fresh_bid": fresh,
                "anchor": anchor,
                "next_index": index,
                "last_sell": sell,
                "spacing": D("0.1"),
                "market": market,
                "created_bar": 7,
                "eligible_from_bar": 8,
                "price": research_price(
                    policy, market, D("0.1"), fresh, anchor=anchor, next_index=index, last_sell=sell
                ),
            }
        )
    rows = (
        ResearchNav("A1", "A", D("15"), D("5"), D("11"), D("1"), D("20")),
        ResearchNav("A2", "A", D("15"), D("5"), D("11"), D("1"), D("20")),
        ResearchNav("B1", "B", D("15"), D("5"), D("11"), D("1"), D("20")),
        ResearchNav("B2", "B", D("6"), D("1"), D("5"), D("1"), D("20")),
    )
    cash, baselines = {"A": D("10"), "B": D("10")}, {"A": D("50"), "B": D("50")}
    tiers = []
    for name, observations, symbol, exchange in (
        ("independent-tiers", rows, D("0.1"), D("0.1")),
        ("symbol-only", rows, D("0.1"), None),
        ("exchange-only", rows, None, D("0.1")),
        ("both-optional-off", rows, None, None),
        (
            "global-at-threshold",
            (*rows[:3], replace(rows[3], marked_base=D("10"))),
            D("0.1"),
            D("0.1"),
        ),
        ("unknown-nav", (*rows[:3], replace(rows[3], marked_base=None)), D("0.1"), D("0.1")),
    ):
        tiers.append(
            {
                "name": name,
                "observations": observations,
                "unallocated_quote": cash,
                "exchange_baselines": baselines,
                "global_baseline": D("100"),
                "symbol_threshold": symbol,
                "exchange_threshold": exchange,
                "expected": tier_triggers(
                    observations,
                    cash,
                    baselines,
                    D("100"),
                    symbol_threshold=symbol,
                    exchange_threshold=exchange,
                ),
            }
        )
    return {
        "scope": "Isolated pricing/TP decisions; no live amendment or simulated exit completion.",
        "prices": prices,
        "tp": tiers,
    }


def decoder_extensions() -> tuple[dict[str, object], ...]:
    smallest = float.fromhex("0x1p-149")
    raw = RECORD.pack(1704067200000, smallest, smallest, smallest, smallest)
    try:
        decode(raw)
    except DomainError as exc:
        return (
            {
                "name": "positive-float32-subnormal",
                "raw_hex": raw.hex(),
                "python_domain_error": str(exc),
                "exact_price": "1/713623846352979940529142984724747568191373312",
                "cpp_research_extension": (
                    "Decoder may retain exact rational; financial tick admission remains separate."
                ),
            },
        )
    raise DomainError("documented Python subnormal boundary changed; review contract")


def _math_arguments(case: dict[str, object]) -> list[str]:
    arguments = cast(dict[str, object], case["input"])
    market = cast(Market, arguments["market"])
    if market.price.tick is None or market.amount.tick is None:
        raise DomainError("C++ math CLI comparison currently requires explicit tick increments")

    def fee_args(fee: FeeRule) -> list[object]:
        return [
            fee.currency,
            fee.rate,
            fee.increment,
            "-" if fee.quote_per_token is None else fee.quote_per_token,
            fee.max_settlements,
        ]

    if case["operation"] == "grid":
        values = [
            "math-grid",
            arguments["anchor"],
            arguments["spacing"],
            market.price.tick,
            *cast(tuple[int, ...], arguments["indices"]),
        ]
    else:
        market_args = [
            market.price.tick,
            market.amount.tick,
            market.buy_amount.minimum,
            market.buy_cost.minimum,
            market.sell_amount.minimum,
            market.sell_cost.minimum,
        ]
        target_args = [arguments["spacing"], arguments["net_target"]]
        if case["operation"] == "buy":
            values = [
                "math-buy",
                arguments["price"],
                *market_args,
                *fee_args(cast(FeeRule, arguments["buy_fee"])),
                *fee_args(cast(FeeRule, arguments["sell_fee"])),
                *target_args,
                arguments["available_quote"],
            ]
        elif case["operation"] == "take_profit":
            values = [
                "math-tp",
                arguments["owned"],
                arguments["cost"],
                *market_args,
                *fee_args(cast(FeeRule, arguments["fee_rule"])),
                *target_args,
            ]
        else:
            raise DomainError("unsupported math observation")
    return [str(value) for value in values]


def _exact_observation(expected: object, actual: object, path: str = "result") -> None:
    # Exact rational diagnostics need not equal Python's rounded Decimal96
    # display. They do not relax equality of ANY quantized financial field.
    omitted = {
        "market_generation",
        "geometric_floor",
        "unrounded_price_floor",
        "unrounded_amount_floor",
    }
    if isinstance(expected, D):
        if not isinstance(actual, str) or D(actual) != expected:
            raise DomainError(f"{path}: expected exact decimal {expected}, observed {actual!r}")
    elif is_dataclass(expected) and not isinstance(expected, type):
        if not isinstance(actual, dict):
            raise DomainError(f"{path}: expected financial object")
        for field in fields(expected):
            if field.name not in omitted:
                if field.name not in actual:
                    raise DomainError(f"{path}.{field.name}: missing field")
                _exact_observation(
                    getattr(expected, field.name), actual[field.name], f"{path}.{field.name}"
                )
    elif isinstance(expected, tuple):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise DomainError(f"{path}: array size/type differs")
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            _exact_observation(left, right, f"{path}[{index}]")
    elif actual != expected:
        raise DomainError(f"{path}: expected {expected!r}, observed {actual!r}")


def compare_cpp_math(executable: Path) -> dict[str, object]:
    """Invoke a local C++ candidate; compare its math against actual domain code."""
    program = executable.resolve(strict=True)
    if not program.is_file():
        raise DomainError("C++ comparison needs a regular executable")
    rows = []
    for case in math_cases():
        arguments = _math_arguments(case)
        # Fixed math argv, caller-selected local binary and explicit timeout.
        completed = subprocess.run(  # nosec B603
            [str(program), *arguments], capture_output=True, text=True, check=False, timeout=30
        )
        row: dict[str, object] = {
            "name": case["name"],
            "arguments": arguments,
            "exit_code": completed.returncode,
            "expected": case.get("expected"),
            "expected_error": case.get("error"),
            "stderr": completed.stderr,
        }
        try:
            if "error" in case:
                rejection: object = json.loads(completed.stderr)
                if (
                    completed.returncode != 2
                    or not isinstance(rejection, dict)
                    or not isinstance(rejection.get("error"), str)
                    or not rejection["error"]
                ):
                    raise DomainError("C++ did not return a controlled domain rejection")
            else:
                if completed.returncode != 0:
                    raise DomainError("C++ rejected a domain-accepted configuration")
                value: object = json.loads(completed.stdout)
                row["observed"] = value
                if case["operation"] == "grid":
                    if not isinstance(value, dict) or "prices" not in value:
                        raise DomainError("grid result must contain prices")
                    value = value["prices"]
                _exact_observation(case["expected"], value)
        except (ValueError, ArithmeticError) as exc:
            row["status"] = "FAIL"
            row["difference"] = str(exc)
        else:
            row["status"] = "PASS"
        rows.append(row)
    result: dict[str, object] = {
        "schema": SCHEMA,
        "kind": "cpp-domain-math-differential",
        "executable": str(program),
        "scope": (
            "Tick-size bounded fixtures; maximum limits and significant digits not certified."
        ),
        "diagnostics_excluded": (
            "market_generation",
            "geometric_floor",
            "unrounded_price_floor",
            "unrounded_amount_floor",
        ),
        "cases": rows,
        "passed": sum(row["status"] == "PASS" for row in rows),
        "failed": sum(row["status"] != "PASS" for row in rows),
    }
    result["result_sha256"] = digest(result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--golden", action="store_true", help="emit exact synthetic oracle JSON to stdout"
    )
    mode.add_argument(
        "--cpp", type=Path, help="compare C++ math CLI with existing domain functions"
    )
    arguments = parser.parse_args(argv)
    result = golden() if arguments.cpp is None else compare_cpp_math(arguments.cpp)
    sys.stdout.buffer.write(canonical(result) + b"\n")
    return int(result.get("failed", 0) != 0)


if __name__ == "__main__":
    raise SystemExit(main())
