import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.exchange.models import BookObservation, Failure, MarketObservation, ReadError
from grid.paper.audit import audit
from grid.paper.journal import Journal
from grid.paper.runner import LIMITS, Observer
from grid.paper.wire import ObservedWire
from paper.test_engine import book, market
from paper.test_journal_wire import MARKET, Wire


class Reader:
    def __init__(self, now: list[int]) -> None:
        self.now = now

    async def load_markets(self) -> dict[str, MarketObservation]:
        return {"BTC/USDT": market(self.now[0], False)}

    async def fetch_order_book(self, symbol: str) -> BookObservation:
        return book(self.now[0])

    async def close(self) -> None:
        pass


@pytest.mark.usefixtures("async_sockets")
def test_complete_mock_schedule_restart_and_reports_never_claim_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        now = [10000]

        def clock() -> tuple[int, int]:
            return now[0], now[0] * 1000000

        real_sleep = asyncio.sleep

        async def sleep(delay: float) -> None:
            now[0] += max(1, int(delay * 1000))
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", sleep)
        journal = Journal(tmp_path)
        journal.append("start", {"observed_clock": False}, *clock())
        for expected in (75, 0):
            observer = Observer(journal, SOURCES, Wire(), clock)
            reader = Reader(now)
            # Keep the real CCXT boundary constructed but replace feed calls only;
            # the wire budget itself has separate adversarial tests.
            monkeypatch.setattr(observer.client, "load_markets", reader.load_markets)
            monkeypatch.setattr(observer.client, "fetch_order_book", reader.fetch_order_book)
            original_poll = observer.poll

            async def poll(poll_source: Callable[[], Awaitable[None]] = original_poll) -> None:
                await poll_source()
                now[0] += 5000

            monkeypatch.setattr(observer, "poll", poll)
            assert await observer.run() == expected
            if expected == 75:
                journal.close()
                journal = Journal(tmp_path)
                journal.append("resumed", {}, *clock())
        assert len(journal.kinds("start")) == 1
        assert len(journal.kinds("restart-requested")) == 1
        assert {r["payload"]["reason"] for r in journal.kinds("hold")} == {
            "disconnect",
            "synthetic-429",
        }
        assert len(journal.kinds("backup")) == 2
        assert len(journal.kinds("checkpoint")) >= 60
        report = json.loads((tmp_path / "report.json").read_text())
        assert report["result"] == "mock-clock-complete"
        assert not report["observed_clock"] and not report["observed_soak_complete"]
        assert report["real_orders"] == 0
        journal.close()
        verified = audit(tmp_path)
        assert verified["run_result"] == "mock-clock-complete"
        assert verified["process_restarts"] == 1
        assert len(str(verified["journal_file_sha256"])) == 64

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_three_metadata_attempts_are_global_across_restarts(tmp_path: Path) -> None:
    async def run() -> None:
        now = [10000]
        wire = Wire()
        journal = Journal(tmp_path)
        journal.append("start", {}, now[0], now[0] * 1000000)
        for _ in range(3):
            observed = ObservedWire(journal, wire, lambda: (now[0], now[0] * 1000000))
            await observed.get(MARKET, LIMITS)
            now[0] += 5000
            journal.close()
            journal = Journal(tmp_path)
        observed = ObservedWire(journal, wire, lambda: (now[0], now[0] * 1000000))
        with pytest.raises(ReadError) as caught:
            await observed.get(MARKET, LIMITS)
        assert caught.value.failure is Failure.BUDGET and wire.calls == 3
        journal.close()

    asyncio.run(run())
