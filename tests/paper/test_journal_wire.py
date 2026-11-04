import asyncio
import os
from pathlib import Path

import pytest

from grid.domain.numbers import DomainError
from grid.exchange.adapter import ReadLimits
from grid.exchange.ccxt_transport import WireReply, retry_after
from grid.exchange.models import ReadError
from grid.paper.journal import Journal
from grid.paper.runner import LIMITS
from grid.paper.wire import ObservedWire

BOOK = "https://www.okx.com/api/v5/market/books?instId=BTC-USDT&sz=20"
MARKET = "https://www.okx.com/api/v5/public/instruments?instType=SPOT&instId=BTC-USDT"


class Wire:
    def __init__(self) -> None:
        self.calls = 0
        self.reply = WireReply(200, b"{}")

    async def get(self, url: str, limits: ReadLimits) -> WireReply:
        self.calls += 1
        return self.reply

    async def close(self) -> None:
        pass


def test_chain_owner_corruption_and_partial_record(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    journal.append("start", {}, 10000, 10000000000)
    with pytest.raises(BlockingIOError):
        Journal(tmp_path)
    journal.close()
    restored = Journal(tmp_path)
    assert len(restored.rows) == 1
    restored.close()
    with (tmp_path / "observations.jsonl").open("ab") as stream:
        stream.write(b'{"unfinished":')
    with pytest.raises(DomainError, match="incomplete"):
        Journal(tmp_path)


@pytest.mark.usefixtures("async_sockets")
def test_persisted_wire_budget_spacing_deadline_and_server_backoff(tmp_path: Path) -> None:
    async def run() -> None:
        now = [10000, 10000000000]
        journal = Journal(tmp_path)
        journal.append("start", {}, *now)
        wire = Wire()
        observed = ObservedWire(journal, wire, lambda: (now[0], now[1]))
        await observed.get(MARKET, LIMITS)
        with pytest.raises(ReadError, match="RATE_LIMITED"):
            await observed.get(BOOK, LIMITS)
        assert wire.calls == 1
        journal.close()
        journal = Journal(tmp_path)
        observed = ObservedWire(journal, wire, lambda: (now[0], now[1]))
        now[0] += 5000
        now[1] += 5 * 10**9
        wire.reply = WireReply(429, b"{}", 120000)
        await observed.get(BOOK, LIMITS)
        now[0] += 60000
        now[1] += 60 * 10**9
        with pytest.raises(ReadError, match="RATE_LIMITED"):
            await observed.get(BOOK, LIMITS)
        assert wire.calls == 2 and len(journal.kinds("attempt")) == 2
        now[0] = 3610000
        now[1] = 3610 * 10**9
        with pytest.raises(ReadError, match="BUDGET_EXHAUSTED"):
            await observed.get(BOOK, LIMITS)
        journal.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_failed_fsync_prevents_network_and_poisoned_journal_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        journal = Journal(tmp_path)
        journal.append("start", {}, 10000, 10**10)
        wire = Wire()
        observed = ObservedWire(journal, wire, lambda: (10000, 10**10))

        def fail(fd: int) -> None:
            raise OSError("injected fsync failure")

        monkeypatch.setattr(os, "fsync", fail)
        with pytest.raises(OSError):
            await observed.get(BOOK, LIMITS)
        assert wire.calls == 0
        with pytest.raises(DomainError):
            journal.append("ignored", {}, 10000, 10**10)
        journal.close()

    asyncio.run(run())


@pytest.mark.parametrize("field", ["requests", "cache_age_ms", "response_bytes", "timeout_ms"])
def test_hour_profile_does_not_relax_default_probe(field: str) -> None:
    if field == "requests":
        with pytest.raises(ReadError):
            ReadLimits(requests=750)
    elif field == "cache_age_ms":
        with pytest.raises(ReadError):
            ReadLimits(cache_age_ms=3600000)
    elif field == "response_bytes":
        with pytest.raises(ReadError):
            ReadLimits(
                observed_hour=True, response_bytes=65537, timeout_ms=5000, min_interval_ms=5000
            )
    else:
        with pytest.raises(ReadError):
            ReadLimits(
                observed_hour=True, response_bytes=65536, timeout_ms=5001, min_interval_ms=5000
            )


def test_retry_after_does_not_shorten_explicit_server_wait() -> None:
    assert retry_after("120", 0) == 121000
    assert retry_after("Thu, 01 Jan 1970 00:02:00 GMT", 0) == 121000
    assert retry_after("1", 0) == 60000
    with pytest.raises(ReadError):
        retry_after("invalid", 0)
    with pytest.raises(ReadError):
        retry_after("9999999999999", 0)
