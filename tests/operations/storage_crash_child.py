"""Kill only this temporary mock worker at a selected storage boundary."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from grid.execution.synthetic import SyntheticExchange
from grid.operations.model import Action, Command, StoragePlan
from grid.operations.supervisor import MockSupervisor
from grid.operations.synthetic import EXECUTION, OPERATIONS, STORAGE, fixture
from grid.persistence.ledger import Ledger


async def main() -> None:
    data = json.loads(sys.stdin.read())
    path = Path(data["path"])
    case = fixture(1, 1)
    owner = await MockSupervisor.open(
        path,
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
            if stage == data["boundary"]:
                os.kill(os.getpid(), signal.SIGKILL)

        ledger.storage.fault = crash

    await owner.writer.submit(install)
    await owner.storage_job(
        "crash-storage",
        StoragePlan(
            1,
            Command(Action.BACKUP, case.scopes, "tester", "crash drill", 10000, True),
            str(path.parent / "backup.db"),
            str(path.parent / "verified.db"),
        ),
    )
    raise RuntimeError("fault boundary was not reached")


if __name__ == "__main__":
    asyncio.run(main())
