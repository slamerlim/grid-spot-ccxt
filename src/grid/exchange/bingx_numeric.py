"""Offline BingX numeric preparation; no client, signing, or order authority."""

from __future__ import annotations

import re
from decimal import Decimal
from urllib.parse import urlencode

from grid.domain.model import Side
from grid.domain.numbers import DomainError, decimal


def decimal_text(value: object) -> str:
    """Preserve bounded positive SDK text, never recover a value from a float."""
    if (
        type(value) is not str
        or not 1 <= len(value) <= 200
        or re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value) is None
    ):
        raise DomainError("bounded plain decimal text required")
    decimal(Decimal(value), positive=True)
    return value


def unsigned_query(frame: object, side: Side, price: str, amount: str, client_id: str) -> str:
    """Validate the fixed invented fixture and freeze its unsigned query text.

    The identifier bound describes this experiment, not BingX acceptance rules.
    No timestamp, signature, account, endpoint URL or credential is produced.
    """
    decimal_text(price)
    decimal_text(amount)
    if (
        not isinstance(side, Side)
        or type(client_id) is not str
        or re.fullmatch(r"[a-zA-Z0-9]{1,64}", client_id) is None
        or type(frame) is not dict
        or set(frame) != {"path", "api", "method", "params"}
        or frame["path"] != "trade/order"
        or frame["api"] != ["spot", "v1", "private"]
        or frame["method"] != "POST"
    ):
        raise DomainError("unexpected offline BingX request")
    payload = frame["params"]
    fields = {
        "symbol": "BASEQUOTE",
        "type": "LIMIT",
        "side": str(side).upper(),
        "newClientOrderId": client_id,
    }
    if (
        type(payload) is not dict
        or set(payload) != set(fields) | {"price", "quantity"}
        or any(type(v) is not str for v in payload.values())
        or any(payload[k] != v for k, v in fields.items())
        or Decimal(decimal_text(payload["price"])) != Decimal(price)
        or Decimal(decimal_text(payload["quantity"])) != Decimal(amount)
    ):
        raise DomainError("offline BingX payload differs from intended limit")
    return urlencode(sorted(payload.items()))
