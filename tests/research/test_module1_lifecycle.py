"""Exactness and containment tests for the independent C++ lifecycle driver."""

import json
import sys
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from typing import cast

import pytest

from grid.domain.model import Reconciled, State
from grid.domain.numbers import DomainError
from grid.research import module1_lifecycle as driver
from grid.research.artifacts import canonical, plain


def case(name: str) -> driver.Case:
    return next(row for row in driver.cases() if row.name == name)


def final(row: driver.Case) -> State:
    return cast(State, cast(dict[str, object], driver.expected(row)[-1]["after"])["state"])


def test_funded_parent_traces_are_replayed_for_actual_sell_fee_observations() -> None:
    quote = case("sell_fee_quote")
    assert quote.initial.quote_cash == 1000 and quote.initial.owned_base == 0
    assert not quote.initial.orders and len(quote.events) > len(case("partial_fee_quote").events)
    state = final(quote)
    assert state.quote_cash == D("1009.8998")
    assert state.owned_base == state.position_cost == 0
    assert state.realized_pnl == D("9.8998")
    base = final(case("sell_fee_base"))
    assert base.owned_base == D("0.001") and base.position_cost == D("0.10010011")


def test_handwritten_full_transition_accounting_includes_reserves_and_cancel_race() -> None:
    seed = driver.expected(case("partial_seed"))[0]
    after = cast(dict[str, object], seed["after"])
    assert cast(State, after["state"]).quote_cash == 1000
    assert after["reserved"] == {"BASE": D(0), "QUOTE": D(100), "TOKEN": D(0)}
    assert after["available_quote"] == D(890)
    state = final(case("cancel_fill_race"))
    assert state.quote_cash == D("853.1")
    assert state.position_cost == D("152.72") and state.realized_pnl == D("5.82")


def test_error_and_recovery_traces_preserve_truth_without_invented_fills() -> None:
    missing = final(case("missing_fee_evidence"))
    assert missing.quote_cash == 1000 and missing.owned_base == 0
    assert missing.reconcile_reason == "fill fee evidence missing"
    duplicate = final(case("duplicate_and_conflicting_fill"))
    assert duplicate.quote_cash == 900 and duplicate.owned_base == 1
    assert len(duplicate.fills) == 1 and duplicate.reconcile_reason == "conflicting duplicate fill"
    recovered = final(case("unknown_then_reconciled"))
    assert recovered.quote_cash == 1000 and recovered.owned_base == 0
    assert recovered.reconcile_reason is None and len(recovered.orders) == 1
    terminal = final(case("terminal_before_fill"))
    assert terminal.quote_cash == 900 and terminal.owned_base == 1
    assert terminal.reconcile_reason is None and terminal.anchor == 100
    rebate = final(case("quote_rebate"))
    assert rebate.quote_cash == D("900.01") and rebate.position_cost == D("99.99")


def test_seeded_drain_interleavings_conserve_late_inventory_and_deduplicate() -> None:
    selected = tuple(row for row in driver.cases() if row.name.startswith("seeded_drain_"))
    assert len(selected) == 4
    for row in selected:
        state = final(row)
        assert state.round_id == 2 and state.owned_base == D("0.4")
        assert state.realized_pnl == 10 and state.position_cost + state.quote_cash == 1010
        assert len(state.fills) == 3
        assert not any(order.role == "seed" for order in state.working)


def test_tsv_is_explicit_bounded_deterministic_and_contains_all_event_types() -> None:
    roster = driver.cases()
    assert [driver.export(row) for row in roster] == [driver.export(row) for row in driver.cases()]
    text = driver.export(case("partial_seed")).decode()
    assert text.startswith("GRID_MODULE1_TRACE\t1\nSCOPE\treference\t")
    assert "CONFIG\tinitial_quote_cash\t1000\n" in text
    assert "CONFIG\tbuy_cost_max\t-\n" in text
    assert "FEE_RULE\tbuy\tQUOTE\t0\t0.0001\t-\t64\n" in text
    assert "TOKEN\tTOKEN\t10\n" in text
    assert "EVENT\tFill\tsmall\t1\t0.05\t98\t0\n" in text
    tags = {
        line.split("\t")[1]
        for row in roster
        for line in driver.export(row).decode().splitlines()
        if line.startswith("EVENT\t")
    }
    assert tags == {
        "Start",
        "Fill",
        "OrderReport",
        "SubmissionUnknown",
        "CancelAcknowledged",
        "Control",
        "Reconciled",
    }


def test_tsv_refuses_nonempty_initial_state_scope_mismatch_and_delimiter_injection() -> None:
    row = case("partial_seed")
    with pytest.raises(DomainError, match="funded empty"):
        driver.export(replace(row, initial=final(row)))
    other = replace(row.events[0], scope=replace(row.initial.scope, allocation="foreign"))
    with pytest.raises(DomainError, match="same-scope"):
        driver.export(replace(row, events=(other,)))
    injection = Reconciled(row.initial.scope, 1, 1, "reason\tEVENT\tStart", 1)
    with pytest.raises(DomainError, match="delimiter"):
        driver.export(replace(row, events=(injection,)))
    with pytest.raises(DomainError, match="bounded same-scope"):
        driver.export(replace(row, events=row.events * 100))


@pytest.mark.parametrize("fault", ["money", "missing_flag", "extra_field", "commands", "accepted"])
def test_complete_comparator_never_ignores_finance_flags_commands_or_types(fault: str) -> None:
    wanted = driver.expected(case("partial_seed"))[0]
    actual = cast(dict[str, object], plain(wanted))
    after = cast(dict[str, object], actual["after"])
    state = cast(dict[str, object], after["state"])
    if fault == "money":
        state["quote_cash"] = 1000.0
    elif fault == "missing_flag":
        del state["sell_dirty"]
    elif fault == "extra_field":
        state["synthetic_free_cash"] = "1"
    elif fault == "commands":
        actual["commands"] = []
    else:
        actual["accepted"] = 1
    with pytest.raises(DomainError):
        driver.compare_value(wanted, actual)


def test_rational_representation_allowed_only_for_nonmoney_weighted_diagnostics() -> None:
    driver.compare_value(D("99.55"), "1991/20", "state.anchor")
    driver.compare_value(D("99.55"), "1991/20", "state.lots[0].execution_price")
    with pytest.raises(DomainError, match="cannot replace"):
        driver.compare_value(D("99.55"), "1991/20", "state.position_cost")
    with pytest.raises(DomainError, match="expected"):
        driver.compare_value(D("99.55"), "1990/20", "state.anchor")


def executable(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "candidate"
    path.write_text(f"#!{sys.executable}\n{body}\n")
    path.chmod(0o700)
    return path


def test_subprocess_drains_both_pipes_and_reports_status(tmp_path: Path) -> None:
    program = executable(
        tmp_path, 'import sys; sys.stdout.write("a"*65536); sys.stderr.write("b"*65536)'
    )
    status, stdout, stderr = driver._invoke(program, tmp_path / "unused.tsv")
    assert status == 0 and stdout == b"a" * 65536 and stderr == b"b" * 65536


def test_subprocess_enforces_output_and_deadline_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(driver, "MAX_OUTPUT_BYTES", 1024)
    program = executable(tmp_path, 'import sys; sys.stdout.write("x"*2048)')
    with pytest.raises(DomainError, match="output byte bound"):
        driver._invoke(program, tmp_path / "unused.tsv")
    program = executable(tmp_path, "import time; time.sleep(10)")
    with pytest.raises(DomainError, match="deadline"):
        driver._invoke(program, tmp_path / "unused.tsv", timeout=0.05)


@pytest.mark.parametrize("fault", [None, "cash", "missing_event", "signal", "stderr"])
def test_harness_reports_all_differences_without_turning_crashes_into_rejections(
    monkeypatch: pytest.MonkeyPatch,
    fault: str | None,
) -> None:
    row = case("cancel_fill_race")

    def invoke(program: Path, source: Path, timeout: float = 30.0) -> tuple[int, bytes, bytes]:
        assert program.is_file() and source.read_bytes() == driver.export(row) and timeout == 30
        observations = cast(list[dict[str, object]], plain(driver.expected(row)))
        if fault == "cash":
            cast(dict[str, object], cast(dict[str, object], observations[-1]["after"])["state"])[
                "quote_cash"
            ] = "853.100000000001"
        elif fault == "missing_event":
            observations.pop()
        payload = b"\n".join(canonical(observed) for observed in observations) + b"\n"
        return (-11 if fault == "signal" else 0), payload, b"error" if fault == "stderr" else b""

    monkeypatch.setattr(driver, "_invoke", invoke)
    report = driver.compare(Path(sys.executable), (row,))
    assert report["failed"] == (0 if fault is None else 1)
    assert report["passed"] == (1 if fault is None else 0)
    if fault:
        assert cast(list[dict[str, object]], report["cases"])[0]["difference"]
    assert json.loads(canonical(report))["schema"] == driver.SCHEMA
