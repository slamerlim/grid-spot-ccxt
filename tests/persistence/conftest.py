"""Temporary synthetic ledgers on an explicitly identified test-only runtime."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from grid.domain.market import Money
from grid.domain.model import Context, State
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds
from grid.persistence.storage import no_fault

# Source identity observed in both isolated python-build-standalone test builds.
# This is mock-test evidence, not an approval for production deployment.
SOURCE_ID = "2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9"
SOURCES = frozenset({SOURCE_ID})


@pytest.fixture
def bounds() -> Bounds:
    return Bounds(50, 2, 4 * 1024 * 1024, 100, 256, 8, 5000)


@pytest.fixture
def ledger(tmp_path: Path, bounds: Bounds) -> Iterator[Ledger]:
    result = Ledger(tmp_path / "ledger.db", bounds, SOURCES, mock_storage=True)
    try:
        yield result
    finally:
        result.storage.fault = no_fault
        result.close()


@pytest.fixture
def funded(ledger: Ledger, state: State, context: Context) -> Ledger:
    ledger.credit(
        "fund-quote",
        state.scope.venue,
        state.scope.account,
        Money(context.market.quote, state.quote_cash),
    )
    for money in state.fee_balances:
        ledger.credit("fund-" + money.currency, state.scope.venue, state.scope.account, money)
    ledger.start(
        "seed",
        state.scope,
        (Money(context.market.quote, state.quote_cash), *state.fee_balances),
        context,
    )
    return ledger
