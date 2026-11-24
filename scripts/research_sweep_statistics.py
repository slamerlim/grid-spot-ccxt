"""Independent Fraction audit of the research sweep's monthly statistics.

Used on small deterministic validation fixtures. It reconstructs accounting
from the oracle's trade records and original observations, rather than using
engine chart samples or its calendar/moment implementation.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import struct
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from fractions import Fraction as F
from pathlib import Path
from typing import Any

from scripts.research_sweep_reference import reference


def calendar_fixtures(directory: Path) -> list[dict[str, Any]]:
    """Tiny invented sources spanning leap February and empty calendar months."""
    directory.mkdir(parents=True)
    dates = (
        "2024-01-01T00:00",
        "2024-01-31T23:59",
        "2024-02-01T00:00",
        "2024-02-15T12:00",
        "2024-02-29T23:59",
        "2024-03-01T00:00",
        "2024-03-31T23:59",
        "2024-04-01T00:00",
        "2024-04-30T23:59",
        "2024-07-01T00:00",
        "2024-07-31T23:59",
        "2024-08-15T12:00",
    )
    prices = (100, 120, 90, 115, 80, 105, 140, 75, 100, 115, 80, 85)
    result = []
    for index, venue in enumerate(
        ("okx", "gate", "htx", "kucoin", "xt", "bingx", "bitfinex", "okx")
    ):
        bars = []
        for offset, date in enumerate(dates):
            if index == 2 and offset in (2, 4, 6):
                continue
            timestamp = int(datetime.fromisoformat(date).replace(tzinfo=UTC).timestamp()) * 1000
            price = 100 if index == 7 else prices[(offset + index) % len(prices)]
            bars.append((timestamp, price, price + (index % 2), price, price))
        payload = b"".join(struct.pack("<qffff", *bar) for bar in bars)
        path = directory / f"{index}.bin"
        path.write_bytes(payload)
        result.append(
            {
                "id": f"{venue}:Calendar{index}/USDT",
                "venue": venue,
                "symbol": f"Calendar{index}/USDT",
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "records": len(bars),
                "first_ms": bars[0][0],
                "last_ms": bars[-1][0],
                "rules": {
                    "price_mode": "significant" if venue == "bitfinex" else "tick",
                    "price_digits": 5,
                    "max_price_decimals": 8,
                    "price_tick": "1e-40" if index == 0 else "0.01",
                    "amount_tick": "0.00000001" if venue == "bitfinex" else "1",
                    "min_amount": "1",
                    "min_cost": "0",
                },
            }
        )
    return result


def month_start(timestamp: int) -> int:
    value = datetime.fromtimestamp(timestamp / 1000, UTC)
    return calendar.timegm((value.year, value.month, 1, 0, 0, 0)) * 1000


def month_end(timestamp: int) -> int:
    value = datetime.fromtimestamp(timestamp / 1000, UTC)
    year, month = (value.year + 1, 1) if value.month == 12 else (value.year, value.month + 1)
    return calendar.timegm((year, month, 1, 0, 0, 0)) * 1000


def _risk_check(row: dict[str, Any], samples: list[F], scale: int) -> None:
    count = len(samples)
    total = sum(samples, F())
    square = sum((value * value for value in samples), F())
    downside = sum((value * value for value in samples if value < 0), F())
    if (
        row["sample_months"] != count
        or F(int(row["monthly_sum_units"]), scale) != total
        or F(int(row["monthly_square_sum_units"]), scale**2) != square
        or F(int(row["monthly_downside_square_sum_units"]), scale**2) != downside
    ):
        raise ValueError("independent monthly moment mismatch")
    for name in ("sharpe", "sortino"):
        unavailable = "insufficient_months"
        expected = None
        if count >= 2:
            denominator = (
                count * (count * square - total * total) if name == "sharpe" else count * downside
            )
            unavailable = "zero_variance" if name == "sharpe" else "no_downside"
            if denominator:
                expected = 12 * total * total * (count - 1 if name == "sharpe" else 1) / denominator
        if expected is None:
            if (
                row[name] is not None
                or row[name + "_squared"] is not None
                or row[name + "_status"] != unavailable
            ):
                raise ValueError("independent monthly unavailable-risk mismatch")
            continue
        if F(row[name + "_squared"]) != expected or row[name + "_status"] != "available":
            raise ValueError("independent exact risk-ratio mismatch")
        with localcontext() as context:
            context.prec = 90
            exact = (Decimal(expected.numerator) / Decimal(expected.denominator)).sqrt()
            if total < 0:
                exact = -exact
            if abs(Decimal(row[name]) - exact) >= Decimal("1e-24"):
                raise ValueError("independent displayed risk-ratio precision mismatch")


def verify_performance(plan: dict[str, Any], directory: Path) -> dict[str, int]:
    """Check all scope/month money, net closure groups and cumulative ratios."""
    summary = json.loads((directory / "summary.json").read_text())
    expected = reference(plan)
    sources = sorted(
        plan["sources"], key=lambda source: (source["venue"], source["symbol"], source["id"])
    )
    begin = plan.get("start_ms", min(source["first_ms"] for source in sources))
    end = plan.get("end_ms", max(source["last_ms"] for source in sources) + 60_000)
    if "max_records" in plan or summary.get("duration_stopped", False):
        end = min(end, int(summary["last_ms"]) + 1)
    marks: dict[int, dict[str, F]] = defaultdict(dict)
    for source in sources:
        for record in struct.iter_unpack("<qffff", Path(source["path"]).read_bytes()):
            if begin <= record[0] < end:
                for phase, price in enumerate(record[1:]):
                    marks[record[0] + phase * 1000][source["id"]] = F(price)
    checked = sum(
        _verify_configuration(
            sources,
            begin,
            end,
            marks,
            config,
            expected[config["id"]],
            summary.get("statistics_calendar_scope") == "venue_own_observed_span_v1",
        )
        for config in summary["configurations"]
    )
    return {"monthly_scope_rows_checked": checked, "configurations": len(summary["configurations"])}


def _verify_configuration(
    sources: list[dict[str, Any]],
    begin: int,
    end: int,
    marks: dict[int, dict[str, F]],
    config: dict[str, Any],
    item: dict[str, Any],
    own_calendar: bool,
) -> int:
    source_venue = {source["id"]: source["venue"] for source in sources}
    venues = sorted(set(source_venue.values()))
    checked = 0
    trades: dict[int, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for source_id, state in item["pairs"].items():
        for trade in state.trades:
            trades[trade["timestamp_ms"]].append((source_id, trade))
    ledger = {
        source["id"]: {
            name: F()
            for name in (
                "spent",
                "qty",
                "mark",
                "realized",
                "fees",
                "buy_fees",
                "buys",
                "sells",
            )
        }
        for source in sources
    }
    scope_ids = {
        "total": list(ledger),
        **{venue: [key for key in ledger if source_venue[key] == venue] for venue in venues},
    }

    def snapshot(scope: str) -> dict[str, F]:
        members = [ledger[key] for key in scope_ids[scope]]
        spent = sum((state["spent"] for state in members), F())
        realized = sum((state["realized"] for state in members), F())
        fees = sum((state["fees"] for state in members), F())
        value = sum((state["qty"] * state["mark"] for state in members), F())
        return {
            "spent": spent,
            "realized_gross": realized,
            "fees": fees,
            "total_pnl": realized + value - spent - fees,
            "buy_count": sum((state["buys"] for state in members), F()),
            "sell_count": sum((state["sells"] for state in members), F()),
        }

    baselines = {scope: snapshot(scope) for scope in scope_ids}
    closing: dict[str, list[F]] = {scope: [] for scope in scope_ids}
    peaks = {scope: (F(), 0) for scope in scope_ids}
    rows: dict[str, list[dict[str, Any]]] = {scope: [] for scope in scope_ids}
    scope_bounds: dict[str, tuple[int, int]] = {"total": (begin, end)}
    for venue in venues:
        if not own_calendar:
            scope_bounds[venue] = begin, end
            continue
        own_sources = [source for source in sources if source["venue"] == venue]
        observed = [
            source
            for source in own_sources
            if source["last_ms"] >= begin and source["first_ms"] < end
        ]
        if not observed:
            empty_at = begin if min(source["first_ms"] for source in own_sources) < begin else end
            scope_bounds[venue] = empty_at, empty_at
            continue
        first = min(source["first_ms"] for source in observed)
        last = max(source["last_ms"] for source in observed) + 60_000
        start = min(max(first, begin), end)
        stop = max(begin, min(last, end))
        scope_bounds[venue] = start, max(start, stop)
    current = {scope: bounds[0] for scope, bounds in scope_bounds.items()}
    historical_peaks = {scope: F() for scope in scope_ids}
    monthly_drawdowns = {scope: (F(), 0) for scope in scope_ids}
    expected_drawdowns: dict[str, list[tuple[F, int]]] = {scope: [] for scope in scope_ids}

    def finish(scope: str, boundary: int) -> None:
        point = snapshot(scope)
        expected_drawdowns[scope].append(monthly_drawdowns[scope])
        opening_drawdown = historical_peaks[scope] - point["total_pnl"]
        monthly_drawdowns[scope] = (opening_drawdown, boundary if opening_drawdown else 0)
        values = {
            name: point[name] - baselines[scope][name]
            for name in ("total_pnl", "realized_gross", "fees", "buy_count", "sell_count")
        }
        profits = sum((net for net in closing[scope] if net > 0), F())
        losses = -sum((net for net in closing[scope] if net < 0), F())
        rows[scope].append(
            {
                "start_ms": current[scope],
                "end_ms": boundary,
                "complete_month": current[scope] == month_start(current[scope])
                and boundary == month_end(current[scope]),
                "spent_open": baselines[scope]["spent"],
                "spent": point["spent"],
                "peak_spent": peaks[scope][0],
                "peak_spent_ms": peaks[scope][1],
                **values,
                "net_profit_closed": profits,
                "net_loss_closed": losses,
                "winning_closures": sum(net > 0 for net in closing[scope]),
                "losing_closures": sum(net < 0 for net in closing[scope]),
                "breakeven_closures": sum(net == 0 for net in closing[scope]),
                "profit_factor": profits / losses if losses else None,
                "profit_factor_status": "no_closures"
                if not closing[scope]
                else ("no_losses" if not losses else "available"),
            }
        )
        baselines[scope] = point
        closing[scope] = []
        peaks[scope] = (point["spent"], boundary if point["spent"] else 0)
        current[scope] = boundary

    for timestamp in sorted(marks):
        for scope in scope_ids:
            scope_end = scope_bounds[scope][1]
            while current[scope] < scope_end and timestamp >= month_end(current[scope]):
                boundary = month_end(current[scope])
                if boundary > scope_end:
                    break
                finish(scope, boundary)
        for source_id, price in marks[timestamp].items():
            ledger[source_id]["mark"] = price
        ordered = trades.get(timestamp, [])
        for forced in (False, True):
            for source_id, trade in ordered:
                if ("exit_scope" in trade) != forced:
                    continue
                state = ledger[source_id]
                cost, fee = F(trade["cost"]), F(trade["fee"])
                state["fees"] += fee
                if trade["side"] == "BUY":
                    state["spent"] += cost
                    state["qty"] += F(trade["quantity"])
                    state["buy_fees"] += fee
                    state["buys"] += 1
                else:
                    net = cost - state["spent"] - state["buy_fees"] - fee
                    for scope in ("total", source_venue[source_id]):
                        closing[scope].append(net)
                    state["realized"] += cost - state["spent"]
                    state["spent"] = state["qty"] = state["buy_fees"] = F()
                    state["sells"] += 1
            for scope in scope_ids:
                point = snapshot(scope)
                spent = point["spent"]
                if spent > peaks[scope][0]:
                    peaks[scope] = spent, timestamp
                historical_peaks[scope] = max(historical_peaks[scope], point["total_pnl"])
                drawdown = historical_peaks[scope] - point["total_pnl"]
                if drawdown > monthly_drawdowns[scope][0]:
                    monthly_drawdowns[scope] = drawdown, timestamp
    for scope in scope_ids:
        scope_end = scope_bounds[scope][1]
        while current[scope] < scope_end and scope_end >= month_end(current[scope]):
            finish(scope, month_end(current[scope]))
        if current[scope] < scope_end:
            finish(scope, scope_end)
    actual = config.get("performance", {})
    if actual.get("schema") != "grid-monthly-performance-v1":
        raise ValueError("missing monthly performance output")
    actual_scopes = {
        "total": actual["total"],
        **{row["venue"]: row for row in actual["venues"]},
    }
    if set(actual_scopes) != set(scope_ids):
        raise ValueError("monthly performance scope coverage mismatch")
    for scope, actual_scope in actual_scopes.items():
        wanted = rows[scope]
        if len(actual_scope["monthly"]) != len(wanted):
            raise ValueError("monthly performance calendar coverage mismatch")
        if "detail" in config:
            detail = config["detail"]
            actual_risk = (
                detail["total"]
                if scope == "total"
                else next(row["monthly"] for row in detail["venues"] if row["venue"] == scope)
            )
            for got, (drawdown, timestamp) in zip(
                actual_risk, expected_drawdowns[scope], strict=True
            ):
                if F(got["max_drawdown"]) != drawdown or got["max_drawdown_ms"] != timestamp:
                    raise ValueError(f"independent monthly drawdown mismatch: {scope}")
        samples: list[F] = []
        for got, want in zip(actual_scope["monthly"], wanted, strict=True):
            for field, value in want.items():
                matches = (
                    got[field] == value
                    if value is None or isinstance(value, (str, bool))
                    else F(str(got[field])) == value
                )
                if not matches:
                    raise ValueError(
                        f"independent monthly mismatch {config['id']} {scope} {field}: "
                        f"{got[field]} != {value}"
                    )
            if want["complete_month"]:
                samples.append(want["total_pnl"])
            _risk_check(got, samples, int(actual_scope["money_scale_denominator"]))
            checked += 1
        overall = actual_scope["overall"]
        _risk_check(overall, samples, int(actual_scope["money_scale_denominator"]))
        for field in (
            "total_pnl",
            "realized_gross",
            "fees",
            "buy_count",
            "sell_count",
            "net_profit_closed",
            "net_loss_closed",
            "winning_closures",
            "losing_closures",
            "breakeven_closures",
        ):
            if F(str(overall[field])) != sum((F(row[field]) for row in wanted), F()):
                raise ValueError(f"independent overall performance mismatch: {field}")
        profit = sum((row["net_profit_closed"] for row in wanted), F())
        loss = sum((row["net_loss_closed"] for row in wanted), F())
        if (
            (overall["profit_factor"] is None) != (loss == 0)
            or loss
            and F(overall["profit_factor"]) != profit / loss
        ):
            raise ValueError("independent overall profit factor mismatch")
    return checked
