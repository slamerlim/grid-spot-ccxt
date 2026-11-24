"""Bounded public archive collection, separate from trading and original datasets.

All published artifacts are immutable. A month is published as one directory
after download, bounded extraction and validation. Resume verifies its checksums.
The collector has no API credentials, exchange client, orders or arbitrary URLs.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import fcntl
import gzip
import hashlib
import json
import mmap
import os
import re
import stat
import struct
import tempfile
import time
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import IO, Any, BinaryIO
from urllib.parse import urlsplit

import aiohttp

VERSION = "historical-collection-v3"
MINUTE = 60_000
BAR = struct.Struct("<qffff")
MAX_COMPRESSED = 16 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
MAX_TRADES = 1_000_000
MAX_MONTHS = 1200
SYMBOL = re.compile(r"[A-Z0-9]{1,24}/[A-Z0-9]{1,24}\Z")
READY = "Already present and validated"
AVAILABLE = "Download available"
VALID = "Downloaded and validated"
INVALID = "Downloaded but invalid"
UNAVAILABLE = "Official source unavailable"
FAILED = "Failed due to transport or transient error"
UNCHECKED = "Not yet checked"


def utc_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Explicit UTC offset required")
    return int(parsed.timestamp() * 1000)


def utc(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def month_bounds(month: str) -> tuple[int, int]:
    if not re.fullmatch(r"20[0-9]{2}(0[1-9]|1[0-2])", month):
        raise ValueError("Invalid UTC month")
    start = datetime.strptime(month, "%Y%m").replace(tzinfo=UTC)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def months(start: int, end: int) -> Iterator[tuple[str, int, int]]:
    if start % MINUTE or end % MINUTE or end <= start:
        raise ValueError("Nonempty half-open minute envelope required")
    current = start
    for _ in range(MAX_MONTHS):
        month = datetime.fromtimestamp(current / 1000, UTC).strftime("%Y%m")
        _, stop = month_bounds(month)
        yield month, current, min(end, stop)
        current = stop
        if current >= end:
            return
    raise ValueError("Month budget exceeded")


def archive_url(exchange: str, market: str, month: str, kind: str = "candles") -> str:
    month_bounds(month)
    if not SYMBOL.fullmatch(market) or kind not in {"candles", "trades"}:
        raise ValueError("Unsupported archive selector")
    if exchange == "gate":
        symbol = market.replace("/", "_")
        data = "candlesticks_1m" if kind == "candles" else "deals"
        return f"https://download.gatedata.org/spot/{data}/{month}/{symbol}-{month}.csv.gz"
    if exchange == "htx":
        symbol = market.replace("/", "")
        date = month[:4] + "-" + month[4:]
        path = (
            f"klines/spot/monthly/{symbol}/1min/{symbol}-1min-{date}.zip"
            if kind == "candles"
            else f"trades/spot/monthly/{symbol}/{symbol}-trades-{date}.zip"
        )
        return "https://www.htx.com/data/data/" + path
    raise ValueError("Only Gate/HTX public spot archives are permitted")


def htx_daily_url(market: str, day: str, kind: str = "candles") -> str:
    if not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", day):
        raise ValueError("Invalid archive day")
    datetime.strptime(day, "%Y-%m-%d")
    archive_url("htx", market, day[:7].replace("-", ""), kind)
    symbol = market.replace("/", "-")
    path = (
        f"klines/{symbol}/1m/{symbol}-klines-1m-{day}.zip"
        if kind == "candles"
        else f"trades/{symbol}/{symbol}-trades-{day}.zip"
    )
    return "https://www.htx.com/data/historical_data/spot/daily/" + path


def check_url(url: str, exchange: str, market: str, month: str, kind: str) -> None:
    parsed = urlsplit(url)
    expected = archive_url(exchange, market, month, kind)
    permitted = {expected}
    if exchange == "htx":
        permitted.add(
            expected.replace("https://www.htx.com/data/data/", "https://futures.huobi.com/data/")
        )
        date = re.search(r"(20[0-9]{2}-[0-9]{2}-[0-9]{2})\.zip\Z", parsed.path)
        if date and date[1][:7].replace("-", "") == month:
            permitted.add(htx_daily_url(market, date[1], kind))
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.query
        or len(url) > 300
        or url not in permitted
    ):
        raise ValueError("Historical transport allowlist refusal")


def source_period(entry: dict[str, Any]) -> tuple[int, int]:
    start, end = month_bounds(entry["month"])
    if entry["exchange"] == "htx" and "/historical_data/spot/daily/" in entry.get("url", ""):
        match = re.search(r"(20[0-9]{2}-[0-9]{2}-[0-9]{2})\.zip\Z", entry["url"])
        if match is None:
            raise ValueError("Missing daily source date")
        day = utc_ms(match[1] + "T00:00:00Z")
        if not start <= day < end:
            raise ValueError("Daily source outside requested month")
        # Current HTX daily files label UTC+08 days: independently checked
        # against all 1,440 timestamps in four 2026-08-31 official archives.
        day -= 8 * 60 * MINUTE
        return day, day + 1440 * MINUTE
    return start, end


def identity(path: Path) -> list[int]:
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError(f"Regular nonsymlink source required: {path}")
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def read_only(path: Path) -> BinaryIO:
    # O_NOATIME also preserves original access timestamps on the owned archive.
    return os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME), "rb")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with read_only(path) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


class LocalArchive:
    def __init__(self, path: Path, expected: Sequence[int]) -> None:
        self.path = path
        self.before = identity(path)
        if self.before != list(expected) or self.before[2] % BAR.size:
            raise ValueError(f"Archive identity differs from retained audit: {path}")
        with read_only(path) as stream:
            self.data = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        self.count = len(self.data) // BAR.size

    def lower(self, timestamp: int) -> int:
        low, high = 0, self.count
        while low < high:
            middle = (low + high) // 2
            if struct.unpack_from("<q", self.data, middle * BAR.size)[0] < timestamp:
                low = middle + 1
            else:
                high = middle
        return low

    def row(self, timestamp: int) -> tuple[float, ...] | None:
        index = self.lower(timestamp)
        if index >= self.count:
            return None
        row = BAR.unpack_from(self.data, index * BAR.size)
        return tuple(row[1:]) if row[0] == timestamp else None

    def close(self) -> None:
        self.data.close()
        if identity(self.path) != self.before:
            raise ValueError(f"Source changed during read: {self.path}")


def inventory(requirements: list[dict[str, Any]], audit: Path, root: Path) -> list[dict[str, Any]]:
    with read_only(audit) as stream:
        records = [json.loads(line) for line in stream]
    by_market = {(r["venue"], r["market"]): r for r in records}
    result = []
    for requirement in requirements:
        venue, market = requirement["exchange"], requirement["market"]
        record = by_market[venue, market]
        if record["classification"] not in {"VALID_WITH_GAPS", "VALID_CONTIGUOUS"}:
            raise ValueError("A validated local archive is required")
        path = root / record["file"]
        local = LocalArchive(path, record["source_identity"])
        try:
            for month, start, end in months(requirement["start_ms"], requirement["end_ms"]):
                count = local.lower(end) - local.lower(start)
                expected = (end - start) // MINUTE
                result.append(
                    {
                        **requirement,
                        "month": month,
                        "required_start_ms": start,
                        "required_end_ms": end,
                        "source_file": str(path),
                        "source_sha256": record["sha256"],
                        "source_identity": record["source_identity"],
                        "local_candles": count,
                        "expected_minutes": expected,
                        "absent_minute_timestamps": expected - count,
                        "status": READY if count == expected else UNCHECKED,
                        "source_availability": UNCHECKED,
                        "url": archive_url(venue, market, month),
                        "first_local_ms": record["first_record_ms"],
                        "last_local_ms": record["last_record_ms"],
                        "expected_format": "gate_csv_gzip" if venue == "gate" else "htx_csv_zip",
                        "listing_status": "First observation is not a verified listing date",
                    }
                )
        finally:
            local.close()
    if len(result) > MAX_MONTHS:
        raise ValueError("Collection month budget exceeded")
    return result


def decimal(value: str, *, positive: bool = False) -> Decimal:
    if len(value) > 100:
        raise ValueError("Numeric field length exceeded")
    number = Decimal(value)
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError("Invalid price/volume")
    if number and abs(number.adjusted()) > 60:
        raise ValueError("Numeric magnitude exceeded")
    return number


@dataclass(frozen=True)
class Candle:
    time_ms: int
    prices: tuple[Decimal, Decimal, Decimal, Decimal]
    quote_volume: Decimal | None
    base_volume: Decimal | None
    raw: tuple[str, ...]
    reported_volume: Decimal | None = None


def candles(path: Path, exchange: str, month: str, market: str | None = None) -> Iterator[Candle]:
    start, end = month_bounds(month)
    previous = start - MINUTE
    daily = False
    instrument = market.replace("/", "-") if market else None
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        for number, row in enumerate(reader):
            if number == 0 and exchange == "htx" and row and row[0] == "instId":
                if row != [
                    "instId",
                    "open",
                    "high",
                    "low",
                    "close",
                    "vol",
                    "volCcy",
                    "volCcyQuote",
                    "ts",
                ]:
                    raise ValueError("Unexpected HTX daily candle header")
                daily = True
                start -= 8 * 60 * MINUTE
                previous = start - MINUTE
                continue
            if number == 0 and row and row[0].lower() in {"id", "timestamp", "time"}:
                continue
            if number > 44_641 or sum(map(len, row)) > 2048:
                raise ValueError("Candle row/field budget exceeded")
            time_column = 0
            if exchange == "htx" and daily and len(row) == 9:
                instrument = instrument or row[0]
                if row[0] != instrument:
                    raise ValueError("HTX instrument mismatch")
                prices = tuple(decimal(row[i], positive=True) for i in (1, 2, 3, 4))
                reported_volume = decimal(row[5])
                decimal(row[6])
                decimal(row[7])
                # Preserve all three volume strings. The newer download schema
                # must not inherit units from the older seven-column REST schema.
                qvolume = bvolume = None
                time_column = 8
            elif exchange == "gate" and len(row) in {6, 7, 8}:
                prices = tuple(decimal(row[i], positive=True) for i in (5, 3, 4, 2))
                reported_volume = decimal(row[1])
                # Older download CSVs are not the current REST schema. Preserve
                # their volume, but do not guess its currency from column position.
                qvolume = reported_volume if len(row) >= 7 else None
                bvolume = decimal(row[6]) if len(row) >= 7 else None
                if len(row) == 8 and row[7].lower() != "true":
                    raise ValueError("Unfinished candle")
            elif exchange == "htx" and not daily and len(row) == 7:
                prices = tuple(decimal(row[i], positive=True) for i in (1, 3, 4, 2))
                qvolume, bvolume = decimal(row[5]), decimal(row[6])
                reported_volume = qvolume
            else:
                raise ValueError("Unexpected candle schema")
            stamp = decimal(row[time_column], positive=True) * 1000
            timestamp = int(stamp)
            if stamp != timestamp or timestamp % MINUTE or not start <= timestamp < end:
                raise ValueError("Candle timestamp outside UTC month/minute")
            if timestamp <= previous:
                raise ValueError("Duplicate or out-of-order candle")
            opened, high, low, close = prices
            if not low <= min(opened, close) <= max(opened, close) <= high:
                raise ValueError("Invalid OHLC bounds")
            previous = timestamp
            yield Candle(
                timestamp, (opened, high, low, close), qvolume, bvolume, tuple(row), reported_volume
            )


@dataclass(frozen=True)
class Trade:
    seconds: Decimal
    trade_id: str
    price: Decimal
    amount: Decimal
    side: str
    raw: tuple[str, ...]

    @property
    def minute_ms(self) -> int:
        return int(self.seconds // 60) * MINUTE


def trades(path: Path, exchange: str, month: str, market: str | None = None) -> Iterator[Trade]:
    start, end = month_bounds(month)
    previous = Decimal(start) / 1000
    seen: set[str] = set()
    daily = False
    instrument = market.replace("/", "-") if market else None
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for number, row in enumerate(csv.reader(stream)):
            if number == 0 and exchange == "htx" and row and row[0] == "instId":
                if row != ["instId", "tradeId", "px", "side", "size", "ts"]:
                    raise ValueError("Unexpected HTX daily trade header")
                daily = True
                start -= 8 * 60 * MINUTE
                previous = Decimal(start) / 1000
                continue
            if number == 0 and row and row[0].lower() in {"id", "timestamp", "time"}:
                continue
            if len(row) != (6 if daily else 5) or number >= MAX_TRADES or sum(map(len, row)) > 2048:
                raise ValueError("Unexpected trade schema or row budget")
            if exchange == "gate":
                seconds, trade_id, price, amount, side = row
                timestamp = decimal(seconds, positive=True)
            elif exchange == "htx":
                if daily:
                    instrument = instrument or row[0]
                    if row[0] != instrument:
                        raise ValueError("HTX instrument mismatch")
                    _, trade_id, price, side, amount, milliseconds = row
                else:
                    trade_id, milliseconds, price, amount, side = row
                timestamp = decimal(milliseconds, positive=True) / 1000
            else:
                raise ValueError("Unknown trade exchange")
            trade_id = trade_id.strip()
            if not Decimal(start) <= timestamp * 1000 < Decimal(end) or timestamp < previous:
                raise ValueError("Trade timestamp outside UTC month or out of order")
            if not trade_id.isascii() or not trade_id.isdigit() or trade_id in seen:
                raise ValueError("Missing or duplicate trade ID")
            seen.add(trade_id)
            previous = timestamp
            yield Trade(
                timestamp,
                trade_id,
                decimal(price, positive=True),
                decimal(amount, positive=True),
                side,
                tuple(row),
            )


def reconstruct(observations: Iterator[Trade]) -> Iterator[Candle]:
    """Only minutes with source trades; equal-time order is source-file order."""
    stamp: int | None = None
    prices: list[Decimal] = []
    base = quote = Decimal(0)
    with localcontext() as context:
        context.prec = 80
        for trade in observations:
            if trade.minute_ms != stamp:
                if stamp is not None:
                    yield Candle(
                        stamp, (prices[0], prices[1], prices[2], prices[3]), quote, base, ()
                    )
                stamp = trade.minute_ms
                prices = [trade.price] * 4
                base = quote = Decimal(0)
            prices[1], prices[2], prices[3] = (
                max(prices[1], trade.price),
                min(prices[2], trade.price),
                trade.price,
            )
            base += trade.amount
            quote += trade.price * trade.amount
        if stamp is not None:
            yield Candle(stamp, (prices[0], prices[1], prices[2], prices[3]), quote, base, ())


def valuation(
    observations: Sequence[tuple[int, Decimal | None, bool]], moment: int, *, max_age_ms: int = 0
) -> dict[str, Any]:
    """Analysis helper; does not enable stale marks in accounting or trading."""
    if not observations or max_age_ms < 0:
        return {"quality": "unavailable", "rate": None}
    ages = []
    with localcontext() as context:
        context.prec = 80
        result = Decimal(1)
        for candle_open, price, inverse in observations:
            age = moment - (candle_open + MINUTE)
            if price is None or not price.is_finite() or price <= 0 or not 0 <= age <= max_age_ms:
                return {"quality": "unavailable", "rate": None}
            ages.append(age)
            result *= Decimal(1) / price if inverse else price
    quality = "derived" if len(observations) > 1 or observations[0][2] else "observed"
    if any(ages):
        quality = "stale" if len(set(ages)) == 1 else "approximate"
    return {"quality": quality, "rate": str(result), "source_age_ms": ages}


def bounded_copy(source: IO[bytes] | gzip.GzipFile, target: BinaryIO, limit: int) -> int:
    size = 0
    while chunk := source.read(min(64 * 1024, limit + 1 - size)):
        size += len(chunk)
        if size > limit:
            raise ValueError("Expanded archive size limit exceeded")
        target.write(chunk)
    return size


def extract(archive: Path, output: Path, limit: int = MAX_EXPANDED) -> None:
    """Never extract a supplied filename or use extractall."""
    with output.open("xb") as target:
        if archive.suffix == ".gz":
            with gzip.open(archive, "rb") as stream:
                bounded_copy(stream, target, limit)
        elif archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zipped:
                members = zipped.infolist()
                if len(members) != 1:
                    raise ValueError("Exactly one CSV archive member required")
                member = members[0]
                name = member.filename
                if (
                    not re.fullmatch(r"[A-Za-z0-9_.-]+\.csv", name)
                    or ".." in name
                    or member.is_dir()
                    or stat.S_ISLNK(member.external_attr >> 16)
                    or member.flag_bits & 1
                    or member.file_size > limit
                ):
                    raise ValueError("Unsafe or oversized archive member")
                with zipped.open(member) as stream:
                    bounded_copy(stream, target, limit)
        else:
            raise ValueError("Only gzip CSV or single-CSV ZIP permitted")
        target.flush()
        os.fsync(target.fileno())


def validate_file(
    csv_path: Path, entry: dict[str, Any], kind: str, *, write_derived: bool = True
) -> dict[str, Any]:
    source_start, source_end = source_period(entry)
    local = LocalArchive(Path(entry["source_file"]), entry["source_identity"])
    result: dict[str, Any] = {
        "parser": VERSION,
        "status": VALID,
        "source_start_ms": source_start,
        "source_end_ms_exclusive": source_end,
        "candles": 0,
        "within_required_period": 0,
        "new_minutes": 0,
        "overlapping_minutes": 0,
        "float32_matching_minutes": 0,
        "conflicting_minutes": 0,
        "conflict_examples": [],
        "first_ms": None,
        "last_ms": None,
        "trade_count": None,
        "base_volume_rows": 0,
        "quote_volume_rows": 0,
        "reported_volume_rows": 0,
        "volume_unit_note": (
            "Old HTX vol/amount fields follow the documented REST schema. "
            "New HTX daily volume fields and Gate six-column volume retain raw values "
            "without assigning base/quote units. Trade reconstruction sums source amounts; "
            "volume-unit certification remains separate from price/timestamp validation."
        ),
    }

    def counted_trades() -> Iterator[Trade]:
        result["trade_count"] = 0
        for trade in trades(csv_path, entry["exchange"], entry["month"], entry["market"]):
            result["trade_count"] += 1
            yield trade

    observations = (
        reconstruct(counted_trades())
        if kind == "trades"
        else candles(csv_path, entry["exchange"], entry["month"], entry["market"])
    )
    derived = None
    try:
        if kind == "trades" and write_derived:
            derived = (csv_path.parent / "reconstructed.csv").open("x", newline="")
            writer = csv.writer(derived)
            writer.writerow(
                ["time_ms", "open", "high", "low", "close", "base_volume", "quote_volume"]
            )
            result["derivation"] = (
                "Trade-derived, not official candles. Equal-time ordering uses source row order."
            )
        for candle in observations:
            if not source_start <= candle.time_ms < source_end:
                raise ValueError("Observation outside declared source day/month")
            if kind == "trades" and write_derived:
                writer.writerow(
                    [
                        candle.time_ms,
                        *map(str, candle.prices),
                        str(candle.base_volume),
                        str(candle.quote_volume),
                    ]
                )
            result["candles"] += 1
            result["base_volume_rows"] += candle.base_volume is not None
            result["quote_volume_rows"] += candle.quote_volume is not None
            result["reported_volume_rows"] += candle.reported_volume is not None
            if result["first_ms"] is None:
                result["first_ms"] = candle.time_ms
            result["last_ms"] = candle.time_ms
            if not entry["required_start_ms"] <= candle.time_ms < entry["required_end_ms"]:
                continue
            result["within_required_period"] += 1
            original = local.row(candle.time_ms)
            if original is None:
                result["new_minutes"] += 1
                continue
            result["overlapping_minutes"] += 1
            # Compare at the legacy precision, while preserving original source decimals.
            rounded = struct.pack("<4f", *[float(p) for p in candle.prices])
            if rounded == struct.pack("<4f", *original):
                result["float32_matching_minutes"] += 1
            else:
                result["conflicting_minutes"] += 1
                if len(result["conflict_examples"]) < 10:
                    result["conflict_examples"].append(
                        {
                            "time_ms": candle.time_ms,
                            "source": list(map(str, candle.prices)),
                            "local_exact": [str(Decimal.from_float(p)) for p in original],
                        }
                    )
        if result["candles"] == 0 and kind == "candles":
            raise ValueError("Empty candle archive")
        result["empty_trade_classification"] = (
            "no_trades_observed_in_source" if result["trade_count"] == 0 else None
        )
    finally:
        if derived is not None:
            derived.close()
        local.close()
    return result


def retryable(status: int) -> bool:
    return status in {429, 500, 502, 503, 504}


class ArchiveTransport:
    """Two connections, two starts/second, three attempts, 30s request timeout."""

    def __init__(self, byte_budget: int = 256 * 1024 * 1024) -> None:
        self.session: aiohttp.ClientSession | None = None
        self.lock = asyncio.Lock()
        self.next_start = 0.0
        if not 0 < byte_budget <= 1024 * 1024 * 1024:
            raise ValueError("Compressed byte budget must be within 1 GiB")
        self.byte_budget = byte_budget
        self.transferred = 0

    async def request(
        self,
        entry: dict[str, Any],
        method: str,
        target: Path | None = None,
        *,
        kind: str = "candles",
    ) -> dict[str, Any]:
        url = entry["url"]
        check_url(url, entry["exchange"], entry["market"], entry["month"], kind)
        if method not in {"GET", "HEAD"} or (method == "GET") != (target is not None):
            raise ValueError("Read-only archive GET/HEAD required")
        if target is not None and target.exists():
            raise FileExistsError(target)
        if self.session is None:
            self.session = aiohttp.ClientSession(
                trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(),
                auto_decompress=False,
                connector=aiohttp.TCPConnector(limit=2, limit_per_host=2),
                timeout=aiohttp.ClientTimeout(total=30),
            )
        attempts: list[dict[str, Any]] = []
        for attempt in range(3):
            async with self.lock:
                await asyncio.sleep(max(0, self.next_start - time.monotonic()))
                self.next_start = time.monotonic() + 0.5
            result: dict[str, Any] = {"url": url, "retrieved_at": datetime.now(UTC).isoformat()}
            wait = float(2**attempt)
            try:
                async with self.session.request(method, url, allow_redirects=False) as response:
                    result.update(
                        status_code=response.status,
                        headers={
                            k: response.headers.get(k)
                            for k in (
                                "Content-Length",
                                "Content-Type",
                                "ETag",
                                "Last-Modified",
                                "Retry-After",
                            )
                        },
                    )
                    declared = response.headers.get("Content-Length")
                    if response.status == 200 and declared and int(declared) > MAX_COMPRESSED:
                        raise ValueError("Compressed response size limit exceeded")
                    if method == "GET" and response.status == 200:
                        assert target is not None
                        size = 0
                        with target.open("xb") as stream:
                            async for chunk in response.content.iter_chunked(64 * 1024):
                                size += len(chunk)
                                self.transferred += len(chunk)
                                if size > MAX_COMPRESSED or self.transferred > self.byte_budget:
                                    raise ValueError("Compressed response size limit exceeded")
                                stream.write(chunk)
                            stream.flush()
                            os.fsync(stream.fileno())
                        if declared and size != int(declared):
                            raise ValueError("Truncated or inconsistent HTTP response")
                        result.update(bytes=size, sha256=sha256(target))
                    if not retryable(response.status):
                        result["attempts"] = attempts
                        return result
                    after = response.headers.get("Retry-After")
                    if after:
                        if not after.isdigit() or int(after) > 60:
                            result.update(
                                error="Retry-After requires a later explicit run", attempts=attempts
                            )
                            return result
                        wait = max(wait, float(after) + 0.1)
            except (aiohttp.ClientError, TimeoutError) as exc:
                result["error"] = type(exc).__name__ + ": " + str(exc)
            except (ValueError, OSError) as exc:
                result.update(error=str(exc), attempts=attempts)
                return result
            # Only incomplete files owned by this attempt can be removed for retry.
            if target is not None and target.exists():
                target.unlink()
            attempts.append(result.copy())
            if attempt < 2:
                await asyncio.sleep(wait)
        result["attempts"] = attempts
        return result

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None


def artifact_dir(root: Path, entry: dict[str, Any], kind: str) -> Path:
    archive_url(entry["exchange"], entry["market"], entry["month"], kind)
    exchange: str = entry["exchange"]
    market: str = entry["market"]
    month: str = entry["month"]
    return root / "data" / exchange / market.replace("/", "_") / "1m" / month[:4] / month[4:] / kind


def verify_artifact(destination: Path, entry: dict[str, Any], kind: str) -> dict[str, Any]:
    check_url(entry["url"], entry["exchange"], entry["market"], entry["month"], kind)
    receipt: dict[str, Any] = json.loads((destination / "receipt.json").read_text())
    if receipt["url"] != entry["url"]:
        raise ValueError("Resume source URL mismatch")
    for name, expected in receipt["files"].items():
        if Path(name).name != name or sha256(destination / name) != expected["sha256"]:
            raise ValueError("Retained artifact checksum mismatch")
        if (destination / name).stat().st_size != expected["bytes"]:
            raise ValueError("Retained artifact size mismatch")
    if receipt["source_identity"] != entry["source_identity"]:
        raise ValueError("Resume local archive identity mismatch")
    if identity(Path(entry["source_file"])) != entry["source_identity"]:
        raise ValueError("Local archive changed since collection")
    return receipt


async def collect_one(
    root: Path, entry: dict[str, Any], transport: ArchiveTransport, *, kind: str = "candles"
) -> dict[str, Any]:
    destination = artifact_dir(root, entry, kind)
    if destination.exists():
        return verify_artifact(destination, entry, kind)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".collect-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        raw = stage / ("original.csv.gz" if entry["exchange"] == "gate" else "original.zip")
        wire = await transport.request(entry, "GET", raw, kind=kind)
        if wire.get("status_code") != 200 or wire.get("error"):
            return {
                "status": UNAVAILABLE if wire.get("status_code") == 404 else FAILED,
                "wire": wire,
                "url": entry["url"],
            }
        receipt = {
            "url": entry["url"],
            "exchange": entry["exchange"],
            "market": entry["market"],
            "month": entry["month"],
            "kind": kind,
            "parser": VERSION,
            "wire": wire,
            "source_identity": entry["source_identity"],
            "source_sha256": entry["source_sha256"],
            "required_start_ms": entry["required_start_ms"],
            "required_end_ms": entry["required_end_ms"],
        }
        try:
            extract(raw, stage / "original.csv")
            receipt.update(validate_file(stage / "original.csv", entry, kind))
        except (
            ValueError,
            ArithmeticError,
            OSError,
            EOFError,
            csv.Error,
            zipfile.BadZipFile,
        ) as exc:
            receipt.update(status=INVALID, error=type(exc).__name__ + ": " + str(exc))
        receipt["files"] = {
            p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)}
            for p in stage.iterdir()
            if p.is_file()
        }
        save_new(stage / "receipt.json", receipt)
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(stage, destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return receipt


@contextmanager
def collection_lock(root: Path) -> Iterator[None]:
    resolved = root.resolve()
    if any(resolved.is_relative_to(p) for p in (Path("/opt/datasets"), Path("/opt/module1_n"))):
        raise ValueError("Protected source trees cannot be collection destinations")
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / ".collector.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def checked_entries(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads((root / "monthly-inventory.json").read_text())
    if len(rows) > MAX_MONTHS:
        raise ValueError("Month budget exceeded")
    checks = {}
    for path in sorted(root.glob("availability*.jsonl"), key=lambda p: p.stat().st_mtime_ns):
        for line in path.read_text().splitlines():
            r = json.loads(line)
            checks[r["exchange"], r["market"], r["month"]] = r
    for row in rows:
        check_url(row["url"], row["exchange"], row["market"], row["month"], "candles")
        head = checks.get((row["exchange"], row["market"], row["month"]))
        if head is not None:
            check_url(head["url"], row["exchange"], row["market"], row["month"], "candles")
            row["url"] = head["url"]
            row["availability_check"] = head
            code = head.get("status_code")
            row["source_availability"] = (
                AVAILABLE
                if code == 200 and not head.get("error")
                else (UNAVAILABLE if code == 404 else FAILED)
            )
            if row["status"] != READY:
                row["status"] = row["source_availability"]
        destination = artifact_dir(root, row, "candles")
        if destination.exists():
            row["collection"] = verify_artifact(destination, row, "candles")
            row["status"] = row["collection"]["status"]
            row["artifact_directory"] = str(destination)
    return rows


async def run_collection(root: Path, byte_budget: int) -> None:
    rows = checked_entries(root)
    pending = [r for r in rows if r["status"] == AVAILABLE]
    estimate = sum(
        int(r["availability_check"]["headers"].get("Content-Length") or MAX_COMPRESSED)
        for r in pending
    )
    if estimate > byte_budget:
        raise ValueError(
            f"Proposed compressed bytes {estimate} exceed {byte_budget}; review required"
        )
    transport = ArchiveTransport(byte_budget)
    spent = 0
    # Sequential parsing and publishing bounds memory and gives priority markets first.
    try:
        for index, row in enumerate(pending):
            declared = int(
                row["availability_check"]["headers"].get("Content-Length") or MAX_COMPRESSED
            )
            if spent + declared > byte_budget:
                raise ValueError("Collection byte budget exhausted")
            result = await collect_one(root, row, transport)
            spent += result.get("wire", {}).get("bytes", 0)
            print(
                json.dumps(
                    {
                        "done": index + 1,
                        "of": len(pending),
                        "market": row["market"],
                        "month": row["month"],
                        "status": result["status"],
                        "new_minutes": result.get("new_minutes"),
                        "conflicts": result.get("conflicting_minutes"),
                    }
                ),
                flush=True,
            )
            if result["status"] == FAILED:
                save_new(
                    root
                    / "failures"
                    / (
                        row["exchange"]
                        + "_"
                        + row["market"].replace("/", "_")
                        + "_"
                        + row["month"]
                        + "_"
                        + str(time.time_ns())
                        + ".json"
                    ),
                    result,
                )
    finally:
        await transport.close()


async def check_availability(root: Path) -> None:
    """Retain every HEAD receipt; never repeat an already recorded 404."""
    rows = checked_entries(root)
    pending = [r for r in rows if r["status"] in {UNCHECKED, FAILED}]
    transport = ArchiveTransport()
    try:
        with (root / f"availability-{time.time_ns()}.jsonl").open("x") as output:
            for row in pending:
                result = await transport.request(row, "HEAD")
                result.update(exchange=row["exchange"], market=row["market"], month=row["month"])
                output.write(json.dumps(result) + "\n")
                output.flush()
    finally:
        await transport.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "availability", "collect", "manifest"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-compressed-bytes", type=int, default=256 * 1024 * 1024)
    args = parser.parse_args()
    with collection_lock(args.root):
        if args.action == "collect":
            asyncio.run(run_collection(args.root, args.max_compressed_bytes))
        elif args.action == "availability":
            asyncio.run(check_availability(args.root))
        elif args.action == "manifest":
            if args.output is None:
                parser.error("--output must name a new manifest file")
            save_new(args.output, checked_entries(args.root))
        else:
            if args.output is None:
                parser.error("--output must name a new inventory file")
            requirements = json.loads((args.root / "requirements.json").read_text())
            save_new(
                args.output,
                inventory(
                    requirements,
                    Path("/opt/grid-runs/htx-gate-integrity-20261112-compact/files.jsonl"),
                    Path("/opt/datasets/crypto_1m"),
                ),
            )


if __name__ == "__main__":
    main()
