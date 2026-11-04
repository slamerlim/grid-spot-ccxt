"""Explicit universe edits; no allocation, risk-policy or exchange discovery effects."""

from dataclasses import dataclass, replace
from enum import StrEnum

from grid.domain.model import Scope
from grid.domain.numbers import DomainError, bounded_int, identifier
from grid.universe.model import MarketKey, SelectionMode, UniversePolicy


class EditAction(StrEnum):
    ENABLE = "enable-venue"
    DISABLE = "disable-venue"
    MODE = "set-symbol-mode"
    ALLOW = "allow"
    DENY = "deny"
    PIN = "pin"
    UNPIN = "unpin"
    CAPACITY = "set-capacity"
    REFRESH = "refresh-universe"


class Retirement(StrEnum):
    MANAGE = "manage-existing"
    HOLD = "cancel-buys-and-hold"
    LIMIT = "bounded-limit-exit"


@dataclass(frozen=True)
class UniverseEdit:
    revision: int
    action: EditAction
    keys: tuple[MarketKey, ...] = ()
    venues: tuple[str, ...] = ()
    mode: SelectionMode | None = None
    capacity: int | None = None
    retirement: Retirement | None = None

    def __post_init__(self) -> None:
        bounded_int(self.revision, 1, 2**63 - 2)
        if not isinstance(self.action, EditAction):
            raise DomainError("explicit universe edit required")
        for values, bound in ((self.keys, 1000), (self.venues, 7)):
            if (
                not isinstance(values, tuple)
                or len(values) > bound
                or len(set(values)) != len(values)
            ):
                raise DomainError("bounded unique universe edit targets required")
        for venue in self.venues:
            identifier(venue)
        key_action = self.action in (
            EditAction.ALLOW,
            EditAction.DENY,
            EditAction.PIN,
            EditAction.UNPIN,
        )
        venue_action = self.action in (EditAction.ENABLE, EditAction.DISABLE)
        retirement_action = self.action in (EditAction.DENY, EditAction.DISABLE)
        if (
            key_action != bool(self.keys)
            or venue_action != bool(self.venues)
            or (self.action is EditAction.MODE) != (self.mode is not None)
            or (self.action is EditAction.CAPACITY) != (self.capacity is not None)
            or retirement_action != (self.retirement is not None)
            or self.mode is not None
            and not isinstance(self.mode, SelectionMode)
            or self.retirement is not None
            and not isinstance(self.retirement, Retirement)
        ):
            raise DomainError("universe edit fields differ from operation")
        if self.capacity is not None:
            bounded_int(self.capacity, 0, 1000)

    def targets(self, scopes: tuple[Scope, ...]) -> tuple[Scope, ...]:
        if not set(self.keys) <= {MarketKey.of(s) for s in scopes} or not set(self.venues) <= {
            s.venue for s in scopes
        }:
            raise DomainError("universe edit outside supervised roster")
        return tuple(s for s in scopes if MarketKey.of(s) in self.keys or s.venue in self.venues)

    def apply(self, policy: UniversePolicy, scopes: tuple[Scope, ...]) -> UniversePolicy:
        self.targets(scopes)
        keys, venues = set(self.keys), set(self.venues)
        if self.action is EditAction.ENABLE:
            return replace(
                policy, enabled_venues=tuple(sorted(set(policy.enabled_venues) | venues))
            )
        if self.action is EditAction.DISABLE:
            return replace(
                policy, enabled_venues=tuple(v for v in policy.enabled_venues if v not in venues)
            )
        if self.action is EditAction.MODE:
            if self.mode is None:
                raise DomainError("selection mode required")
            return replace(policy, mode=self.mode)
        if self.action is EditAction.CAPACITY:
            if self.capacity is None:
                raise DomainError("selection capacity required")
            return replace(policy, capacity=self.capacity)
        if self.action is EditAction.ALLOW:
            return replace(
                policy,
                allow=tuple(sorted(set(policy.allow) | keys)),
                deny=tuple(k for k in policy.deny if k not in keys),
            )
        if self.action is EditAction.DENY:
            return replace(policy, deny=tuple(sorted(set(policy.deny) | keys)))
        if self.action is EditAction.PIN:
            return replace(policy, pin=tuple(sorted(set(policy.pin) | keys)))
        if self.action is EditAction.UNPIN:
            return replace(policy, pin=tuple(k for k in policy.pin if k not in keys))
        return policy
