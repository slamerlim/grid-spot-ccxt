"""Explicitly synthetic reproducibility example; never production settings."""

from decimal import Decimal as D
from pathlib import Path

from grid.domain.market import FeeRule, Limits, Market, Precision, PrecisionMode
from grid.domain.model import Context, Policy, Scope
from grid.research.artifacts import output_directory, publish
from grid.research.config import bundle
from grid.research.history import MINUTE, RECORD, Source
from grid.research.replay import Allocation, Capital, Replay


def create(directory: Path, revision: str) -> Path:
    directory = output_directory(directory)
    path = directory / "synthetic.bin"
    prices = (
        (100, 101, 99, 100),
        (100, 101, 99, 100),
        (100, 112, 95, 110),
        (110, 111, 100, 105),
        (105, 106, 90, 100),
        (100, 101, 95, 100),
        (100, 120, 99, 115),
    )
    start = 1704067200000
    with path.open("xb") as stream:
        for i, row in enumerate(prices):
            stream.write(RECORD.pack(start + i * MINUTE, *row))
    market = Market(
        "SYNTH/USD",
        "SYNTH",
        "USD",
        1,
        Precision(PrecisionMode.TICK, tick=D("0.01")),
        Precision(PrecisionMode.TICK, tick=D("0.001")),
        Limits(D("0.01"), D("1000000")),
        Limits(D("1"), D("10000")),
        Limits(D("0"), None),
        Limits(D("0.1"), D("10000")),
        Limits(D("0"), None),
        True,
        False,
    )
    policy = Policy(
        D("0.1"),
        D("0.01"),
        D("10"),
        D("900"),
        D("1"),
        D("1000"),
        D("0.00000001"),
        D("0.01"),
        3,
        10,
        8,
        1000,
        1000,
        10,
        60000,
        "bid",
    )
    fee = FeeRule("USD", D("0.001"), D("0.0001"), None, 1)
    context = Context(market, policy, fee, fee, 1, start, start, start, None, True)
    scope = Scope("synthetic", "research", market.symbol, "example", "grid", "demo")
    source = Source(
        path,
        scope.venue,
        scope.symbol,
        0,
        len(prices),
        "generated <qffff by grid.research.demo",
        "invented unleveraged spot fixture",
        "deterministic synthetic test bars, NOT exchange observations",
        True,
    )
    spec = Replay(
        (Allocation(scope, source, context, D("1000")),),
        (Capital(scope.venue, scope.account, "USD", D("1000")),),
        start,
        start + len(prices) * MINUTE,
        revision,
        interval_bars=3,
    )
    config = directory / "replay.json"
    publish(config, bundle(spec))
    return config
