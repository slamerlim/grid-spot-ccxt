import asyncio
import json
import os
import signal
import subprocess
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.domain.model import Side
from grid.paper.engine import SCOPE, PaperEngine
from grid.paper.journal import Journal
from paper.test_engine import book, market


@pytest.mark.parametrize("boundary", ["after-submit", "after-fill", "after-posting"])
@pytest.mark.usefixtures("async_sockets")
def test_sigkill_replays_fake_acceptance_before_financial_reconciliation(
    tmp_path: Path,
    boundary: str,
) -> None:
    child = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("crash_child.py"))],
        input=json.dumps(
            {"directory": str(tmp_path), "sources": sorted(SOURCES), "boundary": boundary}
        ),
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert child.returncode == -signal.SIGKILL, child.stderr

    async def recover() -> None:
        journal = Journal(tmp_path)
        now = [30000]

        def clock() -> tuple[int, int]:
            return now[0], now[0] * 1000000

        engine = await PaperEngine.open(journal, SOURCES, clock)
        try:
            assert engine.owner is not None
            health = await engine.owner.status()
            assert health.admission_halted
            sent_before = len(engine.gateway.sent)
            assert sent_before >= 1
            assert len(engine.gateway.trades) == (0 if boundary == "after-submit" else 1)
            await engine.observe(market(10000), book(now[0]), 10)
            now[0] += 5000
            await engine.observe(market(10000), book(now[0]), 11)
            state = (await engine.writer.submit(lambda ledger: ledger.snapshot(SCOPE))).state
            assert state is not None
            assert state.owned_base == (D(0) if boundary == "after-submit" else D("0.05"))
            assert state.quote_cash == (D(1000) if boundary == "after-submit" else D("994.99"))
            seeds = [request for request in engine.gateway.sent if request.order_id == 1]
            assert len(seeds) == 1 and seeds[0].side is Side.BUY
            assert len({request.client_id for request in engine.gateway.sent}) == len(
                engine.gateway.sent
            )
            await engine.writer.submit(lambda ledger: ledger.storage.integrity())
        finally:
            await engine.close()
            journal.close()

    asyncio.run(recover())
