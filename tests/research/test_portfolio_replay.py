from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path
from typing import Any

import pytest
from persistence.conftest import SOURCE_ID

from grid.domain.market import FeeRule, Money
from grid.domain.model import Side
from grid.domain.numbers import DomainError
from grid.persistence.records import LedgerError
from grid.portfolio.model import Conversion, Cycle, CyclePhase, ExitRule, PoolKey, PortfolioPolicy
from grid.research.artifacts import digest
from grid.research.history import MINUTE, RECORD
from grid.research.portfolio_config import Liquidity, Settings
from grid.research.portfolio_replay import run
from grid.research.replay import Capital, Replay

from .conftest import MakeReplay

pytestmark = pytest.mark.usefixtures("async_sockets")


def settings_for(spec: Replay) -> Settings:
    policy = PortfolioPolicy(
        "QUOTE",
        tuple(PoolKey(p.venue, p.account, p.currency) for p in spec.capital),
        tuple(
            ExitRule(a.scope, "bid", D(1), D("0.5"), D(0), 5 * MINUTE, 10) for a in spec.allocations
        ),
        (),
        (),
        D(0),
        1000,
        1,
        20 * MINUTE,
        "no_transfers",
    )
    return Settings(
        policy,
        tuple(Liquidity(a.scope, D(100), D(100)) for a in spec.allocations),
        (),
        8,
        500,
        32 * 1024**2,
        SOURCE_ID,
    )


def exit_scenario(make_replay: MakeReplay) -> Replay:
    spec = make_replay(
        ((100, 101, 99, 100),) * 2 + ((220, 221, 210, 220),) + ((150, 151, 140, 150),) * 6
    )
    allocation = spec.allocations[0]
    context = replace(allocation.context, policy=replace(allocation.context.policy, spacing=D(2)))
    return replace(spec, allocations=(replace(allocation, context=context),))


def test_real_controller_trigger_exit_and_shortfall(make_replay: MakeReplay) -> None:
    spec = exit_scenario(make_replay)
    result: dict[str, Any] = run(spec, settings_for(spec))
    cycle: Cycle = result["cycle"]
    assert cycle.baseline.net == D(1000)
    assert cycle.trigger is not None and cycle.trigger.net == D(1120)
    assert cycle.phase is CyclePhase.COMPLETE
    assert cycle.final is not None and cycle.final.net == D(1050)
    assert result["final_return"] == D("0.05")
    assert result["exit_shortfall"] == D(70)
    assert result["max_drawdown_fraction_observed_nav"] == D("0.0625")
    assert not result["drawdown_complete"]
    assert result["intervals"][0]["nav_before_actions"].net == D(1120)
    assert result["intervals"][0]["nav"] is None
    assert result["final_states"][0].owned_base == 0
    assert len(result["fills"]) == 2
    assert result["simulated_bars"] < result["consumed_source_minutes"]
    assert result["simulation_end_ms_exclusive"] < spec.end_ms
    assert result["intervals"][-1]["phase"] is CyclePhase.COMPLETE
    assert (
        digest({k: v for k, v in result.items() if k != "result_sha256"}) == result["result_sha256"]
    )


def test_new_sell_waits_later_bar_and_no_forced_end_exit(make_replay: MakeReplay) -> None:
    spec = make_replay(((100, 101, 99, 100), (100, 150, 99, 100)))
    result: dict[str, Any] = run(spec, settings_for(spec))
    assert len(result["fills"]) == 1
    assert result["final_states"][0].owned_base == D(1)
    assert result["cycle"].phase is CyclePhase.ACTIVE
    assert result["final_return"] is None


def test_repeat_is_deterministic(make_replay: MakeReplay) -> None:
    spec = exit_scenario(make_replay)
    result = run(spec, settings_for(spec))
    with localcontext() as decimal_context:
        decimal_context.prec = 5
        assert run(spec, settings_for(spec)) == result


def test_late_buy_is_owned_during_global_cancel_barrier(make_replay: MakeReplay) -> None:
    spec = make_replay(
        ((100, 101, 99, 100),) * 3
        + ((220, 221, 210, 220), (220, 221, 30, 220))
        + ((150, 151, 140, 150),) * 9
    )
    allocation = spec.allocations[0]
    spec = replace(
        spec,
        latency_bars=2,
        allocations=(
            replace(
                allocation,
                context=replace(
                    allocation.context, policy=replace(allocation.context.policy, spacing=D(2))
                ),
            ),
        ),
    )
    result: dict[str, Any] = run(spec, settings_for(spec))
    cycle: Cycle = result["cycle"]
    assert cycle.trigger is not None
    assert any(
        i == 4 and kind == "fill" and event.side is Side.BUY for i, kind, event in result["events"]
    )
    assert all(
        i < 3 for i, kind, event in result["events"] if kind == "submit" and event.side is Side.BUY
    )
    assert sum((f.amount for f in result["fills"] if f.side is Side.BUY), D(0)) == D(2)
    assert cycle.phase is CyclePhase.COMPLETE
    assert result["final_states"][0].owned_base == 0
    assert cycle.final is not None and cycle.final.net == D("1166.67")


def test_partial_seed_and_exit_respect_shared_bar_capacity(make_replay: MakeReplay) -> None:
    spec = make_replay(
        ((100, 101, 99, 100),) * 4 + ((220, 221, 210, 220),) + ((150, 151, 140, 150),) * 8
    )
    allocation = spec.allocations[0]
    spec = replace(
        spec,
        allocations=(
            replace(
                allocation,
                context=replace(
                    allocation.context,
                    policy=replace(allocation.context.policy, spacing=D(2)),
                    buy_fee=replace(allocation.context.buy_fee, max_settlements=8),
                    sell_fee=replace(allocation.context.sell_fee, max_settlements=8),
                ),
            ),
        ),
    )
    settings = settings_for(spec)
    settings = replace(settings, liquidity=(replace(settings.liquidity[0], fill_per_bar=D("0.5")),))
    result: dict[str, Any] = run(spec, settings)
    assert result["cycle"].phase is CyclePhase.COMPLETE
    assert result["cycle"].final.net == D(1050)
    assert [fill.amount for fill in result["fills"]] == [D("0.5")] * 4
    for index in range(result["simulated_bars"]):
        assert sum(
            (event.amount for i, kind, event in result["events"] if i == index and kind == "fill"),
            D(0),
        ) <= D("0.5")


def test_loser_and_unallocated_cash_remain_in_consolidated_nav(
    make_replay: MakeReplay, tmp_path: Path
) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 2 + ((350, 351, 340, 350),))
    first = spec.allocations[0]
    first = replace(
        first, context=replace(first.context, policy=replace(first.context.policy, spacing=D(4)))
    )
    path = tmp_path / "loser.bin"
    path.write_bytes(
        b"".join(
            RECORD.pack(spec.start_ms + i * MINUTE, *prices)
            for i, prices in enumerate(((100, 101, 99, 100),) * 2 + ((40, 41, 35, 40),))
        )
    )
    market = replace(first.context.market, symbol="LOSER/QUOTE", base="LOSER")
    second = replace(
        first,
        scope=replace(first.scope, symbol=market.symbol, allocation="loser"),
        source=replace(first.source, symbol=market.symbol, path=path),
        context=replace(first.context, market=market),
    )
    spec = replace(
        spec, allocations=(first, second), capital=(replace(spec.capital[0], amount=D(3000)),)
    )
    settings = settings_for(spec)
    result: dict[str, Any] = run(spec, settings)
    assert result["cycle"].baseline.net == D(3000)
    assert result["last_nav"].net == D(3190)
    assert result["cycle"].phase is CyclePhase.ACTIVE
    assert result["final_unallocated_pools"] == ((settings.policy.pools[0], D(1000)),)
    backwards = replace(
        settings,
        liquidity=tuple(reversed(settings.liquidity)),
        policy=replace(settings.policy, exits=tuple(reversed(settings.policy.exits))),
    )
    assert run(replace(spec, allocations=tuple(reversed(spec.allocations))), backwards) == result


def test_insufficient_assumed_nav_depth_freezes_new_exposure(make_replay: MakeReplay) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 3)
    settings = settings_for(spec)
    settings = replace(settings, liquidity=(replace(settings.liquidity[0], nav_depth=D("0.5")),))
    result: dict[str, Any] = run(spec, settings)
    assert result["last_nav"] is None
    assert result["nav_unknown_bars"] == 2 and not result["drawdown_complete"]
    assert len([o for o in result["orders"] if o.side is Side.BUY]) == 1
    assert result["final_return"] is None


@pytest.mark.parametrize("obstacle", ["floor", "deadline"])
def test_exit_obstacle_never_invents_completion(make_replay: MakeReplay, obstacle: str) -> None:
    spec = exit_scenario(make_replay)
    settings = settings_for(spec)
    policy = (
        replace(settings.policy, exits=(replace(settings.policy.exits[0], floor=D(400)),))
        if obstacle == "floor"
        else replace(settings.policy, exit_duration_ms=MINUTE)
    )
    result: dict[str, Any] = run(spec, replace(settings, policy=policy))
    assert result["cycle"].phase is CyclePhase.BLOCKED
    assert result["cycle"].final is None and result["final_return"] is None
    assert result["final_states"][0].owned_base == D(1)
    assert all(fill.side is Side.BUY for fill in result["fills"])


@pytest.mark.parametrize("bound", ["venue", "database", "sqlite"])
def test_resource_or_unapproved_sqlite_refuses_success(make_replay: MakeReplay, bound: str) -> None:
    spec = exit_scenario(make_replay)
    settings = settings_for(spec)
    settings = (
        replace(settings, venue_capacity=1)
        if bound == "venue"
        else replace(settings, max_database_bytes=1048576)
        if bound == "database"
        else replace(settings, sqlite_source_id="unapproved-synthetic-source")
    )
    with pytest.raises(
        (DomainError, LedgerError),
        match=(
            "historical submission"
            if bound == "venue"
            else "database byte cap"
            if bound == "database"
            else "SQLite"
        ),
    ):
        run(spec, settings)


@pytest.mark.parametrize("currency", ["BASE", "QUOTE", "TOKEN"])
def test_actual_fill_fees_use_declared_owned_currency(
    make_replay: MakeReplay, currency: str
) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 2)
    allocation = spec.allocations[0]
    fee = FeeRule(currency, D("0.001"), D("0.0001"), D(2) if currency == "TOKEN" else None, 8)
    tokens = (Money("TOKEN", D(10)),) if currency == "TOKEN" else ()
    spec = replace(
        spec,
        allocations=(
            replace(
                allocation,
                context=replace(allocation.context, buy_fee=fee, sell_fee=fee),
                fee_balances=tokens,
            ),
        ),
        capital=(
            *spec.capital,
            Capital(allocation.scope.venue, allocation.scope.account, "TOKEN", D(10)),
        )
        if tokens
        else spec.capital,
    )
    settings = settings_for(spec)
    if tokens:
        settings = replace(
            settings,
            conversions=(
                Conversion(
                    PoolKey(allocation.scope.venue, allocation.scope.account, "TOKEN"),
                    "QUOTE",
                    D(2),
                    D(2),
                    D(100),
                    spec.start_ms,
                    spec.start_ms * 1000000,
                    "explicit-synthetic-token-conversion",
                ),
            ),
        )
    result: dict[str, Any] = run(spec, settings)
    state = result["final_states"][0]
    paid = result["fees_by_currency"][currency]
    assert paid > 0 and result["fees_quote"] == D("0.1")
    assert state.quote_cash == D(900) - (paid if currency == "QUOTE" else D(0))
    assert state.owned_base == D(1) - (paid if currency == "BASE" else D(0))
    if tokens:
        assert state.fee_balances == (Money("TOKEN", D(10) - paid),)
        assert result["cycle"].baseline.net == D(1020)
    assert result["last_nav"].net < result["cycle"].baseline.net


def test_fill_count_beyond_fee_reservation_refuses_success(make_replay: MakeReplay) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 3)
    settings = settings_for(spec)
    settings = replace(settings, liquidity=(replace(settings.liquidity[0], fill_per_bar=D("0.5")),))
    with pytest.raises(DomainError, match="historical reconciliation"):
        run(spec, settings)


def test_shared_account_backoff_cannot_overfill_stale_partial_snapshot(
    make_replay: MakeReplay,
) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 8)
    first = spec.allocations[0]
    market = replace(
        first.context.market, buy_amount=replace(first.context.market.buy_amount, minimum=D("3.5"))
    )
    first = replace(
        first,
        context=replace(
            first.context,
            market=market,
            buy_fee=replace(first.context.buy_fee, max_settlements=8),
            sell_fee=replace(first.context.sell_fee, max_settlements=8),
        ),
    )
    second_market = replace(market, symbol="SECOND/QUOTE", base="SECOND")
    second = replace(
        first,
        scope=replace(first.scope, symbol=second_market.symbol, allocation="second"),
        source=replace(first.source, symbol=second_market.symbol),
        context=replace(first.context, market=second_market),
    )
    spec = replace(
        spec,
        latency_bars=3,
        allocations=(first, second),
        capital=(replace(spec.capital[0], amount=D(2000)),),
    )
    settings = settings_for(spec)
    settings = replace(
        settings, liquidity=tuple(replace(r, fill_per_bar=D(1)) for r in settings.liquidity)
    )
    result: dict[str, Any] = run(spec, settings)
    assert all(state.owned_base == D("3.5") for state in result["final_states"])
    assert sum((fill.amount for fill in result["fills"]), D(0)) == D(7)
    assert any(fill.amount == D("0.5") for fill in result["fills"])
    assert result["nav_unknown_bars"] > 0


def test_omitted_ownership_or_foreign_roster_refused_before_ledger(make_replay: MakeReplay) -> None:
    spec = exit_scenario(make_replay)
    settings = settings_for(spec)
    with pytest.raises(DomainError, match="all declared capital"):
        run(spec, replace(settings, policy=replace(settings.policy, pools=())))
    other = replace(
        settings.liquidity[0], scope=replace(spec.allocations[0].scope, account="other")
    )
    with pytest.raises(DomainError, match="complete replay roster"):
        run(spec, replace(settings, liquidity=(other,)))
