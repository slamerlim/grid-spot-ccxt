"""Read-only, pre-return archive selection. No strategy or price-return calculation.

Run with Python 3.12+: python scripts/module1_selection/audit.py --root ROOT --output NEW_DIR
Outputs are exclusively created outside the source tree; originals are never repaired.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import stat
import struct
from collections import Counter
from pathlib import Path
from typing import Any

RECORD = struct.Struct("<qffff")
MINUTE = 60_000
DAY = 86_400_000
VENUES = ("okx", "htx", "gate", "xt", "bingx", "kucoin", "bitfinex")
ALIASES = {"huobi": "htx", "huobipro": "htx", "gateio": "gate"}
LEVERAGED = re.compile(r"(?:[235][LS]|BULL|BEAR|UP|DOWN)$")
MAX_TIMESTAMP = 253402300740000
STATUS = "UNVERIFIED SOURCE / ASSUMPTION-BASED, NOT CERTIFIED HISTORICAL PERFORMANCE"


def identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def row_errors(row: tuple[Any, ...]) -> list[str]:
    timestamp, opened, high, low, close = row
    errors = []
    if timestamp <= 0 or timestamp > MAX_TIMESTAMP or timestamp % MINUTE:
        errors.append("invalid_timestamp")
    if not all(math.isfinite(x) and x > 0 for x in (opened, high, low, close)):
        errors.append("invalid_price")
    elif not low <= min(opened, close) <= max(opened, close) <= high:
        errors.append("invalid_ohlc")
    return errors


def inventory(root: Path, minimum_days: int) -> list[dict[str, Any]]:
    """Metadata plus two endpoint records; this is not whole-archive validation."""
    result = []
    for path in sorted(root.glob("*.bin")):
        info = path.lstat()
        fields = path.stem.split("_")
        item: dict[str, Any] = {
            "file": path.name,
            "bytes": info.st_size,
            "rows": info.st_size // RECORD.size,
            "file_identity": identity(info),
            "rejections": [],
        }
        errors = item["rejections"]
        if len(fields) != 3:
            errors.append("filename_not_three_fields")
        else:
            venue, quote, base = fields
            item.update(
                venue=venue, canonical_venue=ALIASES.get(venue, venue), quote=quote, base=base
            )
            if LEVERAGED.search(base):
                errors.append("suspect_leveraged_name")
        if not stat.S_ISREG(info.st_mode):
            errors.append("not_regular_nonsymlink")
        if info.st_size < RECORD.size or info.st_size % RECORD.size:
            errors.append("malformed_length")
        if not errors:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                first = RECORD.unpack(stream.read(RECORD.size))
                stream.seek(-RECORD.size, 2)
                last = RECORD.unpack(stream.read(RECORD.size))
                if identity(os.fstat(stream.fileno())) != identity(info):
                    raise ValueError("source changed during endpoint inventory")
            if identity(path.lstat()) != identity(info):
                raise ValueError("source changed after endpoint inventory")
            for name, row in (("first", first), ("last", last)):
                item[name + "_ms"] = row[0]
                if row_errors(row):
                    errors.append(name + "_invalid")
            span = (last[0] - first[0]) // MINUTE + 1
            item["span_minutes"] = span
            if span < minimum_days * 1440 or item["rows"] < minimum_days * 1440:
                errors.append("under_required_days_or_rows")
            if span <= 0 or item["rows"] * 100 < span * 99:
                errors.append("endpoint_density_below_99pct")
        result.append(item)
    return result


def scan(path: Path, output: Path, expected: tuple[int, ...]) -> dict[str, Any]:
    """Evaluate/hash every actual record, retaining every gap; bounded read blocks."""
    before = path.lstat()
    if identity(before) != expected or not stat.S_ISREG(before.st_mode):
        raise ValueError("source identity mismatch or nonregular source")
    rows = accepted = flat = missing = gap_count = max_gap = 0
    first = last = previous = None
    errors: Counter[str] = Counter()
    days: Counter[int] = Counter()
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    gap_path = output / (path.name + ".gaps.csv")
    with os.fdopen(fd, "rb") as stream, gap_path.open("x", newline="") as gaps:
        writer = csv.writer(gaps)
        writer.writerow(("previous_ms", "next_ms", "missing_minutes"))
        while raw := stream.read(RECORD.size * 65536):
            digest.update(raw)
            if len(raw) % RECORD.size:
                errors["malformed_length"] += 1
                break
            for row in RECORD.iter_unpack(raw):
                rows += 1
                timestamp, opened, high, low, close = row
                problems = row_errors(row)
                if previous is not None and timestamp <= previous:
                    problems.append("duplicate" if timestamp == previous else "disorder")
                previous = timestamp
                if problems:
                    errors.update(problems)
                    continue
                if first is None:
                    first = timestamp
                if last is not None and timestamp > last + MINUTE:
                    gap = (timestamp - last) // MINUTE - 1
                    missing += gap
                    gap_count += 1
                    max_gap = max(max_gap, gap)
                    writer.writerow((last, timestamp, gap))
                last = timestamp
                accepted += 1
                days[timestamp // DAY] += 1
                flat += opened == high == low == close
        if identity(os.fstat(stream.fileno())) != expected:
            raise ValueError("source changed while reading")
    if identity(path.lstat()) != expected:
        raise ValueError("source changed after reading")
    complete = sorted(day for day, count in days.items() if count == 1440)
    result = {
        "rows": rows,
        "accepted": accepted,
        "errors": dict(sorted(errors.items())),
        "first_ms": first,
        "last_ms": last,
        "sha256": digest.hexdigest(),
        "missing_minutes": missing,
        "gap_count": gap_count,
        "max_gap_minutes": max_gap,
        "complete_utc_days": complete,
        "flat_rows": flat,
        "source_unchanged": True,
    }
    write_json(output / (path.name + ".validation.json"), result)
    return result


def longest_run(days: set[int]) -> int:
    longest = length = 0
    previous = None
    for day in sorted(days):
        length = length + 1 if previous is not None and day == previous + 1 else 1
        longest = max(longest, length)
        previous = day
    return longest


def select(
    root: Path, output: Path, *, count: int = 10, minimum_days: int = 365, max_scans: int = 64
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    output = output.resolve()
    if not root.is_dir() or count < 1 or minimum_days < 1 or max_scans < count:
        raise ValueError("invalid selection bounds")
    if (
        output == root
        or root in output.parents
        or output == Path("/opt/datasets")
        or Path("/opt/datasets") in output.parents
    ):
        raise ValueError("outputs must be outside original source trees")
    output.mkdir(parents=True, exist_ok=False)
    rule = {
        "version": 2,
        "venue_order": VENUES,
        "alias_mapping": ALIASES,
        "quote": "USDT",
        "distinct_base": True,
        "minimum_days": minimum_days,
        "minimum_endpoint_density": "0.99",
        "minimum_common_complete_utc_days": minimum_days,
        "count": count,
        "max_full_candidate_scans": max_scans,
        "ranking": "round-robin venues; descending endpoint span, descending rows, filename",
        "selection_before_strategy_returns": True,
        "gap_policy": "preserve gaps; never concatenate complete days or forward-fill",
        "source_status": STATUS,
    }
    write_json(output / "selection_rule.json", rule)
    items = inventory(root, minimum_days)
    write_json(output / "inventory.json", items)
    pools = {
        venue: sorted(
            [
                item
                for item in items
                if item.get("canonical_venue") == venue
                and item.get("quote") == "USDT"
                and not item["rejections"]
            ],
            key=lambda item: (-item["span_minutes"], -item["rows"], item["file"]),
        )
        for venue in VENUES
    }
    selected: list[dict[str, Any]] = []
    decisions: dict[str, str] = {}
    bases: set[str] = set()
    shared: set[int] | None = None
    scans = 0
    while len(selected) < count and any(pools.values()):
        for venue in VENUES:
            while pools[venue]:
                item = pools[venue].pop(0)
                if item["base"] in bases:
                    decisions[item["file"]] = "duplicate_base"
                    continue
                if scans >= max_scans:
                    raise ValueError("full candidate scan limit; retained partial audit, no roster")
                scans += 1
                validation = scan(root / item["file"], output, tuple(item["file_identity"]))
                if validation["errors"]:
                    decisions[item["file"]] = "full_file_structural_errors"
                    continue
                candidate_days = set(validation["complete_utc_days"])
                common = candidate_days if shared is None else shared & candidate_days
                if len(common) < minimum_days:
                    decisions[item["file"]] = "insufficient_common_complete_utc_days"
                    continue
                chosen = dict(item, validation=validation)
                selected.append(chosen)
                shared = common
                bases.add(item["base"])
                decisions[item["file"]] = "selected"
                break
            if len(selected) == count:
                break
    with (output / "candidate_decisions.csv").open("x", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("file", "decision"))
        for item in items:
            reason = decisions.get(item["file"])
            if reason is None:
                reasons = list(item["rejections"])
                if item.get("canonical_venue") not in VENUES:
                    reasons.append("outside_target_venues")
                if item.get("quote") != "USDT":
                    reasons.append("different_quote")
                reason = ";".join(reasons) or "not_reached_frozen_round_robin"
            writer.writerow((item["file"], reason))
    result = {
        "schema": "module1-selection-v2",
        "rule": rule,
        "root": str(root),
        "inventory_files": len(items),
        "inventory_bytes": sum(item["bytes"] for item in items),
        "selected": selected,
        "selected_total_rows": sum(item["rows"] for item in selected),
        "common_start_ms": max((item["first_ms"] for item in selected), default=None),
        "common_end_ms_exclusive": min(
            (item["last_ms"] + MINUTE for item in selected), default=None
        ),
        "common_complete_utc_days": sorted(shared or set()),
        "longest_consecutive_complete_utc_days": longest_run(shared or set()),
        "requested_cohort_complete": len(selected) == count,
        "source_status": STATUS,
    }
    write_json(output / "selection_manifest.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = select(arguments.root, arguments.output)
    print(json.dumps({"selected": len(result["selected"]), "output": str(arguments.output)}))
    return 0 if result["requested_cohort_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
