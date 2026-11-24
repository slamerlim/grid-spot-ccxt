"""Finite crash/recovery checks for the isolated exact research engine.

Only invented histories and retained test outputs under the supplied directory
are changed. This does not touch archive datasets or live trading state.
"""

from __future__ import annotations

import copy
import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _resume_copy(plan: dict[str, Any], source: Path, destination: Path) -> dict[str, Any]:
    import shutil

    shutil.copytree(source, destination)
    result = copy.deepcopy(plan)
    result.update(output_dir=str(destination.resolve()), resume=True, threads=3)
    result.pop("stop_after_blocks", None)
    return result


def _same_results(expected: Path, actual: Path) -> None:
    first = json.loads((expected / "summary.json").read_text())
    second = json.loads((actual / "summary.json").read_text())
    for key in ("configurations", "processed_records", "last_ms"):
        if first[key] != second[key]:
            raise ValueError(f"resume changed exact {key}")
    for filename in ("pairs.jsonl", "pairs.index.json", "series.jsonl", "trades.jsonl"):
        if not (expected / filename).exists() and not (actual / filename).exists():
            continue
        if (expected / filename).read_bytes() != (actual / filename).read_bytes():
            raise ValueError(f"resume changed {filename}")


def _flip_first_byte(path: Path) -> None:
    with path.open("r+b") as handle:
        original = handle.read(1)
        if not original:
            raise ValueError("corruption fixture unexpectedly empty")
        handle.seek(0)
        handle.write(bytes([original[0] ^ 1]))


def _parallel_checkpoint_checks(binary: Path, output: Path) -> list[dict[str, Any]]:
    """Exercise bounded serializer queues and an actual failing writer syscall."""
    from scripts import research_sweep as runner
    from scripts.research_sweep_reference import fixture_sources

    # A single configuration exceeds two 1 MiB queue chunks, including before
    # every invented market has started. This actually exercises backpressure.
    # Compact checkpoints omit derived reporting fields. Keep the fixture
    # larger than both 1 MiB queue slots so this still proves backpressure.
    sources = fixture_sources(output / "inputs", count=8192, minutes=1)
    configurations = [
        {"id": "normal", "step_pct": "1", "tp_pct": "10"},
        {"id": "round", "step_pct": "1.5", "tp_pct": "2"},
    ]
    plan = runner.make_plan(
        sources,
        output / "continuous/engine",
        "validation",
        threads=32,
        block_bytes=4096,
        configurations=configurations,
    )
    plan.update(scheduler="dynamic", checkpoint_seconds=0, chart_mode="eager")
    runner.engine_run(binary, plan, output / "continuous")
    paused = dict(plan, output_dir=str(output / "paused/engine"), stop_after_blocks=1)
    runner.engine_run(binary, paused, output / "paused", expected_codes=(3,))
    paused_directory = Path(paused["output_dir"])
    with (paused_directory / "checkpoint.latest.json").open("rb") as stream:
        stream.readline()
        configuration_bytes = len(stream.readline())
    if configuration_bytes <= 2 * 1024**2:
        raise ValueError("checkpoint fixture did not exercise serialization queue backpressure")
    resumed = _resume_copy(paused, paused_directory, output / "resumed/engine")
    resumed["scheduler"] = "static"
    runner.engine_run(binary, resumed, output / "resumed")
    _same_results(Path(plan["output_dir"]), Path(resumed["output_dir"]))

    fault_directory = output / "writer-failure"
    fault_directory.mkdir()
    fault_plan = dict(plan, output_dir=str(fault_directory / "engine"), stop_after_blocks=1)
    fault_path = fault_directory / "plan.json"
    runner.write_json(fault_path, fault_plan)
    # Allow plan publication, but fail partway through the much larger checkpoint.
    file_limit = len(json.dumps(fault_plan, separators=(",", ":")).encode()) + 64 * 1024
    if file_limit >= 2 * configuration_bytes:
        raise ValueError("writer fault limit would not interrupt checkpoint publication")
    launcher = (
        "import os, resource, signal, sys\n"
        "resource.setrlimit(resource.RLIMIT_FSIZE, (int(sys.argv[1]), int(sys.argv[1])))\n"
        "signal.signal(signal.SIGXFSZ, signal.SIG_IGN)\n"
        "os.execv(sys.argv[2], sys.argv[2:])\n"
    )
    inherited = (runner._ACTIVE_LOCK_FD,) if runner._ACTIVE_LOCK_FD is not None else ()
    with (
        (fault_directory / "stdout.log").open("wb") as stdout,
        (fault_directory / "stderr.log").open("wb") as stderr,
    ):
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                launcher,
                str(file_limit),
                str(binary),
                "research-sweep",
                str(fault_path),
            ],
            stdout=stdout,
            stderr=stderr,
            timeout=60,
            pass_fds=inherited,
            check=False,
        )
    engine = fault_directory / "engine"
    temporary = engine / "checkpoint.tmp"
    if (
        child.returncode != 2
        or not temporary.is_file()
        or temporary.stat().st_size != file_limit
        or "research output write failed" not in (fault_directory / "stderr.log").read_text()
        or (engine / "summary.json").exists()
        or (engine / "checkpoint.latest.json").exists()
    ):
        raise ValueError("checkpoint writer failure was not rejected cleanly")
    return [
        {
            "name": "parallel_checkpoint_backpressure_and_resume",
            "passed": True,
            "configuration_bytes": configuration_bytes,
            "sources": len(sources),
        },
        {
            "name": "parallel_checkpoint_writer_failure_no_deadlock",
            "passed": True,
            "file_limit_bytes": file_limit,
            "exit_code": child.returncode,
        },
    ]


def validate_recovery(binary: Path, output: Path) -> dict[str, Any]:
    """Prove durable continuation, fail-closed inputs and a real SIGTERM stop."""
    from scripts import research_sweep as runner
    from scripts.research_sweep_reference import fixture_sources, verify_result

    output.mkdir(parents=True, exist_ok=False)
    binary = binary.resolve()
    binary_hash = runner.sha256(binary)
    sources = fixture_sources(output / "inputs", minutes=96)
    configurations = [
        {"id": "normal", "step_pct": "1", "tp_pct": "10"},
        {"id": "round", "step_pct": "1.5", "tp_pct": "2"},
        {"id": "deep", "step_pct": "10", "tp_pct": "15"},
    ]
    sources[1]["rules"]["price_tick"] = "1"
    sources[-1]["rules"].update(price_mode="significant", price_digits=5, max_price_decimals=8)
    plan = runner.make_plan(
        sources,
        output / "continuous" / "engine",
        "validation",
        threads=1,
        block_bytes=4096,
        configurations=configurations,
    )
    plan.update(trace=True, checkpoint_seconds=0, chart_mode="eager")
    checks: list[dict[str, Any]] = []
    runner.engine_run(binary, plan, output / "continuous")
    baseline = Path(plan["output_dir"])
    checks.append(
        {"name": "continuous_fraction_reference", "passed": True, **verify_result(plan, baseline)}
    )

    paused = copy.deepcopy(plan)
    paused.update(output_dir=str(output / "paused-one" / "engine"), stop_after_blocks=1)
    runner.engine_run(binary, paused, output / "paused-one", expected_codes=(3,))
    if (Path(paused["output_dir"]) / "summary.json").exists():
        raise ValueError("checkpoint stop falsely published completion")
    resumed = _resume_copy(paused, Path(paused["output_dir"]), output / "resumed" / "engine")
    for filename in ("series.jsonl", "trades.jsonl"):
        with (Path(resumed["output_dir"]) / filename).open("ab") as handle:
            handle.write(b"uncommitted garbage tail\n")
    runner.engine_run(binary, resumed, output / "resumed")
    _same_results(baseline, Path(resumed["output_dir"]))
    checks.append({"name": "checkpoint_resume_and_tail_truncation", "passed": True})

    # Disabling chart accumulation must not alter financial observations or
    # recovery. Compare with the eager run and independently with Fraction.
    from scripts.research_sweep_differential import compare_results

    deferred = dict(
        plan,
        chart_mode="on_demand",
        scheduler="dynamic",
        threads=32,
        output_dir=str(output / "on-demand/engine"),
    )
    runner.engine_run(binary, deferred, output / "on-demand")
    compare_results(baseline, Path(deferred["output_dir"]), include_charts=False)
    deferred_stop = dict(
        deferred, output_dir=str(output / "on-demand-paused/engine"), stop_after_blocks=2
    )
    runner.engine_run(binary, deferred_stop, output / "on-demand-paused", expected_codes=(3,))
    deferred_resume = _resume_copy(
        deferred_stop, Path(deferred_stop["output_dir"]), output / "on-demand-resumed/engine"
    )
    runner.engine_run(binary, deferred_resume, output / "on-demand-resumed")
    _same_results(Path(deferred["output_dir"]), Path(deferred_resume["output_dir"]))
    checks.append(
        {
            "name": "on_demand_financial_parity_and_resume",
            "passed": True,
            **verify_result(deferred_resume, Path(deferred_resume["output_dir"])),
        }
    )

    paused_two = copy.deepcopy(plan)
    paused_two.update(output_dir=str(output / "paused-two" / "engine"), stop_after_blocks=2)
    runner.engine_run(binary, paused_two, output / "paused-two", expected_codes=(3,))
    paused_dir = Path(paused_two["output_dir"])
    fallback = _resume_copy(paused_two, paused_dir, output / "fallback" / "engine")
    (Path(fallback["output_dir"]) / "checkpoint.latest.json").write_bytes(
        b"incomplete checkpoint\n"
    )
    runner.engine_run(binary, fallback, output / "fallback")
    _same_results(baseline, Path(fallback["output_dir"]))
    progress = runner.read_json(Path(fallback["output_dir"]) / "progress.json")
    if not progress.get("checkpoint_fallback"):
        raise ValueError("corrupt latest checkpoint did not use the previous generation")
    checks.append({"name": "previous_checkpoint_fallback", "passed": True})

    corrupted = _resume_copy(paused_two, paused_dir, output / "output-corruption" / "engine")
    _flip_first_byte(Path(corrupted["output_dir"]) / "series.jsonl")
    runner.engine_run(binary, corrupted, output / "output-corruption", expected_codes=(2,))
    diagnostic = (output / "output-corruption" / "stderr.log").read_text()
    if "prefix" not in diagnostic or (Path(corrupted["output_dir"]) / "summary.json").exists():
        raise ValueError("committed output corruption was not rejected")
    checks.append({"name": "committed_output_corruption_rejected", "passed": True})

    for changed_kind in ("rules", "configuration"):
        changed_dir = output / f"changed-{changed_kind}"
        changed = _resume_copy(paused_two, paused_dir, changed_dir / "engine")
        if changed_kind == "rules":
            changed["sources"][0]["rules"]["min_cost"] = "6"
        else:
            changed["configurations"][0]["tp_pct"] = "11"
        runner.engine_run(binary, changed, changed_dir, expected_codes=(2,))
        diagnostic = (changed_dir / "stderr.log").read_text()
        if "checkpoint plan or executable mismatch" not in diagnostic:
            raise ValueError(f"resume did not reject changed {changed_kind}")
        if (Path(changed["output_dir"]) / "summary.json").exists():
            raise ValueError(f"changed {changed_kind} falsely published completion")
        checks.append({"name": f"changed_{changed_kind}_resume_rejected", "passed": True})

    mutation_sources = fixture_sources(output / "source-mutation" / "inputs", minutes=32)
    mutation = runner.make_plan(
        mutation_sources,
        output / "source-mutation" / "engine",
        "validation",
        block_bytes=4096,
        configurations=configurations,
    )
    mutation.update(checkpoint_seconds=0, stop_after_blocks=1)
    runner.engine_run(binary, mutation, output / "source-mutation" / "pause", expected_codes=(3,))
    # Only this test's freshly generated source is mutated; archive paths are never used.
    source_path = Path(mutation_sources[0]["path"])
    with source_path.open("r+b") as handle:
        handle.seek(-1, 2)
        original = handle.read(1)
        handle.seek(-1, 2)
        handle.write(bytes([original[0] ^ 1]))
    mutation["resume"] = True
    mutation.pop("stop_after_blocks")
    runner.engine_run(binary, mutation, output / "source-mutation" / "resume", expected_codes=(2,))
    if (Path(mutation["output_dir"]) / "summary.json").exists():
        raise ValueError("mutated source falsely published completion")
    checks.append({"name": "source_mutation_rejected", "passed": True})

    # Deliberately impossible resource limits run directly, so the outer monitor
    # cannot intercept the failure before the engine demonstrates its own guard.
    inherited = (runner._ACTIVE_LOCK_FD,) if runner._ACTIVE_LOCK_FD is not None else ()
    for name, bound, value, marker in (
        ("resource-refusal", "rss_bytes", 1, "RSS limit"),
        ("disk-refusal", "min_free_disk_bytes", 2**63, "free-disk floor"),
    ):
        resource_dir = output / name
        resource_dir.mkdir()
        limited = copy.deepcopy(plan)
        limited["output_dir"] = str(resource_dir / "engine")
        limited["runtime_limits"][bound] = value
        runner.write_json(resource_dir / "plan.json", limited)
        with (
            (resource_dir / "stdout.log").open("wb") as stdout,
            (resource_dir / "stderr.log").open("wb") as stderr,
        ):
            child = subprocess.run(
                [str(binary), "research-sweep", str(resource_dir / "plan.json")],
                stdout=stdout,
                stderr=stderr,
                timeout=30,
                pass_fds=inherited,
                check=False,
            )
        diagnostic = (resource_dir / "stderr.log").read_text()
        if child.returncode != 2 or marker not in diagnostic:
            raise ValueError(f"engine did not reject the impossible {bound}")
        if (Path(limited["output_dir"]) / "summary.json").exists():
            raise ValueError(f"{name} falsely published completion")
        checks.append(
            {
                "name": f"engine_{name.replace('-', '_')}",
                "passed": True,
                "exit_code": child.returncode,
            }
        )

    cancel_dir = output / "signal-cancel"
    cancel_sources = fixture_sources(cancel_dir / "inputs", minutes=3000)
    cancellation = runner.make_plan(
        cancel_sources,
        cancel_dir / "engine",
        "validation",
        threads=2,
        block_bytes=4096,
        configurations=configurations[:2],
    )
    cancellation["checkpoint_seconds"] = 0
    runner.write_json(cancel_dir / "plan.json", cancellation)
    with (
        (cancel_dir / "stdout.log").open("wb") as stdout,
        (cancel_dir / "stderr.log").open("wb") as stderr,
    ):
        process = subprocess.Popen(
            [str(binary), "research-sweep", str(cancel_dir / "plan.json")],
            stdout=stdout,
            stderr=stderr,
            pass_fds=inherited,
        )
        signaled = False
        deadline = time.monotonic() + 60
        try:
            while process.poll() is None and time.monotonic() < deadline:
                progress_path = cancel_dir / "engine" / "progress.json"
                if progress_path.exists():
                    current = runner.read_json(progress_path)
                    if (
                        current.get("status") == "RUNNING"
                        and current.get("processed_blocks", 0) >= 1
                    ):
                        process.send_signal(signal.SIGTERM)
                        signaled = True
                        break
                time.sleep(0.01)
            code = process.wait(timeout=60)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    cancellation_progress = runner.read_json(cancel_dir / "engine" / "progress.json")
    if not signaled or code != 3 or cancellation_progress["status"] != "CHECKPOINTED":
        raise ValueError("SIGTERM did not stop at a durable complete timestamp boundary")
    if (cancel_dir / "engine" / "summary.json").exists():
        raise ValueError("SIGTERM stop falsely published completion")
    cancel_resume = _resume_copy(
        cancellation, cancel_dir / "engine", output / "signal-resume" / "engine"
    )
    runner.engine_run(binary, cancel_resume, output / "signal-resume")
    checked_cancel = verify_result(cancel_resume, Path(cancel_resume["output_dir"]))
    checks.append({"name": "sigterm_checkpoint_and_resume", "passed": True, **checked_cancel})
    checks.extend(_parallel_checkpoint_checks(binary, output / "parallel-checkpoint"))
    if runner.sha256(binary) != binary_hash:
        raise ValueError("research executable changed during recovery validation")
    result = {
        "schema": "grid-research-sweep-recovery-validation-v1",
        "binary_sha256": binary_hash,
        "checks": checks,
        "passed": True,
        "output_dir": str(output),
    }
    runner.write_json(output / "recovery-validation.json", result)
    return result
