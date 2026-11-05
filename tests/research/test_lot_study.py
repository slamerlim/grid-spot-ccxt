"""Independent-lot studies share inputs and funding without selecting from test returns."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path
from typing import Any, NoReturn

import pytest

from grid.cli import main
from grid.domain.numbers import DomainError
from grid.research import study
from grid.research.artifacts import digest, publish
from grid.research.config import bundle as replay_bundle
from grid.research.history import MINUTE, RECORD
from grid.research.lot_reference import run as reference
from grid.research.replay import MODELS, Replay
from grid.research.replay import run as basket_replay
from grid.research.study import Fold, Scenario, compare_lots, effective, run
from grid.research.study_config import bundle

from .conftest import MakeReplay
from .test_portfolio_replay import settings_for
from .test_study import fixture


def test_paired_study_matches_standalone_members_and_retains_all_outcomes(
    make_replay: MakeReplay,
) -> None:
    spec, plan = fixture(make_replay)
    report: Any = compare_lots(spec, plan)
    assert report["schema"] == "grid-lot-walk-forward-comparison-v1"
    assert report["basket_single_sell"] == run(spec, plan)
    second = report["independent_lots"]
    assert second["schema"] == "grid-independent-lot-walk-forward-study-v1"
    assert second == run(spec, plan, policy="independent_lots")
    assert second["config_sha256"] != report["basket_single_sell"]["config_sha256"]
    fold, scenario = plan.folds[0], plan.scenarios[0]
    for phase, start, end in [
        ("training", fold.train_start_ms, fold.train_end_ms),
        ("testing", fold.test_start_ms, fold.test_end_ms),
    ]:
        for candidate in plan.candidates:
            member = second["fold_scenarios"][0][phase][candidate.name]
            assert member == reference(effective(spec, candidate, scenario, start, end))
            first = report["basket_single_sell"]["fold_scenarios"][0][phase][candidate.name]
            assert member["inputs"] == first["inputs"]
            assert member["initial_allocated_nav"] == first["initial_allocated_nav"] == D(1000)
            assert member["config"] == first["config"]
            assert member["intervals"][-1]["end_ms_exclusive"] == end
            assert report["comparisons"][0]["matched_candidate_return_differences"][phase][
                candidate.name
            ] == (member["final_nav_at_close"] - first["final_nav_at_close"]) / D(1000)
    comparison = report["comparisons"][0]
    assert (
        comparison["reference_minus_basket_selected_test_return_fraction"]
        == comparison["reference_selected_test_return_fraction"]
        - comparison["basket_selected_test_return_fraction"]
    )
    assert (
        digest({k: v for k, v in report.items() if k != "result_sha256"}) == report["result_sha256"]
    )


def test_held_out_losses_do_not_change_either_training_selection(make_replay: MakeReplay) -> None:
    spec, plan = fixture(make_replay)
    before: Any = compare_lots(spec, plan)
    path = spec.allocations[0].source.path
    original = path.read_bytes()
    path.write_bytes(
        original[: 5 * RECORD.size] + RECORD.pack(spec.start_ms + 5 * MINUTE, 100, 101, 20, 20)
    )
    after: Any = compare_lots(spec, plan)
    for name in ("basket_single_sell", "independent_lots"):
        a, b = before[name]["fold_scenarios"][0], after[name]["fold_scenarios"][0]
        assert a["selected_candidate"] == b["selected_candidate"]
        assert a["selected_training_return_fraction"] == b["selected_training_return_fraction"]
        assert b["selected_test_return_fraction"] < 0
        assert b["selected_test_return_fraction"] != a["selected_test_return_fraction"]
        assert b["testing"]["narrow"]["final_nav_at_close"] < D(1000)
        assert b["testing"]["wide"]["final_nav_at_close"] < D(1000)


def test_each_policy_selects_from_its_own_training_before_its_test(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, plan = fixture(make_replay)
    original_basket, original_reference = basket_replay, reference
    selection = study.return_fraction
    calls: list[tuple[str, int]] = []
    decisions: dict[str, bool] = {
        "grid-research-v1": False,
        "grid-independent-lot-research-v1": False,
    }

    def basket(config: Replay) -> dict[str, object]:
        if config.start_ms == plan.folds[0].test_start_ms:
            assert decisions["grid-research-v1"]
        calls.append(("basket", config.start_ms))
        return original_basket(config)

    def independent(config: Replay) -> dict[str, object]:
        if config.start_ms == plan.folds[0].test_start_ms:
            assert decisions["grid-independent-lot-research-v1"]
        calls.append(("reference", config.start_ms))
        return original_reference(config)

    def scored(result: dict[str, object]) -> D:
        config = result["config"]
        assert isinstance(config, Replay)
        if config.start_ms == spec.start_ms:
            key = str(result["schema"])
            kind = "basket" if key == "grid-research-v1" else "reference"
            if sum(c == (kind, spec.start_ms) for c in calls) == 2:
                decisions[key] = True
        return selection(result)

    monkeypatch.setattr(study, "replay", basket)
    monkeypatch.setattr(study, "reference_replay", independent)
    monkeypatch.setattr(study, "return_fraction", scored)
    compare_lots(spec, plan)
    assert len(calls) == 8 and all(decisions.values())


def test_policies_can_choose_different_parameters_from_training(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, plan = fixture(make_replay)
    original = reference

    def declared(config: Replay) -> dict[str, object]:
        result = original(config)
        if config.start_ms == spec.start_ms:
            result["final_nav_at_close"] = (
                D(1030) if config.allocations[0].context.policy.spacing == D(".2") else D(1010)
            )
        return result

    # Controlled engine outcomes isolate selector coupling; actual accounting tested separately.
    monkeypatch.setattr(study, "reference_replay", declared)
    result: Any = compare_lots(spec, plan)
    row = result["comparisons"][0]
    assert row["basket_selected_candidate"] == "narrow"
    assert row["reference_selected_candidate"] == "wide"
    assert row["reference_selected_test_return_fraction"] == D(".02")


def test_models_fees_latency_and_order_are_explicit_and_reproducible(
    make_replay: MakeReplay,
) -> None:
    spec, plan = fixture(make_replay)
    plan = replace(
        plan,
        scenarios=tuple(
            Scenario(str(i), model, i + 1, D(i) / 1000, D(i) / 500)
            for i, model in enumerate(MODELS)
        ),
    )
    result: Any = compare_lots(spec, plan)
    with localcontext() as context:
        context.prec = 4
        assert (
            compare_lots(
                spec,
                replace(plan, candidates=plan.candidates[::-1], scenarios=plan.scenarios[::-1]),
            )
            == result
        )
    for i, scenario in enumerate(plan.scenarios):
        for policy in ("basket_single_sell", "independent_lots"):
            row = result[policy]["fold_scenarios"][i]
            for phase in ("training", "testing"):
                for candidate in plan.candidates:
                    config = row[phase][candidate.name]["config"]
                    assert (
                        config.model == scenario.model
                        and config.latency_bars == scenario.latency_bars
                    )
                    assert config.allocations[0].context.buy_fee.rate == scenario.buy_fee_rate
                    assert config.allocations[0].context.sell_fee.rate == scenario.sell_fee_rate
                    assert config.capital == spec.capital


def test_rolling_folds_and_nonzero_offsets_reset_funding(make_replay: MakeReplay) -> None:
    spec, plan = fixture(make_replay)
    source = spec.allocations[0].source
    raw = source.path.read_bytes()
    source.path.write_bytes(
        RECORD.pack(spec.start_ms - MINUTE, 100, 101, 99, 100)
        + raw
        + b"".join(RECORD.pack(spec.end_ms + i * MINUTE, 100, 101, 99, 100) for i in range(3))
    )
    spec = replace(
        spec,
        end_ms=spec.end_ms + 3 * MINUTE,
        allocations=(replace(spec.allocations[0], source=replace(source, offset=1, count=9)),),
    )
    first = plan.folds[0]
    plan = replace(
        plan,
        folds=(
            Fold(
                "second",
                first.train_start_ms,
                first.test_end_ms,
                first.test_end_ms,
                spec.end_ms,
                "up",
                "sideways",
            ),
            first,
        ),
    )
    result: Any = compare_lots(spec, plan)
    for policy in ("basket_single_sell", "independent_lots"):
        assert [r["fold"] for r in result[policy]["fold_scenarios"]] == ["first", "second"]
        for member in result[policy]["fold_scenarios"][1]["testing"].values():
            assert member["initial_allocated_nav"] == D(1000)
            assert member["inputs"][0].source.offset == 7
            assert member["inputs"][0].first_ms == first.test_end_ms


@pytest.mark.parametrize(
    "limit,value", [("MAX_RUNS", 4), ("MAX_BAR_STEPS", 20), ("MAX_POCKETS", 0)]
)
def test_combined_work_caps_precede_all_history_reads(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch, limit: str, value: int
) -> None:
    spec, plan = fixture(make_replay)

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("study read before combined workload preflight")

    monkeypatch.setattr(study, limit, value)
    monkeypatch.setattr(study, "validate", forbidden)
    with pytest.raises(DomainError, match="cap"):
        compare_lots(spec, plan)


@pytest.mark.parametrize(
    "fault", ["inputs", "cases", "candidate", "config", "funding", "winner", "metric"]
)
def test_comparison_refuses_inconsistent_policy_evidence(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    spec, plan = fixture(make_replay)
    original = study.run

    def corrupted(*args: Any, **kwargs: Any) -> dict[str, object]:
        result: Any = original(*args, **kwargs)
        if kwargs.get("policy") != "independent_lots":
            return result  # type: ignore[no-any-return]
        row = result["fold_scenarios"][0]
        if fault == "inputs":
            result["inputs"] = (replace(result["inputs"][0], accepted=1),)
        elif fault == "cases":
            result["fold_scenarios"] = ()
        elif fault == "candidate":
            del row["testing"]["wide"]
        elif fault == "config":
            row["testing"]["wide"]["config"] = replace(
                row["testing"]["wide"]["config"], code_revision="different"
            )
        elif fault == "funding":
            row["testing"]["wide"]["initial_allocated_nav"] = D(2000)
        elif fault == "winner":
            row["selected_candidate"] = "missing"
        else:
            row["selected_test_return_fraction"] = D(99)
        return result  # type: ignore[no-any-return]

    monkeypatch.setattr(study, "run", corrupted)
    with pytest.raises(DomainError):
        compare_lots(spec, plan)


@pytest.mark.parametrize("when", ["between", "final"])
def test_source_change_between_policies_or_after_last_case_fails(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    spec, plan = fixture(make_replay)
    original = study.run
    calls = 0

    def changed(*args: Any, **kwargs: Any) -> dict[str, object]:
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == (1 if when == "between" else 2):
            path = spec.allocations[0].source.path
            stamp = path.stat()
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000000))
        return result

    monkeypatch.setattr(study, "run", changed)
    with pytest.raises(DomainError, match="history changed"):
        compare_lots(spec, plan)


def test_reference_policy_rejects_portfolio_and_invalid_mode(make_replay: MakeReplay) -> None:
    spec, plan = fixture(make_replay)
    with pytest.raises(DomainError, match="portfolio"):
        run(spec, plan, settings_for(spec), policy="independent_lots")
    with pytest.raises(DomainError, match="portfolio"):
        run(spec, plan, max_cycles=2, policy="independent_lots")
    with pytest.raises(DomainError, match="unknown"):
        run(spec, plan, policy="future_policy")  # type: ignore[arg-type]


def test_cli_pairing_exclusive_publication_and_failed_member(
    make_replay: MakeReplay, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, plan = fixture(make_replay)
    config, study_path, output = (
        tmp_path / name for name in ("config.json", "study.json", "result.json")
    )
    publish(config, replay_bundle(spec))
    publish(study_path, bundle(plan))
    args = [
        "backtest",
        "--config",
        str(config),
        "--study",
        str(study_path),
        "--compare-lots",
        "--output",
        str(output),
    ]
    process = subprocess.run(
        [sys.executable, "-m", "grid", *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(output.read_bytes())
    assert report["schema"] == "grid-research-run-v1"
    assert report["result"]["schema"] == "grid-lot-walk-forward-comparison-v1"
    assert report["result_payload_sha256"] == digest(report["result"])
    original_bytes = output.read_bytes()
    assert main(args) == 2 and output.read_bytes() == original_bytes
    args[-1] = str(tmp_path / "failed.json")

    def failed(config: Replay) -> NoReturn:
        raise DomainError("synthetic reference failure")

    monkeypatch.setattr(study, "reference_replay", failed)
    assert main(args) == 2 and not (tmp_path / "failed.json").exists()


def test_combined_output_limit_refuses_pair_even_when_each_study_fits(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, plan = fixture(make_replay)
    from grid.research.artifacts import canonical

    first = run(spec, plan)
    second = run(spec, plan, policy="independent_lots")
    monkeypatch.setattr(
        study, "MAX_RESULT_BYTES", max(len(canonical(first)), len(canonical(second))) + 1
    )
    with pytest.raises(DomainError, match="byte cap"):
        compare_lots(spec, plan)


def test_cli_portfolio_reference_pairing_is_refused_before_replay(
    make_replay: MakeReplay, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grid.research import __main__ as cli
    from grid.research.portfolio_config import bundle as portfolio_bundle

    spec, plan = fixture(make_replay)
    config, plan_path, settings, output = (
        tmp_path / name for name in ("replay.json", "plan.json", "portfolio.json", "result.json")
    )
    publish(config, replay_bundle(spec))
    publish(plan_path, bundle(plan))
    publish(settings, portfolio_bundle(settings_for(spec)))

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("reference portfolio combination reached replay")

    monkeypatch.setattr(cli, "compare_study_lots", forbidden)
    assert (
        main(
            [
                "backtest",
                "--config",
                str(config),
                "--study",
                str(plan_path),
                "--compare-lots",
                "--portfolio",
                str(settings),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()
