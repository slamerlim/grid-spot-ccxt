"""Translate saved CCXT spot limits into the existing funded minimum-lot calculator.

The explicit refresh command makes one public metadata request per venue. Reading
saved rules is offline. Current rules replayed on old candles are an assumption.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.backtest_legacy_n import SNAPSHOTS, ArchiveCatalog
from scripts.backtest_totals import decimal_text, number

if TYPE_CHECKING:
    from grid.exchange.ccxt_transport import PublicWire

RULES_SCHEMA = 2
HISTORICAL_NOTICE = (
    "Current exchange rules applied to historical simulation — historical rule values not verified."
)
INFO_FIELDS = (
    "min_base_amount",
    "max_base_amount",
    "min_quote_amount",
    "max_quote_amount",
    "trade_status",
    "trade_quotes",
    "amount_precision",
    "precision",
    "up_rate",
    "down_rate",
    "min-order-amt",
    "max-order-amt",
    "min-order-value",
    "max-order-value",
    "limit-order-min-order-amt",
    "limit-order-max-order-amt",
    "limit-order-max-buy-amt",
    "limit-order-max-sell-amt",
    "buy-limit-must-less-than",
    "sell-limit-must-greater-than",
    "price-precision",
    "amount-precision",
    "value-precision",
    "api-trading",
    "tags",
    "underlying",
)


def exact_values(value: Any) -> Any:
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, dict):
        return {key: exact_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [exact_values(item) for item in value]
    return value


def snapshot_entry(venue: str, row: dict[str, Any], mode: int) -> dict[str, Any]:
    """Preserve unified fields and the narrow documented spot exceptions."""
    from grid.exchange.models import ReadError, map_market, money

    precision = row.get("precision") or {}
    info = row.get("info") or {}
    result = {
        key: row.get(key) for key in ("symbol", "id", "base", "quote", "active", "spot", "type")
    }
    result.update(
        precision_mode={4: "tick_size", 2: "decimal_places", 3: "significant_digits"}.get(mode),
        price_precision=precision.get("price"),
        amount_precision=precision.get("amount"),
        limits=row.get("limits"),
        source_fields={"precision": precision, "limits": row.get("limits")},
        info={key: info[key] for key in INFO_FIELDS if key in info},
        rules_schema=RULES_SCHEMA,
    )
    try:
        observed = map_market(row, venue, mode, 1, 0)
        if observed.active is not True:
            raise ValueError("Current spot market is not active")
        order_limits = {
            "price": asdict(observed.prices),
            "amount": asdict(observed.amounts),
            "buy_amount": asdict(observed.buy_amount),
            "sell_amount": asdict(observed.sell_amount),
            "cost": asdict(observed.costs),
        }
        result["order_limits"] = {
            key: {"min": value["minimum"], "max": value["maximum"]}
            for key, value in order_limits.items()
        }
        required = list(observed.unmapped_constraints)
        protection: dict[str, Decimal | int] = {}
        if venue == "htx":
            for name, raw in (
                ("buy_price_max_ratio", "buy-limit-must-less-than"),
                ("sell_price_min_ratio", "sell-limit-must-greater-than"),
            ):
                if info.get(raw) is None:
                    raise ValueError(f"HTX LIMIT protection field unavailable: {raw}")
                protection[name] = money(info[raw], positive=True)
            protection["limit_reference_bars"] = 1
        if venue == "gate":
            if info.get("up_rate") is None or info.get("down_rate") is None:
                raise ValueError("Gate LIMIT price-protection percentages are unavailable")
            up, down = money(info["up_rate"]), money(info["down_rate"])
            if down >= 1:
                raise ValueError("Gate LIMIT decline percentage must be below one")
            with localcontext() as context:
                context.prec = 96
                protection["buy_price_max_ratio"] = Decimal(1) + up
                protection["sell_price_min_ratio"] = Decimal(1) - down
            protection["limit_reference_bars"] = 2
        # A Gate unified market needs a selected actual quote, not a guessed USDT peg.
        if venue == "gate" and info.get("trade_quotes"):
            required.append("gate_unified_market_quote_selection_required")
        result.update(price_protection=protection, required_context=required)
        if venue == "gate" and any(
            key not in info
            for key in (
                "min_base_amount",
                "max_base_amount",
                "min_quote_amount",
                "max_quote_amount",
            )
        ):
            raise ValueError("Gate raw spot amount/notional bounds are incomplete")
    except (ReadError, ValueError) as exc:
        result["rules_error"] = str(exc)
    return {key: exact_values(value) for key, value in result.items()}


async def refresh_snapshot(
    venue: str, directory: Path = SNAPSHOTS, *, wire: PublicWire | None = None
) -> dict[str, Any]:
    """One public GET; immutable versions plus an atomically replaced latest cache."""
    from grid.exchange.adapter import ReadLimits
    from grid.exchange.ccxt_transport import CCXT_VERSION, PublicCCXT

    if venue not in {"htx", "gate"}:
        raise ValueError("This refresh supports HTX and Gate spot metadata only")
    client = PublicCCXT(venue, ReadLimits(requests=1), wire=wire, catalog=True)
    try:
        async with asyncio.timeout(20):
            raw = await client.load_markets(True)
        if not isinstance(raw, dict):
            raise ValueError("CCXT market catalogue is unavailable")
        markets = {}
        for row in raw.values():
            if (
                not isinstance(row, dict)
                or row.get("spot") is not True
                or row.get("contract") is not False
            ):
                continue
            key = f"{row['quote']}_{row['base']}"
            if key in markets:
                raise ValueError("Duplicate spot market in CCXT catalogue")
            markets[key] = snapshot_entry(venue, row, client.precision_mode)
        if not markets:
            raise ValueError("CCXT returned no spot markets")
        now = datetime.now(UTC)
        value = {
            "schema": 2,
            "rules_schema": RULES_SCHEMA,
            "exchange": venue,
            "ccxt_version": CCXT_VERSION,
            "fetched_at": now.timestamp(),
            "fetched_utc": now.isoformat(),
            "source": "ccxt.load_markets / public spot catalogue",
            "historical_rules_verified": False,
            "historical_notice": HISTORICAL_NOTICE,
            "requests": [asdict(item) for item in client.evidence],
            "adapter_source_hashes": dict(client.source_hashes()),
            "markets": markets,
        }
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode()
        if len(encoded) > 4_000_000:
            raise ValueError("Market snapshot exceeds existing 4 MB bound")
        directory.mkdir(parents=True, exist_ok=True)
        versions = directory / "versions"
        versions.mkdir(exist_ok=True)
        current = directory / f"{venue}.json"
        if current.is_symlink():
            raise ValueError("Market cache cannot be a symlink")
        if current.exists():
            old = current.read_bytes()
            retained = versions / f"{venue}-{hashlib.sha256(old).hexdigest()}.json"
            if not retained.exists():
                with retained.open("xb") as stream:
                    stream.write(old)
        version = versions / f"{venue}-{hashlib.sha256(encoded).hexdigest()}.json"
        with version.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        descriptor, temporary = tempfile.mkstemp(prefix=f"{venue}-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, current)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return value
    finally:
        await client.close()


def market_rules(
    catalog: ArchiveCatalog, market: str, quote_quantum: str = "1e-12"
) -> dict[str, Any]:
    venue, symbol = market.split(":", 1)
    base, quote = symbol.split("/")
    snapshot = catalog.precision_snapshot(venue)
    if snapshot is None:
        raise ValueError(f"{market}: saved CCXT minimum-order rules are unavailable")
    entry = snapshot["markets"].get(f"{quote}_{base}")
    if not isinstance(entry, dict) or entry.get("symbol") != symbol:
        raise ValueError(f"{market}: no matching saved CCXT market rules")
    # This pinned Bybit mapping reads fields the current spot API deprecates.
    # Reject certification rather than silently interpreting them as current minima.
    if venue == "bybit":
        raise ValueError("Bybit minimum sizing requires a validated current CCXT field mapping")
    if venue in {"htx", "gate"}:
        if entry.get("rules_schema") != RULES_SCHEMA:
            raise ValueError(
                f"{market}: refresh HTX/Gate rules; old precision snapshots omit spot constraints"
            )
        if entry.get("rules_error"):
            raise ValueError(f"{market}: {entry['rules_error']}")
        unresolved = set(entry.get("required_context", [])) - {
            "htx_limit_price_reference_required",
            "gate_two_minute_trade_average_required",
        }
        if unresolved:
            raise ValueError(f"{market}: unsupported rule context: {', '.join(sorted(unresolved))}")

    def tick(side: str) -> str:
        raw = entry.get(f"{side}_precision")
        mode = entry.get(f"{side}_precision_mode", entry.get("precision_mode"))
        if raw is None:
            raise ValueError(f"{market}: missing {side} precision")
        value = number(raw)
        if mode == "decimal_places" and value == int(value) and -12 <= value <= 12:
            value = Decimal(10) ** -int(value)
        elif mode != "tick_size":
            raise ValueError(f"{market}: funded replay does not support {mode} precision")
        if not Decimal("1e-12") <= value <= Decimal("1e12"):
            raise ValueError(f"{market}: unsupported {side} increment")
        return decimal_text(value)

    limits = entry.get("order_limits", entry.get("limits"))
    if not isinstance(limits, dict):
        raise ValueError(f"{market}: missing amount and notional limits")

    def limit(kind: str, bound: str) -> str | None:
        raw = limits.get(kind, {}).get(bound)
        if raw is None:
            return None
        value = number(raw)
        if value < 0:
            raise ValueError(f"{market}: negative market limit")
        return decimal_text(value)

    amount_min, cost_min = limit("amount", "min"), limit("cost", "min")
    if amount_min is None and cost_min is None:
        raise ValueError(f"{market}: neither minimum amount nor minimum notional is saved")
    price_tick, amount_tick = tick("price"), tick("amount")
    with localcontext() as context:
        context.prec = 96
        product = number(price_tick) * number(amount_tick)
        exponent = min(
            int(number(quote_quantum).as_tuple().exponent), int(product.as_tuple().exponent)
        )
        accounting_quote = Decimal(1).scaleb(exponent)
    return {
        "exchange": venue,
        "symbol": symbol,
        "spot": True,
        "source": "saved_ccxt_rules_applied_to_history",
        "fetched_utc": snapshot["fetched_utc"],
        "ccxt_version": snapshot.get("ccxt_version"),
        "historical_rules_verified": False,
        "historical_notice": HISTORICAL_NOTICE,
        "rules_schema": entry.get("rules_schema"),
        "source_fields": entry.get("source_fields"),
        "exchange_specific_fields": entry.get("info"),
        "required_context": entry.get("required_context", []),
        "price_reference_assumption": (
            "mean of two consecutive completed minute closes; "
            "Gate two-minute average trade-price history unavailable"
            if venue == "gate"
            else "last completed candle close; exchange reference history unavailable"
        )
        if entry.get("price_protection")
        else None,
        "values": {
            "price_tick": price_tick,
            "amount_tick": amount_tick,
            "base_quantum": amount_tick,
            "quote_quantum": decimal_text(accounting_quote),
            "price_min": limit("price", "min") or price_tick,
            "price_max": limit("price", "max"),
            "buy_amount_min": limit("buy_amount", "min")
            if "buy_amount" in limits
            else amount_min or "0",
            "sell_amount_min": limit("sell_amount", "min")
            if "sell_amount" in limits
            else amount_min or "0",
            "buy_amount_max": limit("buy_amount", "max")
            if "buy_amount" in limits
            else limit("amount", "max"),
            "sell_amount_max": limit("sell_amount", "max")
            if "sell_amount" in limits
            else limit("amount", "max"),
            "buy_cost_min": cost_min or "0",
            "sell_cost_min": cost_min or "0",
            "buy_cost_max": limit("cost", "max"),
            "sell_cost_max": limit("cost", "max"),
            **entry.get("price_protection", {}),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded public HTX/Gate spot-rule refresh")
    parser.add_argument("venue", choices=("htx", "gate"))
    parser.add_argument("--directory", type=Path, default=SNAPSHOTS)
    args = parser.parse_args()
    value = asyncio.run(refresh_snapshot(args.venue, args.directory))
    refused = sum(bool(row.get("rules_error")) for row in value["markets"].values())
    print(json.dumps({key: item for key, item in value.items() if key != "markets"}))
    print(f"Saved {len(value['markets'])} spot entries; {refused} have explicit rule refusals")


if __name__ == "__main__":
    main()
