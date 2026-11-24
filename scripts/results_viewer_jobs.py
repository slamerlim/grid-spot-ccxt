"""One owned, restartable detail replay; binary derivatives of a published run."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import zlib
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, suppress
from fractions import Fraction
from pathlib import Path
from typing import Any, BinaryIO

import msgpack  # type: ignore[import-untyped]

from scripts import research_sweep as runner
from scripts import research_sweep_matrix as matrix
from scripts import results_viewer_data as data
from scripts.research_sweep_binary import FRAME_HEADER, MAX_FRAME_BYTES

DEFAULT_ROOT = Path("/opt/grid-runs/results-viewer-details")
DEFAULT_BINARY = Path("/opt/grid-builds/results-detail/grid_backtest")
POLICY = "maximum_lifetime_peak_shortfall_attained_in_month"
SCHEMA = "grid-configuration-detail-v1"
MAGIC = b"GRIDDT1\n"
LIVE = {"QUEUED", "RUNNING", "STOPPING"}
RATIO = re.compile(r"-?\d+/[1-9]\d*\Z")
JOB_ID = re.compile(r"[a-f0-9]{64}\Z")
SOURCE_FILES = (
    "scripts/results_viewer_jobs.py",
    "scripts/results_viewer_data.py",
    "scripts/research_sweep.py",
    "scripts/research_sweep_matrix.py",
    "scripts/research_sweep_binary.py",
    "scripts/research_sweep_reference.py",
    "scripts/research_sweep_verify.py",
    "scripts/research_sweep_sources.py",
    "cpp/backtest/sweep.cpp",
    "cpp/backtest/sweep.hpp",
    "cpp/backtest/sweep_units.cpp",
    "cpp/backtest/sweep_units.hpp",
    "cpp/backtest/numeric.cpp",
    "cpp/backtest/numeric.hpp",
    "cpp/backtest/CMakeLists.txt",
    "uv.lock",
)


def code_identity() -> str:
    root = Path(__file__).resolve().parents[1]
    return matrix._digest({name: runner.sha256(root / name) for name in SOURCE_FILES})


def _pack(value: Any) -> Any:
    if isinstance(value, str) and RATIO.fullmatch(value):
        return msgpack.ExtType(
            42, msgpack.packb([matrix._integer_bytes(v) for v in value.split("/")])
        )
    if isinstance(value, dict):
        return {key: _pack(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_pack(item) for item in value]
    return value


def _ext(code: int, raw: bytes) -> str:
    if code != 42:
        raise ValueError("Неизвестный точный тип подробностей")
    value = data._packed_fraction(msgpack.unpackb(raw))
    if value is None:
        raise ValueError("Пустая точная дробь")
    return f"{value.numerator}/{value.denominator}"


def _raw_frames(path: Path) -> Iterator[dict[str, Any]]:
    if path.is_symlink():
        raise ValueError("Ссылка вместо бинарного результата")
    with path.open("rb") as handle:
        while header := handle.read(FRAME_HEADER.size):
            if len(header) != FRAME_HEADER.size:
                raise ValueError("Усечённый кадр подробностей")
            length, checksum = FRAME_HEADER.unpack(header)
            if not 0 < length <= MAX_FRAME_BYTES:
                raise ValueError("Слишком большой кадр подробностей")
            raw = handle.read(length)
            if len(raw) != length or hashlib.sha256(raw).digest() != checksum:
                raise ValueError("Контрольная сумма кадра подробностей не совпала")
            row = msgpack.unpackb(raw, raw=False, strict_map_key=True)
            if not isinstance(row, dict):
                raise ValueError("Неверный бинарный результат")
            yield row


class DetailCatalog:
    """Bounded frames; commit index only after the binary prefix is durable."""

    def __init__(self, directory: Path, *, create: bool = False):
        self.directory = directory
        self.binary_path = directory / "details.bin"
        index = directory / "details.sqlite"
        self.handle: BinaryIO = self.binary_path.open("xb" if create else "rb")
        self.connection = sqlite3.connect(index) if create else matrix._readonly(index)
        self.fields: dict[str, list[str]] = {}
        if create:
            self.handle.write(MAGIC)
            self.connection.executescript(
                "PRAGMA synchronous=FULL; CREATE TABLE metadata(key TEXT PRIMARY KEY,value BLOB);"
                "CREATE TABLE records(kind TEXT,scope TEXT,ordinal INTEGER,offset INTEGER,"
                "size INTEGER,sha256 BLOB,PRIMARY KEY(kind,scope,ordinal));"
            )
        elif self.handle.read(len(MAGIC)) != MAGIC:
            self.close()
            raise ValueError("Неверный формат подробностей")
        else:
            self.fields = self.metadata("fields")

    def metadata(self, key: str) -> Any:
        row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        if row is None:
            raise ValueError("Отсутствует индекс подробностей")
        return msgpack.unpackb(row[0], raw=False)

    def put(self, kind: str, scope: str, ordinal: int, row: dict[str, Any]) -> None:
        fields = self.fields.setdefault(kind, list(row))
        if set(fields) != set(row):
            raise ValueError("Поля подробностей изменились")
        raw = msgpack.packb(_pack([row[key] for key in fields]), use_bin_type=True)
        if len(raw) > matrix.MAX_PAYLOAD_BYTES:
            raise ValueError("Кадр подробностей превышает лимит")
        payload, checksum = zlib.compress(raw, level=3), hashlib.sha256(raw).digest()
        offset = self.handle.tell()
        self.handle.write(FRAME_HEADER.pack(len(payload), checksum))
        self.handle.write(payload)
        self.connection.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?)",
            (kind, scope, ordinal, offset, len(payload), checksum),
        )

    def commit(self, identity: dict[str, Any], complete: bool = False) -> None:
        self.handle.flush()
        os.fsync(self.handle.fileno())
        for key, value in (
            ("identity", identity),
            ("fields", self.fields),
            ("complete", complete),
            ("committed_bytes", self.handle.tell()),
        ):
            self.connection.execute(
                "INSERT OR REPLACE INTO metadata VALUES(?,?)",
                (key, msgpack.packb(value, use_bin_type=True)),
            )
        self.connection.commit()

    def rows(self, kind: str, scope: str | None = None) -> Iterator[dict[str, Any]]:
        sql = "SELECT offset,size,sha256 FROM records WHERE kind=?"
        args: tuple[Any, ...] = (kind,)
        if scope is not None:
            sql += " AND scope=?"
            args += (scope,)
        boundary = self.metadata("committed_bytes")
        for offset, size, checksum in self.connection.execute(
            sql + " ORDER BY scope,ordinal", args
        ):
            if offset < len(MAGIC) or offset + FRAME_HEADER.size + size > boundary:
                raise ValueError("Индекс выходит за подтверждённые подробности")
            payload = matrix._read_payload(self.handle, offset, size, checksum)
            try:
                raw = matrix._verified_payload_bytes(payload, checksum)
            except zlib.error as error:
                raise ValueError("Повреждено сжатие подробностей") from error
            values = msgpack.unpackb(raw, raw=False, ext_hook=_ext, strict_map_key=True)
            yield dict(zip(self.fields[kind], values, strict=True))

    def close(self) -> None:
        self.handle.close()
        self.connection.close()


def verify_drawdowns(actual: dict[str, Any]) -> None:
    detail = actual["detail"]
    if detail.get("schema") != "grid-detail-risk-v1" or detail.get("definition") != POLICY:
        raise ValueError("Неизвестное определение месячной просадки")
    monthly = {
        "total": detail["total"],
        **{row["venue"]: row["monthly"] for row in detail["venues"]},
    }
    performance = {
        "total": actual["performance"]["total"],
        **{row["venue"]: row for row in actual["performance"]["venues"]},
    }
    metrics = {"total": actual["total"], **{row["venue"]: row for row in actual["venues"]}}
    if set(monthly) != set(performance):
        raise ValueError("Неполный состав месячных просадок")
    for scope, rows in monthly.items():
        maximum = Fraction()
        for row, period in zip(rows, performance[scope]["monthly"], strict=True):
            if (row["start_ms"], row["end_ms"]) != (period["start_ms"], period["end_ms"]):
                raise ValueError("Календарь просадок различается")
            drawdown, peak = Fraction(row["max_drawdown"]), Fraction(period["peak_spent"])
            expected = drawdown * 100 / peak if peak else None
            if (
                drawdown < 0
                or (None if row["max_drawdown_pct"] is None else Fraction(row["max_drawdown_pct"]))
                != expected
            ):
                raise ValueError("Неверный процент месячной просадки")
            if drawdown and not row["start_ms"] <= row["max_drawdown_ms"] < row["end_ms"]:
                raise ValueError("Дата просадки вне месяца")
            maximum = max(maximum, drawdown)
        if maximum != Fraction(metrics[scope]["max_drawdown"]):
            raise ValueError("Месячные просадки не совпали с максимальной просадкой прогона")


def _parent(
    publication: Path, config_id: str
) -> tuple[data.Archive, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    archive = data.open_archive(publication)
    entry = next((row for row in archive.entries if row[1] == config_id), None)
    if entry is None:
        raise ValueError("Неизвестная конфигурация")
    with archive.binary.open("rb") as handle:
        expected = matrix.decode_result(matrix._read_payload(handle, *entry[2:]), entry[4])
    data.check_configuration_depth(expected, archive.configurations[entry[0]])
    study = runner.read_json(archive.binary.parent / "matrix-study.json")
    if (
        study["study_sha256"] != archive.source_hashes[0]
        or matrix._digest(study["identity"]) != study["study_sha256"]
    ):
        raise ValueError("Исходный manifest изменился")
    preparation = Path(study["preparation_path"])
    if runner.sha256(preparation) != study["identity"]["preparation_sha256"]:
        raise ValueError("Правила или источники исходного прогона изменились")
    sources = runner._engine_sources(runner.read_json(preparation))
    if len(sources) != archive.source_count:
        raise ValueError("Состав источников изменился")
    return archive, expected, study, sources


def matrix_detail(binary: Path, request: dict[str, Any], output: Path) -> dict[str, Any]:
    try:
        return _matrix_detail(binary, request, output)
    except sqlite3.Error as error:
        if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
            raise runner.ResourceLimit("Недостаточно места для индекса подробностей") from error
        raise ValueError(f"Ошибка индекса подробностей: {error}") from error
    except (KeyError, TypeError, zlib.error, msgpack.UnpackException) as error:
        raise ValueError(f"Повреждена структура подробностей: {error}") from error


def _matrix_detail(binary: Path, request: dict[str, Any], output: Path) -> dict[str, Any]:
    """Separate derivative admission; never weaken _study's strict equality."""
    publication = Path(
        request.get("publication", Path(request["source_output"]) / "final-publication.json")
    )
    if request.get("artifact_root"):
        resource_root = Path(request["artifact_root"]).resolve()
        if not output.resolve().is_relative_to(resource_root):
            raise ValueError("Подробности выходят за каталог артефактов")
        runner._ACTIVE_JOB_ROOT = resource_root
    archive, expected, study, sources = _parent(publication, request["config_id"])
    if request.get("parent_hashes", list(archive.source_hashes)) != list(archive.source_hashes):
        raise runner.Blocked("Исходный опубликованный прогон изменился после подтверждения")
    actual_code, actual_binary = code_identity(), runner.sha256(binary)
    if (
        request.get("replay_code_sha256", actual_code) != actual_code
        or request.get("replay_binary_sha256", actual_binary) != actual_binary
    ):
        raise runner.Blocked("Сборка подробного расчёта изменилась")
    ordinal = next(row[0] for row in archive.entries if row[1] == expected["id"])
    config = matrix.configuration_at(study["identity"], ordinal)
    settings = dict(
        study["identity"]["settings"], threads=1, numa_interleave_nodes=None, chart_mode="eager"
    )
    plan = runner.make_plan(
        sources, output / "engine", "matrix_batch", configurations=[config], settings=settings
    )
    matrix._bind_source_selection(study, sources, plan)
    plan.update(
        matrix_kind=matrix.MATRIX_KIND,
        detail_mode="monthly_drawdown_v1",
        statistics_mode="monthly_v1",
        result_output="framed_msgpack_v1",
        keep_results=True,
        summary_stdout=False,
        chart_bins=2000,
    )
    if request.get("resume_from"):
        saved = Path(request["resume_from"])
        proof = runner.read_json(saved / "request.json")
        for key in ("publication", "config_id", "replay_code_sha256", "replay_binary_sha256"):
            if proof.get(key) != request.get(key):
                raise runner.Blocked("Контрольная точка относится к другому подробному расчёту")
        plan = runner.copy_checkpoint(binary, saved, output)
    control = Path(request["control_dir"]) if request.get("control_dir") else output

    def stop() -> bool:
        return (control / "stop-request.json").exists()

    outcome = runner.engine_run(
        binary,
        plan,
        output,
        output / "progress.json",
        stop_requested=stop,
        checkpoint_grace_seconds=None,
    )
    actuals = list(
        matrix._verified_results(
            outcome["summary"],
            [config],
            sources,
            full_history=True,
            expected_records=sum(row["records"] for row in sources),
            binary_sha256=actual_binary,
            statistics_required=True,
            result_dir=Path(plan["output_dir"]),
        )
    )
    if len(actuals) != 1:
        raise ValueError("Подробный расчёт содержит более одной конфигурации")
    actual = actuals[0]
    progress_value = runner.read_json(output / "progress.json")
    progress_value.update(
        status="VERIFYING",
        processed_records=outcome["summary"]["processed_records"],
        input_records=sum(row["records"] for row in sources),
    )
    runner.write_json(output / "progress.json", progress_value, durable=False)
    legacy = dict(actual)
    legacy.pop("detail")
    if legacy != expected:
        raise ValueError(
            "Подробный расчёт не совпал с исходными финансовыми результатами или хешами"
        )
    verify_drawdowns(actual)
    identity = {
        "schema": SCHEMA,
        "parent_hashes": list(archive.source_hashes),
        "config_id": expected["id"],
        "replay_code_sha256": actual_code,
        "replay_binary_sha256": actual_binary,
        "parent_code_sha256": study["identity"]["code_sha256"],
        "definition": POLICY,
    }
    last_pulse = 0.0

    def pulse() -> None:
        nonlocal last_pulse
        if time.monotonic() - last_pulse >= 1:
            progress_value.update(
                status="PUBLISHING",
                **runner.resource_observation(runner._ACTIVE_JOB_ROOT or output, os.getpid()),
            )
            runner.write_json(output / "progress.json", progress_value, durable=False)
            last_pulse = time.monotonic()

    _publish_details(output, Path(plan["output_dir"]), actual, sources, identity, pulse)
    if (
        code_identity() != actual_code
        or runner.sha256(binary) != actual_binary
        or data.signature(publication) != archive.identity
    ):
        raise ValueError("Код или исходные результаты изменились во время расчёта")
    return {
        "status": "COMPLETE",
        "schema": SCHEMA,
        "config_id": expected["id"],
        "identity": identity,
        "result_dir": str(output),
        "binary_sha256": runner.sha256(output / "details.bin"),
        "index_sha256": runner.sha256(output / "details.sqlite"),
        "processed_records": outcome["summary"]["processed_records"],
        "source_count": len(sources),
        "wall_seconds": outcome["wall_seconds"],
        "verified_original_results": True,
    }


def _publish_details(
    output: Path,
    engine: Path,
    actual: dict[str, Any],
    sources: list[dict[str, Any]],
    identity: dict[str, Any],
    pulse: Callable[[], None] | None = None,
) -> None:
    with closing(DetailCatalog(output, create=True)) as catalog:
        catalog.put("configuration", "total", 0, actual)
        catalog.commit(identity)
        seen: set[str] = set()
        required = {row["id"] for row in sources}
        fingerprint = hashlib.sha256()
        for index, row in enumerate(_raw_frames(engine / "pairs.bin")):
            source_id = row["source_id"]
            if source_id not in required or source_id in seen or row["config_id"] != actual["id"]:
                raise ValueError("Повтор или неизвестная пара в подробностях")
            seen.add(source_id)
            fingerprint.update(
                json.dumps(
                    row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
                + b"\n"
            )
            catalog.put("pair", row["venue"], index, row)
            if index % 128 == 0:
                catalog.commit(identity)
                runner.check_resources(
                    runner.resource_observation(runner._ACTIVE_JOB_ROOT or output, os.getpid()),
                    runner.LIMITS,
                )
                if pulse:
                    pulse()
        if seen != required or fingerprint.hexdigest() != actual["pair_fingerprint"]["sha256"]:
            raise ValueError("Отпечаток подробностей пар отличается от исходного")
        scopes = {"all", *["venue:" + row["venue"] for row in sources]}
        last: dict[str, int] = {}
        maxima: dict[str, Fraction] = {}
        for index, row in enumerate(_raw_frames(engine / "series.bin")):
            scope = row["scope"]
            if (
                scope not in scopes
                or row["bin"] <= last.get(scope, -1)
                or not 0 <= row["bin"] < 2000
            ):
                raise ValueError("Повтор или неверный интервал графика")
            last[scope] = row["bin"]
            maxima[scope] = max(maxima.get(scope, Fraction()), Fraction(row["detail"]["dd_max"]))
            catalog.put("series", scope, row["bin"], row)
            if index % 128 == 0:
                catalog.commit(identity)
                runner.check_resources(
                    runner.resource_observation(runner._ACTIVE_JOB_ROOT or output, os.getpid()),
                    runner.LIMITS,
                )
                if pulse:
                    pulse()
        metrics = {
            "all": actual["total"],
            **{"venue:" + row["venue"]: row for row in actual["venues"]},
        }
        for scope, metric in metrics.items():
            if maxima.get(scope, Fraction()) != Fraction(metric["max_drawdown"]):
                raise ValueError("Экстремумы графика не совпадают с точной просадкой")
        catalog.commit(identity, complete=True)


@contextmanager
def _requests_lock(root: Path) -> Iterator[None]:
    with (root / "requests.lock").open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _job(root: Path, job_id: str) -> Path:
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("Неверный идентификатор задачи")
    path = root / "jobs" / job_id
    if path.is_symlink():
        raise ValueError("Ссылка вместо задачи")
    return path


def job_for(root: Path, archive: data.Archive, config_id: str) -> Path:
    installed = runner.read_json(root / "worker.json")
    key = matrix._digest(
        [
            list(archive.source_hashes),
            config_id,
            installed["code_sha256"],
            installed["binary_sha256"],
            POLICY,
        ]
    )
    return _job(root, key)


def status(job: Path) -> dict[str, Any]:
    return (
        runner.read_json(job / "status.json")
        if (job / "status.json").exists()
        else {"status": "NOT_CALCULATED"}
    )


def checkpoint_attempt(job: Path, saved: dict[str, Any]) -> Path | None:
    for raw in reversed(saved.get("attempts", [])):
        attempt = _checked_attempt(job, raw)
        if any(
            (attempt / "engine" / name).is_file()
            for name in ("checkpoint.latest.json", "checkpoint.previous.json")
        ):
            return attempt
    return None


def submit(
    root: Path,
    archive: data.Archive,
    config_id: str,
    *,
    resume: bool = False,
    restart: bool = False,
) -> Path:
    if config_id not in {row[1] for row in archive.entries}:
        raise ValueError("Неизвестная конфигурация")
    job = job_for(root, archive, config_id)
    with _requests_lock(root):
        active = runner.read_json(root / "active.json") if (root / "active.json").exists() else {}
        if active.get("job_id"):
            other = _job(root, active["job_id"])
            if status(other)["status"] in LIVE:
                if other == job:
                    return job
                raise runner.Blocked("Выполняется другая тяжёлая задача")
        saved = status(job)
        if saved["status"] == "COMPLETE":
            return job
        if saved["status"] != "NOT_CALCULATED" and not resume:
            raise runner.Blocked("Незавершённая задача требует явного продолжения")
        if resume and saved["status"] not in {
            "PAUSED",
            "INTERRUPTED",
            "FAILED",
            "RESOURCE_LIMIT",
            "BLOCKED",
        }:
            raise runner.Blocked("Эту задачу нельзя продолжить")
        installed = runner.read_json(root / "worker.json")
        if installed["publication"] != str(archive.publication):
            raise ValueError("Worker настроен на другое исследование")
        job.mkdir(parents=True, exist_ok=True)
        if (job / "stop-request.json").exists():
            (job / "stop-request.json").unlink()
        attempts = saved.get("attempts", [])
        checkpoint = None if restart else checkpoint_attempt(job, saved)
        request = {
            "config_id": config_id,
            "parent_hashes": list(archive.source_hashes),
            "resume_from": str(checkpoint) if checkpoint else None,
        }
        runner.write_json(job / "request.json", request)
        runner.write_json(
            job / "status.json",
            {
                "status": "QUEUED",
                "config_id": config_id,
                "attempts": attempts,
                "created_unix": time.time(),
            },
        )
        runner.write_json(root / "active.json", {"job_id": job.name})
    return job


def stop(job: Path) -> None:
    with _requests_lock(job.parent.parent):
        if status(job)["status"] in LIVE:
            runner.write_json(job / "stop-request.json", {"requested_unix": time.time()})


def progress(job: Path) -> dict[str, Any]:
    saved = status(job)
    if saved["status"] in LIVE and (job / "stop-request.json").exists():
        saved["status"] = "STOPPING"
    if saved.get("attempts"):
        attempt = Path(saved["attempts"][-1])
        if (attempt / "progress.json").exists():
            saved["progress"] = runner.read_json(attempt / "progress.json")
        saved["checkpoint_available"] = checkpoint_attempt(job, saved) is not None
    return saved


def verified_receipt(job: Path, archive: data.Archive) -> dict[str, Any] | None:
    saved = status(job)
    if saved["status"] != "COMPLETE":
        return None
    result = _checked_attempt(job, saved["result_dir"])
    receipt = runner.read_json(result / "receipt.json")
    if (
        receipt.get("status") != "COMPLETE"
        or receipt.get("schema") != SCHEMA
        or receipt["identity"]["parent_hashes"] != list(archive.source_hashes)
        or receipt["identity"]["definition"] != POLICY
        or receipt.get("config_id") != saved["config_id"]
    ):
        raise ValueError("Подробности относятся к другому результату")
    for name, key in (("details.bin", "binary_sha256"), ("details.sqlite", "index_sha256")):
        path = result / name
        data.stamp(path)
        if runner.sha256(path) != receipt[key]:
            raise ValueError("Контрольная сумма подробностей не совпала")
    with closing(DetailCatalog(result)) as catalog:
        if (
            catalog.metadata("complete") is not True
            or catalog.metadata("identity") != receipt["identity"]
            or catalog.metadata("committed_bytes") != (result / "details.bin").stat().st_size
        ):
            raise ValueError("Подробности не завершены")
    return receipt


def _checked_attempt(job: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_symlink() or path.parent != job / "attempts" or not path.name.isdecimal():
        raise ValueError("Неверный путь попытки расчёта")
    return path


def serve(publication: Path, binary: Path, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "worker.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        installed = {
            "publication": str(publication.resolve()),
            "binary": str(binary.resolve()),
            "binary_sha256": runner.sha256(binary),
            "code_sha256": code_identity(),
        }
        runner.write_json(root / "worker.json", installed)
        if (root / "active.json").exists():
            active = _job(root, runner.read_json(root / "active.json")["job_id"])
            previous = status(active)
            if previous["status"] in LIVE:
                runner.write_json(
                    active / "status.json",
                    {
                        **previous,
                        "status": "INTERRUPTED",
                        "error": "Worker был перезапущен; продолжение только вручную",
                    },
                )
        while True:
            selected = None
            with _requests_lock(root):
                if (root / "active.json").exists():
                    job = _job(root, runner.read_json(root / "active.json")["job_id"])
                    current = status(job)
                    if current["status"] == "QUEUED":
                        if (job / "stop-request.json").exists():
                            runner.write_json(
                                job / "status.json",
                                {**current, "status": "PAUSED", "finished_unix": time.time()},
                            )
                            continue
                        selected = job
                        attempts = current.get("attempts", [])
                        attempt = job / "attempts" / str(len(attempts))
                        attempt.mkdir(parents=True)
                        runner.write_json(
                            job / "status.json",
                            {
                                **current,
                                "status": "RUNNING",
                                "started_unix": time.time(),
                                "attempts": [*attempts, str(attempt)],
                            },
                        )
            if selected is None:
                time.sleep(1)
                continue
            request = runner.read_json(selected / "request.json")
            request.update(
                action="matrix-detail",
                publication=installed["publication"],
                source_output=str(publication.parent),
                binary=installed["binary"],
                replay_code_sha256=installed["code_sha256"],
                replay_binary_sha256=installed["binary_sha256"],
                control_dir=str(selected),
                artifact_root=str(root),
            )
            runner.write_json(attempt / "input.json", request)
            with (attempt / "worker.log").open("ab") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "scripts.research_sweep",
                        "worker",
                        "--request",
                        str(attempt / "input.json"),
                        "--output",
                        str(attempt),
                    ],
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                process.wait()
            # The job wrapper may die while its C++ child retains the heavy lock.
            # Its private process group lets the owner stop that child safely.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            receipt_path = attempt / "receipt.json"
            receipt = (
                runner.read_json(receipt_path)
                if receipt_path.exists()
                else {"status": "FAIL", "error": "Worker завершился без квитанции"}
            )
            state = {
                "COMPLETE": "COMPLETE",
                "CANCELLED": "PAUSED",
                "RESOURCE_LIMIT": "RESOURCE_LIMIT",
                "BLOCKED": "BLOCKED",
            }.get(receipt["status"], "FAILED")
            current = status(selected)
            runner.write_json(
                selected / "status.json",
                {
                    **current,
                    "status": state,
                    "result_dir": str(attempt) if state == "COMPLETE" else None,
                    "finished_unix": time.time(),
                    "error": receipt.get("error"),
                },
            )
            if state == "COMPLETE":
                with _requests_lock(root):
                    path = root / "completed.json"
                    completed = runner.read_json(path) if path.exists() else {}
                    completed[selected.name] = {
                        "config_id": request["config_id"],
                        "parent_hashes": request["parent_hashes"],
                    }
                    runner.write_json(path, completed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    serve(args.publication, args.binary, args.root)


if __name__ == "__main__":
    main()
