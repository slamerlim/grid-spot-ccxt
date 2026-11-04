"""Temporary mock worker killed at dust completion or confirmed resume boundaries."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from grid.operations.model import Action
from grid.persistence.ledger import Ledger
from operations.test_allocation_exit import tick
from operations.test_scoped_dust import configured, residual_owner
from operations.test_supervisor import command


async def main() -> None:
    data = json.loads(sys.stdin.read())
    now, case = [10000], configured()
    release = data["boundary"].startswith("operations:")
    owner = await residual_owner(Path(data["path"]), now, case, finish=release)

    def install(ledger: Ledger) -> None:
        def crash(stage: str) -> None:
            if stage == data["boundary"]:
                os.kill(os.getpid(), signal.SIGKILL)

        ledger.storage.fault = crash

    await owner.writer.submit(install)
    if release:
        await command(owner, Action.RESUME, "release-dust", confirmed=True)
    else:
        await tick(owner, case, now, ms=1001)
    raise RuntimeError("fault boundary was not reached")


if __name__ == "__main__":
    asyncio.run(main())
