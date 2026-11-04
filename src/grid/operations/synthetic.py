"""Explicit invented operational fixtures; these are never production defaults."""

from dataclasses import dataclass, replace
from decimal import Decimal as D

from grid.domain.market import FeeRule, Limits, Market, Money, Precision, PrecisionMode
from grid.domain.model import Book, Context, Policy, Scope, State
from grid.domain.numbers import bounded_int
from grid.exchange.models import BookObservation
from grid.execution.models import Limits as ExecutionLimits
from grid.operations.model import Limits as OperationalLimits
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds
from grid.portfolio.model import ExitRule, PortfolioPolicy, ValuationFacts
from grid.universe.model import Facts, MarketKey, SelectionMode, UniversePolicy

# Small fixture caps chosen before the local experiment, not deployment budgets.
STORAGE = Bounds(50, 8, 4 * 1024 * 1024, 100, 256, 8, 5000)
EXECUTION = ExecutionLimits(16, 20, 20, 1000, 1000, 10000, 1000, 1000, 10, 1000)
OPERATIONS = OperationalLimits(7, 10, 3, 100, 32, 60000, 512 * 1024**2, 4 * 1024**2, 5000, 10000)


@dataclass(frozen=True)
class Fixture:
    contexts: tuple[tuple[Scope, Context], ...]
    universe: UniversePolicy
    portfolio: PortfolioPolicy

    @property
    def scopes(self) -> tuple[Scope, ...]:
        return tuple(s for s, _ in self.contexts)

    def prepare(self, ledger: Ledger) -> None:
        for index, (scope, context) in enumerate(self.contexts):
            funds = Money("QUOTE", D(1000))
            ledger.credit(f"fixture-fund-{index}", scope.venue, scope.account, funds)
            ledger.start(f"fixture-start-{index}", scope, (funds,), context)

    def facts(self, ms: int, revision: int, bid: D = D(100)) -> ValuationFacts:
        rows: list[Facts] = []
        for scope, original in self.contexts:
            context = replace(
                original,
                now_ms=ms,
                rules_observed_ms=ms,
                fees_observed_ms=ms,
                book=Book(bid, bid + D("0.01"), ms, 1),
            )
            book = BookObservation(
                scope.symbol,
                1,
                ((bid, D(100)),),
                ((bid + D("0.01"), D(100)),),
                ms,
                ms,
                ms * 1000000,
            )
            rows.append(
                Facts(
                    MarketKey.of(scope),
                    context,
                    book,
                    revision,
                    ms,
                    ms * 1000000,
                    f"invented-operational-{revision}",
                    True,
                    True,
                    True,
                )
            )
        return ValuationFacts(
            revision, ms, ms * 1000000, f"invented-packet-{revision}", True, tuple(rows), (), ()
        )

    def states(self) -> tuple[State, ...]:
        return tuple(State(scope, D(1000), (), 1, 1) for scope in self.scopes)


def fixture(venues: int, candidates: int) -> Fixture:
    bounded_int(venues, 1, 7)
    bounded_int(candidates, venues, 1000)
    rows: list[tuple[Scope, Context]] = []
    for index in range(candidates):
        base = f"BASE{index}"
        market = Market(
            f"{base}/QUOTE",
            base,
            "QUOTE",
            1,
            Precision(PrecisionMode.TICK, tick=D("0.01")),
            Precision(PrecisionMode.TICK, tick=D("0.001")),
            Limits(D("0.01"), D(1000000)),
            Limits(D(1), D(10000)),
            Limits(D(0), None),
            Limits(D("0.1"), D(10000)),
            Limits(D(0), None),
            True,
            False,
        )
        policy = Policy(
            D("0.1"),
            D("0.01"),
            D(10),
            D(900),
            D(1),
            D(1000),
            D("0.00000001"),
            D("0.01"),
            3,
            10,
            8,
            1000,
            1000,
            10,
            1000,
            "bid",
        )
        fee = FeeRule("QUOTE", D(0), D("0.0001"), None, 64)
        scope = Scope(
            f"synthetic-{index % venues}",
            "mock-account",
            market.symbol,
            f"allocation-{index}",
            "grid",
            "fixture",
        )
        context = Context(
            market,
            policy,
            fee,
            fee,
            1,
            10000,
            10000,
            10000,
            Book(D(100), D("100.01"), 10000, 1),
            True,
        )
        rows.append((scope, context))
    universe = UniversePolicy(
        SelectionMode.AUTOMATIC,
        tuple(f"synthetic-{i}" for i in range(venues)),
        "QUOTE",
        (),
        (),
        (),
        candidates,
        candidates,
        candidates,
        1000,
        0,
        D(0),
        D("0.01"),
        D(2),
        D("0.01"),
    )
    exits = tuple(ExitRule(s, "bid", D(1), D("0.5"), D(0), 1000, 3) for s, _ in rows)
    portfolio = PortfolioPolicy("QUOTE", (), exits, (), (), D(0), 1000, 10, 10000, "no_transfers")
    return Fixture(tuple(rows), universe, portfolio)
