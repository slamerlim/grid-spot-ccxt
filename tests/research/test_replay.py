import json
import signal
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path
from typing import Any

import pytest

from grid.domain.market import FeeRule, Money, Precision, PrecisionMode
from grid.domain.model import Side, State
from grid.domain.numbers import DomainError
from grid.domain.reducer import reserved
from grid.research.artifacts import digest, publish
from grid.research.config import bundle, load
from grid.research.history import MINUTE, RECORD
from grid.research.replay import Capital, Replay, run

from .conftest import MakeReplay


def report(spec: Replay) -> dict[str, Any]:
    result = run(spec)
    reports: Any = result["allocations"]
    return dict(reports[0])


def test_no_same_bar_new_sell_or_touch_fill(make_replay: MakeReplay) -> None:
    spec = make_replay(((100, 101, 99, 100), (100, 150, 99, 100)))
    conservative = report(spec)
    assert conservative["fills"] == 1 and conservative["sell_fills"] == 0
    assert conservative["realized_pnl"] == 0
    assert any(order.side is Side.SELL for order in conservative["unfinished_orders"])
    touched = make_replay(((100, 101, 99, 100), (100, 150, 100, 100)))
    assert report(touched)["fills"] == 0
    assert report(replace(touched, model="touch_buy_priority"))["fills"] == 1


def test_ambiguous_pending_sides_never_two_sided(make_replay: MakeReplay) -> None:
    spec = make_replay(((100, 101, 99, 100), (100, 101, 99, 100), (100, 150, 50, 100)))
    strict = report(spec)
    assert strict["ambiguous_bars"] == 1
    assert strict["fills"] == 1 and strict["sell_fills"] == 0
    alternative = report(replace(spec, model="touch_buy_priority"))
    assert alternative["fills"] > 1 and alternative["sell_fills"] == 0
    assert len([o for o in alternative["unfinished_orders"] if o.side is Side.SELL]) == 1


def test_known_round_trip_accounting_and_final_partial_interval(make_replay: MakeReplay) -> None:
    spec = make_replay(
        ((100, 101, 99, 100), (100, 101, 99, 100), (110, 111, 100, 110), (110, 111, 109, 110))
    )
    result = run(spec)
    allocation = report(spec)
    assert allocation["fills"] == 2 and allocation["sell_fills"] == 1
    assert allocation["realized_pnl"] == D("10")
    assert result["final_nav_at_close"] == D("1010")
    assert allocation["profit_factor"] is None
    intervals: Any = result["intervals"]
    assert [i["partial"] for i in intervals] == [False, True]
    assert intervals[-1]["end_ms_exclusive"] == spec.end_ms
    assert result["cash_baseline_nav"] == D("1000")
    assert result["buy_hold_nav_at_close"] > D("1000")  # type: ignore[operator]


@pytest.mark.parametrize("currency", ["QUOTE", "BASE", "TOKEN"])
def test_finite_fees_reserves_and_nav_conservation(make_replay: MakeReplay, currency: str) -> None:
    spec = make_replay(
        ((100, 101, 99, 100), (100, 101, 99, 100), (110, 120, 100, 110), (110, 120, 109, 110))
    )
    allocation = spec.allocations[0]
    fee = FeeRule(currency, D("0.001"), D("0.0001"), D("2") if currency == "TOKEN" else None, 1)
    context = replace(allocation.context, buy_fee=fee, sell_fee=fee)
    balances = (Money("TOKEN", D("10")),) if currency == "TOKEN" else ()
    capital = (*spec.capital, Capital("fake", "allocated-account", "TOKEN", D("10")))
    spec = replace(
        spec,
        allocations=(replace(allocation, context=context, fee_balances=balances),),
        capital=capital,
    )
    result = run(spec)
    allocation_result = report(spec)
    state: State = allocation_result["final_state"]
    assert state.quote_cash >= 0 and state.owned_base >= 0
    assert reserved(state, "QUOTE") <= state.quote_cash
    assert reserved(state, "BASE") <= state.owned_base
    assert all(m.amount >= 0 for m in state.fee_balances)
    assert allocation_result["fees_quote_at_modeled_conversion"] > 0
    assert result["net_change_at_close"] == (
        allocation_result["realized_pnl"] + allocation_result["unrealized_pnl_at_close"]
    )


def test_latency_and_minimum_budget_do_not_create_cash(make_replay: MakeReplay) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 5)
    assert report(replace(spec, latency_bars=5))["fills"] == 0
    allocation = replace(spec.allocations[0], quote_cash=D("50"))
    result = report(replace(spec, allocations=(allocation,)))
    assert result["fills"] == 0 and result["final_state"].quote_cash == D("50")
    assert result["notices"]
    with pytest.raises(DomainError, match="finite account capital"):
        replace(spec, allocations=(replace(allocation, quote_cash=D("1001")),))


def test_scope_ties_are_canonical_and_report_deterministic(make_replay: MakeReplay) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 5)
    first = replace(spec.allocations[0], quote_cash=D("500"))
    second = replace(first, scope=replace(first.scope, allocation="second"))
    forward = replace(spec, allocations=(first, second))
    backward = replace(spec, allocations=(second, first))
    assert run(forward) == run(backward)
    result = run(forward)
    assert result["initial_allocated_nav"] == D("1000")
    with localcontext() as context:
        context.prec = 5
        assert run(forward) == result
    checksum = result.pop("result_sha256")
    assert digest(result) == checksum


def test_missing_coverage_and_gap_refuse_replay(make_replay: MakeReplay) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 3)
    with pytest.raises(DomainError, match="coverage"):
        run(replace(spec, start_ms=spec.start_ms + MINUTE, end_ms=spec.end_ms + MINUTE))
    path = spec.allocations[0].source.path
    path.write_bytes(path.read_bytes()[:48] + RECORD.pack(spec.end_ms + MINUTE, 100, 101, 99, 100))
    with pytest.raises(DomainError, match="missing_minutes"):
        run(spec)


def test_float_precision_mismatch_refuses_and_rounding_noise_is_retained(
    make_replay: MakeReplay,
) -> None:
    spec = make_replay(((100.12, 101.12, 99.12, 100.12),) * 3)
    run(spec)  # Exchange decimal .01 ticks may acquire representational float32 noise.
    allocation = spec.allocations[0]
    market = replace(allocation.context.market, price=Precision(PrecisionMode.TICK, tick=D("1")))
    with pytest.raises(DomainError, match="float32 price incompatible"):
        run(
            replace(
                spec,
                allocations=(
                    replace(allocation, context=replace(allocation.context, market=market)),
                ),
            )
        )


def test_unlike_quotes_duplicate_scopes_and_unknown_identity_refused(
    make_replay: MakeReplay,
) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 3)
    first = spec.allocations[0]
    with pytest.raises(DomainError, match="duplicate replay scope"):
        replace(spec, allocations=(first, first))
    second_market = replace(first.context.market, quote="USD", symbol="BASE/USD")
    second = replace(
        first,
        scope=replace(first.scope, symbol="BASE/USD"),
        source=replace(first.source, symbol="BASE/USD"),
        context=replace(
            first.context,
            market=second_market,
            buy_fee=replace(first.context.buy_fee, currency="USD"),
            sell_fee=replace(first.context.sell_fee, currency="USD"),
        ),
    )
    with pytest.raises(DomainError, match="unlike quote"):
        replace(spec, allocations=(first, second))
    with pytest.raises(DomainError, match="unknown_identity"):
        run(
            replace(
                spec,
                allocations=(replace(first, source=replace(first.source, identity_evidence=None)),),
            )
        )


def test_restart_replays_identical_inputs_and_publishes_exclusively(
    make_replay: MakeReplay,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 5)
    config = tmp_path / "config.json"
    publish(config, bundle(spec))
    assert load(config) == spec
    result = run(load(config))
    assert result == run(spec)
    output = tmp_path / "result.json"
    with monkeypatch.context() as patch:

        def crash(descriptor: int) -> None:
            raise OSError("synthetic interrupted fsync")

        patch.setattr("grid.research.artifacts.os.fsync", crash)
        with pytest.raises(OSError, match="interrupted"):
            publish(output, result)
    assert not output.exists()
    rerun = run(load(config))
    assert rerun == result
    publish(output, rerun)
    with pytest.raises(FileExistsError):
        publish(output, rerun)
    assert not list(tmp_path.glob(".grid-research-*"))


def test_multiple_markets_merge_by_utc_and_scope(make_replay: MakeReplay, tmp_path: Path) -> None:
    spec = make_replay(((100, 101, 99, 100), (100, 101, 99, 100), (110, 111, 100, 110)))
    first = replace(spec.allocations[0], quote_cash=D("500"))
    path = tmp_path / "second.bin"
    path.write_bytes(
        b"".join(
            RECORD.pack(spec.start_ms + i * MINUTE, *prices)
            for i, prices in enumerate(
                ((200, 202, 198, 200), (200, 202, 198, 200), (190, 200, 185, 190))
            )
        )
    )
    market = replace(first.context.market, base="SECOND", symbol="SECOND/QUOTE")
    second = replace(
        first,
        scope=replace(first.scope, symbol=market.symbol, allocation="second"),
        source=replace(first.source, path=path, symbol=market.symbol),
        context=replace(first.context, market=market),
    )
    forward = replace(spec, allocations=(first, second))
    result = run(forward)
    assert run(replace(forward, allocations=(second, first))) == result
    assert result["final_nav_at_close"] == D("1000")  # +10 realized and -10 open mark.
    reports: Any = result["allocations"]
    assert len(reports) == 2
    assert sum((a["realized_pnl"] for a in reports), D("0")) == D("10")


def test_sell_can_fill_while_cancel_waits(make_replay: MakeReplay) -> None:
    spec = make_replay(
        ((100, 101, 99, 100),) * 4 + ((90, 100, 89, 90), (110, 120, 95, 110), (110, 111, 109, 110))
    )
    result = report(replace(spec, latency_bars=2))
    state: State = result["final_state"]
    assert result["sell_fills"] == 1
    assert state.owned_base == D("1")
    assert len([o for o in state.working if o.side is Side.SELL]) <= 1
    assert state.quote_cash > 0


def test_long_replay_keeps_state_and_reports_bounded(make_replay: MakeReplay) -> None:
    spec = make_replay(((100, 150, 99, 100),) * 800)
    result = report(spec)
    state: State = result["final_state"]
    assert result["fills"] > 100
    assert len(state.orders) <= 20 and len(state.fills) <= 8
    assert state.quote_cash >= 0 and state.owned_base >= 0


def test_sigkill_discards_unpublished_result_and_restart_flushes_final_interval(
    make_replay: MakeReplay,
    tmp_path: Path,
) -> None:
    spec = make_replay(((100, 150, 99, 100),) * 5)
    config, output = tmp_path / "config.json", tmp_path / "result.json"
    publish(config, bundle(spec))
    killed = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("crash_child.py")), str(config), str(output)],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert killed.returncode == -signal.SIGKILL, killed.stderr
    assert not output.exists()
    expected = run(spec)
    publish(output, run(load(config)))
    saved = json.loads(output.read_bytes())
    assert saved["result_sha256"] == expected["result_sha256"]
    assert saved["intervals"][-1]["partial"]
    assert saved["intervals"][-1]["end_ms_exclusive"] == spec.end_ms
