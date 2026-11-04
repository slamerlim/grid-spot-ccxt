"""Isolated Linux subreaper proves stopped research dies with its killed parent."""

import asyncio
import ctypes
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

from grid.operations.contention import Worker


async def parent(config: Path) -> None:
    worker = await Worker.start(config)
    try:
        async with asyncio.timeout(15):
            await worker.frame("ready")
            await worker.go()
            await worker.frame("active")
            await worker.pause()
            print(worker.process.pid, flush=True)
            await asyncio.sleep(10)
    finally:
        await worker.close()


def supervisor(config: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER, in this helper only.
        raise RuntimeError("subreaper unavailable")
    process = subprocess.Popen(
        [sys.executable, __file__, "parent", str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    child: int | None = None
    reaped = False
    try:
        if process.stdout is None or not select.select([process.stdout], [], [], 10)[0]:
            raise RuntimeError("parent handshake missing")
        child = int(process.stdout.readline(32))
        process.kill()
        process.wait(timeout=5)
        deadline = time.monotonic() + 3
        status = None
        while time.monotonic() < deadline:
            pid, observed = os.waitpid(child, os.WNOHANG)
            if pid == child:
                reaped, status = True, os.waitstatus_to_exitcode(observed)
                break
            time.sleep(0.01)
        print(json.dumps({"died_with_parent": reaped, "worker_exit": status}))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if child is not None and not reaped:
            os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)
        if process.stdout is not None:
            process.stdout.close()


if __name__ == "__main__":
    if sys.argv[1] == "parent":
        asyncio.run(parent(Path(sys.argv[2])))
    else:
        supervisor(Path(sys.argv[2]))
