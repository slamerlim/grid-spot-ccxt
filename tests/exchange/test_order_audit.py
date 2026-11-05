"""Pinned asynchronous SDK builders intercepted before all signing and networking."""

import asyncio
import json
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import ccxt.pro as ccxtpro  # type: ignore[import-untyped]
import pytest

from grid.config import VENUE_IDS
from grid.domain.model import Side
from grid.domain.numbers import DomainError
from grid.exchange import order_audit as audit
from grid.research.artifacts import digest

pytestmark = pytest.mark.usefixtures("async_sockets")


def test_complete_pinned_sdk_matrix_and_preserved_negative_findings(tmp_path: Path) -> None:
    output = tmp_path / "audit.json"
    args = ["--code-revision", "test", "--output", str(output)]
    assert audit.main(args) == 1
    report = json.loads(output.read_bytes())
    assert report["operation"] == "sdk-order-audit"
    result = report["result"]
    assert result["ccxt_version"] == "4.5.91"
    assert result["schema"] == "grid-sdk-order-audit-v1"
    assert result["fixture_calls"] == len(result["cases"]) == 91
    assert result["client_concurrency"] == 1 and result["external_requests"] == 0
    assert result["readiness"] == "NOT_READY" and result["live"] == "DISABLED"
    assert not result["all_fixture_checks_passed"]
    assert report["result_payload_sha256"] == digest(result)
    assert report["report_sha256"] == digest(
        {k: v for k, v in report.items() if k != "report_sha256"}
    )
    for venue in VENUE_IDS:
        rows = [r for r in result["cases"] if r["venue"] == venue]
        assert len(rows) == 13
        assert len({r["sdk_builder_file_sha256"] for r in rows}) == 1
        assert all("ccxt.async_support." in r["sdk_builder_module"] for r in rows)
        default = next(r for r in rows if not r["explicit_spot_fixture"])
        assert default["request"]["method"] == ("GET" if venue in {"gate", "kucoin"} else "POST")
        if venue in {"gate", "kucoin"}:
            assert not default["checks"]["intended_order_endpoint"]
        for row in (r for r in rows if r["explicit_spot_fixture"]):
            flags = row["checks"]
            assert row["external_requests"] == 0
            if venue == "gate" and row["client_id_fixture"] == "existing_internal_digest":
                assert row["outcome"] == "builder_refused" and row["request"] is None
                assert not any(flags.values())
                continue
            assert row["outcome"] == "intercepted"
            assert flags["intended_order_endpoint"] and flags["spot_limit_fields"]
            assert flags["expected_client_encoding"] and flags["no_extra_order_fields"]
            assert flags["internal_client_id_unchanged"] is (venue != "gate")
            fractional = row["values"] != "integer"
            assert flags["exact_price_without_float"] is (
                not fractional or venue not in {"bingx", "bitfinex"}
            )
            assert flags["exact_amount_without_float"] is (not fractional or venue != "bingx")
            if venue == "bingx" and row["values"] == "large_fractional":
                assert D(str(row["request"]["params"]["price"])) != D(row["input"]["price"])
            if venue == "bitfinex" and row["side"] == "sell":
                assert row["request"]["params"]["amount"].startswith("-")
    before = output.read_bytes()
    assert audit.main(args) == 2 and output.read_bytes() == before


@pytest.mark.parametrize("lower", ["sign", "fetch", "fetch2"])
def test_lower_paths_are_refused_and_client_is_closed(
    monkeypatch: pytest.MonkeyPatch, lower: str
) -> None:
    closed = []
    original_close = ccxtpro.okx.close

    async def attempted(self: Any, *args: Any, **kwargs: Any) -> Any:
        if lower == "sign":
            return self.sign("sensitive-diagnostic-marker")
        return await getattr(self, lower)("sensitive-diagnostic-marker")

    async def close(self: Any) -> None:
        closed.append(self)
        await original_close(self)

    monkeypatch.setattr(ccxtpro.okx, "create_order", attempted)
    monkeypatch.setattr(ccxtpro.okx, "close", close)
    with pytest.raises(DomainError, match="forbidden SDK") as failure:
        asyncio.run(audit.case("okx", Side.BUY, "integer", explicit=True, short_id=True))
    assert "sensitive-diagnostic-marker" not in str(failure.value)
    assert len(closed) == 1 and closed[0].session is None


def test_gate_cannot_swallow_interception_and_continue_with_a_default_account() -> None:
    row: Any = asyncio.run(audit.case("gate", Side.BUY, "integer", explicit=False, short_id=True))
    # Gate's load_unified_status catches Exception and would otherwise assume non-unified.
    assert row["request"]["path"] == "detail"
    assert row["request"]["method"] == "GET"
    assert not row["checks"]["spot_limit_fields"]
    assert not issubclass(audit.Intercepted, Exception)


@pytest.mark.parametrize("fault", ["headers", "body", "nested", "oversized", "batch", "method"])
def test_recorder_rejects_unexpected_or_unbounded_requests(fault: str) -> None:
    args: dict[str, Any] = {"path": "orders", "api": ["private"], "method": "POST", "params": {}}
    if fault in {"headers", "body"}:
        args[fault] = "sensitive-marker"
    elif fault == "nested":
        args["params"] = {"bad": {"nested": True}}
    elif fault == "oversized":
        args["params"] = {"bad": "x" * 257}
    elif fault == "batch":
        args["params"] = [{}, {}]
    else:
        args["method"] = "DELETE"
    recorder = audit.Recorder()
    with pytest.raises(audit.TransportReached):
        asyncio.run(recorder.request(**args))
    assert recorder.frame is None


def test_capture_detaches_request_and_refuses_second_attempt() -> None:
    recorder = audit.Recorder()
    payload = {"price": "100", "amount": "1"}
    with pytest.raises(audit.Intercepted):
        asyncio.run(recorder.request("orders", ["private"], "POST", payload))
    payload["price"] = "wrong"
    assert recorder.frame is not None and recorder.frame["params"]["price"] == "100"
    with pytest.raises(audit.TransportReached):
        asyncio.run(recorder.request("orders", ["private"], "POST", {}))


@pytest.mark.parametrize("bad", [1.0, True, None, "NaN", "Infinity", "not-a-number", "1.01"])
def test_numeric_contract_does_not_accept_float_or_coercion(bad: object) -> None:
    assert not audit.exact(bad, D(1))
    assert audit.exact("1.000", D(1)) and audit.exact(1, D(1))


@pytest.mark.parametrize(
    "fault", ["market", "margin", "extra", "price", "amount", "id", "endpoint"]
)
def test_contract_flags_changed_spot_limit_request(fault: str) -> None:
    frame: dict[str, Any] = {
        "path": "orders",
        "api": ["private", "spot"],
        "method": "POST",
        "params": {
            "currency_pair": audit.MARKET_ID,
            "type": "limit",
            "side": "buy",
            "account": "spot",
            "price": "100",
            "amount": "1",
            "text": "t-fixture",
        },
    }
    if fault == "endpoint":
        frame["api"] = ["private", "futures"]
    else:
        key, value = {
            "market": ("type", "market"),
            "margin": ("account", "margin"),
            "extra": ("auto_borrow", True),
            "price": ("price", "101"),
            "amount": ("amount", "2"),
            "id": ("text", "t-other"),
        }[fault]
        frame["params"][key] = value
    flags = audit.checks(frame, "gate", Side.BUY, "100", "1", "fixture", True)
    assert not all(v for k, v in flags.items() if k != "internal_client_id_unchanged")


def test_late_failure_never_publishes_partial_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original = audit.case

    async def fail(venue: str, *args: Any, **kwargs: Any) -> dict[str, object]:
        if venue == "htx":
            raise DomainError("sensitive-diagnostic-marker")
        return await original(venue, *args, **kwargs)

    monkeypatch.setattr(audit, "case", fail)
    output = tmp_path / "partial.json"
    assert audit.main(["--code-revision", "test", "--output", str(output)]) == 2
    assert not output.exists() and "sensitive-diagnostic-marker" not in capsys.readouterr().err


def test_cli_has_no_credential_or_arbitrary_order_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as failure:
        audit.main(["--api-key", "sensitive-diagnostic-marker"])
    assert failure.value.code == 2 and "sensitive-diagnostic-marker" not in capsys.readouterr().err


def test_cancellation_closes_owned_client(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = []
    original_close = ccxtpro.okx.close

    async def canceled(self: Any, *args: Any, **kwargs: Any) -> None:
        raise asyncio.CancelledError()

    async def close(self: Any) -> None:
        closed.append(self)
        await original_close(self)

    monkeypatch.setattr(ccxtpro.okx, "create_order", canceled)
    monkeypatch.setattr(ccxtpro.okx, "close", close)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(audit.case("okx", Side.BUY, "integer", explicit=True, short_id=True))
    assert len(closed) == 1 and closed[0].session is None
