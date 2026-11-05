"""Bounded format comparison, not a deployment/NVMe or cold-cache certification."""

from __future__ import annotations

import bisect
import hashlib
import os
import platform
import re
import resource
import sqlite3
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from functools import partial
from importlib.metadata import version
from pathlib import Path
from typing import Any, TypedDict

from grid.domain.numbers import DomainError, bounded_int
from grid.persistence.storage import filesystem
from grid.research.artifacts import output_directory
from grid.research.history import (
    MAX_TIMESTAMP,
    MINUTE,
    RECORD,
    Manifest,
    Source,
    identity,
    records,
    validate,
)

MAX_DERIVED_BYTES = 128 * 1024 * 1024
MAX_BENCH_RECORDS = 131072
BLOCK_ROWS = 4096
MAX_QUERIES = 32
MAX_QUERY_RECORDS = 1_048_576


@dataclass(frozen=True)
class QueryPlan:
    """Optional half-open UTC interval plus deterministic random minute windows."""

    start_ms: int | None = None
    end_ms: int | None = None
    random_windows: int = 0
    window_minutes: int | None = None
    seed: int | None = None

    def __post_init__(self) -> None:
        if (self.start_ms is None) != (self.end_ms is None):
            raise DomainError("both UTC endpoints required")
        if self.start_ms is not None and self.end_ms is not None:
            bounded_int(self.start_ms, MINUTE, MAX_TIMESTAMP)
            bounded_int(self.end_ms, self.start_ms + MINUTE, MAX_TIMESTAMP + MINUTE)
            if self.start_ms % MINUTE or self.end_ms % MINUTE:
                raise DomainError("query endpoints must be UTC minutes")
        bounded_int(self.random_windows, 0, MAX_QUERIES - int(self.start_ms is not None))
        if self.random_windows:
            if self.window_minutes is None or self.seed is None:
                raise DomainError("random queries require explicit duration and seed")
            bounded_int(self.window_minutes, 1, MAX_TIMESTAMP // MINUTE)
            bounded_int(self.seed, 0, 2**64 - 1)
        elif self.window_minutes is not None or self.seed is not None:
            raise DomainError("random duration/seed without random queries")
        if self.start_ms is None and not self.random_windows:
            raise DomainError("at least one query required")


def query_windows(plan: QueryPlan, manifest: Manifest) -> tuple[tuple[int, int], ...]:
    first, last = manifest.first_ms, manifest.last_ms
    if first is None or last is None:
        raise DomainError("query source has no UTC span")
    windows: list[tuple[int, int]] = []
    if plan.start_ms is not None and plan.end_ms is not None:
        if plan.start_ms < first or plan.end_ms > last + MINUTE:
            raise DomainError("query exceeds selected source UTC span")
        windows.append((plan.start_ms, plan.end_ms))
    if plan.random_windows:
        if plan.window_minutes is None or plan.seed is None:
            raise DomainError("random queries require explicit duration and seed")
        available = (last + MINUTE - first) // MINUTE - plan.window_minutes + 1
        if available < 1:
            raise DomainError("random query duration exceeds selected UTC span")
        for index in range(plan.random_windows):
            # Versioned deterministic schedule; modulo selection, not a claim of unbiased sampling.
            raw = hashlib.sha256(f"grid-storage-query-v1:{plan.seed}:{index}".encode()).digest()
            start = first + (int.from_bytes(raw, "big") % available) * MINUTE
            windows.append((start, start + plan.window_minutes * MINUTE))
    return tuple(windows)


def query_columnar(
    path: Path, kind: str, start: int, stop: int, total: int, *, block_rows: int = BLOCK_ROWS
) -> Iterator[bytes]:
    """Only this benchmark's verified fixed-size batches/row groups are supported."""
    bounded_int(total, 1, MAX_BENCH_RECORDS)
    bounded_int(block_rows, 1, BLOCK_ROWS)
    bounded_int(start, 0, total)
    bounded_int(stop, start, total)
    if kind not in ("arrow", "parquet"):
        raise DomainError("unsupported derived format")
    if start == stop:
        return
    pa, ipc, pq = _arrow()
    expected = pa.schema(
        [
            ("timestamp", pa.int64()),
            *[(name, pa.float32()) for name in ("open", "high", "low", "close")],
        ]
    )
    with pa.memory_map(str(path), "r") as stream:
        reader = ipc.open_file(stream) if kind == "arrow" else pq.ParquetFile(stream)
        schema = reader.schema if kind == "arrow" else reader.schema_arrow
        groups = reader.num_record_batches if kind == "arrow" else reader.num_row_groups
        if not schema.equals(expected) or groups != (total + block_rows - 1) // block_rows:
            raise DomainError("derived query layout differs")
        for group in range(start // block_rows, (stop - 1) // block_rows + 1):
            expected_rows = min(block_rows, total - group * block_rows)
            if kind == "parquet" and reader.metadata.row_group(group).num_rows != expected_rows:
                raise DomainError("derived query rows differ")
            batch = (
                reader.get_batch(group)
                if kind == "arrow"
                else reader.read_row_group(group, use_threads=False)
            )
            if batch.num_rows != expected_rows or any(c.null_count for c in batch.columns):
                raise DomainError("derived query rows differ")
            offset = max(start - group * block_rows, 0)
            count = min(stop - group * block_rows, expected_rows) - offset
            columns = [column.to_pylist() for column in batch.slice(offset, count).columns]
            for values in zip(*columns, strict=True):
                yield RECORD.pack(*values)
            del batch, columns


class Measurement(TypedDict):
    wall_ns: int
    cpu_ns: int
    roundtrip_sha256: str
    process_io_counters: dict[str, int]


def measure(reader: Callable[[], Iterator[bytes]], expected: str) -> Measurement:
    before = resource.getrusage(resource.RUSAGE_SELF)
    cpu_start, wall_start = time.process_time_ns(), time.perf_counter_ns()
    observed = checksum(reader())
    wall, cpu = time.perf_counter_ns() - wall_start, time.process_time_ns() - cpu_start
    after = resource.getrusage(resource.RUSAGE_SELF)
    if observed != expected:
        raise DomainError("derived data failed byte-exact roundtrip")
    return {
        "wall_ns": wall,
        "cpu_ns": cpu,
        "roundtrip_sha256": observed,
        "process_io_counters": {
            "input_blocks": after.ru_inblock - before.ru_inblock,
            "output_blocks": after.ru_oublock - before.ru_oublock,
            "major_faults": after.ru_majflt - before.ru_majflt,
        },
    }


def benchmark_queries(
    source: Source,
    sample_dir: Path,
    timestamps: tuple[int, ...],
    queries: list[dict[str, object]],
    repeats: int,
) -> list[dict[str, object]]:
    names = ("raw_mmap", "arrow", "parquet")
    results: list[dict[str, object]] = []
    for index, query in enumerate(queries):
        start_ms, end_ms = query["start_ms"], query["end_ms_exclusive"]
        if not isinstance(start_ms, int) or not isinstance(end_ms, int):
            raise DomainError("invalid prepared query")

        def read(name: str, start_ms: int = start_ms, end_ms: int = end_ms) -> Iterator[bytes]:
            start, stop = (
                bisect.bisect_left(timestamps, start_ms),
                bisect.bisect_left(timestamps, end_ms),
            )
            if name == "raw_mmap":
                if start == stop:
                    return iter(())
                return records(
                    replace(source, offset=source.offset + start, count=stop - start), "mmap"
                )
            return query_columnar(sample_dir / f"sample.{name}", name, start, stop, source.count)

        timings: dict[str, list[Measurement]] = {name: [] for name in names}
        orders: list[tuple[str, ...]] = []
        for repeat in range(repeats):
            shift = (index + repeat) % len(names)
            order = names[shift:] + names[:shift]
            orders.append(order)
            for name in order:
                timings[name].append(measure(partial(read, name), str(query["expected_sha256"])))
        summary = {
            name: {
                "median_wall_ns": statistics.median(sample["wall_ns"] for sample in samples),
                "max_wall_ns": max(sample["wall_ns"] for sample in samples),
                "median_cpu_ns": statistics.median(sample["cpu_ns"] for sample in samples),
                "max_cpu_ns": max(sample["cpu_ns"] for sample in samples),
            }
            for name, samples in timings.items()
        }
        results.append({**query, "reader_order": orders, "timings": timings, "summary": summary})
    return results


def device_evidence(path: Path, sysfs: Path = Path("/sys")) -> dict[str, object]:
    device = path.stat().st_dev
    node = sysfs / "dev/block" / f"{os.major(device)}:{os.minor(device)}"
    try:
        resolved = node.resolve(strict=True)
        controller = next(part for part in resolved.parts if re.fullmatch(r"nvme\d+", part))
        model = (sysfs / "class/nvme" / controller / "model").read_text().strip()
        firmware = (sysfs / "class/nvme" / controller / "firmware_rev").read_text().strip()
        return {
            "status": "OBSERVED_NVME",
            "device": node.name,
            "sysfs_path": str(resolved),
            "model": model,
            "firmware": firmware,
            "limit": "Kernel device evidence only; cached scan is not hardware throughput.",
        }
    except (OSError, StopIteration):
        return {
            "status": "BLOCKED",
            "device": node.name,
            "reason": "Direct NVMe backing not established from st_dev and sysfs.",
        }


def _arrow() -> tuple[Any, Any, Any]:
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
        import pyarrow.ipc as ipc  # type: ignore[import-untyped]
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as exc:
        raise DomainError("install the pinned research dependency group for benchmarks") from exc
    return pa, ipc, pq


def columnar_records(path: Path, kind: str) -> Iterator[bytes]:
    pa, ipc, pq = _arrow()
    with pa.memory_map(str(path), "r") as stream:
        if kind == "arrow":
            reader = ipc.open_file(stream)
            batches = (reader.get_batch(i) for i in range(reader.num_record_batches))
        elif kind == "parquet":
            batches = pq.ParquetFile(stream).iter_batches(batch_size=4096, use_threads=False)
        else:
            raise DomainError("unsupported derived format")
        for batch in batches:
            if batch.schema.names != ["timestamp", "open", "high", "low", "close"]:
                raise DomainError("derived schema differs")
            columns = [column.to_pylist() for column in batch.columns]
            for values in zip(*columns, strict=True):
                yield RECORD.pack(*values)


def checksum(rows: Iterator[bytes]) -> str:
    hasher = hashlib.sha256()
    for raw in rows:
        # Include typed reconstruction for EVERY format, not a raw-only memcpy baseline.
        hasher.update(RECORD.pack(*RECORD.unpack(raw)))
    return hasher.hexdigest()


def benchmark(
    source: Source, directory: Path, *, repeats: int = 3, queries: QueryPlan | None = None
) -> dict[str, object]:
    bounded_int(repeats, 1, 10)
    bounded_int(source.count, 1, MAX_BENCH_RECORDS)
    manifest = validate(source)
    if manifest.rejected or "malformed_file_length" in manifest.blockers:
        raise DomainError("benchmark needs structurally valid finite OHLC records")
    # Storage comparison may include gaps/unverified collection identity; no trading occurs.
    windows = () if queries is None else query_windows(queries, manifest)
    pa, ipc, pq = _arrow()
    started = time.perf_counter_ns()
    rows = [RECORD.unpack(raw) for raw in records(source)]
    query_started = time.perf_counter_ns()
    timestamps = tuple(int(row[0]) for row in rows) if queries is not None else ()
    prepared: list[dict[str, object]] = []
    selected = 0
    for start_ms, end_ms in windows:
        start, stop = (
            bisect.bisect_left(timestamps, start_ms),
            bisect.bisect_left(timestamps, end_ms),
        )
        selected += stop - start
        if selected > MAX_QUERY_RECORDS:
            raise DomainError("query workload exceeds selected-record cap")
        prepared.append(
            {
                "start_ms": start_ms,
                "end_ms_exclusive": end_ms,
                "source_row_offset": source.offset + start,
                "selected_records": stop - start,
                "selected_logical_bytes": (stop - start) * RECORD.size,
                "columnar_blocks": 0
                if start == stop
                else (stop - 1) // BLOCK_ROWS - start // BLOCK_ROWS + 1,
                "expected_sha256": checksum(RECORD.pack(*rows[i]) for i in range(start, stop)),
            }
        )
    index_hash = hashlib.sha256(
        b"".join(t.to_bytes(8, "little", signed=True) for t in timestamps)
    ).hexdigest()
    index_memory = sys.getsizeof(timestamps) + sum(sys.getsizeof(t) for t in timestamps)
    preparation_ns = time.perf_counter_ns() - query_started
    directory = output_directory(directory)
    sample_dir = Path(tempfile.mkdtemp(prefix="format-sample-", dir=directory))
    schema = pa.schema(
        [
            ("timestamp", pa.int64()),
            ("open", pa.float32()),
            ("high", pa.float32()),
            ("low", pa.float32()),
            ("close", pa.float32()),
        ]
    )
    table = pa.Table.from_arrays(
        [
            pa.array(column, type=field.type)
            for column, field in zip(zip(*rows, strict=True), schema, strict=True)
        ],
        schema=schema,
    )
    del rows
    arrow_path, parquet_path = sample_dir / "sample.arrow", sample_dir / "sample.parquet"
    with arrow_path.open("xb") as stream, ipc.new_file(stream, schema) as writer:
        writer.write_table(table, max_chunksize=BLOCK_ROWS)
    with parquet_path.open("xb") as stream:
        pq.write_table(
            table, stream, compression="zstd", use_dictionary=False, row_group_size=BLOCK_ROWS
        )
    conversion_ns = time.perf_counter_ns() - started
    sizes = {
        "raw_window": source.count * RECORD.size,
        "arrow": arrow_path.stat().st_size,
        "parquet": parquet_path.stat().st_size,
    }
    if sizes["arrow"] + sizes["parquet"] > MAX_DERIVED_BYTES:
        raise DomainError("derived sample size exceeds 128 MiB cap")
    del table
    derived_identities = {
        p: identity(p.stat(follow_symlinks=False)) for p in (arrow_path, parquet_path)
    }
    readers: dict[str, Callable[[], Iterator[bytes]]] = {
        "raw_sequential": lambda: records(source),
        "raw_mmap": lambda: records(source, "mmap"),
        "arrow": lambda: columnar_records(arrow_path, "arrow"),
        "parquet": lambda: columnar_records(parquet_path, "parquet"),
    }
    timings: dict[str, object] = {}
    for name, reader in readers.items():
        wall: list[int] = []
        cpu: list[int] = []
        io: list[dict[str, int]] = []
        for _ in range(repeats):
            usage_before = resource.getrusage(resource.RUSAGE_SELF)
            cpu_start, wall_start = time.process_time_ns(), time.perf_counter_ns()
            observed = checksum(reader())
            wall.append(time.perf_counter_ns() - wall_start)
            cpu.append(time.process_time_ns() - cpu_start)
            usage_after = resource.getrusage(resource.RUSAGE_SELF)
            io.append(
                {
                    "input_blocks": usage_after.ru_inblock - usage_before.ru_inblock,
                    "output_blocks": usage_after.ru_oublock - usage_before.ru_oublock,
                    "major_faults": usage_after.ru_majflt - usage_before.ru_majflt,
                }
            )
            if observed != manifest.window_sha256:
                raise DomainError("derived data failed byte-exact roundtrip")
        timings[name] = {
            "wall_ns": wall,
            "cpu_ns": cpu,
            "median_wall_ns": statistics.median(wall),
            "max_wall_ns": max(wall),
            "roundtrip_sha256": observed,
            "process_io_counters": io,
        }
    query_report: dict[str, object] = {}
    if queries is not None:
        query_report["queries"] = {
            "schema": "grid-storage-queries-v1",
            "plan": queries,
            "index": {
                "rows": len(timestamps),
                "timestamps_sha256": index_hash,
                "python_index_bytes": index_memory,
                "preparation_wall_ns": preparation_ns,
                "basis": (
                    "Shared in-memory UTC index from validated selected source; index and oracle "
                    "preparation excluded from query timing, included in conversion_wall_ns."
                ),
            },
            "windows": benchmark_queries(source, sample_dir, timestamps, prepared, repeats),
            "measurement": (
                "UTC lookup, open, selected read/decode/repack/SHA256 and close; empty windows "
                "open no file. Full validation/conversion/scans precede queries."
            ),
            "limits": {
                "max_queries": MAX_QUERIES,
                "max_selected_records_per_repeat": MAX_QUERY_RECORDS,
                "block_rows": BLOCK_ROWS,
            },
            "cache": (
                "Uncontrolled warmed OS cache; first measured query is NOT a cold-cache result. "
                "No persistent reader or decoded-row cache."
            ),
            "sampling": (
                "Seeded SHA256 modulo minute offsets; overlap and duplicate windows retained. "
                "Not calendar months or multi-symbol sampling."
            ),
            "memory_io": (
                "Bounded timestamp index and one columnar block; logical selected bytes are not "
                "physical device IO. RSS is process lifetime high-water only."
            ),
        }
    if any(
        identity(p.stat(follow_symlinks=False)) != before
        for p, before in derived_identities.items()
    ):
        raise DomainError("derived sample changed during benchmark")
    if identity(source.path.stat(follow_symlinks=False)) != manifest.file_identity:
        raise DomainError("source changed since benchmark validation")
    cpu_model = next(
        (
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        ),
        "unknown",
    )
    memory = next(
        (
            line
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemTotal:")
        ),
        "unknown",
    )
    return {
        "schema": "grid-storage-sample-v1",
        **query_report,
        "input": manifest,
        "sample_directory": sample_dir,
        "sizes_bytes": sizes,
        "conversion_wall_ns": conversion_ns,
        "timings": timings,
        "python": platform.python_version(),
        "ccxt": version("ccxt"),
        "sqlite_linked": sqlite3.sqlite_version,
        "pyarrow": pa.__version__,
        "cpu_model": cpu_model,
        "host_memory": memory,
        "machine": platform.machine(),
        "kernel": platform.release(),
        "source_filesystem": filesystem(source.path.resolve()),
        "output_filesystem": filesystem(sample_dir),
        "process_peak_rss_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "arrow_peak_pool_bytes": pa.default_memory_pool().max_memory(),
        "cache": "Cache uncontrolled; validation/conversion warmed input. Cold cache NOT TESTED.",
        "measurement": "Read/decode/repack/SHA256; fixed reader order, not replay timing.",
        "concurrency": "One sequential reader; Parquet parallel column decoding disabled.",
        "memory": "Lifetime high-water RSS includes imports/conversion; not per-format memory.",
        "source_device": device_evidence(source.path),
        "output_device": device_evidence(sample_dir),
        "decision": "Retain raw inputs; this warm sample does not justify archive conversion.",
    }
