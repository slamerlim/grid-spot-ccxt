"""Bounded format comparison, not a deployment/NVMe or cold-cache certification."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import resource
import sqlite3
import statistics
import tempfile
import time
from collections.abc import Callable, Iterator
from importlib.metadata import version
from pathlib import Path
from typing import Any

from grid.domain.numbers import DomainError, bounded_int
from grid.persistence.storage import filesystem
from grid.research.artifacts import output_directory
from grid.research.history import RECORD, Source, identity, records, validate

MAX_DERIVED_BYTES = 128 * 1024 * 1024
MAX_BENCH_RECORDS = 131072


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
            batches = pq.ParquetFile(stream).iter_batches(batch_size=4096)
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


def benchmark(source: Source, directory: Path, *, repeats: int = 3) -> dict[str, object]:
    bounded_int(repeats, 1, 10)
    bounded_int(source.count, 1, MAX_BENCH_RECORDS)
    manifest = validate(source)
    if manifest.rejected or "malformed_file_length" in manifest.blockers:
        raise DomainError("benchmark needs structurally valid finite OHLC records")
    # Storage comparison may include gaps/unverified collection identity; no trading occurs.
    pa, ipc, pq = _arrow()
    directory = output_directory(directory)
    sample_dir = Path(tempfile.mkdtemp(prefix="format-sample-", dir=directory))
    started = time.perf_counter_ns()
    rows = [RECORD.unpack(raw) for raw in records(source)]
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
        writer.write_table(table, max_chunksize=4096)
    with parquet_path.open("xb") as stream:
        pq.write_table(table, stream, compression="zstd", use_dictionary=False, row_group_size=4096)
    conversion_ns = time.perf_counter_ns() - started
    sizes = {
        "raw_window": source.count * RECORD.size,
        "arrow": arrow_path.stat().st_size,
        "parquet": parquet_path.stat().st_size,
    }
    if sizes["arrow"] + sizes["parquet"] > MAX_DERIVED_BYTES:
        raise DomainError("derived sample size exceeds 128 MiB cap")
    del table
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
        "memory": "Lifetime high-water RSS includes imports/conversion; not per-format memory.",
        "source_device": device_evidence(source.path),
        "output_device": device_evidence(sample_dir),
        "decision": "Retain raw inputs; this warm sample does not justify archive conversion.",
    }
