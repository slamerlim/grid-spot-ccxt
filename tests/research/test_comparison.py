"""Predeclared OHLC alternatives: finite funding, distinct outcomes, no claimed PnL ordering."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path
from typing import Any, cast

import pytest

from grid.cli import main
from grid.domain.model import Side
from grid.domain.numbers import DomainError
from grid.research import comparison
from grid.research.artifacts import digest, publish
from grid.research.comparison import Summary, nav_range, run
from grid.research.config import bundle, load
from grid.research.history import MINUTE, RECORD, decode
from grid.research.portfolio_config import bundle as portfolio_bundle
from grid.research.replay import MODELS, Model, Replay, candidates
from grid.research.replay import run as replay

from .conftest import MakeReplay
from .test_portfolio_replay import exit_scenario, settings_for


def ambiguity(make_replay: MakeReplay) -> Replay:
    return make_replay(((100, 101, 99, 100),) * 2 + ((100, 111, 80, 100),))


def test_fixed_scenarios_do_not_sort_by_profit(make_replay: MakeReplay) -> None:
    spec = ambiguity(make_replay)
    report: dict[str, Any] = run(spec)
    rows = report["summaries"]
    assert [r.scenario for r in rows] == ["conservative", "baseline", "optimistic"]
    assert [r.model for r in rows] == list(MODELS)
    assert [r.starting_nav for r in rows] == [D(1000)] * 3
    assert [r.observed_end_nav for r in rows] == [D(1000), D("1026.46"), D(1010)]
    assert rows[1].observed_end_nav > rows[2].observed_end_nav  # Optimistic is not an upper bound.
    limits = report["observed_nav_range"]
    assert limits["available"] and limits["span"] == D("26.46")
    assert limits["minimum_observed_nav"] == D(1000)
    assert limits["maximum_observed_nav"] == D("1026.46")
    assert limits["evaluated_until_ms_exclusive"] == spec.end_ms
    allocations = [s["result"]["allocations"][0] for s in report["scenarios"]]
    assert [r["fills"] for r in allocations] == [1, 3, 2]
    assert [r["sell_fills"] for r in allocations] == [0, 0, 1]
    assert all(
        len([o for o in r["unfinished_orders"] if o.side is Side.SELL]) <= 1 for r in allocations
    )
    assert all(row.final_cycle_return_fraction is None for row in rows)
    assert report["reporting_currency"] == "QUOTE"
    assert all(s["result"]["config"].model == s["model"] for s in report["scenarios"])
    assert (
        digest({k: v for k, v in report.items() if k != "result_sha256"}) == report["result_sha256"]
    )


@pytest.mark.parametrize("model", MODELS)
def test_all_models_delay_new_opposite_order_and_touch_assumptions(
    make_replay: MakeReplay, model: Model
) -> None:
    spec = make_replay(((100, 101, 99, 100), (100, 150, 99, 100)))
    result: Any = replay(replace(spec, model=model))
    allocation = result["allocations"][0]
    assert allocation["fills"] == 1 and allocation["sell_fills"] == 0
    touched = make_replay(((100, 101, 99, 100), (100, 150, 100, 100)))
    result = replay(replace(touched, model=model))
    assert result["allocations"][0]["fills"] == (0 if model == MODELS[0] else 1)


def test_candidate_order_independent_of_input_order_and_invalid_model_refused(
    make_replay: MakeReplay,
) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 2)
    result: Any = replay(spec)
    orders = result["allocations"][0]["unfinished_orders"]
    bar = decode(RECORD.pack(spec.end_ms, 100, 111, 80, 100))
    for model in MODELS:
        forward = candidates(orders, bar, model)
        assert candidates(tuple(reversed(orders)), bar, model) == forward
        assert forward.ambiguous
        assert len({o.side for o in forward.orders}) <= 1
    assert {o.side for o in candidates(orders, bar, "touch_sell_priority").orders} == {Side.SELL}
    with pytest.raises(DomainError, match="unsupported OHLC"):
        candidates(orders, bar, cast(Model, "choose_best_future_price"))


def test_canonical_roster_and_low_ambient_precision_preserve_comparison(
    make_replay: MakeReplay,
) -> None:
    spec = ambiguity(make_replay)
    first = replace(spec.allocations[0], quote_cash=D(500))
    second = replace(first, scope=replace(first.scope, allocation="second"))
    spec = replace(spec, allocations=(first, second))
    expected = run(spec)
    with localcontext() as precision:
        precision.prec = 5
        assert run(replace(spec, allocations=(second, first))) == expected


@pytest.mark.usefixtures("async_sockets")
def test_portfolio_partial_outcomes_never_become_zero_or_completed(make_replay: MakeReplay) -> None:
    spec = ambiguity(make_replay)
    report: dict[str, Any] = run(spec, settings_for(spec))
    assert report["valuation_basis"] == "owned_portfolio_controller_net_nav"
    rows = report["summaries"]
    assert rows[0].observed_end_nav == D(1000)
    assert any(r.observed_end_nav is None for r in rows)
    assert all(r.phase == "active" and r.final_cycle_return_fraction is None for r in rows)
    assert not report["observed_nav_range"]["available"]
    assert "unknown_ending_nav" in report["observed_nav_range"]["reasons"]
    assert report["observed_nav_range"]["minimum_observed_nav"] is None
    for member in report["scenarios"]:
        by_bar: dict[int, set[Side]] = {}
        for i, kind, event in member["result"]["events"]:
            if kind == "fill":
                by_bar.setdefault(i, set()).add(event.side)
        assert all(len(sides) == 1 for sides in by_bar.values())


@pytest.mark.usefixtures("async_sockets")
def test_complete_portfolio_cli_scenarios_retain_actual_trigger_and_shortfall(
    make_replay: MakeReplay,
    tmp_path: Path,
) -> None:
    spec = exit_scenario(make_replay)
    config, settings, output = (
        tmp_path / name for name in ("config.json", "portfolio.json", "comparison.json")
    )
    publish(config, bundle(spec))
    publish(settings, portfolio_bundle(settings_for(spec)))
    assert (
        main(
            [
                "backtest",
                "--config",
                str(config),
                "--portfolio",
                str(settings),
                "--compare-scenarios",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    report = json.loads(output.read_bytes())["result"]
    assert report["observed_nav_range"]["available"]
    assert report["observed_nav_range"]["span"] == "0"
    for row in report["summaries"]:
        assert row["phase"] == "exit_complete"
        assert row["observed_end_nav"] == "1050"
        assert row["final_cycle_return_fraction"] == "0.05"
        assert row["exit_shortfall"] == "70"
        assert row["evaluated_until_ms_exclusive"] < spec.end_ms


@pytest.mark.parametrize("unknown", [False, True])
def test_different_horizons_cannot_publish_a_shared_nav_range(unknown: bool) -> None:
    row = Summary(
        "conservative",
        MODELS[0],
        D(1000),
        D(1100),
        D("0.1"),
        2 * MINUTE,
        "exit_complete",
        D("0.1"),
        D(0),
    )
    later = replace(
        row,
        scenario="baseline",
        model=MODELS[1],
        evaluated_until_ms_exclusive=3 * MINUTE,
        observed_end_nav=None if unknown else D(900),
        observed_nav_return_fraction=None if unknown else D("-0.1"),
        phase="exit_blocked",
        final_cycle_return_fraction=None,
        exit_shortfall=None,
    )
    result = nav_range((row, later, replace(row, scenario="optimistic", model=MODELS[2])))
    assert not result["available"] and result["span"] is None
    assert isinstance(result["reasons"], tuple)
    assert "different_evaluation_endpoints" in result["reasons"]
    assert result["minimum_observed_nav"] is None and result["maximum_observed_nav"] is None


@pytest.mark.parametrize("after", [1, 3])
def test_history_change_during_members_refuses_comparison(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch, after: int
) -> None:
    spec = ambiguity(make_replay)
    calls = 0

    def change(effective: Replay) -> dict[str, object]:
        nonlocal calls
        result = replay(effective)
        calls += 1
        if calls == after:
            path = effective.allocations[0].source.path
            before = path.stat()
            # Even identical bytes with changed identity cannot be mixed.
            path.write_bytes(path.read_bytes())
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
        return result

    monkeypatch.setattr(comparison, "run_allocation", change)
    with pytest.raises(DomainError, match="history changed"):
        run(spec)
    assert calls == (2 if after == 1 else 3)


def test_failed_member_or_capacity_never_publishes_partial_cli_result(
    make_replay: MakeReplay, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = ambiguity(make_replay)
    config, output = tmp_path / "config.json", tmp_path / "result.json"
    publish(config, bundle(spec))

    def fail(effective: Replay) -> dict[str, object]:
        if effective.model == "touch_buy_priority":
            raise DomainError("synthetic member failure")
        return replay(effective)

    args = ["backtest", "--config", str(config), "--compare-scenarios", "--output", str(output)]
    with monkeypatch.context() as patch:
        patch.setattr(comparison, "run_allocation", fail)
        assert main(args) == 2 and not output.exists()
    with monkeypatch.context() as patch:
        patch.setattr(comparison, "MAX_RESULT_BYTES", 1)
        assert main(args) == 2 and not output.exists()


def test_cli_process_and_single_model_bundle_preserve_inputs(
    make_replay: MakeReplay, tmp_path: Path
) -> None:
    spec = replace(ambiguity(make_replay), model="touch_sell_priority")
    config, output = tmp_path / "config.json", tmp_path / "result.json"
    publish(config, bundle(spec))
    assert load(config) == spec
    source = spec.allocations[0].source.path
    before = (source.read_bytes(), source.stat().st_mtime_ns, config.read_bytes())
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "grid",
            "backtest",
            "--config",
            str(config),
            "--compare-scenarios",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(output.read_bytes())
    assert report["schema"] == "grid-research-run-v1"
    assert report["result"]["schema"] == "grid-scenario-comparison-v1"
    assert len(report["result"]["scenarios"]) == 3
    assert report["result"]["input_config"]["model"] == "touch_sell_priority"
    assert digest(report["result"]) == report["result_payload_sha256"]
    assert (source.read_bytes(), source.stat().st_mtime_ns, config.read_bytes()) == before
