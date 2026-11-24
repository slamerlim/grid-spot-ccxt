"""Immutable archive preflight and bounded public metadata for the research sweep.

This module has no order/account API and does not change the live gateway. The
original preparation remains strict; an explicit selection can retain only
fully documented markets for a separately identified seven-venue study.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import math
import os
import selectors
import stat
import struct
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

VENUES = ("okx", "htx", "gate", "xt", "bingx", "kucoin", "bitfinex")
GAP_FREE_SCHEMA = "grid-research-sweep-selection-v2"
REVIEWED_SCHEMA = "grid-research-sweep-selection-v3"
CURRENT_RULES_SCHEMA = "grid-research-sweep-selection-v4"
FLAT_HOUR_SCHEMA = "grid-research-sweep-selection-v5"
REVIEW_SCHEMA = "reviewed-exclusions-v1"
REVIEWED_READINESS = "PENDING_HISTORICAL_RULES_AND_STATISTICS_REVIEW"
CURRENT_RULES_READINESS = "CURRENT_PUBLIC_RULES_ASSUMPTION"
GAP_FREE_POLICY = "gap_free_own_span"
GAP_FREE_VENUES = ("bingx", "gate", "htx", "kucoin", "okx")
FLAT_HOUR_MINUTES = 60
FLAT_HOUR_REASON = "identical_flat_ohlc_run_at_least_60_minutes"
DEFAULT_FLAT_AUDIT = Path("/opt/grid-runs/gap-free-recheck-20261122-3tzk2l35/pair-audit.jsonl")
DATA_ROOT = Path("/opt/datasets/crypto_1m")
RECORD = struct.Struct("<qffff")
MAX_SOURCES = 10_000
CHUNK_RECORDS = 262_144
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
CPP_SCANNERS = (
    Path("/opt/grid-builds/module1/research-sweep-release/grid_backtest"),
    Path("/opt/grid-builds/module1/release/grid_backtest"),
)
SCANNER_SECONDS = 120.0
SCANNER_OUTPUT_BYTES = 64 * 1024
DEFAULT_SNAPSHOT_DIRS = (
    Path("/opt/grid-runs/research-sweep-20261119/preparation/catalogues"),
    Path("/opt/grid-runs/backtest-ui/market-snapshots"),
    Path("/opt/grid-runs/backtest-ui/market-snapshots/versions"),
    Path("/opt/module1_n/runtime/markets"),
)
CCXT_VERSION = "4.5.91"
RESEARCH_CCXT_VERSION = "4.5.93"
ENDPOINTS = {
    "okx": ("www.okx.com", "/api/v5/public/instruments", (("instType", "SPOT"),)),
    "htx": ("api.huobi.pro", "/v1/common/symbols", ()),
    "gate": ("api.gateio.ws", "/api/v4/spot/currency_pairs", ()),
    "xt": ("sapi.xt.com", "/v4/public/symbol", ()),
    "bingx": ("open-api.bingx.com", "/openApi/spot/v1/common/symbols", ()),
    "kucoin": ("api.kucoin.com", "/api/v2/symbols", ()),
    "bitfinex": ("api-pub.bitfinex.com", "/v2/conf/pub:info:pair", ()),
}
DOCUMENTATION = {
    "okx": "https://www.okx.com/docs-v5/en/#rest-api-public-data-get-instruments",
    "htx": "https://huobiapi.github.io/docs/spot/v1/en/",
    "gate": "https://www.gate.com/docs/developers/apiv4/en/",
    "xt": "https://doc.xt.com/docs/spot/Market/GetSymbolInformation",
    "bingx": "https://bingx-api.github.io/docs-v3/",
    "kucoin": "https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-all-symbols",
    "bitfinex": "https://docs.bitfinex.com/reference/rest-public-conf",
}
BITFINEX_SPOT_URL = "https://api-pub.bitfinex.com/v2/conf/pub:list:pair:exchange"
# These are interpretations of the documented ordinary exchange LIMIT rules,
# never a fallback for a missing per-pair rule in another market or order type.
NO_SEPARATE_COST_MINIMUM = {
    "okx": "https://my.okx.com/docs-v5/log_en/#2026-08-18",
    "bitfinex": "https://support.bitfinex.com/hc/en-us/articles/115003283709-What-is-the-minimum-order-size-on-Bitfinex",
}


def identity(info: os.stat_result) -> list[int]:
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _publish(path: Path, value: object) -> None:
    encoded = (
        json.dumps(
            _json_value(value), ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2
        ).encode("utf-8")
        + b"\n"
    )
    with path.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def inventory_sources(dataset_root: Path = DATA_ROOT) -> list[dict[str, Any]]:
    """Read filenames/stat and endpoint records only; no coverage/profit filtering."""
    root = dataset_root / "crypto_1m" if (dataset_root / "crypto_1m").is_dir() else dataset_root
    rows: list[dict[str, Any]] = []
    with os.scandir(root) as entries:
        for entry in entries:
            parts = entry.name.split("_", 2)
            if len(parts) != 3 or parts[0] not in VENUES or parts[1] != "USDT":
                continue
            if not parts[2].endswith(".bin"):
                continue
            base = parts[2][:-4]
            info = entry.stat(follow_symlinks=False)
            row: dict[str, Any] = {
                "id": f"{parts[0]}:{base}/USDT",
                "venue": parts[0],
                "symbol": f"{base}/USDT",
                "path": str(root.absolute() / entry.name),
                "identity": identity(info),
                "bytes": info.st_size,
                "records": info.st_size // RECORD.size,
                "first_ms": None,
                "last_ms": None,
                "sha256": None,
                "inventory_errors": [],
            }
            if not stat.S_ISREG(info.st_mode):
                row["inventory_errors"].append("not_regular_nonsymlink")
            elif info.st_size == 0 or info.st_size % RECORD.size:
                row["inventory_errors"].append("invalid_record_length")
            else:
                fd = os.open(row["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as stream:
                    row["first_ms"] = RECORD.unpack(stream.read(RECORD.size))[0]
                    stream.seek(-RECORD.size, os.SEEK_END)
                    row["last_ms"] = RECORD.unpack(stream.read(RECORD.size))[0]
                    if identity(os.fstat(stream.fileno())) != row["identity"]:
                        raise ValueError("source changed during inventory")
                if identity(Path(row["path"]).lstat()) != row["identity"]:
                    raise ValueError("source changed after inventory")
            rows.append(row)
            if len(rows) > MAX_SOURCES:
                raise ValueError("source bound exceeded; no universe was truncated")
    rows.sort(key=lambda row: row["id"])
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("duplicate archive market identity")
    return rows


def audit_source(row: Mapping[str, Any], *, use_numpy: bool = True) -> dict[str, Any]:
    """Hash and validate every original record with bounded memory, no gap expansion."""
    path = Path(row["path"])
    expected = row["identity"]
    if identity(path.lstat()) != expected or not path.is_file() or path.is_symlink():
        raise ValueError("source identity changed before audit")
    np: Any = None
    if use_numpy:
        with suppress(ModuleNotFoundError):
            np = importlib.import_module("numpy")
        if np is None and (scanner := _find_cpp_scanner()) is not None:
            return _audit_cpp(row, scanner)
    result: dict[str, Any] = {
        "records": 0,
        "bytes": 0,
        "first_ms": None,
        "last_ms": None,
        "gap_intervals": 0,
        "missing_minutes": 0,
        "largest_gap_minutes": 0,
        "errors": {},
        "sha256": None,
        "complete": False,
    }
    errors: Counter[str] = Counter()
    digest = hashlib.sha256()
    previous: int | None = None
    now_ms = int(time.time() * 1000)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if identity(os.fstat(stream.fileno())) != expected:
            raise ValueError("source changed on audit open")
        while chunk := stream.read(CHUNK_RECORDS * RECORD.size):
            digest.update(chunk)
            result["bytes"] += len(chunk)
            if len(chunk) % RECORD.size:
                errors["partial_record"] += 1
            complete = chunk[: len(chunk) // RECORD.size * RECORD.size]
            if not complete:
                continue
            if np is not None:
                a = np.frombuffer(complete, dtype=[("ts", "<i8"), ("p", "<f4", (4,))])
                timestamps, prices = a["ts"], a["p"]
                invalid_ts = (timestamps <= 0) | (timestamps > now_ms) | (timestamps % 60000 != 0)
                errors["invalid_timestamp"] += int(np.count_nonzero(invalid_ts))
                errors["invalid_price"] += int(
                    np.count_nonzero(~np.isfinite(prices).all(axis=1) | (prices <= 0).any(axis=1))
                )
                errors["invalid_ohlc"] += int(
                    np.count_nonzero(
                        ~(
                            (prices[:, 2] <= np.minimum(prices[:, 0], prices[:, 3]))
                            & (prices[:, 1] >= np.maximum(prices[:, 0], prices[:, 3]))
                            & (prices[:, 2] <= prices[:, 1])
                        )
                    )
                )
                # Only legal timestamps enter subtraction, avoiding int64 overflow.
                good_ts = not bool(np.any(invalid_ts))
                if good_ts:
                    differences = np.diff(timestamps)
                    errors["nonincreasing_timestamp"] += int(np.count_nonzero(differences <= 0))
                    gaps = differences[differences > 60000] // 60000 - 1
                    result["gap_intervals"] += len(gaps)
                    result["missing_minutes"] += int(gaps.sum())
                    if len(gaps):
                        result["largest_gap_minutes"] = max(
                            result["largest_gap_minutes"], int(gaps.max())
                        )
                else:
                    for before, after in zip(timestamps[:-1], timestamps[1:], strict=True):
                        _audit_difference(result, errors, int(before), int(after))
                first, last = int(timestamps[0]), int(timestamps[-1])
                count = len(timestamps)
            else:
                first = last = 0
                count = 0
                inner_previous: int | None = None
                for timestamp, opened, high, low, closed in RECORD.iter_unpack(complete):
                    if count == 0:
                        first = timestamp
                    last = timestamp
                    count += 1
                    if timestamp <= 0 or timestamp > now_ms or timestamp % 60000:
                        errors["invalid_timestamp"] += 1
                    if not all(math.isfinite(p) and p > 0 for p in (opened, high, low, closed)):
                        errors["invalid_price"] += 1
                    if not low <= min(opened, closed) <= max(opened, closed) <= high:
                        errors["invalid_ohlc"] += 1
                    if inner_previous is not None:
                        _audit_difference(result, errors, inner_previous, timestamp)
                    inner_previous = timestamp
            if previous is not None:
                _audit_difference(result, errors, previous, first)
            if result["first_ms"] is None:
                result["first_ms"] = first
            result["last_ms"] = last
            previous = last
            result["records"] += count
        if identity(os.fstat(stream.fileno())) != expected:
            raise ValueError("source changed during audit")
    if identity(path.lstat()) != expected:
        raise ValueError("source changed after audit")
    if not result["records"]:
        errors["empty_source"] += 1
    result.update(
        errors={key: count for key, count in errors.items() if count},
        sha256=digest.hexdigest(),
        complete=True,
    )
    return result


def _find_cpp_scanner() -> Path | None:
    for path in CPP_SCANNERS:
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode) and os.access(path, os.X_OK):
            return path
    return None


@lru_cache(maxsize=4)
def _scanner_digest(path: str, expected: tuple[int, ...]) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if tuple(identity(os.fstat(stream.fileno()))) != expected:
            raise ValueError("scanner changed before fingerprint")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if tuple(identity(os.fstat(stream.fileno()))) != expected:
            raise ValueError("scanner changed during fingerprint")
    return digest


def _audit_cpp(row: Mapping[str, Any], scanner: Path) -> dict[str, Any]:
    """Use the existing immutable full-file scanner with bounded owned output."""
    expected_binary = tuple(identity(scanner.lstat()))
    digest = _scanner_digest(str(scanner), expected_binary)
    deadline = time.monotonic() + SCANNER_SECONDS
    captured = bytearray()
    with subprocess.Popen(
        [str(scanner), "scan", str(row["path"])],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        close_fds=True,
    ) as process:
        assert process.stdout is not None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise ValueError("C++ source scanner timeout")
                    chunk = os.read(
                        process.stdout.fileno(), SCANNER_OUTPUT_BYTES + 1 - len(captured)
                    )
                    if not chunk:
                        break
                    captured.extend(chunk)
                    if len(captured) > SCANNER_OUTPUT_BYTES:
                        raise ValueError("C++ source scanner output bound exceeded")
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise ValueError("C++ source scanner exit timeout") from exc
        except BaseException:
            process.kill()
            process.wait()
            raise
        if process.returncode != 0:
            raise ValueError(
                "C++ source scanner refused source: " + captured[:500].decode(errors="replace")
            )
    if tuple(identity(scanner.lstat())) != expected_binary:
        raise ValueError("scanner changed during source audit")
    if identity(Path(row["path"]).lstat()) != row["identity"]:
        raise ValueError("source changed during C++ audit")
    try:
        stats = json.loads(captured)
        valid = (
            stats["schema"] == "grid-source-scan-v1"
            and stats["complete"] is True
            and stats["bytes"] == row["bytes"]
            and stats["records"] == row["bytes"] // RECORD.size
            and stats["accepted"] + stats["rejected"] == stats["records"]
            and isinstance(stats["reasons"], dict)
            and sum(stats["reasons"].values()) == stats["rejected"]
            and isinstance(stats["sha256"], str)
            and len(stats["sha256"]) == 64
            and all(c in "0123456789abcdef" for c in stats["sha256"])
            and all(
                type(stats[name]) is int and stats[name] >= 0
                for name in (
                    "bytes",
                    "records",
                    "accepted",
                    "rejected",
                    "gap_count",
                    "missing_minutes",
                )
            )
            and all(type(count) is int and count > 0 for count in stats["reasons"].values())
            and (
                stats["first_ms"] is None and stats["last_ms"] is None
                if stats["accepted"] == 0
                else type(stats["first_ms"]) is int
                and type(stats["last_ms"]) is int
                and 0 < stats["first_ms"] <= stats["last_ms"]
                and stats["first_ms"] % 60000 == stats["last_ms"] % 60000 == 0
            )
            and (
                stats["rejected"] > 0
                or (stats["first_ms"], stats["last_ms"]) == (row["first_ms"], row["last_ms"])
            )
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("malformed C++ source audit") from exc
    if not valid:
        raise ValueError("incomplete or inconsistent C++ source audit")
    errors = dict(stats["reasons"])
    if stats["last_ms"] is not None and stats["last_ms"] > int(time.time() * 1000):
        errors["future_timestamp_present"] = 1
    return {
        "records": stats["records"],
        "bytes": stats["bytes"],
        "first_ms": stats["first_ms"],
        "last_ms": stats["last_ms"],
        "gap_intervals": stats["gap_count"],
        "missing_minutes": stats["missing_minutes"],
        "largest_gap_minutes": None,
        "errors": errors,
        "sha256": stats["sha256"],
        "complete": True,
        "scanner": {"path": str(scanner), "sha256": digest},
    }


def _audit_difference(
    result: dict[str, Any], errors: Counter[str], before: int, after: int
) -> None:
    difference = after - before
    if difference <= 0:
        errors["nonincreasing_timestamp"] += 1
    elif difference > 60000 and before > 0 and after > 0 and difference % 60000 == 0:
        missing = difference // 60000 - 1
        result["gap_intervals"] += 1
        result["missing_minutes"] += missing
        result["largest_gap_minutes"] = max(result["largest_gap_minutes"], missing)


def _decimal(raw: object, name: str, *, positive: bool = False) -> Decimal:
    if raw is None or isinstance(raw, (float, bool)):
        raise ValueError(f"{name}: exact value unavailable")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError(f"{name}: malformed value") from exc
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError(f"{name}: invalid value")
    if value.adjusted() < -30 or value.adjusted() > 30:
        raise ValueError(f"{name}: unsupported exponent")
    return value


def _tick(raw: object, mode: object, name: str) -> str:
    if mode in (4, "tick_size", "tick"):
        return str(_decimal(raw, name, positive=True))
    if mode in (2, "decimal_places"):
        value = _decimal(raw, name)
        if value != value.to_integral_value() or not 0 <= value <= 18:
            raise ValueError(f"{name}: unsupported decimal places")
        return str(Decimal(1).scaleb(-int(value)))
    raise ValueError(f"{name}: unsupported precision mode")


def normalize_rules(
    venue: str, symbol: str, entry: Mapping[str, Any], mode: object = None
) -> dict[str, Any]:
    """Normalize only agreed research minima/precision; unknown never means zero."""
    base, quote = symbol.split("/")
    if (entry.get("symbol"), entry.get("base"), entry.get("quote")) != (symbol, base, quote):
        raise ValueError("archive and market identity do not match")
    if entry.get("spot") is not True or entry.get("type") != "spot":
        raise ValueError("spot identity unavailable")
    if any(entry.get(key) is True for key in ("contract", "swap", "future", "leveraged")):
        raise ValueError("market is outside unleveraged spot scope")
    info = entry.get("info") or {}
    if not isinstance(info, dict):
        info = {}
    if venue == "htx" and ("etp" in str(info.get("tags", "")).lower() or info.get("underlying")):
        raise ValueError("market metadata identifies a leveraged product")
    precision = entry.get("precision") or {}
    mode = entry.get("precision_mode", mode)
    price = entry.get("price_precision", precision.get("price"))
    amount = entry.get("amount_precision", precision.get("amount"))
    amount_mode = entry.get("amount_precision_mode", mode)
    price_mode = entry.get("price_precision_mode", mode)
    rules: dict[str, Any]
    if venue == "bitfinex":
        if _decimal(price, "price digits") != 5 or _decimal(amount, "amount decimals") != 8:
            raise ValueError("unexpected Bitfinex compound precision")
        rules = {
            "price_mode": "significant",
            "price_tick": "0.00000001",
            "price_digits": 5,
            "max_price_decimals": 8,
            "amount_tick": "0.00000001",
        }
    else:
        rules = {
            "price_mode": "tick",
            "price_tick": _tick(price, price_mode, "price tick"),
            "amount_tick": _tick(amount, amount_mode, "amount tick"),
        }
    limits = entry.get("order_limits") or entry.get("limits") or {}
    min_amount = (limits.get("buy_amount") or limits.get("amount") or {}).get("min")
    min_cost = (limits.get("cost") or {}).get("min")
    evidence: dict[str, str] = {}
    if venue == "okx" and min_cost is None:
        if (
            info.get("instType") != "SPOT"
            or _decimal(info.get("minSz"), "OKX minSz", positive=True)
            != _decimal(min_amount, "min_amount", positive=True)
            or _decimal(info.get("lotSz"), "OKX lotSz", positive=True)
            != _decimal(rules["amount_tick"], "amount_tick", positive=True)
            or _decimal(info.get("tickSz"), "OKX tickSz", positive=True)
            != _decimal(rules["price_tick"], "price_tick", positive=True)
        ):
            raise ValueError("OKX spot instrument rules disagree with unified rules")
        min_cost = "0"
        evidence["min_cost"] = NO_SEPARATE_COST_MINIMUM["okx"]
    if venue == "bingx":
        native_amount = _decimal(info.get("minQty"), "BingX minQty", positive=True)
        if min_amount is not None and native_amount != _decimal(min_amount, "min_amount"):
            raise ValueError("BingX minQty disagrees with unified minimum")
        if _decimal(info.get("minNotional"), "BingX minNotional") != _decimal(min_cost, "min_cost"):
            raise ValueError("BingX minNotional disagrees with unified minimum")
        min_amount = info["minQty"]
    if venue == "bitfinex":
        if entry.get("exchange_listed") is not True:
            raise ValueError("Bitfinex exchange spot listing unavailable")
        if (
            not isinstance(entry.get("info"), list)
            or len(entry["info"]) < 4
            or _decimal(entry["info"][3], "Bitfinex native minimum", positive=True)
            != _decimal(min_amount, "min_amount", positive=True)
        ):
            raise ValueError("Bitfinex pair minimum disagrees with unified rules")
    if venue == "bitfinex" and min_cost is None:
        min_cost = "0"
        evidence["min_cost"] = NO_SEPARATE_COST_MINIMUM["bitfinex"]
    if venue == "htx":
        min_amount = info.get("limit-order-min-order-amt", min_amount)
    if venue == "gate":
        min_amount = info.get("min_base_amount", min_amount)
        min_cost = info.get("min_quote_amount", min_cost)
    if venue == "xt":
        filters = {
            item.get("filter"): item for item in info.get("filters", []) if isinstance(item, dict)
        }
        notional = filters.get("QUOTE_QTY", {})
        if "min" in notional and notional["min"] is None:
            min_cost = "0"
            evidence["min_cost"] = DOCUMENTATION["xt"] + "#quote_qty-filter"
        for kind, tick in (("PRICE", "price_tick"), ("QUANTITY", "amount_tick")):
            rule = filters.get(kind, {})
            if rule.get("min") is not None:
                with localcontext() as context:
                    context.prec = 80
                    if _decimal(rule["min"], kind) % Decimal(rules[tick]):
                        raise ValueError(
                            f"{kind}: nonzero precision offset requires explicit support"
                        )
    for key, raw in (("min_amount", min_amount), ("min_cost", min_cost)):
        rules[key] = str(_decimal(raw, key))
    if evidence:
        rules["known_absent_evidence"] = evidence
    return rules


def load_snapshots(directories: Sequence[Path]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {venue: [] for venue in VENUES}
    seen: set[str] = set()
    for directory in directories:
        for venue in VENUES:
            for path in sorted(directory.glob(f"{venue}*.json")):
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or path.stat().st_size > MAX_SNAPSHOT_BYTES
                ):
                    continue
                raw = path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                if digest in seen:
                    continue
                value = json.loads(raw)
                if value.get("exchange") != venue or not isinstance(value.get("markets"), dict):
                    continue
                seen.add(digest)
                value["snapshot_path"], value["snapshot_sha256"] = str(path.absolute()), digest
                result[venue].append(value)
    for snapshots in result.values():
        snapshots.sort(key=lambda item: str(item.get("fetched_utc", "")), reverse=True)
    return result


def prepare_sources(
    dataset_root: Path,
    output_dir: Path,
    snapshot_dirs: Sequence[Path] = (),
    *,
    audit: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    scanner_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    rows = inventory_sources(dataset_root)
    _publish(output_dir / "inventory.json", rows)
    snapshots = load_snapshots(snapshot_dirs or DEFAULT_SNAPSHOT_DIRS)
    blockers: list[dict[str, str]] = []
    completed: list[dict[str, Any]] = []
    with (output_dir / "sources.jsonl").open("x", encoding="utf-8") as report:
        for index, row in enumerate(rows):
            row["rules"] = None
            row["audit_accepted"] = False
            reasons = []
            key = "USDT_" + row["symbol"].removesuffix("/USDT")
            for snapshot in snapshots[row["venue"]]:
                entry = snapshot["markets"].get(key)
                if not isinstance(entry, dict):
                    continue
                try:
                    row["rules"] = normalize_rules(
                        row["venue"], row["symbol"], entry, snapshot.get("precision_mode")
                    )
                    row["rules_snapshot"] = {
                        name: snapshot.get(name)
                        for name in (
                            "snapshot_path",
                            "snapshot_sha256",
                            "fetched_utc",
                            "ccxt_version",
                        )
                    }
                    break
                except ValueError as exc:
                    reasons.append(str(exc))
            if row["rules"] is None:
                blockers.append(
                    {
                        "id": row["id"],
                        "kind": "rules",
                        "reason": "; ".join(dict.fromkeys(reasons))
                        or "no matching saved market rules",
                    }
                )
            if audit and "not_regular_nonsymlink" not in row["inventory_errors"]:
                try:
                    row["audit"] = audit_source(row)
                    row["audit_accepted"] = not bool(
                        row["audit"]["errors"] or row["inventory_errors"]
                    )
                    row.update(
                        {
                            name: row["audit"][name]
                            for name in ("sha256", "records", "first_ms", "last_ms")
                        }
                    )
                    if row["audit"]["errors"]:
                        blockers.append(
                            {
                                "id": row["id"],
                                "kind": "data",
                                "reason": json.dumps(row["audit"]["errors"], sort_keys=True),
                            }
                        )
                except (ValueError, OSError) as exc:
                    blockers.append({"id": row["id"], "kind": "data", "reason": str(exc)})
            elif row["inventory_errors"]:
                blockers.append(
                    {"id": row["id"], "kind": "data", "reason": ", ".join(row["inventory_errors"])}
                )
            else:
                blockers.append(
                    {"id": row["id"], "kind": "data", "reason": "full source audit not requested"}
                )
            completed.append(row)
            report.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            report.flush()
            if progress:
                progress(index + 1, len(rows), row["id"])
    final_roster = inventory_sources(dataset_root)
    if [(r["id"], r["identity"]) for r in final_roster] != [(r["id"], r["identity"]) for r in rows]:
        blockers.append(
            {"id": "universe", "kind": "data", "reason": "source roster changed after preparation"}
        )
    if not rows:
        blockers.append({"id": "universe", "kind": "data", "reason": "empty source roster"})
    manifest = {
        "schema": "grid-research-sweep-sources-v1",
        "created_utc": datetime.now(UTC).isoformat(),
        "status": "BLOCKED" if blockers else "READY",
        "dataset_root": str(dataset_root.absolute()),
        "sources": completed,
        "blockers": blockers,
        "totals": {
            "sources": len(rows),
            "bytes": sum(row["bytes"] for row in rows),
            "records": sum(row["records"] for row in rows),
            "rules_ready": sum(row["rules"] is not None for row in rows),
            "by_venue": dict(sorted(Counter(row["venue"] for row in rows).items())),
        },
        "historical_rules_verified": False,
        "universe_reduced": False,
        "scanner_sha256": scanner_sha256,
    }
    _publish(output_dir / "manifest.json", manifest)
    _publish(
        output_dir / "blockers.json",
        {"status": manifest["status"], "totals": manifest["totals"], "blockers": blockers},
    )
    return manifest


def select_verified_sources(preparation_path: Path, output_dir: Path) -> dict[str, Any]:
    """Freeze a declared seven-venue subset without rewriting strict preparation."""
    raw = preparation_path.read_bytes()
    if len(raw) > 128 * 1024 * 1024:
        raise ValueError("preparation manifest exceeds bounded selector")
    prepared = json.loads(raw)
    if prepared.get("schema") != "grid-research-sweep-sources-v1":
        raise ValueError("selection requires an original audited source manifest")
    rows, findings = prepared.get("sources"), prepared.get("blockers")
    if not isinstance(rows, list) or not isinstance(findings, list):
        raise ValueError("malformed audited source manifest")
    by_id: dict[str, list[dict[str, str]]] = {}
    for finding in findings:
        if not isinstance(finding, dict) or not isinstance(finding.get("id"), str):
            raise ValueError("malformed source finding")
        by_id.setdefault(finding["id"], []).append(finding)
    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    fatal: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError("malformed source row")
        market_id = row["id"]
        if market_id in seen or row.get("venue") not in VENUES:
            raise ValueError("duplicate or unsupported market in preparation")
        seen.add(market_id)
        own_findings = by_id.get(market_id, [])
        if not row.get("audit_accepted") or any(
            finding.get("kind") == "data" for finding in own_findings
        ):
            fatal.append({"id": market_id, "kind": "data", "reason": "source audit incomplete"})
            continue
        if row.get("rules"):
            if not row.get("rules_snapshot") or own_findings:
                fatal.append(
                    {
                        "id": market_id,
                        "kind": "preparation",
                        "reason": "rules and findings conflict",
                    }
                )
                continue
            selected.append(row)
            continue
        if not own_findings or any(finding.get("kind") != "rules" for finding in own_findings):
            fatal.append(
                {
                    "id": market_id,
                    "kind": "preparation",
                    "reason": "missing rule exclusion evidence",
                }
            )
            continue
        excluded.append(
            {
                "id": market_id,
                "venue": row["venue"],
                "sha256": row.get("sha256"),
                "reason": "; ".join(dict.fromkeys(item["reason"] for item in own_findings)),
            }
        )
    if len(seen) != prepared.get("totals", {}).get("sources"):
        fatal.append({"id": "universe", "kind": "data", "reason": "inventory count changed"})
    for finding in findings:
        if finding["id"] not in seen and finding.get("kind") != "rules":
            fatal.append(finding)
    missing = sorted(set(VENUES) - {row["venue"] for row in selected})
    if missing:
        fatal.append(
            {
                "id": "universe",
                "kind": "rules",
                "reason": "no verified spot USDT pair on: " + ", ".join(missing),
            }
        )
    manifest = {
        "schema": "grid-research-sweep-selection-v1",
        "created_utc": datetime.now(UTC).isoformat(),
        "status": "BLOCKED" if fatal else "READY",
        "selection_policy": "verified_model_rules_seven_venues",
        "source_manifest_path": str(preparation_path.resolve()),
        "source_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "historical_rules_verified": False,
        "universe_reduced": True,
        "sources": selected,
        "excluded": excluded,
        "blockers": fatal,
        "totals": {
            "inventoried": len(rows),
            "selected": len(selected),
            "excluded": len(excluded),
            "selected_records": sum(row["records"] for row in selected),
            "selected_bytes": sum(row["bytes"] for row in selected),
            "by_venue": dict(sorted(Counter(row["venue"] for row in selected).items())),
            "excluded_by_venue": dict(sorted(Counter(row["venue"] for row in excluded).items())),
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _publish(output_dir / "manifest.json", manifest)
    return manifest


def verify_selection(manifest: Mapping[str, Any]) -> None:
    """Refuse a changed parent or a selection that silently drops a market."""
    if manifest.get("schema") != "grid-research-sweep-selection-v1":
        raise ValueError("unsupported selected source manifest")
    parent_name = manifest.get("source_manifest_path")
    if not isinstance(parent_name, str) or not parent_name:
        raise ValueError("selected source parent path is missing")
    original_path = Path(parent_name)
    raw = original_path.read_bytes()
    if len(raw) > 128 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != manifest.get(
        "source_manifest_sha256"
    ):
        raise ValueError("selected source parent changed")
    original = json.loads(raw)
    if original.get("schema") != "grid-research-sweep-sources-v1":
        raise ValueError("selected source parent is not an audited preparation")
    rows, findings = original.get("sources"), original.get("blockers")
    selected, excluded = manifest.get("sources"), manifest.get("excluded")
    if not all(isinstance(item, list) for item in (rows, findings, selected, excluded)):
        raise ValueError("selected source roster differs from audited preparation")
    by_id: dict[str, dict[str, Any]] = {}
    own_findings: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError("selected source roster differs from audited preparation")
        if row["id"] in by_id:
            raise ValueError("selected source roster differs from audited preparation")
        by_id[row["id"]] = row
    for finding in findings:
        if (
            not isinstance(finding, dict)
            or finding.get("id") not in by_id
            or finding.get("kind") != "rules"
            or not isinstance(finding.get("reason"), str)
        ):
            raise ValueError("selected source roster differs from audited preparation")
        own_findings.setdefault(finding["id"], []).append(finding)
    expected_selected = []
    expected_excluded = []
    for row in rows:
        if not row.get("audit_accepted"):
            raise ValueError("selected source roster differs from audited preparation")
        own = own_findings.get(row["id"], [])
        if row.get("rules") and row.get("rules_snapshot") and not own:
            expected_selected.append(row)
        elif not row.get("rules") and own:
            expected_excluded.append(
                {
                    "id": row["id"],
                    "venue": row["venue"],
                    "sha256": row.get("sha256"),
                    "reason": "; ".join(dict.fromkeys(item["reason"] for item in own)),
                }
            )
        else:
            raise ValueError("selected source roster differs from audited preparation")
    counts = {
        "inventoried": len(rows),
        "selected": len(expected_selected),
        "excluded": len(expected_excluded),
        "selected_records": sum(row["records"] for row in expected_selected),
        "selected_bytes": sum(row["bytes"] for row in expected_selected),
        "by_venue": dict(sorted(Counter(row["venue"] for row in expected_selected).items())),
        "excluded_by_venue": dict(
            sorted(Counter(row["venue"] for row in expected_excluded).items())
        ),
    }
    if (
        manifest.get("status") != "READY"
        or manifest.get("blockers") != []
        or selected != expected_selected
        or excluded != expected_excluded
        or manifest.get("totals") != counts
        or set(VENUES) != {row["venue"] for row in expected_selected}
    ):
        raise ValueError("selected source roster differs from audited preparation")


def _gap_parent(path: Path, expected_sha256: object = None) -> tuple[dict[str, Any], str]:
    with path.open("rb") as stream:
        raw = stream.read(128 * 1024 * 1024 + 1)
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) > 128 * 1024 * 1024 or (expected_sha256 is not None and digest != expected_sha256):
        raise ValueError("gap-free selection parent changed or exceeds size bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("gap-free selection requires a verified selection-v1 parent")
    verify_selection(value)
    if not 0 < len(value["sources"]) <= MAX_SOURCES:
        raise ValueError("gap-free selection parent source bound exceeded")
    return value, digest


def _gap_audit(row: Mapping[str, Any], audit: object) -> dict[str, Any]:
    """Validate complete positive chronology, including consistency of gap counters."""
    if not isinstance(audit, dict) or audit.get("complete") is not True:
        raise ValueError("full source audit incomplete or unknown")
    if row.get("audit_accepted") is not True or row.get("inventory_errors") != []:
        raise ValueError("parent source audit was not accepted")
    if audit.get("errors") != {}:
        raise ValueError("source structural audit failed")
    for name in ("bytes", "records", "first_ms", "last_ms", "gap_intervals", "missing_minutes"):
        value = audit.get(name)
        if type(value) is not int or value < (0 if name.endswith(("intervals", "minutes")) else 1):
            raise ValueError("source audit has unknown or malformed " + name)
    digest = audit.get("sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValueError("source audit SHA-256 unavailable")
    for name in ("bytes", "records", "first_ms", "last_ms", "sha256"):
        if type(row.get(name)) is not type(audit[name]) or row[name] != audit[name]:
            raise ValueError("source differs from parent: " + name)
    first, last = audit["first_ms"], audit["last_ms"]
    gaps, missing = audit["gap_intervals"], audit["missing_minutes"]
    largest = audit.get("largest_gap_minutes")
    if (
        first % 60_000
        or last % 60_000
        or last < first
        or audit["bytes"] != audit["records"] * RECORD.size
        or audit["records"] + missing != (last - first) // 60_000 + 1
        or (gaps == 0) != (missing == 0)
        or not 0 <= gaps < audit["records"]
        or missing < gaps
        or (
            largest is not None
            and (
                type(largest) is not int
                or not (0 <= largest <= missing)
                or (largest == 0) != (gaps == 0)
            )
        )
    ):
        raise ValueError("source audit chronology/counts are inconsistent")
    return audit


def _gap_receipt(row: Mapping[str, Any]) -> dict[str, Any]:
    receipt: dict[str, Any] = {"id": row["id"], "identity": row.get("identity")}
    try:
        current = audit_source(row)
        receipt["audit"] = current
        previous = _gap_audit(row, row.get("audit"))
        _gap_audit(row, current)
        for key in ("gap_intervals", "missing_minutes", "largest_gap_minutes"):
            if previous.get(key) is not None and previous[key] != current.get(key):
                raise ValueError("source audit differs from parent: " + key)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        receipt["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
    return receipt


def _gap_coverage(row: Mapping[str, Any], archive_last_ms: int) -> dict[str, Any]:
    first, last = row["first_ms"], row["last_ms"]
    end = last + 60_000
    start_date = datetime.fromtimestamp(first / 1000, UTC)
    month = start_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    full_months = 0
    while True:
        following = (
            month.replace(year=month.year + 1, month=1)
            if month.month == 12
            else (month.replace(month=month.month + 1))
        )
        if int(following.timestamp() * 1000) > end:
            break
        full_months += int(int(month.timestamp() * 1000) >= first)
        month = following
    return {
        "id": row["id"],
        "venue": row["venue"],
        "first_ms": first,
        "last_ms": last,
        "end_exclusive_ms": end,
        "duration_minutes": (end - first) // 60_000,
        "complete_utc_days": max(0, end // 86_400_000 - (first + 86_399_999) // 86_400_000),
        "full_utc_months": full_months,
        "ends_before_archive_last_day": last // 86_400_000 < archive_last_ms // 86_400_000,
        "endpoint_lag_minutes": (archive_last_ms - last) // 60_000,
    }


def _gap_derivation(parent: Mapping[str, Any], receipts: object) -> dict[str, Any]:
    rows = parent["sources"]
    if not isinstance(receipts, list) or len(receipts) != len(rows):
        raise ValueError("gap-free selection audit coverage is incomplete")
    selected, excluded, blockers = [], [], []
    for row, receipt in zip(rows, receipts, strict=True):
        if not isinstance(receipt, dict) or receipt.get("id") != row["id"]:
            raise ValueError("gap-free selection audit roster differs from parent")
        try:
            if receipt.get("error"):
                raise ValueError(str(receipt["error"]))
            expected = row.get("identity")
            if (
                not isinstance(expected, list)
                or len(expected) != 5
                or any(type(item) is not int or item < 0 for item in expected)
                or expected[2] != row["bytes"]
                or receipt.get("identity") != expected
            ):
                raise ValueError("source identity audit is malformed")
            original = _gap_audit(row, row.get("audit"))
            audit = _gap_audit(row, receipt.get("audit"))
            for key in ("gap_intervals", "missing_minutes", "largest_gap_minutes"):
                if original.get(key) is not None and original[key] != audit.get(key):
                    raise ValueError("source audit differs from parent: " + key)
        except (KeyError, TypeError, ValueError) as exc:
            blockers.append({"id": row["id"], "kind": "data", "reason": str(exc)})
            continue
        if audit["gap_intervals"]:
            excluded.append(
                {
                    "id": row["id"],
                    "venue": row["venue"],
                    "symbol": row["symbol"],
                    "sha256": row["sha256"],
                    "reason": "missing_minutes_in_own_span",
                    "gap_intervals": audit["gap_intervals"],
                    "missing_minutes": audit["missing_minutes"],
                    "largest_gap_minutes": audit.get("largest_gap_minutes"),
                }
            )
        else:
            selected.append(row)
    actual_venues = {row["venue"] for row in selected}
    if actual_venues != set(GAP_FREE_VENUES):
        blockers.append(
            {
                "id": "universe",
                "kind": "coverage",
                "reason": "gap-free selection requires exactly: " + ", ".join(GAP_FREE_VENUES),
            }
        )
    archive_last = max(row["last_ms"] for row in rows)
    coverage = [_gap_coverage(row, archive_last) for row in selected]
    return {
        "status": "BLOCKED" if blockers else "READY",
        "sources": selected,
        "excluded": excluded,
        "blockers": blockers,
        "coverage": {
            "archive_last_ms": archive_last,
            "sources": coverage,
            "ending_before_archive_last_day": sum(
                row["ends_before_archive_last_day"] for row in coverage
            ),
            "ending_before_archive_last_record": sum(
                row["endpoint_lag_minutes"] > 0 for row in coverage
            ),
        },
        "totals": {
            "inventoried": len(rows),
            "selected": len(selected),
            "excluded": len(excluded),
            "selected_records": sum(row["records"] for row in selected),
            "selected_bytes": sum(row["bytes"] for row in selected),
            "by_venue": dict(sorted(Counter(row["venue"] for row in selected).items())),
            "excluded_by_venue": dict(sorted(Counter(row["venue"] for row in excluded).items())),
        },
    }


def select_gap_free_sources(
    parent_path: Path,
    output_dir: Path,
    *,
    audit_threads: int = 4,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Re-audit every parent's source; exclude only proven internal missing minutes."""
    if type(audit_threads) is not int or not 1 <= audit_threads <= 32:
        raise ValueError("audit threads must be between 1 and 32")
    auditor_path = Path(__file__)
    auditor_sha256 = hashlib.sha256(auditor_path.read_bytes()).hexdigest()
    parent, parent_sha = _gap_parent(parent_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "manifest.json"
    pending = output_dir / "manifest.json.pending"
    if target.exists() or pending.exists():
        raise FileExistsError("gap-free selection output already exists")
    receipts = []
    started = datetime.now(UTC).isoformat()
    # Futures contain only compact receipts; each worker reads bounded chunks.
    with ThreadPoolExecutor(max_workers=audit_threads, thread_name_prefix="source-audit") as pool:
        for index, receipt in enumerate(pool.map(_gap_receipt, parent["sources"]), start=1):
            receipts.append(receipt)
            if progress:
                progress(index, len(parent["sources"]), receipt["id"])
    # A file audited early must also retain its identity at the publication boundary.
    for row, receipt in zip(parent["sources"], receipts, strict=True):
        try:
            if identity(Path(row["path"]).lstat()) != row["identity"]:
                raise ValueError("source changed after full audit")
        except (OSError, ValueError) as exc:
            receipt["error"] = str(exc)
    _gap_parent(parent_path, parent_sha)
    if hashlib.sha256(auditor_path.read_bytes()).hexdigest() != auditor_sha256:
        raise ValueError("source auditor changed during selection")
    manifest = {
        "schema": GAP_FREE_SCHEMA,
        "created_utc": datetime.now(UTC).isoformat(),
        "selection_policy": "verified_model_rules_gap_free_five_venues",
        "history_policy": GAP_FREE_POLICY,
        "expected_venues": list(GAP_FREE_VENUES),
        "source_manifest_path": str(parent_path.resolve()),
        "source_manifest_sha256": parent_sha,
        "historical_rules_verified": False,
        "research_readiness": "PENDING_PRICE_AND_HISTORICAL_RULES_REVIEW",
        "universe_reduced": True,
        "audit": {
            "started_utc": started,
            "finished_utc": datetime.now(UTC).isoformat(),
            "threads": audit_threads,
            "auditor_sha256": auditor_sha256,
        },
        "source_audits": receipts,
        **_gap_derivation(parent, receipts),
    }
    _publish(pending, manifest)
    os.link(pending, target)  # Exclusive atomic publication; never overwrite another manifest.
    pending.unlink()
    directory_fd = os.open(output_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return manifest


def verify_gap_free_selection(manifest: Mapping[str, Any]) -> None:
    """Check provenance and exact derivation; never re-read full archives on load."""
    if (
        manifest.get("schema") != GAP_FREE_SCHEMA
        or manifest.get("history_policy") != GAP_FREE_POLICY
        or manifest.get("expected_venues") != list(GAP_FREE_VENUES)
        or manifest.get("selection_policy") != "verified_model_rules_gap_free_five_venues"
        or manifest.get("historical_rules_verified") is not False
        or manifest.get("research_readiness") != "PENDING_PRICE_AND_HISTORICAL_RULES_REVIEW"
        or manifest.get("universe_reduced") is not True
    ):
        raise ValueError("unsupported gap-free source selection contract")
    parent_name = manifest.get("source_manifest_path")
    if not isinstance(parent_name, str) or not parent_name:
        raise ValueError("gap-free source parent path is missing")
    digest = manifest.get("source_manifest_sha256")
    audit = manifest.get("audit")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
        or not isinstance(audit, dict)
        or type(audit.get("threads")) is not int
        or not 1 <= audit["threads"] <= 32
        or not isinstance(audit.get("auditor_sha256"), str)
        or len(audit["auditor_sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in audit["auditor_sha256"])
        or any(not isinstance(audit.get(key), str) for key in ("started_utc", "finished_utc"))
    ):
        raise ValueError("gap-free selection audit provenance is incomplete")
    try:
        started = datetime.fromisoformat(audit["started_utc"])
        finished = datetime.fromisoformat(audit["finished_utc"])
        if (
            started.utcoffset() != UTC.utcoffset(None)
            or finished.utcoffset() != UTC.utcoffset(None)
            or finished < started
        ):
            raise ValueError("inconsistent UTC audit interval")
    except (TypeError, ValueError) as exc:
        raise ValueError("gap-free selection audit timestamps are invalid") from exc
    parent, _ = _gap_parent(Path(parent_name), digest)
    expected = _gap_derivation(parent, manifest.get("source_audits"))
    if expected["status"] != "READY" or any(
        json.dumps(manifest.get(key), sort_keys=True, allow_nan=False)
        != json.dumps(value, sort_keys=True, allow_nan=False)
        for key, value in expected.items()
    ):
        raise ValueError("gap-free source selection differs from audited parent")
    for row in parent["sources"]:
        if identity(Path(row["path"]).lstat()) != row["identity"]:
            raise ValueError("gap-free source changed after audit")


def _review_file(
    path: Path, expected_sha256: object = None, *, document: bool = False
) -> tuple[dict[str, Any] | None, str]:
    """Read immutable local evidence without following symbolic links or unbounded JSON."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("review evidence requires an absolute normalized path")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("review evidence cannot use symbolic links")
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(c not in "0123456789abcdef" for c in expected_sha256)
    ):
        raise ValueError("invalid review evidence SHA-256")
    before = path.lstat()
    limit = 128 * 1024**2 if document else 64 * 1024**3
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
        raise ValueError("review evidence is not a bounded regular file")
    digest = hashlib.sha256()
    raw = bytearray()
    size = 0
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if identity(os.fstat(stream.fileno())) != identity(before):
            raise ValueError("review evidence changed on open")
        while chunk := stream.read(1024**2):
            size += len(chunk)
            if size > limit:
                raise ValueError("review evidence exceeds its size bound")
            digest.update(chunk)
            if document:
                raw.extend(chunk)
        if identity(os.fstat(stream.fileno())) != identity(before):
            raise ValueError("review evidence changed while reading")
    if size != before.st_size or identity(path.lstat()) != identity(before):
        raise ValueError("review evidence changed while reading")
    checksum = digest.hexdigest()
    if expected_sha256 is not None and checksum != expected_sha256:
        raise ValueError("review evidence SHA-256 mismatch")
    value = json.loads(raw) if document else None
    if document:
        if not isinstance(value, dict):
            raise ValueError("review document must be an object")
        json.dumps(value, allow_nan=False)
    return value, checksum


def _review_inputs(
    parent_path: Path,
    review_path: Path,
    parent_sha256: object = None,
    review_sha256: object = None,
) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    parent, parent_hash = _review_file(parent_path, parent_sha256, document=True)
    assert parent is not None
    verify_gap_free_selection(parent)
    review, review_hash = _review_file(review_path, review_sha256, document=True)
    assert review is not None
    if (
        review.get("schema") != REVIEW_SCHEMA
        or review.get("source_manifest_path") != str(parent_path)
        or review.get("source_manifest_sha256") != parent_hash
    ):
        raise ValueError("review does not identify the immutable gap-free parent")
    evidence = review.get("evidence_files")
    if not isinstance(evidence, list) or not 0 < len(evidence) <= 64:
        raise ValueError("review evidence files are missing or exceed their bound")
    seen = set()
    for item in evidence:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not item["path"]
            or item["path"] in seen
            or "sha256" not in item
        ):
            raise ValueError("review evidence file is malformed or duplicated")
        seen.add(item["path"])
        if item["sha256"] is None:
            raise ValueError("review evidence SHA-256 is missing")
        _review_file(Path(item["path"]), item["sha256"])
    policy = review.get("policy")
    if (
        not isinstance(policy, dict)
        or policy.get("selection_not_based_on_pnl") is not True
        or not isinstance(policy.get("screening_thresholds"), dict)
        or not policy["screening_thresholds"]
    ):
        raise ValueError("review requires explicit screening thresholds independent of PnL")
    return parent, parent_hash, review, review_hash


def _review_derivation(parent: Mapping[str, Any], review: Mapping[str, Any]) -> dict[str, Any]:
    rows = parent["sources"]
    decisions = review.get("decisions")
    if not isinstance(decisions, list) or len(decisions) != len(rows):
        raise ValueError("review decisions must cover every gap-free parent source")
    by_id = {row["id"]: row for row in rows}
    reviewed = {}
    for decision in decisions:
        if not isinstance(decision, dict) or not isinstance(decision.get("id"), str):
            raise ValueError("review source decision is malformed")
        name = decision["id"]
        if name not in by_id or name in reviewed:
            raise ValueError("review contains an unknown or duplicate source")
        reasons = decision.get("reasons")
        if (
            decision.get("sha256") != by_id[name]["sha256"]
            or type(decision.get("exclude")) is not bool
            or not isinstance(reasons, list)
            or any(not isinstance(reason, str) or not reason.strip() for reason in reasons)
            or decision["exclude"] != bool(reasons)
        ):
            raise ValueError("review decision lacks exact source identity, outcome or reasons")
        reviewed[name] = decision
    selected, excluded = [], []
    for row in rows:
        decision = reviewed[row["id"]]
        if decision["exclude"]:
            exclusion = {key: row[key] for key in ("id", "venue", "symbol", "sha256")}
            exclusion["reasons"] = decision["reasons"]
            if "evidence" in decision:
                exclusion["evidence"] = decision["evidence"]
            excluded.append(exclusion)
        else:
            selected.append(row)
    blockers = []
    if {row["venue"] for row in selected} != set(GAP_FREE_VENUES):
        blockers.append(
            {
                "id": "universe",
                "kind": "coverage",
                "reason": "reviewed selection requires exactly: " + ", ".join(GAP_FREE_VENUES),
            }
        )
    archive_last = parent["coverage"]["archive_last_ms"]
    coverage = [_gap_coverage(row, archive_last) for row in selected]
    return {
        "status": "BLOCKED" if blockers else "READY",
        "sources": selected,
        "excluded": excluded,
        "blockers": blockers,
        "review_policy": review["policy"],
        "coverage": {
            "archive_last_ms": archive_last,
            "sources": coverage,
            "ending_before_archive_last_day": sum(
                row["ends_before_archive_last_day"] for row in coverage
            ),
            "ending_before_archive_last_record": sum(
                row["endpoint_lag_minutes"] > 0 for row in coverage
            ),
        },
        "totals": {
            "inventoried": len(rows),
            "selected": len(selected),
            "excluded": len(excluded),
            "selected_records": sum(row["records"] for row in selected),
            "selected_bytes": sum(row["bytes"] for row in selected),
            "by_venue": dict(sorted(Counter(row["venue"] for row in selected).items())),
            "excluded_by_venue": dict(sorted(Counter(row["venue"] for row in excluded).items())),
        },
    }


def select_reviewed_sources(
    parent_path: Path, review_path: Path, output_dir: Path
) -> dict[str, Any]:
    """Publish explicit price/identity exclusions; never call candidates historically certified."""
    parent_path, review_path = parent_path.absolute(), review_path.absolute()
    parent, parent_hash, review, review_hash = _review_inputs(parent_path, review_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    target, pending = output_dir / "manifest.json", output_dir / "manifest.json.pending"
    if target.exists() or pending.exists():
        raise FileExistsError("reviewed selection output already exists")
    manifest = {
        "schema": REVIEWED_SCHEMA,
        "created_utc": datetime.now(UTC).isoformat(),
        "selection_policy": "reviewed_gap_free_five_venues",
        "history_policy": GAP_FREE_POLICY,
        "expected_venues": list(GAP_FREE_VENUES),
        "source_manifest_path": str(parent_path),
        "source_manifest_sha256": parent_hash,
        "review_path": str(review_path),
        "review_sha256": review_hash,
        "historical_rules_verified": False,
        "research_readiness": REVIEWED_READINESS,
        "universe_reduced": True,
        **_review_derivation(parent, review),
    }
    # Recheck the complete provenance at publication, including sources audited earlier.
    _review_inputs(parent_path, review_path, parent_hash, review_hash)
    _publish(pending, manifest)
    os.link(pending, target)
    pending.unlink()
    directory_fd = os.open(output_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return manifest


def verify_reviewed_selection(manifest: Mapping[str, Any]) -> None:
    """Rebuild the exact reviewed partition without weakening its original gap audit."""
    if (
        manifest.get("schema") != REVIEWED_SCHEMA
        or manifest.get("selection_policy") != "reviewed_gap_free_five_venues"
        or manifest.get("history_policy") != GAP_FREE_POLICY
        or manifest.get("expected_venues") != list(GAP_FREE_VENUES)
        or manifest.get("historical_rules_verified") is not False
        or manifest.get("research_readiness") != REVIEWED_READINESS
        or manifest.get("universe_reduced") is not True
    ):
        raise ValueError("unsupported reviewed source selection contract")
    for key in ("source_manifest_path", "review_path", "source_manifest_sha256", "review_sha256"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise ValueError("reviewed selection provenance is missing")
    parent, _, review, _ = _review_inputs(
        Path(manifest["source_manifest_path"]),
        Path(manifest["review_path"]),
        manifest["source_manifest_sha256"],
        manifest["review_sha256"],
    )
    expected = _review_derivation(parent, review)
    if expected["status"] != "READY" or any(
        json.dumps(manifest.get(key), sort_keys=True, allow_nan=False)
        != json.dumps(value, sort_keys=True, allow_nan=False)
        for key, value in expected.items()
    ):
        raise ValueError("reviewed source selection differs from its audited decisions")


def _current_rules_inputs(
    parent_path: Path,
    snapshot_dir: Path,
    parent_sha256: object = None,
    snapshot_hashes: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], str, dict[str, dict[str, Any]]]:
    """Read one new, complete, version-pinned public catalogue per selected venue."""
    parent, parent_hash = _review_file(parent_path, parent_sha256, document=True)
    assert parent is not None
    verify_reviewed_selection(parent)
    if parent["status"] != "READY":
        raise ValueError("current-rule refresh requires a ready reviewed parent")
    if (
        not snapshot_dir.is_absolute()
        or ".." in snapshot_dir.parts
        or any(part.is_symlink() for part in (snapshot_dir, *snapshot_dir.parents))
    ):
        raise ValueError("current-rule catalogue directory must be absolute and non-symlinked")
    if not snapshot_dir.is_dir():
        raise ValueError("current-rule catalogue directory is missing")
    if snapshot_hashes is not None and set(snapshot_hashes) != set(GAP_FREE_VENUES):
        raise ValueError("current-rule catalogue hashes must cover exactly five venues")
    parent_created = datetime.fromisoformat(parent["created_utc"])
    if parent_created.utcoffset() != UTC.utcoffset(None):
        raise ValueError("reviewed parent creation time must be UTC")
    snapshots: dict[str, dict[str, Any]] = {}
    for venue in GAP_FREE_VENUES:
        paths = sorted(snapshot_dir.glob(f"{venue}-*.json"))
        if len(paths) != 1:
            raise ValueError(f"current-rule refresh requires exactly one {venue} catalogue")
        path = paths[0]
        expected_hash = snapshot_hashes[venue] if snapshot_hashes is not None else None
        value, digest = _review_file(path, expected_hash, document=True)
        assert value is not None
        if (
            value.get("schema") != 2
            or value.get("status") != "COLLECTED"
            or value.get("exchange") != venue
            or value.get("ccxt_version") != RESEARCH_CCXT_VERSION
            or value.get("historical_rules_verified") is not False
            or not isinstance(value.get("markets"), dict)
            or not 0 < len(value["markets"]) <= 30_000
        ):
            raise ValueError(f"{venue}: incomplete or incompatible current-rule catalogue")
        fetched = datetime.fromisoformat(value["fetched_utc"])
        if (
            fetched.utcoffset() != UTC.utcoffset(None)
            or fetched < parent_created
            or fetched > datetime.now(UTC) + timedelta(minutes=5)
        ):
            raise ValueError(f"{venue}: catalogue is not a fresh UTC snapshot")
        requests = value.get("requests")
        if not isinstance(requests, list) or not 0 < len(requests) <= 10:
            raise ValueError(f"{venue}: missing bounded public catalogue requests")
        total_bytes = 0
        for request in requests:
            if not isinstance(request, dict):
                raise ValueError(f"{venue}: malformed catalogue request receipt")
            check_catalog_url(
                venue,
                request.get("url", ""),
                request.get("method", ""),
            )
            size = request.get("bytes")
            response_path = Path(request.get("path", ""))
            if (
                request.get("status") != 200
                or type(size) is not int
                or not 0 < size <= 8 * 1024 * 1024
                or response_path.lstat().st_size != size
            ):
                raise ValueError(f"{venue}: incomplete catalogue response receipt")
            _review_file(response_path, request.get("sha256"))
            total_bytes += size
        if total_bytes > MAX_SNAPSHOT_BYTES:
            raise ValueError(f"{venue}: catalogue response budget exceeded")
        value["snapshot_path"] = str(path)
        value["snapshot_sha256"] = digest
        snapshots[venue] = value
    return parent, parent_hash, snapshots


def _current_rules_derivation(
    parent: Mapping[str, Any], snapshots: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    excluded = list(parent["excluded"])
    for row in parent["sources"]:
        venue, symbol = row["venue"], row["symbol"]
        snapshot = snapshots[venue]
        market = snapshot["markets"].get("USDT_" + symbol.removesuffix("/USDT"))
        reason: str | None = None
        rules: dict[str, Any] | None = None
        if not isinstance(market, dict):
            reason = "missing_from_current_public_catalogue"
        elif market.get("active") is not True:
            reason = "current_public_catalogue_not_active"
        else:
            try:
                rules = normalize_rules(venue, symbol, market)
            except (TypeError, ValueError) as exc:
                reason = f"current_public_rules_invalid: {str(exc)[:240]}"
        if reason is not None:
            excluded.append(
                {
                    "id": row["id"],
                    "venue": venue,
                    "symbol": symbol,
                    "sha256": row["sha256"],
                    "reasons": [reason],
                    "evidence": {"snapshot_sha256": snapshot["snapshot_sha256"]},
                }
            )
            continue
        refreshed = dict(row)
        refreshed["rules"] = rules
        refreshed["rules_snapshot"] = {
            "snapshot_path": snapshot["snapshot_path"],
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "fetched_utc": snapshot["fetched_utc"],
            "ccxt_version": snapshot["ccxt_version"],
        }
        selected.append(refreshed)
    blockers = []
    if {row["venue"] for row in selected} != set(GAP_FREE_VENUES):
        blockers.append(
            {
                "id": "universe",
                "kind": "coverage",
                "reason": "current-rule selection requires all five declared venues",
            }
        )
    archive_last = parent["coverage"]["archive_last_ms"]
    coverage = [_gap_coverage(row, archive_last) for row in selected]
    return {
        "status": "BLOCKED" if blockers else "READY",
        "sources": selected,
        "excluded": excluded,
        "blockers": blockers,
        "coverage": {
            "archive_last_ms": archive_last,
            "sources": coverage,
            "ending_before_archive_last_day": sum(
                row["ends_before_archive_last_day"] for row in coverage
            ),
            "ending_before_archive_last_record": sum(
                row["endpoint_lag_minutes"] > 0 for row in coverage
            ),
        },
        "totals": {
            "inventoried": parent["totals"]["inventoried"],
            "selected": len(selected),
            "excluded": len(excluded),
            "selected_records": sum(row["records"] for row in selected),
            "selected_bytes": sum(row["bytes"] for row in selected),
            "by_venue": dict(sorted(Counter(row["venue"] for row in selected).items())),
            "excluded_by_venue": dict(sorted(Counter(row["venue"] for row in excluded).items())),
        },
    }


def _current_rules_source_hashes(parent: Mapping[str, Any]) -> str:
    """Verify archived bytes once, without rerunning the price/chronology audit."""
    rows = parent["sources"]

    def check(row: Mapping[str, Any]) -> tuple[str, str]:
        digest = _review_file(Path(row["path"]), row["sha256"])[1]
        return row["id"], digest

    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="rules-source-hash") as pool:
        pairs = list(pool.map(check, rows))
    encoded = json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def select_current_rules_sources(
    parent_path: Path, snapshot_dir: Path, output_dir: Path
) -> dict[str, Any]:
    """Freeze the reviewed roster with current public CCXT rules as a dated assumption."""
    parent_path, snapshot_dir = parent_path.absolute(), snapshot_dir.absolute()
    parent, parent_hash, snapshots = _current_rules_inputs(parent_path, snapshot_dir)
    source_hash = _current_rules_source_hashes(parent)
    output_dir = output_dir.absolute()
    output_dir.mkdir(parents=True, exist_ok=True)
    target, pending = output_dir / "manifest.json", output_dir / "manifest.json.pending"
    if target.exists() or pending.exists():
        raise FileExistsError("current-rule selection output already exists")
    snapshot_hashes = {venue: snapshots[venue]["snapshot_sha256"] for venue in GAP_FREE_VENUES}
    manifest = {
        "schema": CURRENT_RULES_SCHEMA,
        "created_utc": datetime.now(UTC).isoformat(),
        "selection_policy": "reviewed_gap_free_latest_public_ccxt_five_venues",
        "history_policy": GAP_FREE_POLICY,
        "expected_venues": list(GAP_FREE_VENUES),
        "source_manifest_path": str(parent_path),
        "source_manifest_sha256": parent_hash,
        "historical_rules_verified": False,
        "research_readiness": CURRENT_RULES_READINESS,
        "universe_reduced": True,
        "rules_collection": {
            "ccxt_version": RESEARCH_CCXT_VERSION,
            "snapshots": [
                {
                    "exchange": venue,
                    "path": snapshots[venue]["snapshot_path"],
                    "sha256": snapshot_hashes[venue],
                    "fetched_utc": snapshots[venue]["fetched_utc"],
                }
                for venue in GAP_FREE_VENUES
            ],
        },
        "source_hash_audit": {
            "method": "complete_sha256_without_record_decode",
            "sources": len(parent["sources"]),
            "source_digests_sha256": source_hash,
            "verified_utc": datetime.now(UTC).isoformat(),
        },
        **_current_rules_derivation(parent, snapshots),
    }
    _current_rules_inputs(parent_path, snapshot_dir, parent_hash, snapshot_hashes)
    for row in parent["sources"]:
        if identity(Path(row["path"]).lstat()) != row["identity"]:
            raise ValueError("reviewed source changed after current-rule hash audit")
    _publish(pending, manifest)
    os.link(pending, target)
    pending.unlink()
    directory_fd = os.open(output_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return manifest


def verify_current_rules_selection(manifest: Mapping[str, Any]) -> None:
    """Re-derive rules from pinned public snapshots and immutable reviewed sources."""
    if (
        manifest.get("schema") != CURRENT_RULES_SCHEMA
        or manifest.get("selection_policy") != "reviewed_gap_free_latest_public_ccxt_five_venues"
        or manifest.get("history_policy") != GAP_FREE_POLICY
        or manifest.get("expected_venues") != list(GAP_FREE_VENUES)
        or manifest.get("historical_rules_verified") is not False
        or manifest.get("research_readiness") != CURRENT_RULES_READINESS
        or manifest.get("universe_reduced") is not True
    ):
        raise ValueError("unsupported current-rule source selection contract")
    collection = manifest.get("rules_collection")
    snapshots = collection.get("snapshots") if isinstance(collection, dict) else None
    if (
        not isinstance(collection, dict)
        or collection.get("ccxt_version") != RESEARCH_CCXT_VERSION
        or not isinstance(snapshots, list)
        or len(snapshots) != len(GAP_FREE_VENUES)
    ):
        raise ValueError("current-rule catalogue provenance is incomplete")
    snapshot_hashes: dict[str, str] = {}
    for venue, row in zip(GAP_FREE_VENUES, snapshots, strict=True):
        if (
            not isinstance(row, dict)
            or row.get("exchange") != venue
            or not isinstance(row.get("path"), str)
            or not isinstance(row.get("sha256"), str)
        ):
            raise ValueError("current-rule catalogue venue order differs")
        snapshot_hashes[venue] = row["sha256"]
    snapshot_dir = Path(snapshots[0]["path"]).parent
    if any(Path(row["path"]).parent != snapshot_dir for row in snapshots):
        raise ValueError("current-rule catalogue paths do not share a directory")
    parent_path = Path(manifest["source_manifest_path"])
    parent, _, loaded = _current_rules_inputs(
        parent_path, snapshot_dir, manifest["source_manifest_sha256"], snapshot_hashes
    )
    for row in snapshots:
        actual = loaded[row["exchange"]]
        if row != {
            "exchange": row["exchange"],
            "path": actual["snapshot_path"],
            "sha256": actual["snapshot_sha256"],
            "fetched_utc": actual["fetched_utc"],
        }:
            raise ValueError("current-rule catalogue provenance differs")
    audit = manifest.get("source_hash_audit")
    pairs = [(row["id"], row["sha256"]) for row in parent["sources"]]
    expected_source_hash = hashlib.sha256(
        json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        not isinstance(audit, dict)
        or audit.get("method") != "complete_sha256_without_record_decode"
        or audit.get("sources") != len(pairs)
        or audit.get("source_digests_sha256") != expected_source_hash
        or not isinstance(audit.get("verified_utc"), str)
    ):
        raise ValueError("current-rule source hash audit is incomplete")
    expected = _current_rules_derivation(parent, loaded)
    if expected["status"] != "READY" or any(
        json.dumps(manifest.get(key), sort_keys=True, allow_nan=False)
        != json.dumps(value, sort_keys=True, allow_nan=False)
        for key, value in expected.items()
    ):
        raise ValueError("current-rule selection differs from pinned snapshots")


def _flat_hour_inputs(
    parent_path: Path,
    audit_path: Path,
    parent_sha256: object = None,
    audit_sha256: object = None,
) -> tuple[dict[str, Any], str, dict[str, dict[str, Any]], str, int]:
    """Bind the pre-existing complete flat-run audit to the current-rule roster."""
    parent, parent_hash = _review_file(parent_path, parent_sha256, document=True)
    assert parent is not None
    verify_current_rules_selection(parent)
    if parent["status"] != "READY":
        raise ValueError("flat-hour selection requires a ready current-rule parent")
    _, audit_hash = _review_file(audit_path, audit_sha256)
    if audit_path.stat().st_size > 128 * 1024**2:
        raise ValueError("flat-hour audit exceeds its JSONL size bound")
    raw = audit_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != audit_hash:
        raise ValueError("flat-hour audit changed while reading")
    rows: dict[str, dict[str, Any]] = {}
    for line in raw.splitlines():
        if not line or len(rows) >= MAX_SOURCES:
            raise ValueError("flat-hour audit has an empty row or exceeds its source bound")
        record = json.loads(line)
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise ValueError("flat-hour audit row is malformed")
        if record["id"] in rows:
            raise ValueError("flat-hour audit has duplicate source IDs")
        rows[record["id"]] = record
    if not rows:
        raise ValueError("flat-hour audit is empty")
    by_id = {row["id"]: row for row in parent["sources"]}
    if len(by_id) != len(parent["sources"]) or not set(by_id).issubset(rows):
        raise ValueError("flat-hour audit does not cover the current-rule roster")
    for name, source in by_id.items():
        record = rows[name]
        if (
            any(
                record.get(key) != source[key]
                for key in (
                    "id",
                    "venue",
                    "symbol",
                    "path",
                    "sha256",
                    "records",
                    "bytes",
                    "first_ms",
                    "last_ms",
                )
            )
            or record.get("identity_before") != source["identity"]
            or record.get("identity_after") != source["identity"]
            or record.get("errors") != {}
        ):
            raise ValueError(f"flat-hour audit source identity differs: {name}")
        repeated = record.get("repeated_candles")
        flat = repeated.get("flat") if isinstance(repeated, dict) else None
        if not isinstance(flat, dict):
            raise ValueError(f"flat-hour audit lacks flat runs: {name}")
        longest = flat.get("longest_minutes")
        counts = flat.get("runs_at_least_minutes")
        top = flat.get("top")
        if (
            type(longest) is not int
            or not 0 <= longest <= source["records"]
            or not isinstance(counts, dict)
            or type(counts.get("60")) is not int
            or (counts["60"] > 0) != (longest >= FLAT_HOUR_MINUTES)
            or not isinstance(top, list)
            or (longest > 0 and not top)
        ):
            raise ValueError(f"flat-hour audit run summary is inconsistent: {name}")
        if longest:
            example = top[0]
            if (
                not isinstance(example, dict)
                or example.get("value") != longest
                or type(example.get("timestamp_ms")) is not int
                or type(example.get("end_ms")) is not int
                or example["end_ms"] - example["timestamp_ms"] != (longest - 1) * 60_000
                or not source["first_ms"] <= example["timestamp_ms"]
                or example["end_ms"] > source["last_ms"]
                or not isinstance(example.get("ohlc"), list)
                or len(example["ohlc"]) != 4
                or not all(price == example["ohlc"][0] for price in example["ohlc"])
            ):
                raise ValueError(f"flat-hour audit longest run is inconsistent: {name}")
    return parent, parent_hash, rows, audit_hash, len(rows)


def _flat_hour_derivation(
    parent: Mapping[str, Any], audit: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    excluded = list(parent["excluded"])
    for row in parent["sources"]:
        flat = audit[row["id"]]["repeated_candles"]["flat"]
        if flat["longest_minutes"] >= FLAT_HOUR_MINUTES:
            excluded.append(
                {
                    "id": row["id"],
                    "venue": row["venue"],
                    "symbol": row["symbol"],
                    "sha256": row["sha256"],
                    "reasons": [FLAT_HOUR_REASON],
                    "evidence": {
                        "longest_minutes": flat["longest_minutes"],
                        "longest_run": flat["top"][0],
                    },
                }
            )
        else:
            selected.append(row)
    blockers = []
    if {row["venue"] for row in selected} != set(GAP_FREE_VENUES):
        blockers.append(
            {
                "id": "universe",
                "kind": "coverage",
                "reason": "flat-hour selection requires all five declared venues",
            }
        )
    coverage_by_id = {row["id"]: row for row in parent["coverage"]["sources"]}
    coverage = [coverage_by_id[row["id"]] for row in selected]
    return {
        "status": "BLOCKED" if blockers else "READY",
        "sources": selected,
        "excluded": excluded,
        "blockers": blockers,
        "coverage": {
            "archive_last_ms": parent["coverage"]["archive_last_ms"],
            "sources": coverage,
            "ending_before_archive_last_day": sum(
                row["ends_before_archive_last_day"] for row in coverage
            ),
            "ending_before_archive_last_record": sum(
                row["endpoint_lag_minutes"] > 0 for row in coverage
            ),
        },
        "totals": {
            "inventoried": parent["totals"]["inventoried"],
            "selected": len(selected),
            "excluded": len(excluded),
            "selected_records": sum(row["records"] for row in selected),
            "selected_bytes": sum(row["bytes"] for row in selected),
            "by_venue": dict(sorted(Counter(row["venue"] for row in selected).items())),
            "excluded_by_venue": dict(sorted(Counter(row["venue"] for row in excluded).items())),
        },
    }


def select_flat_hour_sources(
    parent_path: Path, audit_path: Path, output_dir: Path
) -> dict[str, Any]:
    """Publish a strict one-hour flat-OHLC subset without modifying source bytes."""
    parent_path, audit_path, output_dir = (
        parent_path.absolute(),
        audit_path.absolute(),
        output_dir.absolute(),
    )
    parent, parent_hash, audit, audit_hash, audited_rows = _flat_hour_inputs(
        parent_path, audit_path
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    target, pending = output_dir / "manifest.json", output_dir / "manifest.json.pending"
    if target.exists() or pending.exists():
        raise FileExistsError("flat-hour selection output already exists")
    manifest = {
        "schema": FLAT_HOUR_SCHEMA,
        "created_utc": datetime.now(UTC).isoformat(),
        "selection_policy": "reviewed_gap_free_current_rules_flat_hour_five_venues",
        "history_policy": GAP_FREE_POLICY,
        "expected_venues": list(GAP_FREE_VENUES),
        "source_manifest_path": str(parent_path),
        "source_manifest_sha256": parent_hash,
        "historical_rules_verified": False,
        "research_readiness": CURRENT_RULES_READINESS,
        "universe_reduced": True,
        "rules_collection": parent["rules_collection"],
        "source_hash_audit": parent["source_hash_audit"],
        "flat_audit": {
            "path": str(audit_path),
            "sha256": audit_hash,
            "threshold_minutes": FLAT_HOUR_MINUTES,
            "audited_rows": audited_rows,
            "matched_parent_sources": len(parent["sources"]),
            "excluded_parent_sources": sum(
                audit[row["id"]]["repeated_candles"]["flat"]["longest_minutes"] >= FLAT_HOUR_MINUTES
                for row in parent["sources"]
            ),
        },
        **_flat_hour_derivation(parent, audit),
    }
    # Reject a changed parent, audit or source identity before atomic publication.
    _flat_hour_inputs(parent_path, audit_path, parent_hash, audit_hash)
    _publish(pending, manifest)
    os.link(pending, target)
    pending.unlink()
    directory_fd = os.open(output_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return manifest


def verify_flat_hour_selection(manifest: Mapping[str, Any]) -> None:
    """Rebuild the exact child partition from its pinned parent and audit."""
    if (
        manifest.get("schema") != FLAT_HOUR_SCHEMA
        or manifest.get("selection_policy")
        != "reviewed_gap_free_current_rules_flat_hour_five_venues"
        or manifest.get("history_policy") != GAP_FREE_POLICY
        or manifest.get("expected_venues") != list(GAP_FREE_VENUES)
        or manifest.get("historical_rules_verified") is not False
        or manifest.get("research_readiness") != CURRENT_RULES_READINESS
        or manifest.get("universe_reduced") is not True
    ):
        raise ValueError("unsupported flat-hour selection contract")
    provenance = manifest.get("flat_audit")
    if not isinstance(provenance, dict) or provenance.get("threshold_minutes") != 60:
        raise ValueError("flat-hour audit provenance is incomplete")
    parent, _, audit, _, audited_rows = _flat_hour_inputs(
        Path(manifest["source_manifest_path"]),
        Path(provenance["path"]),
        manifest["source_manifest_sha256"],
        provenance["sha256"],
    )
    expected_audit = {
        "path": provenance["path"],
        "sha256": provenance["sha256"],
        "threshold_minutes": FLAT_HOUR_MINUTES,
        "audited_rows": audited_rows,
        "matched_parent_sources": len(parent["sources"]),
        "excluded_parent_sources": sum(
            audit[row["id"]]["repeated_candles"]["flat"]["longest_minutes"] >= FLAT_HOUR_MINUTES
            for row in parent["sources"]
        ),
    }
    expected = _flat_hour_derivation(parent, audit)
    if expected["status"] != "READY" or any(
        json.dumps(manifest.get(key), sort_keys=True, allow_nan=False)
        != json.dumps(value, sort_keys=True, allow_nan=False)
        for key, value in {
            **expected,
            "flat_audit": expected_audit,
            "rules_collection": parent["rules_collection"],
            "source_hash_audit": parent["source_hash_audit"],
        }.items()
    ):
        raise ValueError("flat-hour selection differs from pinned audit and parent")


def check_catalog_url(
    venue: str, url: str, method: str = "GET", headers: object = None, body: object = None
) -> None:
    if venue not in ENDPOINTS or method != "GET" or body is not None or len(url) > 2048:
        raise ValueError("catalog request outside public read-only profile")
    if headers not in (None, {}, {"Content-Type": "application/json"}):
        raise ValueError("unexpected catalog request headers")
    parsed = urlsplit(url)
    host, path, query = ENDPOINTS[venue]
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if venue == "bingx":
        timestamps = [value for key, value in pairs if key == "timestamp"]
        if (
            len(timestamps) != 1
            or not timestamps[0].isascii()
            or not timestamps[0].isdigit()
            or len(timestamps[0]) != 13
        ):
            raise ValueError("invalid BingX public timestamp")
        pairs = [(key, value) for key, value in pairs if key != "timestamp"]
    allowed_bitfinex_spot_list = (
        venue == "bitfinex"
        and parsed.netloc == "api-pub.bitfinex.com"
        and parsed.path == "/v2/conf/pub:list:pair:exchange"
        and not pairs
    )
    if (
        parsed.scheme != "https"
        or parsed.fragment
        or not (
            allowed_bitfinex_spot_list
            or (parsed.netloc == host and parsed.path == path and sorted(pairs) == sorted(query))
        )
    ):
        raise ValueError("catalog request outside exact endpoint allowlist")


async def collect_catalog(
    venue: str,
    output_dir: Path,
    *,
    wire: Any = None,
    ccxt_version: str = CCXT_VERSION,
) -> dict[str, Any]:
    """One spot catalogue through an explicitly pinned SDK; bounded public GETs only."""
    import ccxt.async_support as ccxt  # type: ignore[import-untyped]

    from grid.exchange.adapter import ReadLimits
    from grid.exchange.ccxt_transport import HTTPWire, decode_json

    if (
        ccxt_version not in (CCXT_VERSION, RESEARCH_CCXT_VERSION)
        or ccxt.__version__ != ccxt_version
        or venue not in VENUES
    ):
        raise ValueError("unsupported venue or CCXT version")
    output_dir = output_dir.absolute()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    prefix = f"{venue}-{stamp}"
    limits = ReadLimits(requests=10, timeout_ms=30_000, response_bytes=8 * 1024 * 1024)
    transport = wire if wire is not None else HTTPWire()
    evidence: list[dict[str, Any]] = []
    total_bytes = 0
    started = time.monotonic()
    options: dict[str, Any] = {
        "defaultType": "spot",
        "adjustForTimeDifference": False,
        "maxRetriesOnFailure": 0,
        "fetchMarkets": {"types": ["spot"], "fetchTickersFees": False},
    }
    if venue == "htx":
        options["fetchMarkets"] = {"types": {"spot": True, "linear": False, "inverse": False}}
    client: Any = getattr(ccxt, venue)(
        {
            "enableRateLimit": True,
            "options": options,
            "timeout": 30_000,
            "maxRetriesOnFailure": 0,
            "verbose": False,
            "enableLastHttpResponse": False,
            "enableLastJsonResponse": False,
            "enableLastResponseHeaders": False,
        }
    )
    client.number = str
    client.has["fetchCurrencies"] = False
    if venue in {"xt", "bingx"}:
        client.fetch_markets = client.fetch_spot_markets
    if venue == "gate":

        async def no_margin(params: Any) -> list[object]:
            return []

        client.publicMarginGetCurrencyPairs = no_margin

    async def fetch(url: str, method: str = "GET", headers: Any = None, body: Any = None) -> Any:
        nonlocal total_bytes
        check_catalog_url(venue, url, method, headers, body)
        if len(evidence) >= 10 or time.monotonic() - started >= 120:
            raise ValueError("catalog request budget exceeded")
        event: dict[str, Any] = {"url": url, "method": method, "status": None, "bytes": 0}
        evidence.append(event)
        reply = await transport.get(url, limits)
        event.update(status=reply.status, bytes=len(reply.body))
        total_bytes += len(reply.body)
        if len(reply.body) > limits.response_bytes or total_bytes > MAX_SNAPSHOT_BYTES:
            raise ValueError("catalog response byte budget exceeded")
        response_path = output_dir / f"{prefix}-response-{len(evidence)}.bin"
        with response_path.open("xb") as stream:
            stream.write(reply.body)
        event.update(path=str(response_path), sha256=hashlib.sha256(reply.body).hexdigest())
        if reply.status != 200:
            raise ValueError(f"public catalogue HTTP status {reply.status}")
        response = decode_json(reply.body, limits.response_bytes)
        client.handle_errors(reply.status, "", url, method, {}, "", response, {}, None)
        return response

    client.fetch = fetch
    result: dict[str, Any] = {
        "schema": 2,
        "exchange": venue,
        "ccxt_version": ccxt_version,
        "fetched_utc": datetime.now(UTC).isoformat(),
        "requests": evidence,
        "documentation": DOCUMENTATION[venue],
        "historical_rules_verified": False,
        "markets": {},
        "status": "FAILED",
    }
    try:
        params = {"config": "pub:info:pair"} if venue == "bitfinex" else {}
        async with asyncio.timeout(120):
            raw = await client.load_markets(True, params)
        if not isinstance(raw, dict) or not raw or len(raw) > 30_000:
            raise ValueError("invalid or oversized spot catalogue")
        exchange_pairs: set[str] | None = None
        if venue == "bitfinex":
            listing = await fetch(BITFINEX_SPOT_URL)
            if (
                not isinstance(listing, list)
                or len(listing) != 1
                or not isinstance(listing[0], list)
                or not 0 < len(listing[0]) <= 30_000
                or any(not isinstance(item, str) or not item for item in listing[0])
            ):
                raise ValueError("invalid Bitfinex exchange pair listing")
            exchange_pairs = set(listing[0])
            if len(exchange_pairs) != len(listing[0]):
                raise ValueError("duplicate Bitfinex exchange pair listing")
            result["spot_listing"] = {
                name: evidence[-1][name] for name in ("url", "path", "sha256")
            }
        markets: dict[str, Any] = {}
        for row in raw.values():
            if not isinstance(row, dict) or row.get("spot") is not True:
                continue
            key = f"{row['quote']}_{row['base']}"
            if key in markets:
                raise ValueError("duplicate unified spot market")
            markets[key] = {
                name: row.get(name)
                for name in (
                    "id",
                    "symbol",
                    "base",
                    "quote",
                    "baseId",
                    "quoteId",
                    "spot",
                    "type",
                    "contract",
                    "swap",
                    "future",
                    "leveraged",
                    "active",
                    "precision",
                    "limits",
                    "info",
                )
            }
            markets[key]["precision_mode"] = client.precisionMode
            if exchange_pairs is not None:
                market_id = row["id"]
                markets[key]["exchange_listed"] = (
                    isinstance(market_id, str)
                    and market_id.startswith("t")
                    and market_id[1:] in exchange_pairs
                )
        if not markets:
            raise ValueError("empty spot catalogue")
        result.update(status="COLLECTED", markets=_json_value(markets))
    except (Exception, asyncio.CancelledError) as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
        if isinstance(exc, asyncio.CancelledError):
            _publish(output_dir / f"{prefix}.json", result)
            raise
    finally:
        await client.close()
        await transport.close()
    result["elapsed_seconds"] = time.monotonic() - started
    snapshot_path = output_dir / f"{prefix}.json"
    _publish(snapshot_path, result)
    result["snapshot_path"] = str(snapshot_path)
    result["snapshot_sha256"] = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    return result


async def collect_snapshots(
    output_dir: Path,
    venues: Sequence[str] = VENUES,
    *,
    ccxt_version: str = CCXT_VERSION,
) -> dict[str, Any]:
    if len(set(venues)) != len(venues) or any(venue not in VENUES for venue in venues):
        raise ValueError("invalid catalogue roster")
    results = []
    for venue in venues:
        result = await collect_catalog(venue, output_dir, ccxt_version=ccxt_version)
        results.append(
            {
                name: result.get(name)
                for name in (
                    "exchange",
                    "status",
                    "error",
                    "requests",
                    "elapsed_seconds",
                    "snapshot_path",
                    "snapshot_sha256",
                )
            }
        )
    summary = {
        "schema": "grid-research-catalogues-v1",
        "ccxt_version": ccxt_version,
        "venues": results,
        "status": "COLLECTED"
        if all(row["status"] == "COLLECTED" for row in results)
        else "INCOMPLETE",
    }
    _publish(
        output_dir / ("collection-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json"),
        summary,
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--dataset-root", type=Path, default=DATA_ROOT)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--snapshots", type=Path, action="append", default=[])
    prepare.add_argument("--inventory-only", action="store_true")
    collect = sub.add_parser("collect")
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--venues", nargs="+", choices=VENUES, default=VENUES)
    collect.add_argument(
        "--ccxt-version", choices=(CCXT_VERSION, RESEARCH_CCXT_VERSION), default=CCXT_VERSION
    )
    args = parser.parse_args()
    if args.command == "collect":
        value = asyncio.run(
            collect_snapshots(args.output, args.venues, ccxt_version=args.ccxt_version)
        )
        print(json.dumps(value, ensure_ascii=False))
        return 0 if value["status"] == "COLLECTED" else 2

    def progress(done: int, total: int, market: str) -> None:
        if done % 25 == 0 or done == total:
            print(json.dumps({"audited": done, "sources": total, "market": market}), flush=True)

    value = prepare_sources(
        args.dataset_root,
        args.output,
        args.snapshots,
        audit=not args.inventory_only,
        progress=progress,
    )
    print(
        json.dumps(
            {
                "status": value["status"],
                "totals": value["totals"],
                "blockers": len(value["blockers"]),
            }
        )
    )
    return 0 if value["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
