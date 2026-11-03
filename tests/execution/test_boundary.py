"""Real pinned CCXT formatting, captured synthetic payloads and input refusal."""

import asyncio
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import ccxt  # type: ignore[import-untyped]
import pytest

from grid.config import VENUE_IDS
from grid.domain.model import Context, Scope, Start, State
from grid.domain.reducer import reduce
from grid.execution.models import Evidence, ExecutionError, LimitRequest, limit_request
from grid.persistence.records import Bounds

from .helpers import EVIDENCE, harness


@pytest.mark.parametrize("venue", VENUE_IDS)
def test_actual_ccxt_precision_reaches_captured_limit_payload(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    venue: str,
) -> None:
    # Instantiation + set_markets + numerical formatting only. No transport calls.
    sdk: Any = getattr(ccxt, venue)()
    sdk.set_markets(
        [
            {
                "id": "BASEQUOTE",
                "symbol": "BASE/QUOTE",
                "base": "BASE",
                "quote": "QUOTE",
                "baseId": "BASE",
                "quoteId": "QUOTE",
                "spot": True,
                "type": "spot",
                "contract": False,
                "active": True,
                "precision": {
                    "price": 5 if venue == "bitfinex" else "0.01",
                    "amount": 8 if venue == "bitfinex" else "0.001",
                },
                "limits": {},
            }
        ]
    )

    def formatter(scope: Scope, price: D, amount: D) -> tuple[str, str]:
        return (
            str(sdk.price_to_precision(scope.symbol, str(price))),
            str(sdk.amount_to_precision(scope.symbol, str(amount))),
        )

    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            h.executor.formatter = formatter
            assert await h.service() and await h.send()
            sent = h.venue.sent[0]
            assert sent.order_type == "limit" and sent.price == "100" and sent.amount == "1"
            assert type(sent.price) is str and type(sent.amount) is str
            if venue == "bitfinex":
                # Compound constraint: five significant digits AND <= 8 decimals.
                assert sdk.price_to_precision("BASE/QUOTE", "0.000123456") == "0.00012346"
                assert sdk.price_to_precision("BASE/QUOTE", "0.000000123456") == "0.00000012"
                assert sdk.amount_to_precision("BASE/QUOTE", "0.000000019") == "0.00000001"

    asyncio.run(run())


@pytest.mark.parametrize("venue", ["bitfinex", "xt"])
def test_unrepresented_venue_rules_block_even_with_generic_evidence(
    state: State,
    context: Context,
    venue: str,
) -> None:
    scope = replace(state.scope, venue=venue)
    order = reduce(state, Start(state.scope), context).state.orders[0]
    with pytest.raises(ExecutionError, match="unverified"):
        limit_request(scope, order, context, EVIDENCE)


@pytest.mark.parametrize(
    "change",
    [
        "round_price",
        "round_amount",
        "float",
        "fee",
        "spot",
        "rules",
        "stale_book",
        "stale_fees",
        "stale_rules",
        "config",
        "generation",
        "derivative",
        "paused",
    ],
)
def test_limit_boundary_refuses_changed_numbers_and_unverified_inputs(
    state: State,
    context: Context,
    change: str,
) -> None:
    order = reduce(state, Start(state.scope), context).state.orders[0]
    scope, evidence = state.scope, EVIDENCE
    match change:
        case "fee":
            evidence = Evidence(True, True, False)
        case "spot":
            evidence = Evidence(False, True, True)
        case "rules":
            evidence = Evidence(True, False, True)
        case "stale_book":
            context = replace(context, book=None)
        case "stale_fees":
            context = replace(context, fees_observed_ms=0)
        case "stale_rules":
            context = replace(context, rules_observed_ms=0)
        case "config":
            context = replace(context, config_revision=2)
        case "generation":
            context = replace(context, market=replace(context.market, generation=2))
        case "derivative":
            scope = replace(scope, symbol="BASE/QUOTE:QUOTE")
        case "paused":
            context = replace(context, allow_new_exposure=False)

    def formatter(scope: Scope, price: D, amount: D) -> tuple[str, str]:
        if change == "round_price":
            return "99.99", str(amount)
        if change == "round_amount":
            return str(price), "0.999"
        if change == "float":
            return 100.0, 1.0  # type: ignore[return-value]
        return str(price), str(amount)

    with pytest.raises(ExecutionError):
        limit_request(scope, order, context, evidence, formatter)


def test_market_type_and_hidden_params_have_no_gateway_entrypoint(
    state: State,
    context: Context,
) -> None:
    order = reduce(state, Start(state.scope), context).state.orders[0]
    request = limit_request(state.scope, order, context, EVIDENCE)
    values = {
        "scope": request.scope,
        "order_id": request.order_id,
        "client_id": request.client_id,
        "side": request.side,
        "amount": request.amount,
        "price": request.price,
        "config_revision": request.config_revision,
        "market_generation": request.market_generation,
    }
    for override in ({"order_type": "market"}, {"params": {"type": "market"}}):
        with pytest.raises(TypeError):
            LimitRequest(**values, **override)  # type: ignore[arg-type]
