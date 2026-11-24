"""Offline observable contracts for immutable public archive acquisition."""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import zipfile
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_socket

from scripts import historical_collect as hc
from scripts import historical_coverage as coverage

START = hc.utc_ms("2023-07-01T00:00:00Z")


@pytest.fixture(autouse=True)
def unix_event_loop_only() -> None:
    pytest_socket.enable_socket()
    pytest_socket.disable_socket(allow_unix_socket=True)


def gate_row(stamp: int = START, close: str = "10") -> str:
    return f"{stamp // 1000},30,{close},11,9,10\n"


def entry(tmp: Path) -> dict[str, Any]:
    local = tmp / "local.bin"
    local.write_bytes(hc.BAR.pack(START, 10, 11, 9, 10))
    return {
        "exchange": "gate",
        "market": "BTC/USDT",
        "month": "202307",
        "source_file": str(local),
        "source_identity": hc.identity(local),
        "source_sha256": hc.sha256(local),
        "required_start_ms": START,
        "required_end_ms": START + 3 * hc.MINUTE,
        "url": hc.archive_url("gate", "BTC/USDT", "202307"),
    }


def test_months_exclusive_utc_year_and_leap_boundaries() -> None:
    a, b = hc.utc_ms("2023-12-31T23:59Z"), hc.utc_ms("2024-03-01T00:00Z")
    result = list(hc.months(a, b))
    assert [r[0] for r in result] == ["202312", "202401", "202402"]
    assert result[0][1] == a and result[-1][2] == b
    assert (result[-1][2] - result[-1][1]) // hc.MINUTE == 29 * 1440
    with pytest.raises(ValueError):
        list(hc.months(a, a))
    with pytest.raises(ValueError):
        hc.utc_ms("2023-01-01")


@pytest.mark.parametrize(
    "url",
    [
        "https://download.gatedata.org.evil.test/spot/candlesticks_1m/202307/BTC_USDT-202307.csv.gz",
        "https://user:secret@download.gatedata.org/"  # pragma: allowlist secret
        "spot/candlesticks_1m/202307/BTC_USDT-202307.csv.gz",
        "http://download.gatedata.org/spot/candlesticks_1m/202307/BTC_USDT-202307.csv.gz",
        "https://api.gateio.ws/api/v4/spot/orders",
        "https://download.gatedata.org/futures/candlesticks_1m/202307/BTC_USDT-202307.csv.gz",
        "https://download.gatedata.org/spot/candlesticks_1m/202307/BTC_USDT-202307.csv.gz?token=x",
        "https://download.gatedata.org/spot/candlesticks_1m/202307/BTC_USDT-202307.csv.gz#fragment",
    ],
)
def test_transport_rejects_other_hosts_private_endpoints_and_query(url: str) -> None:
    with pytest.raises(ValueError):
        hc.check_url(url, "gate", "BTC/USDT", "202307", "candles")


def test_exact_url_and_invalid_market_month() -> None:
    url = hc.archive_url("gate", "ETH/BTC", "202307", "trades")
    assert url == "https://download.gatedata.org/spot/deals/202307/ETH_BTC-202307.csv.gz"
    hc.check_url(url, "gate", "ETH/BTC", "202307", "trades")
    for market, month in [("../BTC/USDT", "202307"), ("BTC/USDT", "202313")]:
        with pytest.raises(ValueError):
            hc.archive_url("gate", market, month)


@pytest.mark.parametrize(
    "bad",
    [
        gate_row() + gate_row(),
        gate_row(START + hc.MINUTE) + gate_row(),
        gate_row().replace(",11,9,10", ",8,9,10"),
        gate_row().replace(",30,", ",-1,"),
        gate_row().replace(",10,11", ",NaN,11"),
        gate_row().replace(str(START // 1000), str(START // 1000 + 1)),
        gate_row(hc.utc_ms("2023-08-01T00:00Z")),
    ],
)
def test_invalid_candle_content_rejected(tmp_path: Path, bad: str) -> None:
    path = tmp_path / "raw.csv"
    path.write_text(bad)
    with pytest.raises((ValueError, ArithmeticError)):
        list(hc.candles(path, "gate", "202307"))


def test_decimal_precision_and_overlapping_conflict_classification(tmp_path: Path) -> None:
    item = entry(tmp_path)
    path = tmp_path / "original.csv"
    exact = "10.123456789012345678901234567890123456789"
    path.write_text(gate_row(close=exact) + gate_row(START + hc.MINUTE))
    result = list(hc.candles(path, "gate", "202307"))
    assert str(result[0].prices[3]) == exact
    report = hc.validate_file(path, item, "candles")
    assert report["conflicting_minutes"] == report["new_minutes"] == 1
    assert report["within_required_period"] == 2
    assert hc.sha256(Path(item["source_file"])) == item["source_sha256"]


def test_float32_loss_is_not_a_conflict(tmp_path: Path) -> None:
    item = entry(tmp_path)
    path = tmp_path / "original.csv"
    path.write_text(gate_row(close="10.000000000000000001"))
    report = hc.validate_file(path, item, "candles")
    assert report["float32_matching_minutes"] == 1
    assert report["conflicting_minutes"] == 0


def test_htx_candle_schema_precise_prices_and_both_volume_units(tmp_path: Path) -> None:
    path = tmp_path / "htx.csv"
    exact = "0.012345678901234567890123456789"
    path.write_text(
        "id,open,close,high,low,vol,amount\n"
        f"{START // 1000},{exact},0.02,0.03,0.01,0.056789,2.123456789\n"
    )
    candle = list(hc.candles(path, "htx", "202307"))[0]
    assert candle.time_ms == START
    assert candle.prices == tuple(map(Decimal, (exact, "0.03", "0.01", "0.02")))
    assert candle.quote_volume == Decimal("0.056789")
    assert candle.base_volume == Decimal("2.123456789")
    url = hc.archive_url("htx", "ETH/BTC", "202307")
    assert url.endswith("/klines/spot/monthly/ETHBTC/1min/ETHBTC-1min-2023-07.zip")
    hc.check_url(url, "htx", "ETH/BTC", "202307", "candles")


def test_htx_trade_schema_preserves_milliseconds_and_empty_minutes(tmp_path: Path) -> None:
    path = tmp_path / "htx-trades.csv"
    path.write_text(
        "id,ts,price,amount,direction\n"
        f"1001,{START + 123},0.02,1.123456789,buy\n"
        f"1002,{START + 120456},0.03,0.2,sell\n"
    )
    rows = list(hc.trades(path, "htx", "202307"))
    assert rows[0].seconds == Decimal(START + 123) / 1000
    assert rows[0].trade_id == "1001" and rows[0].side == "buy"
    assert rows[0].raw[1] == str(START + 123)
    candles = list(hc.reconstruct(iter(rows)))
    assert [r.time_ms for r in candles] == [START, START + 2 * hc.MINUTE]
    assert candles[0].base_volume == Decimal("1.123456789")
    assert candles[0].quote_volume == Decimal("0.02246913578")


def test_htx_daily_urls_bound_to_spot_pair_month_and_kind() -> None:
    for kind in ("candles", "trades"):
        url = hc.htx_daily_url("ETH/BTC", "2026-08-31", kind)
        hc.check_url(url, "htx", "ETH/BTC", "202608", kind)
        for bad in (
            url.replace("/spot/", "/futures/"),
            url.replace("ETH-BTC", "BTC-USDT"),
            url.replace("2026-08-31", "2026-09-01"),
            url.replace("www.htx.com", "evil.test"),
            url + "?token=test",
        ):
            with pytest.raises(ValueError):
                hc.check_url(bad, "htx", "ETH/BTC", "202608", kind)
    with pytest.raises(ValueError):
        hc.htx_daily_url("ETH/BTC", "2026-02-30")


def test_trade_reconstruction_real_empty_minutes_and_fractional_time(tmp_path: Path) -> None:
    path = tmp_path / "trades.csv"
    t = START // 1000
    path.write_text(f"{t}.000001,1,10,0.1,1\n{t}.5,2,12,0.2,2\n{t + 120}.999999,3,9,0.3,1\n")
    rows = list(hc.trades(path, "gate", "202307"))
    assert rows[0].seconds == Decimal(f"{t}.000001")
    result = list(hc.reconstruct(iter(rows)))
    assert [r.time_ms for r in result] == [START, START + 2 * hc.MINUTE]
    assert result[0].prices == tuple(map(Decimal, ("10", "12", "10", "12")))
    assert result[0].base_volume == Decimal("0.3")
    assert result[0].quote_volume == Decimal("3.4")
    assert list(hc.reconstruct(iter([]))) == []


def test_duplicate_trade_ids_rejected(tmp_path: Path) -> None:
    path = tmp_path / "trades.csv"
    row = f"{START // 1000},1,10,1,1\n"
    path.write_text(row * 2)
    with pytest.raises(ValueError, match="duplicate trade"):
        list(hc.trades(path, "gate", "202307"))


def test_inversion_alignment_stale_age_and_no_future_prices() -> None:
    now = START + hc.MINUTE
    inverse = hc.valuation([(START, Decimal("0.8"), True)], now)
    assert inverse["rate"] == "1.25" and inverse["quality"] == "derived"
    for price in (None, Decimal(0), Decimal("NaN")):
        assert hc.valuation([(START, price, True)], now)["quality"] == "unavailable"
    assert hc.valuation([(START, Decimal(2), False)], START)["rate"] is None
    assert hc.valuation([(START, Decimal(2), False)], now + hc.MINUTE)["rate"] is None
    stale = hc.valuation([(START, Decimal(2), False)], now + hc.MINUTE, max_age_ms=hc.MINUTE)
    assert stale["quality"] == "stale" and stale["source_age_ms"] == [hc.MINUTE]
    route = [(START, Decimal(2), False), (START, Decimal(3), True)]
    assert hc.valuation(route, now)["quality"] == "derived"
    assert hc.valuation([(START + hc.MINUTE, Decimal(2), False), route[1]], now)["rate"] is None


@pytest.mark.parametrize("member", ["../evil.csv", "/evil.csv", "nested/evil.csv", "x.txt"])
def test_zip_member_traversal_and_unexpected_types(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "raw.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(member, gate_row())
    with pytest.raises(ValueError, match="Unsafe"):
        hc.extract(archive, tmp_path / "safe.csv")
    assert not (tmp_path.parent / "evil.csv").exists()


def test_gzip_limit_crc_and_safe_original_bytes(tmp_path: Path) -> None:
    archive = tmp_path / "raw.gz"
    archive.write_bytes(gzip.compress(gate_row().encode()))
    hc.extract(archive, tmp_path / "safe.csv")
    assert (tmp_path / "safe.csv").read_text() == gate_row()
    with pytest.raises(ValueError, match="size limit"):
        hc.extract(archive, tmp_path / "small.csv", limit=5)
    archive.write_bytes(archive.read_bytes()[:-5])
    with pytest.raises((EOFError, OSError)):
        hc.extract(archive, tmp_path / "truncated.csv")


def test_zip_multiple_members_symlink_and_size_refused(tmp_path: Path) -> None:
    for index, kind in enumerate(("multiple", "symlink", "oversize")):
        archive = tmp_path / f"{index}.zip"
        with zipfile.ZipFile(archive, "w") as z:
            info = zipfile.ZipInfo("file.csv")
            if kind == "symlink":
                info.external_attr = 0o120777 << 16
            z.writestr(info, gate_row())
            if kind == "multiple":
                z.writestr("second.csv", gate_row())
        with pytest.raises(ValueError):
            hc.extract(archive, tmp_path / f"{index}.csv", 5 if kind == "oversize" else 1024)


class FakeTransport(hc.ArchiveTransport):
    def __init__(self, content: str, status: int = 200) -> None:
        super().__init__()
        self.content, self.status, self.calls = content, status, 0

    async def request(
        self,
        item: dict[str, Any],
        method: str,
        target: Path | None = None,
        *,
        kind: str = "candles",
    ) -> dict[str, Any]:
        self.calls += 1
        if self.status == 200:
            assert target is not None
            if item["exchange"] == "htx":
                with zipfile.ZipFile(target, "w") as zipped:
                    zipped.writestr("source.csv", self.content)
            else:
                target.write_bytes(gzip.compress(self.content.encode()))
        return {"status_code": self.status, "url": item["url"]}


def test_collection_resume_checksums_and_no_overwrite(tmp_path: Path) -> None:
    item = entry(tmp_path)
    root = tmp_path / "collection"
    transport = FakeTransport(gate_row())
    result = asyncio.run(hc.collect_one(root, item, transport))
    assert result["status"] == hc.VALID
    destination = hc.artifact_dir(root, item, "candles")
    before = {p.name: (p.stat().st_mtime_ns, hc.sha256(p)) for p in destination.iterdir()}
    again = asyncio.run(hc.collect_one(root, item, transport))
    assert again == result and transport.calls == 1
    assert before == {p.name: (p.stat().st_mtime_ns, hc.sha256(p)) for p in destination.iterdir()}
    (destination / "original.csv").write_text("corruption")
    with pytest.raises(ValueError, match="checksum"):
        asyncio.run(hc.collect_one(root, item, transport))
    assert transport.calls == 1


def test_http_404_and_invalid_download_are_distinct(tmp_path: Path) -> None:
    item = entry(tmp_path)
    root = tmp_path / "collection"
    result = asyncio.run(hc.collect_one(root, item, FakeTransport("", 404)))
    assert result["status"] == hc.UNAVAILABLE
    result = asyncio.run(hc.collect_one(root, item, FakeTransport("not,candles\n")))
    assert result["status"] == hc.INVALID
    assert (hc.artifact_dir(root, item, "candles") / "original.csv.gz").exists()


def test_retryable_errors_and_protected_output() -> None:
    assert all(hc.retryable(x) for x in (429, 500, 502, 503, 504))
    assert not any(hc.retryable(x) for x in (200, 301, 400, 401, 403, 404))
    with (
        pytest.raises(ValueError, match="Protected"),
        hc.collection_lock(Path("/opt/datasets/crypto_1m")),
    ):
        pytest.fail("must not enter")


def test_manifest_reuses_404_without_network(tmp_path: Path) -> None:
    item = entry(tmp_path)
    item.update(status=hc.UNCHECKED, local_candles=1)
    hc.save_new(tmp_path / "monthly-inventory.json", [item])
    check = {**item, "status_code": 404}
    (tmp_path / "availability-head.jsonl").write_text(json.dumps(check) + "\n")
    result = hc.checked_entries(tmp_path)
    assert result[0]["status"] == hc.UNAVAILABLE
    with pytest.raises(FileExistsError):
        hc.save_new(tmp_path / "monthly-inventory.json", [])


def test_bounded_copy_exact_limit() -> None:
    target = io.BytesIO()
    assert hc.bounded_copy(io.BytesIO(b"123"), target, 3) == 3
    assert target.getvalue() == b"123"


def test_normalization_retains_original_rows_values_and_checksum(tmp_path: Path) -> None:
    item = entry(tmp_path)
    root = tmp_path / "collection"
    raw = gate_row(START + hc.MINUTE) + gate_row()
    result = asyncio.run(hc.collect_one(root, item, FakeTransport(raw)))
    assert result["status"] == hc.INVALID
    original = hc.artifact_dir(root, item, "candles") / "original.csv"
    before = hc.sha256(original)
    normalized = coverage.normalize_order(root, item, "candles")
    assert normalized["rows_retained"] == 2
    assert normalized["rows_added"] == normalized["rows_removed"] == 0
    assert hc.sha256(original) == before
    assert (
        list(hc.candles(Path(normalized["normalized_file"]), "gate", "202307"))[0].time_ms == START
    )
    assert coverage.normalize_order(root, item, "candles") == normalized


def test_normalization_does_not_hide_duplicate_candles(tmp_path: Path) -> None:
    item = entry(tmp_path)
    root = tmp_path / "collection"
    asyncio.run(hc.collect_one(root, item, FakeTransport(gate_row() * 2)))
    with pytest.raises(ValueError, match="Duplicate"):
        coverage.normalize_order(root, item, "candles")


def test_current_htx_daily_header_order_units_and_archive_day(tmp_path: Path) -> None:
    item = entry(tmp_path)
    item.update(exchange="htx", url=hc.htx_daily_url("BTC/USDT", "2023-07-01"))
    root = tmp_path / "collection"
    raw = (
        "instId,open,high,low,close,vol,volCcy,volCcyQuote,ts\n"
        f"BTC-USDT,10,11,9,10,2,20,20,{START // 1000 + 60}\n"
        f"BTC-USDT,10,11,9,10,2,20,20,{START // 1000}\n"
    )
    receipt = asyncio.run(hc.collect_one(root, item, FakeTransport(raw)))
    assert receipt["status"] == hc.INVALID
    original = hc.artifact_dir(root, item, "candles") / "original.csv"
    before = hc.sha256(original)
    normalized = coverage.normalize_order(root, item, "candles")
    rows = list(hc.candles(Path(normalized["normalized_file"]), "htx", "202307", "BTC/USDT"))
    assert [r.time_ms for r in rows] == [START, START + hc.MINUTE]
    assert rows[0].base_volume is None and rows[0].quote_volume is None
    assert rows[0].reported_volume == Decimal(2)
    assert normalized["rows_retained"] == 2 and hc.sha256(original) == before
    assert hc.source_period(item) == (START - 480 * hc.MINUTE, START + 960 * hc.MINUTE)
    with pytest.raises(ValueError, match="instrument mismatch"):
        list(hc.candles(original, "htx", "202307", "ETH/BTC"))


def test_current_htx_daily_trades_preserve_instrument_id_and_milliseconds(tmp_path: Path) -> None:
    file = tmp_path / "daily.csv"
    file.write_text(
        "instId,tradeId,px,side,size,ts\n"
        f"ETH-BTC,101,0.031583000000000000,buy,0.029200000000000000,{START + 123}\n"
    )
    rows = list(hc.trades(file, "htx", "202307", "ETH/BTC"))
    assert rows[0].seconds == Decimal(START + 123) / 1000
    assert rows[0].price == Decimal("0.031583000000000000")
    assert rows[0].amount == Decimal("0.029200000000000000")
    assert rows[0].trade_id == "101" and rows[0].raw[0] == "ETH-BTC"
    with pytest.raises(ValueError, match="instrument mismatch"):
        list(hc.trades(file, "htx", "202307", "BTC/USDT"))


def test_daily_trade_coverage_never_classifies_the_rest_of_the_month(tmp_path: Path) -> None:
    item = entry(tmp_path)
    item.update(exchange="htx", url=hc.htx_daily_url("BTC/USDT", "2023-07-02", "trades"))
    root = tmp_path / "collection"
    raw = "instId,tradeId,px,side,size,ts\n"
    receipt = asyncio.run(hc.collect_one(root, item, FakeTransport(raw), kind="trades"))
    assert receipt["status"] == hc.VALID

    class EmptySources:
        def __init__(self) -> None:
            self.root = root

        def mask(self, venue: str, market: str, start: int, end: int, completed: bool) -> bytearray:
            return bytearray((end - start) // hc.MINUTE)

    report = coverage.trading_report(
        cast(coverage.Sources, EmptySources()),
        {
            "exchange": "htx",
            "market": "BTC/USDT",
            "start_ms": START,
            "end_ms": START + 3 * 1440 * hc.MINUTE,
        },
    )
    assert report["totals"]["absent_candle_minutes_with_no_trades_observed_in_source"] == 1440
    assert report["totals"]["unresolved_absences_without_trade_source"] == 2880


def test_old_trade_whitespace_and_descending_source_order(tmp_path: Path) -> None:
    item = entry(tmp_path)
    item["url"] = hc.archive_url("gate", "BTC/USDT", "202307", "trades")
    root = tmp_path / "collection"
    t = START // 1000
    raw = f"{t + 120}.1, 2, 9, 0.3, 1\n{t}.000001, 1, 10, 0.1, 1\n"
    asyncio.run(hc.collect_one(root, item, FakeTransport(raw), kind="trades"))
    normalized = coverage.normalize_order(root, item, "trades")
    assert normalized["trade_count"] == 2
    assert normalized["candles"] == 2
    assert normalized["rows_removed"] == 0


def test_completed_candle_masks_include_recovery_without_lookahead(tmp_path: Path) -> None:
    item = entry(tmp_path)
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        json.dumps(
            {
                "venue": "gate",
                "market": "BTC/USDT",
                "file": "local.bin",
                "source_identity": item["source_identity"],
                "sha256": item["source_sha256"],
                "first_utc": hc.utc(START),
                "last_utc": hc.utc(START),
            }
        )
        + "\n"
    )
    external = tmp_path / "external.csv"
    external.write_text(gate_row(START + hc.MINUTE))
    verification = tmp_path / "verification.jsonl"
    verification.write_text(
        json.dumps(
            {
                "exchange": "gate",
                "market": "BTC/USDT",
                "month": "202307",
                "status": hc.VALID,
                "accepted_csv": str(external),
                "url": item["url"],
                "files": {"original.csv": {"sha256": hc.sha256(external)}},
                "new_minutes": 1,
                "conflicting_minutes": 0,
            }
        )
        + "\n"
    )
    sources = coverage.Sources(tmp_path, verification, audit, dataset_root=tmp_path)
    try:
        assert list(sources.mask("gate", "BTC/USDT", START, START + 4 * hc.MINUTE)) == [0, 1, 2, 0]
        report = coverage.valuation_report(
            sources,
            {
                "exchange": "gate",
                "quote": "BTC",
                "start_ms": START,
                "end_ms": START + 4 * hc.MINUTE,
            },
        )
        assert report["totals"]["observed"] == report["totals"]["unavailable"] == 2
        assert report["totals"]["incremental_valuation_minutes"] == 1
        assert report["totals"]["stale"] == 0
        assert report["stale_age_diagnostics_only"]["max_age_ms"] == hc.MINUTE
    finally:
        sources.close()


def test_stale_percentiles_count_every_absent_minute() -> None:
    # Gaps of length 1 and 3 give ages [1, 1, 2, 3], not two sampled ages.
    result = coverage.stale_distribution(Counter({1: 1, 3: 1}))
    assert result["minutes_with_a_previous_valid_mark"] == 4
    assert result["p50_age_ms"] == hc.MINUTE
    assert result["p95_age_ms"] == result["max_age_ms"] == 3 * hc.MINUTE


class FakeResponse:
    def __init__(self, code: int) -> None:
        self.status = code
        self.headers: dict[str, str] = {"Content-Length": "3"}

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeSession:
    def __init__(self, codes: list[int]) -> None:
        self.codes = iter(codes)
        self.calls = 0

    def request(self, *args: object, **kwargs: object) -> FakeResponse:
        self.calls += 1
        assert kwargs["allow_redirects"] is False
        return FakeResponse(next(self.codes))


@pytest.mark.parametrize(
    "codes, calls, final", [([404], 1, 404), ([503, 200], 2, 200), ([429, 429, 429], 3, 429)]
)
def test_http_retry_behavior_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codes: list[int], calls: int, final: int
) -> None:
    async def immediate(_: float) -> None:
        pass

    monkeypatch.setattr(asyncio, "sleep", immediate)
    session = FakeSession(codes)
    wire = hc.ArchiveTransport()
    wire.session = cast(Any, session)
    result = asyncio.run(wire.request(entry(tmp_path), "HEAD"))
    assert result["status_code"] == final and session.calls == calls
