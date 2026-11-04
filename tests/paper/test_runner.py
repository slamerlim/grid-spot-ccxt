import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.domain.numbers import DomainError
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
@pytest.mark.parametrize("poll_ms", [5000, 5101])
def test_complete_mock_schedule_restart_and_reports_never_claim_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    poll_ms: int,
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
        journal.append("start", {"observed_clock": False, "monitoring_version": 2}, *clock())
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
                now[0] += poll_ms

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
        maximum_gap = verified["maximum_checkpoint_gap_seconds"]
        assert isinstance(maximum_gap, (int, float)) and maximum_gap <= 60
        assert verified["monitoring_faults"] == [] and verified["monitoring_version"] == 2
        assert verified["cpu_maximum_completed_window_seconds"] is not None
        assert all(not r["payload"]["monitor_fault"] for r in journal.kinds("checkpoint"))
        assert len(str(verified["journal_file_sha256"])) == 64

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_delayed_read_stops_before_paper_admission_and_records_partial_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        now = [10000]

        def clock() -> tuple[int, int]:
            return now[0], now[0] * 1000000

        journal = Journal(tmp_path)
        journal.append("start", {"observed_clock": False, "monitoring_version": 2}, *clock())
        wire = Wire()
        observer = Observer(journal, SOURCES, wire, clock)
        reader = Reader(now)
        calls = 0

        async def delayed_book(symbol: str) -> BookObservation:
            nonlocal calls
            calls += 1
            now[0] += 61000  # Model process/event-loop suspension, not a real timeout.
            return book(now[0])

        monkeypatch.setattr(observer.client, "load_markets", reader.load_markets)
        monkeypatch.setattr(observer.client, "fetch_order_book", delayed_book)
        assert await observer.run() == 1
        assert calls == 1 and wire.calls == 0
        assert not journal.kinds("book") and not journal.kinds("paper-observe")
        report = json.loads((tmp_path / "report.json").read_text())
        assert report["result"] == "partial-fault"
        assert report["failure"] == "checkpoint-overdue"
        assert report["paper"]["synthetic_orders"] == 0
        journal.close()
        verified = audit(tmp_path)
        assert verified["run_result"] == "partial-fault"
        assert verified["monitoring_faults"] == ["checkpoint-overdue"]

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_extra_checkpoints_cannot_erase_cpu_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        now, cpu = [10000], [0]

        def clock() -> tuple[int, int]:
            return now[0], now[0] * 1000000

        monkeypatch.setattr("grid.paper.runner.time.process_time_ns", lambda: cpu[0])
        journal = Journal(tmp_path)
        journal.append("start", {"observed_clock": False}, *clock())
        observer = Observer(journal, SOURCES, Wire(), clock)
        try:
            await observer.initialize()
            now[0] += 30000
            cpu[0] += 24 * 10**9
            first = await observer.checkpoint("before-backup")
            assert first["cpu_window_ns"] == 30 * 10**9
            assert first["cpu_window_complete"] is False
            now[0] += 30000
            cpu[0] += 24 * 10**9
            with pytest.raises(DomainError, match="resource-cap-breached"):
                await observer.checkpoint("verified-backup")
            failed = journal.kinds("checkpoint")[-1]["payload"]
            assert failed["cpu_window_ns"] == 60 * 10**9
            assert failed["cpu_window_complete"] is True
            assert failed["cpu_fraction"] == 0.8
            # Further snapshots cannot turn the latched fault into a successful run.
            with pytest.raises(DomainError, match="resource-cap-breached"):
                await observer.checkpoint_if_due()
        finally:
            await observer.client.close()
            if observer.engine:
                await observer.engine.close()
            journal.close()

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
