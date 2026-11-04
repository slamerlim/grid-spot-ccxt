"""Chronological selection, unfavorable held-out outcomes and complete evidence retention."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path
from typing import Any

import pytest

from grid.cli import main
from grid.domain.numbers import DomainError
from grid.research import study
from grid.research.artifacts import digest, publish
from grid.research.config import bundle as replay_bundle
from grid.research.history import MINUTE, RECORD
from grid.research.portfolio_config import bundle as portfolio_bundle
from grid.research.replay import MODELS, Replay
from grid.research.replay import run as replay
from grid.research.study import Candidate, Fold, Plan, Scenario, effective, run
from grid.research.study_config import bundle, load

from .conftest import MakeReplay
from .test_portfolio_replay import settings_for


def fixture(make_replay: MakeReplay) -> tuple[Replay, Plan]:
    spec = make_replay(
        ((100, 101, 99, 100),) * 2
        + ((100, 111, 80, 100),)
        + ((100, 101, 99, 100),) * 2
        + ((100, 150, 99, 150),)
    )
    start = spec.start_ms
    return spec, Plan(
        (Candidate("narrow", D("0.1")), Candidate("wide", D("0.2"))),
        (Scenario("fixture", "touch_buy_priority", 1, D(0), D(0)),),
        (
            Fold(
                "first",
                start,
                start + 3 * MINUTE,
                start + 3 * MINUTE,
                start + 6 * MINUTE,
                "sideways",
                "up",
            ),
        ),
        "Synthetic fixed roster; no historical survivorship claim.",
        "Invented descriptive windows, not a causal regime classifier.",
    )


def test_training_selection_keeps_losing_test_and_all_sensitivity_results(
    make_replay: MakeReplay,
) -> None:
    spec, plan = fixture(make_replay)
    report: Any = run(spec, plan)
    row = report["fold_scenarios"][0]
    assert row["selected_candidate"] == "narrow"
    assert row["selected_training_return_fraction"] == D("0.02646")
    assert row["selected_test_return_fraction"] == D("0.01")
    assert row["testing"]["wide"]["final_nav_at_close"] == D(1020)
    assert row["testing"]["wide"]["final_nav_at_close"] > D(1010)
    for phase in ("training", "testing"):
        assert set(row[phase]) == {"narrow", "wide"}
        for member in row[phase].values():
            assert member["initial_allocated_nav"] == D(1000)
            assert member["cash_baseline_nav"] == D(1000)
            assert member["allocations"][0]["final_state"].quote_cash >= 0
    assert (
        digest({k: v for k, v in report.items() if k != "result_sha256"}) == report["result_sha256"]
    )


def test_test_prices_cannot_change_training_selection(make_replay: MakeReplay) -> None:
    spec, plan = fixture(make_replay)
    before: Any = run(spec, plan)
    path = spec.allocations[0].source.path
    # Dramatically losing held-out prices; training bytes and selection stay unchanged.
    data = path.read_bytes()
    path.write_bytes(
        data[: 5 * RECORD.size] + RECORD.pack(spec.start_ms + 5 * MINUTE, 100, 101, 20, 20)
    )
    after: Any = run(spec, plan)
    a, b = before["fold_scenarios"][0], after["fold_scenarios"][0]
    assert a["selected_candidate"] == b["selected_candidate"] == "narrow"
    assert a["selected_training_return_fraction"] == b["selected_training_return_fraction"]
    assert a["selected_test_return_fraction"] != b["selected_test_return_fraction"]
    assert b["selected_test_return_fraction"] < 0


def test_training_selection_is_frozen_before_any_test_replay(
    make_replay: MakeReplay,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, plan = fixture(make_replay)
    calls: list[int] = []
    scoring: list[int] = []
    original = study.return_fraction

    def observed(effective_spec: Replay) -> dict[str, object]:
        calls.append(effective_spec.start_ms)
        return replay(effective_spec)

    def score(result: dict[str, object]) -> D:
        config = result["config"]
        assert isinstance(config, Replay)
        if config.start_ms == spec.start_ms:
            scoring.append(len(calls))
        return original(result)

    monkeypatch.setattr(study, "replay", observed)
    monkeypatch.setattr(study, "return_fraction", score)
    run(spec, plan)
    assert calls == [spec.start_ms] * 2 + [plan.folds[0].test_start_ms] * 2
    # Both training candidates are scored to select the winner before first held-out replay.
    assert scoring.count(2) >= 3


def test_scenarios_are_separate_and_preserve_funding_rules(make_replay: MakeReplay) -> None:
    spec, plan = fixture(make_replay)
    plan = replace(
        plan,
        scenarios=tuple(
            Scenario(str(i), model, i + 1, D(i) / 1000, D(i) / 500)
            for i, model in enumerate(MODELS)
        ),
    )
    report: Any = run(spec, plan)
    assert len(report["fold_scenarios"]) == 3
    for row, scenario in zip(report["fold_scenarios"], plan.scenarios, strict=True):
        for candidate in plan.candidates:
            member = row["training"][candidate.name]
            config = member["config"]
            assert config.capital == spec.capital
            assert config.model == scenario.model
            assert config.latency_bars == scenario.latency_bars
            a, original = config.allocations[0], spec.allocations[0]
            assert a.quote_cash == original.quote_cash
            assert a.context.market == original.context.market
            assert a.context.buy_fee == replace(
                original.context.buy_fee, rate=scenario.buy_fee_rate
            )
            assert a.context.sell_fee == replace(
                original.context.sell_fee, rate=scenario.sell_fee_rate
            )
            assert a.context.policy == replace(original.context.policy, spacing=candidate.spacing)


def test_offsets_rolling_folds_and_canonical_order(make_replay: MakeReplay) -> None:
    spec, plan = fixture(make_replay)
    source = spec.allocations[0].source
    original = source.path.read_bytes()
    # Add an unselected prefix and additional valid minutes. Index slicing must honor offset.
    source.path.write_bytes(
        RECORD.pack(spec.start_ms - MINUTE, 100, 101, 99, 100)
        + original
        + b"".join(RECORD.pack(spec.end_ms + i * MINUTE, 100, 101, 99, 100) for i in range(3))
    )
    spec = replace(
        spec,
        end_ms=spec.end_ms + 3 * MINUTE,
        allocations=(replace(spec.allocations[0], source=replace(source, offset=1, count=9)),),
    )
    f = plan.folds[0]
    later = Fold(
        "second",
        f.train_start_ms,
        f.test_end_ms,
        f.test_end_ms,
        spec.end_ms,
        "unclassified",
        "sideways",
    )
    plan = replace(plan, folds=(later, f))
    report: Any = run(spec, plan)
    assert [r["fold"] for r in report["fold_scenarios"]] == ["first", "second"]
    tested = report["fold_scenarios"][1]["testing"]["narrow"]
    assert tested["inputs"][0].source.offset == 7
    assert tested["inputs"][0].first_ms == f.test_end_ms
    assert tested["initial_allocated_nav"] == D(1000)
    with localcontext() as context:
        context.prec = 5
        assert (
            run(spec, replace(plan, folds=(f, later), candidates=plan.candidates[::-1])) == report
        )


def test_name_ties_are_stable_and_never_rank_scenarios(make_replay: MakeReplay) -> None:
    spec, plan = fixture(make_replay)
    plan = replace(plan, candidates=(Candidate("z", D("0.1")), Candidate("a", D("0.1"))))
    report: Any = run(spec, plan)
    assert report["fold_scenarios"][0]["selected_candidate"] == "a"


@pytest.mark.parametrize("invalid", ["overlap", "backward", "short", "fractional", "outside"])
def test_temporal_leakage_and_invalid_windows_refused(
    make_replay: MakeReplay, invalid: str
) -> None:
    spec, plan = fixture(make_replay)
    fold = plan.folds[0]
    with pytest.raises(DomainError):
        if invalid == "overlap":
            replace(plan, folds=(fold, replace(fold, name="duplicate")))
        elif invalid == "backward":
            replace(fold, train_end_ms=fold.test_start_ms + MINUTE)
        elif invalid == "short":
            replace(fold, test_end_ms=fold.test_start_ms + MINUTE)
        elif invalid == "fractional":
            replace(fold, train_start_ms=fold.train_start_ms + 1)
        else:
            run(spec, replace(plan, folds=(replace(fold, test_end_ms=spec.end_ms + MINUTE),)))


@pytest.mark.parametrize("after", [1, 4])
def test_changed_history_during_or_after_last_case_refuses(
    make_replay: MakeReplay,
    monkeypatch: pytest.MonkeyPatch,
    after: int,
) -> None:
    spec, plan = fixture(make_replay)
    calls = 0

    def change(effective_spec: Replay) -> dict[str, object]:
        nonlocal calls
        result = replay(effective_spec)
        calls += 1
        if calls == after:
            path = effective_spec.allocations[0].source.path
            stamp = path.stat()
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
        return result

    monkeypatch.setattr(study, "replay", change)
    with pytest.raises(DomainError, match="history changed"):
        run(spec, plan)
    assert calls == after


def test_gaps_in_unused_span_are_not_silently_excluded(make_replay: MakeReplay) -> None:
    spec, plan = fixture(make_replay)
    f = plan.folds[0]
    plan = replace(plan, folds=(replace(f, train_end_ms=f.train_end_ms - MINUTE),))
    path = spec.allocations[0].source.path
    data = bytearray(path.read_bytes())
    data[2 * RECORD.size : 3 * RECORD.size] = RECORD.pack(spec.start_ms + 2 * MINUTE, 0, 0, 0, 0)
    path.write_bytes(data)
    with pytest.raises(DomainError, match="complete eligible"):
        run(spec, plan)


def test_limits_and_failed_member_never_publish_partial_results(
    make_replay: MakeReplay,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, plan = fixture(make_replay)
    config, study_path, output = (
        tmp_path / name for name in ("config.json", "study.json", "out.json")
    )
    publish(config, replay_bundle(spec))
    publish(study_path, bundle(plan))
    args = [
        "backtest",
        "--config",
        str(config),
        "--study",
        str(study_path),
        "--output",
        str(output),
    ]
    for limit in ("MAX_RUNS", "MAX_BAR_STEPS", "MAX_RESULT_BYTES"):
        with monkeypatch.context() as patch:
            patch.setattr(study, limit, 1)
            assert main(args) == 2 and not output.exists()

    def fail(effective_spec: Replay) -> dict[str, object]:
        if effective_spec.start_ms == plan.folds[0].test_start_ms:
            raise DomainError("invented held-out failure")
        return replay(effective_spec)

    monkeypatch.setattr(study, "replay", fail)
    assert main(args) == 2 and not output.exists()


def test_strict_plan_and_separate_cli_process(make_replay: MakeReplay, tmp_path: Path) -> None:
    spec, plan = fixture(make_replay)
    config, study_path, output = (
        tmp_path / name for name in ("config.json", "study.json", "out.json")
    )
    publish(config, replay_bundle(spec))
    publish(study_path, bundle(plan))
    assert load(study_path) == plan
    source = spec.allocations[0].source.path
    before = (source.read_bytes(), source.stat().st_mtime_ns, study_path.read_bytes())
    args = [
        "backtest",
        "--config",
        str(config),
        "--study",
        str(study_path),
        "--output",
        str(output),
    ]
    process = subprocess.run(
        [sys.executable, "-m", "grid", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(output.read_bytes())
    assert report["schema"] == "grid-research-run-v1"
    assert report["result"]["schema"] == "grid-walk-forward-study-v1"
    assert digest(report["result"]) == report["result_payload_sha256"]
    assert (source.read_bytes(), source.stat().st_mtime_ns, study_path.read_bytes()) == before
    args[-1] = str(tmp_path / "unused.json")
    for flag in ("--compare-lots", "--compare-scenarios"):
        with pytest.raises(SystemExit) as refused:
            main([*args, flag])
        assert refused.value.code == 2
    portfolio = tmp_path / "portfolio.json"
    publish(portfolio, portfolio_bundle(settings_for(spec)))
    assert main([*args, "--portfolio", str(portfolio)]) == 2
    assert not Path(args[-1]).exists()
    for content in (
        '{"schema": "grid-study-plan-v1", "schema": "grid-study-plan-v1"}',
        json.dumps({**bundle(plan), "unknown": True}),
        json.dumps({**bundle(plan), "candidates": [{"name": "float", "spacing": 0.1}]}),
        json.dumps({**bundle(plan), "scenarios": []}),
    ):
        study_path.write_text(content)
        with pytest.raises(DomainError):
            load(study_path)


def test_slice_matches_fresh_engine_and_keeps_unrealized_losses(make_replay: MakeReplay) -> None:
    spec, plan = fixture(make_replay)
    report: Any = run(spec, plan)
    fold, candidate, scenario = plan.folds[0], plan.candidates[0], plan.scenarios[0]
    assert report["fold_scenarios"][0]["testing"][candidate.name] == replay(
        effective(spec, candidate, scenario, fold.test_start_ms, fold.test_end_ms)
    )
    # Score the total owned NAV, never just the profitable realized fills.
    assert study.return_fraction(
        {"initial_allocated_nav": D(1000), "final_nav_at_close": D(900), "realized_pnl": D(50)}
    ) == D("-0.1")
