"""Actual SIGKILL against temporary synthetic operational state."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from grid.execution.synthetic import SyntheticExchange
from grid.operations.model import Action, Command
from grid.operations.supervisor import MockSupervisor
from grid.operations.synthetic import EXECUTION, OPERATIONS, STORAGE, fixture
from grid.persistence.ledger import Ledger


async def main() -> None:
    data = json.loads(sys.stdin.read())
    case = fixture(1, 1)
    owner = await MockSupervisor.open(
        Path(data["path"]),
        STORAGE,
        frozenset(data["sources"]),
        case.scopes,
        SyntheticExchange(100),
        EXECUTION,
        OPERATIONS,
        lambda: (10000, 10000000000),
        case.prepare,
    )

    def install(ledger: Ledger) -> None:
        def crash(stage: str) -> None:
            if stage == "operations:" + data["boundary"]:
                os.kill(os.getpid(), signal.SIGKILL)

        ledger.storage.fault = crash

    await owner.writer.submit(install)
    await owner.control(
        "cancel",
        1,
        Command(Action.CANCEL_BUYS, case.scopes, "tester", "crash fixture", 10000, False),
    )
    raise RuntimeError("crash boundary not reached")


if __name__ == "__main__":
    asyncio.run(main())
