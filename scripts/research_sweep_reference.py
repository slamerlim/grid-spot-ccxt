"""Small independent Fraction oracle for the interview's research policy.

This deliberately does not reuse the funded reducer or the C++ quantizer.
It is for short deterministic validation, never archive-scale execution.
"""

from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from fractions import Fraction as F
from pathlib import Path
from typing import Any

VENUES = ("okx", "htx", "gate", "xt", "bingx", "kucoin", "bitfinex")
BASE_MS = 1_700_000_040_000
EXPANDED_CONFIGURATION_COUNT = 540 * 28 * 28


def matrix() -> list[dict[str, str]]:
    return [
        {"id": f"s{s:02d}-t{t:02d}", "step_pct": str(s / 2), "tp_pct": str(t / 2)}
        for s in range(1, 21)
        for t in range(4, 31)
    ]


def expanded_configuration(index: int) -> dict[str, str | None]:
    """Address one of the four-axis candidates without materializing the matrix."""
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError("configuration index must be an integer")
    if not 0 <= index < EXPANDED_CONFIGURATION_COUNT:
        raise IndexError("configuration index outside expanded matrix")
    base, axes = divmod(index, 28 * 28)
    step, tp = divmod(base, 27)
    exchange, global_ = divmod(axes, 28)
    step += 1
    tp += 4
    exchange = exchange + 3 if exchange else 0
    global_ = global_ + 3 if global_ else 0

    def decimal(half_steps: int) -> str:
        return f"{half_steps // 2}.{5 if half_steps % 2 else 0}"

    identity = f"s{step:02d}-t{tp:02d}"
    if exchange or global_:
        identity += f"-e{exchange:02d}-g{global_:02d}"
    return {
        "id": identity,
        "step_pct": decimal(step),
        "tp_pct": decimal(tp),
        "exchange_tp_pct": decimal(exchange) if exchange else None,
        "global_tp_pct": decimal(global_) if global_ else None,
    }


def expanded_matrix() -> Iterator[dict[str, str | None]]:
    """Yield all 423,360 candidates in a fixed order using bounded memory."""
    for index in range(EXPANDED_CONFIGURATION_COUNT):
        yield expanded_configuration(index)


def rounded(value: F, tick: F, up: bool = False) -> F:
    units = value / tick
    count = units.numerator // units.denominator
    if up and units.denominator != 1:
        count += 1
    return count * tick


def price_round(value: F, rules: dict[str, Any], up: bool = False) -> F:
    if rules["price_mode"] == "tick":
        tick = F(rules["price_tick"])
    else:
        power = F(1)
        exponent = 0
        while power > value:
            power /= 10
            exponent -= 1
        while power * 10 <= value:
            power *= 10
            exponent += 1
        tick = max(
            F(10) ** (exponent - int(rules["price_digits"]) + 1),
            F(1, 10 ** int(rules["max_price_decimals"])),
        )
    return rounded(value, tick, up)


@dataclass
class Position:
    rules: dict[str, Any]
    step: F
    tp: F
    depth: F = F(3, 5)
    floor_ratio: F = field(init=False)
    started: bool = False
    qty: F = F(0)
    spent: F = F(0)
    fees: F = F(0)
    realized: F = F(0)
    raw: F = F(0)
    floor: F = F(0)
    buy: F = F(0)
    sell: F = F(0)
    mark: F = F(0)
    last_ms: int = 0
    entry_ms: int = 0
    buys: int = 0
    sells: int = 0
    peak_spent: F = F(0)
    peak_spent_ms: int = 0
    pnl_peak: F = F(0)
    drawdown: F = F(0)
    max_drawdown_ms: int = 0
    longest: int = 0
    trades: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.depth not in {F(percent, 100) for percent in range(30, 61, 5)}:
            raise ValueError("grid depth must be 30..60 percent in steps of 5")
        self.floor_ratio = 1 - self.depth

    @property
    def pnl(self) -> F:
        return self.realized + self.qty * self.mark - self.spent - self.fees

    def next_buy(self, previous: F) -> None:
        while True:
            self.raw *= 1 - self.step
            level = price_round(self.raw, self.rules)
            if level < self.floor or level <= 0:
                self.buy = F(0)
                return
            if level != previous:
                self.buy = level
                return

    def fill_buy(self, price: F, timestamp: int) -> None:
        tick = F(self.rules["amount_tick"])
        qty = rounded(
            max(tick, F(self.rules["min_amount"]), F(self.rules["min_cost"]) / price),
            tick,
            True,
        )
        cost = qty * price
        if self.qty == 0:
            self.entry_ms = timestamp
        self.qty += qty
        self.spent += cost
        self.fees += cost / 500
        self.buys += 1
        self.sell = price_round(self.spent / self.qty * (1 + self.tp), self.rules, True)
        self.trades.append(
            {
                "timestamp_ms": timestamp,
                "side": "BUY",
                "price": price,
                "quantity": qty,
                "cost": cost,
                "fee": cost / 500,
            }
        )

    def point(self, value: F, timestamp: int) -> None:
        self.mark, self.last_ms = value, timestamp
        if not self.started:
            self.started = True
            self.raw, self.floor = value, value * self.floor_ratio
            self.fill_buy(value, timestamp)
            self.next_buy(value)
        while self.buy > 0 and value < self.buy:
            executed = self.buy
            self.fill_buy(executed, timestamp)
            self.next_buy(executed)
        if self.qty > 0 and value > self.sell:
            cost = self.qty * self.sell
            self.trades.append(
                {
                    "timestamp_ms": timestamp,
                    "side": "SELL",
                    "price": self.sell,
                    "quantity": self.qty,
                    "cost": cost,
                    "fee": cost / 500,
                }
            )
            self.fees += cost / 500
            self.realized += cost - self.spent
            self.longest = max(self.longest, timestamp - self.entry_ms)
            self.sells += 1
            self.qty = self.spent = F(0)
            self.raw, self.floor = self.sell, self.sell * self.floor_ratio
            self.next_buy(self.sell)
            self.sell = F(0)
        self.observe(timestamp)

    def observe(self, timestamp: int) -> None:
        if self.spent > self.peak_spent:
            self.peak_spent, self.peak_spent_ms = self.spent, timestamp
        self.pnl_peak = max(self.pnl_peak, self.pnl)
        if self.pnl_peak - self.pnl > self.drawdown:
            self.drawdown, self.max_drawdown_ms = self.pnl_peak - self.pnl, timestamp
        if self.qty:
            self.longest = max(self.longest, timestamp - self.entry_ms)

    def aggregate_exit(self, timestamp: int, scope: str) -> None:
        """Liquidate at the last mark, keeping its original observation time."""
        if self.qty:
            cost = self.qty * self.mark
            self.trades.append(
                {
                    "timestamp_ms": timestamp,
                    "side": "SELL",
                    "price": self.mark,
                    "quantity": self.qty,
                    "cost": cost,
                    "fee": cost / 500,
                    "exit_scope": scope,
                    "quote_timestamp_ms": self.last_ms,
                }
            )
            self.fees += cost / 500
            self.realized += cost - self.spent
            self.longest = max(self.longest, timestamp - self.entry_ms)
            self.sells += 1
            self.qty = self.spent = F(0)
        # Every member is reseeded, even one that was already flat or has never
        # had a quote. Only point() on a later real observation performs a buy.
        self.started = False
        self.raw = self.floor = self.buy = self.sell = F(0)
        self.observe(timestamp)

    def metrics(self, include_dates: bool = False) -> dict[str, Any]:
        report: dict[str, Any] = {
            "realized_gross": self.realized,
            "unrealized_gross": self.qty * self.mark - self.spent,
            "fees": self.fees,
            "total_pnl": self.pnl,
            "spent": self.spent,
            "peak_spent": self.peak_spent,
            "max_drawdown": self.drawdown,
            "open_qty": self.qty,
            "last_price_ms": self.last_ms,
            "longest_hold_ms": self.longest,
            "buy_count": self.buys,
            "sell_count": self.sells,
        }
        if include_dates:
            report.update(
                peak_spent_ms=self.peak_spent_ms,
                max_drawdown_ms=self.max_drawdown_ms,
                open_value=self.qty * self.mark,
                last_price=self.mark if self.mark > 0 else None,
                return_pct=self.pnl * 100 / self.peak_spent if self.peak_spent else None,
            )
        return report


@dataclass
class AggregateCycle:
    """Independent current-cycle accounting, separate from lifetime metrics."""

    baseline_realized_gross: F = F(0)
    baseline_fees: F = F(0)
    peak_spent: F = F(0)
    peak_spent_ms: int = 0
    completed_count: int = 0
    last_exit_ms: int = 0

    def observe(self, spent: F, timestamp: int) -> None:
        if spent > self.peak_spent:
            self.peak_spent, self.peak_spent_ms = spent, timestamp

    def net_after_exit(self, states: list[Position]) -> F:
        return (
            sum((state.pnl - state.qty * state.mark / 500 for state in states), F(0))
            - self.baseline_realized_gross
            + self.baseline_fees
        )

    def reached(self, threshold: F | None, states: list[Position]) -> bool:
        return (
            threshold is not None
            and self.peak_spent > 0
            and self.net_after_exit(states) >= self.peak_spent * threshold
        )

    def reset(self, states: list[Position], timestamp: int) -> None:
        if self.peak_spent > 0:
            self.completed_count += 1
        self.baseline_realized_gross = sum((state.realized for state in states), F(0))
        self.baseline_fees = sum((state.fees for state in states), F(0))
        self.peak_spent = F(0)
        self.peak_spent_ms = 0
        self.last_exit_ms = timestamp

    def report(self) -> dict[str, F | int]:
        return {
            "baseline_realized_gross": self.baseline_realized_gross,
            "baseline_fees": self.baseline_fees,
            "peak_spent": self.peak_spent,
            "peak_spent_ms": self.peak_spent_ms,
            "completed_count": self.completed_count,
            "last_exit_ms": self.last_exit_ms,
        }


@dataclass
class ScopeMetrics:
    peak_spent: F = F(0)
    peak_spent_ms: int = 0
    pnl_peak: F = F(0)
    max_drawdown: F = F(0)
    max_drawdown_ms: int = 0

    def observe(self, states: list[Position], timestamp: int) -> None:
        spent = sum((state.spent for state in states), F(0))
        pnl = sum((state.pnl for state in states), F(0))
        if spent > self.peak_spent:
            self.peak_spent, self.peak_spent_ms = spent, timestamp
        self.pnl_peak = max(self.pnl_peak, pnl)
        if self.pnl_peak - pnl > self.max_drawdown:
            self.max_drawdown = self.pnl_peak - pnl
            self.max_drawdown_ms = timestamp

    def report(self, states: list[Position]) -> dict[str, Any]:
        realized = sum((state.realized for state in states), F(0))
        spent = sum((state.spent for state in states), F(0))
        fees = sum((state.fees for state in states), F(0))
        value = sum((state.qty * state.mark for state in states), F(0))
        pnl = realized + value - spent - fees
        return {
            "realized_gross": realized,
            "unrealized_gross": value - spent,
            "fees": fees,
            "total_pnl": pnl,
            "spent": spent,
            "open_value": value,
            "peak_spent": self.peak_spent,
            "peak_spent_ms": self.peak_spent_ms,
            "return_pct": pnl * 100 / self.peak_spent if self.peak_spent else None,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_ms": self.max_drawdown_ms,
            "longest_hold_ms": max((state.longest for state in states), default=0),
            "buy_count": sum(state.buys for state in states),
            "sell_count": sum(state.sells for state in states),
        }


def trade_digest(config_id: str, source_id: str, trades: list[dict[str, Any]]) -> str:
    """Rebuild the exact byte-level per-pair chain independently of C++."""
    digest = bytes(32)
    for sequence, trade in enumerate(trades, 1):
        payload = {
            "config_id": config_id,
            "source_id": source_id,
            "sequence": sequence,
            **{
                key: f"{value.numerator}/{value.denominator}" if isinstance(value, F) else value
                for key, value in trade.items()
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(digest + encoded.encode()).digest()
    return digest.hex()


def aggregate_threshold(value: Any) -> F | None:
    if value is None:
        return None
    percent = F(value)
    if percent < 2 or percent > 15 or (percent * 2).denominator != 1:
        raise ValueError("aggregate TP must be null or 2..15 percent in steps of 0.5")
    return percent / 100


def reference(plan: dict[str, Any]) -> dict[str, Any]:
    """Replay a small manifest, preserving global phase and market tie order."""
    sources = sorted(plan["sources"], key=lambda row: (row["venue"], row["symbol"]))
    require_current_quotes = (
        plan.get("source_selection", {}).get("history_policy") == "gap_free_own_span"
    )
    groups: dict[int, list[tuple[dict[str, Any], tuple[float, ...]]]] = {}
    for source in sources:
        data = Path(source["path"]).read_bytes()
        for row in struct.iter_unpack("<qffff", data):
            ts = int(row[0])
            if plan.get("start_ms", ts) <= ts < plan.get("end_ms", ts + 1):
                groups.setdefault(ts, []).append((source, tuple(row[1:])))
    result: dict[str, Any] = {}
    for config in plan["configurations"]:
        states = {
            s["id"]: Position(
                s["rules"],
                F(config["step_pct"]) / 100,
                F(config["tp_pct"]) / 100,
                depth=F(config.get("grid_depth_pct", "60")) / 100,
            )
            for s in sources
        }
        members = list(states.values())
        venue_members = {
            venue: [states[source["id"]] for source in sources if source["venue"] == venue]
            for venue in sorted({source["venue"] for source in sources})
        }
        extended = (
            plan.get("matrix_kind") == "aggregate_tp_v1"
            or "exchange_tp_pct" in config
            or "global_tp_pct" in config
        )
        exchange_tp = aggregate_threshold(config.get("exchange_tp_pct"))
        global_tp = aggregate_threshold(config.get("global_tp_pct"))
        total_metrics = ScopeMetrics()
        venue_metrics = {venue: ScopeMetrics() for venue in venue_members}
        global_cycle = AggregateCycle()
        venue_cycles = {venue: AggregateCycle() for venue in venue_members}
        for ts, rows in sorted(groups.items()):
            for phase in range(4):
                timestamp = ts + phase * 1000
                for source, prices in rows:
                    states[source["id"]].point(F(prices[phase]), timestamp)
                total_metrics.observe(members, timestamp)
                if not extended:
                    continue
                for venue, scoped in venue_members.items():
                    venue_metrics[venue].observe(scoped, timestamp)
                global_cycle.observe(sum((state.spent for state in members), F(0)), timestamp)
                for venue, scoped in venue_members.items():
                    venue_cycles[venue].observe(
                        sum((state.spent for state in scoped), F(0)), timestamp
                    )

                def current_quotes(scoped: list[Position], current_ms: int = timestamp) -> bool:
                    return not require_current_quotes or all(
                        not state.qty or state.last_ms == current_ms for state in scoped
                    )

                if current_quotes(members) and global_cycle.reached(global_tp, members):
                    for state in members:
                        state.aggregate_exit(timestamp, "global")
                    global_cycle.reset(members, timestamp)
                    for venue, scoped in venue_members.items():
                        venue_cycles[venue].reset(scoped, timestamp)
                else:
                    for venue, scoped in venue_members.items():
                        if current_quotes(scoped) and venue_cycles[venue].reached(
                            exchange_tp, scoped
                        ):
                            for state in scoped:
                                state.aggregate_exit(timestamp, "venue")
                            venue_cycles[venue].reset(scoped, timestamp)
                # Closing fees can create a new lifetime drawdown at the same
                # timestamp. They do not erase pre-exit peaks or old-cycle fees.
                total_metrics.observe(members, timestamp)
                for venue, scoped in venue_members.items():
                    venue_metrics[venue].observe(scoped, timestamp)
        result[config["id"]] = {
            "pairs": states,
            "peak_spent": total_metrics.peak_spent,
            "max_drawdown": total_metrics.max_drawdown,
            "total_pnl": sum((s.pnl for s in states.values()), F(0)),
            "fees": sum((s.fees for s in states.values()), F(0)),
        }
        if extended:
            result[config["id"]].update(
                total=total_metrics.report(members),
                venues={
                    venue: venue_metrics[venue].report(scoped)
                    for venue, scoped in venue_members.items()
                },
                aggregate_cycles={
                    "global": global_cycle.report(),
                    "venues": [
                        {"venue": venue, **cycle.report()} for venue, cycle in venue_cycles.items()
                    ],
                },
            )
    return result


def fixture_sources(
    directory: Path,
    count: int = 8,
    minutes: int = 24,
    *,
    exact_records: bool = False,
    gap_free: bool = False,
) -> list[dict[str, Any]]:
    """Deterministic invented histories; never presented as exchange evidence."""
    if exact_records and minutes <= 0:
        raise ValueError("exact fixture record count must be positive")
    directory.mkdir(parents=True, exist_ok=True)
    sources = []
    values = (100, 100, 99, 98, 97, 35, 40, 80, 111, 120, 108, 106, 90, 150, 70, 70)
    # There are only 3 timestamp/gap patterns × 16 price phases. Preserve one
    # real file per source, but prepare/hash identical immutable bytes once.
    templates: dict[tuple[int, int], tuple[bytes, int, int, str]] = {}
    template_bytes = 0
    for index in range(count):
        venue = VENUES[index % len(VENUES)]
        path = directory / f"{venue}_USDT_TEST{index}.bin"
        key = (index % 3, index % len(values))
        template = templates.get(key)
        if template is None:
            buffer = bytearray()
            first = last = 0
            minute_limit = minutes + (minutes + 9) // 10 + 2 if exact_records else minutes
            for minute in range(minute_limit):
                if exact_records and len(buffer) == minutes * 24:
                    break
                if not gap_free and index % 3 == 1 and minute % 11 == 4:
                    continue
                ts = BASE_MS + (minute + index % 3) * 60_000
                price = values[(minute + index) % len(values)]
                if not first:
                    first = ts
                last = ts
                buffer.extend(struct.pack("<qffff", ts, price, price + 1, price - 1, price))
            if exact_records and len(buffer) != minutes * 24:
                raise ValueError("synthetic fixture did not reach exact source record count")
            payload = bytes(buffer)
            digest = hashlib.sha256(payload).hexdigest()
            template = payload, first, last, digest
            if template_bytes + len(payload) <= 64 * 1024**2:
                templates[key] = template
                template_bytes += len(payload)
        payload, first, last, digest = template
        path.write_bytes(payload)
        sources.append(
            {
                "id": f"{venue}:TEST{index}/USDT",
                "venue": venue,
                "symbol": f"TEST{index}/USDT",
                "path": str(path.resolve()),
                "sha256": digest,
                "records": len(payload) // 24,
                "first_ms": first,
                "last_ms": last,
                "rules": {
                    "price_mode": "tick",
                    "price_tick": "0.01",
                    "amount_tick": "0.001",
                    "min_amount": "0.001",
                    "min_cost": "5",
                },
            }
        )
    return sources


def verify_result(plan: dict[str, Any], directory: Path) -> dict[str, int]:
    expected = reference(plan)
    expected_pairs = {
        (config_id, source_id)
        for config_id, item in expected.items()
        for source_id in item["pairs"]
    }
    summary = json.loads((directory / "summary.json").read_text())
    actual_pairs = [
        json.loads(line) for line in (directory / "pairs.jsonl").read_text().splitlines()
    ]
    seen_pairs: set[tuple[str, str]] = set()
    for row in actual_pairs:
        key = row["config_id"], row["source_id"]
        if key not in expected_pairs or key in seen_pairs:
            raise ValueError("oracle pair roster has an unexpected or duplicate member")
        seen_pairs.add(key)
        position = expected[row["config_id"]]["pairs"][row["source_id"]]
        extended = "aggregate_cycles" in expected[row["config_id"]]
        for field_name, value in position.metrics(include_dates=extended).items():
            if (row[field_name] is not None or value is not None) and (
                row[field_name] is None or value is None or F(str(row[field_name])) != value
            ):
                raise ValueError(
                    f"oracle mismatch {row['config_id']} {row['source_id']} {field_name}"
                )
        if extended and row.get("trade_hash") != trade_digest(
            row["config_id"], row["source_id"], position.trades
        ):
            raise ValueError("oracle aggregate pair trade hash mismatch")
    if seen_pairs != expected_pairs or len(seen_pairs) != len(plan["sources"]) * len(
        plan["configurations"]
    ):
        raise ValueError("oracle pair coverage mismatch")
    seen_configs: set[str] = set()
    for row in summary["configurations"]:
        config_id = row["id"]
        if config_id not in expected or config_id in seen_configs:
            raise ValueError("oracle summary has an unexpected or duplicate configuration")
        seen_configs.add(config_id)
        for name in ("total_pnl", "fees", "peak_spent", "max_drawdown"):
            if F(row["total"][name]) != expected[row["id"]][name]:
                raise ValueError(f"oracle aggregate mismatch {row['id']} {name}")
        item = expected[config_id]
        if "aggregate_cycles" in item:

            def compare_numbers(
                got: dict[str, Any], want: dict[str, Any], label: str, identity: str = config_id
            ) -> None:
                for field_name, value in want.items():
                    actual_value = got.get(field_name)
                    if value is None:
                        matches = actual_value is None
                    elif isinstance(value, str):
                        matches = actual_value == value
                    else:
                        matches = actual_value is not None and F(str(actual_value)) == value
                    if not matches:
                        raise ValueError(f"oracle {label} mismatch {identity} {field_name}")

            compare_numbers(row["total"], item["total"], "portfolio")
            actual_venues = {venue["venue"]: venue for venue in row["venues"]}
            if len(actual_venues) != len(row["venues"]) or set(actual_venues) != set(
                item["venues"]
            ):
                raise ValueError("oracle venue coverage mismatch")
            for venue, metrics in item["venues"].items():
                compare_numbers(actual_venues[venue], metrics, f"venue {venue}")
            cycles = row.get("aggregate_cycles", {})
            compare_numbers(
                cycles.get("global", {}), item["aggregate_cycles"]["global"], "global cycle"
            )
            expected_cycles = {
                cycle["venue"]: cycle for cycle in item["aggregate_cycles"]["venues"]
            }
            actual_cycles = {cycle["venue"]: cycle for cycle in cycles.get("venues", [])}
            if len(actual_cycles) != len(cycles.get("venues", [])) or set(actual_cycles) != set(
                expected_cycles
            ):
                raise ValueError("oracle venue cycle coverage mismatch")
            for venue, cycle in expected_cycles.items():
                compare_numbers(actual_cycles[venue], cycle, f"venue cycle {venue}")
    if seen_configs != set(expected):
        raise ValueError("oracle summary configuration coverage mismatch")
    trace = directory / "trades.jsonl"
    checked_trades = 0
    if plan.get("trace") and not trace.is_file():
        raise ValueError("oracle required trade trace is missing")
    if trace.exists():
        actual: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for line in trace.read_text().splitlines():
            row = json.loads(line)
            key = row["config_id"], row["source_id"]
            if key not in expected_pairs:
                raise ValueError("oracle trade trace has an unexpected pair or configuration")
            actual.setdefault(key, []).append(row)
        for config_id, item in expected.items():
            for source_id, state in item["pairs"].items():
                trades = actual.get((config_id, source_id), [])
                if len(trades) != len(state.trades):
                    raise ValueError("oracle trade count mismatch")
                for sequence, (got, want) in enumerate(zip(trades, state.trades, strict=True), 1):
                    if got.get("sequence") != sequence:
                        raise ValueError("oracle trade sequence mismatch")
                    for key, value in want.items():
                        if (F(got[key]) if isinstance(value, F) else got[key]) != value:
                            raise ValueError(f"oracle trade mismatch {config_id} {source_id} {key}")
                    checked_trades += 1
    return {"pairs_checked": len(seen_pairs), "trades_checked": checked_trades}
