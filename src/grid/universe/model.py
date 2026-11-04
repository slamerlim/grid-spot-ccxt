"""Scoped configuration, reviewed synthetic facts and durable control receipts."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from grid.domain.model import Context, Scope
from grid.domain.numbers import DomainError, bounded_int, decimal, identifier
from grid.exchange.models import BookObservation


@dataclass(frozen=True, order=True)
class MarketKey:
    venue: str
    account: str
    symbol: str

    def __post_init__(self) -> None:
        for value in (self.venue, self.account, self.symbol):
            identifier(value)
        if len(self.symbol.split("/")) != 2:
            raise DomainError("unified spot symbol required")

    @classmethod
    def of(cls, scope: Scope) -> "MarketKey":
        return cls(scope.venue, scope.account, scope.symbol)


class SelectionMode(StrEnum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class UniversePolicy:
    mode: SelectionMode
    enabled_venues: tuple[str, ...]
    quote: str
    allow: tuple[MarketKey, ...]
    deny: tuple[MarketKey, ...]
    pin: tuple[MarketKey, ...]
    capacity: int
    candidate_limit: int
    cache_size: int
    freshness_ms: int
    residency_ms: int
    hysteresis: Decimal
    max_spread: Decimal
    depth_multiple: Decimal
    depth_deviation: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.mode, SelectionMode):
            raise DomainError("explicit universe mode required")
        identifier(self.quote)
        bounded_int(len(self.enabled_venues), 0, 7)
        if len(set(self.enabled_venues)) != len(self.enabled_venues):
            raise DomainError("duplicate venue")
        for venue in self.enabled_venues:
            identifier(venue)
        for keys in (self.allow, self.deny, self.pin):
            if not isinstance(keys, tuple) or len(keys) > 1000 or len(set(keys)) != len(keys):
                raise DomainError("bounded unique universe keys required")
            if any(not isinstance(key, MarketKey) for key in keys):
                raise DomainError("scoped market keys required")
        bounded_int(self.candidate_limit, 1, 1000)
        bounded_int(self.capacity, 0, self.candidate_limit)
        bounded_int(self.cache_size, 1, self.candidate_limit)
        bounded_int(self.freshness_ms, 1, 300000)
        bounded_int(self.residency_ms, 0, 86400000)
        for value in (self.hysteresis, self.max_spread, self.depth_deviation):
            decimal(value)
            if value >= 1:
                raise DomainError("fraction must be below one")
        decimal(self.depth_multiple, positive=True)
        if self.depth_multiple < 1:
            raise DomainError("depth multiple must cover at least the minimum lot")

    def permits(self, key: MarketKey) -> bool:
        return (
            key.venue in self.enabled_venues
            and key not in self.deny
            and (self.mode is not SelectionMode.MANUAL or key in self.allow)
        )

    def pinned(self, key: MarketKey) -> bool:
        return self.mode is SelectionMode.HYBRID and key in self.pin


@dataclass(frozen=True)
class Facts:
    key: MarketKey
    context: Context
    book: BookObservation
    revision: int
    account_observed_ms: int
    received_ns: int
    evidence_id: str
    account_verified: bool
    rules_verified: bool
    fees_verified: bool

    def __post_init__(self) -> None:
        bounded_int(self.revision, 1, 2**63 - 1)
        bounded_int(self.account_observed_ms, 0, 2**53 - 1)
        bounded_int(self.received_ns, 0, 2**63 - 1)
        identifier(self.evidence_id)
        if any(
            type(flag) is not bool
            for flag in (self.account_verified, self.rules_verified, self.fees_verified)
        ):
            raise DomainError("explicit evidence flags required")
        if self.key.symbol != self.context.market.symbol or self.book.symbol != self.key.symbol:
            raise DomainError("facts identity mismatch")


@dataclass(frozen=True)
class Membership:
    scope: Scope
    selected: bool
    since_ms: int
    control_revision: int
    managed_retirement: bool


@dataclass(frozen=True)
class UniverseSnapshot:
    revision: int
    policy: UniversePolicy
    members: tuple[Membership, ...]
    observed_ms: int
    actor: str
    reason: str

    def __post_init__(self) -> None:
        bounded_int(self.revision, 1, 2**63 - 1)
        bounded_int(self.observed_ms, 0, 2**53 - 1)
        identifier(self.actor)
        identifier(self.reason)
        if len(self.members) > self.policy.candidate_limit:
            raise DomainError("universe roster exceeds candidate bound")
        if len({member.scope for member in self.members}) != len(self.members):
            raise DomainError("duplicate universe membership")
        if sum(member.selected for member in self.members) > self.policy.capacity:
            raise DomainError("universe selection exceeds capacity")
        for member in self.members:
            bounded_int(member.since_ms, 0, self.observed_ms)
            bounded_int(member.control_revision, 0, 2**63 - 1)
            if type(member.selected) is not bool or type(member.managed_retirement) is not bool:
                raise DomainError("invalid persisted membership flags")
            if member.selected and not self.policy.permits(MarketKey.of(member.scope)):
                raise DomainError("persisted selection contradicts universe policy")


@dataclass(frozen=True)
class BuyAdmission:
    scope: Scope
    universe_revision: int
    facts_revision: int
    evidence_id: str
    now_ms: int
    expires_ms: int
    context_digest: str

    def __post_init__(self) -> None:
        bounded_int(self.universe_revision, 1, 2**63 - 1)
        bounded_int(self.facts_revision, 1, 2**63 - 1)
        bounded_int(self.now_ms, 0, 2**53 - 1)
        bounded_int(self.expires_ms, self.now_ms, 2**53 - 1)
        identifier(self.evidence_id)
        if len(self.context_digest) != 64 or any(
            c not in "0123456789abcdef" for c in self.context_digest
        ):
            raise DomainError("canonical context digest required")
