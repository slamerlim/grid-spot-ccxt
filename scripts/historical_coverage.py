"""Independent source verification and minute coverage; no accounting or replay.

Counts refer to declared minute grids, not executable fills. Conversion routes
require contemporaneous completed candles; stale ages are diagnostics only.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import struct
import tempfile
from collections import Counter, OrderedDict
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts import historical_collect as hc


def percentage(numerator: int, denominator: int) -> str:
    return str((Decimal(numerator) * 100 / denominator).quantize(Decimal("0.000000001")))


def normalize_order(root: Path, entry: dict[str, Any], kind: str) -> dict[str, Any]:
    """Lossless row reorder of NEW external files; never deduplicate or change values."""
    original = hc.artifact_dir(root, entry, kind)
    receipt = hc.verify_artifact(original, entry, kind)
    destination = root / "derived" / entry["exchange"] / entry["market"].replace("/", "_")
    destination = destination / entry["month"] / kind
    if destination.exists():
        saved: dict[str, Any] = json.loads((destination / "provenance.json").read_text())
        if (
            hc.sha256(destination / "chronological.csv") != saved["sha256"]
            or hc.sha256(original / "original.csv") != saved["source_sha256"]
        ):
            raise ValueError("Normalized derivative checksum changed")
        return saved
    with (original / "original.csv").open(newline="") as stream:
        rows: list[list[str]] = []
        for row in csv.reader(stream):
            if len(rows) >= (44_641 if kind == "candles" else 100_000):
                raise ValueError("Normalization row budget exceeded")
            rows.append(row)
    # Rows retain all original fields and their decimal strings. Equal-time
    # trades keep their source-file order; this is not proven execution order.
    header = []
    if rows and rows[0][0].lower() in {"id", "timestamp", "time", "instid"}:
        header = rows.pop(0)
    column = 1 if kind == "trades" and entry["exchange"] == "htx" else 0
    if header and header[0] == "instId":
        column = 8 if kind == "candles" else 5
    rows.sort(key=lambda row: hc.decimal(row[column], positive=True))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".normalize-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        normalized = stage / "chronological.csv"
        with normalized.open("x", newline="") as stream:
            if header:
                csv.writer(stream).writerow(header)
            csv.writer(stream).writerows(rows)
        result = hc.validate_file(normalized, entry, kind)
        result.update(
            {
                "source_file": str(original / "original.csv"),
                "source_sha256": hc.sha256(original / "original.csv"),
                "original_admission": receipt["status"],
                "rows_retained": len(rows),
                "rows_added": 0,
                "rows_removed": 0,
                "values_changed": 0,
                "transformation": "Stable chronological row sort; no deduplication or resampling",
                "sha256": hc.sha256(normalized),
                "bytes": normalized.stat().st_size,
                "normalized_file": str(destination / "chronological.csv"),
            }
        )
        hc.save_new(stage / "provenance.json", result)
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(stage, destination)
        return result


def verify(root: Path, output: Path) -> list[dict[str, Any]]:
    """Rehash both compressed and extracted originals and reparse every new file."""
    rows = hc.checked_entries(root)
    result = []
    with output.open("x") as stream:
        for entry in rows:
            path = hc.artifact_dir(root, entry, "candles")
            if not path.exists():
                continue
            receipt = hc.verify_artifact(path, entry, "candles")
            record = {
                "exchange": entry["exchange"],
                "market": entry["market"],
                "month": entry["month"],
                "directory": str(path),
                "files": receipt["files"],
                "url": entry["url"],
                "source_identity": entry["source_identity"],
                "source_sha256": entry["source_sha256"],
                "parser": hc.VERSION,
            }
            try:
                record.update(hc.validate_file(path / "original.csv", entry, "candles"))
            except (ValueError, ArithmeticError, OSError, csv.Error) as exc:
                record.update(status=hc.INVALID, error=str(exc))
            record["agrees_with_initial_admission"] = record["status"] == receipt["status"]
            record["initial_parser"] = receipt["parser"]
            if not record["agrees_with_initial_admission"] and receipt["parser"] == hc.VERSION:
                raise ValueError("Independent admission changed; review before using data")
            record["original_status"] = record["status"]
            record["accepted_csv"] = str(path / "original.csv")
            if record["status"] == hc.INVALID:
                try:
                    normalized = normalize_order(root, entry, "candles")
                    # Independently reparse the published derivative as well.
                    recheck = hc.validate_file(
                        Path(normalized["normalized_file"]), entry, "candles"
                    )
                    record.update(recheck)
                    record["normalization"] = normalized
                    record["accepted_csv"] = normalized["normalized_file"]
                    record["original_error"] = record.pop("error", None)
                except (ValueError, ArithmeticError, OSError, csv.Error) as exc:
                    record["normalization_error"] = str(exc)
            stream.write(json.dumps(record) + "\n")
            stream.flush()
            result.append(record)
            if len(result) % 50 == 0:
                print(json.dumps({"independently_verified_files": len(result)}), flush=True)
    return result


class Sources:
    def __init__(
        self,
        root: Path,
        verification: Path,
        audit: Path,
        dataset_root: Path = Path("/opt/datasets/crypto_1m"),
    ) -> None:
        self.root = root
        self.dataset_root = dataset_root
        with hc.read_only(audit) as stream:
            self.audit = {(r["venue"], r["market"]): r for r in map(json.loads, stream)}
        self.external = {
            (r["exchange"], r["market"], r["month"]): r
            for r in map(json.loads, verification.read_text().splitlines())
        }
        self.opened: OrderedDict[tuple[str, str], hc.LocalArchive] = OrderedDict()
        self.masks: OrderedDict[tuple[str, str, int, int, bool], bytearray] = OrderedDict()
        self.provenance: dict[str, dict[str, Any]] = {}

    def close(self) -> None:
        for source in self.opened.values():
            source.close()
        self.opened.clear()

    def local(self, venue: str, market: str) -> hc.LocalArchive | None:
        key = venue, market
        if key not in self.audit:
            return None
        if key not in self.opened:
            record = self.audit[key]
            path = self.dataset_root / record["file"]
            self.opened[key] = hc.LocalArchive(path, record["source_identity"])
            self.provenance[f"{venue}:{market}"] = {
                "local_file": str(path),
                "sha256": record["sha256"],
                "audit_identity_verified": True,
                "first_observed_utc": record["first_utc"],
                "last_observed_utc": record["last_utc"],
                "price_precision": "original binary32; no recovered decimal precision claimed",
                "provenance": "Legacy collection provenance unverified; structurally audited",
                "external_sources": [
                    {
                        "month": item["month"],
                        "url": item["url"],
                        "accepted_csv": item["accepted_csv"],
                        "accepted_sha256": item.get("normalization", {}).get("sha256")
                        or item["files"]["original.csv"]["sha256"],
                        "normalized_order": "normalization" in item,
                    }
                    for item in self.external.values()
                    if item["status"] == hc.VALID and (item["exchange"], item["market"]) == key
                ],
            }
        self.opened.move_to_end(key)
        while len(self.opened) > 16:
            self.opened.popitem(last=False)[1].close()
        return self.opened[key]

    def mask(
        self, venue: str, market: str, start: int, end: int, completed: bool = True
    ) -> bytearray:
        """1=local observation, 2=new official observation, 0=unavailable.

        Completed-candle valuation at T reads exactly the candle opened at T-1m.
        An original conflict is never silently replaced or counted as accepted.
        """
        key = venue, market, start, end, completed
        if key in self.masks:
            self.masks.move_to_end(key)
            return self.masks[key]
        shift = hc.MINUTE if completed else 0
        result = bytearray((end - start) // hc.MINUTE)
        local = self.local(venue, market)
        if local is not None:
            left, right = local.lower(start - shift), local.lower(end - shift)
            for row in hc.BAR.iter_unpack(local.data[left * hc.BAR.size : right * hc.BAR.size]):
                result[(row[0] + shift - start) // hc.MINUTE] = 1
        for month, _, _ in hc.months(start - shift, end - shift):
            record = self.external.get((venue, market, month))
            if record is None or record["status"] != hc.VALID:
                continue
            if not record["new_minutes"] and not record["conflicting_minutes"]:
                continue
            for candle in hc.candles(Path(record["accepted_csv"]), venue, month):
                moment = candle.time_ms + shift
                if not start <= moment < end:
                    continue
                index = (moment - start) // hc.MINUTE
                if result[index]:
                    original = local.row(candle.time_ms) if local is not None else None
                    if original is not None and struct.pack("<4f", *original) != struct.pack(
                        "<4f", *[float(p) for p in candle.prices]
                    ):
                        result[index] = 0
                else:
                    result[index] = 2
        self.masks[key] = result
        while len(self.masks) > 128:
            self.masks.popitem(last=False)
        return result

    def routes(self, venue: str, asset: str) -> list[list[tuple[str, bool]]]:
        def edge(base: str, quote: str) -> list[tuple[str, bool]]:
            return [
                (market, inverse)
                for market, inverse in ((f"{base}/{quote}", False), (f"{quote}/{base}", True))
                if (venue, market) in self.audit
            ]

        result = [[x] for x in edge(asset, "USDT")]
        for bridge in ("BTC", "ETH", "USDC"):
            if bridge != asset:
                result.extend(
                    [
                        [first, second]
                        for first in edge(asset, bridge)
                        for second in edge(bridge, "USDT")
                    ]
                )
        return result


def route_record(venue: str, legs: list[tuple[str, bool]]) -> dict[str, Any]:
    return {
        "exchange": venue,
        "legs": [
            {
                "market": market,
                "direction": "inverse" if inverse else "direct",
                "source_catalog_key": f"{venue}:{market}",
            }
            for market, inverse in legs
        ],
        "formula": " * ".join(
            f"(1 / close({m}, T-1m))" if i else f"close({m}, T-1m)" for m, i in legs
        ),
        "classification": "derived" if len(legs) > 1 or legs[0][1] else "observed",
        "alignment": "All source candles opened at T-1m and completed at T, on the same venue",
        "listing_policy": "Require positive observations at each timestamp; no extrapolation",
        "precision": "Decimal calculation; original local binary32 limitations remain",
        "execution": "Valuation evidence only; not an executable cross-rate",
    }


def stale_distribution(gaps: Counter[int]) -> dict[str, Any]:
    total = sum(length * count for length, count in gaps.items())
    if not total:
        return {
            "minutes_with_a_previous_valid_mark": 0,
            "max_age_ms": None,
            "p50_age_ms": None,
            "p95_age_ms": None,
            "p99_age_ms": None,
        }
    maximum = max(gaps)
    result: dict[str, Any] = {
        "minutes_with_a_previous_valid_mark": total,
        "max_age_ms": maximum * hc.MINUTE,
    }
    for name, numerator in (("p50", 50), ("p95", 95), ("p99", 99)):
        rank = math.ceil(total * numerator / 100)
        low, high = 1, maximum
        while low < high:
            middle = (low + high) // 2
            if sum(min(length, middle) * count for length, count in gaps.items()) >= rank:
                high = middle
            else:
                low = middle + 1
        result[name + "_age_ms"] = low * hc.MINUTE
    return result


def valuation_report(sources: Sources, requirement: dict[str, Any]) -> dict[str, Any]:
    venue, quote = requirement["exchange"], requirement["quote"]
    start, end = requirement["start_ms"], requirement["end_ms"]
    routes = sources.routes(venue, quote)
    summaries: list[dict[str, Any]] = []
    route_totals = Counter[int]()
    gaps: Counter[int] = Counter()
    have_previous, gap = False, 0
    for month, left, right in hc.months(start, end):
        count = (right - left) // hc.MINUTE
        chosen = bytearray(count)
        local_only = bytearray(count)
        accepted = []
        for index, route in enumerate(routes):
            masks = [sources.mask(venue, market, left, right) for market, _ in route]
            direct = len(route) == 1 and not route[0][1]
            number = 0
            for position in range(count):
                if not local_only[position] and all(m[position] == 1 for m in masks):
                    local_only[position] = 1
                if not chosen[position] and all(m[position] for m in masks):
                    chosen[position] = 1 if direct else 2
                    number += 1
            accepted.append(number)
            route_totals[index] += number
        observed, derived = chosen.count(1), chosen.count(2)
        for flag in chosen:
            if flag:
                if gap:
                    gaps[gap] += 1
                    gap = 0
                have_previous = True
            elif have_previous:
                gap += 1
        summaries.append(
            {
                "month": month,
                "start_utc": hc.utc(left),
                "end_utc_exclusive": hc.utc(right),
                "denominator_minutes": count,
                "observed": observed,
                "derived": derived,
                "stale": 0,
                "approximate": 0,
                "unavailable": count - observed - derived,
                "local_routes_available_before_collection": sum(local_only),
                "available_pct": percentage(observed + derived, count),
                "assigned_minutes_by_route_index": accepted,
            }
        )
    if gap:
        gaps[gap] += 1
    keys = (
        "denominator_minutes",
        "observed",
        "derived",
        "stale",
        "approximate",
        "unavailable",
        "local_routes_available_before_collection",
    )
    total: dict[str, Any] = {key: sum(row[key] for row in summaries) for key in keys}
    available = total["observed"] + total["derived"]
    total["available_pct"] = percentage(available, total["denominator_minutes"])
    total["incremental_valuation_minutes"] = (
        available - total["local_routes_available_before_collection"]
    )
    return {
        "exchange": venue,
        "quote": quote,
        "start_utc": hc.utc(start),
        "end_utc_exclusive": hc.utc(end),
        "classification": "Derived from source data",
        "definition": "Minute grid of valuation timestamps; only the immediately completed candle",
        "freshness_limit": (
            "Observed means a retained candle close, not a certified fresh trade. "
            "Legacy files lack volume/provenance. Consult trade corroboration before claiming "
            "fresh-price coverage; no-trade flat bars need a separately reviewed stale-mark policy."
        ),
        "totals": total,
        "months": summaries,
        "routes": [
            {**route_record(venue, route), "assigned_minutes": route_totals[index]}
            for index, route in enumerate(routes)
        ],
        "stale_age_diagnostics_only": stale_distribution(gaps),
        "stale_policy": "Disabled; no stale rate or synthetic candle is created",
    }


def trade_corroboration(sources: Sources, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for entry in entries:
        directory = hc.artifact_dir(sources.root, entry, "trades")
        if not directory.exists():
            continue
        receipt = json.loads((directory / "receipt.json").read_text())
        trade_path = directory / "original.csv"
        admitted = receipt["status"]
        conflicts = receipt.get("conflicting_minutes")
        normalized = (
            sources.root
            / "derived"
            / entry["exchange"]
            / entry["market"].replace("/", "_")
            / entry["month"]
            / "trades"
            / "provenance.json"
        )
        if normalized.exists():
            normal = json.loads(normalized.read_text())
            admitted = normal["status"]
            trade_path = Path(normal["normalized_file"])
            conflicts = normal.get("conflicting_minutes")
        record: dict[str, Any] = {
            "exchange": entry["exchange"],
            "market": entry["market"],
            "month": entry["month"],
            "status": admitted,
            "source": str(directory / "original.csv"),
            "source_sha256": hc.sha256(directory / "original.csv"),
            "accepted_trade_file": str(trade_path),
            "accepted_trade_sha256": hc.sha256(trade_path),
        }
        if admitted != hc.VALID:
            record["limitation"] = "Invalid trade source; no completeness or no-trade inference"
            results.append(record)
            continue
        source_start, source_end = hc.source_period(receipt)
        start = max(entry["required_start_ms"], source_start)
        end = min(entry["required_end_ms"], source_end)
        if end <= start:
            continue
        observed = {
            t.minute_ms
            for t in hc.trades(trade_path, entry["exchange"], entry["month"], entry["market"])
            if start <= t.minute_ms < end
        }
        mask = sources.mask(entry["exchange"], entry["market"], start, end, completed=False)
        local = sources.local(entry["exchange"], entry["market"])
        external = sources.external.get((entry["exchange"], entry["market"], entry["month"]))
        external_prices = (
            {
                c.time_ms: c.prices
                for c in hc.candles(
                    Path(external["accepted_csv"]), entry["exchange"], entry["month"]
                )
            }
            if external and external["status"] == hc.VALID
            else {}
        )
        flat = nonflat = absent_without_trades = 0
        for i, exists in enumerate(mask):
            moment = start + i * hc.MINUTE
            if moment in observed:
                continue
            if not exists:
                absent_without_trades += 1
            else:
                prices: tuple[float | Decimal, ...] | None = (
                    local.row(moment) if local is not None else None
                )
                if prices is None:
                    prices = external_prices.get(moment)
                if prices is not None:
                    if len(set(prices)) == 1:
                        flat += 1
                    else:
                        nonflat += 1
        record.update(
            {
                "required_minutes": len(mask),
                "start_utc": hc.utc(start),
                "end_utc_exclusive": hc.utc(end),
                "positive_trade_minutes": len(observed),
                "candle_minutes": sum(bool(x) for x in mask),
                "flat_candle_minutes_without_trades_observed": flat,
                "nonflat_candle_minutes_without_trades_observed": nonflat,
                "absent_candle_minutes_without_trades_observed": absent_without_trades,
                "reconstructed_ohlc_conflicts": conflicts,
                "limitation": (
                    "No recorded trades does not prove a complete exchange feed. Flat bars without "
                    "trade corroboration cannot establish fresh-price coverage. "
                    "No stale policy enabled."
                ),
            }
        )
        results.append(record)
    return results


def trading_report(sources: Sources, row: dict[str, Any]) -> dict[str, Any]:
    venue, market = row["exchange"], row["market"]
    start, end = row["start_ms"], row["end_ms"]
    summaries = []
    for month, left, right in hc.months(start, end):
        mask = sources.mask(venue, market, left, right, completed=False)
        entry = {"exchange": venue, "market": market, "month": month}
        trade_dir = hc.artifact_dir(sources.root, entry, "trades")
        trade_minutes: set[int] = set()
        trade_source = False
        trade_start = trade_end = 0
        if trade_dir.exists():
            receipt = json.loads((trade_dir / "receipt.json").read_text())
            trade_start, trade_end = hc.source_period(receipt)
            trade_source = receipt["status"] == hc.VALID
            trade_path = trade_dir / "original.csv"
            normalized = (
                sources.root
                / "derived"
                / venue
                / market.replace("/", "_")
                / month
                / "trades"
                / "provenance.json"
            )
            if normalized.exists():
                normal = json.loads(normalized.read_text())
                trade_source = normal["status"] == hc.VALID
                trade_path = Path(normal["normalized_file"])
            if trade_source:
                for trade in hc.trades(trade_path, venue, month):
                    if left <= trade.minute_ms < right:
                        trade_minutes.add(trade.minute_ms)
        no_trades_in_source = 0
        if trade_source:
            no_trades_in_source = sum(
                not flag
                and trade_start <= left + i * hc.MINUTE < trade_end
                and left + i * hc.MINUTE not in trade_minutes
                for i, flag in enumerate(mask)
            )
        observed = sum(bool(v) for v in mask)
        summaries.append(
            {
                "month": month,
                "start_utc": hc.utc(left),
                "end_utc_exclusive": hc.utc(right),
                "expected_minutes": len(mask),
                "observed_candle_minutes": observed,
                "new_official_candle_minutes": mask.count(2),
                "absent_candle_timestamps": len(mask) - observed,
                "source_confirmed_trade_minutes": len(trade_minutes) if trade_source else None,
                "absent_candle_minutes_with_no_trades_observed_in_source": no_trades_in_source,
                "unresolved_absences_without_trade_source": len(mask)
                - observed
                - no_trades_in_source,
                "trade_archive_available": trade_source,
                "trade_source_start_utc": hc.utc(trade_start) if trade_source else None,
                "trade_source_end_utc_exclusive": hc.utc(trade_end) if trade_source else None,
                "source_completeness_certified": False,
                "candle_occupancy_pct": percentage(observed, len(mask)),
            }
        )
    return {
        "exchange": venue,
        "market": market,
        "classification": "Derived from source data",
        "start_utc": hc.utc(start),
        "end_utc_exclusive": hc.utc(end),
        "months": summaries,
        "totals": {
            key: sum(r[key] for r in summaries)
            for key in (
                "expected_minutes",
                "observed_candle_minutes",
                "new_official_candle_minutes",
                "absent_candle_timestamps",
                "absent_candle_minutes_with_no_trades_observed_in_source",
                "unresolved_absences_without_trade_source",
            )
        },
        "execution_evidence": (
            "OHLC plus explicitly listed trade months; consult the separate execution report "
            "for any diagnostic book snapshots. Exact fills remain unproven."
        ),
    }


def report(root: Path, verification: Path, output: Path) -> None:
    audit = Path("/opt/grid-runs/htx-gate-integrity-20261112-compact/files.jsonl")
    sources = Sources(root, verification, audit)
    requirements = json.loads((root / "requirements.json").read_text())
    results: dict[str, Any] = {
        "schema": "historical-data-coverage-v1",
        "parser": hc.VERSION,
        "valuation": [],
        "trading": [],
    }
    try:
        for r in requirements:
            if "quote" in r:
                results["valuation"].append(valuation_report(sources, r))
            if "trading" in r["purpose"]:
                # Conversion envelopes can be slightly wider than the actual native run.
                native = sources.audit[r["exchange"], r["market"]]
                trading = {
                    **r,
                    "start_ms": native["first_record_ms"],
                    "end_ms": native["last_record_ms"] + hc.MINUTE,
                }
                results["trading"].append(trading_report(sources, trading))
            print(json.dumps({"coverage_completed": r["exchange"] + ":" + r["market"]}), flush=True)
        # Relevant HTX conversion coverage, using its own venue sources exclusively.
        for quote, market in (("BTC", "ETH/BTC"), ("USDC", "ETH/USDC")):
            native = sources.audit["htx", market]
            results["valuation"].append(
                valuation_report(
                    sources,
                    {
                        "exchange": "htx",
                        "quote": quote,
                        "start_ms": native["first_record_ms"],
                        "end_ms": native["last_record_ms"] + hc.MINUTE,
                    },
                )
            )
        results["source_provenance"] = sources.provenance
        results["trade_corroboration"] = trade_corroboration(sources, hc.checked_entries(root))
        results["strict_fresh_trade_valuation_coverage"] = {
            "classification": "Unavailable for the full envelopes",
            "reason": (
                "Candle presence is quantified, but the legacy archive lacks provenance/volume. "
                "Fresh-trade corroboration is limited to explicitly listed trade sources. "
                "Candle-based percentages must not be presented as certified freshness."
            ),
        }
        book_report = root / "orderbook-inspection-final.json"
        results["execution"] = {
            "classification": "Unavailable for exact execution reconstruction",
            "orderbook_data_collected": book_report.exists(),
            "orderbook_inspection": json.loads(book_report.read_text())
            if book_report.exists()
            else None,
            "fills_queue_spread_latency": "Not established",
            "notice": "Candle/trade coverage does not prove order execution or source completeness",
        }
    finally:
        sources.close()
    hc.save_new(output, results)


def publish_manifest(root: Path, verification: Path, output: Path) -> None:
    verified = {
        (r["exchange"], r["market"], r["month"]): r
        for r in map(json.loads, verification.read_text().splitlines())
    }
    rows = hc.checked_entries(root)
    for row in rows:
        record = verified.get((row["exchange"], row["market"], row["month"]))
        if record is not None:
            row["initial_collection"] = row.pop("collection")
            row["validation"] = record
            row["status"] = record["status"]
            row["accepted_csv"] = record["accepted_csv"]
            row["volume_note"] = record["volume_unit_note"]
        trade_directory = hc.artifact_dir(root, row, "trades")
        if trade_directory.exists():
            trade_receipt = json.loads((trade_directory / "receipt.json").read_text())
            trade_entry = {
                **row,
                "url": trade_receipt["url"],
            }
            row["alternative_trade_source"] = hc.verify_artifact(
                trade_directory, trade_entry, "trades"
            )
            normalized = (
                root
                / "derived"
                / row["exchange"]
                / row["market"].replace("/", "_")
                / row["month"]
                / "trades"
                / "provenance.json"
            )
            if normalized.exists():
                row["alternative_trade_validation"] = json.loads(normalized.read_text())
    hc.save_new(output, rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "report", "manifest"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--verification", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "verify":
        verify(args.root, args.output)
    else:
        if args.verification is None:
            parser.error("--verification is required")
        if args.action == "manifest":
            publish_manifest(args.root, args.verification, args.output)
        else:
            report(args.root, args.verification, args.output)


if __name__ == "__main__":
    main()
