from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest
from exchange.conftest import async_sockets as async_sockets

from grid.domain.model import Context, Scope
from grid.research.history import MINUTE, RECORD, Source
from grid.research.replay import Allocation, Capital, Replay

START = 1704067200000
type Prices = tuple[float, float, float, float]
type MakeReplay = Callable[[tuple[Prices, ...]], Replay]


@pytest.fixture
def make_replay(tmp_path: Path, context: Context) -> MakeReplay:
    def make(prices: tuple[Prices, ...]) -> Replay:
        path = tmp_path / "history.bin"
        path.write_bytes(
            b"".join(RECORD.pack(START + i * MINUTE, *row) for i, row in enumerate(prices))
        )
        source = Source(
            path,
            "fake",
            "BASE/QUOTE",
            0,
            len(prices),
            "synthetic <qffff",
            "invented unleveraged fixture",
            "deterministic synthetic candles",
            True,
        )
        scope = Scope("fake", "allocated-account", "BASE/QUOTE", "allocation", "grid", "cycle")
        normalized = replace(
            context,
            buy_fee=replace(context.buy_fee, max_settlements=1),
            sell_fee=replace(context.sell_fee, max_settlements=1),
        )
        allocation = Allocation(scope, source, normalized, D("1000"))
        return Replay(
            (allocation,),
            (Capital(scope.venue, scope.account, "QUOTE", D("1000")),),
            START,
            START + len(prices) * MINUTE,
            "test-revision",
            interval_bars=3,
        )

    return make
