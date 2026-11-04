"""Replay already durable synthetic venue effects before financial reconciliation."""

from decimal import Decimal as D
from typing import Any

from grid.domain.model import ActualFee, Scope, Side
from grid.domain.numbers import DomainError
from grid.execution.models import LimitRequest
from grid.execution.synthetic import SyntheticExchange


async def replay_gateway(events: list[dict[str, Any]]) -> SyntheticExchange:
    gateway = SyntheticExchange(1000)
    for row in events:
        kind, payload = row["payload"]["kind"], row["payload"]["value"]
        if kind == "submit":
            scope = Scope(**payload["scope"])
            request = LimitRequest(
                scope,
                payload["order_id"],
                payload["client_id"],
                Side(payload["side"]),
                payload["amount"],
                payload["price"],
                payload["config_revision"],
                payload["market_generation"],
            )
            await gateway.submit_limit(request)
        elif kind == "cancel":
            await gateway.cancel_limit(gateway.orders[payload].request.scope, payload)
        elif kind == "settle":
            gateway.settle_cancel(payload)
        elif kind == "fill":
            fees = tuple(
                ActualFee(
                    f["currency"],
                    D(f["amount"]),
                    None if f["quote_value"] is None else D(f["quote_value"]),
                )
                for f in payload["fees"]
            )
            gateway.fill(
                payload["external_order"],
                payload["external_id"],
                D(payload["amount"]),
                D(payload["price"]),
                fees,
            )
        else:
            raise DomainError("unknown synthetic venue journal event")
    return gateway
