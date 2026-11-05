"""UTC windows retain exact source bytes, gaps and bounds across indexed formats."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from grid.domain.numbers import DomainError
from grid.research import benchmark as storage
from grid.research.benchmark import BLOCK_ROWS, QueryPlan, benchmark, query_columnar
from grid.research.history import MINUTE, RECORD, Manifest, Source

START = 1704067200000


def source_at(path: Path, count: int, *, offset: int = 0, gap: bool = False) -> Source:
    with path.open("xb") as stream:
        for index in range(count + offset + 2):
            timestamp = START + (index + (100 if gap and index >= offset + 2 else 0)) * MINUTE
            price = 10 + index % 17 * 0.123456
            stream.write(RECORD.pack(timestamp, price, price + 1, price - 1, price + 0.1))
    return Source(path, "synthetic", "TEST/USD", offset, count)


def window_bytes(source: Source, start: int, end: int) -> bytes:
    # Independent full selected-window filter, not the production bisect/index path.
    all_bytes = source.path.read_bytes()
    rows = [
        all_bytes[i * RECORD.size : (i + 1) * RECORD.size]
        for i in range(source.offset, source.offset + source.count)
    ]
    return b"".join(row for row in rows if start <= RECORD.unpack(row)[0] < end)


def test_queries_cross_blocks_with_exact_endpoints_offsets_and_repeat_order(tmp_path: Path) -> None:
    source = source_at(tmp_path / "source.bin", BLOCK_ROWS * 2 + 3, offset=7)
    before = source.path.read_bytes(), source.path.stat().st_mtime_ns
    start, end = START + (7 + BLOCK_ROWS - 1) * MINUTE, START + (7 + BLOCK_ROWS + 1) * MINUTE
    plan = QueryPlan(start, end, random_windows=4, window_minutes=3, seed=73)
    result = benchmark(source, tmp_path / "results", repeats=3, queries=plan)
    query: Any = result["queries"]
    assert query["schema"] == "grid-storage-queries-v1"
    assert query["index"]["rows"] == source.count
    assert query["index"]["python_index_bytes"] > source.count * 8
    assert query["index"]["preparation_wall_ns"] > 0
    timestamps = b"".join(
        (START + i * MINUTE).to_bytes(8, "little", signed=True) for i in range(7, 7 + source.count)
    )
    assert query["index"]["timestamps_sha256"] == hashlib.sha256(timestamps).hexdigest()
    first = query["windows"][0]
    assert first["source_row_offset"] == 7 + BLOCK_ROWS - 1
    assert first["selected_records"] == first["columnar_blocks"] == 2
    assert first["reader_order"] == [
        ("raw_mmap", "arrow", "parquet"),
        ("arrow", "parquet", "raw_mmap"),
        ("parquet", "raw_mmap", "arrow"),
    ]
    for item in query["windows"]:
        expected = window_bytes(source, item["start_ms"], item["end_ms_exclusive"])
        assert item["selected_logical_bytes"] == len(expected)
        assert item["expected_sha256"] == hashlib.sha256(expected).hexdigest()
        assert set(item["timings"]) == {"raw_mmap", "arrow", "parquet"}
        for values in item["timings"].values():
            assert len(values) == 3
            assert all(v["roundtrip_sha256"] == item["expected_sha256"] for v in values)
            assert all(v["wall_ns"] > 0 and v["cpu_ns"] > 0 for v in values)
            assert all(
                set(v["process_io_counters"]) == {"input_blocks", "output_blocks", "major_faults"}
                for v in values
            )
    assert source.path.read_bytes() == before[0] and source.path.stat().st_mtime_ns == before[1]
    manifest = result["input"]
    assert isinstance(manifest, Manifest)
    assert "unknown_identity_provenance" in manifest.blockers
    assert "NOT a cold-cache" in query["cache"]
    assert "Not calendar months" in query["sampling"]


def test_empty_gap_and_full_span_duplicate_queries_are_retained(tmp_path: Path) -> None:
    source = source_at(tmp_path / "source.bin", 4, gap=True)
    result: Any = benchmark(
        source,
        tmp_path / "out",
        repeats=1,
        queries=QueryPlan(START + 2 * MINUTE, START + 102 * MINUTE),
    )
    window = result["queries"]["windows"][0]
    assert (
        window["selected_records"]
        == window["selected_logical_bytes"]
        == window["columnar_blocks"]
        == 0
    )
    assert window["expected_sha256"] == hashlib.sha256(b"").hexdigest()
    assert result["input"].missing_minutes == 100
    assert "missing_minutes" in result["input"].blockers
    assert all(
        v[0]["roundtrip_sha256"] == window["expected_sha256"] for v in window["timings"].values()
    )
    full: Any = benchmark(
        source,
        tmp_path / "full",
        repeats=1,
        queries=QueryPlan(random_windows=3, window_minutes=104, seed=0),
    )
    windows = full["queries"]["windows"]
    assert len(windows) == 3 and all(
        w["start_ms"] == START and w["selected_records"] == 4 for w in windows
    )
    assert len({w["expected_sha256"] for w in windows}) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"start_ms": START},
        {"end_ms": START + MINUTE},
        {"start_ms": START, "end_ms": START},
        {"start_ms": START + 1, "end_ms": START + MINUTE},
        {"random_windows": True, "window_minutes": 1, "seed": 0},
        {"random_windows": 33, "window_minutes": 1, "seed": 0},
        {"random_windows": 1},
        {"random_windows": 1, "window_minutes": 1},
        {"random_windows": 1, "window_minutes": 0, "seed": 0},
        {"random_windows": 1, "window_minutes": 1, "seed": -1},
        {"random_windows": 1, "window_minutes": 1, "seed": 2**64},
        {"random_windows": 1, "window_minutes": 1, "seed": False},
        {"start_ms": START, "end_ms": START + MINUTE, "seed": 0},
        {
            "start_ms": START,
            "end_ms": START + MINUTE,
            "random_windows": 32,
            "window_minutes": 1,
            "seed": 0,
        },
    ],
)
def test_query_plan_rejects_ambiguous_or_unbounded_requests(kwargs: dict[str, Any]) -> None:
    with pytest.raises(DomainError):
        QueryPlan(**kwargs)


@pytest.mark.parametrize("kind", ["outside", "duration", "records"])
def test_workload_bounds_precede_derived_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    source = source_at(tmp_path / "source.bin", 3)
    plan = (
        QueryPlan(START, START + 4 * MINUTE)
        if kind == "outside"
        else QueryPlan(random_windows=2, window_minutes=4 if kind == "duration" else 3, seed=0)
    )
    if kind == "records":
        monkeypatch.setattr(storage, "MAX_QUERY_RECORDS", 5)
    with pytest.raises(DomainError):
        benchmark(source, tmp_path / "out", queries=plan)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("kind", ["arrow", "parquet"])
def test_selected_blocks_only_and_last_partial_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    source = source_at(tmp_path / "source.bin", BLOCK_ROWS * 2 + 3, offset=5)
    report = benchmark(source, tmp_path / "out", repeats=1)
    pa, ipc, pq = storage._arrow()
    accesses: list[int] = []

    class TrackedReader:
        def __init__(self, reader: Any) -> None:
            self.reader = reader

        def __getattr__(self, name: str) -> Any:
            return getattr(self.reader, name)

        def get_batch(self, i: int) -> Any:
            accesses.append(i)
            return self.reader.get_batch(i)

        def read_row_group(self, i: int, *, use_threads: bool) -> Any:
            assert not use_threads
            accesses.append(i)
            return self.reader.read_row_group(i, use_threads=use_threads)

    class IPC:
        @staticmethod
        def open_file(stream: Any) -> TrackedReader:
            return TrackedReader(ipc.open_file(stream))

    class PQ:
        @staticmethod
        def ParquetFile(stream: Any) -> TrackedReader:
            return TrackedReader(pq.ParquetFile(stream))

    monkeypatch.setattr(storage, "_arrow", lambda: (pa, IPC, PQ))
    path = Path(str(report["sample_directory"])) / f"sample.{kind}"
    for start, stop, blocks in [
        (BLOCK_ROWS, BLOCK_ROWS + 1, [1]),
        (BLOCK_ROWS * 2, source.count, [2]),
        (BLOCK_ROWS - 1, BLOCK_ROWS + 1, [0, 1]),
        (0, 0, []),
    ]:
        accesses.clear()
        recovered = b"".join(query_columnar(path, kind, start, stop, source.count))
        expected = source.path.read_bytes()[
            (source.offset + start) * RECORD.size : (source.offset + stop) * RECORD.size
        ]
        assert recovered == expected and accesses == blocks


@pytest.mark.parametrize("target", ["source", "arrow", "parquet"])
def test_changed_input_or_derived_identity_refuses_even_identical_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    source = source_at(tmp_path / "source.bin", 4)
    original = storage.benchmark_queries

    def changed(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        path = source.path if target == "source" else args[1] / f"sample.{target}"
        path.write_bytes(path.read_bytes())
        return result

    monkeypatch.setattr(storage, "benchmark_queries", changed)
    with pytest.raises(DomainError, match="changed"):
        benchmark(source, tmp_path / "out", repeats=1, queries=QueryPlan(START, START + MINUTE))


def test_query_corruption_refuses_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grid.research.__main__ import main

    source = source_at(tmp_path / "source.bin", 4)
    monkeypatch.setattr(
        storage, "query_columnar", lambda *args: iter((RECORD.pack(START, 1, 1, 1, 1),))
    )
    output = tmp_path / "out" / "report.json"
    assert (
        main(
            [
                "benchmark",
                str(source.path),
                "--venue",
                "synthetic",
                "--symbol",
                "TEST/USD",
                "--count",
                "4",
                "--code-revision",
                "test",
                "--output",
                str(output),
                "--utc-start-ms",
                str(START),
                "--utc-end-ms",
                str(START + MINUTE),
            ]
        )
        == 2
    )
    assert not output.exists()
    assert len(list(output.parent.glob("format-sample-*"))) == 1  # Failed artifacts retained.


def test_cli_random_schedule_reproducible_and_opt_in(tmp_path: Path) -> None:
    source = source_at(tmp_path / "source.bin", 10)
    schedules: list[object] = []
    for index, extras in enumerate(
        [
            [],
            ["--random-windows", "4", "--window-minutes", "2", "--query-seed", "42"],
            ["--random-windows", "4", "--window-minutes", "2", "--query-seed", "42"],
        ]
    ):
        output = tmp_path / f"report-{index}.json"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "grid",
                "bench-storage",
                str(source.path),
                "--venue",
                "synthetic",
                "--symbol",
                "TEST/USD",
                "--count",
                "10",
                "--repeats",
                "1",
                "--code-revision",
                "test",
                "--output",
                str(output),
                *extras,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(output.read_bytes())
        assert report["schema"] == "grid-research-run-v1"
        body = report["result"]
        if index == 0:
            assert "queries" not in body
        else:
            schedules.append(
                [
                    (
                        w["start_ms"],
                        w["end_ms_exclusive"],
                        w["selected_records"],
                        w["expected_sha256"],
                    )
                    for w in body["queries"]["windows"]
                ]
            )
    assert schedules[0] == schedules[1]


@pytest.mark.parametrize("kind", ["arrow", "parquet"])
@pytest.mark.parametrize("fault", ["schema", "rows", "null"])
def test_derived_query_layout_and_nulls_refused(tmp_path: Path, kind: str, fault: str) -> None:
    pa, ipc, pq = storage._arrow()
    schema = pa.schema(
        [
            ("timestamp", pa.int64()),
            *[
                (name, pa.float64() if fault == "schema" else pa.float32())
                for name in ("open", "high", "low", "close")
            ],
        ]
    )
    count = 2 if fault == "rows" else 1
    table = pa.Table.from_arrays(
        [
            pa.array([START] * count, type=pa.int64()),
            *[
                pa.array([None if fault == "null" else 1] * count, type=field.type)
                for field in list(schema)[1:]
            ],
        ],
        schema=schema,
    )
    path = tmp_path / f"sample.{kind}"
    with path.open("xb") as stream:
        if kind == "arrow":
            with ipc.new_file(stream, schema) as writer:
                writer.write_table(table)
        else:
            pq.write_table(table, stream)
    with pytest.raises(DomainError, match="derived query"):
        list(query_columnar(path, kind, 0, 1, 1))
