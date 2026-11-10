"""Run the frozen Module 1 cohort through one isolated C++ replay executable.

Use ``python -m scripts.module1_study`` from the repository. Preparation writes
only small declared inputs; --execute runs all selected cases and preserves
every failed trial. Original history is opened only by the read-only C++ reader.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, localcontext
from pathlib import Path
from typing import Any

from scripts.module1_verify_sources import identity, run_child, sha256, utc_ms, write_json

MODELS = ("strict_skip_ambiguous", "touch_buy_priority", "touch_sell_priority")
ENTRIES = ("seed_min_limit", "no_seed_lower")
LADDERS = ("ladder_multi", "ladder_one")
MAX_RESULT_BYTES = 32 * 1024 * 1024


def read_json(path: Path, *, max_bytes: int = 262144) -> dict[str, Any]:
    if path.stat().st_size > max_bytes:
        raise ValueError("configuration/manifest byte bound exceeded")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def finite_constant(value: str) -> object:
        raise ValueError(f"nonfinite JSON value: {value}")

    value = json.loads(path.read_bytes(), object_pairs_hook=unique, parse_constant=finite_constant)
    if not isinstance(value, dict):
        raise ValueError("configuration must be a JSON object")
    return value


def token(value: object) -> str:
    text = "-" if value is None else str(int(value)) if isinstance(value, bool) else str(value)
    if not text or any(ord(ch) < 32 or ord(ch) > 126 for ch in text):
        raise ValueError("TSV values must be nonempty printable ASCII")
    return text


def validate(config: dict[str, Any], manifest: dict[str, Any]) -> None:
    if config["schema"] != "grid-module1-experiment-v1" or config["contract_revision"] != 4:
        raise ValueError("explicit reviewed experiment revision 4 required")
    if config["source_status"] != "UNVERIFIED SOURCE / ASSUMPTION-BASED":
        raise ValueError("unverified source disclosure required")
    if config["optimization"] != "none" or config["reporting_currency"] != "USDT":
        raise ValueError("single-quote frozen experiment required")
    tp = config["take_profit"]
    if tp["global_enabled"] is not True or Decimal(tp["global_return_threshold"]) != Decimal(".1"):
        raise ValueError("mandatory global +10% trigger required")
    if tp["exchange_enabled"] or tp["symbol_enabled"]:
        raise ValueError("primary matrix keeps optional TP tiers disabled")
    if config["fees"]["currency"] != config["reporting_currency"]:
        raise ValueError("primary scenario requires explicit quote fees")
    if (
        config["execution"]["order_eligibility_delay_bars"]
        != config["execution"]["cancel_finality_delay_bars"]
    ):
        raise ValueError("replay protocol requires equal declared placement/cancel latency")
    rows = manifest["selected"]
    root = Path(config["original_sources_root"]).resolve()
    if Path(manifest["source_root"]).resolve() != root or any(
        Path(row["source_path"]).resolve().parent != root for row in rows
    ):
        raise ValueError("source paths must match the frozen root")
    rule_hashes = {
        item["sha256"]
        for item in manifest["artifacts"]
        if item["path"].endswith("/selection/selection_rule.json")
    }
    if rule_hashes != {config["selection_rule_sha256"]}:
        raise ValueError("selection rule identity disagrees with the experiment")
    if len(rows) != 10 or len({(r["venue"], r["market"]) for r in rows}) != 10:
        raise ValueError("frozen ten distinct markets required")
    if len({r["market"].split("/")[0] for r in rows}) != 10:
        raise ValueError("frozen ten distinct bases required")
    if len({r["venue"] for r in rows}) < 2:
        raise ValueError("multiple separately funded venues required")
    if any(r["structural_errors"] != 0 or not r["market"].endswith("/USDT") for r in rows):
        raise ValueError("structurally valid common-quote cohort required")
    if Decimal(config["funding"]["initial_quote_per_allocation"]) * 10 != Decimal(
        config["funding"]["initial_total_quote"]
    ):
        raise ValueError("portfolio funding must equal nonoverlapping allocations")
    cases = config["primary_cases"]
    expected = {
        (entry, ladder, model) for entry in ENTRIES for ladder in LADDERS for model in MODELS
    }
    if len(cases) != 12 or {(c["entry"], c["ladder"], c["ohlc"]) for c in cases} != expected:
        raise ValueError("complete fixed twelve-case matrix required")
    if len({c["id"] for c in cases}) != 12:
        raise ValueError("duplicate trial ID")
    for case in cases:
        if not case["id"] or any(
            ch not in "abcdefghijklmnopqrstuvwxyz0123456789_"  # pragma: allowlist secret
            for ch in case["id"]
        ):
            raise ValueError("unsafe trial ID")


def export_tsv(
    config: dict[str, Any],
    manifest: dict[str, Any],
    case: dict[str, str],
    selected: list[dict[str, Any]],
    *,
    full_history: bool,
) -> bytes:
    rules, grid, funding, fees, execution, tp = (
        config[key]
        for key in ("market_rules", "grid", "funding", "fees", "execution", "take_profit")
    )
    values: dict[str, object] = {
        "config_revision": 1,
        "market_generation": 1,
        "now_ms": 0,
        "rules_observed_ms": 0,
        "fees_observed_ms": 0,
        "allow_new_exposure": True,
        "book_bid": None,
        "book_ask": None,
        "book_observed_ms": None,
        "book_generation": None,
        "price_tick": rules["price_tick"],
        "amount_tick": rules["amount_tick"],
        "base_quantum": rules["base_ledger_quantum"],
        "quote_quantum": rules["quote_ledger_quantum"],
        "token_quantum": rules["fee_token_ledger_quantum"],
        "price_min": rules["minimum_price"],
        "price_max": rules["maximum_price"],
        "buy_amount_min": rules["minimum_buy_amount"],
        "buy_amount_max": rules["maximum_amount"],
        "sell_amount_min": rules["minimum_sell_amount"],
        "sell_amount_max": rules["maximum_amount"],
        "buy_cost_min": rules["minimum_buy_notional"],
        "buy_cost_max": None,
        "sell_cost_min": rules["minimum_sell_notional"],
        "sell_cost_max": None,
        "initial_quote_cash": funding["initial_quote_per_allocation"],
        "spacing": grid["spacing"],
        "net_target": grid["minimum_net_target_quote"],
        "quote_reserve": funding["quote_reserve_per_allocation"],
        "max_quote_committed": funding["maximum_committed_cost_per_allocation"],
        "lower_price": rules["minimum_price"],
        "upper_price": rules["maximum_price"],
        "cost_quantum": rules["cost_quantum"],
        "max_spread": execution["synthetic_spread"],
        "ladder_size": grid[
            "multi_pending_buy_levels"
            if case["ladder"] == "ladder_multi"
            else "one_pending_buy_levels"
        ],
        "max_lots": grid["maximum_accumulated_lots"],
        "max_open_orders": grid["maximum_open_orders"],
        "max_order_records": 10000,
        "max_fill_records": 10000,
        "max_replacements": grid["maximum_sell_replacements_per_round"],
        "max_source_age_ms": 60000,
        "seed_at": "bid",
    }
    coverage = manifest["coverage"]
    replay: dict[str, object] = {
        "start_ms": None if full_history else utc_ms(coverage["common_start_utc"]),
        "end_ms": None if full_history else utc_ms(coverage["common_end_utc_exclusive"]),
        "model": case["ohlc"],
        "latency_bars": execution["order_eligibility_delay_bars"],
        "synthetic_spread": execution["synthetic_spread"],
        "entry": case["entry"],
        "ladder": case["ladder"],
        "reentry_policy": grid["reentry_policy"],
        "require_source_tick_compatibility": execution["require_source_tick_compatibility"],
        "exchange_tp_enabled": tp["exchange_enabled"],
        "symbol_tp_enabled": tp["symbol_enabled"],
        "exchange_tp_threshold": tp["exchange_return_threshold"],
        "symbol_tp_threshold": tp["symbol_return_threshold"],
        "max_deviation": tp["maximum_price_deviation_from_trigger_bid"],
        "mark_max_age_ms": execution["portfolio_mark_max_age_ms"],
        "exit_ttl_ms": tp["exit_order_ttl_minutes"] * 60000,
        "exit_duration_ms": tp["maximum_exit_duration_minutes"] * 60000,
        "max_reprices": tp["maximum_exit_reprices"],
        "max_cycles": tp["maximum_cycles"],
        "nav_order_budget": tp["maximum_exit_reprices"] + 1,
    }
    lines: list[tuple[object, ...]] = [("GRID_MODULE1_REPLAY", 1)]
    for row in sorted(selected, key=lambda item: (item["venue"], item["market"])):
        lines.append(
            (
                "SOURCE",
                row["venue"],
                f"research-{row['venue']}",
                row["market"],
                f"{row['venue']}:{row['market']}",
                "module1",
                "cycle-1",
                row["source_path"],
                row["sha256"],
            )
        )
    lines.extend(("CONFIG", key, value) for key, value in sorted(values.items()))
    for side in ("buy", "sell"):
        lines.append(
            (
                "FEE_RULE",
                side,
                "QUOTE",
                fees[f"{side}_rate"],
                fees["settlement_quantum"],
                None,
                fees["forecast_max_settlements"],
            )
        )
    lines.extend(("REPLAY_CONFIG", key, value) for key, value in sorted(replay.items()))
    return ("\n".join("\t".join(token(value) for value in line) for line in lines) + "\n").encode()


def jobs(config: dict[str, Any], manifest: dict[str, Any], group: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if group in ("all", "individual"):
        control = next(c for c in config["primary_cases"] if c["id"] == "seed_multi_strict")
        for source in manifest["selected"]:
            result.append(
                {
                    "id": f"full_{source['venue']}_{source['market'].split('/')[0]}",
                    "case": control,
                    "selected": [source],
                    "full_history": True,
                }
            )
    if group in ("all", "portfolio", "strict"):
        for case in config["primary_cases"]:
            if group == "strict" and case["ohlc"] != "strict_skip_ambiguous":
                continue
            result.append(
                {
                    "id": case["id"],
                    "case": case,
                    "selected": manifest["selected"],
                    "full_history": False,
                }
            )
    if not result:
        raise ValueError("no declared jobs selected")
    for job in result:
        job["initial_quote_per_allocation"] = config["funding"]["initial_quote_per_allocation"]
        job["start_ms"] = utc_ms(
            job["selected"][0]["first_utc"]
            if job["full_history"]
            else manifest["coverage"]["common_start_utc"]
        )
        job["end_ms_exclusive"] = (
            utc_ms(job["selected"][0]["last_utc"]) + 60000
            if job["full_history"]
            else utc_ms(manifest["coverage"]["common_end_utc_exclusive"])
        )
    return result


def validate_result(result: dict[str, Any], job: dict[str, Any]) -> list[str]:
    failures = []
    if result.get("schema") != "grid-module1-replay-v1":
        failures.append("result_schema")
    if result.get("input_sha256") != job.get("input_sha256"):
        failures.append("input_sha256")
    rows = result.get("sources")
    if not isinstance(rows, list) or len(rows) != len(job["selected"]):
        return [*failures, "source_roster"]
    actual = {row.get("path"): row for row in rows if isinstance(row, dict)}
    if len(actual) != len(rows):
        failures.append("duplicate_or_malformed_source")
    for source in job["selected"]:
        observed = actual.get(source["source_path"], {})
        expected = {
            "sha256": source["sha256"],
            "bytes": source["bytes"],
            "records": source["rows"],
            "complete": True,
            "processed_rows": source["rows"] if job["full_history"] else source["aligned_rows"],
        }
        for key, value in expected.items():
            if type(observed.get(key)) is not type(value) or observed.get(key) != value:
                failures.append(f"{source['file']}:{key}")
    for key in ("start_ms", "end_ms_exclusive"):
        if result.get(key) != job[key]:
            failures.append(f"study_horizon:{key}")
    if result.get("source_rows") != sum(source["rows"] for source in job["selected"]):
        failures.append("source_rows")
    allocations = result.get("allocations", [])
    if not isinstance(allocations, list):
        return [*failures, "allocation_roster"]
    expected_scopes = {(s["venue"], s["market"]) for s in job["selected"]}
    actual_scopes = {(a.get("venue"), a.get("symbol")) for a in allocations if isinstance(a, dict)}
    if actual_scopes != expected_scopes or len(allocations) != len(expected_scopes):
        failures.append("allocation_roster")
    for row in allocations:
        if (
            not isinstance(row, dict)
            or row.get("raw", {}).get("beginning_nav") != job["initial_quote_per_allocation"]
        ):
            failures.append("allocation_funding")
    return failures


def financial_checks(result: dict[str, Any]) -> list[str]:
    """Independently reconcile published money and aggregation, never infer a fill."""
    errors: list[str] = []

    def money(value: object) -> Decimal:
        if not isinstance(value, str) or len(value) > 256:
            raise ValueError("bounded exact decimal string required")
        amount = Decimal(value)
        if not amount.is_finite() or abs(amount.adjusted()) > 100:
            raise ValueError("finite bounded monetary value required")
        return amount

    def equal(actual: object, expected: Decimal, label: str) -> None:
        if money(actual) != expected:
            errors.append(label)

    def aggregate(parent: dict[str, Any], children: list[dict[str, Any]], label: str) -> None:
        for key in ("cash", "position_cost", "realized_pnl"):
            equal(parent[key], sum((money(c[key]) for c in children), Decimal(0)), f"{label}:{key}")
        for key in ("fees_quote", "turnover", "gross_realized_profit", "gross_realized_loss"):
            equal(
                parent["metrics"][key],
                sum((money(c["metrics"][key]) for c in children), Decimal(0)),
                f"{label}:{key}",
            )
        for kind in ("raw", "net", "buy_hold_raw", "buy_hold_net"):
            for key in ("beginning_nav", "ending_nav"):
                values = [c[kind][key] for c in children]
                if any(value is None for value in values):
                    if parent[kind][key] is not None:
                        errors.append(f"{label}:{kind}:{key}:unknown_child")
                else:
                    equal(
                        parent[kind][key],
                        sum(map(money, values), Decimal(0)),
                        f"{label}:{kind}:{key}",
                    )

    try:
        with localcontext() as context:
            context.prec = 96
            if result["source_status"] != "UNVERIFIED SOURCE / ASSUMPTION-BASED":
                errors.append("source_disclosure")
            if result["strict_historical_performance"] != "NOT_COMPUTABLE":
                errors.append("strict_disclosure")
            allocations, portfolio, exchanges = (
                result[key] for key in ("allocations", "portfolio", "exchanges")
            )
            if not allocations or len({a["allocation"] for a in allocations}) != len(allocations):
                errors.append("allocation_roster")
            if len({e["venue"] for e in exchanges}) != len(exchanges) or {
                e["venue"] for e in exchanges
            } != {a["venue"] for a in allocations}:
                errors.append("exchange_roster")
            for index, row in enumerate([portfolio, *allocations, *exchanges]):
                label = f"financial_scope_{index}"
                initial = money(row["raw"]["beginning_nav"])
                cash, cost, realized = (
                    money(row[key]) for key in ("cash", "position_cost", "realized_pnl")
                )
                if cash < 0 or cost < 0 or cash + cost != initial + realized:
                    errors.append(f"{label}:cash_cost_conservation")
                if "reserved_quote" in row and not 0 <= money(row["reserved_quote"]) <= cash:
                    errors.append(f"{label}:reservation")
                metrics = row["metrics"]
                profit, loss = (
                    money(metrics[key]) for key in ("gross_realized_profit", "gross_realized_loss")
                )
                if profit < 0 or loss < 0 or profit - loss != realized:
                    errors.append(f"{label}:realized_components")
                if loss == 0 and metrics["profit_factor"] is not None:
                    errors.append(f"{label}:undefined_profit_factor")
                for kind in ("raw", "net", "buy_hold_raw", "buy_hold_net"):
                    if kind not in row:
                        continue
                    curve = row[kind]
                    equal(curve["beginning_nav"], initial, f"{label}:{kind}:funding")
                    unrealized = row.get(f"{kind}_unrealized_pnl")
                    if curve["ending_nav"] is None:
                        if curve["net_pnl"] is not None or curve["net_return"] is not None:
                            errors.append(f"{label}:{kind}:unknown_return")
                        if kind in ("raw", "net") and unrealized is not None:
                            errors.append(f"{label}:{kind}:unknown_unrealized")
                        continue
                    pnl = money(curve["ending_nav"]) - initial
                    equal(curve["net_pnl"], pnl, f"{label}:{kind}:pnl")
                    equal(
                        curve["net_return"],
                        (pnl / initial).quantize(Decimal("1e-18"), rounding=ROUND_DOWN),
                        f"{label}:{kind}:return",
                    )
                    if kind in ("raw", "net") and unrealized is not None:
                        equal(unrealized, pnl - realized, f"{label}:{kind}:unrealized")
            for row in allocations:
                label = f"allocation_{row['allocation']}"
                known = row["net"]["ending_nav"] is not None
                if row["liquidation_valuation_known"] is not known:
                    errors.append(f"{label}:liquidation_valuation_known")
                if not known and (
                    row["sellable_base"] is not None or row["residual_base"] is not None
                ):
                    errors.append(f"{label}:unknown_liquidation_quantities")
            aggregate(portfolio, allocations, "portfolio")
            for exchange in exchanges:
                aggregate(
                    exchange,
                    [a for a in allocations if a["venue"] == exchange["venue"]],
                    exchange["venue"],
                )
            if result["processed_rows"] != sum(s["processed_rows"] for s in result["sources"]):
                errors.append("processed_row_total")
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        errors.append(f"malformed_financial_result:{type(exc).__name__}")
    return errors


def append_registry(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def study(
    config_path: Path, binary: Path, output: Path, group: str, execute: bool, timeout: float
) -> int:
    config_path = config_path.resolve(strict=True)
    config = read_json(config_path)
    manifest_path = config_path.parent / config["selection_manifest"]
    manifest = read_json(manifest_path)
    validate(config, manifest)
    binary = binary.resolve(strict=True)
    identity(binary)
    output = output.resolve()
    if output.is_relative_to(Path("/opt/datasets")):
        raise ValueError("outputs must remain outside the immutable archive")
    sources = {
        Path(row["source_path"]): identity(Path(row["source_path"])) for row in manifest["selected"]
    }
    if any(output.is_relative_to(source.parent) for source in sources):
        raise ValueError("outputs must remain outside all source directories")
    plan = jobs(config, manifest, group)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(config_path, output / "experiment_config.json")
    shutil.copyfile(manifest_path, output / "selection_manifest.json")
    shutil.copyfile(Path(__file__), output / "executed_study.py")
    shutil.copyfile(
        Path(__file__).with_name("module1_verify_sources.py"), output / "executed_child_driver.py"
    )
    executable = output / "grid_backtest"
    shutil.copy2(binary, executable)
    executable_identity = identity(executable)
    hashes = {
        "config_sha256": sha256(config_path),
        "manifest_sha256": sha256(manifest_path),
        "executable_sha256": sha256(executable),
    }
    registry = output / "registry.jsonl"
    registry.touch(exist_ok=False)
    for job in plan:
        folder = output / job["id"]
        folder.mkdir()
        data = export_tsv(
            config, manifest, job["case"], job["selected"], full_history=job["full_history"]
        )
        (folder / "input.tsv").write_bytes(data)
        job["input_sha256"] = hashlib.sha256(data).hexdigest()
        append_registry(
            registry,
            {
                "id": job["id"],
                "status": "PLANNED",
                **hashes,
                "input_sha256": job["input_sha256"],
                "full_history": job["full_history"],
            },
        )
    write_json(
        output / "plan.json",
        {
            "schema": "grid-module1-study-plan-v1",
            **hashes,
            "source_status": config["source_status"],
            "jobs": plan,
            "command": sys.argv,
            "prepared_utc": datetime.now(UTC).isoformat(),
        },
    )
    if not execute:
        print(json.dumps({"status": "PREPARED_NOT_RUN", "jobs": len(plan), "output": str(output)}))
        return 0
    completed = []
    for job in plan:
        if any(identity(source) != before for source, before in sources.items()):
            raise ValueError("source identity changed before replay; retained earlier trials")
        if identity(executable) != executable_identity:
            raise ValueError("frozen executable identity changed; retained earlier trials")
        folder = output / job["id"]
        append_registry(
            registry, {"id": job["id"], "status": "RUNNING", "utc": datetime.now(UTC).isoformat()}
        )
        print(f"Running {job['id']}", flush=True)
        receipt = run_child(
            [str(executable), "replay", str(folder / "input.tsv")],
            folder,
            timeout,
            log_bytes=MAX_RESULT_BYTES,
        )
        failures: list[str] = []
        if receipt["exit_code"] != 0:
            failures.append("nonzero_exit")
        else:
            try:
                result = read_json(folder / "stdout.json", max_bytes=MAX_RESULT_BYTES)
                failures.extend(validate_result(result, job))
                failures.extend(financial_checks(result))
            except (ValueError, KeyError, TypeError) as exc:
                failures.append(f"invalid_result:{exc}")
        unchanged = all(identity(source) == before for source, before in sources.items())
        if not unchanged:
            failures.append("source_identity_changed")
        if identity(executable) != executable_identity:
            failures.append("executable_identity_changed")
        receipt.update(
            {
                "id": job["id"],
                "status": "FAIL" if failures else "PASS",
                **hashes,
                "input_sha256": job["input_sha256"],
                "result_file_sha256": sha256(folder / "stdout.json"),
                "source_identities_unchanged": unchanged,
                "validation_failures": failures,
            }
        )
        write_json(folder / "receipt.json", receipt)
        append_registry(registry, receipt)
        completed.append(receipt)
        print(
            json.dumps(
                {
                    "id": job["id"],
                    "status": receipt["status"],
                    "exit_code": receipt["exit_code"],
                    "wall_seconds": receipt["wall_seconds_including_process_start_and_poll"],
                }
            ),
            flush=True,
        )
        if not unchanged:
            break
    write_json(
        output / "run_summary.json",
        {
            "schema": "grid-module1-study-execution-v1",
            **hashes,
            "planned_cases": len(plan),
            "finished_cases": len(completed),
            "receipts": completed,
            "not_run": [job["id"] for job in plan[len(completed) :]],
            "scope": (
                "execution and source verification; financial interpretation "
                "requires full results review"
            ),
        },
    )
    return 0 if len(completed) == len(plan) and all(r["status"] == "PASS" for r in completed) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--group", choices=("all", "individual", "portfolio", "strict"), default="all"
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=3600)
    args = parser.parse_args()
    if not 1 <= args.timeout_seconds <= 21600:
        parser.error("each case requires a finite timeout from 1 to 21600 seconds")
    try:
        code = study(
            args.config, args.binary, args.output, args.group, args.execute, args.timeout_seconds
        )
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"Study refused: {exc}\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
