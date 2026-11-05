"""Bounded chronological format comparison; no trading or persistent reader cache."""

from __future__ import annotations

import hashlib
import heapq
import json
import resource
import statistics
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

from grid.domain.numbers import DomainError, bounded_int
from grid.persistence.storage import filesystem
from grid.research.artifacts import canonical, digest, output_directory
from grid.research.benchmark import (
    MAX_BENCH_RECORDS,
    MAX_DERIVED_BYTES,
    _arrow,
    device_evidence,
    query_columnar,
)
from grid.research.config import _fields, _pairs
from grid.research.history import RECORD, Manifest, Source, identity, records, validate
from grid.research.provenance import host, read_file, runtime

MAX_SOURCES = 1000
MAX_MERGE_RECORDS = 1_048_576
BLOCK_ROWS = 256
MAX_REPORT_BYTES = 16 * 1024 * 1024
FORMATS = ("raw_mmap", "arrow", "parquet")


def checked(sources: tuple[Source, ...]) -> tuple[Source, ...]:
    bounded_int(len(sources), 1, MAX_SOURCES)
    if len({(s.venue, s.symbol) for s in sources}) != len(sources):
        raise DomainError("duplicate merge market")
    for source in sources:
        bounded_int(source.count, 1, MAX_BENCH_RECORDS)
    if sum(s.count for s in sources) > MAX_MERGE_RECORDS:
        raise DomainError("merge record workload exceeds cap")
    return tuple(sorted(sources, key=lambda s: (s.venue, s.symbol)))


def load(path: Path) -> tuple[Source, ...]:
    raw, _ = read_file(path, 4 * 1024 * 1024)
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
        _fields(value, "schema sources")
        if value["schema"] != "grid-storage-merge-plan-v1":
            raise DomainError("unknown merge plan schema")
        if not isinstance(value["sources"], list):
            raise DomainError("source array required")
        bounded_int(len(value["sources"]), 1, MAX_SOURCES)
        sources: list[Source] = []
        for item in value["sources"]:
            _fields(
                item,
                "path venue symbol offset count format_evidence identity_evidence "
                "collection_evidence unleveraged_spot",
            )
            sources.append(Source(**{**item, "path": Path(item["path"])}))
        return checked(tuple(sources))
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise DomainError("invalid storage merge plan") from exc


def unchanged(manifests: tuple[Manifest, ...], derived: dict[Path, tuple[int, ...]]) -> None:
    for manifest in manifests:
        if identity(manifest.source.path.stat(follow_symlinks=False)) != manifest.file_identity:
            raise DomainError("merge source changed since validation")
    if any(
        identity(path.stat(follow_symlinks=False)) != before for path, before in derived.items()
    ):
        raise DomainError("merge derived data changed")


def convert(manifest: Manifest, directory: Path, index: int) -> tuple[Path, Path]:
    """One source at a time, one small table per batch; never materialize the archive."""
    pa, ipc, pq = _arrow()
    schema = pa.schema(
        [("timestamp", pa.int64()), *[(n, pa.float32()) for n in ("open", "high", "low", "close")]]
    )
    arrow, parquet = (directory / f"{index:04d}.{kind}" for kind in ("arrow", "parquet"))
    source = manifest.source
    hasher = hashlib.sha256()
    with (
        arrow.open("xb") as a_stream,
        parquet.open("xb") as p_stream,
        ipc.new_file(a_stream, schema) as a_writer,
        pq.ParquetWriter(p_stream, schema, compression="zstd", use_dictionary=False) as p_writer,
    ):
        for start in range(0, source.count, BLOCK_ROWS):
            window = replace(
                source, offset=source.offset + start, count=min(BLOCK_ROWS, source.count - start)
            )
            raw = b"".join(records(window, "mmap"))
            hasher.update(raw)
            rows = [RECORD.unpack_from(raw, i) for i in range(0, len(raw), RECORD.size)]
            table = pa.Table.from_arrays(
                [
                    pa.array(column, type=field.type)
                    for column, field in zip(zip(*rows, strict=True), schema, strict=True)
                ],
                schema=schema,
            )
            a_writer.write_table(table, max_chunksize=BLOCK_ROWS)
            p_writer.write_table(table, row_group_size=BLOCK_ROWS)
    if hasher.hexdigest() != manifest.window_sha256:
        raise DomainError("merge conversion source checksum differs")
    return arrow, parquet


def blocked_rows(manifest: Manifest, directory: Path, index: int, kind: str) -> Iterator[bytes]:
    """Read and close each block before yielding; held readers cannot exhaust file limits."""
    source = manifest.source
    hasher = hashlib.sha256()
    for start in range(0, source.count, BLOCK_ROWS):
        stop = min(start + BLOCK_ROWS, source.count)
        if kind == "raw_mmap":
            block = b"".join(
                records(replace(source, offset=source.offset + start, count=stop - start), "mmap")
            )
        else:
            block = b"".join(
                query_columnar(
                    directory / f"{index:04d}.{kind}",
                    kind,
                    start,
                    stop,
                    source.count,
                    block_rows=BLOCK_ROWS,
                )
            )
        if len(block) != (stop - start) * RECORD.size:
            raise DomainError("merge block record count differs")
        hasher.update(block)
        for position in range(0, len(block), RECORD.size):
            yield block[position : position + RECORD.size]
    if hasher.hexdigest() != manifest.window_sha256:
        raise DomainError("merge source roundtrip checksum differs")


@dataclass(frozen=True)
class Merged:
    records: int
    first_ms: int
    last_ms: int
    sha256: str


def merge(manifests: tuple[Manifest, ...], directory: Path, kind: str) -> Merged:
    if kind not in FORMATS:
        raise DomainError("unsupported merge format")
    readers = [blocked_rows(m, directory, i, kind) for i, m in enumerate(manifests)]
    heap: list[tuple[int, int, bytes]] = []
    hasher = hashlib.sha256()
    count = 0
    previous = (-1, -1)
    first = 0
    for index, reader in enumerate(readers):
        raw = next(reader)
        heapq.heappush(heap, (RECORD.unpack(raw)[0], index, raw))
    while heap:
        timestamp, index, raw = heapq.heappop(heap)
        if (timestamp, index) <= previous:
            raise DomainError("merge chronology or tie ordering differs")
        if count == 0:
            first = timestamp
        # Fixed-width roster ordinal distinguishes equal records from different markets.
        hasher.update(index.to_bytes(4, "little"))
        hasher.update(RECORD.pack(*RECORD.unpack(raw)))
        previous = timestamp, index
        count += 1
        following = next(readers[index], None)
        if following is not None:
            heapq.heappush(heap, (RECORD.unpack(following)[0], index, following))
    if count != sum(m.source.count for m in manifests):
        raise DomainError("merge total record count differs")
    return Merged(count, first, previous[0], hasher.hexdigest())


def benchmark(
    sources: tuple[Source, ...], directory: Path, *, repeats: int = 3
) -> dict[str, object]:
    started = time.perf_counter_ns()
    bounded_int(repeats, 1, 10)
    sources = checked(sources)
    file_ids = [identity(s.path.stat(follow_symlinks=False))[:2] for s in sources]
    if len(set(file_ids)) != len(sources):
        raise DomainError("merge sources must be distinct files, not aliases")
    manifests = tuple(validate(s) for s in sources)
    if [m.file_identity[:2] for m in manifests] != file_ids:
        raise DomainError("merge source changed during preflight")
    if any(m.rejected or "malformed_file_length" in m.blockers for m in manifests):
        raise DomainError("merge requires valid ordered finite OHLC records")
    unchanged(manifests, {})
    validation_ns = time.perf_counter_ns() - started
    sample = Path(tempfile.mkdtemp(prefix="merge-sample-", dir=output_directory(directory)))
    conversion_start = time.perf_counter_ns()
    derived: dict[Path, tuple[int, ...]] = {}
    sizes = {name: 0 for name in FORMATS}
    sizes["raw_mmap"] = sum(s.count for s in sources) * RECORD.size
    for index, manifest in enumerate(manifests):
        for path in convert(manifest, sample, index):
            info = path.stat(follow_symlinks=False)
            sizes[path.suffix[1:]] += info.st_size
            derived[path] = identity(info)
        if sizes["arrow"] + sizes["parquet"] > MAX_DERIVED_BYTES:
            raise DomainError("merge derived size exceeds cap")
    conversion_ns = time.perf_counter_ns() - conversion_start
    unchanged(manifests, derived)
    oracle_start = time.perf_counter_ns()
    expected = merge(manifests, sample, "raw_mmap")
    oracle_ns = time.perf_counter_ns() - oracle_start
    timings: dict[str, list[dict[str, object]]] = {name: [] for name in FORMATS}
    walls: dict[str, list[int]] = {name: [] for name in FORMATS}
    cpus: dict[str, list[int]] = {name: [] for name in FORMATS}
    orders = []
    for repeat in range(repeats):
        shift = repeat % len(FORMATS)
        order = FORMATS[shift:] + FORMATS[:shift]
        orders.append(order)
        for kind in order:
            unchanged(manifests, derived)
            before = resource.getrusage(resource.RUSAGE_SELF)
            cpu, wall = time.process_time_ns(), time.perf_counter_ns()
            observed = merge(manifests, sample, kind)
            wall, cpu = time.perf_counter_ns() - wall, time.process_time_ns() - cpu
            after = resource.getrusage(resource.RUSAGE_SELF)
            if observed != expected:
                raise DomainError("merge formats differ in ordered record bytes")
            walls[kind].append(wall)
            cpus[kind].append(cpu)
            timings[kind].append(
                {
                    "wall_ns": wall,
                    "cpu_ns": cpu,
                    "merged": observed,
                    "process_io_counters": {
                        "input_blocks": after.ru_inblock - before.ru_inblock,
                        "output_blocks": after.ru_oublock - before.ru_oublock,
                        "major_faults": after.ru_majflt - before.ru_majflt,
                    },
                }
            )
    unchanged(manifests, derived)
    report: dict[str, object] = {
        "schema": "grid-storage-merge-v1",
        "inputs": manifests,
        "inputs_sha256": digest(manifests),
        "config_sha256": digest((sources, repeats, BLOCK_ROWS, "grid-storage-merge-v1")),
        "source_count": len(sources),
        "expected": expected,
        "ordering": "UTC timestamp, then lexicographic (venue, symbol); gaps retained, no padding.",
        "hash_framing": "4-byte little-endian index in sorted input roster + 24 raw record bytes.",
        "sample_directory": sample,
        "sizes_bytes": sizes,
        "validation_wall_ns": validation_ns,
        "conversion_wall_ns": conversion_ns,
        "oracle_wall_ns": oracle_ns,
        "reader_order": orders,
        "timings": timings,
        "summary": {
            kind: {
                "median_wall_ns": statistics.median(walls[kind]),
                "max_wall_ns": max(walls[kind]),
                "median_cpu_ns": statistics.median(cpus[kind]),
                "max_cpu_ns": max(cpus[kind]),
            }
            for kind in FORMATS
        },
        "limits": {
            "max_sources": MAX_SOURCES,
            "max_records_total": MAX_MERGE_RECORDS,
            "max_records_per_source": MAX_BENCH_RECORDS,
            "block_rows": BLOCK_ROWS,
            "buffer_record_bytes_bound": len(sources) * BLOCK_ROWS * RECORD.size,
            "max_derived_bytes": MAX_DERIVED_BYTES,
            "max_report_bytes": MAX_REPORT_BYTES,
        },
        "process_peak_rss_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "runtime": runtime(),
        "host": host(),
        "source_storage": tuple(
            {"filesystem": filesystem(s.path.resolve()), "device": device_evidence(s.path)}
            for s in sources
        ),
        "output_storage": {"filesystem": filesystem(sample), "device": device_evidence(sample)},
        "measurement": (
            "Open/block read/decode/repack/merge/hash/close, including per-source hashes; "
            "preparation and identity checks outside each timing."
        ),
        "cache": (
            "Uncontrolled warmed OS cache after validation/conversion/raw oracle. "
            "Cold cache NOT TESTED."
        ),
        "memory": (
            "At most one packed block per source plus heap/metadata and transient decoder/copy "
            "buffers. Record buffer bound is not RSS/address-space quota; "
            "RSS is process lifetime high-water."
        ),
        "concurrency": (
            "Single thread of control; Parquet parallel decoding disabled. Each read closes "
            "before yielding a block; no open reader per symbol."
        ),
        "decision": (
            "Keep original raw data. Synthetic warm merges do not justify archive conversion "
            "or establish replay/deployment performance."
        ),
        "end_to_end_wall_ns": time.perf_counter_ns() - started,
    }
    if len(canonical(report)) > MAX_REPORT_BYTES:
        raise DomainError("merge report exceeds byte cap")
    unchanged(manifests, derived)
    return report
