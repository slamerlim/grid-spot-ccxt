"""Historical USDT reporting over unchanged, independently verified research runs.

Only completed one-minute closes are observable in this archive. Native trading
units stay unchanged. Historical-cost trade PnL and revalued native balances are
different measures; neither is funded NAV for the reference's unfunded model.
"""

from __future__ import annotations

import hashlib
import math
import mmap
import struct
import threading
from collections import OrderedDict
from contextlib import ExitStack
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, cast

from scripts.backtest_legacy_n import ArchiveCatalog, review
from scripts.backtest_totals import decimal_text, identical_inputs, number, timestamp
from scripts.module1_study import read_json
from scripts.module1_verify_sources import identity

REVISION = "historical-usdt-v2"
MINUTE = 60_000
MAX_POINTS = 2000
BAR = struct.Struct("<qffff")
TRADE = struct.Struct("<q7dII")
BRIDGES = ("BTC", "ETH", "USDC")
METRICS = (
    "pnl_usdt",
    "realized_pnl_usdt",
    "unrealized_pnl_usdt",
    "position_value_usdt",
    "position_cost_usdt",
    "fees_usdt",
    "cash_value_usdt",
    "translated_native_pnl_usdt",
)
# Disposable validation metadata only, never candle arrays or financial state.
_validated: OrderedDict[tuple[str, tuple[int, ...]], dict[str, Any]] = OrderedDict()
_validation_lock = threading.Lock()


class Records:
    def __init__(self, path: Path, layout: struct.Struct) -> None:
        self.path, self.layout = path, layout
        self.before = identity(path)
        if path.is_symlink() or self.before[2] % layout.size:
            raise ValueError(f"Invalid historical record file: {path.name}")
        self.count = self.before[2] // layout.size
        self.data: mmap.mmap | None = None
        if self.count:
            with path.open("rb") as stream:
                self.data = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)

    def close(self) -> None:
        if self.data is not None:
            self.data.close()

    def unchanged(self) -> None:
        if identity(self.path) != self.before:
            raise ValueError(f"Historical input changed: {self.path.name}")

    def at(self, index: int) -> tuple[Any, ...]:
        if self.data is None or not 0 <= index < self.count:
            raise ValueError("Historical record index is outside the source")
        return self.layout.unpack_from(self.data, index * self.layout.size)

    def before_time(self, moment: int) -> int:
        """First index strictly after moment; no later row may inform a valuation."""
        low, high = 0, self.count
        while low < high:
            middle = (low + high) // 2
            if self.at(middle)[0] <= moment:
                low = middle + 1
            else:
                high = middle
        return low

    def validate(self, expected_hash: str | None = None) -> dict[str, Any]:
        key = str(self.path), self.before
        with _validation_lock:
            cached = _validated.get(key)
            if cached is not None:
                _validated.move_to_end(key)
        if cached is not None:
            if expected_hash is not None and cached["sha256"] != expected_hash:
                raise ValueError("Historical source hash differs from the saved run")
            return cached
        digest = hashlib.sha256()
        if self.data is not None:
            for offset in range(0, self.before[2], 1024 * 1024):
                digest.update(self.data[offset : offset + 1024 * 1024])
        observed = digest.hexdigest()
        if expected_hash is not None:
            # The retained engine validated every native source row before replay.
            if observed != expected_hash:
                raise ValueError("Historical source hash differs from the saved run")
        else:
            if self.count == 0:
                raise ValueError("Historical conversion source is empty")
            previous = 0
            for index in range(self.count):
                moment, opened, high, low, close = self.at(index)
                if (
                    moment <= previous
                    or moment % MINUTE
                    or not all(math.isfinite(p) and p > 0 for p in (opened, high, low, close))
                    or low > min(opened, close)
                    or high < max(opened, close)
                    or low > high
                ):
                    raise ValueError(f"Invalid conversion candle: {self.path.name}, row {index}")
                previous = moment
        self.unchanged()
        result = {
            "file": self.path.name,
            "sha256": observed,
            "identity": list(self.before),
            "records": self.count,
            "source_status": "UNVERIFIED SOURCE / ASSUMPTION-BASED",
        }
        with _validation_lock:
            _validated[key] = result
            while len(_validated) > 256:
                _validated.popitem(last=False)
        return result

    def close_at(self, moment: int, *, exact: bool = False) -> Decimal | None:
        index = self.before_time(moment - MINUTE) - 1
        if index < 0:
            return None
        row = self.at(index)
        if row[0] + MINUTE != moment:
            return None
        return Decimal.from_float(row[4]) if exact else number(row[4])


def routes(catalog: ArchiveCatalog, venue: str, quote: str) -> list[list[tuple[Path, bool]]]:
    """Direct, inverse, then at most two same-venue legs; no assumed stablecoin pegs."""
    if quote == "USDT":
        return [[]]

    def edge(base: str, counter: str) -> list[tuple[Path, bool]]:
        choices = []
        for symbol, inverse in ((f"{base}/{counter}", False), (f"{counter}/{base}", True)):
            entry = catalog.rows.get(f"{venue}:{symbol}")
            if entry is not None:
                choices.append((catalog.root / entry["file"], inverse))
        return choices

    result = [[leg] for leg in edge(quote, "USDT")]
    for bridge in BRIDGES:
        if bridge != quote:
            result.extend(
                [first, second] for first in edge(quote, bridge) for second in edge(bridge, "USDT")
            )
    return result


class Prices:
    def __init__(self, catalog: ArchiveCatalog, *, exact: bool = False) -> None:
        self.catalog = catalog
        self.exact = exact
        self.opened: OrderedDict[Path, Records] = OrderedDict()
        self.evidence: dict[str, dict[str, Any]] = {}
        self.errors: dict[str, str] = {}
        self.recent: tuple[str, str, int, Decimal | None] | None = None
        self.last_route: list[str] = []
        self.recent_route: list[str] = []

    def close(self) -> None:
        for source in self.opened.values():
            source.close()

    def source(self, path: Path) -> Records:
        if path.name in self.errors:
            raise ValueError(self.errors[path.name])
        if path in self.opened:
            self.opened.move_to_end(path)
            return self.opened[path]
        source = Records(path, BAR)
        try:
            self.evidence[path.name] = source.validate()
        except (OSError, ValueError):
            source.close()
            raise
        self.opened[path] = source
        while len(self.opened) > 4:
            self.opened.popitem(last=False)[1].close()
        return source

    def rate(self, venue: str, quote: str, moment: int) -> Decimal | None:
        if quote == "USDT":
            self.last_route = []
            return Decimal(1)
        if self.recent is not None and self.recent[:3] == (venue, quote, moment):
            self.last_route = list(self.recent_route)
            return self.recent[3]
        answer = None
        self.last_route = []
        for route in routes(self.catalog, venue, quote):
            rate = Decimal(1)
            for path, inverse in route:
                try:
                    close = self.source(path).close_at(moment, exact=self.exact)
                except (OSError, ValueError) as exc:
                    self.errors[path.name] = str(exc)
                    close = None
                if close is None:
                    break
                rate *= Decimal(1) / close if inverse else close
            else:
                answer = rate
                self.last_route = [
                    ("1 / " if inverse else "") + path.name for path, inverse in route
                ]
                break
        self.recent = venue, quote, moment, answer
        self.recent_route = list(self.last_route)
        return answer

    def unchanged(self) -> None:
        for name, evidence in self.evidence.items():
            if list(identity(self.catalog.root / name)) != evidence["identity"]:
                raise ValueError("Historical conversion source changed during valuation")


def dependencies(catalog: ArchiveCatalog, markets: list[str]) -> list[Path]:
    selected = {catalog.root}
    for market in markets:
        venue, symbol = market.split(":", 1)
        # Funded equity marks base holdings independently of quote cash.
        # Both sets of routes must invalidate saved backend reports.
        selected.update(
            path
            for asset in symbol.split("/")
            for route in routes(catalog, venue, asset)
            for path, _ in route
        )
        entry = catalog.rows.get(market)
        if entry is not None:
            selected.add(catalog.root / entry["file"])
    return sorted(selected)


def pair_values(
    market: str, folder: Path, moments: list[int], prices: Prices
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    summary, config = review(folder), read_json(folder / "config.json")
    venue, symbol = market.split(":", 1)
    base, quote = symbol.split("/")
    entry = prices.catalog.rows.get(market)
    if entry is None or config["dataset"] != str(prices.catalog.root / entry["file"]):
        raise ValueError("Valuation source differs from the saved market")
    source_path = prices.catalog.root / entry["file"]
    terminal_time = summary["last_ts"] + MINUTE
    times = sorted(set([*moments, terminal_time]))
    results: dict[int, dict[str, Any]] = {}
    with ExitStack() as stack:
        native = Records(source_path, BAR)
        stack.callback(native.close)
        evidence = native.validate(config["dataset_sha256"])
        trades = Records(folder / "trades.bin", TRADE)
        stack.callback(trades.close)
        receipt = read_json(folder / "receipt.json")
        trades.validate(receipt["hashes"]["trades.bin"])
        if trades.count != summary["trades"]:
            raise ValueError("Retained trade count changed")
        cursor = 0
        position = cost = realized_native = Decimal(0)
        historical_cost: Decimal | None = Decimal(0)
        realized: Decimal | None = Decimal(0)
        fees: Decimal | None = Decimal(0)
        for moment in times:
            while cursor < trades.count:
                row = trades.at(cursor)
                if row[0] >= moment:
                    break
                if row[8] not in (0, 1) or not all(math.isfinite(v) for v in row[1:8]):
                    raise ValueError("Invalid retained trade")
                # A trade's synthetic intra-minute offset cannot see that minute's close.
                # A USDT/QUOTE execution itself prices QUOTE in USDT at this event.
                # Its USDT base quantity needs no estimated FX conversion.
                denominator = number(row[1]) if base == "USDT" else None
                rate = (
                    prices.rate(venue, quote, row[0] // MINUTE * MINUTE)
                    if denominator is None
                    else None
                )
                notional, native_fee = map(number, (row[3], row[4]))
                fee = (
                    native_fee / denominator
                    if denominator is not None
                    else native_fee * rate
                    if rate is not None
                    else None
                )
                fees = fees + fee if fees is not None and fee is not None else None
                if row[8] == 0:
                    added = (
                        (notional + native_fee) / denominator
                        if denominator is not None
                        else (notional + native_fee) * rate
                        if rate is not None
                        else None
                    )
                    historical_cost = (
                        historical_cost + added
                        if historical_cost is not None and added is not None
                        else None
                    )
                else:
                    fraction = number(row[2]) / position if position and row[5] != 0 else Decimal(1)
                    proceeds = (
                        (notional - native_fee) / denominator
                        if denominator is not None
                        else (notional - native_fee) * rate
                        if rate is not None
                        else None
                    )
                    removed = historical_cost * fraction if historical_cost is not None else None
                    realized = (
                        realized + proceeds - removed
                        if realized is not None and proceeds is not None and removed is not None
                        else None
                    )
                    historical_cost = (
                        historical_cost - removed
                        if historical_cost is not None and removed is not None
                        else None
                    )
                    if row[5] == 0:
                        historical_cost = Decimal(0)
                position, cost, realized_native = map(number, (row[5], row[6], row[7]))
                cursor += 1
            if moment <= summary["first_ts"]:
                results[moment] = {key: Decimal(0) for key in METRICS}
                results[moment].update(
                    position_qty=Decimal(0),
                    rate_usdt=None,
                    price_usdt=None,
                    complete=True,
                    active=False,
                )
                continue
            rate = prices.rate(venue, quote, moment)
            close = native.close_at(moment)
            cash = realized_native - cost * Decimal("1.002")
            inventory = (
                position
                if base == "USDT"
                else position * close * rate
                if position and close is not None and rate is not None
                else Decimal(0)
                if not position
                else None
            )
            unrealized = (
                inventory - historical_cost
                if inventory is not None and historical_cost is not None
                else None
            )
            pnl = realized + unrealized if realized is not None and unrealized is not None else None
            cash_value = cash * rate if rate is not None else Decimal(0) if not cash else None
            translated = (
                cash_value + inventory if cash_value is not None and inventory is not None else None
            )
            results[moment] = dict(
                pnl_usdt=pnl,
                realized_pnl_usdt=realized,
                unrealized_pnl_usdt=unrealized,
                position_value_usdt=inventory,
                position_cost_usdt=historical_cost,
                fees_usdt=fees,
                cash_value_usdt=cash_value,
                translated_native_pnl_usdt=translated,
                position_qty=position,
                rate_usdt=rate,
                rate_timestamp_ms=moment if rate is not None else None,
                rate_source_files=list(prices.last_route),
                price_usdt=(
                    Decimal(1)
                    if base == "USDT"
                    else close * rate
                    if close is not None and rate is not None
                    else None
                ),
                complete=pnl is not None and translated is not None and fees is not None,
                active=True,
            )
        native.unchanged()
        trades.unchanged()
    terminal = {
        "market": market,
        "quote": quote,
        "timestamp_ms": terminal_time,
        "timestamp": timestamp(terminal_time),
        "source": evidence,
        "trades_included": trades.count,
        **results[terminal_time],
        "first_order_native": number(config["order_quote"]),
        "first_order_usdt_at_start": (
            number(config["order_quote"]) * initial_rate
            if (initial_rate := prices.rate(venue, quote, summary["first_ts"])) is not None
            else None
        ),
    }
    return [results[moment] for moment in moments], terminal


def text_values(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Nonfinite historical valuation")
        return decimal_text(value)
    if isinstance(value, dict):
        return {key: text_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [text_values(item) for item in value]
    return value


def report(
    children: list[tuple[str, Path, Path]],
    catalog: ArchiveCatalog,
    end_ms: int,
    *,
    at_ms: int | None = None,
) -> dict[str, Any]:
    if not children:
        raise ValueError("No successful matched pairs are available for USDT valuation")
    inputs = [(folder, review(folder)) for _, folder, _ in children]
    first = min(summary["first_ts"] + MINUTE for _, summary in inputs)
    last = max(end_ms, max(summary["last_ts"] + MINUTE for _, summary in inputs))
    if at_ms is not None:
        if at_ms % MINUTE or not first <= at_ms <= last:
            raise ValueError("Choose a completed whole-minute UTC boundary inside the run envelope")
        moments = [at_ms]
    else:
        interval = max(1, ((last - first) // MINUTE + MAX_POINTS - 2) // (MAX_POINTS - 1))
        moments = list(range(first, last + 1, interval * MINUTE))
        if moments[-1] != last:
            moments.append(last)
    combined: list[dict[str, Any]] = [
        {
            **{key: Decimal(0) for key in METRICS},
            "timestamp_ms": moment,
            "timestamp": timestamp(moment),
            "known_pairs": 0,
            "missing_pairs": 0,
            "known_subtotals": {key: Decimal(0) for key in METRICS},
        }
        for moment in moments
    ]
    pairs = []
    prices = Prices(catalog)
    try:
        with localcontext() as context:
            context.prec = 400
            for market, current, audited in children:
                if not identical_inputs([(current, review(current))], [(audited, review(audited))]):
                    raise ValueError("USDT comparison requires identical retained engine records")
                values, terminal = pair_values(market, current, moments, prices)
                terminal["identical_engines"] = True
                if at_ms is not None:
                    terminal["at"] = values[0]
                pairs.append(terminal)
                for total, value in zip(combined, values, strict=True):
                    if len(children) == 1:
                        total["price_usdt"] = value.get("price_usdt")
                    total["known_pairs" if value["complete"] else "missing_pairs"] += 1
                    for key in METRICS:
                        if value[key] is None:
                            total[key] = None
                        else:
                            total["known_subtotals"][key] += value[key]
                            if total[key] is not None:
                                total[key] += value[key]
            prices.unchanged()
            return cast(
                dict[str, Any],
                text_values(
                    {
                        "schema": REVISION,
                        "currency": "USDT",
                        "series": combined,
                        "pairs": pairs,
                        "conversion_sources": list(prices.evidence.values()),
                        "conversion_errors": prices.errors,
                        "requested_timestamp_ms": at_ms,
                        "identical_engines": True,
                        "funded_nav_usdt": None,
                        "basis": (
                            "Historical-cost PnL uses event-time costs and proceeds. "
                            "Trade FX comes from the preceding completed minute; "
                            "USDT-base executions provide their own quote conversion. "
                            "Inventory uses the requested completed native/FX closes. "
                            "Native balance translation separately revalues signed "
                            "quote cash and base inventory, including quote exposure. "
                            "Historical trade PnL excludes later quote-holding returns. "
                            "These unfunded runs have no funded NAV or ROI. "
                            "Only USDT itself is one; other currencies require "
                            "same-venue direct, inverse or two-leg archived prices. "
                            "No interpolation, stale carry or future FX is used. "
                            "Missing inputs make affected totals null. Known subtotals "
                            "are explicitly incomplete. Charts sample at most 2,000 "
                            "completed-minute states including all preceding trades; "
                            "they do not preserve all extrema or maximum drawdown. "
                            "Inspect any completed minute for exact timestamp detail. "
                            "OHLC cannot establish real millisecond values or fills. "
                            "Retained numbers use Decimal with precision 400."
                        ),
                    }
                ),
            )
    finally:
        prices.close()
