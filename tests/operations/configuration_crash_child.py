"""SIGKILL inside a temporary mock configuration/exit transaction."""

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
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.portfolio.model import ExitApproval
from grid.universe.controls import EditAction, Retirement, UniverseEdit
from grid.universe.model import MarketKey


async def main() -> None:
    data = json.loads(sys.stdin.read())
    case = fixture(2, 2)
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
    await owner.observe(case.facts(10000, 1))
    await owner.step()
    await owner.universe.configure("universe", 0, case.universe, "tester", "fixture", 10000)
    await owner.portfolio.begin("cycle", 0, "cycle", case.portfolio, "tester", "fixture")

    def install(ledger: Ledger) -> None:
        def crash(stage: str) -> None:
            if stage == "operations:" + data["boundary"]:
                os.kill(os.getpid(), signal.SIGKILL)

        ledger.storage.fault = crash

    await owner.writer.submit(install)
    retirement = Retirement(data["retirement"])
    await owner.control(
        "configure",
        1,
        Command(Action.CONFIGURE_UNIVERSE, case.scopes, "tester", "atomic fixture", 10000, True),
        universe_edit=UniverseEdit(
            1, EditAction.DENY, keys=(MarketKey.of(case.scopes[0]),), retirement=retirement
        ),
        exit_approval=ExitApproval("cycle", codec.digest(case.portfolio))
        if retirement is Retirement.LIMIT
        else None,
    )
    raise RuntimeError("fault boundary was not reached")


if __name__ == "__main__":
    asyncio.run(main())
