"""Bounded full-roster settings measurements with exact retained parity evidence.

The original baseline executable is never patched. Its replay timing includes the
fixed warmup prefix; candidate-only measured timings are reported separately.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import statistics
import struct
import time
from pathlib import Path
from typing import Any

from scripts import research_sweep as runner
from scripts.research_sweep_differential import result_fingerprint
from scripts.research_sweep_reference import VENUES

MIB = 1024**2
COMPACT_FILES = ("pairs.jsonl", "pairs.index.json", "series.jsonl", "trades.jsonl")
CHECKPOINT_FILES = ("checkpoint.latest.json", "checkpoint.previous.json")


def compact_success(
    directory: Path, fingerprint: dict[str, Any], *, keep_result: bool = False
) -> None:
    """Retire verified benchmark checkpoints and duplicate financial outputs."""
    outcome = runner.read_json(directory / "execution.json")
    if outcome.get("exit_code") != 0 or outcome.get("cancelled") or outcome.get("resource_error"):
        raise ValueError("cannot compact an unsuccessful benchmark execution")
    engine = directory / "engine"
    if Path(outcome["result_dir"]).resolve() != engine.resolve():
        raise ValueError("benchmark output does not belong to its repetition")
    files: list[dict[str, Any]] = []
    for name in CHECKPOINT_FILES + (() if keep_result else COMPACT_FILES):
        path = engine / name
        if path.is_symlink():
            raise ValueError("refusing to compact a symlink")
        if path.is_file():
            files.append(
                {"name": name, "bytes": path.stat().st_size, "sha256": runner.sha256(path)}
            )
    previous_path = directory / "compaction.json"
    previous = runner.read_json(previous_path) if previous_path.exists() else {}
    if previous and previous.get("fingerprint") != fingerprint:
        raise ValueError("benchmark compaction fingerprint changed")
    receipt = {
        "status": "VERIFIED_COMPLETE" if keep_result else "VERIFIED_DUPLICATE",
        "fingerprint": fingerprint,
        "full_financial_result_retained": keep_result,
        "removed_files": [*previous.get("removed_files", []), *files],
    }
    runner.write_json(previous_path, receipt)
    for item in files:
        (engine / item["name"]).unlink()


def choose_settings(rows: list[dict[str, Any]], labels: set[str]) -> dict[str, Any]:
    """Within five percent prefer static scheduling, then smaller buffers."""
    candidates = []
    for label in sorted(labels):
        trials = [row for row in rows if row["label"] == label]
        if len(trials) != 3:
            raise ValueError("each benchmark candidate requires three completed repetitions")
        median = statistics.median(row["replay_seconds"] for row in trials)
        candidates.append((median, trials[0]["settings"], label))
    fastest = min(item[0] for item in candidates)
    eligible = [item for item in candidates if item[0] <= fastest * 1.05]
    median, settings, label = min(
        eligible,
        key=lambda item: (
            item[1]["threads"],
            item[1]["block_bytes"],
            item[1].get("reader_buffer_records", 256),
            item[1]["scheduler"] != "static",
        ),
    )
    return {**settings, "median_seconds": median, "measurement_label": label}


def _measurement(
    label: str, repeat: int, settings: dict[str, Any], outcome: dict[str, Any]
) -> dict[str, Any]:
    summary = outcome["summary"]
    timings = summary["timings"]
    return {
        "label": label,
        "repeat": repeat,
        "settings": settings,
        "replay_seconds": timings["replay_seconds"],
        "measured_replay_seconds": timings.get("measured_replay_seconds"),
        "measured_records": summary.get("measured_records"),
        "processed_records": summary["processed_records"],
        "wall_seconds": outcome["wall_seconds"],
        "peak_rss_bytes": outcome.get("observed_peak_rss_bytes"),
        "timings": timings,
        "resources": summary.get("resources", {}),
    }


def warmup_boundary(sources: list[dict[str, Any]], start: int, end: int) -> int:
    """Find the 65th timestamp with bounded reads and one source open at a time."""
    timestamps: set[int] = set()
    for source in sources:
        with Path(source["path"]).open("rb") as stream:
            left, right = 0, int(source["records"])
            while left < right:
                middle = (left + right) // 2
                stream.seek(middle * 24)
                raw = stream.read(8)
                if len(raw) != 8:
                    raise ValueError("source changed during warmup selection")
                if struct.unpack("<q", raw)[0] < start:
                    left = middle + 1
                else:
                    right = middle
            stream.seek(left * 24)
            payload = stream.read(65 * 24)
            if len(payload) % 24:
                raise ValueError("partial source record during warmup selection")
            for offset in range(0, len(payload), 24):
                timestamp = struct.unpack_from("<q", payload, offset)[0]
                if start <= timestamp < end:
                    timestamps.add(timestamp)
        # Only the earliest 65 unique points can affect the chosen boundary.
        if len(timestamps) > 130:
            timestamps = set(sorted(timestamps)[:65])
    if len(timestamps) < 65:
        raise runner.Blocked("benchmark requires 64 complete warmup timestamp groups")
    return sorted(timestamps)[64]


def compare_binaries(
    baseline: Path,
    binary: Path,
    sources: list[dict[str, Any]],
    output: Path,
    *,
    max_records: int = 2_097_152,
    warmup_until_ms: int | None = None,
    threads: int = 32,
) -> dict[str, Any]:
    """Measure the fixed full roster; never select markets from their returns."""
    if {source["venue"] for source in sources} != set(VENUES):
        raise runner.Blocked("full-roster benchmark requires every declared venue")
    if max_records <= 0 or threads != 32:
        raise ValueError("benchmark requires a positive record cap and 32 calculation threads")
    start, end = runner.best_window(sources, 7)
    warmup = warmup_boundary(sources, start, end) if warmup_until_ms is None else warmup_until_ms
    if not start < warmup < end or warmup % 60_000:
        raise ValueError("warmup must end at a minute boundary inside the fixed window")
    frozen = runner.make_plan(sources, output / "unused", "calibration", threads=threads)
    frozen.update(start_ms=start, end_ms=end, max_records=max_records)
    runner.write_json(
        output / "workload.json",
        {
            **frozen,
            "warmup_until_ms": warmup,
            "timing_comparison": "total replay including the identical fixed warmup prefix",
            "candidate_measured_timing": (
                "from forced warmup boundary; not available in old baseline"
            ),
        },
    )
    rows: list[dict[str, Any]] = []
    expected: dict[str, Any] | None = None
    expected_records: int | None = None
    retained: dict[str, str] = {}
    binary_hashes = {str(path): runner.sha256(path) for path in (baseline, binary)}

    def measure(variants: list[tuple[str, Path, dict[str, Any]]], *, buffers: bool = False) -> None:
        nonlocal expected, expected_records
        for repeat in range(3):
            offset = repeat % len(variants)
            ordered = variants[offset:] + variants[:offset]
            for label, executable, settings in ordered:
                if runner.sha256(executable) != binary_hashes[str(executable)]:
                    raise ValueError("benchmark executable changed between repetitions")
                directory = output / f"{label}-r{repeat}"
                plan = dict(frozen, **settings, output_dir=str(directory / "engine"))
                if label != "baseline":
                    plan["warmup_until_ms"] = warmup
                host_load_before = os.getloadavg()
                outcome = runner.engine_run(executable, plan, directory, output / "progress.json")
                host_load_after = os.getloadavg()
                fingerprint = result_fingerprint(directory / "engine")
                records = outcome["summary"]["processed_records"]
                if expected is not None and fingerprint != expected:
                    raise ValueError(f"exact benchmark parity failed for {label}; output retained")
                if expected_records is not None and records != expected_records:
                    raise ValueError("benchmark variants processed different record counts")
                if label != "baseline":
                    measured = outcome["summary"].get("measured_records")
                    if not isinstance(measured, int) or not 0 < measured < records:
                        raise ValueError(
                            "candidate did not complete both fixed warmup and measured work"
                        )
                if buffers:
                    filled = (
                        outcome["summary"].get("resources", {}).get("measured_filled_blocks", 0)
                    )
                    if filled < 2:
                        raise ValueError("buffer comparison did not fill two measured input blocks")
                expected, expected_records = fingerprint, records
                measurement = _measurement(label, repeat, settings, outcome)
                measurement.update(
                    fingerprint=fingerprint,
                    directory=str(directory),
                    host_load_before=host_load_before,
                    host_load_after=host_load_after,
                )
                rows.append(measurement)
                runner.write_json(output / "measurements.json", {"measurements": rows})
                # Keep the first complete baseline and each first candidate until
                # the winner is known. Failed/mismatched trials are never pruned.
                if label not in retained:
                    retained[label] = str(directory)
                    compact_success(directory, fingerprint, keep_result=True)
                else:
                    compact_success(directory, fingerprint)

    fixed = {"threads": threads, "block_bytes": MIB, "max_timestamp_groups": 4096}
    measure(
        [
            ("baseline", baseline, {**fixed, "scheduler": "static", "max_timestamp_groups": 64}),
            ("static", binary, {**fixed, "scheduler": "static"}),
            ("dynamic", binary, {**fixed, "scheduler": "dynamic"}),
        ]
    )
    scheduler = choose_settings(rows, {"static", "dynamic"})["scheduler"]
    # The buffer phase retains its first complete candidate independently. The
    # scheduler phase's exact results have already matched the retained baseline.
    for label in ("static", "dynamic"):
        assert expected is not None
        compact_success(Path(retained.pop(label)), expected)
    measure(
        [
            (f"buffer-{size}", binary, {**fixed, "scheduler": scheduler, "block_bytes": size * MIB})
            for size in (1, 4, 16)
        ],
        buffers=True,
    )
    settings = choose_settings(rows, {f"buffer-{size}" for size in (1, 4, 16)})
    keep = {"baseline", settings["measurement_label"]}
    for label, directory in retained.items():
        if label not in keep:
            assert expected is not None
            compact_success(Path(directory), expected)
    baseline_median = statistics.median(
        row["replay_seconds"] for row in rows if row["label"] == "baseline"
    )
    result = {
        "settings": settings,
        "sources": len(sources),
        "configurations": len(frozen["configurations"]),
        "processed_records": expected_records,
        "warmup_until_ms": warmup,
        "fingerprint": expected,
        "measurements": rows,
        "baseline_median_seconds": baseline_median,
        "speedup_total_replay": baseline_median / settings["median_seconds"],
        "timing_comparison": "total replay including identical fixed warmup prefix",
        "selection_timing": "total replay; candidate measured interval retained separately",
        "cache_conditions": "OS cache retained; no eviction; rotating execution order",
        "host_cpu_count": os.cpu_count(),
        "host_affinity_cpu_count": len(os.sched_getaffinity(0)),
        "host_background_load": "uncontrolled; 1/5/15-minute load averages saved for each trial",
        "retained_full_results": {label: retained[label] for label in sorted(keep)},
    }
    runner.write_json(output / "benchmark.json", result)
    return result


def compare_current(
    binary: Path,
    sources: list[dict[str, Any]],
    output: Path,
    *,
    max_records: int = 655_360,
    warmup_until_ms: int | None = None,
    audit_threads: int = 8,
) -> dict[str, Any]:
    """Compare bounded settings in fresh states under one process-owned audit.

    Only the current candidate is executed. No parent receipt is treated as a
    reusable source audit, and no completed history is retained in memory.
    """
    if {source["venue"] for source in sources} != set(VENUES) or max_records <= 0:
        raise runner.Blocked("candidate batch requires the complete seven-venue roster")
    if audit_threads not in (4, 8, 16, 32):
        raise ValueError("unsupported audit reader count")
    start, end = runner.best_window(sources, 7)
    warmup = warmup_boundary(sources, start, end) if warmup_until_ms is None else warmup_until_ms
    if not start < warmup < end or warmup % 60000:
        raise ValueError("invalid fixed warmup boundary")
    base = {
        "threads": 32,
        "max_timestamp_groups": 4096,
        "audit_threads": audit_threads,
        "chart_mode": "on_demand",
    }
    # A compact cross-product covers scheduler/buffer interactions without
    # executing the obsolete baseline or duplicating source audits per member.
    candidates = [
        {
            **base,
            "scheduler": scheduler,
            "block_bytes": block * MIB,
            "reader_buffer_records": reader,
        }
        for scheduler in ("static", "dynamic")
        for block in (1, 4, 16)
        for reader in (4096, 16384)
    ]
    variants: list[dict[str, Any]] = []
    settings_by_id: dict[str, dict[str, Any]] = {}
    for repeat in range(3):
        ordered = candidates[repeat:] + candidates[:repeat]
        if repeat == 1:
            ordered = list(reversed(ordered))
        for settings in ordered:
            label = (
                f"{settings['scheduler']}-b{settings['block_bytes']}"
                f"-r{settings['reader_buffer_records']}"
            )
            variant_id = f"{label}-repeat{repeat}"
            settings_by_id[variant_id] = settings
            variants.append(
                {
                    "id": variant_id,
                    "repeat": repeat,
                    "keep_results": not variants,
                    **{
                        k: v
                        for k, v in settings.items()
                        if k not in ("threads", "audit_threads", "chart_mode")
                    },
                }
            )
    plan = runner.make_plan(sources, output / "batch/engine", "calibration", threads=32)
    plan.update(
        base,
        start_ms=start,
        end_ms=end,
        max_records=max_records,
        warmup_until_ms=warmup,
        benchmark_variants=variants,
    )
    runner.write_json(output / "workload.json", plan)
    outcome = runner.engine_run(binary, plan, output / "batch", output / "progress.json")
    report = runner.read_json(Path(outcome["result_dir"]) / "batch-summary.json")
    if (
        report.get("schema") != "grid-research-sweep-benchmark-batch-v1"
        or report.get("status") != "COMPLETE_DIAGNOSTIC"
        or report.get("source_sha256_verified") is not True
        or report.get("source_audit_passes") != 2
    ):
        raise ValueError("batch lacks complete source verification")
    members = report.get("variants", [])
    if len(members) != len(variants) or [x["id"] for x in members] != [x["id"] for x in variants]:
        raise ValueError("batch member coverage/order mismatch")
    rows: list[dict[str, Any]] = []
    expected: dict[str, Any] | None = None
    count: int | None = None
    retained: str | None = None
    directories: set[Path] = set()
    measured_count: int | None = None
    for member, variant in zip(members, variants, strict=True):
        result_dir = Path(member["result_dir"]).resolve()
        if (
            result_dir.parent != Path(outcome["result_dir"]).resolve()
            or result_dir.name != member["id"]
            or result_dir in directories
        ):
            raise ValueError("batch result is outside its owned directory")
        directories.add(result_dir)
        summary = runner.read_json(result_dir / "summary.json")
        if (
            summary.get("status") != "COMPLETE_DIAGNOSTIC"
            or summary.get("batch_source_sha256_verified") is not True
        ):
            raise ValueError("batch member lacks final source verification")
        fingerprint = member["fingerprint"]
        if (
            fingerprint.get("pair_rows") != len(sources) * 540
            or fingerprint.get("configuration_count") != 540
        ):
            raise ValueError("batch fingerprint roster incomplete")
        if summary.get("fingerprint") != fingerprint or any(
            not re.fullmatch(r"[0-9a-f]{64}", str(fingerprint.get(name, "")))
            for name in ("pairs_sha256", "configurations_sha256")
        ):
            raise ValueError("invalid or unbound batch fingerprint")
        encoded = json.dumps(
            summary["configurations"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        if hashlib.sha256(encoded).hexdigest() != fingerprint["configurations_sha256"]:
            raise ValueError("rollup bytes differ from compact fingerprint")
        if expected is not None and fingerprint != expected:
            raise ValueError("candidate batch exact financial parity mismatch")
        expected = fingerprint
        if count is not None and summary["processed_records"] != count:
            raise ValueError("candidate batch changed measured work")
        count = summary["processed_records"]
        measured = summary.get("measured_records", 0)
        if (
            type(count) is not int
            or count <= 0
            or type(measured) is not int
            or not 0 < measured < count
            or (measured_count is not None and measured != measured_count)
        ):
            raise ValueError("candidate batch changed or has empty measured work")
        measured_count = measured
        for key in ("replay_seconds", "measured_replay_seconds"):
            seconds = summary["timings"].get(key)
            if (
                not isinstance(seconds, (float, int))
                or isinstance(seconds, bool)
                or not math.isfinite(seconds)
                or seconds <= 0
            ):
                raise ValueError("invalid candidate replay timing")
        if summary["resources"].get("measured_filled_blocks", 0) < 2:
            raise ValueError("candidate capacity did not fill two measured input blocks")
        settings = settings_by_id[member["id"]]
        actual = summary["resources"]
        for key in (
            "threads",
            "scheduler",
            "block_bytes",
            "reader_buffer_records",
            "audit_threads",
        ):
            if actual.get(key) != settings[key]:
                raise ValueError(f"candidate batch ignored requested {key}")
        if (
            actual.get("max_timestamp_groups_per_block") != settings["max_timestamp_groups"]
            or summary.get("chart_mode") != "on_demand"
        ):
            raise ValueError("candidate batch ignored shared settings")
        label = member["id"].rsplit("-repeat", 1)[0]
        rows.append(
            _measurement(
                label,
                variant["repeat"],
                settings,
                {"summary": summary, "wall_seconds": summary["timings"]["wall_seconds"]},
            )
        )
        if variant["keep_results"]:
            # Independent semantic verification reads every retained pair row.
            independent = result_fingerprint(result_dir)
            if independent["pair_rows"] != fingerprint["pair_rows"]:
                raise ValueError("independent batch fingerprint coverage mismatch")
            if runner.sha256(result_dir / "pairs.jsonl") != fingerprint["pairs_sha256"]:
                raise ValueError("retained pair bytes differ from compact fingerprint")
            retained = str(result_dir)
    if not retained or expected is None:
        raise ValueError("batch has no independent retained result")
    selected = choose_settings(rows, {row["label"] for row in rows})
    result = {
        "sources": len(sources),
        "configurations": 540,
        "settings": selected,
        "measurements": rows,
        "fingerprint": expected,
        "retained_full_result": retained,
        "source_audits": "complete before and after this process-owned batch",
        "cache_conditions": "OS cache retained; rotating order; no cache eviction",
        "scope": "full admitted roster; fixed warmup and measured records; candidate only",
    }
    runner.write_json(output / "benchmark.json", result)
    return result


def worker(request: dict[str, Any], output: Path) -> dict[str, Any]:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("benchmark output must be new and empty")
    receipt: dict[str, Any] = {
        "schema": "grid-research-sweep-benchmark-v1",
        "action": "benchmark",
        "status": "RUNNING",
        "started_unix": time.time(),
        "acceptance_policy": dict(runner.ACCEPTANCE_POLICY),
    }
    runner.write_json(output / "request.json", request)
    old_root, runner._ACTIVE_JOB_ROOT = runner._ACTIVE_JOB_ROOT, output
    try:
        with runner.HeavyJobLock():
            runner.check_resources(runner.resource_observation(output), runner.LIMITS)
            preparation = runner.preparation_path(Path(request["source_output"]))
            sources = runner._engine_sources(runner.read_json(preparation))
            binary = Path(request["binary"]).resolve(strict=True)
            baseline = (
                Path(request["baseline_binary"]).resolve(strict=True)
                if request.get("baseline_binary")
                else None
            )
            study = runner.freeze_study(preparation, binary, output)
            receipt.update(
                preparation_path=str(preparation),
                preparation_sha256=runner.sha256(preparation),
                binary_sha256=runner.sha256(binary),
                baseline_binary_sha256=runner.sha256(baseline) if baseline else None,
                code_sha256=runner.study_code_identity(study),
                study_manifest=str(study),
            )
            evidence = runner.prior_evidence(
                Path(request["source_output"]),
                receipt["preparation_sha256"],
                receipt["binary_sha256"],
                receipt["code_sha256"],
            )
            if "validate" not in evidence:
                raise runner.Blocked(
                    "matching validation receipt required before full-roster benchmark"
                )
            receipt["evidence"] = evidence
            runner.write_json(output / "admission.json", receipt)
            if baseline is None:
                receipt["schema"] = "grid-research-sweep-benchmark-v2"
                result = compare_current(
                    binary,
                    sources,
                    output,
                    max_records=int(request.get("max_records", 655_360)),
                    warmup_until_ms=request.get("warmup_until_ms"),
                    audit_threads=int(request.get("audit_threads", 8)),
                )
            else:
                result = compare_binaries(
                    baseline,
                    binary,
                    sources,
                    output,
                    max_records=int(request.get("max_records", 2_097_152)),
                    warmup_until_ms=request.get("warmup_until_ms"),
                )
            if runner.sha256(preparation) != receipt["preparation_sha256"]:
                raise ValueError("preparation changed during benchmark")
            for relative, digest in runner.read_json(study)["code_sha256"].items():
                if runner.sha256(Path(__file__).resolve().parents[1] / relative) != digest:
                    raise ValueError("implementation changed during benchmark")
            receipt.update(result, status="PASS")
    except runner.Blocked as error:
        receipt.update(status="BLOCKED", error=str(error))
    except runner.ResourceLimit as error:
        receipt.update(status="RESOURCE_LIMIT", error=str(error))
    except InterruptedError as error:
        receipt.update(status="CANCELLED", error=str(error))
    except (ValueError, OSError, RuntimeError) as error:
        receipt.update(status="FAIL", error=f"{type(error).__name__}: {error}")
    finally:
        runner._ACTIVE_JOB_ROOT = old_root
    receipt["finished_unix"] = time.time()
    runner.write_json(output / "receipt.json", receipt)
    runner.write_json(output / "progress.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-output", type=Path, required=True)
    parser.add_argument(
        "--baseline-binary",
        type=Path,
        help="Explicit legacy A/B only; omit for one audited current-candidate batch",
    )
    parser.add_argument("--binary", type=Path, default=runner.DEFAULT_BINARY)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-records", type=int, default=655_360)
    parser.add_argument("--audit-threads", type=int, choices=(4, 8, 16, 32), default=8)
    parser.add_argument("--warmup-until-ms", type=int)
    args = parser.parse_args()
    request = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in vars(args).items()
        if key != "output"
    }
    receipt = worker(request, args.output)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    with contextlib.suppress(BrokenPipeError):
        raise SystemExit(main())
