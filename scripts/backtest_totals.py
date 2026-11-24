"""Stream every retained trade and mark into bounded native-quote OHLC totals."""

from __future__ import annotations

import hashlib
import heapq
import math
import struct
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

from scripts import backtest_legacy_n as reference
from scripts.module1_study import read_json

METRICS = ("pnl", "realized_pnl", "unrealized_pnl", "fees", "buys", "sells")
CHART_METRICS = ("pnl", "position_cost", "drawdown")
TRADE = struct.Struct("<q7dII")
SERIES = struct.Struct("<qq16d")
CHUNK_RECORDS = 64
MAX_BUCKETS = 5000
Event = tuple[int, int, Decimal, Decimal, str]


def identical_inputs(
    current: list[tuple[Path, dict[str, Any]]], audited: list[tuple[Path, dict[str, Any]]]
) -> bool:
    fields = (*METRICS, "first_ts", "last_ts", "position_cost", "trades", "chart_points")
    for (left, a), (right, b) in zip(current, audited, strict=True):
        if any(a[field] != b[field] for field in fields):
            return False
        receipts = [read_json(folder / "receipt.json")["hashes"] for folder in (left, right)]
        if any(receipts[0][name] != receipts[1][name] for name in ("trades.bin", "series.bin")):
            return False
    return True


def timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def number(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite retained research value")
    return result


def decimal_text(value: Decimal) -> str:
    """Omit insignificant fractional zeroes without rounding a single digit."""
    mantissa, separator, exponent = str(value).partition("E")
    if "." in mantissa:
        mantissa = mantissa.rstrip("0").rstrip(".")
    return mantissa + separator + exponent


def records(
    path: Path, layout: struct.Struct, count: int, digest: str
) -> Iterator[tuple[Any, ...]]:
    """Bound buffers and descriptors even when thousands of pair streams coexist."""
    before = path.stat()
    if path.is_symlink() or before.st_size != count * layout.size:
        raise ValueError("Research record count changed during aggregation")
    hashed = hashlib.sha256()
    for offset in range(0, count, CHUNK_RECORDS):
        size = min(CHUNK_RECORDS, count - offset) * layout.size
        with path.open("rb") as stream:
            stream.seek(offset * layout.size)
            raw = stream.read(size)
        if len(raw) != size:
            raise ValueError("Research records changed during aggregation")
        hashed.update(raw)
        yield from layout.iter_unpack(raw)
    after = path.stat()
    if hashed.hexdigest() != digest or (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("Research records changed during aggregation")


def events(folder: Path, summary: dict[str, Any]) -> Iterator[Event]:
    receipt = read_json(folder / "receipt.json")
    first, last = summary["first_ts"], summary["last_ts"] + 3

    def trades() -> Iterator[Event]:
        previous = first
        for row in records(
            folder / "trades.bin", TRADE, summary["trades"], receipt["hashes"]["trades.bin"]
        ):
            moment, price, _quantity, _quote, _fee, quantity, cost, realized, side, _cycle = row
            if not previous <= moment <= last or side not in (0, 1):
                raise ValueError("Research trade order is invalid")
            if not all(math.isfinite(value) for value in row[1:8]):
                raise ValueError("Research trade values are nonfinite")
            previous = moment
            # Fixed 0.2% acquisition fees remain in the open position's basis.
            # These are reconstructed retained-binary64 marks, not original Real arithmetic.
            pnl = (
                number(realized)
                + number(quantity) * number(price)
                - number(cost) * Decimal("1.002")
            )
            yield moment, 0, pnl, number(cost), "trade"

    def closes() -> Iterator[Event]:
        previous = first - 1
        for row in records(
            folder / "series.bin", SERIES, summary["chart_points"], receipt["hashes"]["series.bin"]
        ):
            moment = row[1] - 60000 + 3
            if not previous < moment <= last or not all(math.isfinite(value) for value in row[2:]):
                raise ValueError("Research chart order or values are invalid")
            previous = moment
            yield moment, 1, number(row[9]), number(row[13]), "series"

    terminal = iter(
        [(last, 2, number(summary["pnl"]), number(summary["position_cost"]), "terminal")]
    )
    yield from heapq.merge(trades(), closes(), terminal, key=lambda event: event[:2])


def aggregate(
    inputs: list[tuple[Path, dict[str, Any]]], start_ms: int, interval_ms: int
) -> dict[str, Any]:
    result: dict[str, Any] = {
        metric: sum((number(summary[metric]) for _, summary in inputs), Decimal(0))
        for metric in METRICS
    }
    counts = {"trade": 0, "series": 0, "terminal": 0}
    states = [(Decimal(0), Decimal(0)) for _ in inputs]
    pnl = cost = high = Decimal(0)
    series: list[dict[str, Any]] = []

    def indexed(
        index: int, folder: Path, summary: dict[str, Any]
    ) -> Iterator[tuple[int, int, int, Decimal, Decimal, str]]:
        for moment, priority, pair_pnl, pair_cost, kind in events(folder, summary):
            yield moment, priority, index, pair_pnl, pair_cost, kind

    streams = [indexed(index, folder, summary) for index, (folder, summary) in enumerate(inputs)]
    merged = heapq.merge(*streams, key=lambda event: event[:3])
    for moment, _priority, index, pair_pnl, pair_cost, kind in merged:
        old_pnl, old_cost = states[index]
        pnl += pair_pnl - old_pnl
        cost += pair_cost - old_cost
        states[index] = pair_pnl, pair_cost
        counts[kind] += 1
        high = max(high, pnl)
        values = (pnl, cost, high - pnl)
        bucket_start = start_ms + (moment - start_ms) // interval_ms * interval_ms
        if not series or series[-1]["start_ms"] != bucket_start:
            series.append(
                {
                    "start_ms": bucket_start,
                    "end_ms": bucket_start + interval_ms,
                    "timestamp": timestamp(moment),
                    "events": 0,
                    **{
                        name + "_ohlc": [value] * 4
                        for name, value in zip(CHART_METRICS, values, strict=True)
                    },
                }
            )
        bar = series[-1]
        bar["timestamp"] = timestamp(moment)
        bar["events"] += 1
        for name, value in zip(CHART_METRICS, values, strict=True):
            ohlc = bar[name + "_ohlc"]
            ohlc[1], ohlc[2], ohlc[3] = max(ohlc[1], value), min(ohlc[2], value), value
    expected_trades = sum(summary["trades"] for _, summary in inputs)
    if counts["trade"] != expected_trades or len(series) > MAX_BUCKETS:
        raise ValueError("Research aggregation lost events or exceeded chart bounds")
    for bar in series:
        for name in CHART_METRICS:
            bar[name + "_ohlc"] = [decimal_text(value) for value in bar[name + "_ohlc"]]
            bar[name] = bar[name + "_ohlc"][3]
    for metric in METRICS:
        result[metric] = (
            int(result[metric]) if metric in ("buys", "sells") else decimal_text(result[metric])
        )
    result.update(
        series=series,
        trade_events_processed=counts["trade"],
        series_marks_processed=counts["series"],
        terminal_marks_processed=counts["terminal"],
        merged_events_processed=sum(counts.values()),
    )
    return result


def totals(children: Iterable[tuple[str, Path, Path]], end_ms: int) -> dict[str, Any]:
    """Include every successful pair, trade and retained close; never intersect histories."""
    groups: dict[str, dict[str, Any]] = {}
    with localcontext() as context:
        context.prec = 400
        for market, current_folder, audited_folder in children:
            quote = market.rsplit("/", 1)[-1]
            group = groups.setdefault(
                quote, {"quote": quote, "markets": [], "current": [], "audited": []}
            )
            group["markets"].append(market)
            for label, folder in (("current", current_folder), ("audited", audited_folder)):
                group[label].append((folder, reference.review(folder)))
        output = []
        for group in groups.values():
            summaries = [summary for _, summary in group["current"]]
            start_ms = min(summary["first_ts"] for summary in summaries)
            endpoints = [summary["last_ts"] + 3 for summary in summaries]
            span = max(end_ms, max(endpoints) + 1) - start_ms
            interval_ms = max(
                60000, ((span + MAX_BUCKETS * 60000 - 1) // (MAX_BUCKETS * 60000)) * 60000
            )
            synchronous = len(set(endpoints)) == 1
            group.update(
                aggregation="all_trade_events_and_retained_marks",
                terminal_basis="sum_of_pair_terminal_marks",
                terminal_timestamp=timestamp(endpoints[0]) if synchronous else None,
                terminal_earliest=timestamp(min(endpoints)),
                terminal_latest=timestamp(max(endpoints)),
                terminal_marks_synchronous=synchronous,
                chart_interval_ms=interval_ms,
                missing_minutes=sum(summary["missing_minutes"] for summary in summaries),
            )
            current_inputs, audited_inputs = group["current"], group["audited"]
            reuse = identical_inputs(current_inputs, audited_inputs)
            group["current"] = aggregate(current_inputs, start_ms, interval_ms)
            if reuse:
                # Identical full records imply identical derived charts. Recheck both
                # artifacts, then avoid interpreting millions of equal events twice.
                for folder, summary in audited_inputs:
                    if reference.review(folder) != summary:
                        raise ValueError("Audited inputs changed during aggregation")
                if not identical_inputs(current_inputs, audited_inputs):
                    raise ValueError("Reference records changed during aggregation")
                group["audited"] = group["current"]
            else:
                group["audited"] = aggregate(audited_inputs, start_ms, interval_ms)
            group["identical_aggregate_reused"] = reuse
            output.append(group)
    return {
        "mode": "exact_reference",
        "groups": output,
        "sampling_note": (
            "Every retained trade and chart close from every included successful pair "
            "is processed; "
            "OHLC buckets preserve their combined event extrema without thinning trades. "
            "Independent unfunded runs are grouped by native quote; no FX or shared wallet. "
            "Each pair contributes zero before its first observation and its last recorded mark "
            "between updates and after its history ends; these carried marks can be stale. "
            "Ending PnL sums all pair terminal summaries, even when their last timestamps differ. "
            "Trade marks are reconstructed from retained binary64 positions, prices and fixed 0.2% "
            "buy fees; retained closes and terminal summaries override them. Decimal arithmetic "
            "preserves retained values, not lost source or internal arithmetic precision. "
            "Equal synthetic timestamps use deterministic trade-before-close and market order. "
            "Combined OHLC and drawdown describe this trade/close-mark sequence, not all intrabar "
            "price movements or a synchronized real portfolio."
        ),
    }
