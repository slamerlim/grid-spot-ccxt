"""Read-only allocation attribution from complete NAV proofs and committed transfers."""

from dataclasses import dataclass
from decimal import Decimal

from grid.domain.market import Money
from grid.domain.model import Scope, State
from grid.domain.numbers import ONE, ZERO, DomainError, arithmetic, exact
from grid.domain.reducer import reserved
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.portfolio.model import Nav, PoolKey, PortfolioPolicy, ValuationFacts
from grid.portfolio.valuation import Conversions, fresh
from grid.research.replay import Replay

MAX_INTERVAL_POINTS = 20000
MAX_MOVEMENTS = 10000


@dataclass(frozen=True)
class Movement:
    request_id: str
    scope: Scope | None  # None denotes an unallocated pool, not an external contribution.
    key: PoolKey
    amount: Decimal
    value: Decimal | None


@exact
def transfers(
    ledger: Ledger, request_id: str, policy: PortfolioPolicy, facts: ValuationFacts
) -> tuple[Movement, ...]:
    """Only an actual portfolio advance can move assets without a trade in this adapter."""
    event = ledger.db.execute(
        "SELECT payload,accepted FROM events WHERE request_id=?", (request_id,)
    ).fetchone()
    if event is None or event[1] != 1 or codec.loads(event[0], tuple)[0] != "portfolio_advance":
        raise DomainError("research transfer request is not a committed portfolio advance")
    scopes = {codec.digest(rule.scope): rule.scope for rule in policy.exits}
    accounts = {codec.digest((p.venue, p.account)): (p.venue, p.account) for p in policy.pools}
    rows = ledger.db.execute(
        "SELECT account_key,currency,kind,delta FROM journal WHERE request_id=? "
        "ORDER BY account_key,currency,kind",
        (request_id,),
    ).fetchmany(MAX_MOVEMENTS + 1)
    if len(rows) > MAX_MOVEMENTS:
        raise DomainError("research transfer journal bound exceeded")
    changes: list[tuple[Scope | None, PoolKey, Decimal]] = []
    totals: dict[PoolKey, Decimal] = {}
    incoming: dict[PoolKey, Decimal] = {}
    scope: Scope | None
    for owner, currency, kind, raw in rows:
        amount = codec.parse_amount(raw)
        if not amount:
            continue
        if kind == "cost":
            continue  # Internal transfer basis, not a cashflow or realized earning.
        if kind == "balance" and owner in scopes:
            scope = scopes[owner]
            key = PoolKey(scope.venue, scope.account, currency)
        elif kind == "unassigned" and owner in accounts:
            scope = None
            key = PoolKey(*accounts[owner], currency)
        else:
            raise DomainError("unexpected financial posting in research transfer request")
        totals[key] = totals.get(key, ZERO) + amount
        incoming[key] = incoming.get(key, ZERO) + max(ZERO, amount)
        changes.append((scope, key, amount))
    if any(totals.values()):
        raise DomainError("internal portfolio transfers do not conserve owned currency")
    rates: dict[PoolKey, Decimal | None] = {}
    try:
        fresh(facts, policy, facts.observed_ms, facts.received_ns)
        fx = Conversions(facts, policy, facts.observed_ms, facts.received_ns)
    except DomainError:
        fx = None
    for key in incoming:
        if key.currency == policy.reporting:
            rates[key] = ONE
        else:
            quote = None if fx is None else fx.rows.get(key)
            rates[key] = (
                quote.bid_net if quote is not None and incoming[key] <= quote.capacity else None
            )
    result = []
    for scope, key, amount in changes:
        rate = rates[key]
        result.append(
            Movement(request_id, scope, key, amount, None if rate is None else amount * rate)
        )
    return tuple(result)


@dataclass(frozen=True)
class Point:
    scope: Scope
    end_ms_exclusive: int
    quote_cash: Decimal
    fee_balances: tuple[Money, ...]
    owned_base: Decimal
    position_cost_quote: Decimal
    realized_pnl_quote: Decimal
    reserved_quote: Decimal
    reserved_base: Decimal
    committed_quote: Decimal
    net_nav: Decimal | None
    inventory_net_exit_value: Decimal | None
    inventory_unrealized_pnl_quote: Decimal | None
    nonreporting_asset_value: Decimal | None
    unpriced_residual_base: Decimal | None
    estimated_inventory_exit_fee: Decimal | None
    cumulative_internal_flow_value: Decimal | None
    flow_adjusted_nav: Decimal | None
    unavailable_reason: str | None


@dataclass
class Track:
    baseline: Decimal
    peak: Decimal
    flows: Decimal | None = ZERO
    drawdown: Decimal = ZERO
    drawdown_fraction: Decimal = ZERO
    unknown_observations: int = 0
    unknown_post_bars: int = 0
    committed_sum: Decimal = ZERO
    committed_peak: Decimal = ZERO
    holding_bars: int = 0
    max_owned_base: Decimal = ZERO
    exposure_peak: Decimal = ZERO
    exposure_sum: Decimal = ZERO
    exposure_samples: int = 0


class Risk:
    @arithmetic
    def __init__(self, spec: Replay, baseline: Nav) -> None:
        self.spec = spec
        self.contexts = {a.scope: a.context for a in spec.allocations}
        self.owners = {codec.digest(scope): scope for scope in self.contexts}
        self.tracks: dict[Scope, Track] = {}
        for owner, scope in self.owners.items():
            amount = sum((m.value for m in baseline.marks if m.owner == owner), ZERO)
            self.tracks[scope] = Track(amount, amount)
        self.movements: list[Movement] = []
        self.bars = 0
        self.last_ms = 0
        self.last: tuple[Point, ...] = ()
        self.totals: dict[str, object] = {}

    @arithmetic
    def sample(self, states: tuple[State, ...], nav: Nav | None, now: int) -> tuple[Point, ...]:
        if tuple(s.scope for s in states) != tuple(self.contexts):
            raise DomainError("risk report ownership/order differs from replay")
        if nav is not None and nav.now_ms != now:
            raise DomainError("risk report cannot use an earlier NAV")
        marks = {} if nav is None else {(m.owner, m.key): m for m in nav.marks}
        if nav is not None and len(marks) != len(nav.marks):
            raise DomainError("duplicate ownership mark in risk report")
        rows = []
        for state in states:
            scope = state.scope
            market = self.contexts[scope].market
            owner, track = codec.digest(scope), self.tracks[scope]
            equity = inventory = exposure = residual = fee = None
            if nav is not None:
                holdings = (
                    Money(market.quote, state.quote_cash),
                    *state.fee_balances,
                    *((Money(market.base, state.owned_base),) if state.owned_base else ()),
                )
                owned_marks = []
                for money in holdings:
                    mark = marks.pop(
                        (owner, PoolKey(scope.venue, scope.account, money.currency)), None
                    )
                    if mark is None or mark.amount != money.amount:
                        raise DomainError("NAV ownership differs from paired committed state")
                    owned_marks.append(mark)
                equity = sum((m.value for m in owned_marks), ZERO)
                base = next((m for m in owned_marks if m.key.currency == market.base), None)
                inventory = ZERO if base is None else base.value
                residual = ZERO if base is None else base.residual
                fee = ZERO if base is None else base.estimated_fee
                exposure = sum(
                    (m.value for m in owned_marks if m.key.currency != market.quote), ZERO
                )
            quote_reserved = reserved(state, market.quote)
            adjusted = None if equity is None or track.flows is None else equity - track.flows
            point = Point(
                scope,
                now,
                state.quote_cash,
                state.fee_balances,
                state.owned_base,
                state.position_cost,
                state.realized_pnl,
                quote_reserved,
                reserved(state, market.base),
                state.position_cost + quote_reserved,
                equity,
                inventory,
                None if inventory is None else inventory - state.position_cost,
                exposure,
                residual,
                fee,
                track.flows,
                adjusted,
                "portfolio_nav_unavailable"
                if nav is None
                else "transfer_valuation_unavailable"
                if track.flows is None
                else None,
            )
            rows.append(point)
            if adjusted is None:
                track.unknown_observations += 1
            else:
                track.peak = max(track.peak, adjusted)
                loss = track.peak - adjusted
                track.drawdown = max(track.drawdown, loss)
                if track.baseline > ZERO:
                    track.drawdown_fraction = max(track.drawdown_fraction, loss / track.peak)
            track.max_owned_base = max(track.max_owned_base, state.owned_base)
            track.committed_peak = max(track.committed_peak, point.committed_quote)
            if exposure is not None:
                track.exposure_peak = max(track.exposure_peak, exposure)
        if nav is not None and any(owner != "pool" for owner, _ in marks):
            raise DomainError("unattributed allocation mark in risk report")
        allocated = None if nav is None else sum((p.net_nav or ZERO for p in rows), ZERO)
        pools = None if nav is None else sum((m.value for m in marks.values()), ZERO)
        liability = (
            None
            if allocated is None or pools is None or nav is None
            else allocated + pools - nav.net
        )
        if liability is not None and (
            liability < ZERO or nav is not None and not nav.liabilities and liability != ZERO
        ):
            raise DomainError("risk attribution cannot create unowned NAV")
        self.totals = {
            "allocated_nav": allocated,
            "unallocated_pool_value": pools,
            "unassigned_liability_value": liability,
            "portfolio_net_nav": None if nav is None else nav.net,
            "posted_position_cost_quote": sum((p.position_cost_quote for p in rows), ZERO),
            "posted_realized_pnl_quote": sum((p.realized_pnl_quote for p in rows), ZERO),
            "inventory_net_exit_value": None
            if nav is None
            else sum((p.inventory_net_exit_value or ZERO for p in rows), ZERO),
            "inventory_unrealized_pnl_quote": None
            if nav is None
            else sum((p.inventory_unrealized_pnl_quote or ZERO for p in rows), ZERO),
            "allocated_nonreporting_asset_value": None
            if nav is None
            else sum((p.nonreporting_asset_value or ZERO for p in rows), ZERO),
        }
        return tuple(rows)

    @arithmetic
    def observe(
        self,
        before: tuple[State, ...],
        before_nav: Nav | None,
        after: tuple[State, ...],
        nav: Nav | None,
        movements: tuple[Movement, ...],
        now: int,
    ) -> tuple[tuple[Point, ...], tuple[Point, ...]]:
        if now <= self.last_ms:
            raise DomainError("risk observations must advance chronologically")
        if len(self.movements) + len(movements) > MAX_MOVEMENTS:
            raise DomainError("research transfer receipt bound exceeded")
        first = self.sample(before, before_nav, now)
        for movement in movements:
            if movement.scope is not None:
                track = self.tracks[movement.scope]
                track.flows = (
                    None
                    if track.flows is None or movement.value is None
                    else track.flows + movement.value
                )
        self.movements.extend(movements)
        last = self.sample(after, nav, now)
        for point in last:
            track = self.tracks[point.scope]
            track.committed_sum += point.committed_quote
            track.holding_bars += int(point.owned_base > ZERO)
            track.unknown_post_bars += int(point.net_nav is None)
            if point.nonreporting_asset_value is not None:
                track.exposure_sum += point.nonreporting_asset_value
                track.exposure_samples += 1
        self.bars += 1
        self.last_ms, self.last = now, last
        return first, last

    @arithmetic
    def report(self) -> dict[str, object]:
        reports = []
        for point in self.last:
            track = self.tracks[point.scope]
            pnl = (
                None
                if point.flow_adjusted_nav is None
                else point.flow_adjusted_nav - track.baseline
            )
            reports.append(
                {
                    "scope": point.scope,
                    "initial_owned_nav": track.baseline,
                    "last": point,
                    "net_pnl_after_internal_flows": pnl,
                    "pnl_fraction_initial_nav": None
                    if pnl is None or track.baseline == ZERO
                    else pnl / track.baseline,
                    "max_drawdown_quote_observed": track.drawdown,
                    "max_drawdown_fraction_observed": None
                    if track.baseline == ZERO
                    else track.drawdown_fraction,
                    "drawdown_complete": track.unknown_observations == 0,
                    "unknown_observations": track.unknown_observations,
                    "unknown_post_action_bars": track.unknown_post_bars,
                    "mean_committed_quote": track.committed_sum / self.bars,
                    "mean_committed_fraction_initial_nav": None
                    if track.baseline == ZERO
                    else track.committed_sum / self.bars / track.baseline,
                    "peak_committed_quote": track.committed_peak,
                    "fraction_bars_holding_inventory": Decimal(track.holding_bars) / self.bars,
                    "max_owned_base": track.max_owned_base,
                    "peak_nonreporting_value_observed": track.exposure_peak,
                    "mean_nonreporting_value_known_post_actions": None
                    if not track.exposure_samples
                    else track.exposure_sum / track.exposure_samples,
                    "known_exposure_post_action_bars": track.exposure_samples,
                }
            )
        return {
            "schema": "grid-portfolio-allocation-risk-v1",
            "reporting_currency": self.spec.allocations[0].context.market.quote,
            "simulated_bars": self.bars,
            "last_attribution": self.totals,
            "allocations": tuple(reports),
            "internal_movements": tuple(self.movements),
            "basis": (
                "Paired committed states and complete portfolio NAV; no independent repricing.",
                "Unknown NAV withholds all allocation marks; holdings/costs remain visible.",
                "Inventory unrealized PnL is net modeled exit value less remaining committed cost.",
                "Non-reporting exposure includes base and fee assets; reservations never add NAV.",
                "Transfers use same-time bid FX once per moved currency; never a trade.",
                "Drawdown uses NAV less net internal inflows, before then after actions.",
                "Pnl/initial funding is an additive-flow convention, not a time-weighted return.",
                "Zero-funded sinks have no percentage return/drawdown denominator.",
                "Unknown transfer marks withhold adjusted NAV permanently; no later backfill.",
                "Means use post-action bars; peaks/drawdown include both observations.",
                "Pools and unassigned liabilities remain separate; no arbitrary attribution.",
                "Sampled drawdown excludes unobserved intrabar paths; no execution guarantee.",
            ),
        }
