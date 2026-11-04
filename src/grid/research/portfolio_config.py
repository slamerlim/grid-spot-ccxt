"""Explicit synthetic portfolio assumptions; never production defaults or observed depth."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

from grid.domain.model import Scope
from grid.domain.numbers import DomainError, bounded_int, decimal
from grid.persistence import codec
from grid.portfolio.model import Conversion, PoolKey, PortfolioPolicy
from grid.research.config import _fields, _pairs
from grid.research.replay import Replay


@dataclass(frozen=True)
class Liquidity:
    scope: Scope
    nav_depth: Decimal
    fill_per_bar: Decimal

    def __post_init__(self) -> None:
        decimal(self.nav_depth, positive=True)
        decimal(self.fill_per_bar, positive=True)


@dataclass(frozen=True)
class Settings:
    policy: PortfolioPolicy
    liquidity: tuple[Liquidity, ...]
    conversions: tuple[Conversion, ...]
    actions_per_bar: int
    venue_capacity: int
    max_database_bytes: int
    sqlite_source_id: str

    def __post_init__(self) -> None:
        bounded_int(self.actions_per_bar, 1, 64)
        bounded_int(self.venue_capacity, 1, 5000)
        bounded_int(self.max_database_bytes, 1048576, 536870912)
        if not 1 <= len(self.liquidity) <= 64 or len({r.scope for r in self.liquidity}) != len(
            self.liquidity
        ):
            raise DomainError("unique bounded liquidity assumptions required")
        if len(self.conversions) > 192 or len({r.key for r in self.conversions}) != len(
            self.conversions
        ):
            raise DomainError("unique bounded synthetic conversions required")
        if not self.sqlite_source_id.strip() or len(self.sqlite_source_id) > 256:
            raise DomainError("explicit mock SQLite source identity required")
        if self.policy.cashflow_rule != "no_transfers":
            raise DomainError("historical portfolio has no external cashflow model")

    def checked(self, spec: Replay) -> Settings:
        if spec.end_ms * 1000000 > 2**63 - 1:
            raise DomainError("replay clock exceeds the portfolio nanosecond bound")
        scopes = {a.scope for a in spec.allocations}
        if scopes != {r.scope for r in self.policy.exits} or scopes != {
            r.scope for r in self.liquidity
        }:
            raise DomainError("portfolio policy/liquidity must cover the complete replay roster")
        capital = {PoolKey(p.venue, p.account, p.currency) for p in spec.capital}
        if set(self.policy.pools) != capital:
            raise DomainError("all declared capital pools must remain in portfolio ownership")
        if self.policy.reporting != spec.allocations[0].context.market.quote:
            raise DomainError("replay quote must equal portfolio reporting currency")
        for allocation in spec.allocations:
            if allocation.scope in self.policy.sinks and (
                allocation.quote_cash or allocation.fee_balances
            ):
                raise DomainError("conversion sinks must begin empty")
            if allocation.scope not in self.policy.sinks and allocation.quote_cash <= 0:
                raise DomainError("ordinary allocations require explicit positive quote funds")
            if allocation.scope.venue in {"xt", "bitfinex"}:
                raise DomainError("mock executor cannot certify these compound venue rules")
        return replace(
            self,
            policy=replace(
                self.policy,
                pools=tuple(sorted(self.policy.pools)),
                exits=tuple(sorted(self.policy.exits, key=lambda r: codec.dumps(r.scope))),
                sinks=tuple(sorted(self.policy.sinks, key=codec.dumps)),
                dust=tuple(sorted(self.policy.dust, key=lambda r: r.key)),
            ),
            liquidity=tuple(sorted(self.liquidity, key=lambda r: codec.dumps(r.scope))),
            conversions=tuple(sorted(self.conversions, key=lambda r: r.key)),
        )


def bundle(settings: Settings) -> dict[str, object]:
    return {
        "schema": "grid-portfolio-replay-v1",
        "policy": codec.dumps(settings.policy),
        "liquidity": [
            {
                "scope": codec.dumps(row.scope),
                "nav_depth": codec.amount(row.nav_depth),
                "fill_per_bar": codec.amount(row.fill_per_bar),
            }
            for row in settings.liquidity
        ],
        "conversions": codec.dumps(settings.conversions),
        "actions_per_bar": settings.actions_per_bar,
        "venue_capacity": settings.venue_capacity,
        "max_database_bytes": settings.max_database_bytes,
        "sqlite_source_id": settings.sqlite_source_id,
    }


def load(path: Path) -> Settings:
    with path.open("rb") as stream:
        raw = stream.read(1048577)
    if len(raw) > 1048576:
        raise DomainError("portfolio settings exceed 1 MiB")
    try:
        data = json.loads(raw, object_pairs_hook=_pairs)
        _fields(
            data,
            "schema policy liquidity conversions actions_per_bar venue_capacity "
            "max_database_bytes sqlite_source_id",
        )
        if data["schema"] != "grid-portfolio-replay-v1" or not isinstance(data["liquidity"], list):
            raise DomainError("unknown portfolio replay schema")
        if not 1 <= len(data["liquidity"]) <= 64:
            raise DomainError("liquidity roster bound")
        rows = []
        for row in data["liquidity"]:
            _fields(row, "scope nav_depth fill_per_bar")
            rows.append(
                Liquidity(
                    codec.loads(row["scope"], Scope),
                    codec.parse_amount(row["nav_depth"]),
                    codec.parse_amount(row["fill_per_bar"]),
                )
            )
        conversions = codec.loads(data["conversions"], tuple)
        if any(type(row) is not Conversion for row in conversions):
            raise DomainError("explicit synthetic FX contracts required")
        return Settings(
            codec.loads(data["policy"], PortfolioPolicy),
            tuple(rows),
            conversions,
            data["actions_per_bar"],
            data["venue_capacity"],
            data["max_database_bytes"],
            data["sqlite_source_id"],
        )
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        raise DomainError("invalid portfolio research settings") from exc
