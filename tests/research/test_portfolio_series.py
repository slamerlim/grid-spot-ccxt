"""Continuous finite-funded portfolio cycles over immutable synthetic history."""

import json
from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest

from grid.cli import main
from grid.domain.market import FeeRule
from grid.domain.model import Mode, Side
from grid.domain.numbers import DomainError
from grid.persistence.ledger import Ledger
from grid.portfolio.model import CyclePhase, DustRule, Nav, PoolKey
from grid.research import portfolio_replay
from grid.research.artifacts import digest, publish
from grid.research.comparison import run as compare
from grid.research.config import bundle
from grid.research.history import MINUTE, RECORD, Bar
from grid.research.portfolio_config import bundle as portfolio_bundle
from grid.research.portfolio_replay import Engine, run
from grid.research.replay import Replay

from .conftest import MakeReplay
from .test_portfolio_replay import exit_scenario, settings_for
from .test_portfolio_risk import conversion_scenario

pytestmark = pytest.mark.usefixtures("async_sockets")


def two_cycles(make_replay: MakeReplay) -> Replay:
    prices = ((100, 101, 99, 100),) * 2 + ((220, 221, 210, 220),) + ((150, 151, 140, 150),) * 5
    spec = make_replay(prices * 2 + ((100, 101, 99, 100),) * 3)
    first = spec.allocations[0]
    return replace(
        spec,
        allocations=(
            replace(
                first,
                context=replace(first.context, policy=replace(first.context.policy, spacing=D(2))),
            ),
        ),
    )


def test_two_cycles_preserve_funds_identities_and_original_performance(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = two_cycles(make_replay)
    original = spec.allocations[0].source.path.read_bytes()
    credits = 0
    credit = Ledger.credit

    def count_credit(*args: Any, **kwargs: Any) -> Any:
        nonlocal credits
        credits += 1
        return credit(*args, **kwargs)

    monkeypatch.setattr(Ledger, "credit", count_credit)
    result: Any = run(spec, settings_for(spec), max_cycles=2)
    assert credits == 1
    assert result["schema"] == "grid-portfolio-series-v1"
    assert "cycle" not in result and "final_return" not in result and "exit_shortfall" not in result
    assert result["requested_cycles"] == result["completed_cycles"] == 2
    first, last = result["cycle_outcomes"]
    assert first.id != last.id and first.revision < last.revision
    assert first.baseline.net == 1000 and first.final.net == 1050
    assert first.final.now_ms < last.baseline.now_ms
    assert last.baseline.net == 1050 and last.final.net == 1100
    assert result["last_cycle"] == last
    assert result["last_cycle_final_return"] == last.final_return
    assert result["observed_full_run_return_fraction"] == D("0.1")
    assert result["completed_full_run_return_fraction"] == D("0.1")
    assert result["last_cycle_exit_shortfall"] == 70
    assert result["stop_reason"] == "cycle_limit_reached"
    assert result["simulation_end_ms_exclusive"] == spec.start_ms + 16 * MINUTE
    assert result["consumed_source_minutes"] == 19 and result["simulated_bars"] == 16
    assert result["benchmarks"]["initial_owned_nav"] == 1000
    assert result["benchmarks"]["last"]["cash"]["return_fraction"] == 0
    assert result["benchmarks"]["last"]["buy_hold"]["return_fraction"] == D("0.49485101")
    assert result["allocation_risk"]["allocations"][0]["net_pnl_after_internal_flows"] == 100
    assert result["posted_performance"]["realized_pnl_quote"] == 100
    assert result["posted_performance"]["posted_fills"] == 4
    assert result["posted_performance"]["filled_sell_orders"] == 2
    orders = result["orders"]
    assert len({o.order_id for o in orders}) == len(orders)
    assert len({o.client_id for o in orders}) == len(orders)
    assert len({f.external_id for f in result["fills"]}) == len(result["fills"])
    submitted = [i for i, k, o in result["events"] if k == "submit"]
    for i, kind, event in result["events"]:
        if kind == "fill":
            created = submitted[int(event.external_order.removeprefix("order-")) - 1]
            assert i > created
    assert all(
        s.quote_cash == 1100 and not s.lots and not s.working for s in result["final_states"]
    )
    assert {r["cycle_id"] for r in result["intervals"]} == {first.id, last.id}
    assert result["result_sha256"] == digest(
        {k: v for k, v in result.items() if k != "result_sha256"}
    )
    assert spec.allocations[0].source.path.read_bytes() == original


def test_new_cycle_grid_starts_at_level_one_with_fresh_boundary_prices(
    make_replay: MakeReplay,
) -> None:
    spec = two_cycles(make_replay)
    result: Any = run(spec, settings_for(spec), max_cycles=2)
    buys = [(i, o) for i, kind, o in result["events"] if kind == "submit" and o.side is Side.BUY]
    assert [D(o.price) for i, o in buys if i == 0] == [D(100)]
    # Prior completed bar closed at 150. The next seed must use the new 100 close.
    assert [D(o.price) for i, o in buys if i == 8] == [D(100)]
    assert [D(o.price) for i, o in buys if i == 9] == [D(o.price) for i, o in buys if i == 1]


@pytest.mark.parametrize("obstacle", ["active", "unknown", "floor", "deadline"])
def test_unfinished_cycle_never_restarts_or_reports_completed_return(
    make_replay: MakeReplay, obstacle: str
) -> None:
    spec = (
        exit_scenario(make_replay)
        if obstacle in ("floor", "deadline")
        else make_replay(((100, 101, 99, 100),) * 8)
    )
    settings = settings_for(spec)
    if obstacle == "floor":
        settings = replace(
            settings,
            policy=replace(
                settings.policy, exits=(replace(settings.policy.exits[0], floor=D(400)),)
            ),
        )
    elif obstacle == "deadline":
        settings = replace(settings, policy=replace(settings.policy, exit_duration_ms=MINUTE))
    elif obstacle == "unknown":
        settings = replace(
            settings, liquidity=(replace(settings.liquidity[0], nav_depth=D("0.5")),)
        )
    result: Any = run(spec, settings, max_cycles=3)
    assert len(result["cycle_outcomes"]) == 1 and result["completed_cycles"] == 0
    assert result["stop_reason"] == "history_exhausted"
    assert result["simulation_end_ms_exclusive"] == spec.end_ms
    assert result["completed_full_run_return_fraction"] is None
    assert result["last_cycle_final_return"] is None
    if obstacle == "unknown":
        assert result["observed_full_run_return_fraction"] is None


def test_history_may_end_after_one_completion_or_with_a_new_unfinished_cycle(
    make_replay: MakeReplay,
) -> None:
    spec = two_cycles(make_replay)
    complete: Any = run(spec, settings_for(spec), max_cycles=3)
    assert complete["completed_cycles"] == 2 and len(complete["cycle_outcomes"]) == 3
    assert complete["last_cycle"].phase is CyclePhase.ACTIVE
    assert complete["initial_nav"].net == 1000 and complete["last_cycle"].baseline.net == 1100
    assert complete["completed_full_run_return_fraction"] is None
    # Cut history exactly at first completion: no same-bar restart, no fabricated second cycle.
    path = spec.allocations[0].source.path
    path.write_bytes(path.read_bytes()[: 8 * RECORD.size])
    first = spec.allocations[0]
    short = replace(
        spec,
        end_ms=spec.start_ms + 8 * MINUTE,
        allocations=(replace(first, source=replace(first.source, count=8)),),
    )
    result: Any = run(short, settings_for(short), max_cycles=2)
    assert result["completed_cycles"] == 1 and len(result["cycle_outcomes"]) == 1
    assert result["last_cycle"].phase is CyclePhase.COMPLETE
    assert result["completed_full_run_return_fraction"] is None
    assert result["observed_full_run_return_fraction"] == D("0.05")
    assert result["stop_reason"] == "history_exhausted"


def test_default_single_cycle_compatibility_and_precision(make_replay: MakeReplay) -> None:
    spec = two_cycles(make_replay)
    settings = settings_for(spec)
    single = run(spec, settings)
    assert run(spec, settings, max_cycles=1) == single
    multiple: Any = run(spec, settings, max_cycles=2)
    assert multiple["cycle_outcomes"][0] == single["cycle"]
    assert multiple["config_sha256"] != single["config_sha256"]
    with localcontext() as context:
        context.prec = 5
        assert run(spec, settings, max_cycles=2) == multiple


def test_conversion_sink_and_internal_transfers_survive_restart(
    make_replay: MakeReplay, tmp_path: Path
) -> None:
    spec, settings = conversion_scenario(make_replay, tmp_path)
    result: Any = run(spec, settings, max_cycles=2)
    assert result["completed_cycles"] == 1 and len(result["cycle_outcomes"]) == 2
    assert result["cycle_outcomes"][1].baseline.net == 1065
    sink = next(s for s in result["final_states"] if s.scope in settings.policy.sinks)
    assert sink.mode is Mode.PAUSED and sink.quote_cash == 15 and not sink.working
    assert not any(o.scope == sink.scope and o.side is Side.BUY for o in result["orders"])
    risk = result["allocation_risk"]
    assert sorted(m.amount for m in risk["internal_movements"]) == [D(-10), D(-5), D(15)]
    assert risk["allocations"][1]["net_pnl_after_internal_flows"] == 0
    assert result["initial_nav"].net == result["benchmarks"]["initial_owned_nav"] == 1015


def test_approved_dust_retains_cost_and_never_invents_a_fresh_seed(make_replay: MakeReplay) -> None:
    spec = make_replay(
        ((100, 101, 99, 100),) * 2 + ((230, 231, 220, 230),) + ((150, 151, 140, 150),) * 13
    )
    first = spec.allocations[0]
    context = first.context
    first = replace(
        first,
        context=replace(
            context,
            market=replace(context.market, amount=replace(context.market.amount, tick=D("0.1"))),
            policy=replace(context.policy, spacing=D(2)),
            buy_fee=FeeRule("BASE", D("0.05"), D("0.0001"), None, 1),
        ),
    )
    spec = replace(spec, allocations=(first,))
    settings = settings_for(spec)
    settings = replace(
        settings,
        policy=replace(
            settings.policy,
            dust=(
                DustRule(PoolKey(first.scope.venue, first.scope.account, "BASE"), D("0.05"), D(10)),
            ),
            dust_limit=D(10),
        ),
    )
    result: Any = run(spec, settings, max_cycles=2)
    assert result["completed_cycles"] == 1
    completed, active = result["cycle_outcomes"]
    assert completed.residuals[0].amount == D("0.05")
    assert completed.final.net == active.baseline.net == 1035
    assert active.phase is CyclePhase.ACTIVE
    state = result["final_states"][0]
    assert state.owned_base == D("0.05") and state.position_cost > 0
    assert not state.working
    assert not any(
        i * MINUTE + spec.start_ms >= completed.final.now_ms and k == "submit"
        for i, k, _ in result["events"]
    )
    assert result["completed_full_run_return_fraction"] is None


@pytest.mark.parametrize("limit", [0, -1, 33, True, 2.0])
def test_invalid_cycle_limit_refused_before_history_or_database(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch, limit: object
) -> None:
    spec = two_cycles(make_replay)

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("invalid limit reached history")

    monkeypatch.setattr(portfolio_replay, "validate", forbidden)
    with pytest.raises(DomainError):
        run(spec, settings_for(spec), max_cycles=cast(int, limit))


def test_extra_completion_intervals_count_toward_the_preflight_bound(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = replace(two_cycles(make_replay), interval_bars=100)
    monkeypatch.setattr(portfolio_replay, "MAX_INTERVAL_POINTS", 4)

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("excess completion points reached history")

    monkeypatch.setattr(portfolio_replay, "validate", forbidden)
    with pytest.raises(DomainError, match="interval point cap"):
        run(spec, settings_for(spec), max_cycles=3)


def test_cycle_limit_does_not_reset_shared_venue_history_capacity(make_replay: MakeReplay) -> None:
    spec = two_cycles(make_replay)
    settings = replace(settings_for(spec), venue_capacity=7)
    # One cycle uses six orders; preserving the owner means the second hits the total cap.
    assert run(spec, settings)["final_return"] == D("0.05")
    with pytest.raises(DomainError, match="historical submission failed"):
        run(spec, settings, max_cycles=2)


@pytest.mark.parametrize("stage", ["portfolio_begin:before_commit", "portfolio_begin:after_commit"])
def test_failed_restart_aborts_without_refunding_or_leaking_ledger(
    make_replay: MakeReplay, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    spec = two_cycles(make_replay)
    directory = tmp_path / "scratch"
    directory.mkdir()
    monkeypatch.setattr(portfolio_replay, "gettempdir", lambda: str(directory))
    reached = 0

    def fail(point: str) -> None:
        nonlocal reached
        if point == stage:
            reached += 1
            if reached == 2:
                raise OSError("injected restart failure")

    with pytest.raises(OSError, match="injected restart failure"):
        run(spec, settings_for(spec), max_cycles=2, fault=fail)
    assert reached == 2 and not list(directory.iterdir())


def test_source_changed_after_last_exit_still_refuses_series(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = two_cycles(make_replay)
    step = Engine.step
    changed = False

    async def mutate(engine: Engine, bars: tuple[Bar, ...]) -> Nav | None:
        nonlocal changed
        value = await step(engine, bars)
        if engine.cycle_number == 2 and (await engine.cycle()).phase is CyclePhase.COMPLETE:
            path = spec.allocations[0].source.path
            path.write_bytes(path.read_bytes()[:-1] + b"\x00")
            changed = True
        return value

    monkeypatch.setattr(Engine, "step", mutate)
    with pytest.raises(DomainError, match="source changed"):
        run(spec, settings_for(spec), max_cycles=2)
    assert changed


def test_scenario_series_compares_original_funds_and_keeps_all_cycles(
    make_replay: MakeReplay,
) -> None:
    spec = two_cycles(make_replay)
    result: Any = compare(spec, settings_for(spec), max_cycles=2)
    assert result["portfolio_cycles"] == 2
    assert result["observed_nav_range"]["available"]
    for summary, member in zip(result["summaries"], result["scenarios"], strict=True):
        series = member["result"]
        assert summary.starting_nav == 1000 and summary.observed_end_nav == 1100
        assert summary.observed_nav_return_fraction == D("0.1")
        assert summary.final_cycle_return_fraction == series["last_cycle_final_return"]
        assert summary.observed_nav_return_fraction != summary.final_cycle_return_fraction
        assert series["completed_cycles"] == len(series["cycle_outcomes"]) == 2


@pytest.mark.parametrize("compare_models", [False, True])
def test_cli_series_option_retained_inside_provenance(
    make_replay: MakeReplay, tmp_path: Path, compare_models: bool
) -> None:
    spec = two_cycles(make_replay)
    config, settings, output = (
        tmp_path / name for name in ("config.json", "portfolio.json", "result.json")
    )
    publish(config, bundle(spec))
    publish(settings, portfolio_bundle(settings_for(spec)))
    args = [
        "backtest",
        "--config",
        str(config),
        "--portfolio",
        str(settings),
        "--portfolio-cycles",
        "2",
        "--output",
        str(output),
    ]
    if compare_models:
        args.append("--compare-scenarios")
    assert main(args) == 0
    report = json.loads(output.read_bytes())
    result = report["result"]
    assert report["schema"] == "grid-research-run-v1"
    assert digest(result) == report["result_payload_sha256"]
    if compare_models:
        assert result["portfolio_cycles"] == 2
    else:
        assert result["schema"] == "grid-portfolio-series-v1"
        assert result["completed_full_run_return_fraction"] == "0.1"


@pytest.mark.parametrize("options", [[], ["--compare-lots"], ["--compare-scenarios"]])
def test_cycle_option_requires_portfolio_settings_without_publishing(
    make_replay: MakeReplay, tmp_path: Path, options: list[str]
) -> None:
    spec = two_cycles(make_replay)
    config, output = tmp_path / "config.json", tmp_path / "result.json"
    publish(config, bundle(spec))
    assert (
        main(
            [
                "backtest",
                "--config",
                str(config),
                "--portfolio-cycles",
                "1",
                "--output",
                str(output),
                *options,
            ]
        )
        == 2
    )
    assert not output.exists()
