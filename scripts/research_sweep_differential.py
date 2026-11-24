"""Exact, bounded old/new research-sweep comparison and invented fixture runner.

Read results without changing them. Financial strings are reduced by Fraction;
no tolerance or floating point conversion is used. Series rows are hashed per
configuration/scope in increasing bin order, so block interleaving is immaterial.
Only an explicit fixture invocation executes engines and creates new outputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import time
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

from scripts.research_sweep_reference import BASE_MS, VENUES, fixture_sources, matrix, verify_result

EXACT_FIELDS = frozenset(
    [
        "realized_gross",
        "unrealized_gross",
        "fees",
        "total_pnl",
        "spent",
        "open_value",
        "peak_spent",
        "return_pct",
        "max_drawdown",
        "open_qty",
        "last_price",
        "step_pct",
        "tp_pct",
        "price",
        "quantity",
        "cost",
        "fee",
        "pnl_first",
        "pnl_last",
        "pnl_min",
        "pnl_max",
        "spent_first",
        "spent_last",
        "spent_min",
        "spent_max",
    ]
)
SEMANTIC_FIELDS = (
    "schema",
    "status",
    "mode",
    "assumption_based",
    "certified_historical_performance",
    "source_count",
    "venue_order",
    "configuration_count",
    "input_records",
    "processed_records",
    "last_ms",
    "duration_stopped",
    "configurations",
)
AUDIT_FIELDS = ("bytes", "records", "gaps", "missing_minutes", "source_sha256_verified")
MAX_JSON_BYTES = 128 * 1024**2
MAX_ROW_BYTES = 1024**2


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _object(text: str | bytes) -> dict[str, Any]:
    value = json.loads(text)
    _require(isinstance(value, dict), "expected JSON object")
    return cast(dict[str, Any], value)


def _read(path: Path) -> dict[str, Any]:
    _require(path.is_file() and path.stat().st_size <= MAX_JSON_BYTES, f"missing/large {path}")
    return _object(path.read_bytes())


def _canonical(value: Any, field: str = "") -> Any:
    if field in EXACT_FIELDS and value is not None:
        _require(isinstance(value, str) and len(value) <= 16384, f"invalid exact {field}")
        number = Fraction(value)
        return f"{number.numerator}/{number.denominator}"
    if isinstance(value, dict):
        return {key: _canonical(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    _require(not isinstance(value, float), f"inexact JSON number in {field}")
    return value


def _encoded(value: Any) -> bytes:
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def result_directory(path: Path) -> Path:
    """Accept an engine directory, wrapper job directory, or summary.json."""
    if path.is_file():
        _require(path.name == "summary.json", "file input must be summary.json")
        path = path.parent
    if not (path / "summary.json").exists() and (path / "engine/summary.json").exists():
        path = path / "engine"
    _require((path / "summary.json").is_file(), f"missing completed summary: {path}")
    return path


def _rows(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("rb") as stream:
        while line := stream.readline(MAX_ROW_BYTES + 1):
            _require(len(line) <= MAX_ROW_BYTES and line.endswith(b"\n"), f"invalid row: {path}")
            yield _object(line)


def result_fingerprint(path: Path, *, include_charts: bool = True) -> dict[str, Any]:
    """Hash every financial field, identity, extremum date, graph row and trace.

    Memory is bounded by one pair roster plus configuration/scope hash states;
    histories and graph rows are never materialized. Group order may differ
    between blocks, but each scope's chronological order remains mandatory.
    """
    directory = result_directory(path)
    summary = _read(directory / "summary.json")
    chart_mode = summary.get("chart_mode", "eager")
    _require(chart_mode in ("eager", "on_demand"), "unknown chart mode")
    series_path = directory / "series.jsonl"
    if chart_mode == "on_demand":
        _require(summary.get("charts_available") is False, "on-demand chart availability mismatch")
        _require(
            not series_path.exists() or series_path.stat().st_size == 0,
            "on-demand sweep unexpectedly contains chart rows",
        )
    _require(summary.get("status") in {"COMPLETE", "COMPLETE_DIAGNOSTIC"}, "incomplete result")
    semantic = {key: summary[key] for key in SEMANTIC_FIELDS}
    semantic["audit"] = {key: summary["audit"][key] for key in AUDIT_FIELDS}
    configs = [row["id"] for row in summary["configurations"]]
    _require(
        len(configs) == len(set(configs)) == summary["configuration_count"] > 0,
        "duplicate or missing configuration",
    )
    venues = summary["venue_order"]
    _require(len(venues) == len(set(venues)) > 0, "invalid venue roster")
    for config in summary["configurations"]:
        _require([row["venue"] for row in config["venues"]] == venues, "venue results incomplete")
    source_count = summary["source_count"]
    _require(type(source_count) is int and 0 < source_count <= 10000, "invalid source count")
    pairs_hash = hashlib.sha256()
    pair_rows = 0
    roster: list[str] = []
    seen: set[str] = set()
    for row in _rows(directory / "pairs.jsonl"):
        config_index, source_index = divmod(pair_rows, source_count)
        _require(
            config_index < len(configs) and row["config_id"] == configs[config_index],
            "pair configuration order/coverage mismatch",
        )
        if config_index == 0:
            _require(row["source_id"] not in seen, "duplicate pair")
            roster.append(row["source_id"])
            seen.add(row["source_id"])
        else:
            _require(row["source_id"] == roster[source_index], "pair roster mismatch")
        pairs_hash.update(_encoded(row))
        pair_rows += 1
    _require(pair_rows == len(configs) * source_count, "pair coverage incomplete")
    config_set = set(configs)
    # Each small state is (hash, last bin, row count); no graph point is retained.
    groups: dict[tuple[str, int], tuple[Any, int, int]] = {}
    series_rows = 0
    series = _rows(series_path) if include_charts and chart_mode == "eager" else iter(())
    for row in series:
        key = row["config_id"], row["scope_index"]
        _require(
            key[0] in config_set and type(key[1]) is int and 0 <= key[1] <= len(venues),
            "unexpected graph scope",
        )
        scope = "all" if key[1] == 0 else "venue:" + venues[key[1] - 1]
        _require(row["scope"] == scope, "graph scope name mismatch")
        digest, previous, count = groups.get(key, (hashlib.sha256(), -1, 0))
        _require(
            type(row["bin"]) is int and row["bin"] > previous,
            "duplicate or nonchronological graph bin",
        )
        digest.update(_encoded(row))
        groups[key] = digest, row["bin"], count + 1
        series_rows += 1
    _require(
        not include_charts
        or chart_mode == "on_demand"
        or set(groups)
        == {(config, scope) for config in configs for scope in range(len(venues) + 1)},
        "graph scope coverage incomplete",
    )
    series_hash = hashlib.sha256()
    for (config, scope), (digest, _last, count) in sorted(groups.items()):
        series_hash.update(_encoded([config, scope, count, digest.hexdigest()]))
    trades_path = directory / "trades.jsonl"
    trades_hash = None
    trade_rows = 0
    if trades_path.exists():
        trade_groups = {config: hashlib.sha256() for config in configs}
        sequences: dict[tuple[str, str], int] = {}
        for row in _rows(trades_path):
            key = row["config_id"], row["source_id"]
            _require(key[0] in config_set and key[1] in seen, "unexpected trade identity")
            _require(row["sequence"] == sequences.get(key, 0) + 1, "trade sequence mismatch")
            sequences[key] = row["sequence"]
            trade_groups[key[0]].update(_encoded(row))
            trade_rows += 1
            _require(trade_rows <= 100000, "trade trace outside engine validation bound")
        digest = hashlib.sha256()
        for config, group in sorted(trade_groups.items()):
            digest.update(_encoded([config, group.hexdigest()]))
        trades_hash = digest.hexdigest()
    return {
        "schema": "grid-research-sweep-exact-fingerprint-v1",
        "summary_sha256": hashlib.sha256(_encoded(semantic)).hexdigest(),
        "pairs_sha256": pairs_hash.hexdigest(),
        "series_sha256": series_hash.hexdigest(),
        "trades_sha256": trades_hash,
        "pair_rows": pair_rows,
        "series_rows": series_rows,
        "trade_rows": trade_rows,
        "configuration_count": len(configs),
    }


def compare_results(
    baseline: Path, candidate: Path, *, include_charts: bool = True
) -> dict[str, Any]:
    old = result_fingerprint(baseline, include_charts=include_charts)
    new = result_fingerprint(candidate, include_charts=include_charts)
    differing = [key for key in old if old[key] != new[key]]
    _require(not differing, "exact differential mismatch: " + ", ".join(differing))
    return {"status": "PASS", "fingerprint": old}


def edge_sources(directory: Path) -> list[dict[str, Any]]:
    """Small invented streams covering no-trade branches and numeric extremes."""
    directory.mkdir(parents=True, exist_ok=False)
    tiny = struct.unpack("<f", struct.pack("<I", 1))[0]
    largest = struct.unpack("<f", struct.pack("<I", 0x7F7FFFFF))[0]
    cases: list[tuple[str, list[float], dict[str, Any]]] = [
        ("equal", [100, 99, 110, 111, 111, 120, 121, 119], {}),
        ("flat", [100] * 8, {}),
        ("deep", [100, 35, 35, 40, 90, 130, 130, 20], {}),
        ("duplicates", [10, 9, 8, 3, 3, 4, 12, 12], {"price_tick": "1"}),
        (
            "min_cost_fine",
            [100, 98, 49, 35, 130, 100, 99],
            {"price_tick": "1", "amount_tick": "1", "min_cost": "100.000000000000001"},
        ),
        (
            "min_cost_exact",
            [100, 98, 49, 35, 130, 100, 99],
            {"price_tick": "1", "amount_tick": "1", "min_cost": "100"},
        ),
        ("subnormal", [tiny, tiny * 2, tiny, tiny * 4], {"price_tick": "1e-45"}),
        ("maximum", [largest, largest / 2, largest], {"min_amount": "1e40"}),
        (
            "significant",
            [99999, 100001, 999.99, 100001],
            {"price_mode": "significant", "price_digits": 5, "max_price_decimals": 8},
        ),
        (
            "significant_tiny",
            [tiny, 1e-8, 2e-8, 1e-8],
            {"price_mode": "significant", "price_digits": 5, "max_price_decimals": 8},
        ),
    ]
    sources = []
    for index, (name, values, overrides) in enumerate(cases):
        venue = "bitfinex" if name.startswith("significant") else VENUES[index % len(VENUES)]
        first = BASE_MS + index % 3 * 60000
        timestamps = [
            first + minute * 60000 + (3600000 if minute >= 3 else 0)
            for minute in range(len(values))
        ]
        payload = b"".join(
            struct.pack("<qffff", stamp, price, price, price, price)
            for stamp, price in zip(timestamps, values, strict=True)
        )
        path = directory / f"{name}.bin"
        path.write_bytes(payload)
        sources.append(
            {
                "id": f"{venue}:{name}/USDT",
                "venue": venue,
                "symbol": f"{name}/USDT",
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "records": len(values),
                "first_ms": first,
                "last_ms": timestamps[-1],
                "rules": {
                    "price_mode": "tick",
                    "price_tick": "0.01",
                    "amount_tick": "1e-8",
                    "min_amount": "1",
                    "min_cost": "0",
                    **overrides,
                },
            }
        )
    return sources


def run_fixtures(baseline: Path, candidate: Path, output: Path) -> dict[str, Any]:
    """Run bounded fixtures against each binary and the independent Fraction oracle."""
    from scripts.research_sweep import HeavyJobLock, make_plan

    baseline, candidate, output = baseline.resolve(), candidate.resolve(), output.resolve()
    with HeavyJobLock() as lock:
        output.mkdir(parents=True, exist_ok=False)
        normal = fixture_sources(output / "normal-inputs", minutes=24)
        normal[1]["rules"]["price_tick"] = "1"
        normal[-1]["rules"].update(price_mode="significant", price_digits=5, max_price_decimals=8)
        short = fixture_sources(output / "matrix-inputs", minutes=4)
        configs = [
            {"id": "ordinary", "step_pct": "1", "tp_pct": "10"},
            {"id": "rounding", "step_pct": "1.5", "tp_pct": "2"},
            {"id": "deep", "step_pct": "10", "tp_pct": "15"},
        ]
        cases = [
            ("normal", normal, configs),
            ("matrix", short, matrix()),
            ("extremes", edge_sources(output / "edge-inputs"), configs),
            ("block-boundaries", fixture_sources(output / "boundary-inputs", minutes=96), configs),
        ]
        checks = []
        for name, sources, selected in cases:
            old_dir = output / name / "baseline" / "engine"
            for label, binary, threads, block in (
                ("baseline", baseline, 1, 4096),
                ("candidate-serial", candidate, 1, 4096),
                ("candidate-parallel", candidate, 32, 1024**2),
                ("candidate-dynamic", candidate, 32, 16 * 1024**2),
            ):
                run = output / name / label
                run.mkdir(parents=True)
                plan = make_plan(
                    sources,
                    run / "engine",
                    "validation",
                    threads=threads,
                    block_bytes=block,
                    configurations=selected,
                )
                plan.update(trace=True, checkpoint_seconds=0, chart_mode="eager")
                if label == "candidate-dynamic":
                    plan.update(
                        scheduler="dynamic",
                        max_timestamp_groups=4096,
                        warmup_until_ms=BASE_MS + (32 if name == "block-boundaries" else 2) * 60000,
                    )
                plan_path = run / "plan.json"
                plan_path.write_text(json.dumps(plan, sort_keys=True) + "\n")
                began = time.monotonic()
                with (
                    (run / "stdout.log").open("wb") as stdout,
                    (run / "stderr.log").open("wb") as stderr,
                ):
                    completed = subprocess.run(
                        [str(binary), "research-sweep", str(plan_path)],
                        stdout=stdout,
                        stderr=stderr,
                        timeout=900,
                        pass_fds=(lock.fileno(),),
                        check=False,
                    )
                _require(completed.returncode == 0, f"fixture failed: {run}/stderr.log")
                if label == "candidate-dynamic":
                    summary = _read(run / "engine/summary.json")
                    _require(
                        summary["resources"].get("scheduler") == "dynamic"
                        and summary["resources"].get("max_timestamp_groups_per_block") == 4096,
                        "candidate did not enable dynamic scheduling and enlarged timestamp blocks",
                    )
                    _require(
                        0 < summary["measured_records"] < summary["processed_records"],
                        "candidate did not process both warmup and measured records",
                    )
                    if name == "block-boundaries":
                        _require(
                            summary["processed_blocks"] == 2,
                            "enlarged blocks did not cross the old 64-timestamp limit",
                        )
                checks.append(
                    {
                        "case": name,
                        "engine": label,
                        "threads": threads,
                        "wall_seconds": time.monotonic() - began,
                        "oracle": verify_result(plan, run / "engine"),
                        **compare_results(old_dir, run / "engine"),
                    }
                )
        report = {
            "schema": "grid-research-sweep-differential-v1",
            "status": "PASS",
            "baseline_binary_sha256": _sha(baseline),
            "candidate_binary_sha256": _sha(candidate),
            "checks": checks,
        }
        (output / "receipt.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--financial-only", action="store_true")
    fingerprint = commands.add_parser("fingerprint")
    fingerprint.add_argument("result", type=Path)
    fixtures = commands.add_parser("fixtures")
    fixtures.add_argument("--baseline", type=Path, required=True)
    fixtures.add_argument("--candidate", type=Path, required=True)
    fixtures.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "fixtures":
        result = run_fixtures(args.baseline, args.candidate, args.output)
    elif args.action == "compare":
        result = compare_results(
            args.baseline, args.candidate, include_charts=not args.financial_only
        )
    else:
        result = result_fingerprint(args.result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
