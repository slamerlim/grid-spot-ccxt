"""Explicit portfolio ownership, valuation provenance and exit contracts."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from grid.domain.model import Scope
from grid.domain.numbers import DomainError, arithmetic, bounded_int, decimal, identifier
from grid.universe.model import Facts, MarketKey


class CyclePhase(StrEnum):
    ACTIVE = "active"
    REQUESTED = "exit_requested"
    EXITING = "exiting"
    BLOCKED = "exit_blocked"
    COMPLETE = "exit_complete"


@dataclass(frozen=True, order=True)
class PoolKey:
    venue: str
    account: str
    currency: str

    def __post_init__(self) -> None:
        for value in (self.venue, self.account, self.currency):
            identifier(value)


@dataclass(frozen=True)
class Conversion:
    """One explicitly reviewed direct conversion, never assumed stablecoin parity.

    Bid is net executable reporting proceeds per asset; ask is gross reporting
    cost per liability unit. Both include conversion fees/spread. Capacity is
    shared across all holdings on this venue/account/currency in one valuation.
    """

    key: PoolKey
    reporting: str
    bid_net: Decimal
    ask_gross: Decimal
    capacity: Decimal
    observed_ms: int
    received_ns: int
    evidence_id: str

    def __post_init__(self) -> None:
        identifier(self.reporting)
        identifier(self.evidence_id)
        for value in (self.bid_net, self.ask_gross, self.capacity):
            decimal(value, positive=True)
        if self.key.currency == self.reporting or self.bid_net > self.ask_gross:
            raise DomainError("invalid explicit conversion")
        bounded_int(self.observed_ms, 0, 2**53 - 1)
        bounded_int(self.received_ns, 0, 2**63 - 1)


@dataclass(frozen=True)
class Liability:
    key: PoolKey
    amount: Decimal
    evidence_id: str

    def __post_init__(self) -> None:
        decimal(self.amount, positive=True)
        identifier(self.evidence_id)


@dataclass(frozen=True)
class ExitRule:
    scope: Scope
    pricing: Literal["bid", "ask"]
    floor: Decimal
    max_deviation: Decimal
    minimum_net_quote: Decimal
    ttl_ms: int
    max_orders: int

    def __post_init__(self) -> None:
        if self.pricing not in ("bid", "ask"):
            raise DomainError("explicit exit pricing required")
        decimal(self.floor, positive=True)
        decimal(self.max_deviation)
        decimal(self.minimum_net_quote)
        if self.max_deviation >= 1:
            raise DomainError("exit deviation must be below one")
        bounded_int(self.ttl_ms, 1, 86400000)
        bounded_int(self.max_orders, 1, 1000)


@dataclass(frozen=True)
class DustRule:
    key: PoolKey
    max_amount: Decimal
    max_value: Decimal

    def __post_init__(self) -> None:
        decimal(self.max_amount)
        decimal(self.max_value)


@dataclass(frozen=True)
class PortfolioPolicy:
    reporting: str
    pools: tuple[PoolKey, ...]
    exits: tuple[ExitRule, ...]
    sinks: tuple[Scope, ...]
    dust: tuple[DustRule, ...]
    dust_limit: Decimal
    nav_age_ms: int
    evaluation_interval_ms: int
    exit_duration_ms: int
    cashflow_rule: Literal["no_transfers", "additive_baseline"]

    def __post_init__(self) -> None:
        identifier(self.reporting)
        if self.cashflow_rule not in ("no_transfers", "additive_baseline"):
            raise DomainError("explicit cashflow convention required")
        for values in (self.pools, self.exits, self.sinks, self.dust):
            if not isinstance(values, tuple) or len(values) > 1000:
                raise DomainError("bounded immutable portfolio policy required")
        if not 1 <= len(self.exits) <= 1000 or len({r.scope for r in self.exits}) != len(
            self.exits
        ):
            raise DomainError("unique exit rules required for every allocation")
        if len(set(self.pools)) != len(self.pools) or len(set(self.sinks)) != len(self.sinks):
            raise DomainError("duplicate ownership")
        if len({MarketKey.of(r.scope) for r in self.exits}) != len(self.exits):
            raise DomainError("one portfolio allocation per account and symbol required")
        if not set(self.sinks) <= {r.scope for r in self.exits}:
            raise DomainError("conversion sinks must remain supervised")
        if len({d.key for d in self.dust}) != len(self.dust):
            raise DomainError("duplicate dust approval")
        decimal(self.dust_limit)
        bounded_int(self.nav_age_ms, 1, 300000)
        bounded_int(self.evaluation_interval_ms, 1, 300000)
        bounded_int(self.exit_duration_ms, 1, 86400000)


@dataclass(frozen=True)
class ValuationFacts:
    revision: int
    observed_ms: int
    received_ns: int
    evidence_id: str
    accounts_verified: bool
    markets: tuple[Facts, ...]
    conversions: tuple[Conversion, ...]
    liabilities: tuple[Liability, ...]

    def __post_init__(self) -> None:
        bounded_int(self.revision, 1, 2**63 - 1)
        bounded_int(self.observed_ms, 0, 2**53 - 1)
        bounded_int(self.received_ns, 0, 2**63 - 1)
        identifier(self.evidence_id)
        if type(self.accounts_verified) is not bool:
            raise DomainError("explicit account reconciliation evidence required")
        for values in (self.markets, self.conversions, self.liabilities):
            if not isinstance(values, tuple) or len(values) > 1000:
                raise DomainError("valuation input bound exceeded")
        if len({f.key for f in self.markets}) != len(self.markets):
            raise DomainError("duplicate market evidence")
        if len({f.key for f in self.conversions}) != len(self.conversions):
            raise DomainError("duplicate conversion evidence")
        if len({f.key for f in self.liabilities}) != len(self.liabilities):
            raise DomainError("aggregate liabilities per currency required")


@dataclass(frozen=True)
class Mark:
    owner: str
    key: PoolKey
    amount: Decimal
    value: Decimal
    estimated_fee: Decimal
    residual: Decimal
    price: Decimal
    observed_ms: int
    evidence_id: str

    def __post_init__(self) -> None:
        identifier(self.owner)
        identifier(self.evidence_id)
        for value in (self.amount, self.value, self.estimated_fee, self.residual, self.price):
            decimal(value)
        if self.residual > self.amount:
            raise DomainError("residual exceeds ownership")
        bounded_int(self.observed_ms, 0, 2**53 - 1)


@dataclass(frozen=True)
class Nav:
    now_ms: int
    evidence_revision: int
    evidence_id: str
    net: Decimal
    marks: tuple[Mark, ...]
    conversions: tuple[Conversion, ...]
    liabilities: tuple[Liability, ...]

    def __post_init__(self) -> None:
        bounded_int(self.now_ms, 0, 2**53 - 1)
        bounded_int(self.evidence_revision, 1, 2**63 - 1)
        identifier(self.evidence_id)
        decimal(self.net, signed=True)
        if len(self.marks) > 10000 or len(self.conversions) > 1000 or len(self.liabilities) > 1000:
            raise DomainError("NAV proof bound exceeded")


@dataclass(frozen=True)
class ExitSlot:
    scope: Scope
    anchor_bid: Decimal
    orders: int
    order_id: int | None
    created_ms: int | None
    reason: str

    def __post_init__(self) -> None:
        decimal(self.anchor_bid, positive=True)
        bounded_int(self.orders, 0, 1000)
        identifier(self.reason)
        if self.order_id is not None:
            bounded_int(self.order_id, 1, 2**63 - 1)
        if self.created_ms is not None:
            bounded_int(self.created_ms, 0, 2**53 - 1)


@dataclass(frozen=True)
class Residual:
    key: PoolKey
    amount: Decimal
    value: Decimal

    def __post_init__(self) -> None:
        decimal(self.amount, positive=True)
        decimal(self.value)


@dataclass(frozen=True)
class ExitApproval:
    """Reviewed cycle and immutable exit policy, independent of NAV sample revisions."""

    cycle_id: str
    policy_sha256: str

    def __post_init__(self) -> None:
        identifier(self.cycle_id)
        if (
            not isinstance(self.policy_sha256, str)
            or len(self.policy_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.policy_sha256)
        ):
            raise DomainError("explicit exit policy digest required")


@dataclass(frozen=True)
class AllocationExit:
    """An operator's bounded exit of principal inventory, within an active cycle."""

    slot: ExitSlot
    approval: ExitApproval
    phase: CyclePhase
    started_ms: int
    observed_ms: int
    actor: str
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.phase, CyclePhase) or self.phase is CyclePhase.ACTIVE:
            raise DomainError("allocation exit must remain latched")
        bounded_int(self.started_ms, 0, 2**53 - 1)
        bounded_int(self.observed_ms, self.started_ms, 2**53 - 1)
        identifier(self.actor)
        identifier(self.reason)


@dataclass(frozen=True)
class Cycle:
    id: str
    revision: int
    phase: CyclePhase
    policy: PortfolioPolicy
    baseline: Nav
    adjustment: Decimal
    trigger: Nav | None
    final: Nav | None
    started_exit_ms: int | None
    last_evaluation_ms: int
    slots: tuple[ExitSlot, ...]
    pending_flows: tuple[str, ...]
    residuals: tuple[Residual, ...]
    reason: str
    actor: str
    trigger_baseline: Decimal | None = None
    manual_exit: ExitApproval | None = None
    allocation_exits: tuple[AllocationExit, ...] = ()

    @arithmetic
    def __post_init__(self) -> None:
        identifier(self.id)
        identifier(self.reason)
        identifier(self.actor)
        bounded_int(self.revision, 1, 2**63 - 1)
        decimal(self.baseline.net, positive=True)
        decimal(self.adjustment, signed=True)
        if self.baseline.net + self.adjustment <= 0:
            raise DomainError("positive adjusted baseline required")
        bounded_int(self.last_evaluation_ms, self.baseline.now_ms, 2**53 - 1)
        if not isinstance(self.phase, CyclePhase):
            raise DomainError("explicit cycle phase required")
        if self.manual_exit is not None and (
            type(self.manual_exit) is not ExitApproval or self.manual_exit.cycle_id != self.id
        ):
            raise DomainError("manual exit approval belongs to a different cycle")
        if self.phase is CyclePhase.ACTIVE and (
            self.trigger is not None
            or self.trigger_baseline is not None
            or self.started_exit_ms is not None
            or self.slots
            or self.residuals
            or self.manual_exit is not None
        ):
            raise DomainError("active cycle cannot retain a cleared exit latch")
        if len(self.slots) > 1000 or len(self.pending_flows) > 1000 or len(self.residuals) > 3000:
            raise DomainError("cycle snapshot bound exceeded")
        if len({s.scope for s in self.slots}) != len(self.slots):
            raise DomainError("duplicate exit slots")
        if len(set(self.pending_flows)) != len(self.pending_flows):
            raise DomainError("duplicate pending cashflow")
        if self.phase is not CyclePhase.ACTIVE and (
            self.trigger is None or self.started_exit_ms is None or self.trigger_baseline is None
        ):
            raise DomainError("latched exit requires trigger evidence")
        if self.trigger_baseline is not None:
            decimal(self.trigger_baseline, positive=True)
            if self.trigger is None or (
                self.manual_exit is None
                and self.trigger.net < self.trigger_baseline * Decimal("1.10")
            ):
                raise DomainError("trigger threshold proof inconsistent")
        if (self.final is not None) != (self.phase is CyclePhase.COMPLETE):
            raise DomainError("completion requires a final valuation")
        if self.phase is not CyclePhase.ACTIVE and {s.scope for s in self.slots} != {
            r.scope for r in self.policy.exits
        }:
            raise DomainError("latched exit must retain every allocation")
        if self.phase is CyclePhase.COMPLETE and self.pending_flows:
            raise DomainError("unclassified cashflow prevents completion")
        if self.started_exit_ms is not None:
            bounded_int(self.started_exit_ms, self.baseline.now_ms, self.last_evaluation_ms)
        if (
            not isinstance(self.allocation_exits, tuple)
            or len(self.allocation_exits) > len(self.policy.exits)
            or len({e.slot.scope for e in self.allocation_exits}) != len(self.allocation_exits)
            or not {e.slot.scope for e in self.allocation_exits}
            <= {r.scope for r in self.policy.exits}
            or any(
                e.approval.cycle_id != self.id or e.started_ms < self.baseline.now_ms
                for e in self.allocation_exits
            )
        ):
            raise DomainError("invalid allocation exit ownership")

    @property
    def observation_ms(self) -> int:
        return max(self.last_evaluation_ms, *(e.observed_ms for e in self.allocation_exits), 0)

    def protects(self, scope: Scope) -> bool:
        return self.phase is not CyclePhase.ACTIVE or any(
            e.slot.scope == scope for e in self.allocation_exits
        )

    def exit_slot(self, scope: Scope) -> ExitSlot | None:
        slots = (
            self.slots
            if self.phase is not CyclePhase.ACTIVE
            else tuple(e.slot for e in self.allocation_exits if e.phase is not CyclePhase.COMPLETE)
        )
        return next((s for s in slots if s.scope == scope), None)

    def deadline_ms(self, scope: Scope) -> int:
        starts = [e.started_ms for e in self.allocation_exits if e.slot.scope == scope]
        if self.started_exit_ms is not None:
            starts.append(self.started_exit_ms)
        if not starts:
            raise DomainError("exit deadline requires a durable start")
        return min(starts) + self.policy.exit_duration_ms

    @property
    def exit_cause(self) -> str | None:
        if self.trigger is None:
            return None
        return "operator" if self.manual_exit is not None else "nav_target"

    @property
    @arithmetic
    def target_reached_at_exit(self) -> bool | None:
        if self.trigger is None or self.trigger_baseline is None:
            return None
        return self.trigger.net >= self.trigger_baseline * Decimal("1.10")

    @property
    @arithmetic
    def final_return(self) -> Decimal | None:
        """Additive-baseline return; approved unliquidated dust has zero final NAV."""
        if self.final is None:
            return None
        return self.final.net / (self.baseline.net + self.adjustment) - 1

    @property
    @arithmetic
    def exit_shortfall(self) -> Decimal | None:
        """Trigger NAV plus subsequent classified flows minus final net proceeds."""
        if self.final is None or self.trigger is None or self.trigger_baseline is None:
            return None
        flows_since_trigger = self.baseline.net + self.adjustment - self.trigger_baseline
        return self.trigger.net + flows_since_trigger - self.final.net


@dataclass(frozen=True)
class PortfolioPermit:
    cycle: str
    revision: int
    scope: Scope
    order_id: int
    now_ms: int
    evidence_revision: int
    context_digest: str

    def __post_init__(self) -> None:
        identifier(self.cycle)
        for value in (self.revision, self.order_id, self.evidence_revision):
            bounded_int(value, 1, 2**63 - 1)
        bounded_int(self.now_ms, 0, 2**53 - 1)
        if len(self.context_digest) != 64 or any(
            c not in "0123456789abcdef" for c in self.context_digest
        ):
            raise DomainError("canonical context digest required")


@dataclass(frozen=True)
class Cashflow:
    id: str
    key: PoolKey
    amount: Decimal
    observed_ms: int
    evidence_id: str
    disposition: Literal["unclassified", "adjusted", "not_external"]

    def __post_init__(self) -> None:
        identifier(self.id)
        identifier(self.evidence_id)
        decimal(self.amount, signed=True)
        bounded_int(self.observed_ms, 0, 2**53 - 1)
        if self.amount == 0 or self.disposition not in ("unclassified", "adjusted", "not_external"):
            raise DomainError("explicit nonzero external cashflow required")
