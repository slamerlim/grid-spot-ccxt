"""Deterministic cost/depth admission; no candles, predictions or cached balances."""

import re
from dataclasses import dataclass, replace
from decimal import Decimal

from grid.domain.market import minimum_buy
from grid.domain.model import Mode, Side, State
from grid.domain.numbers import ONE, ZERO, DomainError, arithmetic, decimal
from grid.domain.reducer import reserved
from grid.exchange.models import ReadError
from grid.universe.model import Facts, MarketKey, Membership, UniversePolicy


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reason: str
    cost_fraction: Decimal | None = None
    depth_ratio: Decimal | None = None


@arithmetic
def evaluate(
    state: State, facts: Facts, policy: UniversePolicy, now_ms: int, now_ns: int
) -> Eligibility:
    try:
        key = MarketKey.of(state.scope)
        if facts.key != key or not policy.permits(key):
            raise DomainError("excluded_by_policy")
        if not (facts.account_verified and facts.rules_verified and facts.fees_verified):
            raise DomainError("unverified_account_rules_or_fees")
        context = replace(facts.context, now_ms=now_ms)
        market, book = context.market, facts.book
        if market.quote != policy.quote or re.search(
            r"(?:[235][LS]|BULL|BEAR|UP|DOWN)$", market.base
        ):
            raise DomainError("quote_or_product_excluded")
        if state.mode in (Mode.PAUSED, Mode.PORTFOLIO_EXIT, Mode.EXIT_BLOCKED):
            raise DomainError("higher_priority_control")
        if state.reconcile_reason is not None:
            raise DomainError("unreconciled_ownership")
        if not context.allow_new_exposure:
            raise DomainError("risk_blocks_admission")
        if not 0 <= now_ns - facts.received_ns <= policy.freshness_ms * 1000000:
            raise DomainError("stale_facts")
        if not 0 <= now_ms - facts.account_observed_ms <= policy.freshness_ms:
            raise DomainError("stale_account_evidence")
        if not all(
            0 <= now_ms - stamp <= min(policy.freshness_ms, context.policy.max_source_age_ms)
            for stamp in (context.rules_observed_ms, context.fees_observed_ms)
        ):
            raise DomainError("stale_rules_or_fees")
        book.require_fresh(
            now_ns, now_ms, min(policy.freshness_ms, context.policy.max_source_age_ms)
        )
        if book.generation != market.generation or context.book is None:
            raise DomainError("book_generation_or_reference_missing")
        for side, reverse in ((book.bids, True), (book.asks, False)):
            if not 1 <= len(side) <= 100:
                raise DomainError("bounded_complete_depth_required")
            for price, quantity in side:
                decimal(price, positive=True)
                decimal(quantity, positive=True)
            prices = [p for p, _ in side]
            if prices != sorted(set(prices), reverse=reverse):
                raise DomainError("malformed_depth")
        bid, ask = book.bids[0][0], book.asks[0][0]
        if bid >= ask or (
            context.book.bid,
            context.book.ask,
            context.book.market_generation,
            context.book.observed_ms,
        ) != (bid, ask, market.generation, book.source_ms):
            raise DomainError("book_reference_mismatch")
        spread = (ask - bid) / bid
        if spread > min(policy.max_spread, context.policy.max_spread):
            raise DomainError("spread_exceeds_policy")
        price = market.price.round(bid if context.policy.seed_at == "bid" else ask, up=False)
        plan = minimum_buy(
            price,
            market,
            context.buy_fee,
            context.sell_fee,
            context.policy.spacing,
            context.policy.net_target,
            max(ZERO, state.quote_cash - context.policy.quote_reserve),
        )
        # Existing reservations remain owned; this assessment never credits or reserves money.
        buys = tuple(order for order in state.working if order.side is Side.BUY)
        committed = state.position_cost + sum(
            (order.price * order.remaining + order.acquisition_fee for order in buys), ZERO
        )
        lots = {lot.order_id for lot in state.lots} | {order.id for order in buys}
        if (
            reserved(state, market.quote) > state.quote_cash
            or committed > context.policy.max_quote_committed
            or len(lots) > context.policy.max_lots
            or len(state.working) > context.policy.max_open_orders
            or any(reserved(state, money.currency) > money.amount for money in state.fee_balances)
        ):
            raise DomainError("existing_risk_limit_breach")
        if plan.acquisition_cost > context.policy.max_quote_committed:
            raise DomainError("minimum_exceeds_risk_cap")
        for fee in (plan.fee, plan.prospective_sell.fee):
            if fee.currency not in (market.base, market.quote):
                needed = sum(
                    (
                        f.amount
                        for f in (plan.fee, plan.prospective_sell.fee)
                        if f.currency == fee.currency
                    ),
                    ZERO,
                )
                balance = next(
                    (m.amount for m in state.fee_balances if m.currency == fee.currency), ZERO
                )
                if needed > balance:
                    raise DomainError("insufficient_fee_token_funds")
        bid_depth = sum(
            (q for p, q in book.bids if p >= bid * (ONE - policy.depth_deviation)), ZERO
        )
        ask_depth = sum(
            (q for p, q in book.asks if p <= ask * (ONE + policy.depth_deviation)), ZERO
        )
        depth_ratio = min(bid_depth, ask_depth) / plan.amount
        if depth_ratio < policy.depth_multiple:
            raise DomainError("insufficient_visible_depth")
        fee_cost = context.buy_fee.quote_cost(market, plan.fee, price)
        fee_cost += context.sell_fee.quote_cost(
            market, plan.prospective_sell.fee, plan.prospective_sell.price
        )
        return Eligibility(True, "eligible", spread + fee_cost / (price * plan.amount), depth_ratio)
    except (DomainError, ReadError) as exc:
        return Eligibility(False, str(exc))


@arithmetic
def select(
    policy: UniversePolicy,
    candidates: tuple[tuple[State, Eligibility], ...],
    previous: tuple[Membership, ...],
    now_ms: int,
) -> tuple[State, ...]:
    if len(candidates) > policy.candidate_limit or len({s.scope for s, _ in candidates}) != len(
        candidates
    ):
        raise DomainError("candidate bound or identity violated")
    incumbents = {m.scope: m for m in previous if m.selected}
    eligible = [
        (s, e) for s, e in candidates if e.eligible and policy.permits(MarketKey.of(s.scope))
    ]

    def rank(item: tuple[State, Eligibility]) -> tuple[int, int, Decimal, Decimal, MarketKey]:
        state, evidence = item
        cost = evidence.cost_fraction
        depth = evidence.depth_ratio
        if cost is None or depth is None:
            raise DomainError("eligible candidate lacks cost/depth evidence")
        prior = incumbents.get(state.scope)
        resident = prior is not None and 0 <= now_ms - prior.since_ms < policy.residency_ms
        return (
            0 if policy.pinned(MarketKey.of(state.scope)) else 1,
            0 if resident else 1,
            cost - (policy.hysteresis if prior else ZERO),
            -depth,
            MarketKey.of(state.scope),
        )

    return tuple(state for state, _ in sorted(eligible, key=rank)[: policy.capacity])
