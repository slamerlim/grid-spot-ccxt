"""Extract exact compact tables from retained Module 1 trial receipts.

This command does not run a strategy or choose a winner. Failed and unexecuted
trials retain rows with empty financial values. It verifies the immutable input
and result hashes and independently reconciles accounting before publication.
Planning-notice counts describe emitted diagnostics, not inactive bars or time.
"""

from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

from scripts.module1_study import (
    MAX_RESULT_BYTES,
    financial_checks,
    read_json,
    validate_result,
)
from scripts.module1_verify_sources import sha256, write_json


def flatten(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dict):
            for name, amount in value.items():
                if key == "metrics" and name == "planning_notice_counts":
                    result[f"{key}_{name}"] = json.dumps(
                        amount, sort_keys=True, separators=(",", ":"), allow_nan=False
                    )
                elif not isinstance(amount, (dict, list)):
                    result[f"{key}_{name}"] = amount
        elif not isinstance(value, list):
            result[key] = value
    with localcontext() as context:
        context.prec = 96
        for kind in ("raw", "net"):
            nav = result.get(f"{kind}_ending_nav")
            cash = result.get("cash")
            result[f"{kind}_open_inventory_value"] = (
                str(Decimal(nav) - Decimal(cash)) if nav is not None and cash is not None else None
            )
            rate = result.get(f"{kind}_net_return")
            hold = result.get(f"buy_hold_{kind}_net_return")
            result[f"{kind}_buy_hold_return_delta"] = (
                str(Decimal(rate) - Decimal(hold))
                if rate is not None and hold is not None
                else None
            )
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    preferred = ("trial_id", "status", "full_history", "entry", "ladder", "ohlc", "venue", "symbol")
    keys = set().union(*(row.keys() for row in rows)) if rows else set(preferred)
    fields = [key for key in preferred if key in keys] + sorted(keys.difference(preferred))
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def report(study: Path, output: Path) -> dict[str, Any]:
    study = study.resolve(strict=True)
    plan = read_json(study / "plan.json", max_bytes=MAX_RESULT_BYTES)
    manifest = read_json(study / "selection_manifest.json")
    config = read_json(study / "experiment_config.json")
    for filename, key in (
        ("experiment_config.json", "config_sha256"),
        ("selection_manifest.json", "manifest_sha256"),
        ("grid_backtest", "executable_sha256"),
    ):
        if sha256(study / filename) != plan[key]:
            raise ValueError(f"study artifact changed: {filename}")
    output = output.resolve()
    if output.is_relative_to(Path("/opt/datasets")) or any(
        output.is_relative_to(Path(row["source_path"]).parent) for row in manifest["selected"]
    ):
        raise ValueError("report output must remain outside source directories")
    allocations: list[dict[str, Any]] = []
    portfolios: list[dict[str, Any]] = []
    exchanges: list[dict[str, Any]] = []
    cycles: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    trials: list[dict[str, Any]] = []
    for job in plan["jobs"]:
        folder = study / job["id"]
        if sha256(folder / "input.tsv") != job["input_sha256"]:
            raise ValueError(f"trial input changed: {job['id']}")
        receipt_path = folder / "receipt.json"
        receipt = read_json(receipt_path) if receipt_path.exists() else {"status": "NOT_RUN"}
        status = receipt["status"]
        base = {
            "trial_id": job["id"],
            "status": status,
            "full_history": job["full_history"],
            "entry": job["case"]["entry"],
            "ladder": job["case"]["ladder"],
            "ohlc": job["case"]["ohlc"],
            "source_status": config["source_status"],
            "config_sha256": plan["config_sha256"],
            "input_sha256": job["input_sha256"],
        }
        trial = dict(
            base,
            receipt=str(receipt_path),
            validation_failures=receipt.get("validation_failures", []),
        )
        if status != "PASS":
            portfolios.append(
                dict(base, validation_failures=json.dumps(trial["validation_failures"]))
            )
            allocations.extend(
                dict(base, venue=s["venue"], symbol=s["market"]) for s in job["selected"]
            )
            trials.append(trial)
            continue
        result_path = folder / "stdout.json"
        digest = sha256(result_path)
        if (
            digest != receipt["result_file_sha256"]
            or any(
                receipt[key] != plan[key]
                for key in ("config_sha256", "manifest_sha256", "executable_sha256")
            )
            or receipt["input_sha256"] != job["input_sha256"]
        ):
            raise ValueError(f"trial receipt/hash mismatch: {job['id']}")
        result = read_json(result_path, max_bytes=MAX_RESULT_BYTES)
        failures = validate_result(result, job) + financial_checks(result)
        expected = {(s["venue"], s["market"]) for s in job["selected"]}
        actual = {(a["venue"], a["symbol"]) for a in result["allocations"]}
        if actual != expected or len(result["allocations"]) != len(expected):
            failures.append("allocation_identity")
        if failures:
            raise ValueError(f"trial financial review failed: {job['id']}:{failures}")
        base.update(
            result_sha256=digest,
            start_ms=result["start_ms"],
            end_ms_exclusive=result["end_ms_exclusive"],
        )
        portfolios.append(
            {
                **base,
                **flatten(result["portfolio"]),
                **{
                    key: result[key]
                    for key in (
                        "processed_rows",
                        "source_rows",
                        "calendar_minutes",
                        "observed_timestamp_groups",
                        "complete_timestamp_groups",
                    )
                },
                **{
                    key: receipt.get(key)
                    for key in (
                        "wall_seconds_including_process_start_and_poll",
                        "user_cpu_seconds",
                        "system_cpu_seconds",
                        "cpu_wall_ratio",
                        "peak_rss_kib",
                        "filesystem_input_blocks",
                        "output_bytes",
                    )
                },
            }
        )
        allocations.extend({**base, **flatten(row)} for row in result["allocations"])
        exchanges.extend({**base, **flatten(row)} for row in result["exchanges"])
        for row in result.get("cycle_events", []):
            cycles.append({**base, **flatten(row)})
        for row in result.get("nav_curve", []):
            curve.append({**base, **flatten(row)})
        trial.update(
            result=str(result_path),
            result_sha256=digest,
            processed_rows=result["processed_rows"],
            portfolio=flatten(result["portfolio"]),
            cycles=result["cycles"],
        )
        trials.append(trial)
    summary = {
        "schema": "grid-module1-report-v1",
        "study": str(study),
        "config_sha256": plan["config_sha256"],
        "manifest_sha256": plan["manifest_sha256"],
        "executable_sha256": plan["executable_sha256"],
        "source_status": config["source_status"],
        "strict_historical_performance": "NOT_COMPUTABLE",
        "trials": trials,
        "planned": len(trials),
        "passed_execution_and_accounting": sum(t["status"] == "PASS" for t in trials),
        "failed": sum(t["status"] == "FAIL" for t in trials),
        "not_run": sum(t["status"] == "NOT_RUN" for t in trials),
        "interpretation": (
            "Execution PASS is not profitability or historical market certification. "
            "Empty CSV values mean unavailable, never zero. Returns are on all funded capital; "
            "unknown terminal NAV withholds return. Drawdown is sampled and may omit unknown "
            "periods. No annualization or regime selection is inferred."
        ),
    }
    output.mkdir(parents=True, exist_ok=False)
    for filename, rows in (
        ("results.csv", allocations),
        ("portfolio_results.csv", portfolios),
        ("exchange_results.csv", exchanges),
        ("cycle_events.csv", cycles),
        ("nav_curve.csv", curve),
    ):
        write_csv(output / filename, rows)
    summary["artifacts"] = {p.name: sha256(p) for p in sorted(output.glob("*.csv"))}
    write_json(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = report(args.study, args.output)
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError) as exc:
        parser.exit(2, f"Report refused: {exc}\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("planned", "passed_execution_and_accounting", "failed", "not_run")
            }
        )
    )


if __name__ == "__main__":
    main()
