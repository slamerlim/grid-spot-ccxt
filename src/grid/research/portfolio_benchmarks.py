"""Funded, counterfactual benchmarks valued by the portfolio's existing NAV rules."""

from dataclasses import replace
from decimal import Decimal

from grid.domain.market import Money
from grid.domain.model import Scope, State, TransferredLot
from grid.domain.numbers import ZERO, DomainError, arithmetic
from grid.exchange.models import ReadError
from grid.portfolio.model import PoolKey, ValuationFacts
from grid.portfolio.valuation import valuation
from grid.research.history import MINUTE, Bar
from grid.research.portfolio_config import Settings
from grid.research.replay import Hold, Replay, buy_hold, token_nav
from grid.universe.model import MarketKey


class Benchmarks:
    @arithmetic
    def __init__(self, spec: Replay, settings: Settings, first: tuple[Bar, ...]) -> None:
        self.settings = settings
        self.cash: list[State] = []
        self.hold: list[State] = []
        self.entries: list[tuple[Scope, Hold]] = []
        pools = {PoolKey(p.venue, p.account, p.currency): p.amount for p in spec.capital}
        for allocation, bar in zip(spec.allocations, first, strict=True):
            context, scope = allocation.context, allocation.scope
            initial = State(
                scope,
                allocation.quote_cash,
                allocation.fee_balances,
                context.config_revision,
                context.market.generation,
            )
            self.cash.append(initial)
            for money in (
                Money(context.market.quote, allocation.quote_cash),
                *allocation.fee_balances,
            ):
                key = PoolKey(scope.venue, scope.account, money.currency)
                if money.amount:
                    pools[key] -= money.amount
                    if pools[key] < ZERO:
                        raise DomainError("benchmark allocation exceeds pool ownership")
            held = buy_hold(allocation, bar, spec.synthetic_spread)
            self.entries.append((scope, held))
            # Counterfactual valuation object ONLY; never inserted in a financial ledger.
            self.hold.append(
                replace(
                    initial,
                    quote_cash=held.cash,
                    fee_balances=held.fees,
                    lots=(
                        TransferredLot(
                            "research-buy-hold",
                            held.base,
                            context.market.price.round(
                                bar.close * (1 + spec.synthetic_spread), up=True
                            ),
                        ),
                    )
                    if held.base
                    else (),
                    position_cost=allocation.quote_cash
                    - held.cash
                    + token_nav(context, allocation.fee_balances)
                    - token_nav(context, held.fees),
                )
            )
        self.pools = tuple((key, pools[key]) for key in settings.policy.pools)
        self.entered_ms = first[0].timestamp + MINUTE

    @arithmetic
    def mark(self, facts: ValuationFacts, initial_nav: Decimal) -> dict[str, object]:
        rows: dict[str, object] = {"end_ms_exclusive": facts.observed_ms}
        contexts = {row.key: row.context for row in facts.markets}
        for label, states in (("cash", self.cash), ("buy_hold", self.hold)):
            try:
                nav = valuation(
                    tuple((s, contexts[MarketKey.of(s.scope)]) for s in states),
                    self.pools,
                    facts,
                    self.settings.policy,
                    facts.observed_ms,
                    facts.received_ns,
                )
                value: Decimal | None = nav.net
            except (DomainError, ReadError):
                value = None
            rows[label] = {
                "net_nav": value,
                "return_fraction": value / initial_nav - 1 if value is not None else None,
                "unavailable_reason": "portfolio_valuation_constraints" if value is None else None,
            }
        return rows

    def report(self, initial_nav: Decimal, last: dict[str, object]) -> dict[str, object]:
        return {
            "schema": "grid-portfolio-benchmarks-v1",
            "initial_owned_nav": initial_nav,
            "entry_ms": self.entered_ms,
            "reporting_currency": self.settings.policy.reporting,
            "valuation_basis": "owned_portfolio_controller_net_nav",
            "assumptions": (
                "Counterfactual references only; no orders, fills, ledger postings or transfers.",
                "Cash retains initial currencies including fee tokens and unallocated pools.",
                "Buy/hold invests each allocation's spendable quote once at first completed "
                "close plus assumed spread, rounded up, with its modeled entry fee.",
                "Entry uses market amount/cost maxima and quote reserve, not grid lot caps; "
                "no latency, queue, volume or guaranteed execution is claimed.",
                "Entry constraints may leave cash; unallocated pools remain uninvested.",
                "Valuation shares strategy depth/FX/exit-fee/order-budget rules; insufficient "
                "valuation evidence is null, never zero or forward-filled.",
                "Benchmark ends with strategy at cycle completion or EOF; no future mark "
                "or forced benchmark liquidation. Returns are not realized execution returns.",
            ),
            "entries": tuple(self.entries),
            "unallocated_pools": self.pools,
            "last": last,
        }
