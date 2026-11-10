"""Independent existing portfolio math and explicit research rounding evidence."""

import sys
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from typing import cast

import pytest

import grid.research.module1_portfolio as driver
from grid.domain.model import PlaceLimit, State
from grid.domain.numbers import DomainError
from grid.portfolio.model import ExitSlot, Nav
from grid.research.artifacts import canonical, plain
from grid.research.module1_lifecycle import _invoke


def case(name: str) -> driver.NavCase:
    return next(row for row in driver.cases() if row.name == name)


def expected(name: str) -> dict[str, object]:
    return cast(dict[str, object], driver.oracle(case(name))["expected"])


def test_handwritten_fee_reserve_and_sellable_quantity() -> None:
    single = expected("quote_single_settlement")
    assert single == {
        "raw": D("112.498006432"),
        "net": D("112.47559"),
        "sell_quantity": D("1.234"),
        "residual": D("0.00056"),
        "fee": D("0.01249"),
        "reason": "",
    }
    sixty_four = expected("quote_64_settlements")
    assert sixty_four["fee"] == D("0.01312") and sixty_four["net"] == D("112.47496")
    assert expected("zero_fee")["net"] == D("112.48808")


def test_base_fee_reduces_sellable_quantity_and_is_not_charged_again() -> None:
    exact = expected("base_fee_exact_coverage")
    assert exact["sell_quantity"] == 1 and exact["residual"] == 0
    assert exact["raw"] == D("110.01") and exact["net"] == 110 and exact["fee"] == 0
    remainder = expected("base_fee_residual")
    assert remainder["sell_quantity"] == D("1.232")
    assert remainder["residual"] == D("0.00069") and remainder["net"] == D("112.46784")


def test_cash_and_dust_remain_owned_without_invented_liquidation_proceeds() -> None:
    cash = expected("cash_only")
    assert cash["raw"] == cash["net"] == 100 and cash["residual"] == 0
    for name, owned in (("below_amount_tick", D("0.0005")), ("below_minimum_notional", D("0.1"))):
        row = expected(name)
        assert row["net"] == 100 and row["sell_quantity"] == 0 and row["residual"] == owned
        evidence = driver.oracle(case(name))
        slot = cast(ExitSlot, cast(dict[str, object], evidence["exit_proposal"])["slot"])
        assert slot.reason == "unsellable residual"


def test_fee_token_asset_and_exit_funding_are_distinct_checks() -> None:
    funded = driver.oracle(case("token_fee"))
    unfunded = driver.oracle(case("token_unfunded_exit"))
    left = cast(dict[str, object], funded["expected"])
    right = cast(dict[str, object], unfunded["expected"])
    assert left["net"] == D("114.47558") and right["net"] == D("112.47558")
    assert left["fee"] == right["fee"] == D("0.0125")
    planned = cast(dict[str, object], funded["exit_proposal"])
    command = cast(tuple[PlaceLimit, ...], planned["commands"])[0]
    assert command.order.price == D("10.13") and command.order.amount == D("1.234")
    assert len(command.order.reserves) == 2
    assert cast(State, planned["state"]).quote_cash == 100
    blocked = cast(dict[str, object], unfunded["exit_proposal"])
    assert not blocked["commands"]
    assert cast(ExitSlot, blocked["slot"]).reason == "exit fee-token funds insufficient"


def test_actual_python_unrounded_values_and_research_deltas_remain_visible() -> None:
    raw = driver.oracle(case("exact_float32_raw_mark"))
    assert raw["raw_rounding_delta"] == D("5.75714111328125e-13")
    assert raw["net_rounding_delta"] == 0
    net = driver.oracle(case("net_asset_quantization"))
    assert net["net_rounding_delta"] == D("7.942e-14")
    nav = cast(Nav, net["python_nav"])
    wrapped = cast(dict[str, object], net["expected"])
    assert nav.net == D("111.36189297763907942")
    assert wrapped["net"] == D("111.361892977639")
    token = driver.oracle(case("subquantum_token_asset"))
    assert token["raw_rounding_delta"] == token["net_rounding_delta"] == D("1e-19")
    for row in driver.cases():
        evidence = driver.oracle(row)
        assert D(0) <= cast(D, evidence["raw_rounding_delta"]) < D("2e-12")
        assert D(0) <= cast(D, evidence["net_rounding_delta"]) < D("2e-12")


def test_probe_rejects_invalid_limits_and_bounds_before_subprocess() -> None:
    with pytest.raises(DomainError):
        driver.oracle(replace(case("quote_single_settlement"), nav_order_budget=0))
    with pytest.raises(DomainError):
        driver.oracle(replace(case("quote_single_settlement"), rate=D("1")))
    with pytest.raises(DomainError):
        driver.compare(Path(sys.executable), ())
    with pytest.raises(DomainError):
        _invoke(Path(sys.executable), ("incomplete",), command="--nav-probe")


@pytest.mark.parametrize("fault", [None, "fee", "missing", "signal", "stderr"])
def test_actual_portfolio_probe_harness_compares_all_fields(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    selected = case("quote_single_settlement")

    def invoke(
        program: Path, source: tuple[str, ...], timeout: float = 30.0, *, command: str = "trace"
    ) -> tuple[int, bytes, bytes]:
        assert program.is_file() and command == "--nav-probe" and timeout == 30
        assert source == selected.arguments() and len(source) == 13
        observed = cast(dict[str, object], plain(driver.oracle(selected)["expected"]))
        if fault == "fee":
            observed["fee"] = "0.012490000001"
        elif fault == "missing":
            del observed["residual"]
        return (
            (-11 if fault == "signal" else 0),
            canonical(observed),
            (b"error" if fault == "stderr" else b""),
        )

    monkeypatch.setattr(driver, "_invoke", invoke)
    report = driver.compare(Path(sys.executable), (selected,))
    assert report["passed"] == int(fault is None) and report["failed"] == int(fault is not None)
    if fault:
        assert cast(list[dict[str, object]], report["cases"])[0]["difference"]
