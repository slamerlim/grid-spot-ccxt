from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext

import pytest

from grid.domain.model import Mode, State
from grid.domain.numbers import DomainError
from grid.universe.model import Facts, MarketKey, Membership, SelectionMode, UniversePolicy
from grid.universe.selection import Eligibility, evaluate, select


@pytest.mark.parametrize("mode", list(SelectionMode))
def test_modes_deny_wins_and_pin_cannot_admit_ineligible(
    state: State, facts: Facts, policy: UniversePolicy, mode: SelectionMode
) -> None:
    key = MarketKey.of(state.scope)
    configured = replace(policy, mode=mode, pin=(key,))
    result = evaluate(state, facts, configured, 10000, 10000000000)
    assert result.eligible == (mode is not SelectionMode.MANUAL)
    configured = replace(configured, allow=(key,))
    good = evaluate(state, facts, configured, 10000, 10000000000)
    assert good.eligible and good.cost_fraction == D("0.0001")
    assert select(configured, ((state, good),), (), 10000) == (state,)
    denied = replace(configured, deny=(key,))
    assert select(denied, ((state, good),), (), 10000) == ()
    bad = evaluate(state, replace(facts, fees_verified=False), configured, 10000, 10000000000)
    assert not bad.eligible
    assert select(configured, ((state, bad),), (), 10000) == ()


@pytest.mark.parametrize(
    "failure",
    [
        "account",
        "rules",
        "fees",
        "book",
        "future",
        "monotonic",
        "generation",
        "depth",
        "spread",
        "quote",
        "risk",
        "fee_token",
        "minimum",
        "reconciliation",
        "paused",
        "exit",
        "disabled",
        "malformed_depth",
        "rules_ttl",
        "fees_ttl",
        "committed",
        "product",
    ],
)
def test_hard_admission_failures(
    state: State, facts: Facts, policy: UniversePolicy, failure: str
) -> None:
    context = facts.context
    if failure in {"account", "rules", "fees"}:
        facts = replace(
            facts,
            account_verified=failure != "account",
            rules_verified=failure != "rules",
            fees_verified=failure != "fees",
        )
    elif failure == "book":
        facts = replace(facts, book=replace(facts.book, source_ms=None))
    elif failure == "future":
        facts = replace(facts, account_observed_ms=10001)
    elif failure == "monotonic":
        facts = replace(facts, received_ns=0)
    elif failure == "generation":
        facts = replace(facts, book=replace(facts.book, generation=2))
    elif failure == "depth":
        facts = replace(facts, book=replace(facts.book, bids=((D(100), D("0.1")),)))
    elif failure == "spread":
        policy = replace(policy, max_spread=D("0.00001"))
    elif failure == "quote":
        policy = replace(policy, quote="OTHER")
    elif failure == "risk":
        facts = replace(facts, context=replace(context, allow_new_exposure=False))
    elif failure == "fee_token":
        facts = replace(
            facts,
            context=replace(
                context,
                buy_fee=replace(
                    context.buy_fee, currency="TOKEN", rate=D("0.01"), quote_per_token=D(1)
                ),
            ),
        )
        state = replace(state, fee_balances=())
    elif failure == "minimum":
        facts = replace(
            facts,
            context=replace(
                context,
                market=replace(
                    context.market, buy_amount=replace(context.market.buy_amount, minimum=D(100))
                ),
            ),
        )
    elif failure == "reconciliation":
        state = replace(state, reconcile_reason="unknown")
    elif failure in {"paused", "exit"}:
        state = replace(state, mode=Mode.PAUSED if failure == "paused" else Mode.PORTFOLIO_EXIT)
    elif failure == "disabled":
        policy = replace(policy, enabled_venues=())
    elif failure == "malformed_depth":
        facts = replace(facts, book=replace(facts.book, bids=((D(100), D(5)), (D(100), D(2)))))
    elif failure in {"rules_ttl", "fees_ttl"}:
        facts = replace(
            facts,
            context=replace(
                context,
                rules_observed_ms=8999 if failure == "rules_ttl" else 10000,
                fees_observed_ms=8999 if failure == "fees_ttl" else 10000,
            ),
        )
    elif failure == "committed":
        facts = replace(
            facts,
            context=replace(context, policy=replace(context.policy, max_quote_committed=D(99))),
        )
    elif failure == "product":
        scope = replace(state.scope, symbol="BASE3L/QUOTE")
        state = replace(state, scope=scope)
        facts = replace(
            facts,
            key=MarketKey.of(scope),
            context=replace(
                context, market=replace(context.market, symbol=scope.symbol, base="BASE3L")
            ),
            book=replace(facts.book, symbol=scope.symbol),
        )
    assert not evaluate(state, facts, policy, 10000, 10000000000).eligible


def test_residency_hysteresis_pins_stable_ties_and_bounds(
    state: State, policy: UniversePolicy
) -> None:
    other = replace(state, scope=replace(state.scope, symbol="OTHER/QUOTE"))
    first = Eligibility(True, "eligible", D("0.01"), D(10))
    challenger = Eligibility(True, "eligible", D("0.0095"), D(10))
    prior = (Membership(state.scope, True, 10000, 0, False),)
    rows = ((other, challenger), (state, first))
    assert select(policy, rows, prior, 11000) == (state,)
    assert select(policy, rows, prior, 16000) == (state,)
    assert select(replace(policy, hysteresis=D(0)), rows, prior, 16000) == (other,)
    pinned = replace(policy, mode=SelectionMode.HYBRID, pin=(MarketKey.of(other.scope),))
    assert select(pinned, rows, prior, 11000) == (other,)
    assert select(policy, ((other, first), (state, first)), (), 10000) == (state,)
    assert select(
        policy, ((other, challenger), (state, Eligibility(False, "stale"))), prior, 11000
    ) == (other,)
    with pytest.raises(DomainError):
        select(policy, ((state, first), (state, first)), (), 10000)


@pytest.mark.parametrize("count", [10, 100, 1000])
def test_candidates_remain_bounded_and_decimal_context_independent(
    state: State, facts: Facts, policy: UniversePolicy, count: int
) -> None:
    good = evaluate(state, facts, policy, 10000, 10000000000)
    with localcontext() as ambient:
        ambient.prec = 6
        assert evaluate(state, facts, policy, 10000, 10000000000) == good
    rows = tuple(
        (replace(state, scope=replace(state.scope, symbol=f"ASSET{i:04}/QUOTE")), good)
        for i in range(count)
    )
    assert select(replace(policy, capacity=25), tuple(reversed(rows)), (), 10000) == tuple(
        s for s, _ in rows[:25]
    )
    with pytest.raises(DomainError):
        select(replace(policy, candidate_limit=count - 1, cache_size=1), rows, (), 10000)
