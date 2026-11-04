"""Inventory losses, exact NAV attribution and transfer-neutral observed risk."""

import sqlite3
from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from grid.domain.market import FeeRule, Money
from grid.domain.numbers import ARITHMETIC, DomainError
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.portfolio.model import Conversion, PoolKey, PortfolioPolicy, ValuationFacts
from grid.research import portfolio_replay
from grid.research.history import MINUTE, RECORD
from grid.research.portfolio_config import Settings
from grid.research.portfolio_replay import run
from grid.research.portfolio_risk import Movement, Risk, transfers
from grid.research.replay import Capital, Replay

from .conftest import MakeReplay
from .test_portfolio_replay import exit_scenario, settings_for

pytestmark = pytest.mark.usefixtures("async_sockets")


def test_realized_gain_cannot_hide_larger_remaining_inventory_loss(make_replay: MakeReplay) -> None:
    spec = make_replay(
        ((100, 101, 99, 100),) * 2 + ((110, 111, 109, 110),) * 3 + ((50, 51, 49, 50),) * 3
    )
    first = spec.allocations[0]
    spec = replace(
        spec,
        interval_bars=1,
        allocations=(
            replace(
                first,
                context=replace(first.context, policy=replace(first.context.policy, max_lots=1)),
            ),
        ),
    )
    result: Any = run(spec, settings_for(spec))
    row = result["allocation_risk"]["allocations"][0]
    point = row["last"]
    assert point.realized_pnl_quote == D(10)
    assert point.owned_base == D(1) and point.position_cost_quote == D(110)
    assert point.inventory_net_exit_value == D(50)
    assert point.inventory_unrealized_pnl_quote == D(-60)
    assert point.net_nav == D(950)
    assert row["net_pnl_after_internal_flows"] == D(-50)
    assert row["pnl_fraction_initial_nav"] == D("-0.05")
    assert row["max_drawdown_quote_observed"] == D(60)
    with localcontext(ARITHMETIC):
        assert row["max_drawdown_fraction_observed"] == D(60) / D(1010)
    first_point = result["intervals"][0]["allocation_risk"][0]
    assert first_point.quote_cash == first_point.net_nav == D(1000)
    assert first_point.reserved_quote == first_point.committed_quote == D(100)
    assert first_point.reserved_base == D(0)


def test_trigger_peak_and_completed_exit_keep_same_observed_drawdown(
    make_replay: MakeReplay,
) -> None:
    spec = exit_scenario(make_replay)
    result: Any = run(spec, settings_for(spec))
    risk = result["allocation_risk"]
    row = risk["allocations"][0]
    assert row["last"].inventory_unrealized_pnl_quote == 0
    assert row["net_pnl_after_internal_flows"] == D(50)
    assert row["max_drawdown_quote_observed"] == D(70)
    assert (
        row["max_drawdown_fraction_observed"]
        == result["max_drawdown_fraction_observed_nav"]
        == D("0.0625")
    )
    assert not row["drawdown_complete"] and row["unknown_observations"] > 0
    assert row["last"].end_ms_exclusive == result["simulation_end_ms_exclusive"]
    assert risk["simulated_bars"] == result["simulated_bars"]
    point = result["intervals"][0]["allocation_risk_before_actions"][0]
    assert point.net_nav == D(1120) and point.inventory_unrealized_pnl_quote == D(120)
    assert result["intervals"][0]["allocation_risk"][0].net_nav is None
    expected = {
        "allocated_nav": D(1050),
        "unallocated_pool_value": D(0),
        "unassigned_liability_value": D(0),
        "portfolio_net_nav": D(1050),
    }
    assert {key: risk["last_attribution"][key] for key in expected} == expected


def test_unknown_depth_withholds_marks_without_erasing_owned_exposure(
    make_replay: MakeReplay,
) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 3)
    settings = settings_for(spec)
    settings = replace(settings, liquidity=(replace(settings.liquidity[0], nav_depth=D("0.5")),))
    result: Any = run(spec, settings)
    row = result["allocation_risk"]["allocations"][0]
    point = row["last"]
    assert point.owned_base == D(1) and point.position_cost_quote == D(100)
    assert point.inventory_net_exit_value is point.inventory_unrealized_pnl_quote is None
    assert point.net_nav is point.flow_adjusted_nav is None
    assert point.unavailable_reason == "portfolio_nav_unavailable"
    assert row["unknown_post_action_bars"] == 2
    assert row["unknown_observations"] == 4 and not row["drawdown_complete"]
    assert row["known_exposure_post_action_bars"] == 1
    assert row["mean_nonreporting_value_known_post_actions"] == 0
    assert row["max_owned_base"] == 1 and row["net_pnl_after_internal_flows"] is None
    with localcontext(ARITHMETIC):
        assert row["fraction_bars_holding_inventory"] == D(2) / D(3)
    totals = result["allocation_risk"]["last_attribution"]
    assert totals["inventory_unrealized_pnl_quote"] is None
    assert totals["allocated_nonreporting_asset_value"] is None
    assert totals["posted_position_cost_quote"] == D(100)


@pytest.mark.parametrize("currency", ["BASE", "QUOTE", "TOKEN"])
def test_marked_inventory_pnl_includes_entry_and_estimated_exit_fees(
    make_replay: MakeReplay,
    currency: str,
) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 2)
    first = spec.allocations[0]
    fee = FeeRule(currency, D("0.001"), D("0.0001"), D(2) if currency == "TOKEN" else None, 8)
    tokens = (Money("TOKEN", D(10)),) if currency == "TOKEN" else ()
    spec = replace(
        spec,
        allocations=(
            replace(
                first,
                context=replace(first.context, buy_fee=fee, sell_fee=fee),
                fee_balances=tokens,
            ),
        ),
        capital=(*spec.capital, Capital(first.scope.venue, first.scope.account, "TOKEN", D(10)))
        if tokens
        else spec.capital,
    )
    settings = settings_for(spec)
    if tokens:
        settings = replace(
            settings,
            conversions=(
                Conversion(
                    PoolKey(first.scope.venue, first.scope.account, "TOKEN"),
                    "QUOTE",
                    D(2),
                    D(2),
                    D(100),
                    spec.start_ms,
                    spec.start_ms * 1000000,
                    "synthetic-fee-fx",
                ),
            ),
        )
    result: Any = run(spec, settings)
    row = result["allocation_risk"]["allocations"][0]
    point, state = row["last"], result["final_states"][0]
    base_mark = next(m for m in result["last_nav"].marks if m.key.currency == "BASE")
    assert point.net_nav == result["last_nav"].net
    assert point.position_cost_quote == state.position_cost
    assert point.inventory_net_exit_value == base_mark.value
    assert point.inventory_unrealized_pnl_quote == base_mark.value - state.position_cost < 0
    assert point.estimated_inventory_exit_fee == base_mark.estimated_fee
    assert point.unpriced_residual_base == base_mark.residual
    assert row["net_pnl_after_internal_flows"] == point.inventory_unrealized_pnl_quote
    assert point.fee_balances == state.fee_balances
    assert point.reserved_base <= point.owned_base


def conversion_scenario(make_replay: MakeReplay, tmp_path: Path) -> tuple[Replay, Settings]:
    spec = make_replay(
        ((100, 101, 99, 100),) * 2 + ((220, 221, 210, 220),) + ((150, 151, 140, 150),) * 14
    )
    first = spec.allocations[0]
    first = replace(
        first,
        context=replace(
            first.context,
            policy=replace(first.context.policy, spacing=D(2)),
            buy_fee=FeeRule("TOKEN", D(0), D("0.0001"), D(1), 1),
        ),
        fee_balances=(Money("TOKEN", D(10)),),
    )
    path = tmp_path / "token.bin"
    path.write_bytes(
        b"".join(
            RECORD.pack(spec.start_ms + i * MINUTE, 1, 1.01, 0.99, 1)
            for i in range(first.source.count)
        )
    )
    market = replace(first.context.market, symbol="TOKEN/QUOTE", base="TOKEN")
    sink = replace(
        first,
        scope=replace(first.scope, symbol=market.symbol, allocation="conversion"),
        source=replace(first.source, path=path, symbol=market.symbol),
        context=replace(first.context, market=market, buy_fee=first.context.sell_fee),
        quote_cash=D(0),
        fee_balances=(),
    )
    spec = replace(
        spec,
        allocations=(first, sink),
        capital=(*spec.capital, Capital(first.scope.venue, first.scope.account, "TOKEN", D(15))),
    )
    settings = settings_for(spec)
    settings = replace(
        settings,
        policy=replace(settings.policy, sinks=(sink.scope,)),
        conversions=(
            Conversion(
                PoolKey(first.scope.venue, first.scope.account, "TOKEN"),
                "QUOTE",
                D(1),
                D(1),
                D(100),
                spec.start_ms,
                spec.start_ms * 1000000,
                "synthetic-transfer-fx",
            ),
        ),
    )
    return spec, settings


def test_internal_transfer_does_not_manufacture_loss_or_conversion_profit(
    make_replay: MakeReplay,
    tmp_path: Path,
) -> None:
    spec, settings = conversion_scenario(make_replay, tmp_path)
    result: Any = run(spec, settings)
    assert result["cycle"].phase.value == "exit_complete"
    assert result["cycle"].baseline.net == D(1015) and result["cycle"].final.net == D(1065)
    risk = result["allocation_risk"]
    source, sink = risk["allocations"]
    assert source["scope"].allocation == "allocation"
    assert source["initial_owned_nav"] == D(1010)
    assert source["last"].net_nav == D(1050)
    assert source["last"].cumulative_internal_flow_value == D(-10)
    assert source["net_pnl_after_internal_flows"] == D(50)
    assert sink["initial_owned_nav"] == 0
    assert sink["last"].net_nav == sink["last"].cumulative_internal_flow_value == D(15)
    assert sink["net_pnl_after_internal_flows"] == sink["max_drawdown_quote_observed"] == 0
    assert sink["pnl_fraction_initial_nav"] is sink["max_drawdown_fraction_observed"] is None
    movements = risk["internal_movements"]
    assert sorted(m.amount for m in movements) == [D(-10), D(-5), D(15)]
    assert sum((m.value for m in movements), D(0)) == 0
    assert next(m for m in movements if m.scope is None).amount == D(-5)
    assert result["posted_performance"]["posted_fills"] == 3
    assert risk["last_attribution"]["allocated_nav"] == D(1065)
    assert risk["last_attribution"]["unallocated_pool_value"] == 0


@pytest.mark.parametrize("missing", ["absent", "stale", "capacity"])
def test_unknown_transfer_mark_never_backfills_adjusted_returns(
    make_replay: MakeReplay,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    def without_fx(
        ledger: Ledger, request_id: str, policy: PortfolioPolicy, facts: ValuationFacts
    ) -> tuple[Movement, ...]:
        rows = (
            ()
            if missing == "absent"
            else tuple(
                replace(row, observed_ms=0) if missing == "stale" else replace(row, capacity=D(1))
                for row in facts.conversions
            )
        )
        return transfers(ledger, request_id, policy, replace(facts, conversions=rows))

    monkeypatch.setattr(portfolio_replay, "transfers", without_fx)
    spec, settings = conversion_scenario(make_replay, tmp_path)
    result: Any = run(spec, settings)
    assert result["cycle"].final.net == D(1065)
    for row in result["allocation_risk"]["allocations"]:
        assert row["last"].net_nav is not None
        assert row["last"].flow_adjusted_nav is None
        assert row["last"].unavailable_reason == "transfer_valuation_unavailable"
        assert row["net_pnl_after_internal_flows"] is None and not row["drawdown_complete"]
    assert all(m.value is None for m in result["allocation_risk"]["internal_movements"])


@pytest.mark.parametrize("damage", ["amount", "timestamp", "owner", "duplicate", "net"])
def test_attribution_refuses_unpaired_or_ambiguous_nav(
    make_replay: MakeReplay,
    damage: str,
) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 2)
    result: Any = run(spec, settings_for(spec))
    nav = result["last_nav"]
    if damage == "amount":
        nav = replace(nav, marks=(replace(nav.marks[0], amount=D(12345)), *nav.marks[1:]))
    elif damage == "timestamp":
        nav = replace(nav, now_ms=nav.now_ms - 1)
    elif damage == "owner":
        nav = replace(nav, marks=(replace(nav.marks[0], owner="foreign"), *nav.marks[1:]))
    elif damage == "duplicate":
        nav = replace(nav, marks=(*nav.marks, nav.marks[0]))
    else:
        nav = replace(nav, net=nav.net - 1)
    with pytest.raises(DomainError):
        Risk(spec, result["cycle"].baseline).sample(
            result["final_states"], nav, result["simulation_end_ms_exclusive"]
        )


def test_risk_interval_bound_refuses_before_history_or_database(make_replay: MakeReplay) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 2)
    first = replace(spec.allocations[0], source=replace(spec.allocations[0].source, count=6000))
    market = replace(first.context.market, symbol="OTHER/QUOTE", base="OTHER")
    second = replace(
        first,
        scope=replace(first.scope, allocation="other", symbol=market.symbol),
        source=replace(first.source, symbol=market.symbol),
        context=replace(first.context, market=market),
    )
    spec = replace(
        spec,
        allocations=(first, second),
        end_ms=spec.start_ms + 6000 * MINUTE,
        interval_bars=1,
        capital=(replace(spec.capital[0], amount=D(2000)),),
    )
    with pytest.raises(DomainError, match="interval point cap"):
        run(spec, settings_for(spec))


def test_transfer_reader_refuses_unrelated_or_missing_committed_request(
    make_replay: MakeReplay,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = portfolio_replay.Engine.close
    checked = False

    async def check(engine: portfolio_replay.Engine) -> None:
        nonlocal checked
        facts = engine.facts
        assert facts is not None
        for request in ("historical-begin", "missing"):

            def read(ledger: Ledger, request_id: str = request) -> tuple[Movement, ...]:
                return transfers(ledger, request_id, engine.settings.policy, facts)

            with pytest.raises(DomainError, match="committed portfolio advance"):
                await engine.writer.submit(read)
        checked = True
        await original(engine)

    monkeypatch.setattr(portfolio_replay.Engine, "close", check)
    spec = exit_scenario(make_replay)
    run(spec, settings_for(spec))
    assert checked


def test_transfer_reader_refuses_nonconserving_currency_postings(
    make_replay: MakeReplay,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked = False

    def inspect(
        ledger: Ledger, request_id: str, policy: PortfolioPolicy, facts: ValuationFacts
    ) -> tuple[Movement, ...]:
        nonlocal checked
        result = transfers(ledger, request_id, policy, facts)
        if result:
            incoming = next(m for m in result if m.amount > 0)
            assert incoming.scope is not None
            # Temporary mock ledger only. Roll back the deliberate corruption before continuing.
            ledger.db.execute("SAVEPOINT risk_corruption")
            try:
                ledger.db.execute(
                    "UPDATE journal SET delta=? WHERE request_id=? AND account_key=? "
                    "AND currency=? AND kind='balance'",
                    (
                        codec.amount(incoming.amount + 1),
                        request_id,
                        codec.digest(incoming.scope),
                        incoming.key.currency,
                    ),
                )
                with pytest.raises(DomainError, match="conserve"):
                    transfers(ledger, request_id, policy, facts)
                checked = True
            finally:
                ledger.db.execute("ROLLBACK TO risk_corruption")
                ledger.db.execute("RELEASE risk_corruption")
        return result

    monkeypatch.setattr(portfolio_replay, "transfers", inspect)
    spec, settings = conversion_scenario(make_replay, tmp_path)
    result: Any = run(spec, settings)
    assert checked and result["cycle"].final.net == D(1065)


def test_transfer_conservation_refuses_decimal_precision_loss(
    make_replay: MakeReplay,
    tmp_path: Path,
) -> None:
    spec, settings = conversion_scenario(make_replay, tmp_path)
    facts = ValuationFacts(
        1, spec.start_ms, spec.start_ms * 1000000, "invented-precision-fault", True, (), (), ()
    )
    db = sqlite3.connect(":memory:")
    try:
        # Minimal hostile journal fixture, never an existing financial database.
        db.execute("CREATE TABLE events(request_id TEXT,payload TEXT,accepted INT)")
        db.execute(
            "CREATE TABLE journal(request_id TEXT,account_key TEXT,"
            "currency TEXT,kind TEXT,delta TEXT)"
        )
        db.execute(
            "INSERT INTO events VALUES (?,?,1)",
            ("invented-advance", codec.dumps(("portfolio_advance",))),
        )
        owners = [(codec.digest(a.scope), "balance") for a in spec.allocations]
        owners.append(
            (codec.digest((spec.capital[0].venue, spec.capital[0].account)), "unassigned")
        )
        # Exact sum is one. A 96-digit rounded running sum can falsely yield zero.
        for (owner, kind), amount in zip(sorted(owners), ("1e96", "1", "-1e96"), strict=True):
            db.execute(
                "INSERT INTO journal VALUES (?,?,?,?,?)",
                ("invented-advance", owner, "QUOTE", kind, codec.amount(D(amount))),
            )
        with pytest.raises(DomainError, match="exact supported precision"):
            transfers(
                cast(Ledger, SimpleNamespace(db=db)), "invented-advance", settings.policy, facts
            )
    finally:
        db.close()
