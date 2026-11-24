"""Exhaustive read-only HTX/Gate OHLC audit, including invalid files and every gap.

Uses NumPy from the existing audited-simulator environment, without changing the
application dependencies. Example:
  /opt/module1_n/.venv/bin/python -m scripts.audit_archive_integrity \
      --root /opt/datasets/crypto_1m --output /opt/grid-runs/NEW-AUDIT

All source bytes are hashed. Fixed-size blocks bound normal scan memory. Rare
unordered histories use a temporary on-disk timestamp index to count exact
duplicates and gaps without assuming the original record order was correct.
Gap ranges use compact little-endian <qq (previous/next UTC milliseconds);
--export-gaps FILE --csv-output NEW_FILE provides a streaming readable export.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import os
import platform
import resource
import sqlite3
import stat
import struct
import tempfile
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO, cast

from scripts.module1_selection.audit import (
    ALIASES,
    LEVERAGED,
    MAX_TIMESTAMP,
    MINUTE,
    RECORD,
)

CHUNK_ROWS = 262_144
VENUES = {"htx", "gate"}
ERROR_KEYS = (
    "timestamp_out_of_range_rows",
    "timestamp_off_minute_rows",
    "timestamp_after_audit_rows",
    "nonfinite_price_rows",
    "nonpositive_price_rows",
    "invalid_ohlc_rows",
    "duplicate_adjacent_rows",
    "descending_adjacent_rows",
    "invalid_value_rows",
)
GAP_FIELDS = (
    "file",
    "venue",
    "market",
    "previous_ms",
    "next_ms",
    "missing_start_utc",
    "missing_end_utc_exclusive",
    "missing_minutes",
)


def _numpy() -> Any:
    return importlib.import_module("numpy")


def _identity(info: os.stat_result) -> list[int]:
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _utc(value: int | None) -> str | None:
    if value is None or not 0 < value <= MAX_TIMESTAMP:
        return None
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def _json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def _roster(root: Path) -> list[dict[str, Any]]:
    roster = []
    for path in sorted(root.iterdir()):
        venue = path.name.partition("_")[0]
        if ALIASES.get(venue, venue) not in VENUES:
            continue
        info = path.lstat()
        roster.append({"file": path.name, "identity": _identity(info), "mode": info.st_mode})
    return roster


def _gap(writer: Any, result: dict[str, Any], previous: int, following: int) -> None:
    missing = (following - previous) // MINUTE - 1
    if missing <= 0:
        return
    result["gap_count"] += 1
    result["missing_minutes"] += missing
    if missing > result["largest_gap_minutes"]:
        result["largest_gap_minutes"] = missing
        result["largest_gap_start_utc"] = _utc(previous + MINUTE)
        result["largest_gap_end_utc_exclusive"] = _utc(following)
    if result.get("gap_format") == "binary":
        writer.write(struct.pack("<qq", previous, following))
        return
    writer.writerow(
        (
            result["file"],
            result["venue"],
            result["market"],
            previous,
            following,
            _utc(previous + MINUTE),
            _utc(following),
            missing,
        )
    )


def _reset_gap_counts(result: dict[str, Any]) -> None:
    result.update(
        gap_count=0,
        missing_minutes=0,
        largest_gap_minutes=0,
        largest_gap_start_utc=None,
        largest_gap_end_utc_exclusive=None,
    )


def _ordered_gap_recheck(path: Path, destination: Path, result: dict[str, Any]) -> None:
    """Only for disordered sources: exact distinct valid timestamps, bounded RAM."""
    with (
        tempfile.TemporaryDirectory(prefix="timestamp-index-", dir=destination.parent) as temp,
        closing(sqlite3.connect(Path(temp) / "timestamps.sqlite")) as database,
    ):
        database.execute("PRAGMA cache_size=-8192")
        database.execute("CREATE TABLE stamps (stamp INTEGER PRIMARY KEY)")
        timestamp_count = 0
        with path.open("rb") as source:
            while raw := source.read(RECORD.size * CHUNK_ROWS):
                valid = [(row[0],) for row in RECORD.iter_unpack(raw[: len(raw) // 24 * 24])]
                timestamp_count += len(valid)
                database.executemany("INSERT OR IGNORE INTO stamps VALUES (?)", valid)
                database.commit()
        unique = database.execute("SELECT COUNT(*) FROM stamps").fetchone()[0]
        result["duplicate_timestamp_rows"] = timestamp_count - unique
        valid_query = (
            "SELECT stamp FROM stamps WHERE stamp > 0 AND stamp <= ? "
            "AND stamp % ? = 0 ORDER BY stamp"
        )
        result["unique_valid_timestamp_rows"] = database.execute(
            "SELECT COUNT(*) FROM stamps WHERE stamp > 0 AND stamp <= ? AND stamp % ? = 0",
            (MAX_TIMESTAMP, MINUTE),
        ).fetchone()[0]
        _reset_gap_counts(result)
        binary = result.get("gap_format") == "binary"
        with (
            destination.open("wb")
            if binary
            else destination.open("w", newline="", encoding="utf-8")
        ) as output:
            writer: Any = output if binary else csv.writer(cast(TextIO, output))
            if not binary:
                writer.writerow(GAP_FIELDS)
            previous = None
            for (timestamp,) in database.execute(valid_query, (MAX_TIMESTAMP, MINUTE)):
                if previous is not None:
                    _gap(writer, result, previous, timestamp)
                previous = timestamp


def scan_file(
    path: Path,
    gaps_directory: Path,
    *,
    audit_ms: int,
    chunk_rows: int = CHUNK_ROWS,
    gap_format: str = "binary",
) -> dict[str, Any]:
    """Hash all bytes; count all errors; never discard a source for bad records."""
    if chunk_rows < 1 or gap_format not in {"binary", "csv"}:
        raise ValueError("invalid chunk_rows or gap_format")
    np = _numpy()
    before = path.lstat()
    fields = path.stem.split("_")
    venue = ALIASES.get(fields[0], fields[0])
    result: dict[str, Any] = {
        "file": path.name,
        "venue": venue,
        "market": f"{fields[2]}/{fields[1]}" if len(fields) == 3 else None,
        "quote": fields[1] if len(fields) == 3 else "UNKNOWN",
        "gap_format": gap_format,
        "filename_valid": len(fields) == 3 and path.suffix == ".bin",
        "suspect_leveraged_name": bool(len(fields) == 3 and LEVERAGED.search(fields[2])),
        "bytes": before.st_size,
        "complete_records": 0,
        "trailing_bytes": before.st_size % 24,
        "source_identity": _identity(before),
        "source_unchanged": False,
        "first_record_ms": None,
        "last_record_ms": None,
        "first_utc": None,
        "last_utc": None,
        "first_valid_timestamp_ms": None,
        "last_valid_timestamp_ms": None,
        "flat_ohlc_rows": 0,
        "valid_timestamp_rows": 0,
        "structural_errors": [],
        "error_counts": dict.fromkeys(ERROR_KEYS, 0),
        "error_examples": {},
        "sha256": None,
    }
    _reset_gap_counts(result)
    if not stat.S_ISREG(before.st_mode):
        result["structural_errors"].append("not_regular_nonsymlink")
        result["classification"] = "INVALID"
        return result
    if not result["filename_valid"]:
        result["structural_errors"].append("invalid_filename")
    if before.st_size == 0:
        result["structural_errors"].append("empty_file")
    if result["trailing_bytes"]:
        result["structural_errors"].append("incomplete_24_byte_record")
    gap_path = gaps_directory / (
        path.name + (".gaps.bin" if gap_format == "binary" else ".gaps.csv")
    )
    result["gap_artifact"] = str(gap_path.resolve())
    digest = hashlib.sha256()
    dtype = np.dtype([("timestamp", "<i8"), ("prices", "<f4", (4,))])
    previous = previous_valid = None
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with (
        os.fdopen(descriptor, "rb") as stream,
        (
            gap_path.open("xb")
            if gap_format == "binary"
            else gap_path.open("x", newline="", encoding="utf-8")
        ) as gaps,
    ):
        if _identity(os.fstat(stream.fileno())) != result["source_identity"]:
            raise ValueError(f"source changed before scan: {path}")
        writer: Any = gaps if gap_format == "binary" else csv.writer(cast(TextIO, gaps))
        if gap_format == "csv":
            writer.writerow(GAP_FIELDS)
        while raw := stream.read(24 * chunk_rows):
            digest.update(raw)
            rows = np.frombuffer(raw, dtype=dtype, count=len(raw) // 24)
            if not len(rows):
                continue
            stamps, prices = rows["timestamp"], rows["prices"]
            out_of_range = (stamps <= 0) | (stamps > MAX_TIMESTAMP)
            off_minute = stamps % MINUTE != 0
            future = stamps > audit_ms
            finite = np.isfinite(prices).all(axis=1)
            nonpositive = (prices <= 0).any(axis=1)
            invalid_ohlc = finite & (
                (prices[:, 2] > prices[:, 0])
                | (prices[:, 2] > prices[:, 3])
                | (prices[:, 1] < prices[:, 0])
                | (prices[:, 1] < prices[:, 3])
                | (prices[:, 2] > prices[:, 1])
            )
            invalid = out_of_range | off_minute | future | ~finite | nonpositive | invalid_ohlc
            masks = {
                "timestamp_out_of_range_rows": out_of_range,
                "timestamp_off_minute_rows": off_minute,
                "timestamp_after_audit_rows": future,
                "nonfinite_price_rows": ~finite,
                "nonpositive_price_rows": nonpositive,
                "invalid_ohlc_rows": invalid_ohlc,
                "invalid_value_rows": invalid,
            }
            for key, mask in masks.items():
                result["error_counts"][key] += int(np.count_nonzero(mask))
                if key not in result["error_examples"] and np.any(mask):
                    index = int(np.flatnonzero(mask)[0])
                    result["error_examples"][key] = {
                        "record_1_based": result["complete_records"] + index + 1,
                        "timestamp_ms": int(stamps[index]),
                        "ohlc_repr": [str(float(value)) for value in prices[index]],
                    }
            result["error_counts"]["duplicate_adjacent_rows"] += int(
                np.count_nonzero(stamps[1:] == stamps[:-1])
            ) + int(previous is not None and previous == int(stamps[0]))
            result["error_counts"]["descending_adjacent_rows"] += int(
                np.count_nonzero(stamps[1:] < stamps[:-1])
            ) + int(previous is not None and previous > int(stamps[0]))
            if result["first_record_ms"] is None:
                result["first_record_ms"] = int(stamps[0])
            previous = result["last_record_ms"] = int(stamps[-1])
            result["flat_ohlc_rows"] += int(
                np.count_nonzero(
                    finite
                    & (prices[:, 0] == prices[:, 1])
                    & (prices[:, 1] == prices[:, 2])
                    & (prices[:, 2] == prices[:, 3])
                    & ~nonpositive
                )
            )
            valid = stamps[~(out_of_range | off_minute)]
            result["valid_timestamp_rows"] += len(valid)
            if len(valid):
                minimum, maximum = int(valid.min()), int(valid.max())
                old_min, old_max = (
                    result["first_valid_timestamp_ms"],
                    result["last_valid_timestamp_ms"],
                )
                result["first_valid_timestamp_ms"] = (
                    minimum if old_min is None else min(old_min, minimum)
                )
                result["last_valid_timestamp_ms"] = (
                    maximum if old_max is None else max(old_max, maximum)
                )
                if previous_valid is not None:
                    _gap(writer, result, previous_valid, int(valid[0]))
                indexes = np.flatnonzero(valid[1:] - valid[:-1] > MINUTE)
                if gap_format == "binary" and len(indexes):
                    starts, ends = valid[indexes], valid[indexes + 1]
                    missing = (ends - starts) // MINUTE - 1
                    result["gap_count"] += len(indexes)
                    result["missing_minutes"] += int(missing.sum())
                    largest = int(missing.argmax())
                    if int(missing[largest]) > result["largest_gap_minutes"]:
                        result["largest_gap_minutes"] = int(missing[largest])
                        result["largest_gap_start_utc"] = _utc(int(starts[largest]) + MINUTE)
                        result["largest_gap_end_utc_exclusive"] = _utc(int(ends[largest]))
                    writer.write(
                        np.column_stack((starts, ends)).astype("<i8", copy=False).tobytes()
                    )
                elif gap_format == "csv":
                    for index in indexes:
                        _gap(writer, result, int(valid[index]), int(valid[index + 1]))
                previous_valid = int(valid[-1])
            result["complete_records"] += len(rows)
        result["source_unchanged"] = (
            _identity(os.fstat(stream.fileno())) == result["source_identity"]
        )
    result["sha256"] = digest.hexdigest()
    result["duplicate_timestamp_rows"] = result["error_counts"]["duplicate_adjacent_rows"]
    result["unique_valid_timestamp_rows"] = (
        result["valid_timestamp_rows"] - result["duplicate_timestamp_rows"]
    )
    # A malformed timestamp can duplicate an invalid value, so use the exact index
    # fallback for that uncommon case too. Gap counts only concern valid grid stamps.
    if (
        result["error_counts"]["descending_adjacent_rows"]
        or result["error_counts"]["timestamp_out_of_range_rows"]
        or result["error_counts"]["timestamp_off_minute_rows"]
    ):
        _ordered_gap_recheck(path, gap_path, result)
    result["source_unchanged"] &= _identity(path.lstat()) == result["source_identity"]
    if not result["source_unchanged"]:
        result["structural_errors"].append("source_changed_during_audit")
    result["first_utc"] = _utc(result["first_valid_timestamp_ms"])
    result["last_utc"] = _utc(result["last_valid_timestamp_ms"])
    unique = result["unique_valid_timestamp_rows"]
    result["span_minutes"] = unique + result["missing_minutes"]
    result["coverage_pct"] = 100 * unique / result["span_minutes"] if unique else None
    result["flat_ohlc_pct"] = (
        100 * result["flat_ohlc_rows"] / result["complete_records"]
        if result["complete_records"]
        else None
    )
    result["classification"] = (
        "INVALID"
        if result["structural_errors"] or any(result["error_counts"].values())
        else "VALID_WITH_GAPS"
        if result["gap_count"]
        else "VALID_CONTIGUOUS"
    )
    result["gap_artifact_bytes"] = gap_path.stat().st_size
    result["gap_artifact_sha256"] = hash_file(gap_path)
    return result


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def export_gaps(path: Path, output: Path) -> int:
    """Decode compact gaps with bounded memory; never overwrite files or datasets."""
    if Path("/opt/datasets") in output.resolve().parents or output.resolve() == path.resolve():
        raise ValueError("export must be outside the archive and source file")
    if path.stat().st_size % 16:
        raise ValueError("truncated compact gap record")
    name = path.name.removesuffix(".gaps.bin")
    fields = Path(name).stem.split("_")
    count = 0
    with (
        path.open("rb") as source,
        output.open("x", newline="", encoding="utf-8") as target,
    ):
        writer = csv.writer(target)
        writer.writerow(GAP_FIELDS)
        while block := source.read(16 * CHUNK_ROWS):
            for previous, following in struct.iter_unpack("<qq", block):
                if (
                    not (0 < previous < following <= MAX_TIMESTAMP)
                    or previous % MINUTE
                    or following % MINUTE
                    or following - previous <= MINUTE
                ):
                    raise ValueError("invalid compact gap boundaries")
                writer.writerow(
                    (
                        name,
                        ALIASES.get(fields[0], fields[0]),
                        f"{fields[2]}/{fields[1]}" if len(fields) == 3 else "",
                        previous,
                        following,
                        _utc(previous + MINUTE),
                        _utc(following),
                        (following - previous) // MINUTE - 1,
                    )
                )
                count += 1
    return count


def _checkpoint(resume: Path, roster: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if json.loads((resume / "inventory_before.json").read_text()) != roster:
        raise ValueError("checkpoint inventory differs from current source identities")
    by_name = {item["file"]: item for item in roster}
    completed = {}
    for line in (resume / "files.jsonl").read_bytes().splitlines(keepends=True):
        if not line.endswith(b"\n"):
            continue  # Retained incomplete last write is not completed evidence.
        item = json.loads(line)
        if (
            item["file"] in completed
            or item["source_identity"] != by_name[item["file"]]["identity"]
            or not item.get("source_unchanged")
            or not item.get("sha256")
        ):
            raise ValueError("unusable or duplicate checkpoint source evidence")
        item["gap_format"] = item.get("gap_format", "csv")
        item["gap_artifact"] = item.get(
            "gap_artifact", str(resume / "gaps" / (item["file"] + ".gaps.csv"))
        )
        artifact = Path(item["gap_artifact"])
        item["gap_artifact_bytes"] = artifact.stat().st_size
        item["gap_artifact_sha256"] = hash_file(artifact)
        item["checkpoint_origin"] = str(resume)
        completed[item["file"]] = item
    return completed


def run(root: Path, output: Path, *, resume: Path | None = None) -> dict[str, Any]:
    root, output = root.resolve(strict=True), output.resolve()
    if root == output or root in output.parents or Path("/opt/datasets") in output.parents:
        raise ValueError("audit outputs must be outside the original archive")
    output.mkdir(parents=True, exist_ok=False)
    (output / "gaps").mkdir()
    started = datetime.now(UTC)
    audit_ms = int(started.timestamp() * 1000)
    wall, cpu = time.monotonic(), time.process_time()
    roster = _roster(root)
    _json(output / "inventory_before.json", roster)
    completed = _checkpoint(resume.resolve(strict=True), roster) if resume else {}
    _json(
        output / "checkpoint_reuse.json",
        {
            "origin": str(resume) if resume else None,
            "files_reused": len(completed),
            "source_inventory_identity_match": True,
        },
    )
    print(json.dumps({"checkpoint_files_verified": len(completed)}), flush=True)
    results = []
    with (output / "files.jsonl").open("x", encoding="utf-8") as stream:
        for index, source in enumerate(roster, 1):
            try:
                result = completed.get(source["file"])
                if result is None:
                    result = scan_file(root / source["file"], output / "gaps", audit_ms=audit_ms)
                if result["source_identity"] != source["identity"]:
                    result["structural_errors"].append("source_changed_since_roster")
                    result["source_unchanged"] = False
                    result["classification"] = "INVALID"
            except (OSError, ValueError, struct.error) as error:
                venue = source["file"].partition("_")[0]
                result = {
                    **source,
                    "venue": ALIASES.get(venue, venue),
                    "quote": "UNKNOWN",
                    "classification": "UNREADABLE",
                    "error": str(error),
                }
            results.append(result)
            stream.write(json.dumps(result, sort_keys=True, allow_nan=False) + "\n")
            stream.flush()
            if index % 25 == 0 or index == len(roster):
                print(
                    json.dumps(
                        {
                            "files_completed": index,
                            "files_total": len(roster),
                            "elapsed_seconds": round(time.monotonic() - wall, 1),
                            "file": source["file"],
                        }
                    ),
                    flush=True,
                )
    after = _roster(root)
    _json(output / "inventory_after.json", after)
    fields = [
        "file",
        "venue",
        "market",
        "quote",
        "classification",
        "bytes",
        "complete_records",
        "trailing_bytes",
        "first_utc",
        "last_utc",
        "span_minutes",
        "coverage_pct",
        "gap_count",
        "missing_minutes",
        "largest_gap_minutes",
        "largest_gap_start_utc",
        "largest_gap_end_utc_exclusive",
        "flat_ohlc_rows",
        "flat_ohlc_pct",
        "duplicate_timestamp_rows",
        "suspect_leveraged_name",
        "source_unchanged",
        "sha256",
        "gap_format",
        "gap_artifact",
        "gap_artifact_bytes",
        "gap_artifact_sha256",
        *ERROR_KEYS,
        "structural_errors",
    ]
    with (output / "files.csv").open("x", newline="", encoding="utf-8") as csv_stream:
        writer = csv.DictWriter(csv_stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for result in results:
            row = {**result, **result.get("error_counts", {})}
            row["structural_errors"] = ";".join(result.get("structural_errors", []))
            writer.writerow(row)
    totals: dict[str, Any] = {}
    for venue in sorted(VENUES):
        group = [item for item in results if item.get("venue") == venue]
        totals[venue] = {
            "files": len(group),
            "classifications": dict(Counter(item["classification"] for item in group)),
            **{
                key: sum(item.get(key, 0) for item in group)
                for key in (
                    "bytes",
                    "complete_records",
                    "gap_count",
                    "missing_minutes",
                    "flat_ohlc_rows",
                    "duplicate_timestamp_rows",
                    "suspect_leveraged_name",
                )
            },
            "error_counts": {
                key: sum(item.get("error_counts", {}).get(key, 0) for item in group)
                for key in ERROR_KEYS
            },
            "quotes": dict(Counter(item.get("quote") for item in group)),
            "earliest_utc": min(
                (item["first_utc"] for item in group if item.get("first_utc")),
                default=None,
            ),
            "latest_utc": max(
                (item["last_utc"] for item in group if item.get("last_utc")),
                default=None,
            ),
        }
    summary = {
        "schema": "htx-gate-archive-integrity-v2",
        "root": str(root),
        "checkpoint_origin": str(resume) if resume else None,
        "checkpoint_files_reused": len(completed),
        "gap_storage": "binary little-endian <qq previous/next UTC-ms (16 bytes per gap), "
        "or retained checkpoint CSV; see per-file gap_artifact/format/hash",
        "output": str(output),
        "started_utc": started.isoformat(),
        "completed_utc": datetime.now(UTC).isoformat(),
        "elapsed_seconds": time.monotonic() - wall,
        "cpu_seconds": time.process_time() - cpu,
        "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "python": platform.python_version(),
        "numpy": _numpy().__version__,
        "scanner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "files_in_inventory": len(roster),
        "files_in_results": len(results),
        "roster_unchanged": roster == after,
        "all_source_identities_unchanged": all(item.get("source_unchanged") for item in results),
        "all_sources_read": all(item.get("sha256") for item in results),
        "record_format": "little-endian <qffff: UTC milliseconds, float32 open/high/low/close",
        "gap_definition": "missing minute timestamps between min/max valid grid timestamps; "
        "invalid OHLC rows counted separately; no leading/trailing inference",
        "source_status": "UNVERIFIED PROVENANCE; structure cannot certify historical spot identity",
        "totals": totals,
    }
    _json(output / "summary.json", summary)
    _json(
        output / "artifact_hashes.json",
        {
            name: hashlib.sha256((output / name).read_bytes()).hexdigest()
            for name in (
                "summary.json",
                "files.csv",
                "files.jsonl",
                "inventory_before.json",
                "inventory_after.json",
            )
        },
    )
    print(json.dumps(summary, sort_keys=True), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--export-gaps", type=Path)
    parser.add_argument("--csv-output", type=Path)
    args = parser.parse_args()
    if args.export_gaps:
        if args.csv_output is None:
            parser.error("--export-gaps requires --csv-output")
        print(json.dumps({"gaps_exported": export_gaps(args.export_gaps, args.csv_output)}))
        return 0
    if args.root is None or args.output is None:
        parser.error("audit requires --root and --output")
    summary = run(args.root, args.output, resume=args.resume)
    return (
        0
        if all(
            summary[key]
            for key in (
                "roster_unchanged",
                "all_sources_read",
                "all_source_identities_unchanged",
            )
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
