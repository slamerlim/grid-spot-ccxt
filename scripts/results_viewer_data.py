"""Read-only, bounded projections of published exact GRID binary results."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import zlib
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import msgpack  # type: ignore[import-untyped]

from scripts import research_sweep_matrix as matrix

Scalar = Fraction | int | str | None
Row = dict[str, Scalar]
Stamp = tuple[int, int, int, int]
Kind = Literal["number", "text", "date"]


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    kind: Kind = "number"


COLUMNS = (
    Column("ordinal", "№"),
    Column("id", "Конфигурация", "text"),
    Column("scope", "Портфель / биржа", "text"),
    Column("source_id", "Пара на бирже", "text"),
    Column("venue", "Биржа", "text"),
    Column("symbol", "Пара", "text"),
    Column("open_qty", "Открыто монет"),
    Column("last_price", "Последняя цена, USDT"),
    Column("last_price_ms", "Дата последней цены", "date"),
    Column("trade_hash", "Хеш сделок", "text"),
    Column("month", "Месяц (UTC)", "text"),
    Column("step_pct", "Шаг сетки, %"),
    Column("tp_pct", "TP монеты, %"),
    Column("exchange_tp_pct", "TP биржи, %"),
    Column("global_tp_pct", "Глобальный TP, %"),
    Column("buy_floor_pct", "Нижняя граница BUY, %"),
    Column("total_pnl", "Чистый PnL, USDT"),
    Column("pnl_cumulative", "Накопленный чистый PnL, USDT"),
    Column("realized_gross", "Закрытый PnL до комиссий, USDT"),
    Column("unrealized_gross", "Открытый PnL до комиссий, USDT"),
    Column("fees", "Комиссии, USDT"),
    Column("spent_open", "Spent в начале месяца, USDT"),
    Column("spent", "Spent в конце, USDT"),
    Column("peak_spent", "Пик spent, USDT"),
    Column("peak_spent_ms", "Дата пика spent", "date"),
    Column("open_value", "Стоимость открытых позиций, USDT"),
    Column("return_pct", "PnL / пик spent, %"),
    Column("cumulative_return_pct", "Накопленный PnL / пик spent, %"),
    Column("max_drawdown", "Макс. просадка, USDT"),
    Column("max_drawdown_pct", "Макс. просадка / пик spent, %"),
    Column("max_drawdown_ms", "Дата макс. просадки", "date"),
    Column("median_monthly_pnl_pct", "Медианный месячный PnL, %"),
    Column("median_monthly_drawdown_pct", "Медианная месячная просадка, %"),
    Column("median_monthly_profit_factor", "Медианный месячный PF закрытий"),
    Column("monthly_net_pnl_ratio", "Прибыль / убыток полных месяцев по общему PnL"),
    Column("monthly_net_pnl_ratio_status", "Статус отношения месяцев", "text"),
    Column("positive_pnl_months", "Прибыльных полных месяцев"),
    Column("negative_pnl_months", "Убыточных полных месяцев"),
    Column("full_months", "Полных месяцев UTC"),
    Column("median_pnl_months", "Месяцев в медиане PnL"),
    Column("median_pnl_missing_months", "Месяцев без PnL %"),
    Column("median_pf_months", "Месяцев в медиане PF"),
    Column("median_pf_missing_months", "Месяцев без PF"),
    Column("median_drawdown_status", "Статус медианы просадки", "text"),
    Column("median_dd_months", "Месяцев в медиане просадки"),
    Column("median_dd_missing_months", "Месяцев без процента просадки"),
    Column("longest_hold_hours", "Макс. удержание, ч"),
    Column("buy_count", "Покупок"),
    Column("sell_count", "Продаж"),
    Column("net_profit_closed", "Прибыль закрытий после комиссий, USDT"),
    Column("net_loss_closed", "Убыток закрытий после комиссий, USDT"),
    Column("winning_closures", "Прибыльных закрытий"),
    Column("losing_closures", "Убыточных закрытий"),
    Column("breakeven_closures", "Безубыточных закрытий"),
    Column("profit_factor", "PF закрытых позиций"),
    Column("profit_factor_status", "Статус PF", "text"),
    Column("sharpe", "Sharpe"),
    Column("sharpe_status", "Статус Sharpe", "text"),
    Column("sortino", "Sortino"),
    Column("sortino_status", "Статус Sortino", "text"),
    Column("sample_months", "Месяцев для Sharpe/Sortino"),
    Column("global_exits", "Глобальных TP"),
    Column("exchange_exits", "Биржевых TP"),
    Column("start_ms", "Начало периода", "date"),
    Column("end_ms", "Конец периода (не включён)", "date"),
    Column("complete_month", "Полный месяц", "text"),
)
BY_KEY = {column.key: column for column in COLUMNS}
DEFAULT_COLUMNS = (
    "id",
    "step_pct",
    "tp_pct",
    "exchange_tp_pct",
    "global_tp_pct",
    "buy_floor_pct",
    "total_pnl",
    "return_pct",
    "max_drawdown_pct",
    "median_monthly_pnl_pct",
    "median_monthly_drawdown_pct",
    "median_monthly_profit_factor",
    "monthly_net_pnl_ratio",
    "monthly_net_pnl_ratio_status",
    "positive_pnl_months",
    "negative_pnl_months",
    "median_pf_months",
    "median_pf_missing_months",
    "peak_spent",
    "spent",
    "fees",
    "unrealized_gross",
    "net_profit_closed",
    "net_loss_closed",
    "profit_factor",
    "profit_factor_status",
    "sharpe",
    "sortino",
    "buy_count",
    "sell_count",
)
STATUS = {
    "available": "Доступен",
    "no_losses": "Нет убыточных закрытий",
    "no_closures": "Нет закрытий",
    "insufficient_months": "Недостаточно месяцев",
    "zero_variance": "Нет разброса",
    "no_downside": "Нет отрицательных месяцев",
    "no_negative_months": "Нет убыточных полных месяцев",
    "no_full_months": "Нет полных месяцев",
    "not_computed": "Ещё не рассчитано",
}


def stamp(path: Path) -> Stamp:
    if path.is_symlink():
        raise ValueError("Результат не должен быть символической ссылкой")
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def signature(publication: Path) -> tuple[Stamp, Stamp, Stamp]:
    if publication.stat().st_size > 1024**2:
        raise ValueError("Слишком большой manifest результатов")
    value = json.loads(publication.read_text())
    return stamp(publication), stamp(Path(value["binary_results"])), stamp(Path(value["index"]))


@dataclass(frozen=True)
class Archive:
    publication: Path
    binary: Path
    entries: tuple[tuple[int, str, int, int, bytes], ...]
    identity: tuple[Stamp, Stamp, Stamp]
    venues: tuple[str, ...]
    months: tuple[str, ...]
    scope_months: dict[str, tuple[str, ...]]
    scope_periods: dict[str, tuple[tuple[int, int, bool], ...]]
    source_count: int
    elapsed_seconds: float
    source_hashes: tuple[str, str, str]
    flat_hour_study: bool
    configurations: tuple[dict[str, Any], ...]


def open_archive(publication: Path) -> Archive:
    before = signature(publication)
    value = json.loads(publication.read_text())
    if value.get("status") != "COMPLETE":
        raise ValueError("Просмотр доступен после полного завершения и проверки прогона")
    binary, index = Path(value["binary_results"]), Path(value["index"])
    if (
        binary.stat().st_size != value["binary_bytes"]
        or file_hash(binary) != value["binary_sha256"]
    ):
        raise ValueError("Контрольная сумма results.bin не совпала")
    if index.stat().st_size > 64 * 1024**2 or file_hash(index) != value["index_sha256"]:
        raise ValueError("Контрольная сумма индекса не совпала")
    with closing(matrix._readonly(index)) as connection:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("Индекс результатов повреждён")
        metadata = {
            key: msgpack.unpackb(raw, raw=False)
            for key, raw in connection.execute("SELECT key,value FROM metadata")
        }
        count = connection.execute("SELECT COUNT(*) FROM configurations").fetchone()[0]
        if not 0 < count <= 100_000 or count != value["configuration_count"]:
            raise ValueError("Неверное число конфигураций")
        entries = tuple(
            connection.execute(
                "SELECT ordinal,config_id,offset,size,sha256 FROM configurations ORDER BY ordinal"
            )
        )
    if metadata.get("complete") is not True or metadata.get("schema") != matrix.CATALOG_SCHEMA:
        raise ValueError("Бинарный каталог не завершён или его формат не поддерживается")
    if matrix._digest(metadata["identity"]) != value["study_sha256"] or metadata[
        "committed_boundary"
    ] != {"bytes": value["binary_bytes"], "sha256": value["binary_sha256"]}:
        raise ValueError("Индекс и опубликованный прогон различаются")
    configurations = tuple(
        matrix.configuration_at(metadata["identity"], ordinal) for ordinal in range(count)
    )
    offset = len(matrix.RESULT_MAGIC)
    with binary.open("rb") as handle:
        if handle.read(offset) != matrix.RESULT_MAGIC:
            raise ValueError("Неверный заголовок results.bin")
        for expected, (ordinal, config_id, actual_offset, size, _checksum) in enumerate(entries):
            if (
                ordinal != expected
                or actual_offset != offset
                or config_id != configurations[ordinal]["id"]
            ):
                raise ValueError("Нарушен порядок или состав конфигураций")
            offset += matrix.RESULT_HEADER.size + size
        if offset != value["binary_bytes"]:
            raise ValueError("Индекс не покрывает бинарный файл целиком")
        first = entries[0]
        first_row = matrix.decode_result(matrix._read_payload(handle, *first[2:]), first[4])
        check_configuration_depth(first_row, configurations[0])
    performance = first_row["performance"]
    scopes = {"total": performance["total"]}
    scopes.update((row["venue"], row) for row in performance["venues"])
    venues = tuple(row["venue"] for row in first_row["venues"])
    if set(scopes) != {"total", *venues} or len(scopes) != len(venues) + 1:
        raise ValueError("Месячные результаты содержат неверный состав бирж")
    scope_months: dict[str, tuple[str, ...]] = {}
    scope_periods: dict[str, tuple[tuple[int, int, bool], ...]] = {}
    for name, scope in scopes.items():
        months: list[str] = []
        periods: list[tuple[int, int, bool]] = []
        for row in scope["monthly"]:
            start, end, complete = row["start_ms"], row["end_ms"], row["complete_month"]
            month = datetime.fromtimestamp(start / 1000, UTC).strftime("%Y-%m")
            first, following = _month_bounds(month)
            if (
                type(start) is not int
                or type(end) is not int
                or type(complete) is not bool
                or not first <= start < end <= following
                or complete != ((start, end) == (first, following))
                or periods
                and periods[-1][1] != start
                or months
                and months[-1] == month
            ):
                raise ValueError("Некорректный месячный календарь результатов")
            months.append(month)
            periods.append((start, end, complete))
        if periods and (
            scope["overall"]["start_ms"] != periods[0][0]
            or scope["overall"]["end_ms"] != periods[-1][1]
        ):
            raise ValueError("Месячный календарь не совпадает с итоговым периодом")
        scope_months[name] = tuple(months)
        scope_periods[name] = tuple(periods)
    if any(not set(scope_months[name]).issubset(scope_months["total"]) for name in venues):
        raise ValueError("Календарь биржи выходит за пределы портфеля")
    if before != signature(publication):
        raise ValueError("Результаты изменились во время чтения")
    return Archive(
        publication,
        binary,
        entries,
        before,
        venues,
        scope_months["total"],
        scope_months,
        scope_periods,
        value["source_count"],
        value["stage_observations"][-1]["wall_seconds"],
        (value["study_sha256"], value["binary_sha256"], value["index_sha256"]),
        value.get("selection_policy") == "reviewed_gap_free_current_rules_flat_hour_five_venues"
        and type(value.get("flat_run_minutes")) is int
        and value["flat_run_minutes"] == 60,
        configurations,
    )


def check_configuration_depth(result: dict[str, Any], configuration: dict[str, Any]) -> None:
    """Depth is part of configuration identity; absent legacy values mean 60%."""
    actual = Fraction(result.get("grid_depth_pct", "60"))
    expected = Fraction(configuration.get("grid_depth_pct", "60"))
    if actual != expected:
        raise ValueError("Глубина BUY в результате и manifest различается")


def _scope(unpacker: Any, venue: str, month: int | None) -> dict[str, Any] | None:
    if unpacker.read_array_header() != 4:
        raise ValueError("Неверная структура месячного результата")
    name = unpacker.unpack()
    if name != (None if venue == "total" else venue):
        for _ in range(3):
            unpacker.skip()
        return None
    unpacker.skip()  # Exact monetary scale; values retain their stored ratios.
    overall = unpacker.unpack()
    length = unpacker.read_array_header()
    if not 0 <= length <= 2401 or month is not None and not 0 <= month < length:
        raise ValueError("Месяц отсутствует в результате")
    selected = overall if month is None else None
    for index in range(length):
        if index == month:
            selected = unpacker.unpack()
        else:
            unpacker.skip()
    if not isinstance(selected, list):
        raise ValueError("Финансовый период отсутствует")
    return matrix._unpack_period(selected, overall=month is None)


def project(
    payload: bytes,
    checksum: bytes,
    venue: str,
    month: int | None,
    expected_period: tuple[int, int, bool] | None = None,
) -> dict[str, Any]:
    """Skip all unselected months in C rather than creating their Python objects."""
    raw = matrix._verified_payload_bytes(payload, checksum)
    unpacker = msgpack.Unpacker(
        raw=False, strict_map_key=True, max_buffer_size=matrix.MAX_PAYLOAD_BYTES
    )
    unpacker.feed(raw)
    if unpacker.read_array_header() != len(matrix.RESULT_FIELDS) + 1:
        raise ValueError("Неверная структура конфигурации")
    result = {key: unpacker.unpack() for key in matrix.RESULT_FIELDS[:5]}
    primary = matrix._unpack_metrics(unpacker.unpack())
    venues = [
        matrix._unpack_metrics(unpacker.unpack()) for _ in range(unpacker.read_array_header())
    ]
    if venue != "total":
        primary = next(row for row in venues if row["venue"] == venue)
    unpacker.skip()  # trade hash remains verified by the complete payload checksum
    unpacker.skip()  # pair fingerprint
    cycles = unpacker.unpack()
    period = None
    for _ in range(unpacker.read_map_header()):
        key = unpacker.unpack()
        if key == "grid_depth_pct":
            result[key] = unpacker.unpack()
            continue
        if key != "performance":
            unpacker.skip()
            continue
        if unpacker.read_map_header() != 1 or unpacker.unpack() != "binary_monthly_v1":
            raise ValueError("Неподдерживаемый формат финансовых показателей")
        if unpacker.read_array_header() != 2:
            raise ValueError("Неверная структура биржевых итогов")
        period = _scope(unpacker, venue, month)
        for _ in range(unpacker.read_array_header()):
            candidate = _scope(unpacker, venue, month)
            if candidate is not None:
                period = candidate
    if period is None or unpacker.tell() != len(raw):
        raise ValueError("Финансовый результат отсутствует или содержит лишние данные")
    if (
        expected_period is not None
        and (period["start_ms"], period["end_ms"], period["complete_month"]) != expected_period
    ):
        raise ValueError("Месячный календарь конфигурации различается")
    return _assemble(result, primary, period, cycles, venue, month is None)


def _assemble(
    result: dict[str, Any],
    primary: dict[str, Any],
    period: dict[str, Any],
    cycles: dict[str, Any],
    venue: str,
    overall: bool,
) -> dict[str, Any]:
    result = dict(result)
    if overall:
        result.update(primary)
        result["longest_hold_hours"] = Fraction(primary["longest_hold_ms"], 3_600_000)
        result["global_exits"] = cycles["global"]["completed_count"]
        result["exchange_exits"] = sum(
            row["completed_count"]
            for row in cycles["venues"]
            if venue == "total" or row["venue"] == venue
        )
    result.update(period)
    return result


def _row(projected: dict[str, Any], ordinal: int) -> Row:
    row: Row = {
        "ordinal": ordinal + 1,
        "buy_floor_pct": -Fraction(projected.get("grid_depth_pct", "60")),
    }
    for column in COLUMNS[1:]:
        if column.key not in projected:
            continue
        value = projected[column.key]
        if value is not None and column.kind == "number":
            value = value if isinstance(value, (int, Fraction)) else Fraction(value)
        if column.key.endswith("_status"):
            value = STATUS.get(value, value)
        if column.key == "complete_month":
            value = "Да" if value else "Нет"
        row[column.key] = value
    peak = Fraction(cast(Any, row["peak_spent"]))
    row["return_pct"] = Fraction(cast(Any, row["total_pnl"])) * 100 / peak if peak else None
    if "max_drawdown" in row:
        row["max_drawdown_pct"] = (
            Fraction(cast(Any, row["max_drawdown"])) * 100 / peak if peak else None
        )
    return row


def read_rows(archive: Archive, venue: str, period: str) -> list[Row]:
    if venue not in ("total", *archive.venues):
        raise ValueError("Неизвестная биржа")
    calendar = archive.scope_months[venue]
    if period != "overall" and period not in calendar:
        raise ValueError("Месяц отсутствует в истории выбранной биржи")
    month = None if period == "overall" else calendar.index(period)
    expected_period = None if month is None else archive.scope_periods[venue][month]
    if signature(archive.publication) != archive.identity:
        raise ValueError("Результаты изменились; обновите страницу")
    rows: list[Row] = []
    with archive.binary.open("rb") as handle:
        for ordinal, config_id, offset, size, checksum in archive.entries:
            projected = project(
                matrix._read_payload(handle, offset, size, checksum),
                checksum,
                venue,
                month,
                expected_period,
            )
            if projected["id"] != config_id:
                raise ValueError("Конфигурация в индексе и бинарном файле различается")
            check_configuration_depth(projected, archive.configurations[ordinal])
            rows.append(_row(projected, ordinal))
    if signature(archive.publication) != archive.identity:
        raise ValueError("Результаты изменились во время чтения")
    return rows


MEDIAN_KEYS = (
    "median_monthly_pnl_pct",
    "median_monthly_profit_factor",
    "full_months",
    "median_pnl_months",
    "median_pnl_missing_months",
    "median_pf_months",
    "median_pf_missing_months",
    "monthly_net_pnl_ratio",
    "monthly_net_pnl_ratio_status",
    "positive_pnl_months",
    "negative_pnl_months",
)
LEGACY_MEDIAN_KEYS = MEDIAN_KEYS[:7]
MEDIAN_FRACTION_KEYS = frozenset(
    ("median_monthly_pnl_pct", "median_monthly_profit_factor", "monthly_net_pnl_ratio")
)
MEDIAN_MAGIC = b"GRIDMED1\n"
# The accepted 59,367-row depth matrix needs about 30 MiB for six exact scopes.
MEDIAN_LIMIT = 64 * 1024**2
MedianIndex = dict[str, dict[str, Row]]
MISSING_DRAWDOWN = {
    "median_monthly_drawdown_pct": None,
    "median_drawdown_status": "Ещё не рассчитано",
    "median_dd_months": None,
    "median_dd_missing_months": None,
}


def monthly_medians(periods: Iterable[dict[str, Any]]) -> Row:
    """Exact full-month summaries; closed PF and marked PnL remain distinct."""
    returns: list[Fraction] = []
    factors: list[Fraction] = []
    count = 0
    positive_months = negative_months = 0
    positive_pnl = negative_pnl = Fraction()
    for period in periods:
        if period["complete_month"] is not True:
            continue
        count += 1
        pnl = Fraction(period["total_pnl"])
        if pnl > 0:
            positive_months += 1
            positive_pnl += pnl
        elif pnl < 0:
            negative_months += 1
            negative_pnl -= pnl
        peak = Fraction(period["peak_spent"])
        if peak < 0:
            raise ValueError("Отрицательный пик spent")
        if peak:
            returns.append(pnl * 100 / peak)
        profit, loss = (Fraction(period[key]) for key in ("net_profit_closed", "net_loss_closed"))
        factor = period["profit_factor"]
        if period["profit_factor_status"] == "available":
            if factor is None or loss <= 0 or profit < 0 or Fraction(factor) != profit / loss:
                raise ValueError("Месячный PF не совпадает с чистыми результатами закрытий")
            factors.append(Fraction(factor))
        elif factor is not None or period["profit_factor_status"] not in (
            "no_losses",
            "no_closures",
        ):
            raise ValueError("Некорректный статус месячного PF")
    return {
        "median_monthly_pnl_pct": median(returns) if returns else None,
        "median_monthly_profit_factor": median(factors) if factors else None,
        "full_months": count,
        "median_pnl_months": len(returns),
        "median_pnl_missing_months": count - len(returns),
        "median_pf_months": len(factors),
        "median_pf_missing_months": count - len(factors),
        "monthly_net_pnl_ratio": positive_pnl / negative_pnl if negative_pnl else None,
        "monthly_net_pnl_ratio_status": (
            "no_full_months"
            if not count
            else "no_negative_months"
            if not negative_pnl
            else "available"
        ),
        "positive_pnl_months": positive_months,
        "negative_pnl_months": negative_months,
        **MISSING_DRAWDOWN,
    }


def _packed_fraction(value: Any) -> Fraction | None:
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(v, bytes) for v in value)
    ):
        raise ValueError("Некорректная точная дробь")
    numerator, denominator = (int.from_bytes(v, "little", signed=True) for v in value)
    if denominator <= 0:
        raise ValueError("Некорректный знаменатель")
    return Fraction(numerator, denominator)


@lru_cache(maxsize=2401)
def _month_bounds(month: str) -> tuple[int, int]:
    first = datetime.fromisoformat(month + "-01").replace(tzinfo=UTC)
    following = (
        first.replace(year=first.year + 1, month=1)
        if first.month == 12
        else first.replace(month=first.month + 1)
    )
    return int(first.timestamp() * 1000), int(following.timestamp() * 1000)


MEDIAN_PERIOD_POSITIONS = tuple(
    (key, matrix.PERIOD_FIELDS.index(key))
    for key in ("peak_spent", "total_pnl", "net_profit_closed", "net_loss_closed", "profit_factor")
)


def _packed_periods(
    periods: list[Any],
    months: tuple[str, ...],
    expected_periods: tuple[tuple[int, int, bool], ...] | None = None,
) -> Iterable[dict[str, Any]]:
    if len(periods) != len(months):
        raise ValueError("Месячный календарь конфигурации различается")
    if expected_periods is not None and len(periods) != len(expected_periods):
        raise ValueError("Месячный календарь конфигурации различается")
    for index, (period, month) in enumerate(zip(periods, months, strict=True)):
        if not isinstance(period, list) or len(period) != len(matrix.PERIOD_FIELDS):
            raise ValueError("Неверная структура месяца")
        start, end, complete = period[:3]
        expected = _month_bounds(month)
        if (
            not isinstance(complete, bool)
            or not expected[0] <= start < end <= expected[1]
            or complete != ((start, end) == expected)
            or expected_periods is not None
            and (start, end, complete) != expected_periods[index]
        ):
            raise ValueError("Некорректная граница полного месяца UTC")
        if complete:
            yield {
                "complete_month": True,
                "profit_factor_status": period[matrix.PERIOD_FIELDS.index("profit_factor_status")],
                **{key: _packed_fraction(period[index]) for key, index in MEDIAN_PERIOD_POSITIONS},
            }


def build_medians(archive: Archive) -> MedianIndex:
    """One bounded pass; retain only two exact medians and coverage per scope."""
    if signature(archive.publication) != archive.identity:
        raise ValueError("Результаты изменились")
    result: MedianIndex = {}
    with archive.binary.open("rb") as handle:
        for _, config_id, offset, size, checksum in archive.entries:
            payload = matrix._read_payload(handle, offset, size, checksum)
            packed = msgpack.unpackb(matrix._verified_payload_bytes(payload, checksum), raw=False)
            if len(packed) != len(matrix.RESULT_FIELDS) + 1 or packed[0] != config_id:
                raise ValueError("Конфигурация в индексе и бинарном файле различается")
            scopes = packed[-1]["performance"]["binary_monthly_v1"]
            rows = [scopes[0], *scopes[1]]
            if [row[0] for row in rows] != [None, *archive.venues]:
                raise ValueError("Биржи конфигурации различаются")
            result[config_id] = {
                venue: monthly_medians(
                    _packed_periods(
                        row[3], archive.scope_months[venue], archive.scope_periods[venue]
                    )
                )
                for venue, row in zip(("total", *archive.venues), rows, strict=True)
            }
    if signature(archive.publication) != archive.identity:
        raise ValueError("Результаты изменились во время чтения")
    return result


def _median_identity(archive: Archive) -> list[Any]:
    return [list(archive.source_hashes), list(archive.months), ["total", *archive.venues]]


def save_medians(archive: Archive, rows: MedianIndex, output: Path) -> None:
    """Create a separate binary derivative; never replace an existing artifact."""
    packed = []
    for _, config_id, *_ in archive.entries:
        scopes = []
        for venue in ("total", *archive.venues):
            values = []
            for key in MEDIAN_KEYS:
                value: Any = rows[config_id][venue][key]
                if isinstance(value, Fraction):
                    value = [
                        matrix._integer_bytes(str(value.numerator)),
                        matrix._integer_bytes(str(value.denominator)),
                    ]
                values.append(value)
            scopes.append(values)
        packed.append([config_id, scopes])
    raw = msgpack.packb([_median_identity(archive), packed], use_bin_type=True)
    if len(raw) > MEDIAN_LIMIT:
        raise ValueError("Индекс медиан превышает лимит")
    payload = zlib.compress(raw, level=3)
    with output.open("xb") as handle:
        handle.write(MEDIAN_MAGIC)
        handle.write(matrix.RESULT_HEADER.pack(len(raw), hashlib.sha256(raw).digest()))
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def load_medians(archive: Archive, path: Path) -> MedianIndex:
    """Read a small exact derivative tied to the immutable result hashes/policy."""
    stamp(path)
    if path.stat().st_size > MEDIAN_LIMIT:
        raise ValueError("Индекс медиан превышает лимит")
    with path.open("rb") as handle:
        if handle.read(len(MEDIAN_MAGIC)) != MEDIAN_MAGIC:
            raise ValueError("Неизвестный формат индекса медиан")
        header = handle.read(matrix.RESULT_HEADER.size)
        if len(header) != matrix.RESULT_HEADER.size:
            raise ValueError("Индекс медиан усечён")
        size, checksum = matrix.RESULT_HEADER.unpack(header)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(handle.read(MEDIAN_LIMIT), MEDIAN_LIMIT + 1)
    if (
        len(raw) > MEDIAN_LIMIT
        or len(raw) != size
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
        or hashlib.sha256(raw).digest() != checksum
    ):
        raise ValueError("Контрольная сумма индекса медиан не совпала")
    identity, packed = msgpack.unpackb(raw, raw=False)
    if identity != _median_identity(archive) or [row[0] for row in packed] != [
        row[1] for row in archive.entries
    ]:
        raise ValueError("Индекс медиан относится к другому прогону")
    result: MedianIndex = {}
    for config_id, scopes in packed:
        result[config_id] = {}
        for venue, values in zip(("total", *archive.venues), scopes, strict=True):
            if len(values) not in (len(LEGACY_MEDIAN_KEYS), len(MEDIAN_KEYS)):
                raise ValueError("Неверная структура медиан")
            keys = MEDIAN_KEYS if len(values) == len(MEDIAN_KEYS) else LEGACY_MEDIAN_KEYS
            row: Row = dict(zip(keys, values, strict=True))
            for key in MEDIAN_FRACTION_KEYS.intersection(row):
                row[key] = _packed_fraction(row[key])
            if len(values) == len(LEGACY_MEDIAN_KEYS):
                row.update(
                    monthly_net_pnl_ratio=None,
                    monthly_net_pnl_ratio_status="not_computed",
                    positive_pnl_months=None,
                    negative_pnl_months=None,
                )
            result[config_id][venue] = {**row, **MISSING_DRAWDOWN}
    return result


def leaders(rows: list[Row]) -> tuple[Row, Row]:
    """Whole-run portfolio net USDT; ties use the smallest canonical ordinal."""
    ordered = sorted(rows, key=lambda row: cast(int, row["ordinal"]))
    present = [row for row in ordered if row.get("total_pnl") is not None]
    if not present:
        raise ValueError("Нет итогового PnL")
    return (
        max(present, key=lambda row: cast(Any, row["total_pnl"])),
        min(present, key=lambda row: cast(Any, row["total_pnl"])),
    )


def median_pnl_leaders(rows: list[Row]) -> tuple[Row, Row, int] | None:
    """Rank exact portfolio medians only when every full UTC month is represented."""
    if not rows:
        return None
    full_months = {row.get("full_months") for row in rows}
    if len(full_months) != 1 or type(next(iter(full_months))) is not int:
        raise ValueError("Несопоставимое число полных месяцев у конфигураций")
    months = cast(int, next(iter(full_months)))
    if months <= 0:
        return None
    comparable = sorted(
        (
            row
            for row in rows
            if row.get("median_monthly_pnl_pct") is not None
            and row.get("median_pnl_months") == months
            and row.get("median_pnl_missing_months") == 0
        ),
        key=lambda row: cast(int, row["ordinal"]),
    )
    if not comparable:
        return None
    return (
        max(comparable, key=lambda row: cast(Any, row["median_monthly_pnl_pct"])),
        min(comparable, key=lambda row: cast(Any, row["median_monthly_pnl_pct"])),
        len(comparable),
    )


def read_configuration(archive: Archive, config_id: str) -> tuple[list[Row], dict[str, list[Row]]]:
    """Read one verified frame, retaining all its months and scopes without replay."""
    if signature(archive.publication) != archive.identity:
        raise ValueError("Результаты изменились; обновите страницу")
    entry = next((row for row in archive.entries if row[1] == config_id), None)
    if entry is None:
        raise ValueError("Неизвестная конфигурация")
    ordinal, _, offset, size, checksum = entry
    with archive.binary.open("rb") as handle:
        result = matrix.decode_result(
            matrix._read_payload(handle, offset, size, checksum), checksum
        )
    if result["id"] != config_id:
        raise ValueError("Конфигурация в индексе и бинарном файле различается")
    check_configuration_depth(result, archive.configurations[ordinal])
    axes = {key: result[key] for key in matrix.RESULT_FIELDS[:5]}
    axes["grid_depth_pct"] = result.get("grid_depth_pct", "60")
    scopes = {"total": result["performance"]["total"]}
    scopes.update((row["venue"], row) for row in result["performance"]["venues"])
    primary = {"total": result["total"]}
    primary.update((row["venue"], row) for row in result["venues"])
    overall, monthly = [], {}
    for venue in ("total", *archive.venues):
        scope = scopes[venue]
        summary = _row(
            _assemble(
                axes, primary[venue], scope["overall"], result["aggregate_cycles"], venue, True
            ),
            ordinal,
        )
        summary["scope"] = "Весь портфель" if venue == "total" else venue.upper()
        summary.update(monthly_medians(scope["monthly"]))
        status = summary["monthly_net_pnl_ratio_status"]
        summary["monthly_net_pnl_ratio_status"] = STATUS.get(str(status), str(status))
        overall.append(summary)
        periods: list[Row] = []
        cumulative, peak = Fraction(), Fraction()
        if len(scope["monthly"]) != len(archive.scope_months[venue]):
            raise ValueError("Месячный календарь конфигурации различается")
        for month, expected_period, period in zip(
            archive.scope_months[venue],
            archive.scope_periods[venue],
            scope["monthly"],
            strict=True,
        ):
            if (period["start_ms"], period["end_ms"], period["complete_month"]) != expected_period:
                raise ValueError("Месячный календарь конфигурации различается")
            actual_month = datetime.fromtimestamp(period["start_ms"] / 1000, UTC).strftime("%Y-%m")
            if actual_month != month:
                raise ValueError("Месячный календарь конфигурации различается")
            row = _row(dict(axes, **period), ordinal)
            cumulative += Fraction(period["total_pnl"])
            peak = max(peak, Fraction(period["peak_spent"]))
            row.update(
                month=month,
                scope=summary["scope"],
                pnl_cumulative=cumulative,
                cumulative_return_pct=cumulative * 100 / peak if peak else None,
            )
            periods.append(row)
        if cumulative != Fraction(cast(Any, summary["total_pnl"])):
            raise ValueError("Сумма месячного PnL не совпала с итогом")
        monthly[venue] = periods
    if signature(archive.publication) != archive.identity:
        raise ValueError("Результаты изменились во время чтения")
    return overall, monthly


def apply_drawdowns(
    overall: list[Row], monthly: dict[str, list[Row]], detail: dict[str, Any]
) -> None:
    """Overlay verified derivatives without changing original PnL or exposure."""
    scopes = {
        "total": detail["total"],
        **{row["venue"]: row["monthly"] for row in detail["venues"]},
    }
    if set(scopes) != set(monthly):
        raise ValueError("Состав просадок отличается от конфигурации")
    for summary, (venue, rows) in zip(overall, monthly.items(), strict=True):
        percentages: list[Fraction] = []
        complete = 0
        for row, risk in zip(rows, scopes[venue], strict=True):
            if row["start_ms"] != risk["start_ms"] or row["end_ms"] != risk["end_ms"]:
                raise ValueError("Календарь просадок отличается")
            row.update(
                {
                    key: None if risk[key] is None else Fraction(risk[key])
                    for key in ("max_drawdown", "max_drawdown_pct")
                }
            )
            row["max_drawdown_ms"] = risk["max_drawdown_ms"]
            if row["complete_month"] == "Да":
                complete += 1
                if row["max_drawdown_pct"] is not None:
                    percentages.append(cast(Fraction, row["max_drawdown_pct"]))
        summary.update(
            median_monthly_drawdown_pct=median(percentages) if percentages else None,
            median_drawdown_status="Рассчитано"
            if percentages
            else "Нулевой пик spent во всех полных месяцах",
            median_dd_months=len(percentages),
            median_dd_missing_months=complete - len(percentages),
        )


OPERATORS = ("≥", "≤", "=", "≠", "Между", "Нет значения", "Есть значение")
TEXT_OPERATORS = ("Содержит", "=", "≠", "Нет значения", "Есть значение")


def parse_value(text: str, column: Column) -> Scalar:
    if column.kind == "text":
        return text.strip()
    if column.kind == "date":
        value = datetime.fromisoformat(text.strip())
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo("Europe/Moscow"))
        return int(value.timestamp() * 1000)
    try:
        return Fraction(text.strip().replace(",", "."))
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("Введите число, например 100, 0.5 или 1/3") from error


def matches(value: Scalar, operator: str, target: Scalar = None, upper: Scalar = None) -> bool:
    if operator == "Нет значения":
        return value is None
    if operator == "Есть значение":
        return value is not None
    if value is None or target is None:
        return False
    if operator == "Содержит":
        return str(target).casefold() in str(value).casefold()
    if operator == "=":
        return value == target
    if operator == "≠":
        return value != target
    if isinstance(value, str) or isinstance(target, str):
        raise ValueError("Для текста используйте сравнение или поиск")
    if operator == "≥":
        return value >= target
    if operator == "≤":
        return value <= target
    if operator == "Между" and upper is not None and not isinstance(upper, str):
        if target > upper:
            raise ValueError("Нижняя граница больше верхней")
        return target <= value <= upper
    raise ValueError("Неизвестное условие фильтра")


def filter_rows(rows: list[Row], filters: list[tuple[str, str, Scalar, Scalar]]) -> list[Row]:
    return [
        row
        for row in rows
        if all(matches(row.get(key), op, value, upper) for key, op, value, upper in filters)
    ]


def sort_rows(rows: list[Row], key: str, descending: bool = False) -> list[Row]:
    present = [row for row in rows if row.get(key) is not None]
    missing = [row for row in rows if row.get(key) is None]
    # Stable input order resolves equal exact values. Missing values stay last.
    return sorted(present, key=lambda row: cast(Any, row[key]), reverse=descending) + missing


def exact_csv(rows: list[Row], columns: list[str]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([BY_KEY[key].label for key in columns])
    for row in rows:
        writer.writerow(["" if row.get(key) is None else str(row[key]) for key in columns])
    return output.getvalue()
