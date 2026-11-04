"""Real SIGKILL at fake-world/financial-writer handoff boundaries; no network."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import pytest_socket

from grid.paper.engine import PaperEngine
from grid.paper.journal import Journal
from paper.test_engine import book, market


async def main() -> None:
    data = json.loads(sys.stdin.read())
    now = [10000]

    def clock() -> tuple[int, int]:
        return now[0], now[0] * 1000000

    journal = Journal(Path(data["directory"]))
    journal.append("start", {"observed_clock": False}, *clock())
    engine = await PaperEngine.open(journal, frozenset(data["sources"]), clock)
    recorded = engine.gateway.record

    def record(kind: str, payload: object) -> None:
        if recorded is None:
            raise RuntimeError("durable sink missing")
        recorded(kind, payload)
        if data["boundary"] == "after-" + kind:
            os.kill(os.getpid(), signal.SIGKILL)

    engine.gateway.record = record
    rules = market(now[0])
    for sequence in range(1, 4):
        await engine.observe(rules, book(now[0]), sequence)
        now[0] += 5000
    await engine.observe(rules, book(now[0], "98"), 4)
    if data["boundary"] == "after-posting":
        os.kill(os.getpid(), signal.SIGKILL)
    raise RuntimeError("crash boundary not reached")


if __name__ == "__main__":
    pytest_socket.disable_socket(allow_unix_socket=True)
    asyncio.run(main())
