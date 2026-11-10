"""Verify the frozen ten sources with the C++ scanner, without a second Python scan.

Each child opens its source read-only and verifies the expected full-file SHA-256.
Linux wait4 supplies per-child CPU/RSS/I/O counters. Receipts are exclusive outputs;
cache state is observed, never forcibly evicted. This is validation, not replay.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import resource
import signal
import stat

# Bounded explicit local executable; no shell invocation.
import subprocess  # nosec B404
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

MAX_LOG_BYTES = 1_048_576
MAX_CHILD_MEMORY = 512 * 1024 * 1024


def identity(path: Path) -> tuple[int, ...]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("regular nonsymlink source required")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1_048_576):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def utc_ms(value: str) -> int:
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() != timedelta(0):
        raise ValueError("UTC timestamp required")
    return (stamp - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)


def child_limits(log_bytes: int = MAX_LOG_BYTES) -> None:
    # Standalone single-threaded driver: executed only between fork and exec.
    resource.setrlimit(resource.RLIMIT_FSIZE, (log_bytes, log_bytes))
    resource.setrlimit(resource.RLIMIT_AS, (MAX_CHILD_MEMORY, MAX_CHILD_MEMORY))


def run_child(
    command: list[str], output: Path, timeout: float, *, log_bytes: int = MAX_LOG_BYTES
) -> dict[str, Any]:
    if not MAX_LOG_BYTES <= log_bytes <= 32 * MAX_LOG_BYTES:
        raise ValueError("child log bound must be between 1 and 32 MiB")
    start_utc = datetime.now(UTC).isoformat()
    start = time.perf_counter()
    timed_out = False
    with (
        (output / "stdout.json").open("xb") as stdout,
        (output / "stderr.txt").open("xb") as stderr,
    ):
        # Explicit argv to a local executable; no shell invocation.
        child = subprocess.Popen(  # nosec B603
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            preexec_fn=functools.partial(child_limits, log_bytes),
        )
        try:
            while True:
                pid, status, usage = os.wait4(child.pid, os.WNOHANG)
                if pid:
                    break
                if time.perf_counter() - start > timeout:
                    timed_out = True
                    os.killpg(child.pid, signal.SIGKILL)
                    _, status, usage = os.wait4(child.pid, 0)
                    break
                time.sleep(0.01)
            # wait4 owns reaping; never let Popen poll/wait reap a second time.
            child.returncode = os.waitstatus_to_exitcode(status)
        except BaseException:
            os.killpg(child.pid, signal.SIGKILL)
            _, status, _ = os.wait4(child.pid, 0)
            child.returncode = os.waitstatus_to_exitcode(status)
            raise
    wall = time.perf_counter() - start
    cpu = usage.ru_utime + usage.ru_stime
    return {
        "command": command,
        "start_utc": start_utc,
        "end_utc": datetime.now(UTC).isoformat(),
        "exit_code": child.returncode,
        "timed_out": timed_out,
        "wall_seconds_including_process_start_and_poll": wall,
        "user_cpu_seconds": usage.ru_utime,
        "system_cpu_seconds": usage.ru_stime,
        "cpu_wall_ratio": cpu / wall,
        "peak_rss_kib": usage.ru_maxrss,
        "filesystem_input_blocks": usage.ru_inblock,
        "filesystem_output_blocks": usage.ru_oublock,
        "major_page_faults": usage.ru_majflt,
        "minor_page_faults": usage.ru_minflt,
        "output_bytes": sum(
            (output / name).stat().st_size for name in ("stdout.json", "stderr.txt")
        ),
        "per_file_output_limit_bytes": log_bytes,
    }


def compare_scan(actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    fields = {
        "schema": "grid-source-scan-v1",
        "bytes": expected["bytes"],
        "records": expected["rows"],
        "accepted": expected["rows"],
        "rejected": 0,
        "first_ms": utc_ms(expected["first_utc"]),
        "last_ms": utc_ms(expected["last_utc"]),
        "missing_minutes": expected["missing_minutes"],
        "gap_count": expected["gap_count"],
        "sha256": expected["sha256"],
        "complete": True,
        "reasons": {},
        "rejection_examples": [],
    }
    return [
        name
        for name, value in fields.items()
        if type(actual.get(name)) is not type(value) or actual.get(name) != value
    ]


def verify(binary: Path, manifest_path: Path, output: Path, timeout: float = 120) -> dict[str, Any]:
    if not 0 < timeout <= 3600 or not sys.platform.startswith("linux"):
        raise ValueError("bounded timeout and Linux wait4 accounting required")
    binary = binary.resolve(strict=True)
    manifest_path = manifest_path.resolve(strict=True)
    if manifest_path.stat().st_size > MAX_LOG_BYTES:
        raise ValueError("selection manifest size bound exceeded")
    raw_manifest = manifest_path.read_bytes()
    manifest = json.loads(raw_manifest)
    selected = manifest["selected"]
    if len(selected) != 10 or len({item["source_path"] for item in selected}) != 10:
        raise ValueError("exactly ten distinct frozen sources required")
    root = Path(manifest["source_root"]).resolve(strict=True)
    output = output.resolve()
    if (
        root == output
        or root in output.parents
        or Path("/opt/datasets") in (output, *output.parents)
    ):
        raise ValueError("output cannot be inside immutable source trees")
    if any((parent / ".git").exists() for parent in (output, *output.parents)):
        raise ValueError("generated scan evidence belongs outside Git")
    binary_identity = identity(binary)
    binary_hash = sha256(binary)
    source_identities = {}
    for item in selected:
        path = Path(item["source_path"])
        if path.parent != root or path.name != item["file"]:
            raise ValueError("source path disagrees with frozen root/filename")
        current = identity(path)
        if list(current) != item["file_identity_before_and_after"]:
            raise ValueError("source identity differs from frozen selection")
        source_identities[str(path)] = current
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    with (output / "selection_manifest.json").open("xb") as stream:
        stream.write(raw_manifest)
    with (output / "executed_driver.py").open("xb") as stream:
        stream.write(Path(__file__).read_bytes())
    start_utc = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    receipts = []
    for index, item in enumerate(selected):
        destination = output / f"{index:02d}-{item['file']}"
        destination.mkdir()
        command = [str(binary), "scan", item["source_path"], item["sha256"]]
        receipt: dict[str, Any] = {"file": item["file"], "command": command, "status": "FAIL"}
        try:
            if identity(binary) != binary_identity:
                raise ValueError("scanner binary changed during experiment")
            receipt.update(run_child(command, destination, timeout))
            if receipt["exit_code"] != 0:
                raise ValueError("scanner child failed; retained stdout/stderr")
            actual = json.loads((destination / "stdout.json").read_bytes())
            mismatches = compare_scan(actual, item)
            receipt["comparison_mismatches"] = mismatches
            receipt["scan"] = actual
            if mismatches:
                raise ValueError("scanner result disagrees with frozen manifest")
            if identity(Path(item["source_path"])) != source_identities[item["source_path"]]:
                raise ValueError("source identity changed during scan")
            receipt["rows_per_second"] = (
                actual["records"] / receipt["wall_seconds_including_process_start_and_poll"]
            )
            receipt["status"] = "PASS"
        except (OSError, ValueError, KeyError, TypeError) as error:
            receipt["error"] = str(error)
        write_json(destination / "receipt.json", receipt)
        receipts.append(receipt)
        print(json.dumps({"file": item["file"], "status": receipt["status"]}), flush=True)
    unchanged = identity(binary) == binary_identity and sha256(binary) == binary_hash
    all_sources_unchanged = all(
        identity(Path(path)) == before for path, before in source_identities.items()
    )
    passed = (
        all(receipt["status"] == "PASS" for receipt in receipts)
        and unchanged
        and all_sources_unchanged
    )
    summary = {
        "schema": "module1-cpp-source-verification-v1",
        "status": "PASS" if passed else "FAIL",
        "kind": "SOURCE_VALIDATION_NOT_STRATEGY_REPLAY",
        "start_utc": start_utc,
        "end_utc": datetime.now(UTC).isoformat(),
        "total_wall_seconds": time.perf_counter() - started,
        "scanner": str(binary),
        "scanner_sha256": binary_hash,
        "scanner_identity_unchanged": unchanged,
        "source_identities_unchanged": all_sources_unchanged,
        "selection_manifest_path": str(manifest_path),
        "selection_manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
        "source_status": manifest["source_status"],
        "total_expected_source_bytes": sum(item["bytes"] for item in selected),
        "total_expected_source_rows": sum(item["rows"] for item in selected),
        "total_child_user_cpu_seconds": sum(
            receipt.get("user_cpu_seconds", 0) for receipt in receipts
        ),
        "total_child_system_cpu_seconds": sum(
            receipt.get("system_cpu_seconds", 0) for receipt in receipts
        ),
        "peak_child_rss_kib": max(
            (receipt.get("peak_rss_kib", 0) for receipt in receipts), default=0
        ),
        "output_bytes_before_summary": sum(
            path.stat().st_size for path in output.rglob("*") if path.is_file()
        ),
        "read_pattern": (
            "one sequential read-only C++ scan per source, 98304-byte reader buffer; "
            "no second Python source read"
        ),
        "cache_caveat": (
            "OS cache not evicted; prior source validation may have warmed it. "
            "Kernel block counters are not physical-device throughput guarantees."
        ),
        "measurement_caveat": (
            "Linux wait4 counters are per child; wall time includes launch and up to 10ms "
            "polling delay. No universal speed or profitability claim."
        ),
        "limits": {
            "child_address_space_bytes": MAX_CHILD_MEMORY,
            "per_log_bytes": MAX_LOG_BYTES,
            "per_source_timeout_seconds": timeout,
        },
        "receipts": receipts,
    }
    write_json(output / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-seconds", default=120, type=float)
    arguments = parser.parse_args()
    result = verify(
        arguments.binary, arguments.manifest, arguments.output, arguments.timeout_seconds
    )
    print(json.dumps({"status": result["status"], "output": str(arguments.output)}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
