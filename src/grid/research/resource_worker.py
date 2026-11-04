"""Disposable, network-free replay worker used by the synthetic contention harness."""

import argparse
import json
import resource
import sys
import time
from pathlib import Path
from typing import NoReturn

from grid.domain.numbers import DomainError, bounded_int
from grid.research.resources import Caps, ReadBudget, guard_parent, reading


def emit(value: object) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(payload.encode()) > 8192:
        raise DomainError("worker response exceeds protocol cap")
    print(payload, flush=True)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, "research worker: invalid arguments\n")


def main() -> int:
    parser = Parser(description="Internal bounded synthetic replay worker")
    parser.add_argument("config", type=Path)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--passes", type=int, required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    parser.add_argument("--address-space-bytes", type=int, required=True)
    parser.add_argument("--read-rate", type=int, required=True)
    parser.add_argument("--read-burst", type=int, required=True)
    parser.add_argument("--read-total", type=int, required=True)
    args = parser.parse_args()
    try:
        death_signal = guard_parent(args.parent_pid)
        bounded_int(args.passes, 1, 4)
        caps = Caps(
            args.cpu_seconds,
            args.address_space_bytes,
            args.read_rate,
            args.read_burst,
            args.read_total,
        )
        enforced = caps.apply_process()
        enforced["parent_death_signal"] = death_signal
        # Load the replay engine only after the child process ceilings are applied.
        from grid.research.config import load
        from grid.research.replay import run

        spec = load(args.config)
        emit({"kind": "ready", "enforced": enforced})
        if sys.stdin.buffer.readline(8) != b"go\n":
            raise DomainError("worker start not authorized")
        active = False

        def progress(total: int) -> None:
            nonlocal active
            if not active and total >= 4096:
                active = True
                emit({"kind": "active", "charged_bytes": total})

        budget = ReadBudget(caps, progress=progress)
        started, cpu = time.monotonic_ns(), time.process_time_ns()
        hashes = []
        with reading(budget):
            for _ in range(args.passes):
                result = run(spec)
                hashes.append(result["result_sha256"])
        if len(set(hashes)) != 1:
            raise DomainError("worker repeated replay changed financial result")
        usage = resource.getrusage(resource.RUSAGE_SELF)
        io = {
            key: int(value)
            for key, value in (
                line.split(":", 1) for line in Path("/proc/self/io").read_text().splitlines()
            )
        }
        emit(
            {
                "kind": "done",
                "result_hashes": hashes,
                "logical_read_bytes": budget.total,
                "requested_read_wait_ns": budget.wait_ns,
                "wall_ns": time.monotonic_ns() - started,
                "cpu_ns": time.process_time_ns() - cpu,
                "lifetime_rss_peak_bytes": usage.ru_maxrss * 1024,
                "process_io": io,
            }
        )
        return 0
    except Exception:
        print("bounded research worker refused or failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
