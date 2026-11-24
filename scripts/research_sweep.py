"""Owned research sweep jobs: preparation, verification, calibration and replay.

All financial calculations are delegated to the isolated C++ research command.
Original histories are only read. Receipts describe actual completed stages.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, BinaryIO

from scripts.research_sweep_reference import VENUES, fixture_sources, matrix, verify_result

DEFAULT_ROOT = Path("/opt/grid-runs/research-sweep-20261119")
DEFAULT_HEAVY_LOCK = Path("/opt/grid-runs/.research-heavy.lock")
DEFAULT_BINARY = Path("/opt/grid-builds/module1/research-sweep-release/grid_backtest")
DEFAULT_MATRIX_BINARY = Path(
    "/opt/grid-builds/module1/research-sweep-selected-bin-v6/grid_backtest"
)
DEFAULT_DETAIL_BINARY = Path("/opt/grid-builds/results-detail/grid_backtest")
LIMITS = {
    "rss_bytes": 64 * 1024**3,
    "min_available_bytes": 32 * 1024**3,
    "min_free_disk_bytes": 32 * 1024**3,
    "max_output_bytes": 64 * 1024**3,
}
DAY_MS = 86_400_000
REQUIRED_STAGES = ("validate", "calibrate", "endurance", "rehearsal")
ACCEPTANCE_POLICY = {
    "id": "optimized-600s-v1",
    "endurance_seconds": 600,
    "minimum_checkpoints": 2,
    "recovery_required": True,
}
EXECUTION_DEFAULTS: dict[str, Any] = {
    "threads": 32,
    "block_bytes": 16 * 1024**2,
    "scheduler": "dynamic",
    "max_timestamp_groups": 4096,
    "audit_threads": 4,
    "reader_buffer_records": 4096,
    "chart_mode": "on_demand",
    "numa_interleave_nodes": None,
}
EXECUTION_KEYS = tuple(EXECUTION_DEFAULTS)
_ACTIVE_LOCK_FD: int | None = None
_ACTIVE_JOB_ROOT: Path | None = None


class Blocked(ValueError):
    """An unmet declared prerequisite, not a partially successful full run."""


class ResourceLimit(RuntimeError):
    """A measured resource bound stopped the owned experiment."""


def resource_observation(root: Path, pid: int = 0) -> dict[str, int]:
    available = next(
        int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    rss = 0
    if pid:
        with contextlib.suppress(FileNotFoundError, ProcessLookupError):
            rss = int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * os.sysconf("SC_PAGESIZE")
    size = 0
    for directory, _, names in os.walk(root):
        for name in names:
            with contextlib.suppress(FileNotFoundError):
                size += (Path(directory) / name).stat().st_size
    return {
        "rss_bytes": rss,
        "available_bytes": available,
        "free_disk_bytes": shutil.disk_usage(root).free,
        "artifact_bytes": size,
    }


def check_resources(observed: dict[str, int], limits: dict[str, int]) -> None:
    for observed_key, limit_key, minimum in (
        ("rss_bytes", "rss_bytes", False),
        ("available_bytes", "min_available_bytes", True),
        ("free_disk_bytes", "min_free_disk_bytes", True),
        ("artifact_bytes", "max_output_bytes", False),
    ):
        bound = limits.get(limit_key, 0)
        value = observed[observed_key]
        if bound and (value < bound if minimum else value > bound):
            raise ResourceLimit(f"{observed_key}={value} violates {limit_key}={bound}")


def read_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 128 * 1024**2:
        raise ValueError("JSON artifact exceeds bounded reader")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def write_json(path: Path, value: Any, *, durable: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        if durable:
            os.fsync(handle.fileno())
    os.replace(temporary, path)
    if not durable:
        return
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def acquire_heavy_lock(path: Path = DEFAULT_HEAVY_LOCK) -> BinaryIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        handle.close()
        raise
    return handle


def release_heavy_lock(handle: BinaryIO) -> None:
    # Last inherited descriptor releases the lock. Explicit LOCK_UN would also
    # unlock a still-running C++ child after its Python/UI owner dies.
    handle.close()


@contextlib.contextmanager
def HeavyJobLock(path: Path | None = None) -> Iterator[BinaryIO]:
    global _ACTIVE_LOCK_FD
    selected = path or Path(os.environ.get("GRID_RESEARCH_HEAVY_LOCK", str(DEFAULT_HEAVY_LOCK)))
    inherited = os.environ.get("GRID_RESEARCH_HEAVY_LOCK_FD")
    if inherited is not None:
        fd = int(inherited)
        opened, target = os.fstat(fd), selected.stat()
        if (opened.st_dev, opened.st_ino) != (target.st_dev, target.st_ino):
            raise ValueError("inherited heavy-job lock identity mismatch")
        # The parent retains ownership; closing our duplicate must not unlock it.
        with os.fdopen(os.dup(fd), "rb") as inherited_handle:
            previous_fd, _ACTIVE_LOCK_FD = _ACTIVE_LOCK_FD, inherited_handle.fileno()
            try:
                yield inherited_handle
            finally:
                _ACTIVE_LOCK_FD = previous_fd
    else:
        handle = acquire_heavy_lock(selected)
        previous_fd, _ACTIVE_LOCK_FD = _ACTIVE_LOCK_FD, handle.fileno()
        try:
            yield handle
        finally:
            _ACTIVE_LOCK_FD = previous_fd
            release_heavy_lock(handle)


def choose_sources(sources: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    groups = {
        venue: iter(sorted((s for s in sources if s["venue"] == venue), key=lambda s: s["id"]))
        for venue in VENUES
    }
    chosen: list[dict[str, Any]] = []
    while len(chosen) < count:
        before = len(chosen)
        for venue in VENUES:
            source = next(groups[venue], None)
            if source is not None:
                chosen.append(source)
                if len(chosen) == count:
                    break
        if before == len(chosen):
            break
    return chosen


def best_window(sources: list[dict[str, Any]], days: int) -> tuple[int, int]:
    """Earliest UTC-day window maximizing total source-span overlap in ms."""
    first_day = min(int(s["first_ms"]) // DAY_MS for s in sources)
    last_day = max(int(s["last_ms"]) // DAY_MS for s in sources)
    length = last_day - first_day + 1
    if length > 100_000:
        raise ValueError("source calendar span exceeds supported bounds")
    edges = [0] * (length + days + 2)
    changes = [0] * len(edges)
    for source in sources:
        start, end = int(source["first_ms"]), int(source["last_ms"]) + 60_000
        left, right = start // DAY_MS - first_day, (end - 1) // DAY_MS - first_day
        if left == right:
            edges[left] += end - start
        else:
            edges[left] += (first_day + left + 1) * DAY_MS - start
            edges[right] += end - (first_day + right) * DAY_MS
            changes[left + 1] += DAY_MS
            changes[right] -= DAY_MS
    active = 0
    for index in range(len(edges)):
        active += changes[index]
        edges[index] += active
    score = sum(edges[:days])
    best, best_index = score, 0
    for index in range(1, length):
        score += edges[index + days - 1] - edges[index - 1]
        if score > best:
            best, best_index = score, index
    start = (first_day + best_index) * DAY_MS
    return start, start + days * DAY_MS


def make_plan(
    sources: list[dict[str, Any]],
    output: Path,
    mode: str,
    *,
    threads: int = 1,
    block_bytes: int = 4 * 1024**2,
    configurations: list[dict[str, Any]] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = {
        "schema": "grid-research-sweep-plan-v1",
        "mode": mode,
        "sources": sources,
        "configurations": configurations or matrix(),
        "output_dir": str(output.resolve()),
        "threads": threads,
        "block_bytes": block_bytes,
        "checkpoint_seconds": 900,
        "resume": False,
        "runtime_limits": dict(LIMITS),
    }
    if settings:
        plan.update({key: settings[key] for key in EXECUTION_KEYS if key in settings})
    return plan


def execution_settings(request: dict[str, Any]) -> dict[str, Any]:
    """Bound explicit technical settings before they reach a child process."""
    result = {key: request.get(key, value) for key, value in EXECUTION_DEFAULTS.items()}
    for key, low, high in (
        ("threads", 1, 32),
        ("block_bytes", 4096, 64 * 1024**2),
        ("max_timestamp_groups", 1, 4096),
        ("audit_threads", 1, 32),
        ("reader_buffer_records", 256, 65536),
    ):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"invalid research setting: {key}")
    if result["scheduler"] not in ("static", "dynamic"):
        raise ValueError("invalid research scheduler")
    if result["chart_mode"] not in ("eager", "on_demand"):
        raise ValueError("invalid research chart mode")
    result["numa_interleave_nodes"] = _numa_nodes(result["numa_interleave_nodes"])
    return result


def _numa_nodes(value: Any) -> str | None:
    """Accept only explicit absolute node IDs; never infer a host-specific policy."""
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 384:
        raise ValueError("NUMA interleave nodes must be a comma-separated list of node IDs")
    fields = value.split(",")
    if len(fields) > 64 or any(not field.isascii() or not field.isdecimal() for field in fields):
        raise ValueError("NUMA interleave nodes require explicit nonnegative integer IDs")
    nodes = [int(field) for field in fields]
    if len(set(nodes)) != len(nodes) or any(node > 65535 for node in nodes):
        raise ValueError("NUMA interleave node IDs must be unique and bounded")
    return ",".join(str(node) for node in sorted(nodes))


def _engine_command(
    binary: Path, plan_path: Path, plan: dict[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    command = [str(binary), "research-sweep", str(plan_path)]
    nodes = _numa_nodes(plan.get("numa_interleave_nodes"))
    if nodes is None:
        return command, {"kind": "direct"}

    def node_set(text: str) -> set[int]:
        result: set[int] = set()
        for part in text.strip().split(","):
            bounds = part.split("-")
            first, last = int(bounds[0]), int(bounds[-1])
            if len(bounds) > 2 or not 0 <= first <= last <= 65535:
                raise ValueError("invalid kernel NUMA node list")
            result.update(range(first, last + 1))
        return result

    try:
        allowed_text = next(
            line.split(":", 1)[1]
            for line in Path("/proc/self/status").read_text().splitlines()
            if line.startswith("Mems_allowed_list:")
        )
        allowed = node_set(allowed_text)
        online = node_set(Path("/sys/devices/system/node/online").read_text())
        memory = node_set(Path("/sys/devices/system/node/has_memory").read_text())
    except (OSError, ValueError, StopIteration) as error:
        raise Blocked("cannot verify the explicitly requested NUMA memory nodes") from error
    requested = {int(node) for node in nodes.split(",")}
    if not requested <= allowed & online & memory:
        raise Blocked("requested NUMA nodes are unavailable, disallowed or have no memory")
    location = shutil.which("numactl")
    if location is None:
        raise Blocked("explicit NUMA interleave requires an installed numactl; no fallback")
    launcher = Path(location).resolve(strict=True)
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise Blocked("NUMA launcher is not an executable file")
    identity = {
        "kind": "numactl_interleave",
        "nodes": nodes,
        "path": str(launcher),
        "sha256": sha256(launcher),
    }
    return [str(launcher), f"--interleave={nodes}", "--", *command], identity


def freeze_study(preparation: Path, binary: Path, output: Path) -> Path:
    """Bind the retained source/rule inventory to every agreed configuration."""
    code_root = Path(__file__).resolve().parents[1]
    code_files = [
        Path(__file__),
        code_root / "scripts/research_sweep_sources.py",
        code_root / "scripts/research_sweep_reference.py",
        code_root / "scripts/research_sweep_recovery.py",
        code_root / "scripts/research_sweep_benchmark.py",
        code_root / "scripts/research_sweep_differential.py",
        code_root / "scripts/research_sweep_verify.py",
        code_root / "cpp/backtest/sweep.cpp",
        code_root / "cpp/backtest/sweep.hpp",
        code_root / "cpp/backtest/sweep_units.cpp",
        code_root / "cpp/backtest/sweep_units.hpp",
        code_root / "cpp/backtest/numeric.hpp",
        code_root / "cpp/backtest/numeric.cpp",
        code_root / "cpp/backtest/CMakeLists.txt",
    ]
    path = output / "study-manifest.json"
    write_json(
        path,
        {
            "schema": "grid-research-sweep-study-v1",
            "preparation_path": str(preparation.resolve()),
            "preparation_sha256": sha256(preparation),
            "configurations": matrix(),
            "code_sha256": {str(item.relative_to(code_root)): sha256(item) for item in code_files},
            "binary_sha256": sha256(binary) if binary.is_file() else None,
            "acceptance_policy": dict(ACCEPTANCE_POLICY),
            "assumptions": {
                "phases": ["O", "H", "L", "C"],
                "phase_offsets_ms": [0, 1000, 2000, 3000],
                "timestamps_are_synthetic_within_bar": True,
                "execution": "always_full_at_order_price",
                "buy_condition": "observed_price_strictly_below",
                "sell_condition": "observed_price_strictly_above",
                "initial_entry": "first_observed_price",
                "size": "pair_minimum_at_current_price",
                "fee_each_side": "0.002",
                "fee_currency": "USDT",
                "capital_limit": None,
                "grid_floor_fraction": "0.4",
                "next_cycle_anchor": "executed_sell_price",
                "global_sell": False,
                "force_close_at_eof": False,
                "rules": "one_snapshot_per_pair_history",
                "primary_ranking": None,
            },
        },
    )
    return path


def study_code_identity(path: Path) -> str:
    """Canonical digest of the frozen implementation file hashes."""
    hashes = read_json(path).get("code_sha256")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("study manifest lacks implementation identity")
    return hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def engine_run(
    binary: Path,
    plan: dict[str, Any],
    directory: Path,
    progress_path: Path | None = None,
    *,
    expected_codes: tuple[int, ...] = (0,),
    stop_requested: Callable[[], bool] | None = None,
    checkpoint_grace_seconds: float | None = 60,
) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    resource_root = _ACTIVE_JOB_ROOT or directory
    check_resources(resource_observation(resource_root), plan["runtime_limits"])
    plan_path = directory / "plan.json"
    command, launcher = _engine_command(binary, plan_path, plan)
    write_json(plan_path, plan)
    binary_hash = sha256(binary)
    write_json(
        directory / "execution-start.json",
        {
            "binary_sha256": binary_hash,
            "plan_sha256": sha256(plan_path),
            "command": command,
            "launcher": launcher,
            "started_unix": time.time(),
        },
    )
    started = time.monotonic()
    cancelled = False
    resource_error: str | None = None
    last_resource_check = 0.0
    stop_deadline: float | None = None
    peak_rss = 0
    rate_anchor: tuple[int, float] | None = None
    record_rate: float | None = None
    observed: dict[str, int] = {}
    last_progress_check = 0.0
    last_progress_signature: tuple[int, int] | None = None
    with (
        (directory / "stdout.log").open("wb") as stdout,
        (directory / "stderr.log").open("wb") as stderr,
    ):
        child = subprocess.Popen(
            command,
            stdout=stdout,
            stderr=stderr,
            pass_fds=(_ACTIVE_LOCK_FD,) if _ACTIVE_LOCK_FD is not None else (),
        )
        previous: dict[int, Any] = {}

        def stop(signum: int, _frame: Any) -> None:
            nonlocal cancelled, stop_deadline
            cancelled = True
            stop_deadline = (
                time.monotonic() + checkpoint_grace_seconds
                if checkpoint_grace_seconds is not None
                else None
            )
            if child.poll() is None:
                child.send_signal(signum)

        try:
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous[signum] = signal.signal(signum, stop)
            while child.poll() is None:
                if not cancelled and stop_requested is not None and stop_requested():
                    stop(signal.SIGTERM, None)
                source = Path(plan["output_dir"]) / "progress.json"
                now = time.monotonic()
                if progress_path is not None and source.exists() and now - last_progress_check >= 1:
                    with contextlib.suppress(OSError, ValueError):
                        source_stat = source.stat()
                        signature = (source_stat.st_mtime_ns, source_stat.st_size)
                        last_progress_check = now
                        if signature != last_progress_signature:
                            progress = read_json(source)
                            count = int(progress.get("processed_records", 0))
                            now = time.monotonic()
                            if progress.get("status") == "RUNNING":
                                if rate_anchor is not None and count > rate_anchor[0]:
                                    record_rate = (count - rate_anchor[0]) / (now - rate_anchor[1])
                                if rate_anchor is None or count > rate_anchor[0]:
                                    rate_anchor = (count, now)
                            progress.update(observed)
                            progress["record_rate_per_second"] = record_rate
                            progress["estimated_replay_remaining_seconds"] = (
                                max(0, int(progress["input_records"]) - count) / record_rate
                                if plan["mode"] in ("full", "matrix_batch") and record_rate
                                else None
                            )
                            checkpoint_path = source.parent / "checkpoint.latest.json"
                            progress["latest_checkpoint_unix"] = (
                                checkpoint_path.stat().st_mtime
                                if checkpoint_path.exists()
                                else None
                            )
                            write_json(progress_path, progress, durable=False)
                            last_progress_signature = signature
                if time.monotonic() - last_resource_check >= 5:
                    observed = resource_observation(resource_root, child.pid)
                    peak_rss = max(peak_rss, observed["rss_bytes"])
                    with (directory / "resources.jsonl").open("a") as sample:
                        sample.write(
                            json.dumps({"elapsed_seconds": time.monotonic() - started, **observed})
                            + "\n"
                        )
                    last_resource_check = time.monotonic()
                    try:
                        check_resources(observed, plan["runtime_limits"])
                    except ResourceLimit as error:
                        if resource_error is None:
                            resource_error = str(error)
                            stop_deadline = (
                                time.monotonic() + checkpoint_grace_seconds
                                if checkpoint_grace_seconds is not None
                                else None
                            )
                            child.terminate()
                if stop_deadline is not None and time.monotonic() >= stop_deadline:
                    child.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    child.wait(timeout=0.1)
                    break
            code = child.wait()
        finally:
            for prior_signal, handler in previous.items():
                signal.signal(prior_signal, handler)
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    result_dir = Path(plan["output_dir"])
    summary_path = result_dir / "summary.json"
    outcome = {
        "exit_code": code,
        "wall_seconds": time.monotonic() - started,
        "plan_sha256": sha256(plan_path),
        "binary_sha256": binary_hash,
        "command": command,
        "launcher": launcher,
        "result_dir": str(result_dir),
        "cancelled": cancelled,
        "observed_peak_rss_bytes": peak_rss,
        "resource_error": resource_error,
    }
    if summary_path.exists():
        outcome["summary_sha256"] = sha256(summary_path)
        outcome["summary"] = read_json(summary_path)
    write_json(directory / "execution.json", outcome)
    if sha256(binary) != binary_hash:
        raise ValueError("research executable changed during calculation")
    if launcher["kind"] != "direct" and sha256(Path(launcher["path"])) != launcher["sha256"]:
        raise ValueError("NUMA launcher changed during calculation")
    if resource_error:
        raise ResourceLimit(resource_error)
    if cancelled:
        raise InterruptedError("research worker cancelled; retained checkpoints may be resumed")
    if code not in expected_codes:
        diagnostic = (directory / "stderr.log").read_text(errors="replace")[-8192:]
        if any(
            marker in diagnostic
            for marker in (
                "RSS limit",
                "memory floor",
                "available-memory",
                "free-disk",
                "output limit",
                "No space left",
            )
        ):
            raise ResourceLimit(diagnostic.strip())
        raise ValueError(f"engine exit {code}; retained diagnostics: {directory / 'stderr.log'}")
    if code == 0 and not summary_path.exists():
        raise ValueError("engine exited without final summary")
    return outcome


def result_fingerprint(outcome: dict[str, Any]) -> str:
    summary = outcome["summary"]
    if "binary_configurations" in summary:
        from scripts.research_sweep_binary import engine_results

        for _ in engine_results(Path(outcome["result_dir"]), summary):
            pass
        return str(summary["fingerprint"]["configurations_sha256"])
    encoded = json.dumps(summary["configurations"], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def exact_result_fingerprint(outcome: dict[str, Any]) -> str:
    from scripts.research_sweep_differential import result_fingerprint as exact_fingerprint

    value = exact_fingerprint(Path(outcome["result_dir"]))
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validate_engine(binary: Path, output: Path) -> dict[str, Any]:
    sources = fixture_sources(output / "fixtures")
    configs = [
        {"id": "one", "step_pct": "1", "tp_pct": "10"},
        {"id": "round", "step_pct": "1.5", "tp_pct": "2"},
        {"id": "deep", "step_pct": "10", "tp_pct": "15"},
    ]
    sources[1]["rules"]["price_tick"] = "1"
    sources[-1]["rules"].update(price_mode="significant", price_digits=5, max_price_decimals=8)
    results = []
    digest = None
    for threads, block in ((1, 1024**2), (2, 4 * 1024**2), (4, 16 * 1024**2)):
        run = output / f"oracle-{threads}"
        plan = make_plan(
            sources,
            run / "engine",
            "validation",
            threads=threads,
            block_bytes=block,
            configurations=configs,
        )
        plan["trace"] = True
        outcome = engine_run(binary, plan, run)
        checked = verify_result(plan, run / "engine")
        current = result_fingerprint(outcome)
        if digest is not None and digest != current:
            raise ValueError("sequential / parallel exact result mismatch")
        digest = current
        results.append({"threads": threads, **checked, "fingerprint": current})
    small = fixture_sources(output / "matrix-fixtures", minutes=4)
    plan = make_plan(small, output / "matrix" / "engine", "validation", threads=4)
    outcome = engine_run(binary, plan, output / "matrix")
    results.append(
        {
            "matrix": 540,
            **verify_result(plan, Path(plan["output_dir"])),
            "fingerprint": result_fingerprint(outcome),
        }
    )
    matrix_parallel = dict(plan, threads=32, output_dir=str(output / "matrix-32/engine"))
    parallel_outcome = engine_run(binary, matrix_parallel, output / "matrix-32")
    if result_fingerprint(parallel_outcome) != result_fingerprint(outcome):
        raise ValueError("complete 540-configuration results changed with 32 workers")
    results.append(
        {"matrix": 540, "threads": 32, "fingerprint": result_fingerprint(parallel_outcome)}
    )
    deferred_plan = dict(
        matrix_parallel, chart_mode="on_demand", output_dir=str(output / "matrix-on-demand/engine")
    )
    deferred = engine_run(binary, deferred_plan, output / "matrix-on-demand")
    if result_fingerprint(deferred) != result_fingerprint(outcome):
        raise ValueError("on-demand charts changed the exact 540-configuration results")
    if sha256(Path(deferred_plan["output_dir"]) / "pairs.jsonl") != sha256(
        Path(plan["output_dir"]) / "pairs.jsonl"
    ):
        raise ValueError("on-demand charts changed exact pair results or trade hashes")
    if (Path(deferred_plan["output_dir"]) / "series.jsonl").stat().st_size:
        raise ValueError("on-demand sweep unexpectedly retained chart points")
    results.append(
        {
            "matrix": 540,
            "threads": 32,
            "chart_mode": "on_demand",
            **verify_result(deferred_plan, Path(deferred_plan["output_dir"])),
        }
    )
    from scripts.research_sweep_recovery import validate_recovery

    recovery = validate_recovery(binary, output / "recovery")
    return {
        "checks": results,
        "recovery": recovery,
        "result_dir": str(output / "matrix" / "engine"),
    }


def calibrate(
    binary: Path,
    sources: list[dict[str, Any]],
    output: Path,
    *,
    threads: int | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if threads is not None and threads not in (1, 2, 4, 8, 16, 24, 32):
        raise ValueError("unsupported calibration thread count")
    smoke_sources = choose_sources(sources, 8)
    if len(smoke_sources) != 8 or {source["venue"] for source in smoke_sources} != set(VENUES):
        raise Blocked("the smoke run requires eight validated pairs covering all seven venues")
    smoke_start, smoke_end = best_window(smoke_sources, 1)
    technical = dict(settings or {})
    if threads is not None:
        technical["threads"] = threads
    smoke_plan = make_plan(
        smoke_sources, output / "smoke/engine", "calibration", settings=technical
    )
    smoke_plan.update(start_ms=smoke_start, end_ms=smoke_end)
    smoke = engine_run(binary, smoke_plan, output / "smoke", output / "progress.json")
    selected = choose_sources(sources, 64)
    start, end = best_window(selected, 7)
    frozen = make_plan(selected, output / "unused", "calibration", settings=technical)
    frozen.update(start_ms=start, end_ms=end)
    cap = 4096
    pilots = []
    visited: set[int] = set()
    while True:
        visited.add(cap)
        run = output / f"pilot-{cap}"
        plan = dict(frozen, max_records=cap, output_dir=str(run / "engine"))
        outcome = engine_run(binary, plan, run, output / "progress.json")
        seconds = outcome["summary"]["timings"]["replay_seconds"]
        pilots.append(
            {"max_records": cap, "wall_seconds": outcome["wall_seconds"], "replay_seconds": seconds}
        )
        if 60 <= seconds <= 120 or (cap >= 65536 and seconds < 60) or (cap <= 64 and seconds > 120):
            break
        suggested = max(64, min(65536, int(cap * 90 / max(seconds, 0.001)) // 64 * 64))
        if suggested in visited:
            cap = min(pilots, key=lambda row: abs(row["replay_seconds"] - 90))["max_records"]
            break
        cap = suggested
    frozen["max_records"] = cap
    write_json(output / "workload.json", frozen)
    measurements: list[dict[str, Any]] = []
    expected: str | None = None

    def measure(settings: list[tuple[int, int]], tag: str) -> None:
        nonlocal expected
        for repeat in range(3):
            ordered = settings[repeat % len(settings) :] + settings[: repeat % len(settings)]
            for workers, block in ordered:
                run = output / f"{tag}-r{repeat}-w{workers}-b{block}"
                plan = dict(
                    frozen, threads=workers, block_bytes=block, output_dir=str(run / "engine")
                )
                outcome = engine_run(binary, plan, run, output / "progress.json")
                current = exact_result_fingerprint(outcome)
                if expected is not None and current != expected:
                    raise ValueError("calibration changed exact results")
                expected = current
                measurements.append(
                    {
                        "threads": workers,
                        "block_bytes": block,
                        "repeat": repeat,
                        "wall_seconds": outcome["wall_seconds"],
                        "replay_seconds": outcome["summary"]["timings"]["replay_seconds"],
                        "fingerprint": current,
                        "directory": str(run),
                    }
                )
                write_json(output / "measurements.json", {"measurements": measurements})

    def winner() -> dict[str, Any]:
        groups: dict[tuple[int, int], list[float]] = {}
        for row in measurements:
            groups.setdefault((row["threads"], row["block_bytes"]), []).append(
                row["replay_seconds"]
            )
        medians = [(statistics.median(times), key) for key, times in groups.items()]
        fastest = min(item[0] for item in medians)
        eligible = [(key, median) for median, key in medians if median <= fastest * 1.05]
        key, median = min(eligible)
        return {**technical, "threads": key[0], "block_bytes": key[1], "median_seconds": median}

    measure(
        [(n, 4 * 1024**2) for n in ((threads,) if threads else (1, 2, 4, 8, 16, 24, 32))], "workers"
    )
    workers = winner()["threads"]
    measure([(workers, n * 1024**2) for n in (1, 4, 16)], "buffers")
    result = {
        "settings": winner(),
        "selection_timing": "replay_seconds; audit/publication wall time recorded separately",
        "smoke": {
            "sources": len(smoke_sources),
            "start_ms": smoke_start,
            "end_ms": smoke_end,
            "fingerprint": exact_result_fingerprint(smoke),
            "wall_seconds": smoke["wall_seconds"],
        },
        "pilots": pilots,
        "measurements": measurements,
        "cache_conditions": "OS cache retained; no cache eviction; rotating warm repeats",
        "buffer_limitations": (
            "Blocks end after 64 timestamp groups; on a 64-pair workload buffers may not fill. "
            "No throughput claim for an unexercised capacity."
        ),
        "result_dir": str(Path(measurements[-1]["directory"]) / "engine"),
    }
    write_json(output / "calibration.json", result)
    return result


def benchmark_settings(
    output: Path, preparation_hash: str, binary_hash: str, code_hash: str, source_count: int
) -> tuple[dict[str, Any], dict[str, str]]:
    path = output / "receipt.json"
    proof = read_json(path)
    if (
        proof.get("schema")
        not in ("grid-research-sweep-benchmark-v1", "grid-research-sweep-benchmark-v2")
        or proof.get("status") != "PASS"
        or proof.get("preparation_sha256") != preparation_hash
        or proof.get("binary_sha256") != binary_hash
        or proof.get("code_sha256") != code_hash
        or proof.get("sources") != source_count
        or proof.get("configurations") != 540
    ):
        raise Blocked("full-roster benchmark evidence does not match this study")
    settings = proof["settings"]
    if (
        settings.get("threads") != 32
        or settings.get("scheduler") not in ("static", "dynamic")
        or settings.get("block_bytes") not in (1024**2, 4 * 1024**2, 16 * 1024**2)
        or settings.get("max_timestamp_groups") != 4096
    ):
        raise Blocked("full-roster benchmark settings are outside the declared candidates")
    if proof["schema"] == "grid-research-sweep-benchmark-v2":
        if proof.get("acceptance_policy") != ACCEPTANCE_POLICY:
            raise Blocked("current benchmark acceptance policy differs from this study")
        if any(key not in settings for key in EXECUTION_KEYS):
            raise Blocked("current benchmark technical settings are incomplete")
        execution_settings(settings)
        if settings.get("chart_mode") != "on_demand":
            raise Blocked("current benchmark must use the agreed on-demand charts")
    return dict(settings), {"receipt": str(path.resolve()), "sha256": sha256(path)}


def check_endurance(summary: dict[str, Any], policy: dict[str, Any] | None) -> None:
    if policy is not None and policy != ACCEPTANCE_POLICY:
        raise Blocked("unknown endurance acceptance policy")
    duration = policy["endurance_seconds"] if policy else 3600
    if summary["timings"]["replay_seconds"] < duration:
        raise ValueError("endurance workload ended before its required observed duration")
    if policy and summary.get("checkpoint_count", 0) < policy["minimum_checkpoints"]:
        raise ValueError("endurance did not publish the required checkpoints")


def preparation_path(source_output: Path | None) -> Path:
    if source_output:
        for candidate in (
            source_output / "manifest.json",
            source_output / "preparation/manifest.json",
        ):
            if candidate.is_file():
                return candidate
        for filename in ("receipt.json", "admission.json"):
            receipt_path = source_output / filename
            if receipt_path.exists():
                receipt = read_json(receipt_path)
                if receipt.get("preparation_path"):
                    return Path(receipt["preparation_path"])
    return DEFAULT_ROOT / "preparation/manifest.json"


def prior_evidence(
    source_output: Path | None, preparation_hash: str, binary_hash: str, code_hash: str
) -> dict[str, Any]:
    if source_output is None:
        return {}
    old = (
        read_json(source_output / "admission.json")
        if (source_output / "admission.json").exists()
        else {}
    )
    if (source_output / "receipt.json").exists():
        old.update(read_json(source_output / "receipt.json"))
    evidence = old.get("evidence", {})
    accepted = {}
    for stage, entry in evidence.items():
        path = Path(entry["receipt"])
        if (
            entry.get("binary_sha256") == binary_hash
            and entry.get("preparation_sha256") == preparation_hash
            and entry.get("code_sha256") == code_hash
            and path.is_file()
            and sha256(path) == entry["sha256"]
        ):
            proof = read_json(path)
            if (
                proof.get("status") in ("PASS", "COMPLETE")
                and proof.get("completed_stage", proof.get("action")) == stage
                and proof.get("binary_sha256") == binary_hash
                and proof.get("preparation_sha256") == preparation_hash
                and proof.get("code_sha256") == code_hash
            ):
                accepted[stage] = entry
    if (
        old.get("status") in ("PASS", "COMPLETE")
        and old.get("binary_sha256") == binary_hash
        and old.get("preparation_sha256") == preparation_hash
        and old.get("code_sha256") == code_hash
    ):
        path = source_output / "receipt.json"
        accepted[old.get("completed_stage", old["action"])] = {
            "receipt": str(path),
            "sha256": sha256(path),
            "binary_sha256": binary_hash,
            "preparation_sha256": preparation_hash,
            "code_sha256": code_hash,
        }
    return accepted


def _detail_checkpoint_prefix(original: Path) -> int:
    """Validate saved checkpoints and copy only their durable series prefix."""
    valid: list[int] = []
    footer_start = b'],"payload_sha256":"'
    footer_size = len(footer_start) + 64 + 3
    for name in ("checkpoint.latest.json", "checkpoint.previous.json"):
        path = original / name
        if not path.is_file() or path.is_symlink():
            continue
        try:
            remaining = path.stat().st_size - footer_size
            if remaining <= 0:
                continue
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                first = handle.readline(1024**2)
                suffix = b',"configurations":[\n'
                if not first.endswith(suffix):
                    continue
                header = json.loads(first[: -len(suffix)] + b"}")
                handle.seek(0)
                while remaining:
                    block = handle.read(min(remaining, 1024**2))
                    if not block:
                        raise ValueError("checkpoint truncated")
                    digest.update(block)
                    remaining -= len(block)
                footer = handle.read()
            if (
                not footer.startswith(footer_start)
                or json.loads(b"{" + footer[2:])["payload_sha256"] != digest.hexdigest()
            ):
                continue
            size = header["series_offset"]
            if not isinstance(size, int) or size < 0:
                continue
            digest = hashlib.sha256()
            with (original / "series.bin").open("rb") as handle:
                remaining = size
                while remaining:
                    block = handle.read(min(remaining, 1024**2))
                    if not block:
                        raise ValueError("committed series truncated")
                    digest.update(block)
                    remaining -= len(block)
            if digest.hexdigest() == header["series_sha256"]:
                valid.append(size)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if not valid:
        raise Blocked("no intact detailed checkpoint with a verified output prefix")
    return max(valid)


def copy_checkpoint(binary: Path, source_output: Path, output: Path) -> dict[str, Any]:
    plan_path = source_output / "plan.json"
    plan = read_json(plan_path)
    binding = read_json(source_output / "execution-start.json")
    if binding["binary_sha256"] != sha256(binary) or binding["plan_sha256"] != sha256(plan_path):
        raise Blocked("resume executable or saved plan differs from original")
    _, launcher = _engine_command(binary, plan_path, plan)
    if binding.get("launcher", {"kind": "direct"}) != launcher:
        raise Blocked("resume NUMA launcher or policy differs from original")
    original = Path(plan["output_dir"])
    filenames = (
        "checkpoint.latest.json",
        "checkpoint.previous.json",
        "series.bin" if plan.get("detail_mode") else "series.jsonl",
        "trades.jsonl",
    )
    files = [original / name for name in filenames if (original / name).is_file()]
    committed_series = _detail_checkpoint_prefix(original) if plan.get("detail_mode") else None
    if not any(path.name.startswith("checkpoint.") for path in files):
        raise Blocked("no saved checkpoint to resume")
    target = output / "engine"
    target.mkdir()
    observed = resource_observation(_ACTIVE_JOB_ROOT or output)
    projected = sum(path.stat().st_size for path in files)
    observed["artifact_bytes"] += projected
    observed["free_disk_bytes"] -= projected
    check_resources(observed, plan["runtime_limits"])
    for path in files:
        if path.name == "series.bin" and committed_series is not None:
            with path.open("rb") as source, (target / path.name).open("xb") as destination:
                remaining = committed_series
                while remaining:
                    block = source.read(min(remaining, 1024**2))
                    if not block:
                        raise Blocked("committed detailed series changed during copying")
                    destination.write(block)
                    remaining -= len(block)
                destination.flush()
                os.fsync(destination.fileno())
        else:
            shutil.copyfile(path, target / path.name)
    plan.update(output_dir=str(target), resume=True)
    plan.pop("stop_after_blocks", None)
    return plan


def _engine_sources(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if manifest.get("status") != "READY" or manifest.get("blockers"):
        raise Blocked(f"source preparation blocked: {len(manifest.get('blockers', []))} findings")
    from scripts.research_sweep_sources import (
        CURRENT_RULES_SCHEMA,
        FLAT_HOUR_SCHEMA,
        GAP_FREE_SCHEMA,
        REVIEWED_SCHEMA,
        verify_current_rules_selection,
        verify_flat_hour_selection,
        verify_gap_free_selection,
        verify_reviewed_selection,
        verify_selection,
    )

    schema = manifest.get("schema")
    if schema == "grid-research-sweep-selection-v1":
        verify_selection(manifest)
    elif schema == GAP_FREE_SCHEMA:
        verify_gap_free_selection(manifest)
    elif schema == REVIEWED_SCHEMA:
        verify_reviewed_selection(manifest)
    elif schema == CURRENT_RULES_SCHEMA:
        verify_current_rules_selection(manifest)
    elif schema == FLAT_HOUR_SCHEMA:
        verify_flat_hour_selection(manifest)
    elif schema != "grid-research-sweep-sources-v1":
        raise Blocked("unsupported source preparation schema")
    sources = manifest["sources"]
    if not sources or any(not source.get("rules") for source in sources):
        raise Blocked("missing rules in complete roster")
    return list(sources)


def _diagnostic_sources(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """A declared calibration subset never changes the full-study roster."""
    if manifest.get("schema") == "grid-research-sweep-selection-v1":
        _engine_sources(manifest)
    sources = [
        source
        for source in manifest.get("sources", [])
        if source.get("audit_accepted") and source.get("rules")
    ]
    missing = set(VENUES) - {source["venue"] for source in sources}
    if missing:
        raise Blocked(
            "calibration lacks validated rules/history for: " + ", ".join(sorted(missing))
        )
    return sources


def worker(request: dict[str, Any], output: Path) -> dict[str, Any]:
    global _ACTIVE_JOB_ROOT
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "receipt.json").exists():
        raise ValueError("refusing to overwrite an existing job receipt")
    action = str(request.get("action", "prepare"))
    if action == "run":
        action = "full"
    default_binary = (
        DEFAULT_DETAIL_BINARY
        if action == "matrix-detail"
        else DEFAULT_MATRIX_BINARY
        if action.startswith("matrix-")
        else DEFAULT_BINARY
    )
    binary = Path(request.get("binary", default_binary)).resolve()
    source_output = (
        Path(request["source_output"]).resolve() if request.get("source_output") else None
    )
    receipt: dict[str, Any] = {
        "schema": "grid-research-sweep-job-v1",
        "action": action,
        "status": "RUNNING",
        "started_unix": time.time(),
        "output": str(output),
        "evidence": {},
        "acceptance_policy": dict(ACCEPTANCE_POLICY),
    }
    write_json(output / "request.json", request)
    write_json(output / "progress.json", receipt)
    previous_root, _ACTIVE_JOB_ROOT = _ACTIVE_JOB_ROOT, output
    try:
        with HeavyJobLock():
            check_resources(resource_observation(output), LIMITS)
            if action.startswith("matrix-"):
                from scripts.research_sweep_matrix import run_matrix

                receipt.update(run_matrix(request, output))
            elif action == "select":
                from scripts.research_sweep_sources import (
                    DEFAULT_FLAT_AUDIT,
                    GAP_FREE_POLICY,
                    select_current_rules_sources,
                    select_flat_hour_sources,
                    select_gap_free_sources,
                    select_reviewed_sources,
                    select_verified_sources,
                )

                if source_output is None:
                    raise ValueError("select requires an audited source job")
                parent_path = preparation_path(source_output)
                history_policy = request.get("history_policy")
                if "exclude_flat_run_minutes" in request:
                    if (
                        type(request["exclude_flat_run_minutes"]) is not int
                        or request["exclude_flat_run_minutes"] != 60
                        or history_policy not in (None, GAP_FREE_POLICY)
                        or request.get("rules_snapshot_dir")
                        or request.get("exclusion_review")
                    ):
                        raise ValueError(
                            "flat-hour selection requires only --exclude-flat-run-minutes 60"
                        )
                    selected = select_flat_hour_sources(
                        parent_path, DEFAULT_FLAT_AUDIT, output / "preparation"
                    )
                    receipt.update(
                        history_policy=GAP_FREE_POLICY,
                        research_admission=selected["research_readiness"],
                    )
                elif request.get("rules_snapshot_dir"):
                    if history_policy != GAP_FREE_POLICY or request.get("exclusion_review"):
                        raise ValueError(
                            "current rules require gap_free_own_span without another review"
                        )
                    selected = select_current_rules_sources(
                        parent_path,
                        Path(request["rules_snapshot_dir"]),
                        output / "preparation",
                    )
                    receipt.update(
                        history_policy=GAP_FREE_POLICY,
                        research_admission=selected["research_readiness"],
                    )
                elif request.get("exclusion_review"):
                    if history_policy != GAP_FREE_POLICY:
                        raise ValueError("exclusion review requires gap_free_own_span selection")
                    selected = select_reviewed_sources(
                        parent_path, Path(request["exclusion_review"]), output / "preparation"
                    )
                    receipt.update(history_policy=GAP_FREE_POLICY, research_admission="PENDING")
                elif history_policy is None:
                    selected = select_verified_sources(parent_path, output / "preparation")
                elif history_policy == GAP_FREE_POLICY:

                    def selection_progress(done: int, total: int, market: str) -> None:
                        write_json(
                            output / "progress.json",
                            {
                                **receipt,
                                "phase": "gap_free_source_audit",
                                "history_policy": GAP_FREE_POLICY,
                                "audited_sources": done,
                                "total_sources": total,
                                "current_source": market,
                            },
                            durable=False,
                        )

                    selected = select_gap_free_sources(
                        parent_path,
                        output / "preparation",
                        audit_threads=execution_settings(request)["audit_threads"],
                        progress=selection_progress,
                    )
                    receipt.update(history_policy=GAP_FREE_POLICY, research_admission="PENDING")
                else:
                    raise ValueError("unsupported selection history policy")
                receipt.update(
                    status=selected["status"],
                    blockers=selected["blockers"],
                    totals=selected["totals"],
                    preparation_path=str(output / "preparation/manifest.json"),
                    source_manifest_sha256=selected["source_manifest_sha256"],
                )
                if history_policy is None:
                    receipt["study_manifest"] = str(
                        freeze_study(output / "preparation/manifest.json", binary, output)
                    )
                    receipt["code_sha256"] = study_code_identity(Path(receipt["study_manifest"]))
                else:
                    receipt["preparation_sha256"] = sha256(output / "preparation/manifest.json")
            elif action in ("prepare", "collect"):
                from scripts.research_sweep_sources import (
                    DEFAULT_SNAPSHOT_DIRS,
                    collect_snapshots,
                    prepare_sources,
                )

                snapshots = [
                    *DEFAULT_SNAPSHOT_DIRS,
                    *(Path(p) for p in request.get("snapshot_dirs", [])),
                ]
                if action == "collect":
                    collection = asyncio.run(collect_snapshots(output / "snapshots"))
                    receipt["collection"] = collection
                    snapshots.append(output / "snapshots")
                if source_output:
                    snapshots.extend(
                        (source_output, source_output / "snapshots", source_output / "catalogues")
                    )
                    previous_manifest = preparation_path(source_output)
                    if previous_manifest.exists():
                        for source in read_json(previous_manifest).get("sources", []):
                            saved_path = source.get("rules_snapshot", {}).get("snapshot_path")
                            if saved_path:
                                snapshots.append(Path(saved_path).parent)
                snapshots = list(dict.fromkeys(snapshots))

                def prepare_progress(done: int, total: int, market: str) -> None:
                    write_json(
                        output / "progress.json",
                        {
                            **receipt,
                            "validated_sources": done,
                            "source_count": total,
                            "current_source": market,
                        },
                    )
                    if done % 100 == 0:
                        check_resources(resource_observation(output), LIMITS)

                prepared = prepare_sources(
                    Path(request.get("dataset_root", "/opt/datasets")),
                    output / "preparation",
                    snapshots,
                    progress=prepare_progress,
                )
                receipt.update(
                    status=prepared["status"],
                    blockers=prepared.get("blockers", []),
                    preparation_path=str(output / "preparation/manifest.json"),
                    totals=prepared.get("totals", {}),
                )
                receipt["study_manifest"] = str(
                    freeze_study(output / "preparation/manifest.json", binary, output)
                )
                receipt["code_sha256"] = study_code_identity(Path(receipt["study_manifest"]))
            else:
                if not binary.is_file():
                    raise Blocked(f"research executable is missing: {binary}")
                receipt["binary_sha256"] = sha256(binary)
                prep_path = preparation_path(source_output)
                prepared = read_json(prep_path) if prep_path.exists() else {}
                prep_hash = sha256(prep_path) if prep_path.exists() else ""
                receipt.update(preparation_path=str(prep_path), preparation_sha256=prep_hash)
                if prepared:
                    receipt["study_manifest"] = str(freeze_study(prep_path, binary, output))
                    receipt["code_sha256"] = study_code_identity(Path(receipt["study_manifest"]))
                evidence = prior_evidence(
                    source_output,
                    prep_hash,
                    receipt["binary_sha256"],
                    receipt.get("code_sha256", ""),
                )
                if action != "resume":
                    evidence = {
                        stage: entry
                        for stage, entry in evidence.items()
                        if read_json(Path(entry["receipt"])).get("acceptance_policy")
                        == receipt["acceptance_policy"]
                    }
                receipt["evidence"] = evidence
                write_json(output / "admission.json", receipt)
                if action == "validate":
                    receipt.update(validate_engine(binary, output), status="PASS")
                elif action == "calibrate":
                    if "validate" not in evidence:
                        raise Blocked("matching validation receipt required before calibration")
                    technical = execution_settings(request)
                    benchmark_choice = None
                    if request.get("benchmark_output"):
                        selected_settings, selected_proof = benchmark_settings(
                            Path(request["benchmark_output"]),
                            prep_hash,
                            receipt["binary_sha256"],
                            receipt["code_sha256"],
                            len(_engine_sources(prepared)),
                        )
                        for setting in EXECUTION_KEYS:
                            if setting in request and request[setting] != selected_settings.get(
                                setting
                            ):
                                raise Blocked(
                                    f"{setting} differs from the saved benchmark settings"
                                )
                        technical = execution_settings(selected_settings)
                        benchmark_choice = selected_settings, selected_proof
                    receipt.update(
                        calibrate(
                            binary,
                            _diagnostic_sources(prepared),
                            output,
                            threads=technical["threads"],
                            settings=technical,
                        ),
                        status="PASS",
                    )
                    receipt["scope"] = "declared calibration subset; full roster unchanged"
                    if benchmark_choice is not None:
                        settings, proof = benchmark_choice
                        receipt["short_calibration_settings"] = receipt["settings"]
                        receipt["settings"] = settings
                        receipt["benchmark_evidence"] = proof
                        receipt["scope"] = "full-roster settings; short calibration retained"
                elif action in ("rehearsal", "full", "endurance"):
                    requirements = (
                        REQUIRED_STAGES if action == "full" else ("validate", "calibrate")
                    )
                    missing = [stage for stage in requirements if stage not in evidence]
                    if missing:
                        raise Blocked("missing matching passed stages: " + ", ".join(missing))
                    sources = _engine_sources(prepared)
                    calibration_receipt = read_json(Path(evidence["calibrate"]["receipt"]))
                    calibration = calibration_receipt["settings"]
                    if calibration_receipt.get("acceptance_policy") != receipt["acceptance_policy"]:
                        raise Blocked("calibration acceptance policy differs from this run")
                    for setting in EXECUTION_KEYS:
                        if setting in request and request[setting] != calibration.get(setting):
                            raise Blocked(f"{setting} differs from the saved calibration settings")
                    if benchmark_proof := calibration_receipt.get("benchmark_evidence"):
                        proof_path = Path(benchmark_proof["receipt"])
                        if sha256(proof_path) != benchmark_proof["sha256"]:
                            raise Blocked("full-roster benchmark evidence changed")
                        checked, _ = benchmark_settings(
                            proof_path.parent,
                            prep_hash,
                            receipt["binary_sha256"],
                            receipt["code_sha256"],
                            len(sources),
                        )
                        if checked != calibration:
                            raise Blocked("calibration differs from full-roster benchmark settings")
                    if action == "endurance":
                        count = len(sources)
                        if count == 0:
                            raise Blocked(
                                "endurance requires the complete inventoried roster count"
                            )
                        projected = resource_observation(output)
                        fixture_bytes = count * 32_000 * 24
                        projected["artifact_bytes"] += fixture_bytes
                        projected["free_disk_bytes"] -= fixture_bytes
                        check_resources(projected, LIMITS)
                        sources = fixture_sources(output / "synthetic", count=count, minutes=32_000)
                    plan = make_plan(
                        sources,
                        output / "engine",
                        action,
                        threads=calibration["threads"],
                        block_bytes=calibration["block_bytes"],
                        settings=calibration,
                    )
                    if action == "rehearsal":
                        start, end = best_window(sources, 7)
                        plan.update(start_ms=start, end_ms=end)
                    elif action == "endurance":
                        plan["duration_seconds"] = ACCEPTANCE_POLICY["endurance_seconds"]
                        plan["checkpoint_seconds"] = 120
                    outcome = engine_run(binary, plan, output, output / "progress.json")
                    receipt.update(
                        result_dir=outcome["result_dir"],
                        status="COMPLETE",
                        wall_seconds=outcome["wall_seconds"],
                        settings=calibration,
                        charts_available=outcome["summary"].get("charts_available", True),
                        chart_mode=plan.get("chart_mode", "eager"),
                    )
                    if action == "endurance":
                        check_endurance(outcome["summary"], receipt["acceptance_policy"])
                elif action == "resume":
                    if source_output is None:
                        raise ValueError("resume requires an existing source_output")
                    admission = read_json(source_output / "admission.json")
                    policy = admission.get("acceptance_policy")
                    if policy is None:
                        receipt.pop("acceptance_policy", None)
                    else:
                        if policy != ACCEPTANCE_POLICY:
                            raise Blocked("unknown saved acceptance policy")
                        receipt["acceptance_policy"] = policy
                    if admission.get("preparation_sha256") != prep_hash:
                        raise Blocked("resume preparation manifest differs from original")
                    if admission.get("code_sha256") != receipt.get("code_sha256"):
                        raise Blocked("resume implementation differs from original")
                    plan = copy_checkpoint(binary, source_output, output)
                    stage = plan["mode"]
                    if stage in ("full", "rehearsal", "endurance"):
                        required = REQUIRED_STAGES if stage == "full" else ("validate", "calibrate")
                        if any(name not in evidence for name in required):
                            raise Blocked(
                                "resume requires the original matching prerequisite evidence"
                            )
                        _engine_sources(prepared)
                    outcome = engine_run(binary, plan, output, output / "progress.json")
                    if stage == "endurance":
                        check_endurance(outcome["summary"], policy)
                    receipt.update(
                        status="COMPLETE", completed_stage=stage, result_dir=outcome["result_dir"]
                    )
                elif action == "chart":
                    receipt.update(build_chart(binary, request, output), status="COMPLETE")
                else:
                    raise ValueError("unknown research sweep action")
    except Blocked as error:
        receipt.update(status="BLOCKED", error=str(error))
        if "prepared" in locals():
            receipt["blockers"] = prepared.get("blockers", [])
    except InterruptedError as error:
        receipt.update(status="CANCELLED", error=str(error))
    except ResourceLimit as error:
        receipt.update(status="RESOURCE_LIMIT", error=str(error))
    except BlockingIOError:
        receipt.update(
            status="BLOCKED", error="Другая тяжёлая исследовательская задача уже выполняется"
        )
    except (ValueError, OSError, RuntimeError) as error:
        receipt.update(status="FAIL", error=f"{type(error).__name__}: {error}")
    finally:
        _ACTIVE_JOB_ROOT = previous_root
    if action.startswith("matrix-") and (output / "admission.json").is_file():
        # A stopped batch must retain the same immutable study and admission
        # evidence as a completed one. Never replace the actual failure status.
        try:
            saved = read_json(output / "admission.json")
            protected = {"status", "error", "action", "output", "started_unix", "finished_unix"}
            for key, value in saved.items():
                if key not in protected and (
                    key not in receipt
                    or (key == "evidence" and not receipt[key])
                    or key == "acceptance_policy"
                ):
                    receipt[key] = value
        except (OSError, ValueError) as error:
            receipt.update(status="FAIL", error=f"Cannot preserve matrix admission: {error}")
    receipt["finished_unix"] = time.time()
    write_json(output / "receipt.json", receipt)
    write_json(output / "progress.json", receipt)
    return receipt


def _selected_pair_rows(result: Path, config_id: str) -> Iterator[dict[str, Any]]:
    index_path = result / "pairs.index.json"
    entry = read_json(index_path).get(config_id) if index_path.is_file() else None
    with (result / "pairs.jsonl").open("rb") as stream:
        ending = os.fstat(stream.fileno()).st_size
        if entry is not None:
            offset, end = entry.get("offset"), entry.get("end")
            if type(offset) is not int or type(end) is not int or not 0 <= offset <= end <= ending:
                raise ValueError("invalid parent pair index")
            stream.seek(offset)
            ending = end
        while stream.tell() < ending:
            line = stream.readline(1024 * 1024 + 1)
            if not line.endswith(b"\n") or len(line) > 1024 * 1024 or stream.tell() > ending:
                raise ValueError("invalid parent pair row")
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("invalid parent pair object")
            if row.get("config_id") == config_id:
                yield row
            elif entry is not None:
                raise ValueError("parent pair index configuration mismatch")


def build_chart(binary: Path, request: dict[str, Any], output: Path) -> dict[str, Any]:
    source_output = Path(request["source_output"])
    parent = read_json(source_output / "receipt.json")
    if parent.get("status") != "COMPLETE":
        raise Blocked("chart requires a completed sweep")
    result_dir = Path(parent["result_dir"])
    plan = read_json(source_output / "plan.json")
    summary = read_json(result_dir / "summary.json")
    config_id, scope = request["config_id"], request.get("scope", "all")
    configs = [config for config in plan["configurations"] if config["id"] == config_id]
    if len(configs) != 1:
        raise ValueError("unknown configuration")
    pair_scope = scope.startswith("pair:")
    eager = plan.get("chart_mode", "eager") == "eager"
    if (
        not pair_scope
        and scope != "all"
        and scope not in {"venue:" + source["venue"] for source in plan["sources"]}
    ):
        raise ValueError("unknown chart scope")
    if (
        (pair_scope or not eager)
        and parent.get("binary_sha256")
        and sha256(binary) != parent["binary_sha256"]
    ):
        raise Blocked("chart executable differs from the completed sweep")
    parent_pairs = list(_selected_pair_rows(result_dir, config_id))
    if pair_scope:
        parent_pairs = [row for row in parent_pairs if row["source_id"] == scope[5:]]
    parent_pairs_hash = hashlib.sha256(
        json.dumps(parent_pairs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    identity = hashlib.sha256(
        json.dumps(
            {
                "receipt": sha256(source_output / "receipt.json"),
                "plan": sha256(source_output / "plan.json"),
                "summary": sha256(result_dir / "summary.json"),
                "binary": parent.get("binary_sha256"),
                "selected_pairs": parent_pairs_hash,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    cache_scope = "aggregate" if not pair_scope and not eager else scope
    cache_key = hashlib.sha256(json.dumps([identity, config_id, cache_scope]).encode()).hexdigest()
    target = output / "engine"
    # Cache only completed, bounded sibling jobs. Cached bytes are checked again
    # and copied into the new owned output; no published parent is modified.
    if output.parent.is_dir():
        for candidate_index, candidate in enumerate(output.parent.iterdir()):
            if candidate_index >= 1000:
                break
            receipt_path = candidate / "receipt.json"
            if candidate == output or candidate.is_symlink() or not receipt_path.is_file():
                continue
            try:
                cached = read_json(receipt_path)
                if cached.get("status") != "COMPLETE" or cached.get("chart_cache_key") != cache_key:
                    continue
                if (
                    cached.get("config_id") != config_id
                    or cached.get("parent_result_identity") != identity
                ):
                    continue
                cached_result = Path(cached["result_dir"]).resolve()
                if cached_result != (candidate / "engine").resolve():
                    continue
                hashes = cached.get("chart_artifact_sha256", {})
                if not {"summary.json", "series.jsonl"} <= hashes.keys():
                    continue
                if set(hashes) - {
                    "summary.json",
                    "series.jsonl",
                    "pairs.jsonl",
                    "pairs.index.json",
                }:
                    continue
                if sum((cached_result / name).stat().st_size for name in hashes) > 128 * 1024**2:
                    continue
                if any(
                    name not in {"summary.json", "series.jsonl", "pairs.jsonl", "pairs.index.json"}
                    or (cached_result / name).is_symlink()
                    or sha256(cached_result / name) != digest
                    for name, digest in hashes.items()
                ):
                    continue
                target.mkdir(parents=True)
                for name in hashes:
                    shutil.copyfile(cached_result / name, target / name)
                metadata = {
                    key: cached[key]
                    for key in (
                        "config_id",
                        "scope",
                        "chart_mode",
                        "charts_available",
                        "parent_result_identity",
                        "chart_cache_key",
                        "chart_artifact_sha256",
                    )
                }
                return {
                    **metadata,
                    "result_dir": str(target),
                    "scope": scope,
                    "cache_hit": True,
                    "chart_source": str(target / "series.jsonl"),
                }
            except (OSError, ValueError, KeyError):
                continue
    if not pair_scope and eager:
        target.mkdir(parents=True)
        selected = [row for row in summary["configurations"] if row["id"] == config_id]
        if len(selected) != 1:
            raise ValueError("unknown configuration result")
        filtered_summary = dict(summary, configurations=selected, charts_available=True)
        write_json(target / "summary.json", filtered_summary)
        found = 0
        with (
            (result_dir / "series.jsonl").open() as source,
            (target / "series.jsonl").open("x") as sink,
        ):
            for line in source:
                row = json.loads(line)
                if row.get("config_id") == config_id and row.get("scope") == scope:
                    sink.write(line)
                    found += 1
                    if found > 2001:
                        raise ValueError("chart bin bound exceeded")
        if found == 0:
            raise ValueError("no chart for requested scope")
    else:
        source_id = scope.removeprefix("pair:")
        sources = (
            [source for source in plan["sources"] if source["id"] == source_id]
            if pair_scope
            else plan["sources"]
        )
        if not sources or (pair_scope and len(sources) != 1):
            raise ValueError("unknown pair")
        expected = parent_pairs
        if len(expected) != len(sources):
            raise ValueError("missing original pair result")
        last_ms = summary.get("last_ms")
        if pair_scope and isinstance(last_ms, int) and last_ms >= 3000:
            plan["end_ms"] = min(plan.get("end_ms", last_ms + 1), last_ms - 3000 + 1)
        for field in ("duration_seconds", "stop_after_blocks", "max_records", "warmup_until_ms"):
            plan.pop(field, None)
        if not pair_scope:
            processed = summary.get("processed_records")
            if type(processed) is not int or processed < 0:
                raise ValueError("original processed-record count is invalid")
            # Keep the original chart window, including empty trailing time.
            # A record cap reproduces a duration/diagnostic stop at the same
            # complete timestamp without shifting any interval boundary.
            if processed:
                plan["max_records"] = processed
        plan.update(
            mode="validation",
            sources=sources,
            configurations=configs,
            threads=1,
            output_dir=str(target),
            resume=False,
            chart_mode="eager",
        )
        outcome = engine_run(binary, plan, output, output / "progress.json")
        actual = list(_selected_pair_rows(target, config_id))
        if actual != expected:
            raise ValueError(
                "detailed replay differs from original exact pair results or trade hashes"
            )
        if not pair_scope:
            expected_config = [row for row in summary["configurations"] if row["id"] == config_id]
            actual_config = outcome["summary"]["configurations"]
            if len(expected_config) != 1 or actual_config != expected_config:
                raise ValueError(
                    "chart replay differs from exact portfolio/venue results or trade hash"
                )
    hashes = {
        name: sha256(target / name)
        for name in ("summary.json", "series.jsonl", "pairs.jsonl", "pairs.index.json")
        if (target / name).is_file()
    }
    return {
        "result_dir": str(target),
        "chart_source": str(target / "series.jsonl"),
        "config_id": config_id,
        "scope": scope,
        "chart_mode": "eager",
        "charts_available": True,
        "parent_result_identity": identity,
        "chart_cache_key": cache_key,
        "chart_artifact_sha256": hashes,
        "cache_hit": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "worker",
            "prepare",
            "collect",
            "select",
            "validate",
            "calibrate",
            "endurance",
            "rehearsal",
            "full",
            "resume",
            "chart",
            "matrix-prepare",
            "matrix-plan",
            "matrix-validate",
            "matrix-calibrate",
            "matrix-endurance",
            "matrix-rehearsal",
            "matrix-full",
            "matrix-resume",
            "matrix-chart",
            "matrix-detail",
        ),
    )
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-output", type=Path)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--threads", type=int, choices=(1, 2, 4, 8, 16, 24, 32))
    parser.add_argument("--block-bytes", type=int)
    parser.add_argument("--scheduler", choices=("static", "dynamic"))
    parser.add_argument("--audit-threads", type=int)
    parser.add_argument("--reader-buffer-records", type=int)
    parser.add_argument("--max-timestamp-groups", type=int)
    parser.add_argument("--chart-mode", choices=("on_demand", "eager"))
    parser.add_argument(
        "--numa-interleave-nodes", help="explicit memory node IDs, e.g. 1,5; default disabled"
    )
    parser.add_argument("--benchmark-output", type=Path)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--configuration-plan", type=Path)
    parser.add_argument("--skip-endurance", action="store_true")
    parser.add_argument("--history-policy", choices=("gap_free_own_span",))
    parser.add_argument("--exclude-flat-run-minutes", type=int)
    parser.add_argument("--exclusion-review", type=Path)
    parser.add_argument("--rules-snapshot-dir", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--config-id")
    parser.add_argument("--scope", default="all")
    args = parser.parse_args()
    if args.skip_endurance and args.action not in ("matrix-full", "matrix-resume"):
        parser.error("--skip-endurance applies only to matrix-full or matrix-resume")
    if args.action == "matrix-plan":
        from scripts.research_sweep_matrix import depth_configuration_plan

        # A pure, explicitly requested plan generator: no worker, replay or data
        # selection. Exclusive creation keeps earlier research plans immutable.
        plan = depth_configuration_plan()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        with args.output.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        print(
            json.dumps(
                {
                    "status": "PREPARED",
                    "configuration_plan": str(args.output.resolve()),
                    "configuration_count": len(plan["configurations"]),
                    "sha256": sha256(args.output),
                    "backtest_started": False,
                }
            )
        )
        return 0
    if args.action == "worker":
        if args.request is None:
            parser.error("worker requires --request")
        request = read_json(args.request)
    else:
        request = {"action": args.action}
        if args.binary is not None:
            request["binary"] = str(args.binary)
        if args.source_output:
            request["source_output"] = str(args.source_output)
        for key in EXECUTION_KEYS:
            if getattr(args, key) is not None:
                request[key] = getattr(args, key)
        if args.benchmark_output is not None:
            request["benchmark_output"] = str(args.benchmark_output)
        if args.batch_size is not None:
            request["batch_size"] = args.batch_size
        if args.configuration_plan is not None:
            request["configuration_plan"] = str(args.configuration_plan)
        if args.skip_endurance:
            request["skip_endurance"] = True
        if args.history_policy is not None:
            if args.action != "select":
                parser.error("--history-policy applies only to select")
            request["history_policy"] = args.history_policy
        if args.exclude_flat_run_minutes is not None:
            if args.action != "select" or args.exclude_flat_run_minutes != 60:
                parser.error("--exclude-flat-run-minutes requires select and exactly 60")
            request["exclude_flat_run_minutes"] = args.exclude_flat_run_minutes
        if args.exclusion_review is not None:
            if args.action != "select" or args.history_policy != "gap_free_own_span":
                parser.error(
                    "--exclusion-review requires select --history-policy gap_free_own_span"
                )
            request["exclusion_review"] = str(args.exclusion_review)
        if args.rules_snapshot_dir is not None:
            if args.action != "select" or args.history_policy != "gap_free_own_span":
                parser.error(
                    "--rules-snapshot-dir requires select --history-policy gap_free_own_span"
                )
            request["rules_snapshot_dir"] = str(args.rules_snapshot_dir)
        if args.artifact_root is not None:
            request["artifact_root"] = str(args.artifact_root)
        if args.config_id is not None:
            request.update(config_id=args.config_id, scope=args.scope)
    receipt = worker(request, args.output)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if receipt["status"] in ("READY", "PASS", "COMPLETE") else 1


if __name__ == "__main__":
    sys.exit(main())
