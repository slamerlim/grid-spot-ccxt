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
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.portfolio.model import ExitApproval


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
    releasing = data.get("action") == "resume-allocation"
    liquidation = data.get("action") in ("limit-liquidate", "allocation-exit") or releasing
    exit_action = (
        Action.LIQUIDATE_ALLOCATION
        if data.get("action") == "allocation-exit"
        else Action.LIMIT_LIQUIDATE
    )
    approval = None
    if liquidation:
        await owner.observe(case.facts(10000, 1))
        await owner.step()
        await owner.portfolio.begin("cycle", 0, "cycle", case.portfolio, "tester", "fixture")
        approval = ExitApproval("cycle", codec.digest(case.portfolio))
    if releasing:
        await owner.control(
            "scoped",
            1,
            Command(
                Action.LIQUIDATE_ALLOCATION, case.scopes, "tester", "exit fixture", 10000, True
            ),
            exit_approval=approval,
        )
        assert await owner.executor.service(case.scopes[0], owner.contexts[case.scopes[0]])
        cycle = await owner.portfolio.status()
        assert cycle is not None
        await owner.portfolio.advance("complete", cycle.revision, "tester", "zero fixture")

    def install(ledger: Ledger) -> None:
        def crash(stage: str) -> None:
            if stage == "operations:" + data["boundary"]:
                os.kill(os.getpid(), signal.SIGKILL)

        ledger.storage.fault = crash

    await owner.writer.submit(install)
    await owner.control(
        "release" if releasing else "liquidate" if liquidation else "cancel",
        2 if releasing else 1,
        Command(
            Action.RESUME if releasing else exit_action if liquidation else Action.CANCEL_BUYS,
            case.scopes,
            "tester",
            "crash fixture",
            10000,
            liquidation,
        ),
        exit_approval=None if releasing else approval,
    )
    raise RuntimeError("crash boundary not reached")


if __name__ == "__main__":
    asyncio.run(main())
