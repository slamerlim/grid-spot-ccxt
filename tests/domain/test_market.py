from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from grid.domain.market import (
    FeeRule,
    Limits,
    Market,
    Precision,
    PrecisionMode,
    aggregate_take_profit,
    grid_prices,
    minimum_buy,
)
from grid.domain.model import Context
from grid.domain.numbers import DomainError, decimal, units


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity"), D("-1"), D("1e100")])
def test_invalid_financial_inputs_fail_closed(bad: D) -> None:
    with pytest.raises(DomainError):
        decimal(bad)


def test_float_is_not_a_financial_input() -> None:
    with pytest.raises(DomainError, match="Decimal"):
        decimal(0.1)  # type: ignore[arg-type]


@given(
    value=st.decimals(min_value="0", max_value="100000", places=6, allow_nan=False),
    tick=st.sampled_from([D("0.00001"), D("0.005"), D("0.25"), D("5")]),
)
@settings(max_examples=150, derandomize=True)
def test_arbitrary_tick_rounding_bounds(value: D, tick: D) -> None:
    down, up = units(value, tick, up=False), units(value, tick, up=True)
    assert down <= value <= up
    assert value - down < tick and up - value < tick
    assert down % tick == up % tick == 0


@pytest.mark.parametrize(
    ("value", "down", "up", "previous"),
    [
        ("9.999", "9.99", "10", "9.99"),
        ("0.009999", "0.00999", "0.01", "0.00999"),
        ("1001", "1000", "1010", "1000"),
    ],
)
def test_significant_digits_at_decade_boundaries(
    value: str, down: str, up: str, previous: str
) -> None:
    precision = Precision(PrecisionMode.SIGNIFICANT, digits=3)
    assert precision.round(D(value), up=False) == D(down)
    assert precision.round(D(value), up=True) == D(up)
    assert precision.previous(D(up)) == D(previous)


def test_geometric_ladder_and_collapsed_ticks(market: Market) -> None:
    assert grid_prices(D("100"), D("0.1"), (1, 2, 3), market) == (
        D("90.90"),
        D("82.64"),
        D("75.13"),
    )
    with pytest.raises(DomainError, match="collapsed"):
        grid_prices(D("100"), D("0.000001"), (1, 2, 3), market)
    with pytest.raises(DomainError, match="increasing"):
        grid_prices(D("100"), D("0.1"), (2, 1), market)


def test_minimum_buy_is_not_increased_to_hide_unsellability(context: Context) -> None:
    market = replace(context.market, sell_amount=Limits(D("1"), None))
    fee = FeeRule("BASE", D("0.001"), D("0.001"), None, 1)
    with pytest.raises(DomainError, match="SELL amount"):
        minimum_buy(D("10"), market, fee, context.sell_fee, D("0.1"), D("0.01"), D("100"))


@pytest.mark.parametrize("significant", [False, True])
@given(price=st.integers(1, 10000), minimum=st.integers(1, 10000))
@settings(max_examples=100, derandomize=True)
def test_buy_is_smallest_valid_notional_lot(
    context: Context, significant: bool, price: int, minimum: int
) -> None:
    precision = (
        Precision(PrecisionMode.SIGNIFICANT, digits=3)
        if significant
        else Precision(PrecisionMode.TICK, tick=D("0.005"))
    )
    market = replace(
        context.market,
        amount=precision,
        buy_amount=Limits(D("0.0001"), None),
        buy_cost=Limits(D(minimum), None),
        sell_amount=Limits(D("0.000001"), None),
    )
    plan = minimum_buy(
        D(price),
        market,
        context.buy_fee,
        context.sell_fee,
        D("0.1"),
        D("0.01"),
        D("100000000"),
    )
    assert market.valid(plan.price, plan.amount, buy=True)
    previous = market.amount.previous(plan.amount)
    assert previous == 0 or not market.valid(plan.price, previous, buy=True)
    assert plan.quote_required <= D("100000000")


def test_missing_limits_and_conversions_are_not_guessed(context: Context) -> None:
    with pytest.raises(DomainError):
        Limits(None, None)  # type: ignore[arg-type]
    with pytest.raises(DomainError, match="conversion"):
        minimum_buy(
            D("100"),
            context.market,
            FeeRule("TOKEN", D("0.001"), D("0.001"), None, 1),
            context.sell_fee,
            D("0.1"),
            D("0.01"),
            D("1000"),
        )


@pytest.mark.parametrize("currency", ["BASE", "QUOTE", "TOKEN"])
@pytest.mark.parametrize("significant", [False, True])
@given(owned=st.integers(1, 30), rate=st.integers(0, 200), cost=st.integers(1, 3000))
@settings(max_examples=100, derandomize=True)
def test_tp_reserves_fees_and_chooses_least_valid_price(
    context: Context, currency: str, significant: bool, owned: int, rate: int, cost: int
) -> None:
    market = context.market
    if significant:
        market = replace(
            market,
            price=Precision(PrecisionMode.SIGNIFICANT, digits=3),
            amount=Precision(PrecisionMode.SIGNIFICANT, digits=3),
        )
    rule = FeeRule(currency, D(rate) / D("10000"), D("0.01"), D("2"), 1)
    plan = aggregate_take_profit(D(owned), D(cost), market, D("0.001"), D("1"), rule)
    assert plan.reserved_base <= owned
    assert plan.amount > 0 and market.valid(plan.price, plan.amount, buy=False)
    assert plan.expected_profit >= 1 and plan.price >= plan.geometric_floor
    previous = market.price.previous(plan.price)
    fee = rule.estimate(market, previous, plan.amount)
    fee_cost = D(0) if currency == "BASE" else rule.quote_cost(market, fee, previous)
    assert (
        previous < plan.geometric_floor
        or plan.amount * previous - fee_cost - D(cost) < 1
        or not market.valid(previous, plan.amount, buy=False)
    )
    if currency == "BASE":
        next_amount = market.amount.next(plan.amount)
        assert next_amount + rule.estimate(market, plan.price, next_amount).amount > owned


def test_large_fee_increment_and_significant_base_quantity(context: Context) -> None:
    market = replace(context.market, amount=Precision(PrecisionMode.SIGNIFICANT, digits=3))
    fee = FeeRule("BASE", D("0.001"), D("0.5"), None, 1)
    plan = aggregate_take_profit(D("10"), D("100"), market, D("0.1"), D("1"), fee)
    assert plan.amount == D("9.50") and plan.reserved_base == D("10")


def test_math_ignores_ambient_decimal_context(context: Context) -> None:
    expected = minimum_buy(
        D("100"),
        context.market,
        context.buy_fee,
        context.sell_fee,
        D("0.1"),
        D("0.01"),
        D("1000"),
    )
    with localcontext() as ambient:
        ambient.prec = 3
        assert (
            minimum_buy(
                D("100"),
                context.market,
                context.buy_fee,
                context.sell_fee,
                D("0.1"),
                D("0.01"),
                D("1000"),
            )
            == expected
        )


def test_unsatisfiable_limits_budget_and_nonspot_fail(context: Context) -> None:
    with pytest.raises(DomainError, match="quote"):
        minimum_buy(
            D("100"),
            context.market,
            context.buy_fee,
            context.sell_fee,
            D("0.1"),
            D("0.01"),
            D("99"),
        )
    with pytest.raises(DomainError, match="profitable"):
        aggregate_take_profit(
            D("1"),
            D("100"),
            replace(context.market, prices=Limits(D("0.01"), D("100"))),
            D("0.1"),
            D("1"),
            context.sell_fee,
        )
    with pytest.raises(DomainError, match="spot"):
        replace(context.market, spot=False)


@given(parts=st.lists(st.integers(1, 10000), min_size=1, max_size=20))
@settings(max_examples=100, derandomize=True)
def test_fee_reserve_bounds_rounding_each_partial_fill(context: Context, parts: list[int]) -> None:
    rule = FeeRule("QUOTE", D("0.001"), D("0.01"), None, len(parts))
    one_fill = replace(rule, max_settlements=1)
    amounts = [D(value) / D(10000) for value in parts]
    total = sum(amounts, D(0))
    paid = sum(
        (one_fill.estimate(context.market, D("99.99"), amount).amount for amount in amounts), D(0)
    )
    assert paid <= rule.estimate(context.market, D("99.99"), total).amount


def test_quantization_rejects_inexact_out_of_range_product() -> None:
    with pytest.raises(DomainError, match="exact supported precision"):
        units(D("1e96"), D("0.03"), up=True)


def test_math_audit_records_unrounded_bounds_and_generation(context: Context) -> None:
    market = replace(
        context.market,
        buy_amount=Limits(D("0"), None),
        buy_cost=Limits(D("10"), None),
        sell_amount=Limits(D("0.001"), None),
    )
    plan = minimum_buy(
        D("99"), market, context.buy_fee, context.sell_fee, D("0.1"), D("0.01"), D("1000")
    )
    assert plan.unrounded_amount_floor < plan.amount
    assert plan.market_generation == plan.prospective_sell.market_generation == market.generation
    assert plan.prospective_sell.unrounded_price_floor <= plan.prospective_sell.price


def test_very_small_coin_price_and_notional_minimum(context: Context) -> None:
    market = replace(
        context.market,
        prices=Limits(D("1e-14"), None),
        price=Precision(PrecisionMode.TICK, tick=D("1e-14")),
        buy_amount=Limits(D("1"), None),
        sell_amount=Limits(D("1"), None),
        buy_cost=Limits(D("5"), None),
    )
    plan = minimum_buy(
        D("1e-12"), market, context.buy_fee, context.sell_fee, D("0.01"), D("0.01"), D("10")
    )
    assert plan.amount == D("5e12") and plan.quote_required == D("5")
    assert plan.prospective_sell.price == D("1.01e-12")


def test_minimum_buy_cannot_round_away_a_fee_to_fit_budget(context: Context) -> None:
    fee = FeeRule("QUOTE", D("1e-96"), D("1e-96"), None, 1)
    with pytest.raises(DomainError, match="exact supported precision"):
        minimum_buy(D(1), context.market, fee, context.sell_fee, D("0.1"), D("0.01"), D(1))


def test_fee_conversion_rounds_up_without_an_intermediate_rounded_quotient(
    context: Context,
) -> None:
    # The exact token charge is just above 1; a 96-digit intermediate division
    # rounds it to 1. Rounding that result would under-reserve a whole token.
    conversion = D("9" * 96)
    fee = FeeRule("TOKEN", D("0.5"), D(1), conversion, 1)
    assert fee.estimate(context.market, D("2e96"), D(1)).amount == D(2)


def test_aggregate_sell_cannot_round_away_base_fee_ownership(context: Context) -> None:
    fee = FeeRule("BASE", D("1e-96"), D("1e-96"), None, 1)
    with pytest.raises(DomainError, match="exact supported precision"):
        aggregate_take_profit(D(1), D(100), context.market, D("0.1"), D("0.01"), fee)
