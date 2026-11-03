"""Synthetic constraints and allocated funds; never production policy defaults."""

from decimal import Decimal as D

import pytest

from grid.domain.market import FeeRule, Limits, Market, Money, Precision, PrecisionMode
from grid.domain.model import Book, Context, Policy, Scope, State


@pytest.fixture(scope="module")
def market() -> Market:
    return Market(
        symbol="BASE/QUOTE",
        base="BASE",
        quote="QUOTE",
        generation=1,
        price=Precision(PrecisionMode.TICK, tick=D("0.01")),
        amount=Precision(PrecisionMode.TICK, tick=D("0.001")),
        prices=Limits(D("0.01"), D("1000000")),
        buy_amount=Limits(D("1"), D("10000")),
        buy_cost=Limits(D("0"), None),
        sell_amount=Limits(D("0.1"), D("10000")),
        sell_cost=Limits(D("0"), None),
        spot=True,
        leveraged=False,
    )


@pytest.fixture(scope="module")
def context(market: Market) -> Context:
    return Context(
        market=market,
        policy=Policy(
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
        ),
        buy_fee=FeeRule("QUOTE", D("0"), D("0.0001"), None, 64),
        sell_fee=FeeRule("QUOTE", D("0"), D("0.0001"), None, 64),
        config_revision=1,
        now_ms=10000,
        rules_observed_ms=10000,
        fees_observed_ms=10000,
        book=Book(D("100"), D("100.01"), 10000, 1),
        allow_new_exposure=True,
    )


@pytest.fixture(scope="module")
def state() -> State:
    return State(
        scope=Scope("fake", "allocated-account", "BASE/QUOTE", "allocation", "grid", "cycle"),
        quote_cash=D("1000"),
        fee_balances=(Money("TOKEN", D("10")),),
        config_revision=1,
        market_generation=1,
    )
