"""Independent sorted oracle, bounded readers and fail-closed format comparisons."""

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from grid.domain.numbers import DomainError
from grid.research import merge_benchmark as storage
from grid.research.__main__ import main
from grid.research.benchmark import MAX_BENCH_RECORDS, query_columnar
from grid.research.config import source_object
from grid.research.history import MINUTE, RECORD, Source, validate
from grid.research.merge_benchmark import BLOCK_ROWS, Merged, benchmark, blocked_rows, load, merge

START = 1704067200000


def source_at(path: Path, symbol: str, count: int, *, offset: int = 0, shift: int = 0) -> Source:
    with path.open("xb") as stream:
        for index in range(count + offset + 1):
            timestamp = START + (index * 2 + shift) * MINUTE  # Deliberately retain gaps.
            price = 10 + (index % 41) * 0.123456
            stream.write(RECORD.pack(timestamp, price, price + 1, price - 1, price + 0.1))
    return Source(path, "synthetic", symbol, offset, count)


def plan_at(path: Path, sources: tuple[Source, ...]) -> Path:
    path.write_text(
        json.dumps(
            {"schema": "grid-storage-merge-plan-v1", "sources": [source_object(s) for s in sources]}
        )
    )
    return path


def oracle(sources: tuple[Source, ...]) -> Merged:
    # Intentionally materialize/sort small test data; do not share heap/block production logic.
    entries = []
    for index, source in enumerate(sorted(sources, key=lambda s: (s.venue, s.symbol))):
        data = source.path.read_bytes()
        for row in range(source.offset, source.offset + source.count):
            raw = data[row * RECORD.size : (row + 1) * RECORD.size]
            entries.append((RECORD.unpack(raw)[0], index, raw))
    entries.sort()
    data = b"".join(i.to_bytes(4, "little") + row for _, i, row in entries)
    return Merged(len(entries), entries[0][0], entries[-1][0], hashlib.sha256(data).hexdigest())


def test_exact_merge_across_blocks_offsets_gaps_ties_and_input_order(tmp_path: Path) -> None:
    sources = (
        source_at(tmp_path / "z.bin", "Z/USD", BLOCK_ROWS + 7, offset=5),
        source_at(tmp_path / "a.bin", "A/USD", BLOCK_ROWS * 2 + 1, shift=1),
        source_at(tmp_path / "b.bin", "B/USD", BLOCK_ROWS, offset=5),
    )
    before = [(s.path.read_bytes(), s.path.stat().st_mtime_ns) for s in sources]
    expected = oracle(sources)
    report: Any = benchmark(sources, tmp_path / "out", repeats=3)
    assert report["expected"] == expected
    assert report["reader_order"] == [
        ("raw_mmap", "arrow", "parquet"),
        ("arrow", "parquet", "raw_mmap"),
        ("parquet", "raw_mmap", "arrow"),
    ]
    assert report["sizes_bytes"]["raw_mmap"] == expected.records * RECORD.size
    assert [m.source.symbol for m in report["inputs"]] == ["A/USD", "B/USD", "Z/USD"]
    assert all(m.missing_minutes > 0 and not m.replay_eligible for m in report["inputs"])
    for kind, measurements in report["timings"].items():
        assert len(measurements) == 3
        for measurement in measurements:
            assert measurement["merged"] == expected
            assert measurement["wall_ns"] > 0 and measurement["cpu_ns"] > 0
            assert set(measurement["process_io_counters"]) == {
                "input_blocks",
                "output_blocks",
                "major_faults",
            }
        assert report["summary"][kind]["max_wall_ns"] == max(m["wall_ns"] for m in measurements)
    again: Any = benchmark(tuple(reversed(sources)), tmp_path / "again", repeats=3)
    for key in ("config_sha256", "inputs_sha256", "expected"):
        assert again[key] == report[key]
    assert [(s.path.read_bytes(), s.path.stat().st_mtime_ns) for s in sources] == before
    assert "NOT TESTED" in report["cache"] and "not RSS" in report["memory"]


@pytest.mark.parametrize("kind", storage.FORMATS)
def test_no_file_remains_open_between_block_yields(tmp_path: Path, kind: str) -> None:
    source = source_at(tmp_path / "raw.bin", "A/USD", BLOCK_ROWS + 1)
    manifest = validate(source)
    storage.convert(manifest, tmp_path, 0)
    before = len(os.listdir("/proc/self/fd"))
    reader = blocked_rows(manifest, tmp_path, 0, kind)
    for _ in reader:
        assert len(os.listdir("/proc/self/fd")) == before
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("fault", ["empty", "count", "total", "duplicate", "repeats", "many"])
def test_work_caps_before_source_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    source = Source(tmp_path / "missing.bin", "synthetic", "A/USD", 0, 1)
    sources: tuple[Source, ...] = (source,)
    repeats = 1
    if fault == "empty":
        sources = ()
    elif fault == "count":
        sources = (replace(source, count=MAX_BENCH_RECORDS + 1),)
    elif fault == "total":
        monkeypatch.setattr(storage, "MAX_MERGE_RECORDS", 1)
        sources = (replace(source, count=2),)
    elif fault == "duplicate":
        sources = (source, source)
    elif fault == "many":
        sources = tuple(replace(source, symbol=f"S{i}/USD") for i in range(1001))
    else:
        repeats = True
    with pytest.raises(DomainError):
        benchmark(sources, tmp_path / "out", repeats=repeats)
    assert not (tmp_path / "out").exists()


def test_hardlink_alias_cannot_inflate_source_count(tmp_path: Path) -> None:
    source = source_at(tmp_path / "raw.bin", "A/USD", 2)
    alias = tmp_path / "alias.bin"
    os.link(source.path, alias)
    with pytest.raises(DomainError, match="aliases"):
        benchmark((source, replace(source, path=alias, symbol="B/USD")), tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("fault", ["duplicate", "backward", "nan", "length"])
def test_bad_selected_data_refuses_before_conversion(tmp_path: Path, fault: str) -> None:
    source = source_at(tmp_path / "raw.bin", "A/USD", 3)
    raw = source.path.read_bytes()
    if fault == "length":
        raw += b"x"
    else:
        second = RECORD.pack(START if fault == "duplicate" else START - MINUTE, 1, 1, 1, 1)
        if fault == "nan":
            second = RECORD.pack(START + MINUTE, float("nan"), 1, 1, 1)
        raw = raw[: RECORD.size] + second + raw[RECORD.size * 2 :]
    source.path.write_bytes(raw)
    with pytest.raises(DomainError, match="valid ordered"):
        benchmark((source,), tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("target", ["source", "arrow", "parquet"])
def test_mutation_after_last_read_refuses_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    source = source_at(tmp_path / "raw.bin", "A/USD", 4)
    original = storage.merge

    def changed(manifests: Any, directory: Path, kind: str) -> Merged:
        result = original(manifests, directory, kind)
        if kind == "parquet":
            path = source.path if target == "source" else directory / f"0000.{target}"
            before = path.stat()
            path.write_bytes(path.read_bytes())  # Same bytes are still a new source identity.
            # Some mounts coalesce sub-tick writes; make the metadata change deterministic.
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        return result

    monkeypatch.setattr(storage, "merge", changed)
    with pytest.raises(DomainError, match="changed"):
        benchmark((source,), tmp_path / "out", repeats=1)


@pytest.mark.parametrize("fault", ["short", "price", "reverse"])
def test_corrupt_derived_blocks_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    source = source_at(tmp_path / "raw.bin", "A/USD", 4)
    original = query_columnar

    def corrupt(*args: Any, **kwargs: Any) -> Iterator[bytes]:
        rows = list(original(*args, **kwargs))
        if args[1] == "parquet":
            if fault == "short":
                rows.pop()
            elif fault == "reverse":
                rows.reverse()
            else:
                rows[0] = RECORD.pack(RECORD.unpack(rows[0])[0], 1, 1, 1, 1)
        return iter(rows)

    monkeypatch.setattr(storage, "query_columnar", corrupt)
    with pytest.raises(DomainError):
        benchmark((source,), tmp_path / "out", repeats=1)
    assert len(list((tmp_path / "out").glob("merge-sample-*"))) == 1


def test_per_source_hash_catches_same_order_changed_price(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = source_at(tmp_path / "raw.bin", "A/USD", 4)
    manifest = validate(source)
    storage.convert(manifest, tmp_path, 0)
    original = query_columnar

    def corrupt(*args: Any, **kwargs: Any) -> Iterator[bytes]:
        for raw in original(*args, **kwargs):
            yield RECORD.pack(RECORD.unpack(raw)[0], 1, 1, 1, 1)

    monkeypatch.setattr(storage, "query_columnar", corrupt)
    with pytest.raises(DomainError, match="roundtrip checksum"):
        merge((manifest,), tmp_path, "arrow")


def test_combined_merge_identity_must_match_raw_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = source_at(tmp_path / "raw.bin", "A/USD", 4)
    original = storage.merge

    def wrong(manifests: Any, directory: Path, kind: str) -> Merged:
        result = original(manifests, directory, kind)
        return replace(result, sha256="0" * 64) if kind == "arrow" else result

    monkeypatch.setattr(storage, "merge", wrong)
    with pytest.raises(DomainError, match="ordered record bytes"):
        benchmark((source,), tmp_path / "out", repeats=1)


def test_conversion_mutation_cannot_replace_validated_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = source_at(tmp_path / "raw.bin", "A/USD", 4)
    original = storage.convert

    def changed(*args: Any, **kwargs: Any) -> tuple[Path, Path]:
        source.path.write_bytes(RECORD.pack(START, 1, 1, 1, 1) + source.path.read_bytes()[24:])
        return original(*args, **kwargs)

    monkeypatch.setattr(storage, "convert", changed)
    with pytest.raises(DomainError, match="source checksum"):
        benchmark((source,), tmp_path / "out", repeats=1)


@pytest.mark.parametrize("cap", ["MAX_DERIVED_BYTES", "MAX_REPORT_BYTES"])
def test_output_caps_refuse_and_retain_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cap: str
) -> None:
    source = source_at(tmp_path / "raw.bin", "A/USD", 2)
    monkeypatch.setattr(storage, cap, 1)
    with pytest.raises(DomainError, match="cap"):
        benchmark((source,), tmp_path / "out", repeats=1)
    assert len(list((tmp_path / "out").glob("merge-sample-*"))) == 1


@pytest.mark.parametrize("fault", ["extra", "duplicate", "schema", "type", "bool", "oversize"])
def test_strict_plan(tmp_path: Path, fault: str) -> None:
    source = Source(tmp_path / "missing.bin", "synthetic", "A/USD", 0, 1)
    path = plan_at(tmp_path / "plan.json", (source,))
    assert load(path) == (source,)
    data = json.loads(path.read_bytes())
    if fault == "extra":
        data["sources"][0]["extra"] = 1
    elif fault == "schema":
        data["schema"] = "unknown"
    elif fault == "type":
        data["sources"] = {}
    elif fault == "bool":
        data["sources"][0]["count"] = True
    raw = json.dumps(data)
    if fault == "duplicate":
        raw = raw.replace('"offset": 0', '"offset": 0, "offset": 0')
    elif fault == "oversize":
        raw = " " * (4 * 1024 * 1024 + 1)
    path.write_text(raw)
    with pytest.raises(DomainError):
        load(path)


@pytest.mark.parametrize(
    "extra",
    [
        ["ignored.bin"],
        ["--offset", "0"],
        ["--utc-start-ms", str(START)],
        ["--verified-unleveraged-spot"],
    ],
)
def test_cli_rejects_mixed_single_and_merge_mode(tmp_path: Path, extra: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(
            [
                "benchmark",
                "--merge-plan",
                "missing.json",
                "--output",
                str(tmp_path / "result.json"),
                "--code-revision",
                "test",
                *extra,
            ]
        )
    assert error.value.code == 2
    assert not (tmp_path / "result.json").exists()


def test_failed_cli_never_publishes_or_exposes_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = source_at(tmp_path / "raw.bin", "A/USD", 2)
    plan = plan_at(tmp_path / "plan.json", (source,))
    output = tmp_path / "result.json"

    def fail(*args: Any, **kwargs: Any) -> Merged:
        raise DomainError("secret-marker")

    monkeypatch.setattr(storage, "merge", fail)
    args = [
        "benchmark",
        "--merge-plan",
        str(plan),
        "--output",
        str(output),
        "--code-revision",
        "test",
    ]
    assert main(args) == 2
    assert not output.exists() and "secret-marker" not in capsys.readouterr().err
    output.write_text("retained")
    assert main(args) == 2 and output.read_text() == "retained"
    assert len(list(tmp_path.glob("merge-sample-*"))) == 1


def test_thousand_sources_cli_under_64_descriptor_limit(tmp_path: Path) -> None:
    sources = tuple(
        source_at(tmp_path / f"{i}.bin", f"S{i:04d}/USD", 2, shift=i % 7) for i in range(1000)
    )
    plan = plan_at(tmp_path / "plan.json", sources)
    output = tmp_path / "result.json"
    # Child-only limit; no host/cgroup/parent mutation. The full CLI exercises conversion too.
    script = (
        "import resource,sys; from grid.cli import main; "
        "soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE); "
        "limit=64 if soft==resource.RLIM_INFINITY else min(64,soft); "
        "resource.setrlimit(resource.RLIMIT_NOFILE,(limit,hard)); "
        "sys.exit(main(sys.argv[1:]))"
    )
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            "bench-storage",
            "--merge-plan",
            str(plan),
            "--output",
            str(output),
            "--code-revision",
            "test",
            "--repeats",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(output.read_bytes())
    assert report["schema"] == "grid-research-run-v1"
    result = report["result"]
    assert result["source_count"] == 1000 and result["expected"]["records"] == 2000
    assert result["expected"]["sha256"] == oracle(sources).sha256
    assert result["limits"]["buffer_record_bytes_bound"] == 6_144_000
    assert report["provenance"]["source"]["python_sha256"]
