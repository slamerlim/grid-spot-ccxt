"""Accounting and horizon-matched counterfactuals, never claims of tradable performance."""

import json
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest

from grid.cli import main
from grid.domain.model import Fill, State
from grid.domain.numbers import DomainError
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.portfolio.model import Conversion, PoolKey
from grid.research.artifacts import publish
from grid.research.config import bundle
from grid.research.history import MINUTE, RECORD
from grid.research.performance import posted
from grid.research.portfolio_config import bundle as portfolio_bundle
from grid.research.portfolio_replay import Engine, run
from grid.research.replay import Capital
from grid.research.replay import run as replay

from .conftest import MakeReplay
from .test_portfolio_replay import exit_scenario, settings_for

pytestmark = pytest.mark.usefixtures("async_sockets")


def test_pure_report_serializes_net_realized_wins_separately_from_inventory(
    make_replay: MakeReplay,
) -> None:
    spec = make_replay(
        ((100, 101, 99, 100), (100, 101, 99, 100), (110, 111, 100, 110), (110, 111, 109, 110))
    )
    result: Any = replay(spec)
    row = result["allocations"][0]
    assert row["gross_wins_quote"] == D(10) and row["gross_losses_quote"] == D(0)
    assert row["gross_wins_quote"] - row["gross_losses_quote"] == row["realized_pnl"]
    assert row["profit_factor"] is None


def test_portfolio_cli_benchmarks_share_funding_and_early_endpoint(
    make_replay: MakeReplay, tmp_path: Path
) -> None:
    spec = exit_scenario(make_replay)
    config, settings, output = (tmp_path / n for n in ("replay.json", "policy.json", "result.json"))
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
                "--output",
                str(output),
            ]
        )
        == 0
    )
    result = json.loads(output.read_bytes())["result"]
    benchmarks = result["benchmarks"]
    assert D(benchmarks["initial_owned_nav"]) == D(1000)
    assert benchmarks["entry_ms"] == spec.start_ms + MINUTE
    last = benchmarks["last"]
    assert last["end_ms_exclusive"] == result["simulation_end_ms_exclusive"] < spec.end_ms
    assert D(last["cash"]["net_nav"]) == D(1000)
    assert D(last["buy_hold"]["net_nav"]) == D("1494.85101")
    assert D(last["buy_hold"]["return_fraction"]) == D("0.49485101")
    assert result["intervals"][-1]["benchmarks"] == last
    stats = result["posted_performance"]
    assert stats["posted_fills"] == 2 and stats["posted_sell_fills"] == 1
    assert D(stats["gross_wins_quote"]) == D(50) and D(stats["gross_losses_quote"]) == D(0)
    assert D(stats["posted_turnover_quote"]) == D(250)
    assert stats["profit_factor"] is None
    risk = result["allocation_risk"]
    assert risk["schema"] == "grid-portfolio-allocation-risk-v1"
    allocation = risk["allocations"][0]
    assert D(allocation["last"]["inventory_unrealized_pnl_quote"]) == 0
    assert D(allocation["net_pnl_after_internal_flows"]) == D(50)
    assert allocation["last"]["end_ms_exclusive"] == result["simulation_end_ms_exclusive"]


def test_unused_future_prices_cannot_change_completed_benchmark(make_replay: MakeReplay) -> None:
    spec = exit_scenario(make_replay)
    result: Any = run(spec, settings_for(spec))
    path = spec.allocations[0].source.path
    count = result["simulated_bars"]
    data = path.read_bytes()[: count * RECORD.size]
    for index in range(count, spec.allocations[0].source.count):
        data += RECORD.pack(spec.start_ms + index * MINUTE, 900, 901, 899, 900)
    path.write_bytes(data)  # Synthetic fixture only, never user archives.
    changed: Any = run(spec, settings_for(spec))
    assert changed["benchmarks"] == result["benchmarks"]
    assert changed["posted_performance"] == result["posted_performance"]
    assert changed["allocation_risk"] == result["allocation_risk"]
    assert changed["inputs"] != result["inputs"]


@pytest.mark.parametrize("constraint", ["depth", "order_budget"])
def test_hold_nav_unknown_does_not_replace_known_strategy_nav(
    make_replay: MakeReplay, constraint: str
) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 2)
    settings = settings_for(spec)
    if constraint == "depth":
        settings = replace(settings, liquidity=(replace(settings.liquidity[0], nav_depth=D(2)),))
    else:
        first = spec.allocations[0]
        market = replace(
            first.context.market,
            sell_amount=replace(first.context.market.sell_amount, maximum=D(1)),
        )
        spec = replace(
            spec, allocations=(replace(first, context=replace(first.context, market=market)),)
        )
        settings = replace(
            settings,
            policy=replace(
                settings.policy, exits=(replace(settings.policy.exits[0], max_orders=2),)
            ),
        )
    result: Any = run(spec, settings)
    assert result["last_nav"] is not None and result["last_nav"].net == D(1000)
    point = result["benchmarks"]["last"]
    assert point["cash"]["net_nav"] == D(1000)
    assert point["buy_hold"]["net_nav"] is None and point["buy_hold"]["return_fraction"] is None
    assert point["buy_hold"]["unavailable_reason"] == "portfolio_valuation_constraints"


def test_unallocated_cash_and_foreign_pool_remain_in_both_references(
    make_replay: MakeReplay,
) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 2)
    scope = spec.allocations[0].scope
    spec = replace(
        spec,
        capital=(
            replace(spec.capital[0], amount=D(1500)),
            Capital(scope.venue, scope.account, "TOKEN", D(10)),
        ),
    )
    settings = settings_for(spec)
    settings = replace(
        settings,
        conversions=(
            Conversion(
                PoolKey(scope.venue, scope.account, "TOKEN"),
                "QUOTE",
                D(2),
                D(2),
                D(100),
                spec.start_ms,
                spec.start_ms * 1000000,
                "synthetic-token-value",
            ),
        ),
    )
    result: Any = run(spec, settings)
    benchmark = result["benchmarks"]
    assert result["cycle"].baseline.net == benchmark["initial_owned_nav"] == D(1520)
    assert benchmark["last"]["cash"]["net_nav"] == D(1520)
    assert benchmark["last"]["buy_hold"]["net_nav"] == D("1519.90101")
    assert sorted(amount for _, amount in benchmark["unallocated_pools"]) == [D(10), D(500)]


def test_unfundable_benchmark_entry_preserves_cash_and_reason(make_replay: MakeReplay) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 2)
    allocation = spec.allocations[0]
    context = replace(
        allocation.context, policy=replace(allocation.context.policy, quote_reserve=D(900))
    )
    spec = replace(spec, allocations=(replace(allocation, context=context),))
    result: Any = run(spec, settings_for(spec))
    benchmark = result["benchmarks"]
    held = benchmark["entries"][0][1]
    assert held.base == 0 and held.cash == D(1000)
    assert held.reason == "minimum_or_budget"
    assert (
        benchmark["last"]["cash"]["net_nav"] == benchmark["last"]["buy_hold"]["net_nav"] == D(1000)
    )
    assert result["final_states"][0].owned_base == D(1)


def test_portfolio_gross_wins_and_losses_do_not_net_away_losing_exit(
    make_replay: MakeReplay, tmp_path: Path
) -> None:
    spec = make_replay(
        ((100, 101, 99, 100),) * 2 + ((320, 321, 310, 320),) + ((150, 151, 140, 150),) * 12
    )
    first = spec.allocations[0]
    first = replace(
        first, context=replace(first.context, policy=replace(first.context.policy, spacing=D(4)))
    )
    path = tmp_path / "loser.bin"
    prices = ((100, 101, 99, 100),) * 3 + ((80, 81, 75, 80),) * 12
    path.write_bytes(
        b"".join(RECORD.pack(spec.start_ms + i * MINUTE, *p) for i, p in enumerate(prices))
    )
    market = replace(first.context.market, symbol="LOSER/QUOTE", base="LOSER")
    second = replace(
        first,
        scope=replace(first.scope, symbol=market.symbol, allocation="loser"),
        source=replace(first.source, symbol=market.symbol, path=path),
        context=replace(first.context, market=market),
    )
    spec = replace(
        spec, allocations=(first, second), capital=(replace(spec.capital[0], amount=D(2000)),)
    )
    settings = settings_for(spec)
    # Explicitly allow this synthetic drop from the trigger mark in this loss study.
    settings = replace(
        settings,
        policy=replace(
            settings.policy,
            exits=tuple(replace(r, max_deviation=D("0.75")) for r in settings.policy.exits),
        ),
    )
    result: Any = run(spec, settings)
    stats = result["posted_performance"]
    assert result["cycle"].phase.value == "exit_complete"
    assert stats["gross_wins_quote"] == D(50)
    assert stats["gross_losses_quote"] == D(20)
    assert stats["realized_pnl_quote"] == D(30)
    assert stats["posted_sell_fills"] == 2 and stats["profit_factor"] == D("2.5")
    assert all(r["profit_factor"] is None for r in stats["allocations"])
    assert result["cycle"].final.net == D(2030)


def test_losing_partials_do_not_inflate_profit_factor_sample(make_replay: MakeReplay) -> None:
    spec = make_replay(
        ((100, 101, 99, 100),) * 4 + ((220, 221, 210, 220),) + ((80, 81, 75, 80),) * 10
    )
    allocation = spec.allocations[0]
    context = replace(
        allocation.context,
        policy=replace(allocation.context.policy, spacing=D(2)),
        buy_fee=replace(allocation.context.buy_fee, max_settlements=8),
        sell_fee=replace(allocation.context.sell_fee, max_settlements=8),
    )
    spec = replace(spec, allocations=(replace(allocation, context=context),))
    settings = settings_for(spec)
    settings = replace(
        settings,
        liquidity=(replace(settings.liquidity[0], fill_per_bar=D("0.5")),),
        policy=replace(
            settings.policy, exits=(replace(settings.policy.exits[0], max_deviation=D("0.75")),)
        ),
    )
    result: Any = run(spec, settings)
    stats = result["posted_performance"]
    assert result["cycle"].phase.value == "exit_complete"
    assert stats["gross_losses_quote"] == D(20) and stats["gross_wins_quote"] == D(0)
    assert stats["posted_sell_fills"] == 2 and stats["filled_sell_orders"] == 1
    assert stats["profit_factor"] is None


def test_duplicate_post_and_compaction_do_not_inflate_realized_statistics(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Engine.close
    checked = False

    async def check(engine: Engine) -> None:
        nonlocal checked

        def inspect(ledger: Ledger) -> None:
            scope = engine.scopes[0]
            snapshot = ledger.snapshot(scope)
            assert snapshot.state is not None and snapshot.version is not None
            states = (snapshot.state,)
            before = posted(ledger, states, "QUOTE")
            row = ledger.db.execute(
                "SELECT payload FROM fills ORDER BY trade_id LIMIT 1"
            ).fetchone()
            fill = codec.loads(row[0], Fill)
            outcome = ledger.apply(
                "duplicate-research-check", snapshot.version, fill, engine.contexts[scope]
            )
            assert outcome.accepted and outcome.version is not None
            compacted = ledger.compact("compact-research-check", scope, outcome.version)
            assert compacted.state is not None
            assert posted(ledger, (compacted.state,), "QUOTE") == before
            with pytest.raises(DomainError, match="unlike"):
                posted(ledger, (compacted.state,), "OTHER")
            altered: State = replace(compacted.state, realized_pnl=D(999))
            with pytest.raises(DomainError, match="differs"):
                posted(ledger, (altered,), "QUOTE")

        await engine.writer.submit(inspect)
        checked = True
        await original(engine)

    monkeypatch.setattr(Engine, "close", check)
    spec = exit_scenario(make_replay)
    run(spec, settings_for(spec))
    assert checked
