"""Fixed offline CCXT request experiment; no signing, transport or live gateway."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn

import ccxt  # type: ignore[import-untyped]
import ccxt.pro as ccxtpro  # type: ignore[import-untyped]

from grid.config import VENUE_IDS
from grid.domain.model import Scope, Side
from grid.domain.numbers import DomainError
from grid.persistence.ledger import client_identity
from grid.research.artifacts import canonical, output_directory, publish
from grid.research.provenance import Observation

SYMBOL = "BASE/QUOTE"
MARKET_ID = "BASEQUOTE"
VALUES = {
    "integer": ("100", "1"),
    "fractional": ("12.34567891", "0.12345678"),
    "large_fractional": ("123456789.12345677", "0.12345678"),
}
ENDPOINTS = {
    "okx": ("trade/order", ["private"]),
    "htx": ("v1/order/orders/place", ["spot", "private"]),
    "gate": ("orders", ["private", "spot"]),
    "xt": ("order", ["private", "spot"]),
    "bingx": ("trade/order", ["spot", "v1", "private"]),
    "kucoin": ("orders", ["private"]),
    "bitfinex": ("auth/w/order/submit", ["private"]),
}


class Intercepted(BaseException):
    """Stop even an SDK catch-Exception fallback before it assumes account mode."""


class TransportReached(BaseException):
    """A forbidden lower transport/signing path invalidates the whole experiment."""


def refuse(*args: object, **kwargs: object) -> NoReturn:
    raise TransportReached()


async def refuse_async(*args: object, **kwargs: object) -> NoReturn:
    raise TransportReached()


class Recorder:
    def __init__(self) -> None:
        self.frame: dict[str, Any] | None = None

    async def request(
        self,
        path: object,
        api: object = None,
        method: object = "GET",
        params: object = None,
        headers: object = None,
        body: object = None,
        config: object = None,
    ) -> NoReturn:
        if (
            self.frame is not None
            or not isinstance(path, str)
            or len(path) > 128
            or not isinstance(api, list)
            or not 1 <= len(api) <= 4
            or any(not isinstance(v, str) or len(v) > 32 for v in api)
            or method not in ("GET", "POST")
            or headers is not None
            or body is not None
        ):
            raise TransportReached()
        rows = params if isinstance(params, list) else [params]
        if len(rows) != 1 or not isinstance(rows[0], dict) or len(rows[0]) > 24:
            raise TransportReached()
        for key, value in rows[0].items():
            if (
                not isinstance(key, str)
                or len(key) > 64
                or type(value) not in (str, int, float, bool, type(None))
                or isinstance(value, str)
                and len(value) > 256
            ):
                raise TransportReached()
        frame = {"path": path, "api": api, "method": method, "params": params}
        raw = canonical(frame)
        if len(raw) > 16384:
            raise TransportReached()
        self.frame = json.loads(raw)  # Detach before the SDK can mutate its request dictionary.
        raise Intercepted()


def options(venue: str, explicit: bool) -> dict[str, object]:
    result: dict[str, object] = {"defaultType": "spot"}
    if explicit:
        if venue == "okx":
            result["createOrder"] = "privatePostTradeOrder"
        elif venue == "gate":
            result["unifiedAccount"] = False
        elif venue == "kucoin":
            result.update(uta=False, hf=False)
    return result


def market(venue: str) -> dict[str, object]:
    return {
        "id": MARKET_ID,
        "symbol": SYMBOL,
        "base": "BASE",
        "quote": "QUOTE",
        "baseId": "BASE",
        "quoteId": "QUOTE",
        "spot": True,
        "type": "spot",
        "contract": False,
        "active": True,
        "precision": {
            "price": 5 if venue == "bitfinex" else "0.00000001",
            "amount": 8 if venue == "bitfinex" else "0.00000001",
        },
        "limits": {},
        "info": {},
    }


def expected(venue: str, side: Side, client: str) -> tuple[dict[str, object], str, str, str]:
    row: dict[str, object] = {"symbol": MARKET_ID, "type": "limit", "side": str(side)}
    price, amount, client_field = "price", "amount", "clientOrderId"
    if venue == "okx":
        row = {
            "instId": MARKET_ID,
            "ordType": "limit",
            "side": str(side),
            "tdMode": "cash",
            "tgtCcy": "base_ccy",
        }
        price, amount, client_field = "px", "sz", "clOrdId"
    elif venue == "htx":
        row = {"symbol": MARKET_ID, "type": str(side) + "-limit", "account-id": "101"}
        client_field = "client-order-id"
    elif venue == "gate":
        row = {"currency_pair": MARKET_ID, "type": "limit", "side": str(side), "account": "spot"}
        client_field = "text"
    elif venue in {"xt", "bingx"}:
        row = {"symbol": MARKET_ID, "type": "LIMIT", "side": str(side).upper()}
        amount = "quantity"
        if venue == "xt":
            row.update(bizType="SPOT", timeInForce="GTC")
        else:
            client_field = "newClientOrderId"
    elif venue == "kucoin":
        amount, client_field = "size", "clientOid"
    elif venue == "bitfinex":
        row = {"symbol": MARKET_ID, "type": "EXCHANGE LIMIT"}
        client_field = "cid"
    row[client_field] = "t-" + client if venue == "gate" else client
    return row, price, amount, client_field


def exact(value: object, desired: Decimal) -> bool:
    # Floats are deliberately rejected even if decimal string formatting appears reversible.
    if type(value) not in (str, int):
        return False
    try:
        return Decimal(value) == desired  # type: ignore[arg-type]
    except (ArithmeticError, ValueError):
        return False


def checks(
    frame: dict[str, Any] | None,
    venue: str,
    side: Side,
    price: str,
    amount: str,
    client: str,
    explicit: bool,
) -> dict[str, bool]:
    row, price_key, amount_key, client_field = expected(venue, side, client)
    endpoint, api = ENDPOINTS[venue]
    if venue == "okx" and not explicit:
        endpoint = "trade/batch-orders"
    frame = {} if frame is None else frame
    payload = frame.get("params", {})
    if venue == "okx" and not explicit:
        payload = payload[0] if isinstance(payload, list) and len(payload) == 1 else {}
    payload = payload if isinstance(payload, dict) else {}
    desired_amount = (
        -Decimal(amount) if venue == "bitfinex" and side is Side.SELL else Decimal(amount)
    )
    return {
        "intended_order_endpoint": (frame.get("path"), frame.get("api"), frame.get("method"))
        == (endpoint, api, "POST"),
        "spot_limit_fields": all(payload.get(k) == v for k, v in row.items() if k != client_field),
        "exact_price_without_float": exact(payload.get(price_key), Decimal(price)),
        "exact_amount_without_float": exact(payload.get(amount_key), desired_amount),
        "expected_client_encoding": payload.get(client_field) == row[client_field],
        "internal_client_id_unchanged": payload.get(client_field) == client,
        "no_extra_order_fields": set(payload) == set(row) | {price_key, amount_key},
    }


async def case(
    venue: str, side: Side, values: str, *, explicit: bool, short_id: bool
) -> dict[str, object]:
    if (
        venue not in VENUE_IDS
        or not isinstance(side, Side)
        or values not in VALUES
        or type(explicit) is not bool
        or type(short_id) is not bool
    ):
        raise DomainError("unknown fixed SDK audit fixture")
    recorder = Recorder()

    async def intercept(_client: Any, *args: Any, **kwargs: Any) -> NoReturn:
        await recorder.request(*args, **kwargs)

    base: Any = getattr(ccxtpro, venue)
    offline: Any = type(
        "OfflineOrderAudit",
        (base,),
        {"request": intercept, "fetch": refuse_async, "fetch2": refuse_async, "sign": refuse},
    )
    chosen = options(venue, explicit)
    client: Any = offline({"options": chosen})
    price, amount = VALUES[values]
    internal = client_identity(Scope(venue, "invented", SYMBOL, "audit", "grid", "fixture"), 1)
    supplied = "g" + internal[:15] if short_id else internal
    outcome = "intercepted"
    try:
        if any(getattr(client, k, None) for k in ("apiKey", "secret", "password", "token")):
            raise DomainError("audit client unexpectedly has credentials")
        client.set_markets([market(venue)])
        client.accounts = [{"id": "101", "type": "spot", "info": {}}]
        try:
            async with asyncio.timeout(5):
                await client.create_order(
                    SYMBOL, "limit", str(side), amount, price, {"clientOrderId": supplied}
                )
            raise DomainError("SDK order unexpectedly returned without interception")
        except Intercepted:
            if recorder.frame is None:
                raise DomainError("SDK interception lacks a captured request") from None
        except ccxt.BadRequest:
            # Current Gate rejects the full internal ID before reaching request().
            if venue != "gate" or short_id or recorder.frame is not None:
                raise DomainError("unexpected SDK builder refusal") from None
            outcome = "builder_refused"
        except TransportReached:
            raise DomainError("forbidden SDK transport or signing path reached") from None
    finally:
        async with asyncio.timeout(3):
            await client.close()
    source = Path(inspect.getfile(base.create_order))
    if source.stat().st_size > 2 * 1024**2:
        raise DomainError("SDK source fingerprint exceeds bound")
    return {
        "venue": venue,
        "side": str(side),
        "values": values,
        "explicit_spot_fixture": explicit,
        "client_id_fixture": "short_candidate_only" if short_id else "existing_internal_digest",
        "input": {"price": price, "amount": amount, "client_id": supplied},
        "options": chosen,
        "outcome": outcome,
        "request": recorder.frame,
        "checks": checks(recorder.frame, venue, side, price, amount, supplied, explicit),
        "sdk_builder_module": base.create_order.__module__,
        "sdk_builder_file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "external_requests": 0,
    }


async def experiment(revision: str) -> dict[str, object]:
    observation = Observation.start(revision)
    rows = []
    for venue in VENUE_IDS:
        rows.append(await case(venue, Side.BUY, "integer", explicit=False, short_id=True))
        for side in Side:
            for values in VALUES:
                for short in (False, True):
                    rows.append(await case(venue, side, values, explicit=True, short_id=short))
    if len(rows) != 91:
        raise DomainError("SDK audit fixture count differs")
    result = {
        "schema": "grid-sdk-order-audit-v1",
        "mode": "offline_synthetic",
        "live": "DISABLED",
        "ccxt_version": ccxt.__version__,
        "fixture_calls": len(rows),
        "client_concurrency": 1,
        "external_requests": 0,
        "cases": rows,
        "all_fixture_checks_passed": all(
            isinstance(row["checks"], dict) and all(row["checks"].values()) for row in rows
        ),
        "readiness": "NOT_READY",
        "limitations": (
            "Captured before signing/HTTP encoding; no request was sent and no fee/fill observed.",
            "Invented metadata/account/options; no venue minimum or account-mode certification.",
            "Short client ID is a diagnostic candidate, not an implemented durable ID mapping.",
            "SDK serialization alone proves no client-ID acceptance, uniqueness or recovery.",
            "Original executor remains synthetic-only; XT/Bitfinex rule blockers remain intact.",
            "No arbitrary credentials, markets, parameters or exchange response injection.",
        ),
    }
    return observation.finish("sdk-order-audit", result)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, "offline SDK audit: invalid arguments; use --help\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = Parser(description="Fixed offline CCXT request audit; signing/transport prohibited")
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        output_directory(args.output.parent)
        if args.output.exists() or args.output.is_symlink():
            raise DomainError("existing audit result")
        report = asyncio.run(experiment(args.code_revision))
        result = report["result"]
        if (
            not isinstance(result, dict)
            or type(result.get("all_fixture_checks_passed")) is not bool
        ):
            raise DomainError("audit result missing")
        code = 0 if result["all_fixture_checks_passed"] else 1
        publish(args.output, report)
    except KeyboardInterrupt:
        print("offline SDK audit interrupted; inspect retained artifacts", file=sys.stderr)
        return 130
    except Exception:
        print("offline SDK audit refused or failed; no successful audit", file=sys.stderr)
        return 2
    print("Offline SDK observations published; every venue remains NOT_READY.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
