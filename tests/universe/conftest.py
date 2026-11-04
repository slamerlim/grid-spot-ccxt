from decimal import Decimal as D

import pytest
from exchange.conftest import async_sockets as async_sockets
from exchange.conftest import raw_market as raw_market
from persistence.conftest import bounds as bounds
from persistence.conftest import funded as funded
from persistence.conftest import ledger as ledger

from grid.domain.model import Context, State
from grid.exchange.models import BookObservation
from grid.universe.model import Facts, MarketKey, SelectionMode, UniversePolicy


def facts_for(state: State, context: Context, revision: int = 1) -> Facts:
    assert context.book is not None
    book = context.book
    return Facts(
        MarketKey.of(state.scope),
        context,
        BookObservation(
            context.market.symbol,
            context.market.generation,
            ((book.bid, D(100)),),
            ((book.ask, D(100)),),
            book.observed_ms,
            context.now_ms,
            context.now_ms * 1000000,
        ),
        revision,
        context.now_ms,
        context.now_ms * 1000000,
        f"synthetic-{revision}",
        True,
        True,
        True,
    )


@pytest.fixture
def facts(state: State, context: Context) -> Facts:
    return facts_for(state, context)


@pytest.fixture
def policy(state: State) -> UniversePolicy:
    return UniversePolicy(
        SelectionMode.AUTOMATIC,
        (state.scope.venue,),
        "QUOTE",
        (),
        (),
        (),
        1,
        1000,
        100,
        1000,
        5000,
        D("0.001"),
        D("0.01"),
        D(2),
        D("0.01"),
    )
