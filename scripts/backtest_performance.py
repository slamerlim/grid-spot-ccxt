"""Full-period/monthly statistics from every fill and exact daily boundaries.

Returns use a declared funded-capital baseline; the immutable reference itself
does not enforce that baseline. Missing observations are never dropped or filled.
"""

from __future__ import annotations

import json
from collections.abc import Generator, Iterable, Iterator, Mapping
from contextlib import ExitStack, closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal, localcontext
from pathlib import Path
from typing import Any, cast

from scripts import backtest_usdt as valuation
from scripts.backtest_legacy_n import ArchiveCatalog
from scripts.backtest_legacy_n import review as reference_review
from scripts.backtest_totals import identical_inputs, number, timestamp
from scripts.module1_study import read_json
from scripts.module1_verify_sources import identity, sha256

REVISION = "grid-performance-v2"
DAY = 86_400_000
ZERO = Decimal(0)


def month(moment: int) -> str:
    return datetime.fromtimestamp(moment / 1000, UTC).strftime("%Y-%m")


def periods(start: int, end: int) -> list[tuple[str, int, int]]:
    result = []
    cursor = start
    while cursor < end:
        date = datetime.fromtimestamp(cursor / 1000, UTC)
        boundary = datetime(date.year + (date.month == 12), date.month % 12 + 1, 1, tzinfo=UTC)
        stop = min(end, int(boundary.timestamp()) * 1000)
        result.append((month(cursor), cursor, stop))
        cursor = stop
    return [("Entire selected period", start, end), *result]


@dataclass
class Closures:
    count: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    unknown: int = 0
    profit: Decimal = ZERO
    loss: Decimal = ZERO

    def add(self, pnl: Decimal | None) -> None:
        self.count += 1
        if pnl is None:
            self.unknown += 1
        elif pnl > 0:
            self.wins += 1
            self.profit += pnl
        elif pnl < 0:
            self.losses += 1
            self.loss -= pnl
        else:
            self.breakeven += 1

    def merge(self, other: Closures) -> None:
        for key in ("count", "wins", "losses", "breakeven", "unknown", "profit", "loss"):
            setattr(self, key, getattr(self, key) + getattr(other, key))

    def metrics(self) -> dict[str, Any]:
        reasons = []
        if self.unknown:
            reasons.append("A completed position has missing historical USDT costs/proceeds")
            status = "unavailable"
        elif not self.count:
            reasons.append("No completed economic cycles")
            status = "no_completed_cycles"
        elif self.loss == 0:
            reasons.append("Profit Factor is undefined without realized losses")
            status = "no_losses"
        else:
            status = "finite" if self.profit else "zero"
        return {
            "completed_positions": self.count,
            "completed_cycles": self.count,
            "wins": self.wins,
            "losses": self.losses,
            "breakeven": self.breakeven,
            "unknown_positions": self.unknown,
            "winning_position_pnl_usdt": self.profit if not self.unknown else None,
            "losing_position_pnl_usdt": self.loss if not self.unknown else None,
            "profit_factor": self.profit / self.loss
            if status == "finite"
            else ZERO
            if status == "zero"
            else None,
            "profit_factor_status": status,
            "win_rate_pct": Decimal(100) * self.wins / self.count
            if self.count and not self.unknown
            else None,
            "win_rate_status": "unavailable"
            if self.unknown
            else "available"
            if self.count
            else "no_completed_cycles",
            "trade_metric_notes": reasons,
        }


@dataclass
class Drawdown:
    start: int
    end: int
    observations: int = 0
    missing: int = 0
    invalid: int = 0
    peak: Decimal | None = None
    peak_time: int | None = None
    worst: Decimal = ZERO
    worst_peak: Decimal | None = None
    worst_trough: Decimal | None = None
    worst_peak_time: int | None = None
    worst_trough_time: int | None = None

    def add(self, time: int, equity: Decimal | None) -> None:
        self.observations += 1
        if equity is None:
            self.missing += 1
            return
        if not equity.is_finite() or equity <= 0:
            self.invalid += 1
            return
        if self.peak is None:
            self.worst_peak = self.worst_trough = equity
            self.worst_peak_time = self.worst_trough_time = time
        if self.peak is None or equity > self.peak:
            self.peak, self.peak_time = equity, time
        loss = (equity - self.peak) / self.peak
        if loss < self.worst:
            self.worst = loss
            self.worst_peak, self.worst_trough = self.peak, equity
            self.worst_peak_time, self.worst_trough_time = self.peak_time, time

    def metrics(self) -> dict[str, Any]:
        expected = (self.end - self.start) // valuation.MINUTE + 1
        status = (
            "missing_valuation"
            if self.missing
            else "invalid_equity"
            if self.invalid
            else "incomplete_minute_curve"
            if self.observations != expected
            else "available"
        )
        valid = status == "available"
        return {
            "max_drawdown_pct": self.worst * 100 if valid else None,
            "max_drawdown_status": status,
            "drawdown_peak_ms": self.worst_peak_time if valid else None,
            "drawdown_trough_ms": self.worst_trough_time if valid else None,
            "drawdown_peak_equity_usdt": self.worst_peak if valid else None,
            "drawdown_trough_equity_usdt": self.worst_trough if valid else None,
            "expected_minute_observations": expected,
            "minute_observations": self.observations,
            "missing_valuation_observations": self.missing,
            "invalid_equity_observations": self.invalid,
            "valuation_status": "available" if valid else "unavailable",
            "valuation_unavailable_reasons": [] if valid else [status],
        }


class EquityCurve:
    """Retain only daily/boundary values; inspect every minute for drawdown."""

    def __init__(self, start: int, end: int) -> None:
        self.windows = periods(start, end)
        self.boundaries = {t for _, a, b in self.windows for t in (a, b)}
        self.nav: dict[int, Decimal | None] = {}
        self.drawdowns = [Drawdown(a, b) for _, a, b in self.windows]
        self.current_month = 1

    def add(self, time: int, value: Decimal | None) -> None:
        if not self.windows[0][1] <= time <= self.windows[0][2]:
            return
        if time in self.boundaries or time % DAY == 0:
            self.nav[time] = value
        self.drawdowns[0].add(time, value)
        while self.current_month < len(self.windows) and time > self.windows[self.current_month][2]:
            self.current_month += 1
        if self.current_month < len(self.windows):
            self.drawdowns[self.current_month].add(time, value)
            # Adjacent months share the same marked boundary equity.
            if time == self.windows[self.current_month][2] and self.current_month + 1 < len(
                self.windows
            ):
                self.drawdowns[self.current_month + 1].add(time, value)


def statistics(
    label: str,
    start: int,
    end: int,
    nav: Mapping[int, Decimal | None],
    closes: Closures,
    funding_ok: bool | None,
    drawdown: Drawdown | None = None,
    external_flows: Mapping[int, Decimal] | None = None,
) -> dict[str, Any]:
    first, last = nav.get(start), nav.get(end)
    notes = []
    if funding_ok is False:
        notes.append("Unfunded reference exceeds the same starting capital")
    elif funding_ok is None:
        notes.append("Funding sufficiency could not be established")
    if first is None or last is None:
        notes.append("Missing start/end USDT equity or FX")
    positive = first is not None and first > 0
    if first is not None and not positive:
        notes.append("Return requires positive starting equity")
    flows = external_flows or {}
    net_flows = sum((v for t, v in flows.items() if start < t <= end), ZERO)
    change = last - first if first is not None and last is not None else None
    pnl = change - net_flows if change is not None else None
    return_pct = (
        pnl / first * 100
        if pnl is not None and first is not None and first > 0 and funding_ok
        else None
    )
    # Use complete, equally spaced UTC days only. Never turn a gap into one day.
    left = ((start + DAY - 1) // DAY) * DAY
    right = end // DAY * DAY
    returns = []
    expected = max(0, (right - left) // DAY)
    missing = invalid = 0
    for day in range(left, right, DAY):
        before, after = nav.get(day), nav.get(day + DAY)
        if before is None or after is None:
            missing += 1
        elif before <= 0 or after <= 0 or not before.is_finite() or not after.is_finite():
            invalid += 1
        else:
            flow = sum((v for t, v in flows.items() if day < t <= day + DAY), ZERO)
            returns.append((after - before - flow) / before)
    sharpe = None
    if missing:
        sharpe_status = "missing_valuation"
    elif invalid:
        sharpe_status = "invalid_equity"
    elif len(returns) < 2:
        sharpe_status = "insufficient_observations"
    elif not funding_ok:
        sharpe_status = "insufficient_funding" if funding_ok is False else "unknown_funding"
    else:
        mean = sum(returns, ZERO) / len(returns)
        variance = sum(((value - mean) ** 2 for value in returns), ZERO) / (len(returns) - 1)
        if variance:
            sharpe = mean / variance.sqrt() * Decimal(365).sqrt()
            sharpe_status = "available"
        else:
            sharpe_status = "zero_volatility"
    if sharpe_status != "available":
        notes.append("Sharpe unavailable: " + sharpe_status)
    if drawdown is None:
        drawdown = Drawdown(start, end)
        for time, equity in sorted(nav.items()):
            if start <= time <= end:
                drawdown.add(time, equity)
    return {
        "period": label,
        "start_ms": start,
        "end_ms_exclusive": end,
        "starting_equity_usdt": first,
        "ending_equity_usdt": last,
        "equity_change_usdt": change,
        "external_net_cash_flows_usdt": net_flows,
        "pnl_usdt": pnl,
        "pnl_pct": return_pct,
        "return_status": "available"
        if return_pct is not None
        else "missing_valuation"
        if pnl is None
        else "invalid_starting_equity"
        if not positive
        else "insufficient_or_unknown_funding",
        "sharpe_ratio": sharpe,
        "sharpe_status": sharpe_status,
        "daily_intervals": expected,
        "known_daily_intervals": len(returns),
        "missing_daily_intervals": missing,
        "invalid_daily_intervals": invalid,
        "funding_sufficient": funding_ok,
        "notes": notes,
        **closes.metrics(),
        **drawdown.metrics(),
    }


def accounting_period(start: Mapping[str, Any], end: Mapping[str, Any]) -> dict[str, Any]:
    def difference(key: str) -> Decimal | None:
        a, b = start.get(key), end.get(key)
        return b - a if a is not None and b is not None else None

    realized = difference("realized_pnl_usdt")
    unrealized = difference("unrealized_pnl_usdt")
    trading = known_sum(realized, unrealized)
    fx = difference("quote_fx_pnl_usdt")
    change = difference("nav_usdt")
    components = known_sum(trading, fx)
    error = change - components if change is not None and components is not None else None
    if change is not None and components is not None:
        reconcile(change, components, "period equity/trading/FX bridge")
    return {
        "realized_pnl_usdt": realized,
        "starting_unrealized_pnl_usdt": start.get("unrealized_pnl_usdt"),
        "unrealized_pnl_usdt": end.get("unrealized_pnl_usdt"),
        "unrealized_pnl_change_usdt": unrealized,
        "trading_pnl_usdt": trading,
        "quote_fx_pnl_usdt": fx,
        "fees_usdt": difference("fees_usdt"),
        "cash_value_usdt": end.get("cash_value_usdt"),
        "position_value_usdt": end.get("position_value_usdt"),
        "position_cost_usdt": end.get("position_cost_usdt"),
        "open_cycles": end.get("open_cycles", int(end.get("position_qty", ZERO) > 0)),
        "reconciliation_error_usdt": error,
        "accounting_status": "available" if error is not None else "missing_valuation",
        "accounting_unavailable_reasons": sorted(
            set(start.get("accounting_unavailable_reasons", []))
            | set(end.get("accounting_unavailable_reasons", []))
        ),
    }


# timestamp, buy, price, amount, fee, notional, cash after fill, base after fill
Fill = tuple[int, bool, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]


@dataclass(frozen=True)
class NativeFill:
    values: Fill
    cost_after: Decimal
    realized_after: Decimal


def known_sum(*values: Decimal | None) -> Decimal | None:
    return sum((v for v in values if v is not None), ZERO) if None not in values else None


def reconcile(actual: Decimal, expected: Decimal, field: str) -> None:
    # C++ sidecars print 96 significant decimal digits. This permits only
    # the final decimal serialization error, not a ledger quantum of slack.
    tolerance = Decimal(1).scaleb(max(actual.adjusted(), expected.adjusted()) - 90)
    if abs(actual - expected) > tolerance:
        raise ValueError(f"Funded {field} does not reconcile to executed fills")


def reference_fills(folder: Path, capital: Decimal) -> Iterator[Fill]:
    receipt = read_json(folder / "receipt.json")
    records = valuation.Records(folder / "trades.bin", valuation.TRADE)
    try:
        records.validate(receipt["hashes"]["trades.bin"])
        previous = 0
        for index in range(records.count):
            row = records.at(index)
            if row[0] < previous or row[8] not in (0, 1):
                raise ValueError("Retained reference trade chronology/side is invalid")
            previous = row[0]
            yield (
                row[0],
                row[8] == 0,
                number(row[1]),
                number(row[2]),
                number(row[4]),
                number(row[3]),
                capital + number(row[7]) - number(row[6]) * Decimal("1.002"),
                number(row[5]),
            )
        records.unchanged()
    finally:
        records.close()


def funded_fills(folder: Path, market: str) -> Iterator[NativeFill]:
    path = folder / "fills.jsonl"
    before = identity(path)
    prefix = '{"market":' + json.dumps(market, ensure_ascii=False, separators=(",", ":")) + ","
    previous = 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if len(line) > 16_384:
                raise ValueError("Funded fill record exceeds bound")
            if not line.startswith(prefix):
                continue
            row = json.loads(line)
            if row["timestamp_ms"] < previous:
                raise ValueError("Funded fill chronology is invalid")
            previous = row["timestamp_ms"]
            if row["market"] == market:
                if row.get("fee_currency", market.split("/")[-1]) != market.split("/")[-1]:
                    raise ValueError("Funded historical reports support quote-currency fees only")
                if "cost" not in row or "realized" not in row:
                    raise ValueError(
                        "Older funded fill sidecar lacks authoritative cost/realized state"
                    )
                if not isinstance(row["buy"], bool):
                    raise ValueError("Invalid funded fill side")
                price, amount, fee, cash, base = (
                    number(row[name]) for name in ("price", "amount", "fee", "cash", "base")
                )
                yield NativeFill(
                    (
                        row["timestamp_ms"],
                        row["buy"],
                        price,
                        amount,
                        fee,
                        price * amount,
                        cash,
                        base,
                    ),
                    number(row["cost"]),
                    number(row["realized"]),
                )
    if identity(path) != before:
        raise ValueError("Funded fill sidecar changed during reporting")


@dataclass
class Pair:
    market: str
    source: Path
    source_hash: str
    start: int
    end: int
    capital: Decimal
    fills: Iterator[Fill | NativeFill]
    funded: bool = False
    quote_quantum: Decimal | None = None
    by_month: dict[str, Closures] = field(default_factory=dict)
    full: Closures = field(default_factory=Closures)
    reserve_quote: Decimal = ZERO
    ending_reserved_quote: Decimal | None = None


def stream_pair(
    pair: Pair,
    moments: Iterable[int],
    prices: valuation.Prices,
    curve: EquityCurve,
    result: dict[str, Any],
    snapshots: dict[int, dict[str, Any]] | None = None,
    extra_boundaries: set[int] | None = None,
) -> Generator[tuple[int, Decimal | None]]:
    venue, symbol = pair.market.split(":", 1)
    base, quote = symbol.split("/")
    cash, quantity = pair.capital, ZERO
    cycle_profit: Decimal | None = ZERO
    historical_basis: Decimal | None = ZERO
    realized_trade: Decimal | None = ZERO
    paid_fees: Decimal | None = ZERO
    native_cost = native_realized = native_fees = ZERO
    minimum_cash = pair.capital
    count = 0
    first_cash_violation: int | None = None
    initial_rate = prices.rate(venue, quote, pair.start)
    historical_cash = pair.capital * initial_rate if initial_rate is not None else None
    boundaries = curve.boundaries | (extra_boundaries or set())
    boundary_states: dict[int, dict[str, Any]] = {}
    with ExitStack() as stack:
        source = valuation.Records(pair.source, valuation.BAR)
        stack.callback(source.close)
        source.validate(pair.source_hash)
        iterator = pair.fills
        if hasattr(iterator, "close"):
            stack.callback(iterator.close)
        pending = next(iterator, None)
        for moment in moments:
            while pending is not None and (
                (pending.values if isinstance(pending, NativeFill) else pending)[0] <= moment
                if pair.funded
                else (pending.values if isinstance(pending, NativeFill) else pending)[0] < moment
            ):
                time, buy, price, amount, fee, notional, cash_after, qty_after = (
                    pending.values if isinstance(pending, NativeFill) else pending
                )
                if min(price, amount) <= 0 or fee < 0 or qty_after < 0:
                    raise ValueError("Invalid retained fill amount, price or fee")
                fx_time = time // valuation.MINUTE * valuation.MINUTE
                rate = prices.rate(venue, quote, fx_time)
                paid = fee * rate if rate is not None else None
                paid_fees = paid_fees + paid if paid_fees is not None and paid is not None else None
                native_fees += fee
                cash_delta = -notional - fee if buy else notional - fee
                if pair.funded:
                    reconcile(
                        qty_after, quantity + amount if buy else quantity - amount, "base balance"
                    )
                    reconcile(cash_after, cash + cash_delta, "cash balance")
                historical_cash = known_sum(
                    historical_cash, cash_delta * rate if rate is not None else None
                )
                if buy:
                    native_cost += notional + fee
                    added = (notional + fee) * rate if rate is not None else None
                    historical_basis = (
                        historical_basis + added
                        if historical_basis is not None and added is not None
                        else None
                    )
                else:
                    if amount > quantity and (pair.funded or qty_after != 0):
                        raise ValueError("Retained sell exceeds inventory")
                    removed_native = (
                        native_cost * amount / quantity if qty_after and quantity else native_cost
                    )
                    if isinstance(pending, NativeFill):
                        removed_native = native_cost - pending.cost_after
                        if not ZERO <= removed_native <= native_cost:
                            raise ValueError("Invalid funded cost-basis removal")
                    elif pair.funded and pair.quote_quantum is not None and qty_after:
                        removed_native = (removed_native / pair.quote_quantum).to_integral_value(
                            rounding=ROUND_FLOOR
                        ) * pair.quote_quantum
                    removed = (
                        historical_basis * removed_native / native_cost
                        if historical_basis is not None and native_cost
                        else historical_basis
                    )
                    native_cost -= removed_native
                    native_realized += notional - fee - removed_native
                    proceeds = (notional - fee) * rate if rate is not None else None
                    gain = (
                        proceeds - removed if proceeds is not None and removed is not None else None
                    )
                    realized_trade = (
                        realized_trade + gain
                        if realized_trade is not None and gain is not None
                        else None
                    )
                    cycle_profit = (
                        cycle_profit + gain
                        if cycle_profit is not None and gain is not None
                        else None
                    )
                    historical_basis = (
                        historical_basis - removed
                        if historical_basis is not None and removed is not None
                        else None
                    )
                    if not qty_after:
                        pair.full.add(cycle_profit)
                        # Funded fills occur at the completed boundary of their bar.
                        bucket = month(time)
                        pair.by_month.setdefault(bucket, Closures()).add(cycle_profit)
                        cycle_profit, historical_basis = ZERO, ZERO
                if isinstance(pending, NativeFill):
                    reconcile(pending.cost_after, native_cost, "cost basis")
                    reconcile(pending.realized_after, native_realized, "realized PnL")
                    reconcile(
                        cash_after + pending.cost_after,
                        pair.capital + pending.realized_after,
                        "cash/cost conservation",
                    )
                    native_cost, native_realized = pending.cost_after, pending.realized_after
                quantity, cash = qty_after, cash_after
                minimum_cash = min(minimum_cash, cash)
                if cash < 0 and first_cash_violation is None:
                    first_cash_violation = time
                count += 1
                pending = next(iterator, None)
            rate = prices.rate(venue, quote, moment)
            quote_route = list(prices.last_route)
            close = source.close_at(moment, exact=True)
            native_inventory = quantity * close if close is not None else None
            base_rate = prices.rate(venue, base, moment)
            base_route = list(prices.last_route)
            inventory = (
                quantity * base_rate if base_rate is not None else ZERO if not quantity else None
            )
            cash_value = cash * rate if rate is not None else ZERO if not cash else None
            nav = known_sum(cash_value, inventory)
            curve.add(moment, nav)
            if snapshots is not None or moment in boundaries:
                unrealized = (
                    inventory - historical_basis
                    if inventory is not None and historical_basis is not None
                    else None
                )
                trading = known_sum(realized_trade, unrealized)
                fx_pnl = (
                    cash_value - historical_cash
                    if cash_value is not None and historical_cash is not None
                    else None
                )
                economic = (
                    nav - pair.capital * initial_rate
                    if nav is not None and initial_rate is not None
                    else None
                )
                components = known_sum(trading, fx_pnl)
                if economic is not None and components is not None:
                    reconcile(economic, components, "USDT equity/trading/FX bridge")
                state = {
                    "pnl_usdt": economic,
                    "realized_pnl_usdt": realized_trade,
                    "unrealized_pnl_usdt": unrealized,
                    "position_qty": quantity,
                    "position_value_usdt": inventory,
                    "position_cost_usdt": historical_basis,
                    "fees_usdt": paid_fees,
                    "cash_value_usdt": cash_value,
                    "cash_native_quote": cash,
                    "cost_native_quote": native_cost,
                    "realized_pnl_native_quote": native_realized,
                    "unrealized_pnl_native_quote": native_inventory - native_cost
                    if native_inventory is not None
                    else ZERO
                    if not quantity
                    else None,
                    "fees_native_quote": native_fees,
                    "strategy_reserve_native_quote": pair.reserve_quote,
                    "reserved_quote_included_in_cash": True,
                    "quote_fx_pnl_usdt": fx_pnl,
                    "trading_pnl_usdt": trading,
                    "equity_change_usdt": economic,
                    "external_net_cash_flows_usdt": ZERO,
                    "reconciliation_error_usdt": economic - components
                    if economic is not None and components is not None
                    else None,
                    "valuation_status": "available" if nav is not None else "unavailable",
                    "unavailable_reasons": (
                        ["missing_quote_conversion"] if cash and rate is None else []
                    )
                    + (["missing_inventory_conversion"] if quantity and base_rate is None else []),
                    "accounting_unavailable_reasons": [
                        reason
                        for known, reason in (
                            (initial_rate, "missing_initial_quote_conversion"),
                            (historical_basis, "missing_historical_cost_conversion"),
                            (realized_trade, "missing_historical_realized_conversion"),
                            (paid_fees, "missing_historical_fee_conversion"),
                            (cash_value, "missing_quote_conversion"),
                            (inventory, "missing_inventory_conversion"),
                        )
                        if known is None
                    ],
                    "translated_native_pnl_usdt": (cash - pair.capital) * rate + inventory
                    if rate is not None and inventory is not None
                    else None,
                    "nav_usdt": nav,
                    "wallet_pnl_usdt": nav - pair.capital * initial_rate
                    if nav is not None and initial_rate is not None
                    else None,
                    "price_usdt": base_rate,
                    "rate_usdt": rate,
                    "rate_timestamp_ms": moment - valuation.MINUTE if rate is not None else None,
                    "mark_available_at_ms": moment,
                    "rate_source_files": quote_route,
                    "base_rate_source_files": base_route,
                    "complete": nav is not None
                    and paid_fees is not None
                    and historical_basis is not None
                    and economic is not None
                    and trading is not None,
                }
                if snapshots is not None:
                    snapshots[moment] = state
                if moment in boundaries:
                    boundary_states[moment] = state
            yield moment, nav
        if pending is not None:
            raise ValueError("Metrics horizon omitted retained fills")
        source.unchanged()
    own = [
        {
            **statistics(
                label,
                start,
                end,
                curve.nav,
                pair.full
                if label == "Entire selected period"
                else pair.by_month.get(label, Closures()),
                first_cash_violation is None or first_cash_violation >= end + int(pair.funded),
                curve.drawdowns[index],
            ),
            **accounting_period(boundary_states.get(start, {}), boundary_states.get(end, {})),
        }
        for index, (label, start, end) in enumerate(curve.windows)
    ]
    result.update(
        {
            "market": pair.market,
            "capital_native_quote": pair.capital,
            "quote": quote,
            "minimum_cash_native_quote": minimum_cash,
            "first_insufficient_funding_ms": first_cash_violation,
            "fills_included": count,
            "ending_inventory_base": quantity,
            "open_cycles": int(quantity > 0),
            "accounting_boundaries": boundary_states,
            "ending_reserved_native_quote": pair.ending_reserved_quote,
            "fee_support": "quote_currency_only"
            if pair.funded
            else "immutable_reference_quote_fee_model",
            "full_period": own[0],
            "monthly": own[1:],
        }
    )


def evaluate_pair(
    pair: Pair,
    moments: list[int],
    prices: valuation.Prices,
    snapshots: dict[int, dict[str, Any]] | None = None,
) -> tuple[dict[int, Decimal | None], dict[str, Any], bool]:
    result: dict[str, Any] = {}
    curve = EquityCurve(pair.start, pair.end)
    values = dict(stream_pair(pair, moments, prices, curve, result, snapshots))
    return values, result, result["first_insufficient_funding_ms"] is None


def report(pairs: list[Pair], catalog: ArchiveCatalog) -> dict[str, Any]:
    if not pairs:
        raise ValueError("No successful pairs available for performance metrics")
    start, end = min(p.start for p in pairs), max(p.end for p in pairs)
    if len({p.market for p in pairs}) != len(pairs):
        raise ValueError("Duplicate market allocation would double-count portfolio funding")
    if any(
        p.start % valuation.MINUTE or p.end % valuation.MINUTE or p.start >= p.end for p in pairs
    ):
        raise ValueError("Performance dates must be ordered completed-minute boundaries")
    combined = EquityCurve(start, end)
    windows = combined.windows
    moments = range(start, end + 1, valuation.MINUTE)
    if (end - start) // DAY > 25_000 or len(pairs) > 50_000:
        raise ValueError("Performance report exceeds the supported historical horizon")
    prices = valuation.Prices(catalog, exact=True)
    by_month: dict[str, Closures] = {}
    complete = Closures()
    pair_rows: list[dict[str, Any]] = [{} for _ in pairs]
    try:
        with localcontext() as context, ExitStack() as stack:
            context.prec = 96
            streams = [
                stack.enter_context(
                    closing(
                        stream_pair(
                            pair,
                            moments,
                            prices,
                            EquityCurve(pair.start, pair.end),
                            row,
                            extra_boundaries=combined.boundaries,
                        )
                    )
                )
                for pair, row in zip(pairs, pair_rows, strict=True)
            ]
            for states in zip(*streams, strict=True):
                combined.add(states[0][0], known_sum(*(nav for _, nav in states)))
            for pair in pairs:
                complete.merge(pair.full)
                for label, stats in pair.by_month.items():
                    by_month.setdefault(label, Closures()).merge(stats)
            prices.unchanged()
            first_violation = min(
                (
                    row["first_insufficient_funding_ms"]
                    for row in pair_rows
                    if row["first_insufficient_funding_ms"] is not None
                ),
                default=None,
            )
            summed_boundaries: dict[int, dict[str, Any]] = {}
            additive = (
                "nav_usdt",
                "realized_pnl_usdt",
                "unrealized_pnl_usdt",
                "fees_usdt",
                "quote_fx_pnl_usdt",
                "cash_value_usdt",
                "position_value_usdt",
                "position_cost_usdt",
            )
            for time in combined.boundaries:
                boundary_states = [r["accounting_boundaries"][time] for r in pair_rows]
                summed_boundaries[time] = {
                    key: known_sum(*(s[key] for s in boundary_states)) for key in additive
                }
                summed_boundaries[time]["open_cycles"] = sum(
                    s["position_qty"] > 0 for s in boundary_states
                )
                summed_boundaries[time]["accounting_unavailable_reasons"] = sorted(
                    {
                        reason
                        for s in boundary_states
                        for reason in s["accounting_unavailable_reasons"]
                    }
                )
            rows = [
                {
                    **statistics(
                        label,
                        a,
                        b,
                        combined.nav,
                        complete
                        if label == "Entire selected period"
                        else by_month.get(label, Closures()),
                        first_violation is None
                        or first_violation >= b + int(all(p.funded for p in pairs)),
                        combined.drawdowns[index],
                    ),
                    **accounting_period(summed_boundaries[a], summed_boundaries[b]),
                }
                for index, (label, a, b) in enumerate(windows)
            ]
            return cast(
                dict[str, Any],
                valuation.text_values(
                    {
                        "schema": REVISION,
                        "full_period": rows[0],
                        "monthly": rows[1:],
                        "pairs": pair_rows,
                        "cycle_completion_months": {k: v.metrics() for k, v in by_month.items()},
                        "accounting_boundaries": summed_boundaries,
                        "equity_sampling": "every_completed_minute_including_period_boundaries",
                        "conversion_sources": list(prices.evidence.values()),
                        "conversion_errors": prices.errors,
                        "basis": (
                            "USDT equity values cash and inventory using immediately completed "
                            "archived candle closes at each minute, "
                            "net of paid fees, without forecasting an unsent exit fee. "
                            "PnL % = equity change / starting equity. Capital per pair matches "
                            "the current funded engine in native quote units. Reference returns "
                            "and Sharpe are unavailable if its trades exceed that capital. "
                            "Monthly returns use month-start equity, including open inventory. "
                            "UTC calendar months are clipped to the selected period. Win Rate "
                            "counts completed positions, including breakevens in the denominator. "
                            "Profit Factor = summed positive net USDT position PnL / absolute "
                            "negative net position PnL. A single losing cycle gives PF=0; "
                            "No losses and no completed cycles have distinct unavailable states. "
                            "Open "
                            "positions affect equity but are excluded from trade statistics. "
                            "Sharpe uses complete UTC daily simple returns, sample standard "
                            "deviation (ddof=1), zero risk-free rate and sqrt(365) annualization, "
                            "including monthly Sharpe. Missing marks invalidate affected metrics; "
                            "gaps and partial days never become daily returns. Nonpositive "
                            "daily equity invalidates Sharpe. Drawdown inspects every minute; "
                            "missing marks invalidate it. Allocations are independent funded "
                            "wallets, each counted once; shared asset names are not shared cash. "
                            "No external flows occur in this runner. Period boundaries include "
                            "fills stamped at that instant; cycle counts belong to their actual "
                            "UTC completion month (also exposed for end-boundary completions). "
                            "Historical-cost trading PnL includes inventory FX; quote-cash FX "
                            "is separate. Fees are already included and must not be subtracted "
                            "again. OHLC research assumptions apply."
                        ),
                    }
                ),
            )
    finally:
        prices.close()


def reference_pairs(children: list[tuple[str, Path, Path]], capital: Decimal) -> list[Pair]:
    pairs = []
    for market, current, audited in children:
        summary, other = reference_review(current), reference_review(audited)
        if not identical_inputs([(current, summary)], [(audited, other)]):
            raise ValueError("Reference performance requires verified identical engine artifacts")
        config = read_json(current / "config.json")
        pairs.append(
            Pair(
                market,
                Path(config["dataset"]),
                config["dataset_sha256"],
                config.get("start_ts", summary["first_ts"]),
                config.get("end_ts", summary["last_ts"] + valuation.MINUTE),
                capital,
                reference_fills(current, capital),
            )
        )
    return pairs


def funded_pairs(
    folder: Path, job: dict[str, Any], receipt: dict[str, Any], market: str = ""
) -> list[Pair]:
    if not receipt.get("fills_sha256"):
        raise ValueError(
            "This older funded run has no complete fill sidecar; rerun it for monthly trade metrics"
        )
    if sha256(folder / "fills.jsonl") != receipt["fills_sha256"]:
        raise ValueError("Retained funded fill sidecar changed")
    capital = number(job["settings"]["capital"])
    config = read_json(folder / "experiment_config.json")
    quantum = number(config["market_rules"]["quote_ledger_quantum"])
    start, end = job["declared_start_ms"], job["declared_end_ms_exclusive"]
    allocations = read_json(folder / "stdout.json", max_bytes=32 * 1024 * 1024).get(
        "allocations", []
    )
    reserved = {f"{r['venue']}:{r['symbol']}": number(r["reserved_quote"]) for r in allocations}
    return [
        Pair(
            f"{row['venue']}:{row['market']}",
            Path(row["source_path"]),
            row["sha256"],
            start,
            end,
            capital,
            funded_fills(folder, f"{row['venue']}:{row['market']}"),
            True,
            number(
                config.get("market_rules_by_market", {})
                .get(f"{row['venue']}:{row['market']}", {})
                .get("values", {})
                .get("quote_quantum", quantum)
            ),
            reserve_quote=number(job["settings"]["reserve"]),
            ending_reserved_quote=reserved.get(f"{row['venue']}:{row['market']}"),
        )
        for row in job["selected"]
        if not market or f"{row['venue']}:{row['market']}" == market
    ]


def funded_valuation(
    pairs: list[Pair], catalog: ArchiveCatalog, at_ms: int | None
) -> dict[str, Any]:
    if not pairs:
        raise ValueError("No funded pairs available")
    start, end = min(p.start for p in pairs), max(p.end for p in pairs)
    if at_ms is not None and (at_ms % valuation.MINUTE or not start <= at_ms <= end):
        raise ValueError("Choose a completed minute inside the funded run")
    interval = max(
        1,
        ((end - start) // valuation.MINUTE + valuation.MAX_POINTS - 2)
        // (valuation.MAX_POINTS - 1),
    )
    moments = (
        [at_ms] if at_ms is not None else list(range(start, end + 1, interval * valuation.MINUTE))
    )
    if at_ms is None and moments[-1] != end:
        moments.append(end)
    fields = (
        *valuation.METRICS,
        "nav_usdt",
        "wallet_pnl_usdt",
        "trading_pnl_usdt",
        "quote_fx_pnl_usdt",
        "equity_change_usdt",
        "external_net_cash_flows_usdt",
    )
    combined: list[dict[str, Any]] = [
        {
            "timestamp_ms": time,
            "timestamp": timestamp(time),
            **dict.fromkeys(fields, ZERO),
            "known_pairs": 0,
            "missing_pairs": 0,
            "known_subtotals": dict.fromkeys(fields, ZERO),
        }
        for time in moments
    ]
    rows = []
    prices = valuation.Prices(catalog, exact=True)
    try:
        with localcontext() as context:
            context.prec = 96
            for pair in pairs:
                snapshots: dict[int, dict[str, Any]] = {}
                evaluate_pair(
                    pair, sorted(set([*moments, pair.start, pair.end])), prices, snapshots
                )
                row = {
                    "market": pair.market,
                    "quote": pair.market.rsplit("/", 1)[1],
                    "timestamp_ms": pair.end,
                    **snapshots[pair.end],
                }
                if at_ms is not None:
                    row["at"] = snapshots[at_ms]
                rows.append(row)
                for total, time in zip(combined, moments, strict=True):
                    state = snapshots[time]
                    total["known_pairs" if state["complete"] else "missing_pairs"] += 1
                    if len(pairs) == 1:
                        total["price_usdt"] = state["price_usdt"]
                    for field in fields:
                        if state[field] is None:
                            total[field] = None
                        else:
                            total["known_subtotals"][field] += state[field]
                            if total[field] is not None:
                                total[field] += state[field]
            prices.unchanged()
            return cast(
                dict[str, Any],
                valuation.text_values(
                    {
                        "schema": "funded-historical-usdt-v2",
                        "kind": "funded",
                        "currency": "USDT",
                        "series": combined,
                        "pairs": rows,
                        "requested_timestamp_ms": at_ms,
                        "funded_nav_usdt": combined[-1]["nav_usdt"],
                        "conversion_sources": list(prices.evidence.values()),
                        "conversion_errors": prices.errors,
                        "basis": (
                            "Exact funded fills and immediately completed archived candle closes. "
                            "PnL is equity change from initial funding, including quote-cash FX. "
                            "Trading PnL is reported separately from quote-cash FX. "
                            "Missing values remain unavailable. Charts sample at most "
                            "2000 states including every preceding fill. Funded fills "
                            "are stamped at modeled bar completion, not observed "
                            "exchange execution times."
                        ),
                    }
                ),
            )
    finally:
        prices.close()
