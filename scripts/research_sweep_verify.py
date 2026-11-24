"""Read-only, streaming publication audit for a completed GRID research sweep.

The calculation engine already hashes every source before and after replay. This
independent audit checks the published roster, every pair row, exact aggregate
accounting, and the end-of-history marks before a final report is released.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

RECORD = struct.Struct("<qffff")
HASH = re.compile(r"[0-9a-f]{64}\Z")
SUM_FIELDS = ("realized_gross", "fees", "spent", "open_value", "buy_count", "sell_count")
MONEY_FIELDS = SUM_FIELDS[:4]


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _read(path: Path) -> dict[str, Any]:
    _require(
        path.is_file() and path.stat().st_size <= 128 * 1024**2, f"missing or oversized: {path}"
    )
    value = json.loads(path.read_text())
    _require(isinstance(value, dict), f"expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fraction(value: Any) -> Fraction:
    _require(isinstance(value, str) and len(value) <= 4096, "invalid exact number")
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("invalid exact number") from error


def _check_metrics(row: dict[str, Any]) -> dict[str, Any]:
    # Parse each exact number once. The same parsed values feed validation and
    # both independent rollups; no floating-point arithmetic enters this audit.
    parsed = dict(row)
    for name in MONEY_FIELDS + ("unrealized_gross", "total_pnl", "peak_spent", "max_drawdown"):
        parsed[name] = _fraction(row[name])
    for name in ("buy_count", "sell_count", "longest_hold_ms"):
        _require(type(row[name]) is int and row[name] >= 0, f"invalid {name}")
    spent, value = parsed["spent"], parsed["open_value"]
    realized, fees = parsed["realized_gross"], parsed["fees"]
    peak = parsed["peak_spent"]
    _require(spent >= 0 and value >= 0 and fees >= 0 and peak >= spent, "invalid exposure")
    _require(parsed["max_drawdown"] >= 0, "invalid drawdown")
    _require(parsed["unrealized_gross"] == value - spent, "unrealized mismatch")
    pnl = realized + value - spent - fees
    _require(parsed["total_pnl"] == pnl, "net PnL mismatch")
    expected_return = pnl / peak * 100 if peak else None
    actual_return = row["return_pct"]
    _require(
        (actual_return is None and expected_return is None)
        or (actual_return is not None and _fraction(actual_return) == expected_return),
        "return percentage mismatch",
    )
    return parsed


def _last_close(source: dict[str, Any]) -> Fraction:
    path = Path(source["path"])
    _require(path.stat().st_size == source["bytes"], f"source size changed: {source['id']}")
    with path.open("rb") as stream:
        stream.seek(-RECORD.size, os.SEEK_END)
        timestamp, _open, _high, _low, close = RECORD.unpack(stream.read(RECORD.size))
    _require(timestamp == source["last_ms"], f"source end changed: {source['id']}")
    return Fraction.from_float(close)


def _rollup(rows: list[dict[str, Any]], expected: dict[str, Any]) -> None:
    parsed = _check_metrics(expected)
    for name in SUM_FIELDS:
        total = sum((row[name] for row in rows), Fraction() if name in MONEY_FIELDS else 0)
        _require(total == parsed[name], f"aggregate {name} mismatch")
    _require(
        max((row["longest_hold_ms"] for row in rows), default=0) == expected["longest_hold_ms"],
        "aggregate hold duration mismatch",
    )


def _verify_source_hash(source: dict[str, Any]) -> None:
    path = Path(source["path"])
    before = path.stat()
    _require(_sha256(path) == source["sha256"], f"source hash changed: {source['id']}")
    after = path.stat()

    def identity(st: os.stat_result) -> tuple[int, ...]:
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns

    _require(identity(before) == identity(after), f"source changed during audit: {source['id']}")


def verify_job(job: Path, selection: Path, *, rehash_sources: bool = False) -> dict[str, Any]:
    """Verify full-history publication without loading pair rows into memory."""
    began = time.monotonic()
    job, selection = job.resolve(), selection.resolve()
    receipt, manifest = _read(job / "receipt.json"), _read(selection)
    resumed = receipt.get("action") == "resume" and receipt.get("completed_stage") == "full"
    _require(
        (receipt.get("action") == "full" or resumed) and receipt.get("status") == "COMPLETE",
        "full job incomplete",
    )
    _require(
        manifest.get("status") == "READY" and len(manifest.get("sources", [])) > 0,
        "selection not ready",
    )
    selection_sha = _sha256(selection)
    _require(receipt.get("preparation_sha256") == selection_sha, "selection hash mismatch")
    admission, study = _read(job / "admission.json"), _read(job / "study-manifest.json")
    _require(admission.get("preparation_sha256") == selection_sha, "admission selection mismatch")
    _require(study.get("preparation_sha256") == selection_sha, "study selection mismatch")
    _require(study.get("binary_sha256") == receipt.get("binary_sha256"), "study binary mismatch")
    code_hash = hashlib.sha256(
        json.dumps(study["code_sha256"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    _require(receipt.get("code_sha256") == code_hash, "study implementation mismatch")
    policy = receipt.get("acceptance_policy")
    if policy is not None:
        _require(
            policy
            == {
                "id": "optimized-600s-v1",
                "endurance_seconds": 600,
                "minimum_checkpoints": 2,
                "recovery_required": True,
            },
            "unknown acceptance policy",
        )
        _require(
            admission.get("acceptance_policy") == policy
            and study.get("acceptance_policy") == policy,
            "acceptance policy binding mismatch",
        )
    for stage in ("validate", "calibrate", "endurance", "rehearsal"):
        evidence = receipt.get("evidence", {}).get(stage)
        _require(isinstance(evidence, dict), f"missing {stage} evidence")
        path = Path(evidence["receipt"])
        _require(_sha256(path) == evidence["sha256"], f"changed {stage} evidence")
        proof = _read(path)
        _require(
            proof.get("status") in ("PASS", "COMPLETE")
            and proof.get("completed_stage", proof.get("action")) == stage
            and proof.get("preparation_sha256") == selection_sha
            and proof.get("binary_sha256") == receipt.get("binary_sha256")
            and proof.get("code_sha256") == code_hash,
            f"incompatible {stage} evidence",
        )
        if policy is not None:
            _require(proof.get("acceptance_policy") == policy, f"incompatible {stage} policy")
            if stage == "validate":
                recovery = proof.get("recovery", {})
                _require(
                    recovery.get("passed") is True
                    and recovery.get("checks")
                    and all(check.get("passed") is True for check in recovery["checks"]),
                    "missing successful recovery evidence",
                )
            if stage == "endurance":
                stage_summary_path = Path(proof["result_dir"]) / "summary.json"
                stage_summary = _read(stage_summary_path)
                stage_execution = _read(path.parent / "execution.json")
                _require(
                    stage_execution.get("exit_code") == 0
                    and stage_execution.get("summary_sha256") == _sha256(stage_summary_path),
                    "endurance execution binding mismatch",
                )
                _require(
                    stage_summary.get("timings", {}).get("replay_seconds", 0) >= 600
                    and stage_summary.get("checkpoint_count", 0) >= 2,
                    "endurance duration/checkpoints incomplete",
                )
    result_dir = Path(receipt["result_dir"]).resolve()
    _require(result_dir == job / "engine", "unexpected result directory")
    plan = _read(job / "plan.json")
    if resumed:
        _require(plan.get("resume") is True, "resumed full plan mismatch")
    else:
        engine_plan = _read(result_dir / "plan.json")
        _require(plan == engine_plan and not plan.get("resume", False), "fresh full plan mismatch")
    _require(plan.get("mode") == "full", "full plan mismatch")
    _require(plan.get("sources") == manifest["sources"], "full roster differs from selection")
    _require(
        not any(key in plan for key in ("start_ms", "end_ms", "max_records", "duration_seconds")),
        "full plan was bounded",
    )
    sources, configs = plan["sources"], plan["configurations"]
    _require(len(sources) > 0, "empty full roster")
    expected_matrix = [
        (f"s{s:02d}-t{t:02d}", Fraction(s, 2), Fraction(t, 2))
        for s in range(1, 21)
        for t in range(4, 31)
    ]
    actual_matrix = [
        (item["id"], _fraction(item["step_pct"]), _fraction(item["tp_pct"])) for item in configs
    ]
    _require(
        actual_matrix == expected_matrix and study["configurations"] == configs,
        "configuration matrix incomplete",
    )
    _require(
        {source["venue"] for source in sources}
        == {"okx", "htx", "gate", "xt", "bingx", "kucoin", "bitfinex"},
        "venue roster incomplete",
    )
    summary_path = result_dir / "summary.json"
    summary, execution = _read(summary_path), _read(job / "execution.json")
    _require(
        execution.get("exit_code") == 0
        and execution.get("summary_sha256") == _sha256(summary_path),
        "execution/summary mismatch",
    )
    _require(execution.get("plan_sha256") == _sha256(job / "plan.json"), "execution plan mismatch")
    _require(
        summary.get("status") == "COMPLETE" and summary.get("mode") == "full",
        "engine result incomplete",
    )
    _require(
        summary.get("source_count") == len(sources)
        and summary.get("configuration_count") == len(configs),
        "summary roster mismatch",
    )
    expected_records = sum(source["records"] for source in sources)
    _require(
        summary.get("input_records") == expected_records
        and summary.get("processed_records") == expected_records,
        "record coverage incomplete",
    )
    _require(summary.get("duration_stopped") is False, "replay stopped by duration")
    audit = summary["audit"]
    _require(
        audit.get("source_sha256_verified") is True and audit.get("records") == expected_records,
        "engine source audit incomplete",
    )
    _require(
        audit.get("bytes") == sum(source["bytes"] for source in sources),
        "source byte count mismatch",
    )
    _require(
        summary.get("build_sha256")
        == receipt.get("binary_sha256")
        == execution.get("binary_sha256"),
        "binary identity mismatch",
    )
    _require(
        summary["resources"]["threads"] == plan["threads"]
        and summary["resources"]["block_bytes"] == plan["block_bytes"],
        "execution settings mismatch",
    )
    reported = summary["configurations"]
    _require(len(reported) == len(configs), "missing configuration results")
    source_close = {source["id"]: _last_close(source) for source in sources}
    if rehash_sources:
        with ThreadPoolExecutor(max_workers=8) as pool:
            # Consume every result so that any reader failure rejects publication.
            for _ in pool.map(_verify_source_hash, sources):
                pass
    index = _read(result_dir / "pairs.index.json")
    _require(set(index) == {config["id"] for config in configs}, "pair index incomplete")
    pair_path = result_dir / "pairs.jsonl"
    pair_hash = hashlib.sha256()
    pair_count = buy_count = sell_count = 0
    with pair_path.open("rb") as stream:
        for config, result in zip(configs, reported, strict=True):
            config_id = config["id"]
            _require(result["id"] == config_id, f"configuration order mismatch: {config_id}")
            _require(
                _fraction(result["step_pct"]) == _fraction(config["step_pct"]), "step mismatch"
            )
            _require(_fraction(result["tp_pct"]) == _fraction(config["tp_pct"]), "TP mismatch")
            _require(
                isinstance(result["trade_hash"], str)
                and HASH.fullmatch(result["trade_hash"]) is not None,
                "invalid configuration trade hash",
            )
            _require(index[config_id]["offset"] == stream.tell(), "pair index offset mismatch")
            venue_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
            all_rows: list[dict[str, Any]] = []
            for source in sources:
                line = stream.readline(1024 * 1024 + 1)
                _require(
                    line.endswith(b"\n") and len(line) <= 1024 * 1024,
                    "missing or oversized pair row",
                )
                pair_hash.update(line)
                row = json.loads(line)
                _require(
                    row.get("config_id") == config_id and row.get("source_id") == source["id"],
                    "pair row order mismatch",
                )
                _require(
                    row.get("venue") == source["venue"] and row.get("symbol") == source["symbol"],
                    "pair identity mismatch",
                )
                _require(
                    row.get("last_price_ms") == source["last_ms"] + 3000,
                    "pair end timestamp mismatch",
                )
                _require(
                    _fraction(row["last_price"]) == source_close[source["id"]],
                    "pair end price mismatch",
                )
                _require(
                    isinstance(row["trade_hash"], str)
                    and HASH.fullmatch(row["trade_hash"]) is not None,
                    "invalid pair trade hash",
                )
                row = _check_metrics(row)
                quantity = _fraction(row["open_qty"])
                _require(
                    quantity >= 0 and (quantity == 0) == (row["spent"] == 0),
                    "position/spent mismatch",
                )
                _require(
                    row["open_value"] == quantity * source_close[source["id"]],
                    "open valuation mismatch",
                )
                venue_rows[source["venue"]].append(row)
                all_rows.append(row)
                pair_count += 1
                buy_count += row["buy_count"]
                sell_count += row["sell_count"]
            _require(index[config_id]["end"] == stream.tell(), "pair index end mismatch")
            _rollup(all_rows, result["total"])
            _require(len(result["venues"]) == 7, "venue results incomplete")
            for venue_result in result["venues"]:
                venue = venue_result["venue"]
                _require(venue in venue_rows, "unexpected venue result")
                _rollup(venue_rows[venue], venue_result)
        _require(stream.read(1) == b"", "extra pair result rows")
    _require(pair_count == len(sources) * len(configs), "pair result coverage incomplete")
    return {
        "schema": "grid-research-sweep-publication-audit-v1",
        "status": "VERIFIED" if rehash_sources else "STRUCTURE_VERIFIED",
        "job": str(job),
        "selection_sha256": selection_sha,
        "summary_sha256": _sha256(summary_path),
        "pairs_sha256": pair_hash.hexdigest(),
        "source_sha256_reverified": rehash_sources,
        "source_count": len(sources),
        "configuration_count": len(configs),
        "pair_rows": pair_count,
        "processed_records": expected_records,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "threads": plan["threads"],
        "block_bytes": plan["block_bytes"],
        "resumed": resumed,
        "verification_seconds": time.monotonic() - began,
        "verifier_sha256": _sha256(Path(__file__)),
    }


def _publish(path: Path, report: dict[str, Any]) -> None:
    _require(
        path.resolve().is_relative_to("/opt/grid-runs"),
        "publication audit must be saved under /opt/grid-runs",
    )
    _require(not path.exists(), "refusing to overwrite publication audit")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink()
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rehash-sources", action="store_true")
    args = parser.parse_args()
    report = verify_job(args.job, args.selection, rehash_sources=args.rehash_sources)
    _publish(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
