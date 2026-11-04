"""Research run evidence outside deterministic financial results; no secret discovery."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import sqlite3
import stat
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from grid.domain.numbers import DomainError
from grid.research.artifacts import digest
from grid.research.git_identity import compare

MAX_FILES = 512
MAX_ENTRIES = 4096
MAX_FILE_BYTES = 1048576
MAX_SOURCE_BYTES = 16777216


def read_file(path: Path, limit: int) -> tuple[bytes, str]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise DomainError("provenance file type or size bound")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(raw) > limit or (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise DomainError("provenance file changed while reading")
    return raw, "100755" if before.st_mode & 0o111 else "100644"


@dataclass(frozen=True)
class File:
    path: str
    size: int
    sha256: str
    mode: str
    git_blob_sha1: str


def record(path: str, raw: bytes, mode: str) -> File:
    # SHA-1 is used only to compare Git's existing blob identity, never for our source digest.
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False)
    return File(path, len(raw), hashlib.sha256(raw).hexdigest(), mode, blob.hexdigest())


@dataclass(frozen=True)
class SourceIdentity:
    python_sha256: str
    files: tuple[File, ...]
    manifests: tuple[File, ...]


def source_identity(package: Path) -> SourceIdentity:
    """Bounded traversal; the aggregate algorithm matches the existing operational digest."""
    pending = [package]
    paths: list[Path] = []
    entries = 0
    while pending:
        directory = pending.pop()
        if directory.is_symlink():
            raise DomainError("symlinked source directory")
        with os.scandir(directory) as children:
            for child in children:
                entries += 1
                if entries > MAX_ENTRIES or child.is_symlink():
                    raise DomainError("source traversal bound or symlink")
                if child.is_dir(follow_symlinks=False):
                    if child.name != "__pycache__":
                        pending.append(Path(child.path))
                elif child.name.endswith(".py"):
                    if not child.is_file(follow_symlinks=False):
                        raise DomainError("nonregular source")
                    paths.append(Path(child.path))
                    if len(paths) > MAX_FILES:
                        raise DomainError("source file bound")
    if not paths:
        raise DomainError("Python sources unavailable")
    hasher = hashlib.sha256()
    files: list[File] = []
    total = 0
    for path in sorted(paths):
        relative = path.relative_to(package).as_posix()
        if len(relative) > 256 or any(
            re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(?:\.py)?", part) is None
            for part in Path(relative).parts
        ):
            raise DomainError("unsupported source name")
        raw, mode = read_file(path, MAX_FILE_BYTES)
        total += len(raw)
        if total > MAX_SOURCE_BYTES:
            raise DomainError("source byte bound")
        hasher.update(relative.encode() + b"\0" + raw + b"\0")
        files.append(record(relative, raw, mode))
    manifests: list[File] = []
    if package.parent.name == "src":
        for name in ("pyproject.toml", "uv.lock"):
            path = package.parent.parent / name
            if path.exists() or path.is_symlink():
                raw, mode = read_file(path, MAX_FILE_BYTES)
                manifests.append(record(name, raw, mode))
    return SourceIdentity(hasher.hexdigest(), tuple(files), tuple(manifests))


def git_identity(package: Path, supplied: str, source: SourceIdentity) -> dict[str, object]:
    root = package.parent.parent if package.parent.name == "src" else None
    if root is not None and not (root / ".git").exists():
        root = None
    blobs = {"src/grid/" + f.path: (f.mode, f.git_blob_sha1) for f in source.files}
    blobs.update({f.path: (f.mode, f.git_blob_sha1) for f in source.manifests})
    return compare(root, supplied, blobs)


def safe_text(value: str) -> str | None:
    return value if 0 < len(value) <= 256 and value.isprintable() else None


def runtime() -> dict[str, object]:
    versions: dict[str, str | None] = {}
    for name in ("grid-spot-ccxt", "ccxt", "aiohttp", "PyYAML", "pyarrow"):
        try:
            versions[name] = safe_text(metadata.version(name))
        except metadata.PackageNotFoundError:
            versions[name] = None
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "sqlite_linked": sqlite3.sqlite_version,
        "distributions": versions,
        "dependency_code_verified": False,
    }


def host() -> dict[str, object]:
    # Only fixed non-identifying fields; no hostname, user, environment or network interfaces.
    values: dict[str, object] = {
        "os": safe_text(os.uname().sysname),
        "kernel": safe_text(platform.release()),
        "machine": safe_text(platform.machine()),
        "logical_cpus": os.cpu_count(),
        "cpu_model": None,
        "memory_total_kib": None,
        "scope": "Host observations, not deployment sizing or effective container quotas",
    }
    try:
        values["affinity_cpus"] = len(os.sched_getaffinity(0))
    except OSError:
        values["affinity_cpus"] = None
    for path, prefix, key in (
        (Path("/proc/cpuinfo"), "model name", "cpu_model"),
        (Path("/proc/meminfo"), "MemTotal", "memory_total_kib"),
    ):
        try:
            # proc pseudo-files report zero size; read only this bounded prefix.
            raw, _ = read_file(path, 262144)
            value = next(
                line.split(":", 1)[1].strip()
                for line in raw.decode("ascii").splitlines()
                if line.split(":", 1)[0].strip() == prefix
            )
            if key == "memory_total_kib":
                match = re.fullmatch(r"([0-9]{1,18}) kB", value)
                values[key] = int(match[1]) if match else None
            else:
                values[key] = safe_text(value)
        except (OSError, ValueError, DomainError, StopIteration):
            pass
    return values


@dataclass(frozen=True)
class Observation:
    package: Path
    supplied: str
    source: SourceIdentity
    git: dict[str, object]
    runtime: dict[str, object]
    host: dict[str, object]
    started_utc: str
    started_ns: int
    cpu_ns: int

    @classmethod
    def start(cls, supplied: str, *, package: Path | None = None) -> Observation:
        if not supplied.strip() or len(supplied) > 256 or not supplied.isprintable():
            raise DomainError("bounded revision label required")
        started_utc = datetime.now(UTC).isoformat()
        started_ns, cpu_ns = time.monotonic_ns(), time.process_time_ns()
        package = package or Path(__file__).resolve().parents[1]
        source = source_identity(package)
        return cls(
            package,
            supplied,
            source,
            git_identity(package, supplied, source),
            runtime(),
            host(),
            started_utc,
            started_ns,
            cpu_ns,
        )

    def finish(self, operation: str, result: object) -> dict[str, object]:
        if operation not in {"replay", "benchmark", "validate"}:
            raise DomainError("unknown research report operation")
        source = source_identity(self.package)
        current_git = git_identity(self.package, self.supplied, source)
        if source != self.source or runtime() != self.runtime or current_git != self.git:
            raise DomainError("research implementation changed during run")
        elapsed, cpu = time.monotonic_ns() - self.started_ns, time.process_time_ns() - self.cpu_ns
        if elapsed < 0 or cpu < 0:
            raise DomainError("research measurement clock regressed")
        report: dict[str, object] = {
            "schema": "grid-research-run-v1",
            "operation": operation,
            "result": result,
            "result_payload_sha256": digest(result),
            "provenance": {
                "code_revision_supplied": self.supplied,
                "source": source,
                "git": self.git,
                "runtime": self.runtime,
                "host_at_start": self.host,
                "started_utc": self.started_utc,
                "finished_utc": datetime.now(UTC).isoformat(),
                "observed_wall_ns": elapsed,
                "process_cpu_ns": cpu,
                "measurement_scope": (
                    "Engine and evidence capture; excludes config loading/report publication. "
                    "Process CPU excludes Git child CPU."
                ),
                "limitation": (
                    "Before/after disk snapshots, not attestation of loaded code or dependencies"
                ),
            },
        }
        report["report_sha256"] = digest(report)
        return report
