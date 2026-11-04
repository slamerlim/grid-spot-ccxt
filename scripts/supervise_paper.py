"""Transient local observer; one explicitly approved minute-30 worker restart.

No service installation. No imports or interfaces for exchange access. The worker
owns the enforced endpoint/budget boundary. A non-75 exit is terminal.
"""

import argparse
import json
import os
# Fixed local worker executable; no shell or command input.
import subprocess  # nosec B404
import sys
import time
from pathlib import Path


def append(path: Path, value: object) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        payload = (json.dumps(value, sort_keys=True) + "\n").encode()
        if os.write(fd, payload) != len(payload):
            raise OSError("short supervisor log write")
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--approved-sqlite-source", required=True)
    parser.add_argument("--replacement-for", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve(strict=True)
    command = [
        sys.executable,
        "-m",
        "grid.paper",
        "--directory",
        str(directory),
        "--approved-sqlite-source",
        args.approved_sqlite_source,
        "--acknowledge-public-one-hour",
        "--replacement-for",
        str(args.replacement_for),
        "--observer-pid",
        str(os.getpid()),
    ]
    started = time.monotonic()
    log = directory / "observer-notes.jsonl"
    append(
        log, {"kind": "coordinator-start", "pid": os.getpid(), "utc_ms": time.time_ns() // 1000000}
    )
    with (directory / "worker-output.jsonl").open("xb", buffering=0) as output:
        for generation in (1, 2):
            # sys.executable plus fixed module; separate argv elements, never a shell.
            child = subprocess.Popen(  # nosec B603
                command + (["--resume"] if generation == 2 else []),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                shell=False,
            )
            try:
                while child.poll() is None:
                    append(
                        log,
                        {
                            "kind": "worker-live",
                            "pid": child.pid,
                            "generation": generation,
                            "utc_ms": time.time_ns() // 1000000,
                        },
                    )
                    if time.monotonic() - started > 3660:
                        child.terminate()
                        raise TimeoutError("worker exceeded bounded observation window")
                    try:
                        child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        continue
                code = child.returncode
                append(
                    log,
                    {
                        "kind": "worker-exit",
                        "pid": child.pid,
                        "generation": generation,
                        "code": code,
                        "utc_ms": time.time_ns() // 1000000,
                    },
                )
                if code != 75 or generation == 2:
                    append(
                        log,
                        {
                            "kind": "coordinator-stop",
                            "code": code,
                            "utc_ms": time.time_ns() // 1000000,
                        },
                    )
                    return 1 if code == 75 else code
                # This is the sole preauthorized restart, after an observed code
                # 75 checkpoint, never after silence, timeout or arbitrary error.
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
    return 1


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(main())
