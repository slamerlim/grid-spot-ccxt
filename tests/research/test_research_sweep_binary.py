from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
import tracemalloc
from contextlib import closing
from pathlib import Path
from typing import Any

import msgpack  # type: ignore[import-untyped]
import pytest

from scripts import research_sweep as runner
from scripts import research_sweep_matrix as matrix
from scripts.research_sweep_binary import ENGINE_MAGIC, FRAME_HEADER, engine_results
from scripts.research_sweep_reference import expanded_configuration, fixture_sources

from .test_research_sweep_matrix import result


def frame_file(directory: Path, count: int, padding: int = 0) -> dict[str, Any]:
    directory.mkdir(exist_ok=True)
    file_hash, canonical = hashlib.sha256(), hashlib.sha256(b"[")
    path = directory / "configurations.bin"
    with path.open("wb") as handle:
        handle.write(ENGINE_MAGIC)
        file_hash.update(ENGINE_MAGIC)
        for index in range(count):
            row = {**result(index), "padding": "x" * padding}
            if index:
                canonical.update(b",")
            canonical.update(matrix._canonical(row))
            payload = msgpack.packb(row, use_bin_type=True)
            frame = FRAME_HEADER.pack(len(payload), hashlib.sha256(payload).digest()) + payload
            handle.write(frame)
            file_hash.update(frame)
    canonical.update(b"]")
    return {
        "configuration_count": count,
        "binary_configurations": {
            "format": "framed-msgpack-v1",
            "file": path.name,
            "bytes": path.stat().st_size,
            "sha256": file_hash.hexdigest(),
        },
        "fingerprint": {"configurations_sha256": canonical.hexdigest()},
    }


def test_selected_plan_exact_coverage_and_five_batches(tmp_path: Path) -> None:
    path = tmp_path / "configurations.json"
    runner.write_json(path, matrix.selected_configuration_plan())
    selection = matrix._selection(path)
    rows, indices = selection["configurations"], selection["canonical_ordinals"]
    assert len(rows) == len({row["id"] for row in rows}) == 4480
    assert rows == [expanded_configuration(index) for index in indices]
    counts = {(False, False): 0, (True, False): 0, (False, True): 0, (True, True): 0}
    for row in rows:
        counts[row["exchange_tp_pct"] is not None, row["global_tp_pct"] is not None] += 1
        assert 1 <= float(row["step_pct"]) <= 10
        assert 2 <= float(row["tp_pct"]) <= 8
    assert counts == {
        (False, False): 70,
        (True, False): 490,
        (False, True): 490,
        (True, True): 3430,
    }
    assert [len(rows[start : start + 1024]) for start in range(0, len(rows), 1024)] == [
        1024
    ] * 4 + [384]
    assert indices[0] != 0  # study ordinal is not a canonical ordinal
    identity = {"matrix": selection}
    catalog = matrix.ResultCatalog(tmp_path / "results.sqlite", identity, create=True)
    try:
        for start in range(0, len(rows), 1024):
            catalog.append(start, (result(index) for index in indices[start : start + 1024]), {})
        assert catalog.verify(4480, complete=True)["configuration_count"] == 4480
        assert (
            matrix.page_results(tmp_path, config_id=rows[-1]["id"])["configurations"][0]["id"]
            == rows[-1]["id"]
        )
        columns = {
            row[1] for row in catalog.connection.execute("PRAGMA table_info(configurations)")
        }
        assert "payload" not in columns and {"offset", "size", "canonical_ordinal"} <= columns
        assert (tmp_path / "results.bin").read_bytes().startswith(matrix.RESULT_MAGIC)
    finally:
        catalog.close()
    invalid = matrix.selected_configuration_plan()
    invalid["configurations"][1] = invalid["configurations"][0]
    runner.write_json(path, invalid)
    with pytest.raises(ValueError, match="unique"):
        matrix._selection(path)


@pytest.mark.parametrize("records", [1, 1024, 2048, 32000])
def test_load_fixture_has_exact_records_on_each_source_and_retains_gaps(
    tmp_path: Path, records: int
) -> None:
    import struct

    sources = fixture_sources(tmp_path / "exact", count=9, minutes=records, exact_records=True)
    assert {source["records"] for source in sources} == {records}
    for source in sources:
        payload = Path(source["path"]).read_bytes()
        assert len(payload) == records * 24
        assert hashlib.sha256(payload).hexdigest() == source["sha256"]
        times = [row[0] for row in struct.iter_unpack("<qffff", payload)]
        assert times == sorted(set(times))
        assert (times[0], times[-1]) == (source["first_ms"], source["last_ms"])
    if records > 11:
        gapped = sources[1]
        assert gapped["last_ms"] - gapped["first_ms"] > (records - 1) * 60000


def test_stream_exceeds_old_128mib_limit_with_1024_records_and_bounded_memory(
    tmp_path: Path,
) -> None:
    summary = frame_file(tmp_path, 1024, 132000)
    assert summary["binary_configurations"]["bytes"] > 128 * 1024**2
    runner.write_json(tmp_path / "summary.json", summary)
    assert (tmp_path / "summary.json").stat().st_size < 2048
    tracemalloc.start()
    try:
        count = 0
        for index, row in enumerate(
            engine_results(tmp_path, runner.read_json(tmp_path / "summary.json"))
        ):
            assert row["id"] == expanded_configuration(index)["id"]
            assert len(row["padding"]) == 132000
            count += 1
        assert count == 1024
        assert tracemalloc.get_traced_memory()[1] < 16 * 1024**2
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize("change", ["truncate", "corrupt", "extra", "digest"])
def test_engine_frames_reject_incomplete_or_changed_results(tmp_path: Path, change: str) -> None:
    summary = frame_file(tmp_path, 2)
    path = tmp_path / "configurations.bin"
    data = path.read_bytes()
    if change == "truncate":
        path.write_bytes(data[:-10])
    elif change == "corrupt":
        path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    elif change == "extra":
        path.write_bytes(data + b"tail")
    else:
        summary["binary_configurations"]["sha256"] = "00" * 32
    with pytest.raises(ValueError):
        list(engine_results(tmp_path, summary))


def test_index_commit_failure_and_uncommitted_tail_are_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "results.sqlite"
    catalog = matrix.ResultCatalog(path, {}, create=True)
    catalog.append(0, [result(0)], {})
    boundary = catalog.metadata("committed_boundary")
    sync = os.fsync

    def no_space(_fd: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", no_space)
    with pytest.raises(OSError, match="No space"):
        catalog.append(1, [result(1)], {})
    monkeypatch.setattr(os, "fsync", sync)
    assert catalog.count() == 1
    assert catalog.metadata("committed_boundary") == boundary
    catalog.close()
    with (tmp_path / "results.bin").open("ab") as handle:
        handle.write(b"uncommitted crash tail")
    assert matrix.page_results(tmp_path)["configuration_count"] == 1
    reopened = matrix.ResultCatalog(path, {})
    try:
        assert reopened.binary_path.stat().st_size == boundary["bytes"]
        reopened.append(1, [result(1)], {})
        assert reopened.verify(2)["configuration_count"] == 2
    finally:
        reopened.close()


def test_legacy_sqlite_blob_results_stay_readable(tmp_path: Path) -> None:
    payload, checksum = matrix.encode_result(result())
    path = tmp_path / "results.sqlite"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            "CREATE TABLE metadata(key TEXT,value BLOB); "
            "CREATE TABLE configurations(ordinal INTEGER,config_id TEXT,payload BLOB,sha256 BLOB);"
        )
        for key, value in {"schema": matrix.LEGACY_CATALOG_SCHEMA, "complete": True}.items():
            connection.execute("INSERT INTO metadata VALUES(?,?)", (key, msgpack.packb(value)))
        connection.execute(
            "INSERT INTO configurations VALUES(?,?,?,?)", (0, result()["id"], payload, checksum)
        )
    before = path.read_bytes()
    assert matrix.page_results(tmp_path)["configurations"] == [result()]
    assert path.read_bytes() == before


@pytest.mark.parametrize("commit", [False, True])
def test_hard_crash_around_index_commit(tmp_path: Path, commit: bool) -> None:
    catalog = matrix.ResultCatalog(tmp_path / "results.sqlite", {}, create=True)
    catalog.append(0, [result(0)], {})
    catalog.close()
    code = """
import os, sys
from pathlib import Path
from scripts.research_sweep_matrix import ResultCatalog
from tests.research.test_research_sweep_matrix import result
c = ResultCatalog(Path(sys.argv[1]), {})
if sys.argv[2] == 'before':
    original = c.set_metadata
    def crash(name, value):
        if name == 'committed_boundary':
            os._exit(17)
        original(name, value)
    c.set_metadata = crash
c.append(1, [result(1)], {})
os._exit(17)
"""
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(tmp_path / "results.sqlite"),
            "after" if commit else "before",
        ],
        check=False,
        timeout=20,
    )
    assert child.returncode == 17
    reopened = matrix.ResultCatalog(tmp_path / "results.sqlite", {})
    try:
        count = 2 if commit else 1
        assert reopened.count() == count
        assert reopened.verify(count)["configuration_count"] == count
        if not commit:
            reopened.append(1, [result(1)], {})
        assert reopened.verify(2)["configuration_count"] == 2
    finally:
        reopened.close()


def test_cancelled_stream_never_commits_partial_batch(tmp_path: Path) -> None:
    catalog = matrix.ResultCatalog(tmp_path / "results.sqlite", {}, create=True)

    def rows() -> Any:
        yield result(0)
        raise InterruptedError("cancel publication")

    try:
        boundary = catalog.metadata("committed_boundary")
        with pytest.raises(InterruptedError):
            catalog.append(0, rows(), {})
        assert catalog.count() == 0
        assert catalog.metadata("committed_boundary") == boundary
        assert catalog.verify(0)["configuration_count"] == 0
    finally:
        catalog.close()


@pytest.mark.parametrize("damage", ["truncate", "corrupt", "header"])
def test_final_binary_corruption_blocks_read_and_resume(tmp_path: Path, damage: str) -> None:
    path = tmp_path / "results.sqlite"
    catalog = matrix.ResultCatalog(path, {}, create=True)
    catalog.append(0, [result(0)], {})
    catalog.close()
    binary = tmp_path / "results.bin"
    payload = binary.read_bytes()
    if damage == "truncate":
        binary.write_bytes(payload[:-5])
    elif damage == "header":
        binary.write_bytes(b"INVALID!" + payload[8:])
    else:
        binary.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
    with pytest.raises(ValueError):
        matrix.ResultCatalog(path, {})
    with pytest.raises(ValueError):
        matrix.page_results(tmp_path)
