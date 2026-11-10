"""Hand-computed expectations for the existing-core C++ differential oracle."""

import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path
from typing import cast

import pytest

from grid.domain.market import MinimumBuy, Money, TakeProfit
from grid.domain.model import Phase, Side, State
from grid.domain.numbers import DomainError
from grid.research.artifacts import canonical, digest, plain
from grid.research.history import MINUTE, RECORD, Bar, decode
from grid.research.module1_reference import (
    MAX_BARS,
    SCHEMA,
    ResearchNav,
    compare_cpp_math,
    decoder_extensions,
    fixture_context,
    golden,
    lifecycle_cases,
    math_cases,
    policy_vectors,
    research_price,
    run_bars,
    tier_triggers,
)


def cases() -> dict[str, dict[str, object]]:
    return {str(row["name"]): row for row in lifecycle_cases()}


def final(row: dict[str, object]) -> State:
    return cast(State, cast(dict[str, object], row["final"])["state"])


def bars(prices: tuple[tuple[int, int, int, int], ...]) -> tuple[Bar, ...]:
    return tuple(decode(RECORD.pack(1704067200000 + i * MINUTE, *p)) for i, p in enumerate(prices))


def test_handwritten_partial_seed_and_weighted_anchor() -> None:
    row = cases()["partial_seed"]
    steps = cast(list[dict[str, object]], row["steps"])
    tiny = cast(State, cast(dict[str, object], steps[1]["after"])["state"])
    partial = cast(State, cast(dict[str, object], steps[2]["after"])["state"])
    assert tiny.owned_base == D("0.05") and tiny.anchor is None
    assert not any(o.side is Side.SELL for o in tiny.working)
    assert partial.owned_base == D("0.4") and partial.anchor is None
    assert len([o for o in partial.working if o.side is Side.SELL]) == 1
    assert len([o for o in partial.working if o.role == "ladder"]) == 0
    state = final(row)
    assert state.anchor == D("99.55")
    assert state.position_cost == D("99.55")
    assert state.quote_cash == D("900.45")
    assert state.owned_base == 1
    assert len([o for o in state.working if o.role == "ladder"]) == 3


def test_handwritten_cancel_race_keeps_one_sell_and_proportional_basis() -> None:
    row = cases()["cancel_fill_race"]
    state = final(row)
    assert state.quote_cash == D("853.10")
    assert state.owned_base == D("1.6")
    assert state.position_cost == D("152.72")
    assert state.realized_pnl == D("5.82")
    steps = cast(list[dict[str, object]], row["steps"])
    acknowledgement = next(s for s in steps if s["event_type"] == "CancelAcknowledged")
    assert acknowledgement["before"] == acknowledgement["after"]
    assert acknowledgement["commands"] == ()
    for step in steps:
        observed = cast(State, cast(dict[str, object], step["after"])["state"])
        assert len([o for o in observed.working if o.side is Side.SELL]) <= 1


def test_handwritten_late_buy_remains_owned_next_round_without_seed() -> None:
    state = final(cases()["drain_late_buy"])
    assert state.round_id == 2 and state.phase is Phase.ACCUMULATING
    assert state.quote_cash == D("973.64")
    assert state.owned_base == D("0.4")
    assert state.position_cost == D("36.36")
    assert state.realized_pnl == D("10")
    assert state.anchor == D("90.90")
    assert not any(o.role == "seed" for o in state.working)


@pytest.mark.parametrize(
    "currency,cash,owned,cost,token",
    [
        ("base", "900", "0.999", "100", "10"),
        ("quote", "899.9", "1", "100.1", "10"),
        ("token", "900", "1", "100.1", "9.95"),
    ],
)
def test_exact_partial_fee_cash_and_cost(
    currency: str,
    cash: str,
    owned: str,
    cost: str,
    token: str,
) -> None:
    state = final(cases()[f"partial_fee_{currency}"])
    assert state.quote_cash == D(cash)
    assert state.owned_base == D(owned)
    assert state.position_cost == D(cost)
    assert state.fee_balances == (Money("TOKEN", D(token)),)


def test_real_fee_overrun_is_posted_before_exposure_halt() -> None:
    state = final(cases()["actual_fee_overrun_posts_then_halts"])
    assert state.quote_cash == 899 and state.owned_base == 1
    assert state.position_cost == 101
    assert state.reconcile_reason == "actual fee exceeds forecast"
    assert len(state.fills) == 1


def test_actual_sell_fees_preserve_proceeds_cost_and_genuine_base_residual() -> None:
    rows = cases()
    quote = final(rows["sell_fee_quote"])
    assert quote.quote_cash == D("1009.8998")
    assert quote.owned_base == quote.position_cost == 0
    assert quote.realized_pnl == D("9.8998")
    token = final(rows["sell_fee_token"])
    assert token.quote_cash == D("1010.11")
    assert token.fee_balances == (Money("TOKEN", D("9.8949")),)
    assert token.realized_pnl == quote.realized_pnl
    base = final(rows["sell_fee_base"])
    # .999 acquired; .997 sold + .001 actual fee consumes .998. Forecast
    # reserved extra settlement quanta, leaving genuine .001 base and its cost.
    assert base.quote_cash == D("900") + D("0.997") * D("110.12")
    assert base.owned_base == D("0.001")
    assert base.position_cost == D("0.10010011")
    assert base.realized_pnl == D("9.88974011")


def test_math_exact_minimum_and_rejected_upsizing_are_exported() -> None:
    rows = {str(row["name"]): row for row in math_cases()}
    assert rows["grid-100"]["expected"] == (D("90.90"), D("82.64"), D("75.13"))
    assert "collapsed" in str(rows["grid-0.02"]["error"])
    minimum = cast(MinimumBuy, rows["minimum-quote-9.09"]["expected"])
    assert minimum.amount == D("1.101")
    # 1.101 * 9.09 + ceil(0.01000809 / .0001) * .0001 + 3 * .0001.
    assert minimum.quote_required == D("10.01849")
    assert "SELL amount minimum" in str(rows["minimum-never-enlarged-for-sell"]["error"])


def test_existing_decimal96_tp_boundary_is_not_silently_replaced_by_rational_math() -> None:
    rows = {str(row["name"]): row for row in math_cases()}
    first = cast(TakeProfit, rows["aggregate-decimal96-boundary-103-38"]["expected"])
    second = cast(TakeProfit, rows["aggregate-decimal96-boundary-3-5"]["expected"])
    assert D("0.38") < first.geometric_floor < D("0.380000000001")
    assert first.price == D("0.39")
    assert D("5") < second.geometric_floor < D("5.000000000001")
    assert second.price == D("5.01")


def test_existing_simulator_round_trip_and_no_same_bar_sell() -> None:
    context = fixture_context()
    result = run_bars(context, bars(((100, 101, 99, 100), (100, 150, 99, 100))))
    assert result["fills"] == 1 and result["sell_fills"] == 0
    complete = run_bars(
        context,
        bars(
            (
                (100, 101, 99, 100),
                (100, 101, 99, 100),
                (110, 111, 100, 110),
                (110, 111, 109, 110),
            )
        ),
    )
    assert complete["fills"] == 2 and complete["sell_fills"] == 1
    assert complete["nav_at_close_excluding_exit_cost"] == 1010
    assert final(complete).realized_pnl == 10


def test_ohlc_sensitivity_retains_existing_one_side_conventions() -> None:
    values = bars(((100, 101, 99, 100), (100, 101, 99, 100), (100, 150, 50, 100)))
    strict = run_bars(fixture_context(), values)
    buy = run_bars(fixture_context(), values, model="touch_buy_priority")
    sell = run_bars(fixture_context(), values, model="touch_sell_priority")
    assert strict["fills"] == 1 and strict["ambiguous_bars"] == 1
    assert buy["fills"] == 4 and buy["sell_fills"] == 0
    assert sell["fills"] == 2 and sell["sell_fills"] == 1


def test_oracle_trace_and_trace_free_speed_path_have_same_accounting() -> None:
    values = bars(((100, 101, 99, 100), (100, 101, 99, 100), (110, 120, 100, 110)))
    full = run_bars(fixture_context(), values)
    short = run_bars(fixture_context(), values, trace=False)
    assert full["final"] == short["final"]
    assert full["synthetic_trace_sha256"] == short["synthetic_trace_sha256"]
    assert short["observations"] == []


def test_reference_rejects_unbounded_nonchronological_or_overfunded_inputs() -> None:
    context = fixture_context()
    values = bars(((100, 101, 99, 100), (100, 101, 99, 100)))
    with pytest.raises(DomainError, match="integer outside"):
        run_bars(context, values[:1])
    with pytest.raises(DomainError, match="integer outside"):
        run_bars(context, (values[0],) * (MAX_BARS + 1))
    with pytest.raises(DomainError, match="consecutive"):
        run_bars(context, (values[0], replace(values[1], timestamp=values[1].timestamp + MINUTE)))
    with pytest.raises(DomainError, match="invalid amount sign"):
        run_bars(context, values, quote_cash=D("-1"))


def test_canonical_golden_is_deterministic_and_ambient_context_independent() -> None:
    first = golden()
    with localcontext() as ambient:
        ambient.prec = 6
        second = golden()
    assert canonical(first) == canonical(second)
    claimed = first.pop("result_sha256")
    assert digest(first) == claimed
    decoded = json.loads(canonical(first))
    assert decoded["schema"] == SCHEMA
    assert decoded["lifecycle"][0]["final"]["owned_base"] == "1"


def test_stdout_cli_emits_only_versioned_deterministic_json() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "grid.research.module1_reference", "--golden"],
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert result.stderr == b""
    assert result.stdout == canonical(golden()) + b"\n"


def test_research_price_vectors_define_sharp_drop_and_fresh_price_reentry() -> None:
    rows = cast(list[dict[str, object]], policy_vectors()["prices"])
    assert [row["price"] for row in rows] == [D("90.90"), D("45.45"), D("82.64"), D("100"), D("90")]
    assert all(row["created_bar"] == 7 and row["eligible_from_bar"] == 8 for row in rows)
    market = fixture_context().market
    with pytest.raises(DomainError, match="executed anchor"):
        research_price("ladder_one", market, D("0.1"), D("50"))
    with pytest.raises(DomainError, match="last executed SELL"):
        research_price("reentry_one_step_below_sell", market, D("0.1"), D("50"))


def test_tier_precedence_counts_reserved_cash_once_and_keeps_switches_independent() -> None:
    rows = cast(list[dict[str, object]], policy_vectors()["tp"])
    expected = {row["name"]: row["expected"] for row in rows}
    assert expected["independent-tiers"] == (("exchange:A", ("A1", "A2")), ("symbol:B1", ("B1",)))
    assert expected["symbol-only"] == (
        ("symbol:A1", ("A1",)),
        ("symbol:A2", ("A2",)),
        ("symbol:B1", ("B1",)),
    )
    assert expected["exchange-only"] == (("exchange:A", ("A1", "A2")),)
    assert expected["both-optional-off"] == ()
    assert expected["global-at-threshold"] == (("global", ("A1", "A2", "B1", "B2")),)
    assert expected["unknown-nav"] is None
    # 15 total quote includes its 5 reserved; 11 marked base less 1 exit cost = 25.
    mark = ResearchNav("A1", "A", D("15"), D("5"), D("11"), D("1"), D("20"))
    assert mark.net() == D("25")
    with pytest.raises(DomainError, match="exceeds owned quote"):
        replace(mark, reserved_quote=D("16")).net()
    with pytest.raises(DomainError, match="global baseline differs"):
        tier_triggers(
            (mark,),
            {"A": D("10")},
            {"A": D("30")},
            D("31"),
            symbol_threshold=None,
            exchange_threshold=None,
        )


def test_decoder_subnormal_extension_is_explicit_not_claimed_python_parity() -> None:
    row = decoder_extensions()[0]
    assert row["python_domain_error"] == "numeric range unsupported"
    raw = bytes.fromhex(cast(str, row["raw_hex"]))
    assert len(raw) == 24
    assert raw[8:12] == b"\x01\x00\x00\x00"
    with pytest.raises(DomainError, match="numeric range unsupported"):
        decode(raw)


@pytest.mark.parametrize("fault", [None, "float_money", "wrong_cash", "missing_field", "signal"])
def test_differential_adapter_rejects_inexact_or_missing_finances_and_crashes(
    monkeypatch: pytest.MonkeyPatch,
    fault: str | None,
) -> None:
    fixtures = iter(math_cases())

    def invoke(command: Sequence[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert options["check"] is False and options["timeout"] == 30
        case = next(fixtures)
        if "error" in case:
            code = -11 if fault == "signal" and case["name"] == "grid-0.02" else 2
            return subprocess.CompletedProcess(command, code, "", '{"error":"controlled refusal"}')
        value = plain(case["expected"])
        if case["operation"] == "grid":
            value = {"prices": value}
        elif case["name"] == "minimum-base-100":
            assert isinstance(value, dict)
            if fault == "float_money":
                value["quote_required"] = 10.0
            elif fault == "wrong_cash":
                value["quote_required"] = "10.000000000001"
            elif fault == "missing_field":
                del value["quote_required"]
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

    monkeypatch.setattr("grid.research.module1_reference.subprocess.run", invoke)
    result = compare_cpp_math(Path(sys.executable))
    assert result["failed"] == (0 if fault is None else 1)
    assert result["passed"] == len(math_cases()) - (0 if fault is None else 1)
