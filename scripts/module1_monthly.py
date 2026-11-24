#!/usr/bin/env python3
"""Describe retained monthly NAV spans; never run or select a trading strategy."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext
from pathlib import Path
from typing import Any

DAY_MS = 86_400_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
VALUES = ("raw_nav", "net_nav", "buy_hold_raw_nav", "buy_hold_net_nav", "cash_benchmark")
METADATA = ("full_history", "entry", "ladder", "ohlc", "config_sha256", "result_sha256")
DISCLOSURE = (
    "Retrospective direction labels from funded buy/hold raw NAV, not a validated "
    "regime classifier, trading signal or parameter-selection rule. Each row uses "
    "the actual first/last retained observations within one UTC month; endpoints "
    "do not represent complete calendar-month boundaries. Capital continues from "
    "the original study without monthly resets. Unknown intermediate valuations "
    "are disclosed but do not alone suppress an endpoint-based label. Retained "
    "daily observations cannot establish intraday completeness. Monthly returns "
    "are not compounded or annualized: intervals between monthly endpoint pairs "
    "are omitted, and omission can exceed one overnight interval. Source identity, "
    "historical market rules and executions remain assumption-based."
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1_048_576):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> tuple[int, ...]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def amount(text: str) -> Decimal | None:
    if text == "":
        return None
    if len(text) > 256:
        raise ValueError("unbounded NAV value")
    result = Decimal(text)
    if not result.is_finite() or result < 0 or abs(result.adjusted()) > 60:
        raise ValueError("NAV must be a bounded nonnegative decimal or empty")
    return result


def decimal_text(value: Decimal) -> str:
    return "0" if not value else format(value, "f")


def timestamp(value: int) -> datetime:
    return EPOCH + timedelta(milliseconds=value)


def analyse(input_csv: Path, output: Path) -> dict[str, Any]:
    input_csv = input_csv.resolve(strict=True)
    if input_csv.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("NAV CSV exceeds 64 MiB")
    initial_stat = identity(input_csv)
    input_hash = sha256(input_csv)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    trial_metadata: dict[str, dict[str, str]] = {}
    latest: dict[str, int] = {}
    with input_csv.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"trial_id", "status", "timestamp_ms", *VALUES}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("missing retained NAV columns")
        for count, row in enumerate(reader, 1):
            if count > 200_000 or None in row or any(value is None for value in row.values()):
                raise ValueError("NAV row bound or malformed CSV")
            if row["status"] != "PASS" or not row["trial_id"]:
                raise ValueError("NAV rows must identify a passed retained trial")
            trial = row["trial_id"]
            now = int(row["timestamp_ms"])
            if now < 0 or (trial in latest and now <= latest[trial]):
                raise ValueError("trial timestamps must be strictly increasing")
            latest[trial] = now
            metadata = {key: row.get(key, "") for key in METADATA}
            if trial in trial_metadata and trial_metadata[trial] != metadata:
                raise ValueError("trial metadata changed between NAV samples")
            trial_metadata[trial] = metadata
            groups[(trial, timestamp(now).strftime("%Y-%m"))].append(
                {"timestamp_ms": now, **{key: amount(row[key]) for key in VALUES}}
            )
    if identity(input_csv) != initial_stat or sha256(input_csv) != input_hash:
        raise ValueError("NAV CSV changed during analysis")
    rows: list[dict[str, Any]] = []
    with localcontext() as context:
        context.prec = 96
        for (trial, month), points in sorted(groups.items()):
            first, last = points[0], points[-1]
            start, end = first["timestamp_ms"], last["timestamp_ms"]
            span = end - start
            days = {point["timestamp_ms"] // DAY_MS for point in points}
            beginning, ending = first["buy_hold_raw_nav"], last["buy_hold_raw_nav"]
            reasons = []
            if span < 20 * DAY_MS:
                reasons.append("insufficient_span_under_20_days")
            if beginning is None or ending is None:
                reasons.append("unknown_buy_hold_raw_endpoint")
            elif beginning <= 0:
                reasons.append("nonpositive_buy_hold_raw_beginning")
            label = "unclassified"
            if not reasons:
                change = ending - beginning
                threshold = beginning * Decimal("0.05")
                label = (
                    "rising" if change > threshold else "falling" if change < -threshold else "flat"
                )
            row = {
                "trial_id": trial,
                **trial_metadata[trial],
                "utc_month": month,
                "direction": label,
                "unclassified_reason": ";".join(reasons),
                "first_timestamp_ms": start,
                "last_timestamp_ms": end,
                "first_utc": timestamp(start).isoformat(),
                "last_utc": timestamp(end).isoformat(),
                "observed_span_ms": span,
                "observed_span_days": decimal_text(Decimal(span) / DAY_MS),
                "retained_observations": len(points),
                "retained_utc_days": len(days),
                "unsampled_utc_days_between_endpoints": end // DAY_MS
                - start // DAY_MS
                + 1
                - len(days),
                "max_retained_observation_gap_ms": max(
                    (
                        right["timestamp_ms"] - left["timestamp_ms"]
                        for left, right in zip(points, points[1:], strict=False)
                    ),
                    default=0,
                ),
                "partial_month_endpoint_sampling": True,
            }
            for name in VALUES:
                before, after = first[name], last[name]
                row[f"{name}_first"] = None if before is None else decimal_text(before)
                row[f"{name}_last"] = None if after is None else decimal_text(after)
                row[f"{name}_unknown_observations"] = sum(point[name] is None for point in points)
                row[f"{name}_change"] = (
                    decimal_text(after - before)
                    if before is not None and after is not None
                    else None
                )
                row[f"{name}_return"] = (
                    decimal_text(
                        ((after - before) / before).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)
                    )
                    if before is not None and before > 0 and after is not None
                    else None
                )
            rows.append(row)
    # The twelve portfolio variants must be described over identical endpoints.
    common_endpoints: dict[tuple[str, str], set[tuple[int, int]]] = defaultdict(set)
    for row in rows:
        if row["full_history"].lower() == "false":
            common_endpoints[(row["config_sha256"], row["utc_month"])].add(
                (row["first_timestamp_ms"], row["last_timestamp_ms"])
            )
    if any(len(values) != 1 for values in common_endpoints.values()):
        raise ValueError("portfolio cases have unmatched observed monthly endpoints")
    output = output.resolve()
    if output.is_relative_to(Path("/opt/datasets")) or any(
        (parent / ".git").exists() for parent in (output, *output.parents)
    ):
        raise ValueError("analysis outputs must remain outside source archive and Git")
    output.mkdir(parents=True, exist_ok=False)
    csv_path = output / "monthly.csv"
    with csv_path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(rows[0]) if rows else ["trial_id", "utc_month", "direction"]
        )
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "schema": "grid-module1-retrospective-monthly-v1",
        "input": str(input_csv),
        "input_sha256": input_hash,
        "script_sha256": sha256(Path(__file__)),
        "command": sys.argv,
        "monthly_csv_sha256": sha256(csv_path),
        "trial_count": len(trial_metadata),
        "monthly_span_count": len(rows),
        "direction_counts": dict(sorted(Counter(row["direction"] for row in rows).items())),
        "return_representation": (
            "Decimal precision 96, ratios truncated toward zero at 18 decimal places; "
            "exact endpoint NAV and change retained; labels use endpoint "
            "cross-multiplication, not rounded ratios"
        ),
        "disclosure": DISCLOSURE,
    }
    with (output / "summary.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = analyse(args.input, args.output)
    except (OSError, ValueError, ArithmeticError, OverflowError) as error:
        parser.exit(2, f"Monthly analysis refused: {error}\n")
    print(
        json.dumps(
            {key: summary[key] for key in ("trial_count", "monthly_span_count", "direction_counts")}
        )
    )


if __name__ == "__main__":
    main()
