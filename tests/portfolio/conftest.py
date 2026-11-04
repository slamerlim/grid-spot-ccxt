from dataclasses import replace
from decimal import Decimal as D

import pytest
from exchange.conftest import async_sockets as async_sockets
from persistence.conftest import bounds as bounds
from persistence.conftest import ledger as ledger
from universe.conftest import facts_for

from grid.domain.model import Context, State
from grid.portfolio.model import ExitRule, PortfolioPolicy, ValuationFacts


@pytest.fixture
def policy(state: State) -> PortfolioPolicy:
    rule = ExitRule(state.scope, "bid", D(1), D("0.5"), D(0), 100, 3)
    return PortfolioPolicy("QUOTE", (), (rule,), (), (), D(0), 1000, 1, 10000, "no_transfers")


def packet(rows: tuple[tuple[State, Context], ...], revision: int = 1) -> ValuationFacts:
    now = max(c.now_ms for _, c in rows)
    return ValuationFacts(
        revision,
        now,
        now * 1000000,
        f"synthetic-portfolio-{revision}",
        True,
        tuple(facts_for(s, c, revision) for s, c in rows),
        (),
        (),
    )


def prices(context: Context, bid: D, now: int) -> Context:
    assert context.book is not None
    return replace(
        context,
        now_ms=now,
        rules_observed_ms=now,
        fees_observed_ms=now,
        book=replace(context.book, bid=bid, ask=bid + D("0.01"), observed_ms=now),
    )
