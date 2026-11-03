"""Strict, secret-free configuration for the I0 offline scaffold.

Configured values describe future prerequisites; none authorize trading. Monetary
values must be quoted decimal strings so YAML cannot introduce binary floats.
"""

from __future__ import annotations

import re
from collections.abc import Hashable
from dataclasses import dataclass, fields
from decimal import Decimal
from pathlib import Path
from typing import cast

import yaml
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken, AnchorToken, TagToken

VENUE_IDS = ("okx", "htx", "gate", "xt", "bingx", "kucoin", "bitfinex")
MAX_CONFIG_BYTES = 65_536
_DECIMAL = re.compile(r"[0-9]+(?:\.[0-9]+)?\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}\Z")


class ConfigError(ValueError):
    """A safe diagnostic which never embeds untrusted values or YAML excerpts."""


class _UniqueSafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Hashable, object]:
        result: dict[Hashable, object] = {}
        for key_node, value_node in node.value:
            key: object = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ConfigError("Configuration contains a duplicate or non-string key.")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


@dataclass(frozen=True)
class VenueConfig:
    enabled: bool = False
    account_id: str | None = None
    allocated_quote: Decimal | None = None
    max_quote_spent: Decimal | None = None


@dataclass(frozen=True)
class GridConfig:
    spacing: Decimal | None = None
    pending_buy_levels: int | None = None
    max_lots: int | None = None
    max_quote_committed: Decimal | None = None
    quote_reserve: Decimal | None = None
    minimum_net_target: Decimal | None = None
    seed_policy: str | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    order_ttl_seconds: int | None = None
    retirement_policy: str | None = None


@dataclass(frozen=True)
class RiskConfig:
    max_quote_spent_global: Decimal | None = None
    max_quote_spent_per_symbol: Decimal | None = None
    max_active_symbols: int | None = None
    max_open_orders: int | None = None
    residual_dust_quote_ceiling: Decimal | None = None
    fee_buffer: Decimal | None = None
    max_source_age_seconds: int | None = None
    max_nav_age_seconds: int | None = None


@dataclass(frozen=True)
class ExitConfig:
    price_policy: str | None = None
    max_price_deviation: Decimal | None = None
    minimum_after_fee_quote: Decimal | None = None
    max_retries: int | None = None
    order_ttl_seconds: int | None = None
    dust_policy: str | None = None


@dataclass(frozen=True)
class ResourceConfig:
    event_queue_capacity: int | None = None
    cache_capacity: int | None = None
    worker_count: int | None = None
    reconciliation_interval_seconds: int | None = None
    discovery_interval_seconds: int | None = None


@dataclass(frozen=True)
class UniverseConfig:
    mode: str | None = None
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    pin: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    live: bool
    quote_currency: str | None
    venues: tuple[tuple[str, VenueConfig], ...]
    grid: GridConfig
    risk: RiskConfig
    exit: ExitConfig
    resources: ResourceConfig
    universe: UniverseConfig

    def missing_prerequisites(self) -> tuple[str, ...]:
        """List absent operator choices without exposing any configured values."""
        missing: list[str] = []
        if self.quote_currency is None:
            missing.append("quote_currency")
        for name, section in (
            ("grid", self.grid),
            ("risk", self.risk),
            ("exit", self.exit),
            ("resources", self.resources),
        ):
            for field in fields(section):
                if getattr(section, field.name) is None:
                    missing.append(f"{name}.{field.name}")
        if self.universe.mode is None:
            missing.append("universe.mode")
        elif self.universe.mode == "manual" and not self.universe.allow:
            missing.append("universe.allow")
        enabled = [(name, venue) for name, venue in self.venues if venue.enabled]
        if not enabled:
            missing.append("venues.enabled")
        for name, venue in enabled:
            for field in fields(venue):
                if getattr(venue, field.name) is None:
                    missing.append(f"venues.{name}.{field.name}")
        return tuple(missing)


class _Section:
    def __init__(self, raw: object, name: str, allowed: set[str]) -> None:
        if not isinstance(raw, dict):
            raise ConfigError(f"{name} must be a mapping.")
        if any(not isinstance(key, str) or key not in allowed for key in raw):
            raise ConfigError(f"{name} contains unsupported keys.")
        self.values = cast(dict[str, object], raw)
        self.name = name

    def decimal(self, key: str, *, zero: bool = False) -> Decimal | None:
        value = self.values.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 64 or not _DECIMAL.fullmatch(value):
            raise ConfigError(f"{self.name}.{key} must be null or a quoted decimal string.")
        result = Decimal(value)
        if result < 0 or (not zero and result == 0):
            raise ConfigError(f"{self.name}.{key} is outside the permitted range.")
        return result

    def integer(self, key: str, *, zero: bool = False) -> int | None:
        value = self.values.get(key)
        if value is None:
            return None
        if type(value) is not int or value < (0 if zero else 1) or value > 2**31 - 1:
            raise ConfigError(f"{self.name}.{key} must be a bounded integer.")
        return value

    def choice(self, key: str, choices: set[str]) -> str | None:
        value = self.values.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or value not in choices:
            raise ConfigError(f"{self.name}.{key} contains an unsupported selection.")
        return value

    def boolean(self, key: str) -> bool:
        value = self.values.get(key, False)
        if type(value) is not bool:
            raise ConfigError(f"{self.name}.{key} must be a boolean.")
        return value

    def identifier(self, key: str) -> str | None:
        value = self.values.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise ConfigError(f"{self.name}.{key} must be a bounded identifier.")
        return value

    def symbols(self, key: str) -> tuple[str, ...]:
        value = self.values.get(key, [])
        if not isinstance(value, list) or len(value) > 1000:
            raise ConfigError(f"{self.name}.{key} must be a bounded symbol list.")
        if any(not isinstance(item, str) or not _IDENTIFIER.fullmatch(item) for item in value):
            raise ConfigError(f"{self.name}.{key} contains an invalid identifier.")
        result = tuple(cast(list[str], value))
        if len(set(result)) != len(result):
            raise ConfigError(f"{self.name}.{key} contains duplicates.")
        return result


def _field_names(
    cls: type[
        Config
        | VenueConfig
        | GridConfig
        | RiskConfig
        | ExitConfig
        | ResourceConfig
        | UniverseConfig
    ],
) -> set[str]:
    return {field.name for field in fields(cls)}


def parse_config(raw: object) -> Config:
    """Validate structure and exact types, including every disabled venue."""
    root = _Section(raw, "config", _field_names(Config))
    venue_section = _Section(root.values.get("venues", {}), "venues", set(VENUE_IDS))
    venues: list[tuple[str, VenueConfig]] = []
    for name in VENUE_IDS:
        section = _Section(
            venue_section.values.get(name, {}), f"venues.{name}", _field_names(VenueConfig)
        )
        venues.append(
            (
                name,
                VenueConfig(
                    enabled=section.boolean("enabled"),
                    account_id=section.identifier("account_id"),
                    allocated_quote=section.decimal("allocated_quote"),
                    max_quote_spent=section.decimal("max_quote_spent"),
                ),
            )
        )
    grid = _Section(root.values.get("grid", {}), "grid", _field_names(GridConfig))
    risk = _Section(root.values.get("risk", {}), "risk", _field_names(RiskConfig))
    exit_config = _Section(root.values.get("exit", {}), "exit", _field_names(ExitConfig))
    resources = _Section(
        root.values.get("resources", {}), "resources", _field_names(ResourceConfig)
    )
    universe = _Section(root.values.get("universe", {}), "universe", _field_names(UniverseConfig))
    result = Config(
        live=root.boolean("live"),
        quote_currency=root.identifier("quote_currency"),
        venues=tuple(venues),
        grid=GridConfig(
            spacing=grid.decimal("spacing"),
            pending_buy_levels=grid.integer("pending_buy_levels"),
            max_lots=grid.integer("max_lots"),
            max_quote_committed=grid.decimal("max_quote_committed"),
            quote_reserve=grid.decimal("quote_reserve", zero=True),
            minimum_net_target=grid.decimal("minimum_net_target"),
            seed_policy=grid.choice("seed_policy", {"passive_limit", "bounded_marketable_limit"}),
            min_price=grid.decimal("min_price"),
            max_price=grid.decimal("max_price"),
            order_ttl_seconds=grid.integer("order_ttl_seconds"),
            retirement_policy=grid.choice(
                "retirement_policy",
                {"manage_existing", "cancel_buys_and_hold", "bounded_limit_exit"},
            ),
        ),
        risk=RiskConfig(
            max_quote_spent_global=risk.decimal("max_quote_spent_global"),
            max_quote_spent_per_symbol=risk.decimal("max_quote_spent_per_symbol"),
            max_active_symbols=risk.integer("max_active_symbols"),
            max_open_orders=risk.integer("max_open_orders"),
            residual_dust_quote_ceiling=risk.decimal("residual_dust_quote_ceiling", zero=True),
            fee_buffer=risk.decimal("fee_buffer", zero=True),
            max_source_age_seconds=risk.integer("max_source_age_seconds"),
            max_nav_age_seconds=risk.integer("max_nav_age_seconds"),
        ),
        exit=ExitConfig(
            price_policy=exit_config.choice(
                "price_policy", {"passive_limit", "bounded_marketable_limit"}
            ),
            max_price_deviation=exit_config.decimal("max_price_deviation", zero=True),
            minimum_after_fee_quote=exit_config.decimal("minimum_after_fee_quote", zero=True),
            max_retries=exit_config.integer("max_retries", zero=True),
            order_ttl_seconds=exit_config.integer("order_ttl_seconds"),
            dust_policy=exit_config.choice(
                "dust_policy", {"block_until_resolved", "operator_approved"}
            ),
        ),
        resources=ResourceConfig(
            event_queue_capacity=resources.integer("event_queue_capacity"),
            cache_capacity=resources.integer("cache_capacity"),
            worker_count=resources.integer("worker_count"),
            reconciliation_interval_seconds=resources.integer("reconciliation_interval_seconds"),
            discovery_interval_seconds=resources.integer("discovery_interval_seconds"),
        ),
        universe=UniverseConfig(
            mode=universe.choice("mode", {"manual", "automatic", "hybrid"}),
            allow=universe.symbols("allow"),
            deny=universe.symbols("deny"),
            pin=universe.symbols("pin"),
        ),
    )
    if (
        result.grid.min_price is not None
        and result.grid.max_price is not None
        and result.grid.min_price >= result.grid.max_price
    ):
        raise ConfigError("grid.min_price must be lower than grid.max_price.")
    if result.exit.max_price_deviation is not None and result.exit.max_price_deviation >= 1:
        raise ConfigError("exit.max_price_deviation must be less than one.")
    return result


def load_config(path: Path | None = None) -> Config:
    """Read only the explicitly supplied YAML; never auto-discover credentials."""
    if path is None:
        return parse_config({})
    try:
        if not path.is_file():
            raise ConfigError("Configuration must be an existing regular file.")
        with path.open("rb") as handle:
            payload = handle.read(MAX_CONFIG_BYTES + 1)
        if len(payload) > MAX_CONFIG_BYTES:
            raise ConfigError("Configuration exceeds the supported size limit.")
        text = payload.decode("utf-8")
        if any(isinstance(token, (AliasToken, AnchorToken, TagToken)) for token in yaml.scan(text)):
            raise ConfigError("Configuration aliases, anchors and explicit tags are not supported.")
        loader = _UniqueSafeLoader(text)
        try:
            raw: object = loader.get_single_data()
        finally:
            loader.dispose()
    except ConfigError:
        raise
    except (
        OSError,
        UnicodeError,
        yaml.YAMLError,
        RecursionError,
        ValueError,
        TypeError,
        AttributeError,
        OverflowError,
    ):
        raise ConfigError("Configuration is unreadable or invalid YAML.") from None
    return parse_config(raw)
