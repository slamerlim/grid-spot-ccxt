"""Offline adapter for the separate, audited /opt/module1_n C++ simulator.

Archive files are discovered by name, then inspected on selection. The worker
validates the complete source and never imports the network-capable backend.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import signal
import struct
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.backtest_progress import engine_error, publish
from scripts.module1_study import read_json
from scripts.module1_verify_sources import identity, run_child, sha256, utc_ms, write_json

SNAPSHOTS = Path("/opt/grid-runs/backtest-ui/market-snapshots")
BINARY = Path("/opt/module1_n/build/backtest")
DATA_ROOT = Path("/opt/datasets/crypto_1m")
FIELDS = {
    "name",
    "market",
    "start",
    "end",
    "step_pct",
    "take_profit_pct",
    "order_quote",
    "averaging",
    "price_tick",
    "amount_tick",
    "confirm_synthetic",
    "assert_spot",
}
LIMITS = {
    "step_pct": (Decimal("0.000001"), Decimal("99")),
    "take_profit_pct": (Decimal("0.000001"), Decimal("100")),
    "order_quote": (Decimal("0.00000001"), Decimal("100000")),
    "averaging": (Decimal("0"), Decimal("100")),
}


def snapshot(path: Path, venue: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_000_000:
        raise ValueError("Saved market snapshot is unavailable")
    value = read_json(path, max_bytes=4_000_000)
    if (
        not isinstance(value, dict)
        or value.get("schema") != 2
        or value.get("exchange") != venue
        or not isinstance(value.get("markets"), dict)
    ):
        raise ValueError("Saved market snapshot has an unexpected format")
    return value


class ArchiveCatalog:
    """Small filename inventory; inspect only requested files, never scan all data."""

    def __init__(self, root: Path = DATA_ROOT, snapshots: Path = SNAPSHOTS) -> None:
        self.root = root
        self.snapshots = snapshots
        self._snapshots: dict[str, tuple[tuple[int, ...], dict[str, Any]]] = {}
        self._snapshot_lock = threading.Lock()
        self.rows: dict[str, dict[str, Any]] = {}
        with os.scandir(root) as entries:
            for entry in entries:
                parts = entry.name.removesuffix(".bin").split("_")
                if (
                    not entry.name.endswith(".bin")
                    or len(parts) != 3
                    or not all(p and p.isprintable() for p in parts)
                    or not entry.is_file(follow_symlinks=False)
                ):
                    continue
                venue, quote, base = parts
                market = f"{base}/{quote}"
                key = f"{venue}:{market}"
                self.rows[key] = {"id": key, "venue": venue, "market": market, "file": entry.name}
        self.sorted_rows = sorted(self.rows.values(), key=lambda row: row["id"].casefold())

    def precision_snapshot(self, venue: str) -> dict[str, Any] | None:
        """Keep at most one parsed venue snapshot; never cache source candles."""
        path = self.snapshots / f"{venue}.json"
        with self._snapshot_lock:
            try:
                revision = identity(path)
            except FileNotFoundError:
                self._snapshots.pop(venue, None)
                return None
            prior = self._snapshots.get(venue)
            if prior is not None and prior[0] == revision:
                return prior[1]
            value = snapshot(path, venue)
            if identity(path) != revision:
                self._snapshots.pop(venue, None)
                raise ValueError("Saved market snapshot changed while reading")
            self._snapshots.clear()
            self._snapshots[venue] = revision, value
            return value

    def exchange_counts(self) -> dict[str, dict[str, int]]:
        """Count archive filenames by venue without opening their data streams."""
        counts: dict[str, dict[str, int]] = {}
        for row in self.sorted_rows:
            if not row["market"].endswith("/USDT"):
                continue
            venue = row["venue"]
            entry = counts.setdefault(venue, {"total": 0, "usdt": 0})
            entry["total"] += 1
            entry["usdt"] += row["market"].upper().endswith("/USDT")
        return dict(sorted(counts.items(), key=lambda item: item[0].casefold()))

    def expand_selection(
        self, all_exchanges: list[str], include: list[str], exclude: list[str]
    ) -> list[str]:
        """Resolve a compact exchange/pair selection to existing archive IDs."""
        for name, values in (
            ("all_exchanges", all_exchanges),
            ("include", include),
            ("exclude", exclude),
        ):
            if (
                not isinstance(values, list)
                or len(values) > 50000
                or any(not isinstance(item, str) or not 1 <= len(item) <= 100 for item in values)
                or len(values) != len(set(values))
            ):
                raise ValueError(f"Invalid {name} selection")
        venues = set(self.exchange_counts())
        if len(all_exchanges) > len(venues) or any(venue not in venues for venue in all_exchanges):
            raise ValueError("Selection contains an unknown exchange")
        if any(market not in self.rows for market in (*include, *exclude)):
            raise ValueError("Selection contains an unknown archive market")
        if any(not market.endswith("/USDT") for market in include):
            raise ValueError("USDT-only policy: choose spot pairs quoted in USDT")
        selected = {
            row["id"]
            for row in self.sorted_rows
            if row["venue"] in all_exchanges and row["market"].endswith("/USDT")
        }
        selected.update(include)
        selected.difference_update(exclude)
        return sorted(selected, key=str.casefold)

    def search(self, query: str, limit: int = 100, exchange: str = "") -> dict[str, Any]:
        if (
            len(query) > 80
            or not 1 <= limit <= 100
            or (exchange and exchange not in self.exchange_counts())
        ):
            raise ValueError("Search must be at most 80 characters; limit at most 100")
        term = query.casefold().strip()
        matches = [
            row
            for row in self.sorted_rows
            if row["market"].endswith("/USDT")
            and (not exchange or row["venue"] == exchange)
            and term in row["id"].casefold()
        ]
        return {
            "sources": matches[:limit],
            "matches": len(matches),
            "total": sum(r["total"] for r in self.exchange_counts().values()),
            "quote_currency": "USDT",
        }

    def detail(self, market_id: str) -> dict[str, Any]:
        if market_id not in self.rows:
            raise ValueError("Choose an archive market")
        row = self.rows[market_id].copy()
        path = self.root / row["file"]
        info = identity(path)
        size = info[2]
        if size < 24 or size % 24:
            raise ValueError("Archive file is empty or not a 24-byte OHLC stream")
        with path.open("rb") as stream:
            first = struct.unpack("<qffff", stream.read(24))
            stream.seek(-24, os.SEEK_END)
            last = struct.unpack("<qffff", stream.read(24))
        for record in (first, last):
            t, opened, high, low, closed = record
            if (
                not 1_000_000_000_000 <= t <= 4_102_444_800_000
                or t % 60000
                or not all(math.isfinite(p) and p > 0 for p in (opened, high, low, closed))
                or low > min(opened, closed)
                or high < max(opened, closed)
            ):
                raise ValueError("Archive boundary candles are invalid")
        if last[0] < first[0]:
            raise ValueError("Archive timestamps are reversed")
        row.update(
            first=datetime.fromtimestamp(first[0] / 1000, UTC).isoformat(),
            end_exclusive=datetime.fromtimestamp((last[0] + 60000) / 1000, UTC).isoformat(),
            records=size // 24,
            spot_unverified=True,
            likely_leveraged=bool(
                re.search(r"(?:[235][LS]|BULL|BEAR|UP|DOWN)$", row["market"].split("/")[0], re.I)
            ),
        )
        data = self.precision_snapshot(row["venue"])
        if data is not None:
            key = row["file"].removeprefix(row["venue"] + "_").removesuffix(".bin")
            if key in data["markets"]:
                row.update(precision_source="saved_ccxt", snapshot_utc=data["fetched_utc"])
                return row
        row.update(precision_source="synthetic", snapshot_utc=None)
        return row


def settings(payload: object, market: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise ValueError("Supply exactly the audited simulator form fields")
    value = dict(payload)
    if (
        not isinstance(value["name"], str)
        or not 1 <= len(value["name"].strip()) <= 80
        or not value["name"].isprintable()
    ):
        raise ValueError("Run name must contain 1–80 printable characters")
    value["name"] = value["name"].strip()
    if value["market"] != market["id"]:
        raise ValueError("Choose the inspected archive market")
    if not market["id"].endswith("/USDT"):
        raise ValueError("USDT-only policy: choose spot pairs quoted in USDT")
    if market["likely_leveraged"]:
        raise ValueError("Likely leveraged token is outside spot-only research")
    if value["assert_spot"] is not True:
        raise ValueError("Confirm the unverified spot-market assumption")
    if market["precision_source"] == "synthetic" and value["confirm_synthetic"] is not True:
        raise ValueError("Confirm the declared synthetic precision assumptions")
    if market["precision_source"] == "synthetic":
        for key in ("price_tick", "amount_tick"):
            raw = value[key]
            if not isinstance(raw, str) or not re.fullmatch(
                r"(?:0|[1-9]\d{0,7})(?:\.\d{1,12})?", raw
            ):
                raise ValueError(f"{key}: use a positive decimal tick")
            number = Decimal(raw)
            if not Decimal("0.000000000001") <= number <= Decimal("100000"):
                raise ValueError(f"{key}: tick outside supported bounds")
            value[key] = format(number.normalize(), "f")
    else:
        value["price_tick"] = None
        value["amount_tick"] = None
    for key, (low, high) in LIMITS.items():
        raw = value[key]
        if not isinstance(raw, str) or not re.fullmatch(r"\d{1,6}(?:\.\d{1,8})?", raw):
            raise ValueError(f"{key}: use a decimal number")
        number = Decimal(raw)
        if not low <= number <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
        value[key] = format(number.normalize(), "f")
    for key in ("start", "end"):
        if not isinstance(value[key], str) or len(value[key]) > 32:
            raise ValueError("UTC date and time required")
        stamp = utc_ms(value[key])
        if stamp % 60000:
            raise ValueError("Date boundaries must be whole UTC minutes")
        value[key] = datetime.fromtimestamp(stamp / 1000, UTC).isoformat()
    if (
        not utc_ms(market["first"])
        <= utc_ms(value["start"])
        < utc_ms(value["end"])
        <= utc_ms(market["end_exclusive"])
    ):
        raise ValueError("Dates must be inside the selected market's archive coverage")
    value["precision_source"] = market["precision_source"]
    return value


def source_for(manifest: dict[str, Any], market: dict[str, Any], root: Path) -> dict[str, Any]:
    existing = next(
        (row for row in manifest["selected"] if f"{row['venue']}:{row['market']}" == market["id"]),
        None,
    )
    path = root / market["file"]
    current = list(identity(path))
    if existing is not None:
        if (
            existing["file"] != market["file"]
            or current != existing["file_identity_before_and_after"]
        ):
            raise ValueError("Frozen study source identity changed")
        return dict(existing)
    return {
        "venue": market["venue"],
        "market": market["market"],
        "file": market["file"],
        "source_path": str(path),
        "sha256": None,
        "file_identity_before_and_after": current,
    }


def review(folder: Path) -> dict[str, Any]:
    job = read_json(folder / "job.json")
    receipt = read_json(folder / "receipt.json")
    if receipt.get("status") != "PASS":
        raise ValueError("Audited run has no passing receipt")
    if (
        "job.json" in receipt.get("hashes", {})
        and sha256(folder / "job.json") != receipt["hashes"]["job.json"]
    ):
        raise ValueError("Retained research job changed")
    for filename in (
        "config.json",
        "engine",
        "market_snapshot.json",
        "summary.json",
        "trades.bin",
        "series.bin",
    ):
        if sha256(folder / filename) != receipt["hashes"][filename]:
            raise ValueError(f"Retained audited artifact changed: {filename}")
    return validate_result(folder, job)


def chart_data(
    folder: Path, *, max_bars: int = 5000, max_trades: int | None = None
) -> dict[str, Any]:
    """Return verified, bounded OHLC bars and every trade marker in the run.

    Adjacent chart bars are combined as OHLC when necessary; trades are never
    silently omitted. The original binary files remain the full-fidelity record.
    """
    if not 1 <= max_bars <= 5000 or (max_trades is not None and max_trades < 1):
        raise ValueError("Chart bounds are outside the supported range")
    for name in ("series.bin", "trades.bin"):
        if (folder / name).is_symlink():
            raise ValueError(f"Retained audited artifact is a link: {name}")
    summary = review(folder)
    points, trade_count = summary["chart_points"], summary["trades"]
    if points > 100000:
        raise ValueError("Audited chart has more than 100,000 bars")
    if max_trades is not None and trade_count > max_trades:
        raise ValueError(
            f"Audited chart has {trade_count:,} trades; limit is {max_trades:,}. "
            "The full trade file is retained, but chart markers cannot be truncated."
        )
    receipt = read_json(folder / "receipt.json")
    config = read_json(folder / "config.json")
    if receipt.get("source_sha256") != config.get("dataset_sha256"):
        raise ValueError("Audited source hash disagrees with the passing receipt")
    binaries: dict[str, bytes] = {}
    for name in ("series.bin", "trades.bin"):
        data = (folder / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != receipt["hashes"][name]:
            raise ValueError(f"Retained audited artifact changed while reading: {name}")
        binaries[name] = data

    metrics = ("price", "pnl", "spent", "drawdown")
    series: list[dict[str, Any]] = []
    group_size = max(1, (points + max_bars - 1) // max_bars)
    previous_end = 0
    for index, record in enumerate(struct.iter_unpack("<qq16d", binaries["series.bin"])):
        start_ms, end_ms, *values = record
        if start_ms < previous_end or end_ms <= start_ms or not all(map(math.isfinite, values)):
            raise ValueError("Retained audited chart has invalid bars")
        previous_end = end_ms
        bar = {name: list(values[part * 4 : part * 4 + 4]) for part, name in enumerate(metrics)}
        if any(
            low > min(opened, closed) or high < max(opened, closed)
            for opened, high, low, closed in bar.values()
        ):
            raise ValueError("Retained audited chart has invalid OHLC values")
        if index % group_size == 0:
            current: dict[str, Any] = {"start_ms": start_ms, "end_ms": end_ms, **bar}
            series.append(current)
        else:
            current = series[-1]
            current["end_ms"] = end_ms
            for name in metrics:
                current[name][1] = max(current[name][1], bar[name][1])
                current[name][2] = min(current[name][2], bar[name][2])
                current[name][3] = bar[name][3]

    trades: list[dict[str, Any]] = []
    previous_trade = 0
    for record in struct.iter_unpack("<q7dII", binaries["trades.bin"]):
        (
            stamp,
            price,
            quantity,
            quote,
            fee,
            _position_qty,
            _position_cost,
            _realized,
            side,
            cycle,
        ) = record
        if (
            stamp < previous_trade
            or side not in (0, 1)
            or not all(map(math.isfinite, (price, quantity, quote, fee)))
        ):
            raise ValueError("Retained audited chart has invalid trade markers")
        previous_trade = stamp
        trades.append(
            {
                "timestamp_ms": stamp,
                "price": price,
                "quantity": quantity,
                "quote": quote,
                "fee": fee,
                "side": "BUY" if side == 0 else "SELL",
                "cycle": cycle,
            }
        )
    return {
        "series": series,
        "trades": trades,
        "chart_points": points,
        "display_points": len(series),
        "trades_count": trade_count,
        "aggregated": points > len(series),
    }


def validate_result(folder: Path, job: dict[str, Any]) -> dict[str, Any]:
    config = read_json(folder / "config.json")
    summary = read_json(folder / "summary.json", max_bytes=1_048_576)
    if (
        config["dataset"] != job["source"]["source_path"]
        or config["start_ts"] != utc_ms(job["settings"]["start"])
        or config["end_ts"] != utc_ms(job["settings"]["end"])
        or summary.get("series_format_version") != 2
        or summary.get("format_version") != 1
        or summary.get("trades") != (folder / "trades.bin").stat().st_size // 72
        or (folder / "trades.bin").stat().st_size % 72
        or summary.get("chart_points") != (folder / "series.bin").stat().st_size // 144
        or (folder / "series.bin").stat().st_size % 144
        or summary.get("first_ts", 0) < config["start_ts"]
        or summary.get("last_ts", 0) >= config["end_ts"]
    ):
        raise ValueError("Audited run result disagrees with retained inputs")
    return summary


def worker(folder: Path) -> None:
    def stop(_signum: int, _frame: object) -> None:
        raise InterruptedError("Run cancelled; retained artifacts")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    receipt: dict[str, Any] = {"status": "FAIL"}
    engine = "audited_n"
    try:
        job = read_json(folder / "job.json")
        engine = job["engine"]
        publish(
            folder, "validating", "Verifying the archive hash and saved precision", engine=engine
        )
        if (
            job["engine"] not in ("audited_n", "current_reference")
            or sha256(folder / "market_snapshot.json") != job["snapshot_sha256"]
            or sha256(folder / "engine") != job["engine_sha256"]
        ):
            raise ValueError("Prepared audited artifacts changed")
        source = job["source"]
        if not source["market"].endswith("/USDT") or not source["file"].startswith(
            source["venue"] + "_USDT_"
        ):
            raise ValueError("USDT-only policy: backtests require USDT-quoted spot markets")
        path = Path(source["source_path"])
        if (
            path.parent != DATA_ROOT
            or path.name != source["file"]
            or list(identity(path)) != source["file_identity_before_and_after"]
        ):
            raise ValueError("Original dataset identity changed")
        observed_hash = sha256(path)
        source_hash = source["sha256"] or observed_hash
        if observed_hash != source_hash:
            raise ValueError("Original dataset hash changed")
        market_data = snapshot(folder / "market_snapshot.json", source["venue"])
        market_key = source["file"].removeprefix(source["venue"] + "_").removesuffix(".bin")
        market = market_data["markets"][market_key]
        value = job["settings"]
        config = {
            "dataset": str(path),
            "output_dir": str(folder),
            "dataset_identity": dict(
                zip(
                    ("device", "inode", "size", "mtime_ns", "ctime_ns"),
                    identity(path),
                    strict=True,
                )
            ),
            "dataset_sha256": source_hash,
            "engine_sha256": sha256(folder / "engine"),
            "start_ts": utc_ms(value["start"]),
            "end_ts": utc_ms(value["end"]),
            **{key: float(value[key]) for key in LIMITS},
            **{
                key: market[key]
                for key in (
                    "precision_mode",
                    "price_precision_mode",
                    "amount_precision_mode",
                    "price_min_tick",
                    "price_max_tick",
                    "price_precision",
                    "amount_precision",
                )
            },
            "precision_source": value.get("precision_source", "saved_ccxt"),
            "spot_identity_assumed": bool(value.get("assert_spot", False)),
            "max_points": 5000,
            "chart_interval_ms": 0,
            "chart_calendar": "",
            "exchange": source["venue"],
            "symbol": source["market"],
            "market_snapshot_utc": market_data["fetched_utc"],
            "market_ccxt_version": market_data["ccxt_version"],
            "market": market,
        }
        write_json(folder / "config.json", config)
        publish(
            folder,
            "replay",
            "Synthetic O/H/L/C replay; all crossed levels are processed",
            engine=engine,
        )
        receipt.update(
            run_child(
                [
                    str(folder / "engine"),
                    *(["compare-reference"] if job["engine"] == "current_reference" else []),
                    str(folder / "config.json"),
                ],
                folder,
                180,
                log_bytes=32 * 1024 * 1024,
            )
        )
        if receipt["exit_code"] != 0:
            raise ValueError(
                "Engine timed out"
                if receipt["timed_out"]
                else engine_error(folder, "Audited engine refused this run")
            )
        publish(
            folder,
            "verifying",
            "Verifying full chart/trade files and source integrity",
            engine=engine,
        )
        if (
            list(identity(path)) != source["file_identity_before_and_after"]
            or sha256(path) != source_hash
        ):
            raise ValueError("Original dataset changed during replay")
        if sha256(folder / "engine") != config["engine_sha256"]:
            raise ValueError("Engine changed during replay")
        receipt["hashes"] = {
            filename: sha256(folder / filename)
            for filename in (
                "job.json",
                "config.json",
                "engine",
                "market_snapshot.json",
                "summary.json",
                "trades.bin",
                "series.bin",
            )
        }
        receipt["source_sha256"] = source_hash
        validate_result(folder, job)
        receipt["status"] = "PASS"
    except InterruptedError as exc:
        receipt.update(status="CANCELLED", error=str(exc))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        receipt.update(status="FAIL", error=str(exc))
    publish(
        folder,
        receipt["status"].lower(),
        receipt.get("error", "Research replay verified"),
        engine=engine,
    )
    write_json(folder / "receipt.json", receipt)
