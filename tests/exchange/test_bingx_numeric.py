"""Lossless offline preparation must reject every rounded or expanded request."""

import asyncio
import json
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import ccxt  # type: ignore[import-untyped]
import ccxt.pro as ccxtpro  # type: ignore[import-untyped]
import pytest

from grid.domain.model import Side
from grid.domain.numbers import DomainError
from grid.exchange import order_audit as audit
from grid.exchange.bingx_numeric import decimal_text, unsigned_query
from grid.research.artifacts import digest

pytestmark = pytest.mark.usefixtures("async_sockets")


@pytest.mark.parametrize(
    "value",
    [
        1,
        1.0,
        True,
        Decimal("1"),
        None,
        "",
        "0",
        "-1",
        "+1",
        " 1",
        "1 ",
        "1e-8",
        "01",
        ".1",
        "1.",
        "NaN",
        "Infinity",
        "１",
        "1\n",
        "1" * 201,
        "9" * 97,
    ],
)
def test_converter_refuses_coercion_and_unsupported_text(value: object) -> None:
    with pytest.raises(DomainError):
        decimal_text(value)


def test_converter_preserves_exact_text_independent_of_ambient_precision() -> None:
    with localcontext() as context:
        context.prec = 2
        for value in ("100", "0.12345678", "123456789.12345677", "1.2300", "9" * 96):
            assert decimal_text(value) == value


def frame() -> dict[str, Any]:
    return {
        "path": "trade/order",
        "api": ["spot", "v1", "private"],
        "method": "POST",
        "params": {
            "symbol": "BASEQUOTE",
            "type": "LIMIT",
            "side": "BUY",
            "newClientOrderId": "fixture",
            "price": "123456789.12345677",
            "quantity": "0.12345678",
        },
    }


@pytest.mark.parametrize(
    "fault",
    [
        "float",
        "rounded",
        "quantity",
        "side",
        "type",
        "symbol",
        "id",
        "extra",
        "endpoint",
        "nested",
        "batch",
        "frame",
    ],
)
def test_preparer_refuses_changed_or_expanded_request(fault: str) -> None:
    value = frame()
    if fault == "endpoint":
        value["api"] = ["swap", "v2", "private"]
    elif fault == "batch":
        value["params"] = [value["params"]]
    elif fault == "frame":
        value["headers"] = {}
    else:
        key, replacement = {
            "float": ("price", 123456789.12345677),
            "rounded": ("price", "123456789.12345678"),
            "quantity": ("quantity", "0.12345679"),
            "side": ("side", "SELL"),
            "type": ("type", "MARKET"),
            "symbol": ("symbol", "REALPAIR"),
            "id": ("newClientOrderId", "other"),
            "extra": ("timeInForce", "IOC"),
            "nested": ("quantity", {"value": "0.12345678"}),
        }[fault]
        value["params"][key] = replacement
    with pytest.raises(DomainError):
        unsigned_query(value, Side.BUY, "123456789.12345677", "0.12345678", "fixture")


def test_prepared_query_is_detached_and_has_only_validated_fields() -> None:
    value = frame()
    query = unsigned_query(value, Side.BUY, "123456789.12345677", "0.12345678", "fixture")
    assert dict(parse_qsl(query, strict_parsing=True)) == value["params"]
    value["params"]["price"] = "1"
    assert dict(parse_qsl(query))["price"] == "123456789.12345677"
    with pytest.raises(DomainError):
        unsigned_query(frame(), Side.BUY, "123456789.12345677", "0.12345678", "x&price=1")


def test_paired_sdk_trial_retains_controls_and_encodes_all_candidate_values(tmp_path: Path) -> None:
    original = ccxtpro.bingx.parse_to_numeric
    output = tmp_path / "text.json"
    args = ["--bingx-text", "--code-revision", "test", "--output", str(output)]
    assert audit.main(args) == 0
    report = json.loads(output.read_bytes())
    result = report["result"]
    assert result["schema"] == "grid-bingx-text-preparation-v1"
    assert result["ccxt_version"] == "4.5.91"
    assert result["fixture_calls"] == 24 and result["client_concurrency"] == 1
    assert result["external_requests"] == 0 and result["all_fixture_checks_passed"]
    assert result["readiness"] == "NOT_READY" and result["live"] == "DISABLED"
    assert len(result["prepared_cases"]) == len(result["unmodified_controls"]) == 12
    assert report["result_payload_sha256"] == digest(result)
    assert report["report_sha256"] == digest(
        {k: v for k, v in report.items() if k != "report_sha256"}
    )
    for control, prepared in zip(
        result["unmodified_controls"], result["prepared_cases"], strict=True
    ):
        assert control["input"] == prepared["input"]
        assert control["side"] == prepared["side"]
        assert control["checks"]["exact_price_without_float"] is (control["values"] == "integer")
        assert all(prepared["checks"].values())
        query = dict(parse_qsl(prepared["unsigned_query"], strict_parsing=True))
        assert query == prepared["request"]["params"]
        assert query["price"] == prepared["input"]["price"]
        assert query["quantity"] == prepared["input"]["amount"]
    assert ccxtpro.bingx.parse_to_numeric is original
    before = output.read_bytes()
    assert audit.main(args) == 2 and output.read_bytes() == before


@pytest.mark.parametrize("fault", ["float", "rounding", "extra", "encoding"])
def test_sdk_changes_refuse_preparation_without_repair(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    original = ccxtpro.bingx.create_order_request

    def changed(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(self, *args, **kwargs)
        if fault == "float":
            result["price"] = float(result["price"])
        elif fault == "rounding":
            result["price"] = "123456789.12345678"
        else:
            result["reduceOnly"] = False
        return result

    if fault == "encoding":
        monkeypatch.setattr(ccxtpro.bingx, "rawencode", lambda *args: "changed")
    else:
        monkeypatch.setattr(ccxtpro.bingx, "create_order_request", changed)
    with pytest.raises(DomainError):
        asyncio.run(
            audit.case(
                "bingx",
                Side.BUY,
                "large_fractional",
                explicit=True,
                short_id=False,
                text_numbers=True,
            )
        )


@pytest.mark.parametrize("lower", ["sign", "fetch", "fetch2", "encoding_sign"])
def test_candidate_still_refuses_transport_and_closes_client(
    monkeypatch: pytest.MonkeyPatch, lower: str
) -> None:
    closed = []
    original_close = ccxtpro.bingx.close

    async def attempted(self: Any, *args: Any, **kwargs: Any) -> Any:
        if lower == "sign":
            return self.sign("sensitive-marker")
        return await getattr(self, lower)("sensitive-marker")

    async def close(self: Any) -> None:
        closed.append(self)
        await original_close(self)

    if lower == "encoding_sign":
        monkeypatch.setattr(ccxtpro.bingx, "parse_params", lambda self, params: self.sign("refuse"))
    else:
        monkeypatch.setattr(ccxtpro.bingx, "create_order", attempted)
    monkeypatch.setattr(ccxtpro.bingx, "close", close)
    with pytest.raises(DomainError, match="forbidden SDK"):
        asyncio.run(
            audit.case(
                "bingx", Side.SELL, "fractional", explicit=True, short_id=True, text_numbers=True
            )
        )
    assert len(closed) == 1 and closed[0].session is None


def test_candidate_is_confined_to_explicit_pinned_bingx(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(DomainError):
        asyncio.run(
            audit.case("okx", Side.BUY, "integer", explicit=True, short_id=True, text_numbers=True)
        )
    with pytest.raises(DomainError):
        asyncio.run(
            audit.case(
                "bingx", Side.BUY, "integer", explicit=False, short_id=True, text_numbers=True
            )
        )
    monkeypatch.setattr(ccxt, "__version__", "unreviewed")
    with pytest.raises(DomainError):
        asyncio.run(
            audit.case(
                "bingx", Side.BUY, "integer", explicit=True, short_id=True, text_numbers=True
            )
        )


def test_later_candidate_failure_publishes_no_partial_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original = audit.case

    async def fail(venue: str, side: Side, *args: Any, **kwargs: Any) -> dict[str, object]:
        if side is Side.SELL:
            raise DomainError("sensitive-marker")
        return await original(venue, side, *args, **kwargs)

    monkeypatch.setattr(audit, "case", fail)
    output = tmp_path / "partial.json"
    assert audit.main(["--bingx-text", "--code-revision", "test", "--output", str(output)]) == 2
    assert not output.exists() and "sensitive-marker" not in capsys.readouterr().err
