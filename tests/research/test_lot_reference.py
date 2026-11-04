"""Research alternatives share funding/math without changing the basket trading reducer."""

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
from grid.domain.market import FeeRule, Money
from grid.domain.model import Fill, Order, Side
from grid.research import lot_reference
from grid.research.artifacts import digest, publish
from grid.research.config import bundle
from grid.research.history import MINUTE, RECORD, validate, validated_bars
from grid.research.lot_reference import Reference, compare, run
from grid.research.replay import MODELS, Capital, Model, Replay, Simulator, token_nav
from grid.research.replay import run as basket

from .conftest import MakeReplay
from .test_portfolio_replay import settings_for


def independent_scenario(make_replay: MakeReplay) -> Replay:
    return make_replay(
        ((100, 101, 99, 100),) * 2 + ((90, 91, 80, 90),) * 3 + ((100, 101, 99, 100),) * 2
    )


def test_independent_tps_preserve_separate_cost_and_common_funding(make_replay: MakeReplay) -> None:
    spec = independent_scenario(make_replay)
    report: Any = compare(spec)
    reference = report["independent_lots"]
    row = reference["allocations"][0]
    assert row["fills"] == 5 and row["sell_fills"] == 2
    assert row["realized_pnl"] == D("17.36")
    assert row["nav_at_close"] == D("1017.36")
    seed = next(p for p in row["pockets"] if p["level"] == 0)
    assert seed["state"].owned_base == D(1)
    assert seed["state"].position_cost == D(100)
    assert next(o for o in seed["state"].working if o.side is Side.SELL).price == D(110)
    assert report["basket_single_sell"] == basket(spec)
    assert report["initial_allocated_nav"] == reference["initial_allocated_nav"] == D(1000)
    assert (
        report["reference_minus_basket_nav"]
        == reference["final_nav_at_close"] - report["basket_single_sell"]["final_nav_at_close"]
    )
    assert (
        digest({k: v for k, v in report.items() if k != "result_sha256"}) == report["result_sha256"]
    )


@pytest.mark.parametrize("model", MODELS)
def test_ambiguity_is_shared_across_lots_and_new_orders_wait(
    make_replay: MakeReplay, model: Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = replace(independent_scenario(make_replay), model=model)
    path = spec.allocations[0].source.path
    # Use a broad ambiguous bar only after multiple independent SELLs exist.
    raw = path.read_bytes()
    path.write_bytes(
        raw[: 4 * RECORD.size]
        + RECORD.pack(spec.start_ms + 4 * MINUTE, 100, 160, 50, 100)
        + raw[5 * RECORD.size :]
    )
    calls: dict[int, set[Side]] = {}
    original = Simulator.fill

    def observe(sim: Simulator, order: Order, index: int) -> Fill:
        assert index >= sim.eligible[order.id]
        calls.setdefault(index, set()).add(order.side)
        return original(sim, order, index)

    monkeypatch.setattr(Simulator, "fill", observe)
    result: Any = run(spec)
    assert all(len(sides) == 1 for sides in calls.values())
    assert 0 not in calls
    assert result["allocations"][0]["ambiguous_bars"] >= 1
    assert (
        (4 not in calls)
        if model == MODELS[0]
        else calls[4] == {Side.BUY if model == MODELS[1] else Side.SELL}
    )


@pytest.mark.parametrize("currency", ["QUOTE", "BASE", "TOKEN"])
def test_fee_currency_conservation_and_shared_caps_at_every_bar(
    make_replay: MakeReplay, currency: str
) -> None:
    spec = independent_scenario(make_replay)
    allocation = spec.allocations[0]
    fee = FeeRule(currency, D("0.001"), D("0.0001"), D(2) if currency == "TOKEN" else None, 4)
    context = replace(
        allocation.context,
        buy_fee=fee,
        sell_fee=fee,
        policy=replace(
            allocation.context.policy, max_lots=4, max_open_orders=4, max_quote_committed=D(400)
        ),
    )
    tokens = (Money("TOKEN", D(10)),) if currency == "TOKEN" else ()
    allocation = replace(allocation, context=context, fee_balances=tokens)
    capital = (
        (*spec.capital, Capital(allocation.scope.venue, allocation.scope.account, "TOKEN", D(10)))
        if tokens
        else spec.capital
    )
    spec = replace(spec, allocations=(allocation,), capital=capital)
    ref = Reference(allocation, spec)
    initial = allocation.quote_cash + token_nav(context, tokens)
    for index, bar in enumerate(validated_bars(validate(allocation.source))):
        ref.step(bar, index)
        row: Any = ref.report(bar.close)
        assert ref.mark(bar.close) - initial == row["realized_pnl"] + row["unrealized_pnl_at_close"]
        assert ref.cash >= context.policy.quote_reserve
        assert all(value >= 0 for value in ref.tokens.values())
        assert ref.committed() <= D(400)
        assert len(ref.pockets) <= 4
        assert sum(len(p.state.working) for p in ref.pockets.values()) <= 4
        for pocket in ref.pockets.values():
            assert sum(o.side is Side.SELL for o in pocket.state.working) <= 1
            assert all(o.amount == D(1) for o in pocket.state.working if o.side is Side.BUY)
    assert ref.fee_quote > 0


def test_fixed_anchor_reentry_reuses_cash_and_keeps_losing_inventory_visible(
    make_replay: MakeReplay,
) -> None:
    spec = make_replay(
        ((100, 101, 99, 100),) * 2
        + ((110, 111, 100, 110),)
        + ((100, 101, 99, 100),) * 2
        + ((50, 51, 49, 50),)
    )
    allocation = spec.allocations[0]
    spec = replace(
        spec,
        allocations=(
            replace(
                allocation,
                context=replace(
                    allocation.context, policy=replace(allocation.context.policy, max_lots=1)
                ),
            ),
        ),
    )
    result: Any = run(spec)
    row = result["allocations"][0]
    assert row["anchor"] == D(100) and row["sell_fills"] == 1
    assert row["pockets"][0]["generation"] == 2
    assert row["realized_pnl"] == D(10)
    assert row["unrealized_pnl_at_close"] == D(-50)
    assert result["net_change_at_close"] == D(-40)


def test_unfunded_or_unsellable_minimum_never_enlarges_a_lot(make_replay: MakeReplay) -> None:
    spec = independent_scenario(make_replay)
    allocation = spec.allocations[0]
    unfunded: Any = run(replace(spec, allocations=(replace(allocation, quote_cash=D(50)),)))
    assert unfunded["allocations"][0]["fills"] == 0
    assert unfunded["final_nav_at_close"] == D(50)
    market = replace(
        allocation.context.market,
        sell_amount=replace(allocation.context.market.sell_amount, minimum=D(1)),
    )
    context = replace(
        allocation.context, market=market, buy_fee=FeeRule("BASE", D("0.001"), D("0.0001"), None, 1)
    )
    rejected: Any = run(replace(spec, allocations=(replace(allocation, context=context),)))
    assert rejected["allocations"][0]["fills"] == 0 and rejected["allocations"][0]["notices"]
    assert rejected["final_nav_at_close"] == D(1000)


def test_scope_order_and_ambient_precision_do_not_change_comparison(
    make_replay: MakeReplay,
) -> None:
    spec = independent_scenario(make_replay)
    first = replace(spec.allocations[0], quote_cash=D(500))
    second = replace(first, scope=replace(first.scope, allocation="second"))
    spec = replace(spec, allocations=(first, second))
    expected: Any = compare(spec)
    with localcontext() as context:
        context.prec = 5
        assert compare(replace(spec, allocations=(second, first))) == expected
    child_scopes = [
        p["state"].scope
        for row in expected["independent_lots"]["allocations"]
        for p in row["pockets"]
    ]
    assert len(child_scopes) == len(set(child_scopes))


def test_cli_and_input_integrity_and_conflicting_modes(
    make_replay: MakeReplay, tmp_path: Path
) -> None:
    spec = independent_scenario(make_replay)
    config, output = tmp_path / "replay.json", tmp_path / "report.json"
    publish(config, bundle(spec))
    original = (spec.allocations[0].source.path.read_bytes(), config.read_bytes())
    command = [
        sys.executable,
        "-m",
        "grid",
        "backtest",
        "--config",
        str(config),
        "--compare-lots",
        "--output",
        str(output),
    ]
    process = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert process.returncode == 0, process.stderr
    report = json.loads(output.read_bytes())
    assert report["result"]["schema"] == "grid-lot-comparison-v1"
    assert digest(report["result"]) == report["result_payload_sha256"]
    assert (spec.allocations[0].source.path.read_bytes(), config.read_bytes()) == original
    conflict_output = tmp_path / "conflict.json"
    assert (
        subprocess.run(
            [*command[:-1], str(conflict_output), "--compare-scenarios"],
            capture_output=True,
            timeout=30,
            check=False,
        ).returncode
        == 2
    )
    assert not conflict_output.exists()


@pytest.mark.parametrize("stage", ["basket", "reference"])
def test_changed_source_and_capacity_never_publish_partial_comparison(
    make_replay: MakeReplay, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    spec = independent_scenario(make_replay)
    original = basket if stage == "basket" else run

    def mutate(value: Replay) -> dict[str, object]:
        result = original(value)
        path = value.allocations[0].source.path
        before = path.stat()
        path.write_bytes(path.read_bytes())
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
        return result

    config, output = tmp_path / "replay.json", tmp_path / "report.json"
    publish(config, bundle(spec))
    args = ["backtest", "--config", str(config), "--compare-lots", "--output", str(output)]
    with monkeypatch.context() as patch:
        patch.setattr(lot_reference, "run_basket" if stage == "basket" else "run", mutate)
        assert main(args) == 2 and not output.exists()
    with monkeypatch.context() as patch:
        patch.setattr(lot_reference, "MAX_POCKETS", 0)
        assert main(args) == 2 and not output.exists()
    with monkeypatch.context() as patch:
        patch.setattr(lot_reference, "MAX_RESULT_BYTES", 1)
        assert main(args) == 2 and not output.exists()


def test_portfolio_reference_combination_is_explicitly_refused(
    make_replay: MakeReplay, tmp_path: Path
) -> None:
    from grid.research.portfolio_config import bundle as portfolio_bundle

    spec = independent_scenario(make_replay)
    config, portfolio, output = (
        tmp_path / name for name in ("replay.json", "portfolio.json", "report.json")
    )
    publish(config, bundle(spec))
    publish(portfolio, portfolio_bundle(settings_for(spec)))
    assert (
        main(
            [
                "backtest",
                "--config",
                str(config),
                "--portfolio",
                str(portfolio),
                "--compare-lots",
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()


@pytest.mark.parametrize("model", MODELS)
def test_repeated_reentries_conserve_finite_capital_and_bound_lifetimes(
    make_replay: MakeReplay, model: Model
) -> None:
    prices = (100, 100, 90, 90, 100, 110, 100, 50, 100, 150) * 12
    spec = replace(make_replay(tuple((p, p + 1, p - 1, p) for p in prices)), model=model)
    ref = Reference(spec.allocations[0], spec)
    for index, bar in enumerate(validated_bars(validate(spec.allocations[0].source))):
        ref.step(bar, index)
        row: Any = ref.report(bar.close)
        assert row["nav_at_close"] - D(1000) == row["realized_pnl"] + row["unrealized_pnl_at_close"]
        assert len(ref.pockets) <= ref.context.policy.max_lots
        assert len(ref.generations) <= ref.context.policy.max_lots
        assert all(len(s.state.orders) <= 2 for s in ref.pockets.values())
    assert max(ref.generations.values()) > 1
