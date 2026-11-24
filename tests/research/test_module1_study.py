"""Study orchestration preserves rejected trials and never mutates source files."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import module1_study as study
from scripts.module1_monthly import DAY_MS, METADATA, VALUES, analyse
from scripts.module1_report import report
from scripts.module1_verify_sources import sha256, utc_ms

ROOT = Path(__file__).resolve().parents[2]


def financial_result(manifest: dict[str, Any]) -> dict[str, Any]:
    """An entirely cash-funded fixture with independently known zero PnL."""

    def scope(funding: int) -> dict[str, Any]:
        curve = {
            "beginning_nav": str(funding),
            "ending_nav": str(funding),
            "net_pnl": "0",
            "net_return": "0",
            "max_observed_drawdown": "0",
            "known_samples": 1,
            "unknown_samples": 0,
        }
        return {
            "raw": curve.copy(),
            "net": curve.copy(),
            "buy_hold_raw": curve.copy(),
            "buy_hold_net": curve.copy(),
            "cash": str(funding),
            "position_cost": "0",
            "realized_pnl": "0",
            "reserved_quote": "0",
            "raw_unrealized_pnl": "0",
            "net_unrealized_pnl": "0",
            "metrics": {
                "fees_quote": "0",
                "turnover": "0",
                "gross_realized_profit": "0",
                "gross_realized_loss": "0",
                "profit_factor": None,
            },
        }

    allocations = [
        dict(
            scope(1000),
            venue=r["venue"],
            symbol=r["market"],
            allocation=str(i),
            owned_base="0",
            sellable_base="0",
            residual_base="0",
            liquidation_valuation_known=True,
        )
        for i, r in enumerate(manifest["selected"])
    ]
    return {
        "source_status": "UNVERIFIED SOURCE / ASSUMPTION-BASED",
        "strict_historical_performance": "NOT_COMPUTABLE",
        "portfolio": scope(1000 * len(allocations)),
        "allocations": allocations,
        "exchanges": [
            dict(scope(1000 * sum(a["venue"] == v for a in allocations)), venue=v)
            for v in sorted({a["venue"] for a in allocations})
        ],
        "processed_rows": len(allocations),
    }


@pytest.fixture
def scenario(tmp_path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    config = study.read_json(ROOT / "research/runs/module1-20261110/experiment_config.json")
    manifest = study.read_json(ROOT / "research/runs/module1-20261110/selection_manifest.json")
    originals = tmp_path / "originals"
    originals.mkdir()
    config["original_sources_root"] = str(originals)
    manifest["source_root"] = str(originals)
    for row in manifest["selected"]:
        path = originals / row["file"]
        path.write_bytes(bytes(24))  # Container fixture only; not a valid market candle.
        row.update(
            source_path=str(path),
            bytes=24,
            rows=1,
            aligned_rows=1,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    config_path = inputs / "experiment_config.json"
    config_path.write_text(json.dumps(config))
    (inputs / "selection_manifest.json").write_text(json.dumps(manifest))
    return config_path, config, manifest


def test_frozen_roster_and_all_twelve_policy_combinations_are_required(
    scenario: tuple[Path, dict[str, Any], dict[str, Any]],
) -> None:
    _, config, manifest = scenario
    study.validate(config, manifest)
    for mutation in ("case", "quote", "global", "capital", "latency"):
        invalid = copy.deepcopy(config)
        if mutation == "case":
            invalid["primary_cases"][-1] = invalid["primary_cases"][0]
        elif mutation == "quote":
            invalid["fees"]["currency"] = "BTC"
        elif mutation == "global":
            invalid["take_profit"]["global_enabled"] = False
        elif mutation == "capital":
            invalid["funding"]["initial_total_quote"] = "11000"
        else:
            invalid["execution"]["cancel_finality_delay_bars"] = 0
        with pytest.raises(ValueError):
            study.validate(invalid, manifest)


def test_preparation_is_exclusive_and_retains_every_source_without_reading_candles(
    tmp_path: Path,
    scenario: tuple[Path, dict[str, Any], dict[str, Any]],
) -> None:
    path, _, manifest = scenario
    output = tmp_path / "prepared"
    assert study.study(path, Path("/usr/bin/true"), output, "all", False, 1) == 0
    plan = study.read_json(output / "plan.json")
    assert len(plan["jobs"]) == 22
    assert sum(job["full_history"] for job in plan["jobs"]) == 10
    portfolio = next(job for job in plan["jobs"] if job["id"] == "lower_one_strict")
    encoded = (output / portfolio["id"] / "input.tsv").read_text()
    assert encoded.count("SOURCE\t") == 10
    assert "CONFIG\tbook_bid\t-\n" in encoded
    assert "CONFIG\tladder_size\t1\n" in encoded
    assert "FEE_RULE\tbuy\tQUOTE\t0.001\t0.00000001\t-\t64\n" in encoded
    assert "REPLAY_CONFIG\texit_duration_ms\t14400000\n" in encoded  # pragma: allowlist secret
    assert not list(output.rglob("receipt.json"))
    assert all(Path(row["source_path"]).read_bytes() == bytes(24) for row in manifest["selected"])
    with pytest.raises(FileExistsError):
        study.study(path, Path("/usr/bin/true"), output, "all", False, 1)


def test_failed_trial_is_retained_and_does_not_remove_other_declared_trials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: tuple[Path, dict[str, Any], dict[str, Any]],
) -> None:
    path, _, manifest = scenario
    calls = []

    def child(
        command: list[str], output: Path, timeout: float, *, log_bytes: int
    ) -> dict[str, Any]:
        calls.append(command)
        assert log_bytes == study.MAX_RESULT_BYTES
        sources = [
            {
                "path": row["source_path"],
                "sha256": row["sha256"],
                "bytes": 24,
                "records": 1,
                "complete": True,
                "processed_rows": 1,
            }
            for row in manifest["selected"]
        ]
        if len(calls) == 1:
            sources[0]["sha256"] = "wrong"
        (output / "stdout.json").write_text(
            json.dumps(
                {
                    "schema": "grid-module1-replay-v1",
                    "sources": sources,
                    "input_sha256": sha256(Path(command[-1])),
                    "source_rows": 10,
                    "start_ms": utc_ms(manifest["coverage"]["common_start_utc"]),
                    "end_ms_exclusive": utc_ms(manifest["coverage"]["common_end_utc_exclusive"]),
                    **financial_result(manifest),
                }
            )
        )
        (output / "stderr.txt").write_text("")
        return {"exit_code": 0, "wall_seconds_including_process_start_and_poll": 0.1}

    monkeypatch.setattr(study, "run_child", child)
    output = tmp_path / "executed"
    assert study.study(path, Path("/usr/bin/true"), output, "strict", True, 1) == 1
    result = study.read_json(output / "run_summary.json")
    assert len(calls) == result["planned_cases"] == result["finished_cases"] == 4
    assert [r["status"] for r in result["receipts"]] == ["FAIL", "PASS", "PASS", "PASS"]
    assert result["not_run"] == []
    assert "sha256" in result["receipts"][0]["validation_failures"][0]
    records = [json.loads(line) for line in (output / "registry.jsonl").read_text().splitlines()]
    assert len(records) == 12  # All planned, started, and final outcomes remain visible.


def test_independent_financial_checks_reject_created_money_and_hidden_unknowns(
    scenario: tuple[Path, dict[str, Any], dict[str, Any]],
) -> None:
    _, _, manifest = scenario
    result = financial_result(manifest)
    result["sources"] = [{"processed_rows": 1} for _ in manifest["selected"]]
    assert study.financial_checks(result) == []
    created_money = copy.deepcopy(result)
    created_money["allocations"][0]["cash"] = "1001"
    assert any("conservation" in e for e in study.financial_checks(created_money))
    unknown = copy.deepcopy(result)
    unknown["allocations"][0]["net"]["ending_nav"] = None
    failures = study.financial_checks(unknown)
    assert any("unknown_return" in e for e in failures)
    assert any("unknown_child" in e for e in failures)
    invalid_factor = copy.deepcopy(result)
    invalid_factor["portfolio"]["metrics"]["profit_factor"] = "Infinity"
    assert any("undefined_profit_factor" in e for e in study.financial_checks(invalid_factor))


@pytest.mark.parametrize("kind", ["buy_hold_raw", "buy_hold_net"])
@pytest.mark.parametrize("scope", ["portfolio", "exchange"])
def test_independently_consistent_benchmark_must_equal_allocation_totals(
    scenario: tuple[Path, dict[str, Any], dict[str, Any]], kind: str, scope: str
) -> None:
    _, _, manifest = scenario
    result = financial_result(manifest)
    result["sources"] = [{"processed_rows": 1} for _ in manifest["selected"]]
    row = result["portfolio"] if scope == "portfolio" else result["exchanges"][0]
    initial = int(row[kind]["beginning_nav"])
    # The parent curve reconciles internally but invents gains absent in its children.
    row[kind].update(ending_nav=str(initial * 2), net_pnl=str(initial), net_return="1")
    label = "portfolio" if scope == "portfolio" else row["venue"]
    assert f"{label}:{kind}:ending_nav" in study.financial_checks(result)


@pytest.mark.parametrize("kind", ["buy_hold_raw", "buy_hold_net"])
def test_unknown_allocation_benchmark_withholds_aggregate_benchmark(
    scenario: tuple[Path, dict[str, Any], dict[str, Any]], kind: str
) -> None:
    _, _, manifest = scenario
    result = financial_result(manifest)
    result["sources"] = [{"processed_rows": 1} for _ in manifest["selected"]]
    allocation = result["allocations"][0]
    allocation[kind].update(ending_nav=None, net_pnl=None, net_return=None)
    failures = study.financial_checks(result)
    assert f"portfolio:{kind}:ending_nav:unknown_child" in failures
    assert f"{allocation['venue']}:{kind}:ending_nav:unknown_child" in failures
    for row in [result["portfolio"], *result["exchanges"]]:
        if row is result["portfolio"] or row["venue"] == allocation["venue"]:
            row[kind].update(ending_nav=None, net_pnl=None, net_return=None)
    assert study.financial_checks(result) == []


def test_unknown_nav_rejects_stale_unrealized_and_liquidation_values(
    scenario: tuple[Path, dict[str, Any], dict[str, Any]],
) -> None:
    _, _, manifest = scenario
    result = financial_result(manifest)
    result["sources"] = [{"processed_rows": 1} for _ in manifest["selected"]]
    # Withhold all related aggregate returns so only the companion fields are wrong.
    for row in [result["portfolio"], *result["allocations"], *result["exchanges"]]:
        for kind in ("raw", "net"):
            row[kind].update(ending_nav=None, net_pnl=None, net_return=None)
    failures = study.financial_checks(result)
    assert any("raw:unknown_unrealized" in error for error in failures)
    assert any("net:unknown_unrealized" in error for error in failures)
    assert "allocation_0:liquidation_valuation_known" in failures
    assert "allocation_0:unknown_liquidation_quantities" in failures
    for row in [result["portfolio"], *result["allocations"], *result["exchanges"]]:
        row.update(raw_unrealized_pnl=None, net_unrealized_pnl=None)
    for row in result["allocations"]:
        row.update(sellable_base=None, residual_base=None, liquidation_valuation_known=False)
    assert study.financial_checks(result) == []


def test_known_nav_requires_true_allocation_valuation_flag(
    scenario: tuple[Path, dict[str, Any], dict[str, Any]],
) -> None:
    _, _, manifest = scenario
    result = financial_result(manifest)
    result["sources"] = [{"processed_rows": 1} for _ in manifest["selected"]]
    result["allocations"][0]["liquidation_valuation_known"] = False
    assert "allocation_0:liquidation_valuation_known" in study.financial_checks(result)


def test_report_retains_losses_and_not_run_rows_and_detects_tampering(
    tmp_path: Path,
    scenario: tuple[Path, dict[str, Any], dict[str, Any]],
) -> None:
    path, _, manifest = scenario
    folder = tmp_path / "batch"
    study.study(path, Path("/usr/bin/true"), folder, "strict", False, 1)
    plan = study.read_json(folder / "plan.json")
    job = plan["jobs"][0]
    trial = folder / job["id"]
    result = financial_result(manifest)
    result.update(
        schema="grid-module1-replay-v1",
        input_sha256=job["input_sha256"],
        start_ms=job["start_ms"],
        end_ms_exclusive=job["end_ms_exclusive"],
        declared_start_ms=job["start_ms"],
        declared_end_ms_exclusive=job["end_ms_exclusive"],
        ending_nav_timestamp_ms=job["end_ms_exclusive"],
        calendar_minutes=1,
        observed_timestamp_groups=1,
        complete_timestamp_groups=1,
        unobserved_timestamp_minutes=0,
        nav_sampling="first observed group, first group in each UTC day, final observed group",
        sample_coverage_scope="observed groups; calendar gaps and declared endpoint are separate",
        terminal_assessment={
            "timestamp_ms": job["end_ms_exclusive"],
            "observed_timestamp_group": True,
            "raw_nav": "9900",
            "net_nav": "9900",
            "buy_hold_raw_nav": "10000",
            "buy_hold_net_nav": "10000",
        },
        source_rows=10,
        cycles=[],
        exit_slots=[
            dict(
                anchor_bid=None,
                orders=0,
                order_id=None,
                created_ms=None,
                started_ms=None,
                reason="",
            )
            for _ in manifest["selected"]
        ],
        sources=[
            {
                "path": s["source_path"],
                "sha256": s["sha256"],
                "bytes": 24,
                "records": 1,
                "complete": True,
                "processed_rows": 1,
                "missing_minutes": 0,
                "window_missing_minutes": 0,
                "leading_missing_minutes": 0,
                "trailing_missing_minutes": 0,
            }
            for s in manifest["selected"]
        ],
    )
    # Every independently funded allocation loses exactly ten quote units.
    for row in [result["portfolio"], *result["allocations"], *result["exchanges"]]:
        initial = int(row["raw"]["beginning_nav"])
        loss = initial // 100
        row.update(cash=str(initial - loss), realized_pnl=str(-loss))
        row["metrics"]["gross_realized_loss"] = str(loss)
        row["metrics"]["planning_notice_counts"] = {
            "waiting for terminal seed": initial // 1000,
            'BUY deferred: "quote reserve", budget': initial // 500,
        }
        for kind in ("raw", "net"):
            row[kind].update(ending_nav=str(initial - loss), net_pnl=str(-loss), net_return="-0.01")
    output = trial / "stdout.json"
    output.write_text(json.dumps(result))
    (trial / "receipt.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "result_file_sha256": sha256(output),
                "input_sha256": job["input_sha256"],
                **{k: plan[k] for k in ("config_sha256", "manifest_sha256", "executable_sha256")},
            }
        )
    )
    summary = report(folder, tmp_path / "report")
    assert summary["tables_directory"] == str(tmp_path / "report")
    assert summary["passed_execution_and_accounting"] == 1
    assert summary["incomplete"] == 0
    assert summary["not_run"] == 3
    assert summary["trials"][0]["portfolio"]["net_net_return"] == "-0.01"
    assert summary["trials"][0]["portfolio"]["net_buy_hold_return_delta"] == "-0.01"
    assert "-0.01" in (tmp_path / "report" / "portfolio_results.csv").read_text()
    for name in ("results.csv", "exchange_results.csv", "portfolio_results.csv"):
        with (tmp_path / "report" / name).open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            if row["status"] != "PASS":
                continue
            initial = int(row["raw_beginning_nav"])
            encoded = row["metrics_planning_notice_counts"]
            assert encoded == (
                '{"BUY deferred: \\"quote reserve\\", budget":'
                + str(initial // 500)
                + ',"waiting for terminal seed":'
                + str(initial // 1000)
                + "}"
            )
            assert json.loads(encoded) == {
                'BUY deferred: "quote reserve", budget': initial // 500,
                "waiting for terminal seed": initial // 1000,
            }
    # A missing terminal mark must retain unknown NAV and the scoped exit diagnosis.
    for row in [result["portfolio"], *result["allocations"], *result["exchanges"]]:
        cost = int(row["raw"]["beginning_nav"]) // 1000
        row.update(cash=str(int(row["cash"]) - cost), position_cost=str(cost))
        for kind in ("raw", "net", "buy_hold_raw", "buy_hold_net"):
            row[kind].update(ending_nav=None, net_pnl=None, net_return=None, unknown_samples=1)
            if kind in ("raw", "net"):
                row[f"{kind}_unrealized_pnl"] = None
    for allocation in result["allocations"]:
        allocation.update(
            owned_base="1",
            sellable_base=None,
            residual_base=None,
            liquidation_valuation_known=False,
        )
    result["declared_end_ms_exclusive"] += 60000
    result["ending_nav_timestamp_ms"] = result["declared_end_ms_exclusive"]
    result["calendar_minutes"] += 1
    result["unobserved_timestamp_minutes"] = 1
    result["terminal_assessment"].update(
        timestamp_ms=result["declared_end_ms_exclusive"],
        observed_timestamp_group=False,
        raw_nav=None,
        net_nav=None,
        buy_hold_raw_nav=None,
        buy_hold_net_nav=None,
    )
    for source in result["sources"]:
        source["trailing_missing_minutes"] = 1
    result["exit_slots"][0].update(
        anchor_bid="2.5",
        orders=4,
        order_id=17,
        created_ms=job["start_ms"] + 60000,
        started_ms=job["start_ms"],
        reason="exit deadline expired",
    )
    result["exit_slots"][1]["reason"] = "minimum lot leaves unsellable residual"
    output.write_text(json.dumps(result))
    receipt = study.read_json(trial / "receipt.json")
    receipt["result_file_sha256"] = sha256(output)
    (trial / "receipt.json").write_text(json.dumps(receipt))
    diagnostic = report(folder, tmp_path / "diagnostic-report")
    retained = study.read_json(tmp_path / "diagnostic-report" / "summary.json")["trials"][0]
    assert retained == diagnostic["trials"][0]
    assert retained["portfolio"]["net_ending_nav"] is None
    assert retained["coverage"] == {key: result[key] for key in retained["coverage"]}
    assert retained["coverage"]["terminal_assessment"]["observed_timestamp_group"] is False
    assert retained["coverage"]["terminal_assessment"]["net_nav"] is None
    assert retained["coverage"]["ending_nav_timestamp_ms"] == job["end_ms_exclusive"] + 60000
    assert retained["coverage"]["unobserved_timestamp_minutes"] == 1
    assert retained["coverage"]["sources"][0]["trailing_missing_minutes"] == 1
    assert retained["coverage"]["nav_sampling"] == result["nav_sampling"]
    assert retained["coverage"]["sample_coverage_scope"] == result["sample_coverage_scope"]
    assert retained["exit_slots"] == [
        {
            "scope": {
                key: result["allocations"][index][key] for key in ("venue", "symbol", "allocation")
            },
            "slot": slot,
        }
        for index, slot in enumerate(result["exit_slots"])
    ]
    assert "nav_curve" not in retained and "nav_curve" not in retained["coverage"]
    attempted, failed = plan["jobs"][1:3]
    study.append_registry(folder / "registry.jsonl", {"id": attempted["id"], "status": "RUNNING"})
    failure_receipt = {"status": "FAIL", "validation_failures": ["nonzero_exit"]}
    (folder / failed["id"] / "receipt.json").write_text(json.dumps(failure_receipt))
    study.append_registry(folder / "registry.jsonl", {"id": failed["id"], **failure_receipt})
    interim = report(folder, tmp_path / "interim-report")
    assert [row["status"] for row in interim["trials"]] == ["PASS", "INCOMPLETE", "FAIL", "NOT_RUN"]
    assert interim["failed"] == interim["incomplete"] == interim["not_run"] == 1
    assert interim["trials"][2]["validation_failures"] == ["nonzero_exit"]
    with (tmp_path / "interim-report" / "portfolio_results.csv").open(newline="") as stream:
        interim_rows = list(csv.DictReader(stream))
    assert interim_rows[1]["status"] == "INCOMPLETE"
    assert interim_rows[1]["net_ending_nav"] == ""
    # Older prepared evidence may lack a registry. No attempt is then inferred.
    (folder / "registry.jsonl").unlink()
    without_registry = report(folder, tmp_path / "without-registry-report")
    assert without_registry["not_run"] == 2 and without_registry["incomplete"] == 0
    output.write_text(output.read_text() + " ")
    with pytest.raises(ValueError, match="receipt/hash mismatch"):
        report(folder, tmp_path / "tampered-report")
    assert not (tmp_path / "tampered-report").exists()


def test_output_cannot_be_created_beside_originals_or_in_archive(
    scenario: tuple[Path, dict[str, Any], dict[str, Any]],
) -> None:
    path, _, manifest = scenario
    for output in (
        Path("/opt/datasets/forbidden-study"),
        Path(manifest["selected"][0]["source_path"]).parent / "forbidden-study",
    ):
        with pytest.raises(ValueError, match="outside"):
            study.study(path, Path("/usr/bin/true"), output, "all", False, 1)
        assert not output.exists()


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}', "[]"])
def test_untrustworthy_json_and_tsv_fields_are_rejected(tmp_path: Path, text: str) -> None:
    path = tmp_path / "input.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        study.read_json(path)
    with pytest.raises(ValueError):
        study.token("value\nCONFIG\tspacing\t0")


def monthly_sample(
    offset_ms: int, *, trial: str = "seed_multi_strict", benchmark: str = "100", net: str = "100"
) -> dict[str, str]:
    return {
        "trial_id": trial,
        "status": "PASS",
        "timestamp_ms": str(1735689600000 + offset_ms),  # 2025-01-01 UTC.
        "full_history": "False",
        "entry": "seed_min_limit",
        "ladder": "ladder_multi",
        "ohlc": "strict_skip_ambiguous",
        "config_sha256": "same-frozen-config",
        "result_sha256": trial,
        "raw_nav": net,
        "net_nav": net,
        "buy_hold_raw_nav": benchmark,
        "buy_hold_net_nav": benchmark,
        "cash_benchmark": "100",
    }


def write_monthly_input(path: Path, points: list[dict[str, str]]) -> None:
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["trial_id", "status", "timestamp_ms", *METADATA, *VALUES]
        )
        writer.writeheader()
        writer.writerows(points)


@pytest.mark.parametrize(
    ("ending", "direction", "ratio"),
    [
        ("105", "flat", "0.050000000000000000"),
        ("95", "flat", "-0.050000000000000000"),
        ("105.000000000000000001", "rising", "0.050000000000000000"),
        ("94.999999999999999999", "falling", "-0.050000000000000000"),
    ],
)
def test_monthly_five_percent_labels_use_exact_endpoints_not_truncated_returns(
    tmp_path: Path, ending: str, direction: str, ratio: str
) -> None:
    source, output = tmp_path / "nav.csv", tmp_path / "monthly"
    write_monthly_input(
        source, [monthly_sample(0), monthly_sample(20 * DAY_MS, benchmark=ending, net="99")]
    )
    summary = analyse(source, output)
    with (output / "monthly.csv").open(newline="") as stream:
        row = next(csv.DictReader(stream))
    assert summary["direction_counts"] == {direction: 1}
    assert row["direction"] == direction
    assert row["buy_hold_raw_nav_return"] == ratio
    assert row["net_nav_return"] == "-0.010000000000000000"
    assert row["observed_span_days"] == "20"
    assert row["unsampled_utc_days_between_endpoints"] == "19"
    assert row["partial_month_endpoint_sampling"] == "True"


@pytest.mark.parametrize("span", [20 * DAY_MS - 1, 20 * DAY_MS, 20 * DAY_MS + 1])
def test_monthly_twenty_day_cutoff_is_inclusive(tmp_path: Path, span: int) -> None:
    source, output = tmp_path / "nav.csv", tmp_path / "monthly"
    write_monthly_input(source, [monthly_sample(0), monthly_sample(span, benchmark="110")])
    analyse(source, output)
    with (output / "monthly.csv").open(newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["direction"] == ("unclassified" if span < 20 * DAY_MS else "rising")
    assert row["unclassified_reason"] == (
        "insufficient_span_under_20_days" if span < 20 * DAY_MS else ""
    )


@pytest.mark.parametrize("endpoint", [0, 1])
def test_monthly_unknown_benchmark_endpoint_keeps_known_strategy_return(
    tmp_path: Path, endpoint: int
) -> None:
    source, output = tmp_path / "nav.csv", tmp_path / "monthly"
    points = [monthly_sample(0), monthly_sample(20 * DAY_MS, benchmark="110", net="102")]
    points[endpoint]["buy_hold_raw_nav"] = ""
    write_monthly_input(source, points)
    analyse(source, output)
    with (output / "monthly.csv").open(newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["direction"] == "unclassified"
    assert row["unclassified_reason"] == "unknown_buy_hold_raw_endpoint"
    assert row["buy_hold_raw_nav_return"] == row["buy_hold_raw_nav_change"] == ""
    assert row["net_nav_return"] == "0.020000000000000000"


def test_monthly_unknown_strategy_endpoint_and_intermediate_marks_remain_visible(
    tmp_path: Path,
) -> None:
    source, output = tmp_path / "nav.csv", tmp_path / "monthly"
    points = [
        monthly_sample(0, net=""),
        monthly_sample(DAY_MS, benchmark="", net=""),
        monthly_sample(20 * DAY_MS, benchmark="110", net="102"),
    ]
    write_monthly_input(source, points)
    analyse(source, output)
    with (output / "monthly.csv").open(newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["direction"] == "rising"
    assert row["buy_hold_raw_nav_unknown_observations"] == "1"
    assert row["net_nav_unknown_observations"] == "2"
    assert row["net_nav_first"] == row["net_nav_return"] == row["net_nav_change"] == ""
    assert row["net_nav_last"] == "102"


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("status", "FAIL", "passed retained trial"),
        ("trial_id", "", "passed retained trial"),
        ("timestamp_ms", "1735689600000", "strictly increasing"),
        ("timestamp_ms", "-1", "strictly increasing"),
        ("config_sha256", "other-config", "metadata changed"),
        ("net_nav", "-1", "bounded nonnegative"),
        ("net_nav", "NaN", "bounded nonnegative"),
        ("net_nav", "Infinity", "bounded nonnegative"),
        ("net_nav", "1e61", "bounded nonnegative"),
        ("net_nav", "1" * 257, "unbounded NAV"),
    ],
)
def test_monthly_invalid_input_is_rejected_before_output(
    tmp_path: Path, column: str, value: str, message: str
) -> None:
    source, output = tmp_path / "nav.csv", tmp_path / "monthly"
    points = [monthly_sample(0), monthly_sample(20 * DAY_MS)]
    points[-1][column] = value
    write_monthly_input(source, points)
    before = source.read_bytes()
    with pytest.raises(ValueError, match=message):
        analyse(source, output)
    assert source.read_bytes() == before
    assert not output.exists()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("trial_id,timestamp_ms\ncase,0\n", "missing retained NAV columns"),
        (
            "trial_id,status,timestamp_ms,raw_nav,net_nav,buy_hold_raw_nav,buy_hold_net_nav,cash_benchmark\n"
            "case,PASS,0,1,1,1,1,1,extra\n",
            "malformed CSV",
        ),
    ],
)
def test_monthly_malformed_csv_is_rejected(tmp_path: Path, text: str, message: str) -> None:
    source, output = tmp_path / "nav.csv", tmp_path / "monthly"
    source.write_text(text)
    with pytest.raises(ValueError, match=message):
        analyse(source, output)
    assert not output.exists()


def test_monthly_portfolio_comparisons_require_matched_observed_endpoints(tmp_path: Path) -> None:
    source, output = tmp_path / "nav.csv", tmp_path / "monthly"
    write_monthly_input(
        source,
        [
            monthly_sample(0),
            monthly_sample(20 * DAY_MS),
            monthly_sample(0, trial="seed_multi_buy"),
            monthly_sample(21 * DAY_MS, trial="seed_multi_buy"),
        ],
    )
    with pytest.raises(ValueError, match="unmatched observed monthly endpoints"):
        analyse(source, output)
    assert not output.exists()


def test_monthly_output_is_exclusive_and_outside_git_and_archive(tmp_path: Path) -> None:
    source, output = tmp_path / "nav.csv", tmp_path / "monthly"
    write_monthly_input(source, [monthly_sample(0), monthly_sample(20 * DAY_MS)])
    analyse(source, output)
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(FileExistsError):
        analyse(source, output)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before
    git_root = tmp_path / "checkout"
    (git_root / ".git").mkdir(parents=True)
    for forbidden in (git_root / "monthly", Path("/opt/datasets/forbidden-module1-monthly")):
        with pytest.raises(ValueError, match="outside source archive and Git"):
            analyse(source, forbidden)
        assert not forbidden.exists()
