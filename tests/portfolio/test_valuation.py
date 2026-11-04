from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext

import pytest

from grid.domain.market import FeeRule, Money
from grid.domain.model import Context, Fill, Start, State
from grid.domain.numbers import DomainError
from grid.domain.reducer import reduce, reserved
from grid.exchange.models import ReadError
from grid.portfolio.model import Conversion, Liability, PoolKey, PortfolioPolicy
from grid.portfolio.valuation import sell_quantity, valuation

from .conftest import packet, prices


def owned(state: State, context: Context) -> State:
    state = replace(state, fee_balances=())
    start = reduce(state, Start(state.scope), context).state
    result = reduce(start, Fill(state.scope, "seed", 1, D(1), D(100), ()), context)
    assert result.accepted
    return result.state


def test_reserved_cash_and_base_are_counted_once(
    state: State, context: Context, policy: PortfolioPolicy
) -> None:
    seeded = owned(state, context)
    assert reserved(seeded, "BASE") == 1 and reserved(seeded, "QUOTE") > 0
    rows = ((seeded, context),)
    value = valuation(rows, (), packet(rows), policy, 10000, 10000000000)
    assert value.net == 1000
    with localcontext() as ambient:
        ambient.prec = 6
        assert valuation(rows, (), packet(rows), policy, 10000, 10000000000) == value


def test_winners_and_losers_across_venues_consolidate(
    state: State, context: Context, policy: PortfolioPolicy
) -> None:
    a = owned(state, context)
    b = owned(replace(state, scope=replace(state.scope, venue="other")), context)
    ca, cb = prices(context, D(300), 10000), prices(context, D(1), 10000)
    rows = ((a, ca), (b, cb))
    policy = replace(policy, exits=(policy.exits[0], replace(policy.exits[0], scope=b.scope)))
    value = valuation(rows, (), packet(rows), policy, 10000, 10000000000)
    assert value.net == 2101  # +5.05%, despite one winning allocation marking +20%.


@pytest.mark.parametrize(
    "damage",
    [
        "packet",
        "receipt",
        "account",
        "book",
        "fees",
        "generation",
        "depth",
        "liability_fx",
        "fx",
        "capacity",
        "fee_conversion",
        "recovery",
    ],
)
def test_missing_stale_or_incomplete_evidence_never_values(
    state: State, context: Context, policy: PortfolioPolicy, damage: str
) -> None:
    seeded = owned(state, context)
    rows = ((seeded, context),)
    facts = packet(rows)
    key = PoolKey(state.scope.venue, state.scope.account, "TOKEN")
    if damage == "packet":
        facts = replace(facts, observed_ms=8999)
    elif damage == "receipt":
        facts = replace(facts, received_ns=0)
    elif damage == "account":
        facts = replace(facts, accounts_verified=False)
    elif damage == "book":
        facts = replace(
            facts,
            markets=(
                replace(facts.markets[0], book=replace(facts.markets[0].book, source_ms=None)),
            ),
        )
    elif damage == "fees":
        facts = replace(facts, markets=(replace(facts.markets[0], fees_verified=False),))
    elif damage == "generation":
        facts = replace(
            facts,
            markets=(replace(facts.markets[0], book=replace(facts.markets[0].book, generation=2)),),
        )
    elif damage == "depth":
        facts = replace(
            facts,
            markets=(
                replace(
                    facts.markets[0],
                    book=replace(facts.markets[0].book, bids=((D(100), D("0.1")),)),
                ),
            ),
        )
    elif damage == "liability_fx":
        facts = replace(facts, liabilities=(Liability(key, D(1), "known debt"),))
    elif damage in ("fx", "capacity"):
        rows = ((replace(seeded, fee_balances=(Money("TOKEN", D(10)),)), context),)
        facts = replace(
            facts,
            conversions=(
                Conversion(
                    key,
                    "QUOTE",
                    D(1),
                    D(1),
                    D(9) if damage == "capacity" else D(100),
                    8999 if damage == "fx" else 10000,
                    10000000000,
                    "FX",
                ),
            ),
        )
    elif damage == "fee_conversion":
        context = replace(context, sell_fee=FeeRule("TOKEN", D("0.001"), D("0.001"), D(1), 1))
        rows = ((seeded, context),)
        facts = packet(rows)
    elif damage == "recovery":
        rows = ((replace(seeded, reconcile_reason="unknown"), context),)
    with pytest.raises((DomainError, ReadError)):
        valuation(rows, (), facts, policy, 10000, 10000000000)


def test_third_token_fee_and_liability_use_fresh_ask_without_double_charging_actual_fees(
    state: State, context: Context, policy: PortfolioPolicy
) -> None:
    seeded = replace(owned(state, context), fee_balances=(Money("TOKEN", D(10)),))
    context = replace(context, sell_fee=FeeRule("TOKEN", D("0.01"), D("0.001"), D(1), 1))
    rows = ((seeded, context),)
    key = PoolKey(state.scope.venue, state.scope.account, "TOKEN")
    facts = replace(
        packet(rows),
        conversions=(
            Conversion(key, "QUOTE", D("0.9"), D("1.1"), D(100), 10000, 10000000000, "FX"),
        ),
        liabilities=(Liability(key, D(2), "unpaid liability"),),
    )
    assert valuation(rows, (), facts, policy, 10000, 10000000000).net == D("1005.7")


@pytest.mark.parametrize("owned_amount", [D("0.0001"), D("0.1"), D(1), D("1.001"), D(10)])
def test_base_fee_quantity_never_oversells(context: Context, owned_amount: D) -> None:
    fee = FeeRule("BASE", D("0.001"), D("0.0001"), None, 10)
    quantity = sell_quantity(owned_amount, context.market, fee, D(100))
    if quantity:
        assert quantity + fee.estimate(context.market, D(100), quantity).amount <= owned_amount
        successor = context.market.amount.next(quantity)
        assert successor + fee.estimate(context.market, D(100), successor).amount > owned_amount


@pytest.mark.parametrize("budget", [2, 3])
def test_split_maximum_orders_have_separately_rounded_fees_and_bounded_work(
    state: State,
    context: Context,
    policy: PortfolioPolicy,
    budget: int,
) -> None:
    seeded = owned(state, context)
    context = replace(
        context,
        market=replace(
            context.market, sell_amount=replace(context.market.sell_amount, maximum=D("0.4"))
        ),
        sell_fee=FeeRule("QUOTE", D("0.0001"), D("0.01"), None, 1),
    )
    policy = replace(policy, exits=(replace(policy.exits[0], max_orders=budget),))
    rows = ((seeded, context),)
    if budget == 2:
        with pytest.raises(DomainError, match="order budget"):
            valuation(rows, (), packet(rows), policy, 10000, 10000000000)
    else:
        value = valuation(rows, (), packet(rows), policy, 10000, 10000000000)
        # Each separate .4/.4/.2 order pays the rounded .01 fee.
        assert value.net == D("999.97") and value.marks[-1].estimated_fee == D("0.03")
