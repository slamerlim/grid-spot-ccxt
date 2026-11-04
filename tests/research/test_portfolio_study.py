"""Common-horizon portfolio selection with complete, finite-funded held-out evidence."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest

from grid.cli import main
from grid.domain.numbers import DomainError
from grid.portfolio.model import CyclePhase
from grid.research import portfolio_replay, study
from grid.research.artifacts import digest, publish
from grid.research.config import bundle as replay_bundle
from grid.research.history import MINUTE, RECORD, Bar
from grid.research.portfolio_config import Settings
from grid.research.portfolio_config import bundle as portfolio_bundle
from grid.research.portfolio_replay import Engine
from grid.research.portfolio_replay import run as portfolio
from grid.research.replay import Replay
from grid.research.study import Candidate, Fold, Plan, Scenario, run
from grid.research.study_config import bundle

from .conftest import MakeReplay
from .test_portfolio_replay import exit_scenario, settings_for
from .test_portfolio_series import two_cycles

pytestmark = pytest.mark.usefixtures("async_sockets")


def fixture(make_replay: MakeReplay) -> tuple[Replay, Plan]:
    spec = make_replay(
        ((100, 101, 99, 100),) * 2
        + ((115, 116, 114, 115),) * 5
        + ((100, 101, 99, 100),) * 2
        + ((40, 41, 39, 40),) * 5
    )
    start, middle, end = spec.start_ms, spec.start_ms + 7 * MINUTE, spec.end_ms
    return spec, Plan(
        (Candidate("narrow", D("0.1")), Candidate("wide", D("0.2"))),
        (Scenario("fixed", "strict_skip_ambiguous", 1, D(0), D(0)),),
        (Fold("first", start, middle, middle, end, "up", "down"),),
        "Invented fixed spot roster.",
        "Invented descriptive regimes.",
    )


def test_fixed_horizon_values_cash_and_benchmarks_after_execution_stops(
    make_replay: MakeReplay,
) -> None:
    spec = replace(exit_scenario(make_replay), interval_bars=100)
    settings = settings_for(spec)
    old: Any = portfolio(spec, settings)
    full: Any = portfolio(spec, settings, through_end=True)
    assert full["schema"] == "grid-portfolio-series-v1"
    assert full["evaluation_horizon"] == "history_end"
    assert full["last_cycle"] == old["cycle"]
    assert (
        full["execution_complete_ms_exclusive"] == old["simulation_end_ms_exclusive"] < spec.end_ms
    )
    assert full["post_completion_valuation_bars"] == 1
    assert full["simulation_end_ms_exclusive"] == spec.end_ms
    assert full["last_nav"].now_ms == spec.end_ms and full["last_nav"].net == 1050
    assert full["last_cycle"].final.now_ms < full["last_nav"].now_ms
    assert full["completed_cycles_return_fraction_at_exit"] == D("0.05")
    assert full["completed_full_run_return_fraction"] == D("0.05")
    assert full["stop_reason"] == "history_exhausted"
    assert len(full["intervals"]) == 2  # The completion and EOF each need a retained point.
    assert full["benchmarks"]["last"]["end_ms_exclusive"] == spec.end_ms
    for key in ("orders", "fills", "events", "synthetic_trace_sha256", "posted_performance"):
        assert full[key] == old[key]


def test_unknown_valuation_after_completion_does_not_reuse_exit_nav(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = exit_scenario(make_replay)
    observe = Engine.observe

    async def missing(engine: Engine, bars: tuple[Bar, ...]) -> None:
        await observe(engine, bars)
        if engine.index == 8:
            assert engine.facts is not None
            # Inject unavailable current account/ownership evidence after the exit receipt.
            engine.facts = replace(engine.facts, accounts_verified=False)

    monkeypatch.setattr(Engine, "observe", missing)
    full: Any = portfolio(spec, settings_for(spec), through_end=True)
    assert full["last_cycle"].phase is CyclePhase.COMPLETE
    assert full["last_cycle"].final.net == 1050
    assert full["last_nav"] is None
    assert full["observed_full_run_return_fraction"] is None
    assert full["completed_full_run_return_fraction"] is None
    assert full["completed_cycles_return_fraction_at_exit"] == D("0.05")
    assert full["allocation_risk"]["allocations"][0]["last"].net_nav is None


def test_fixed_horizon_continues_requested_cycles_then_only_values(make_replay: MakeReplay) -> None:
    spec = two_cycles(make_replay)
    full: Any = portfolio(spec, settings_for(spec), max_cycles=2, through_end=True)
    assert full["completed_cycles"] == 2
    assert full["post_completion_valuation_bars"] == 3
    assert full["last_nav"].net == 1100 and full["last_nav"].now_ms == spec.end_ms
    assert full["execution_complete_ms_exclusive"] == spec.start_ms + 16 * MINUTE
    assert full["observed_full_run_return_fraction"] == D("0.1")
    assert full["benchmarks"]["last"]["buy_hold"]["return_fraction"] < D("0.01")
    assert len(full["fills"]) == 4


def test_horizon_preflight_counts_final_completion_and_eof(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = replace(exit_scenario(make_replay), interval_bars=100)
    monkeypatch.setattr(portfolio_replay, "MAX_INTERVAL_POINTS", 2)

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("preflight bound reached history")

    monkeypatch.setattr(portfolio_replay, "validate", forbidden)
    with pytest.raises(DomainError, match="interval point cap"):
        portfolio(spec, settings_for(spec), through_end=True)


@pytest.mark.parametrize("flag", [1, "true", None])
def test_horizon_requires_an_explicit_boolean(make_replay: MakeReplay, flag: object) -> None:
    spec = exit_scenario(make_replay)
    with pytest.raises(DomainError, match="horizon flag"):
        portfolio(spec, settings_for(spec), through_end=cast(bool, flag))


def test_portfolio_study_selects_training_nav_and_retains_held_out_losses(
    make_replay: MakeReplay,
) -> None:
    spec, plan = fixture(make_replay)
    report: Any = run(spec, plan, settings_for(spec))
    assert report["schema"] == "grid-portfolio-walk-forward-study-v1"
    row = report["fold_scenarios"][0]
    assert row["selected_candidate"] == "wide" and row["selection_status"] == "selected"
    assert row["selected_training_return_fraction"] == D("0.015")
    assert row["selected_test_return_fraction"] < 0
    assert row["selection_unavailable_candidates"] == ()
    for phase, end in (
        ("training", plan.folds[0].train_end_ms),
        ("testing", plan.folds[0].test_end_ms),
    ):
        assert set(row[phase]) == {"narrow", "wide"}
        for member in row[phase].values():
            assert member["initial_nav"].net == 1000
            assert member["simulation_end_ms_exclusive"] == end
            assert member["benchmarks"]["initial_owned_nav"] == 1000
    assert (
        digest({k: v for k, v in report.items() if k != "result_sha256"}) == report["result_sha256"]
    )


def test_different_exit_times_still_receive_the_same_training_horizon(
    make_replay: MakeReplay,
) -> None:
    base = exit_scenario(make_replay)
    first = base.allocations[0]
    data = first.source.path.read_bytes()
    count = first.source.count
    first.source.path.write_bytes(
        data + b"".join(RECORD.pack(base.end_ms + i * MINUTE, 100, 101, 99, 100) for i in range(3))
    )
    spec = replace(
        base,
        end_ms=base.end_ms + 3 * MINUTE,
        allocations=(replace(first, source=replace(first.source, count=count + 3)),),
    )
    plan = Plan(
        (Candidate("dense", D(2)), Candidate("sparse", D(4))),
        (Scenario("fixed", "strict_skip_ambiguous", 1, D(0), D(0)),),
        (Fold("first", base.start_ms, base.end_ms, base.end_ms, spec.end_ms, "up", "sideways"),),
        "Invented roster.",
        "Invented regimes.",
    )
    report: Any = run(spec, plan, settings_for(spec))
    row = report["fold_scenarios"][0]
    members = tuple(row["training"].values())
    assert len({r["execution_complete_ms_exclusive"] for r in members}) == 2
    assert {r["simulation_end_ms_exclusive"] for r in members} == {base.end_ms}
    assert {r["last_nav"].net for r in members} == {D(1050)}
    assert row["selected_candidate"] == "dense"  # Equal end NAV: declared name tie-break.


def test_study_carries_funds_between_cycles_but_resets_between_windows(
    make_replay: MakeReplay,
) -> None:
    base = two_cycles(make_replay)
    first = base.allocations[0]
    data = first.source.path.read_bytes()
    count = first.source.count
    repeated = []
    for index in range(count):
        _, *prices = RECORD.unpack_from(data, index * RECORD.size)
        repeated.append(RECORD.pack(base.end_ms + index * MINUTE, *prices))
    first.source.path.write_bytes(data + b"".join(repeated))
    spec = replace(
        base,
        end_ms=base.end_ms + count * MINUTE,
        allocations=(replace(first, source=replace(first.source, count=2 * count)),),
    )
    plan = Plan(
        (Candidate("fixed", D(2)),),
        (Scenario("fixed", "strict_skip_ambiguous", 1, D(0), D(0)),),
        (Fold("first", base.start_ms, base.end_ms, base.end_ms, spec.end_ms, "up", "up"),),
        "Invented roster.",
        "Invented regimes.",
    )
    report: Any = run(spec, plan, settings_for(spec), max_cycles=2)
    row = report["fold_scenarios"][0]
    for phase in ("training", "testing"):
        member = row[phase]["fixed"]
        assert member["completed_cycles"] == 2
        assert [c.baseline.net for c in member["cycle_outcomes"]] == [D(1000), D(1050)]
        assert member["observed_full_run_return_fraction"] == D("0.1")
    assert (
        row["selected_training_return_fraction"] == row["selected_test_return_fraction"] == D("0.1")
    )


def test_unknown_training_nav_withholds_selection_without_discarding_members(
    make_replay: MakeReplay,
) -> None:
    spec, plan = fixture(make_replay)
    settings = settings_for(spec)
    settings = replace(settings, liquidity=(replace(settings.liquidity[0], nav_depth=D("0.5")),))
    report: Any = run(spec, plan, settings)
    row = report["fold_scenarios"][0]
    assert row["selected_candidate"] is None
    assert row["selected_training_return_fraction"] is row["selected_test_return_fraction"] is None
    assert row["selection_status"] == "unknown_training_nav"
    assert row["selection_unavailable_candidates"] == ("narrow", "wide")
    assert set(row["training"]) == set(row["testing"]) == {"narrow", "wide"}


def test_selection_freezes_before_test_and_unknown_test_does_not_replace_winner(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, plan = fixture(make_replay)
    original = portfolio
    scored: list[int] = []
    calls = 0
    score_fn = study.score

    def observed_score(result: dict[str, object], end: int, portfolio: bool) -> tuple[D, D | None]:
        if end == plan.folds[0].train_end_ms:
            scored.append(calls)
        return score_fn(result, end, portfolio)

    def observed(
        config: Replay, settings: Settings, *, max_cycles: int, through_end: bool
    ) -> dict[str, object]:
        nonlocal calls
        if config.start_ms == plan.folds[0].test_start_ms:
            assert scored[:2] == [1, 2]
            settings = replace(
                settings, liquidity=(replace(settings.liquidity[0], nav_depth=D("0.5")),)
            )
        calls += 1
        return original(config, settings, max_cycles=max_cycles, through_end=through_end)

    monkeypatch.setattr(study, "score", observed_score)
    monkeypatch.setattr(study, "portfolio_replay", observed)
    row: Any = run(spec, plan, settings_for(spec))["fold_scenarios"]
    assert row[0]["selected_candidate"] == "wide"
    assert row[0]["selected_training_return_fraction"] == D("0.015")
    assert row[0]["selected_test_return_fraction"] is None


def test_known_candidate_cannot_win_by_excluding_unknown_training_peer(
    make_replay: MakeReplay,
) -> None:
    spec, plan = fixture(make_replay)
    fold = plan.folds[0]
    plan = replace(plan, folds=(replace(fold, train_end_ms=fold.train_start_ms + 3 * MINUTE),))
    settings = settings_for(spec)
    settings = replace(settings, liquidity=(replace(settings.liquidity[0], nav_depth=D("0.5")),))
    report: Any = run(spec, plan, settings)
    row = report["fold_scenarios"][0]
    assert row["training"]["narrow"]["last_nav"].net == 1010
    assert row["training"]["wide"]["last_nav"] is None
    assert row["selection_unavailable_candidates"] == ("wide",)
    assert row["selected_candidate"] is None
    assert set(row["testing"]) == {"narrow", "wide"}


def test_held_out_prices_and_candidate_order_do_not_rewrite_training_choice(
    make_replay: MakeReplay,
) -> None:
    spec, plan = fixture(make_replay)
    settings = settings_for(spec)
    before: Any = run(spec, plan, settings)
    source = spec.allocations[0].source.path
    data = source.read_bytes()
    source.write_bytes(
        data[: 7 * RECORD.size]
        + b"".join(RECORD.pack(spec.start_ms + i * MINUTE, 100, 101, 99, 100) for i in range(7, 14))
    )
    with localcontext() as precision:
        precision.prec = 5
        after: Any = run(spec, replace(plan, candidates=plan.candidates[::-1]), settings)
    first, last = before["fold_scenarios"][0], after["fold_scenarios"][0]
    assert first["selected_candidate"] == last["selected_candidate"] == "wide"
    assert first["selected_training_return_fraction"] == last["selected_training_return_fraction"]
    assert first["selected_test_return_fraction"] < last["selected_test_return_fraction"]


@pytest.mark.parametrize("damage", ["endpoint", "horizon", "missing", "baseline"])
def test_study_refuses_incompatible_evidence_instead_of_ranking_it(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    spec, plan = fixture(make_replay)
    original = portfolio
    calls = 0

    def changed(
        config: Replay, settings: Settings, *, max_cycles: int, through_end: bool
    ) -> dict[str, object]:
        nonlocal calls
        result = original(config, settings, max_cycles=max_cycles, through_end=through_end)
        calls += 1
        if calls == 2:
            if damage == "endpoint":
                result["simulation_end_ms_exclusive"] = config.end_ms - MINUTE
            elif damage == "horizon":
                result["evaluation_horizon"] = "cycle_completion"
            elif damage == "missing":
                del result["last_nav"]
            else:
                initial: Any = result["initial_nav"]
                result["initial_nav"] = replace(initial, net=initial.net + 1)
        return result

    monkeypatch.setattr(study, "portfolio_replay", changed)
    with pytest.raises(DomainError, match="horizon|baseline"):
        run(spec, plan, settings_for(spec))
    assert calls == 2


def test_late_effective_bounds_fail_before_any_history_or_ledger(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, plan = fixture(make_replay)
    original = portfolio_replay.checked
    calls = 0

    def checked(
        config: Replay, settings: Settings, limit: int, *, through_end: bool
    ) -> tuple[Replay, Settings]:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise DomainError("injected final preflight constraint")
        return original(config, settings, limit, through_end=through_end)

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("replay or history entered before complete preflight")

    monkeypatch.setattr(study, "checked_portfolio", checked)
    monkeypatch.setattr(study, "validate", forbidden)
    monkeypatch.setattr(study, "portfolio_replay", forbidden)
    with pytest.raises(DomainError, match="final preflight"):
        run(spec, plan, settings_for(spec))
    assert calls == 4


@pytest.mark.parametrize("failure", ["history", "member", "size"])
def test_portfolio_study_failures_never_publish_a_partial_report(
    make_replay: MakeReplay, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    spec, plan = fixture(make_replay)
    config, settings, study_path, output = (
        tmp_path / name for name in ("config.json", "settings.json", "study.json", "out.json")
    )
    publish(config, replay_bundle(spec))
    publish(settings, portfolio_bundle(settings_for(spec)))
    publish(study_path, bundle(plan))
    original = portfolio

    def failed(
        config: Replay, settings: Settings, *, max_cycles: int, through_end: bool
    ) -> dict[str, object]:
        if failure == "member":
            raise DomainError("injected portfolio failure")
        result = original(config, settings, max_cycles=max_cycles, through_end=through_end)
        if failure == "history":
            path = config.allocations[0].source.path
            stamp = path.stat()
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
        return result

    monkeypatch.setattr(study, "portfolio_replay", failed)
    if failure == "size":
        monkeypatch.setattr(study, "MAX_RESULT_BYTES", 1)
    assert (
        main(
            [
                "backtest",
                "--config",
                str(config),
                "--portfolio",
                str(settings),
                "--study",
                str(study_path),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()


def test_actual_cli_process_accepts_portfolio_study_and_cycle_limit(
    make_replay: MakeReplay, tmp_path: Path
) -> None:
    spec, plan = fixture(make_replay)
    plan = replace(plan, candidates=plan.candidates[:1])
    config, settings, study_path, output = (
        tmp_path / name for name in ("config.json", "settings.json", "study.json", "out.json")
    )
    publish(config, replay_bundle(spec))
    publish(settings, portfolio_bundle(settings_for(spec)))
    publish(study_path, bundle(plan))
    inputs = (config, settings, study_path, spec.allocations[0].source.path)
    before = tuple((p.read_bytes(), p.stat().st_mtime_ns) for p in inputs)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "grid",
            "backtest",
            "--config",
            str(config),
            "--portfolio",
            str(settings),
            "--portfolio-cycles",
            "2",
            "--study",
            str(study_path),
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_bytes())
    assert report["schema"] == "grid-research-run-v1"
    assert report["result"]["schema"] == "grid-portfolio-walk-forward-study-v1"
    assert report["result"]["portfolio_cycles"] == 2
    assert report["result_payload_sha256"] == digest(report["result"])
    assert tuple((p.read_bytes(), p.stat().st_mtime_ns) for p in inputs) == before


def test_portfolio_cycle_limit_needs_portfolio_settings(make_replay: MakeReplay) -> None:
    spec, plan = fixture(make_replay)
    with pytest.raises(DomainError, match="portfolio settings"):
        run(spec, plan, max_cycles=2)
