"""Conservative Decimal marks from committed ownership and fresh synthetic facts."""

from decimal import Decimal

from grid.domain.market import FeeRule, Market, Money
from grid.domain.model import Context, State
from grid.domain.numbers import ONE, ZERO, DomainError, arithmetic, decimal
from grid.persistence import codec
from grid.portfolio.model import Conversion, Mark, Nav, PoolKey, PortfolioPolicy, ValuationFacts
from grid.universe.model import Facts, MarketKey


def fresh(facts: ValuationFacts, policy: PortfolioPolicy, now_ms: int, now_ns: int) -> None:
    if not facts.accounts_verified or not 0 <= now_ms - facts.observed_ms <= policy.nav_age_ms:
        raise DomainError("portfolio account evidence stale or unverified")
    if not 0 <= now_ns - facts.received_ns <= policy.nav_age_ms * 1000000:
        raise DomainError("portfolio receipt stale")


def market_facts(
    state: State,
    context: Context,
    packet: ValuationFacts,
    policy: PortfolioPolicy,
    now_ms: int,
    now_ns: int,
) -> Facts:
    facts = next((f for f in packet.markets if f.key == MarketKey.of(state.scope)), None)
    if facts is None or not (
        facts.account_verified and facts.rules_verified and facts.fees_verified
    ):
        raise DomainError("portfolio market/account/fee evidence missing")
    observed = facts.context
    if (
        observed.market,
        observed.policy,
        observed.buy_fee,
        observed.sell_fee,
        observed.config_revision,
    ) != (
        context.market,
        context.policy,
        context.buy_fee,
        context.sell_fee,
        context.config_revision,
    ) or (state.config_revision, state.market_generation) != (
        context.config_revision,
        context.market.generation,
    ):
        raise DomainError("portfolio rules generation differs from reconciled state")
    age = min(policy.nav_age_ms, context.policy.max_source_age_ms)
    if (
        any(
            not 0 <= now_ms - stamp <= age
            for stamp in (
                facts.account_observed_ms,
                observed.rules_observed_ms,
                observed.fees_observed_ms,
            )
        )
        or not 0 <= now_ns - facts.received_ns <= age * 1000000
    ):
        raise DomainError("portfolio rules/account/fee evidence stale")
    book = facts.book
    book.require_fresh(now_ns, now_ms, age)
    if book.generation != context.market.generation or observed.book is None:
        raise DomainError("portfolio book generation missing")
    for levels, reverse in ((book.bids, True), (book.asks, False)):
        for price, quantity in levels:
            decimal(price, positive=True)
            decimal(quantity, positive=True)
        if not 1 <= len(levels) <= 100 or any(p <= ZERO or q <= ZERO for p, q in levels):
            raise DomainError("invalid bounded portfolio depth")
        if [p for p, _ in levels] != sorted({p for p, _ in levels}, reverse=reverse):
            raise DomainError("portfolio depth ordering invalid")
    if book.bids[0][0] >= book.asks[0][0] or (
        observed.book.bid,
        observed.book.ask,
        observed.book.observed_ms,
        observed.book.market_generation,
    ) != (book.bids[0][0], book.asks[0][0], book.source_ms, book.generation):
        raise DomainError("portfolio book reference mismatch")
    return facts


@arithmetic
def sell_quantity(owned: Decimal, market: Market, fee: FeeRule, price: Decimal) -> Decimal:
    """Largest bounded precision-valid amount with conservative base fee coverage."""
    upper = min(owned, market.sell_amount.maximum or owned)
    if market.sell_cost.maximum is not None:
        upper = min(upper, market.sell_cost.maximum / price)
    amount = market.amount.round(upper, up=False)
    if fee.currency != market.base or amount + fee.estimate(market, price, amount).amount <= owned:
        return amount
    lower, upper = ZERO, amount
    for _ in range(1024):
        middle = market.amount.round((lower + upper) / Decimal(2), up=False)
        if middle <= lower:
            return lower
        if middle + fee.estimate(market, price, middle).amount <= owned:
            lower = middle
        else:
            upper = middle
    raise DomainError("base fee search bound")


class Conversions:
    def __init__(
        self, facts: ValuationFacts, policy: PortfolioPolicy, now_ms: int, now_ns: int
    ) -> None:
        self.reporting = policy.reporting
        self.rows: dict[PoolKey, Conversion] = {}
        self.used: dict[PoolKey, Decimal] = {}
        for row in facts.conversions:
            if (
                row.reporting != policy.reporting
                or not 0 <= now_ms - row.observed_ms <= policy.nav_age_ms
                or not 0 <= now_ns - row.received_ns <= policy.nav_age_ms * 1000000
            ):
                raise DomainError("stale or mismatched FX quote")
            self.rows[row.key] = row

    @arithmetic
    def value(self, key: PoolKey, amount: Decimal, *, liability: bool = False) -> Decimal:
        if amount == ZERO:
            return ZERO
        if key.currency == self.reporting:
            return amount
        row = self.rows.get(key)
        if row is None:
            raise DomainError("explicit direct conversion missing")
        self.used[key] = self.used.get(key, ZERO) + amount
        if self.used[key] > row.capacity:
            raise DomainError("shared FX executable capacity exceeded")
        return amount * (row.ask_gross if liability else row.bid_net)

    @arithmetic
    def check_fee(self, state: State, context: Context) -> None:
        fee = context.sell_fee
        market = context.market
        if fee.currency in (market.base, market.quote) or fee.rate == ZERO:
            return
        token = self.rows.get(PoolKey(state.scope.venue, state.scope.account, fee.currency))
        quote = self.rows.get(PoolKey(state.scope.venue, state.scope.account, market.quote))
        if token is None or fee.quote_per_token is None:
            raise DomainError("fresh fee-token conversion required")
        bid = ONE if market.quote == self.reporting else quote.bid_net if quote else ZERO
        ask = ONE if market.quote == self.reporting else quote.ask_gross if quote else ZERO
        if (
            bid == ZERO
            or ask == ZERO
            or not token.bid_net / ask <= fee.quote_per_token <= token.ask_gross / bid
        ):
            raise DomainError("fee-token conversion differs from current FX evidence")


@arithmetic
def valuation(
    rows: tuple[tuple[State, Context], ...],
    pools: tuple[tuple[PoolKey, Decimal], ...],
    facts: ValuationFacts,
    policy: PortfolioPolicy,
    now_ms: int,
    now_ns: int,
) -> Nav:
    fresh(facts, policy, now_ms, now_ns)
    if {s.scope for s, _ in rows} != {r.scope for r in policy.exits} or len(rows) != len(
        policy.exits
    ):
        raise DomainError("complete unique portfolio ownership required")
    if tuple(key for key, _ in pools) != policy.pools:
        raise DomainError("explicit portfolio pool ownership required")
    fx = Conversions(facts, policy, now_ms, now_ns)
    marks: list[Mark] = []
    total = ZERO
    for state, context in rows:
        if state.reconcile_reason is not None or any(o.unknown for o in state.working):
            raise DomainError("portfolio ownership requires reconciliation")
        observed = market_facts(state, context, facts, policy, now_ms, now_ns)
        fx.check_fee(state, context)
        scope, market = state.scope, context.market
        owner = codec.digest(scope)
        for money in (Money(market.quote, state.quote_cash), *state.fee_balances):
            key = PoolKey(scope.venue, scope.account, money.currency)
            value = fx.value(key, money.amount)
            total += value
            marks.append(
                Mark(
                    owner,
                    key,
                    money.amount,
                    value,
                    ZERO,
                    ZERO,
                    ZERO,
                    observed.account_observed_ms,
                    observed.evidence_id,
                )
            )
        if state.owned_base == ZERO:
            continue
        bid = observed.book.bids[0][0]
        # NAV needs depth for all sellable inventory, not just one capped order.
        remaining = state.owned_base
        worst = bid
        for price, amount in observed.book.bids:
            remaining -= min(remaining, amount)
            worst = price
            if remaining == ZERO:
                break
        if remaining > ZERO:
            raise DomainError("insufficient executable portfolio depth")
        worst = market.price.round(worst, up=False)
        residual = state.owned_base
        value = fee_value = ZERO
        # Bound chunk valuation by the approved maximum order budget, including
        # separately rounded fees on each chunk. A larger position blocks NAV.
        budget = next(r.max_orders for r in policy.exits if r.scope == scope)
        for _ in range(budget):
            quantity = sell_quantity(residual, market, context.sell_fee, worst)
            if quantity == ZERO or not market.valid(worst, quantity, buy=False):
                break
            fee = context.sell_fee.estimate(market, worst, quantity)
            residual -= quantity + (fee.amount if fee.currency == market.base else ZERO)
            gross = fx.value(PoolKey(scope.venue, scope.account, market.quote), quantity * worst)
            charge = ZERO
            if fee.currency != market.base:
                charge = fx.value(
                    PoolKey(scope.venue, scope.account, fee.currency), fee.amount, liability=True
                )
            if gross < charge:
                raise DomainError("negative executable exit proceeds")
            value += gross - charge
            fee_value += charge
        quantity = sell_quantity(residual, market, context.sell_fee, worst)
        if quantity > ZERO and market.valid(worst, quantity, buy=False):
            raise DomainError("executable inventory exceeds bounded NAV order budget")
        total += value
        marks.append(
            Mark(
                owner,
                PoolKey(scope.venue, scope.account, market.base),
                state.owned_base,
                value,
                fee_value,
                residual,
                worst,
                observed.book.source_ms or 0,
                observed.evidence_id,
            )
        )
    for key, amount in pools:
        value = fx.value(key, amount)
        total += value
        marks.append(
            Mark("pool", key, amount, value, ZERO, ZERO, ZERO, facts.observed_ms, facts.evidence_id)
        )
    for obligation in facts.liabilities:
        total -= fx.value(obligation.key, obligation.amount, liability=True)
    return Nav(
        now_ms,
        facts.revision,
        facts.evidence_id,
        total,
        tuple(marks),
        facts.conversions,
        facts.liabilities,
    )
