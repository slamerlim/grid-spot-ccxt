"""Observed research identity, independent result hashes and bounded offline Git reads."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from grid.domain.numbers import DomainError
from grid.operations.experiment import source_digest
from grid.research import __main__ as research
from grid.research import git_identity, provenance
from grid.research.artifacts import digest, plain, publish
from grid.research.config import bundle
from grid.research.provenance import Observation, source_identity
from grid.research.replay import run

from .conftest import MakeReplay


def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "user.name=Research test",
            "-c",
            "user.email=research-test@localhost",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            *arguments,
        ],
        cwd=root,
        env=git_identity.ENV,
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    return result.stdout.strip()


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    package = root / "src/grid"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('"""Synthetic package."""\n')
    (package / "strategy.py").write_text("value = 1\n")
    (root / "pyproject.toml").write_text('[project]\nname = "synthetic"\n')
    (root / "uv.lock").write_text("version = 1\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "synthetic source")
    return root


def test_clean_git_identity_and_host_evidence_are_bound_to_result(checkout: Path) -> None:
    head = git(checkout, "rev-parse", "HEAD")
    observation = Observation.start(head, package=checkout / "src/grid")
    result = {"synthetic_pnl": "0", "result_sha256": "retained deterministic hash"}
    report: Any = plain(observation.finish("replay", result))
    assert report["schema"] == "grid-research-run-v1"
    assert report["provenance"]["git"]["revision_verified"] is True
    assert report["provenance"]["git"]["observed_head"] == head
    assert report["result"] == result
    assert report["result_payload_sha256"] == digest(result)
    assert report.pop("report_sha256") == digest(report)
    assert report["provenance"]["runtime"]["python"]
    assert report["provenance"]["runtime"]["distributions"]["ccxt"]
    assert report["provenance"]["host_at_start"]["logical_cpus"]
    assert "hostname" not in report["provenance"]["host_at_start"]
    assert report["provenance"]["observed_wall_ns"] >= 0


@pytest.mark.parametrize("change", ["source", "lock", "project", "added", "removed", "mode"])
def test_dirty_source_cannot_verify_supplied_head(checkout: Path, change: str) -> None:
    head = git(checkout, "rev-parse", "HEAD")
    package = checkout / "src/grid"
    path = package / "strategy.py"
    if change == "source":
        path.write_text("value = 2\n")
    elif change == "lock":
        (checkout / "uv.lock").write_text("version = 2\n")
    elif change == "project":
        (checkout / "pyproject.toml").write_text("# changed manifest\n")
    elif change == "added":
        (package / "untracked.py").write_text("value = 3\n")
    elif change == "removed":
        path.unlink()
    else:
        path.chmod(0o700)
    observation = Observation.start(head, package=package)
    assert observation.git["observed_head"] == head
    assert observation.git["source_matches_head"] is False
    assert observation.git["revision_verified"] is False
    assert observation.git["status"] == "mismatch"


def test_labels_and_unrelated_docs_are_not_clean_repository_claims(checkout: Path) -> None:
    head = git(checkout, "rev-parse", "HEAD")
    (checkout / "notes.md").write_text("uncommitted documentation\n")
    package = checkout / "src/grid"
    observed = Observation.start("HEAD", package=package)
    assert observed.git["source_matches_head"] is True
    assert observed.git["revision_verified"] is False
    observed = Observation.start(head, package=package)
    assert observed.git["revision_verified"] is True
    assert "NOT_CHECKED" in str(observed.git["whole_repository_clean"])


def test_git_environment_is_not_inherited_and_index_is_unchanged(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = git(checkout, "rev-parse", "HEAD")
    index = checkout / ".git/index"
    before, stamp = index.read_bytes(), index.stat().st_mtime_ns
    trace = tmp_path / "unwanted-trace"
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "absent"))
    monkeypatch.setenv("GIT_TRACE", str(trace))
    monkeypatch.setenv("PATH", str(tmp_path / "absent"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    observed = Observation.start(head, package=checkout / "src/grid")
    assert observed.git["revision_verified"] is True
    assert not trace.exists()
    assert index.read_bytes() == before and index.stat().st_mtime_ns == stamp


def test_replace_refs_cannot_substitute_commit_contents(checkout: Path) -> None:
    original = git(checkout, "rev-parse", "HEAD")
    path = checkout / "src/grid/strategy.py"
    path.write_text("value = 2\n")
    git(checkout, "add", ".")
    git(checkout, "commit", "-q", "-m", "different synthetic source")
    replacement = git(checkout, "rev-parse", "HEAD")
    git(checkout, "checkout", "-q", "--detach", original)
    git(checkout, "replace", original, replacement)
    observed = Observation.start(original, package=checkout / "src/grid")
    assert observed.git["revision_verified"] is True


def test_missing_local_tree_never_runs_a_promisor_transport(checkout: Path) -> None:
    head = git(checkout, "rev-parse", "HEAD")
    tree = git(checkout, "rev-parse", "HEAD:src/grid")
    marker = checkout / "transport-was-run"
    git(checkout, "config", "remote.evidence.url", "ext::touch " + str(marker))
    git(checkout, "config", "remote.evidence.promisor", "true")
    git(checkout, "config", "protocol.ext.allow", "always")
    (checkout / ".git/objects" / tree[:2] / tree[2:]).unlink()
    observed = Observation.start(head, package=checkout / "src/grid")
    assert observed.git["status"] == "unavailable"
    assert observed.git["revision_verified"] is False
    assert not marker.exists()


def test_missing_git_and_installed_package_remain_explicitly_unverified(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_identity, "GIT", str(tmp_path / "absent"))
    observed = Observation.start("test", package=checkout / "src/grid")
    assert observed.git["status"] == "unavailable"
    package = tmp_path / "site-packages/grid"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# installed synthetic source\n")
    observed = Observation.start("test", package=package)
    assert observed.git["status"] == "no_source_checkout"
    assert observed.git["revision_verified"] is False
    assert observed.finish("validate", {"synthetic": True})["result"] == {"synthetic": True}


@pytest.mark.parametrize("change", ["source", "lock", "added", "head"])
def test_changes_during_run_refuse_publication(checkout: Path, change: str) -> None:
    head = git(checkout, "rev-parse", "HEAD")
    observed = Observation.start(head, package=checkout / "src/grid")
    if change == "head":
        git(checkout, "commit", "--allow-empty", "-q", "-m", "new synthetic revision")
    else:
        target = {
            "source": checkout / "src/grid/strategy.py",
            "lock": checkout / "uv.lock",
            "added": checkout / "src/grid/extra.py",
        }[change]
        target.write_text("# changed during run\n")
    with pytest.raises(DomainError, match="changed during run"):
        observed.finish("replay", {"synthetic": True})


def test_cli_does_not_publish_after_source_changes(
    checkout: Path, make_replay: MakeReplay, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = make_replay(((100, 101, 99, 100),) * 3)
    config, output = tmp_path / "config.json", tmp_path / "report.json"
    publish(config, bundle(spec))
    observed = Observation.start(spec.code_revision, package=checkout / "src/grid")
    monkeypatch.setattr(Observation, "start", lambda supplied: observed)

    def changed(specification: object) -> object:
        (checkout / "src/grid/strategy.py").write_text("# changed source\n")
        return {"synthetic": True}

    monkeypatch.setattr(research, "run", changed)
    assert research.main(["replay", "--config", str(config), "--output", str(output)]) == 2
    assert not output.exists()


def test_machine_observations_do_not_change_financial_result_hash(
    checkout: Path, make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run(make_replay(((100, 101, 99, 100),) * 3))
    before = digest(result)
    first = Observation.start("test", package=checkout / "src/grid").finish("replay", result)
    monkeypatch.setattr(provenance, "host", lambda: {"synthetic_host": "other"})
    second = Observation.start("test", package=checkout / "src/grid").finish("replay", result)
    assert first["result"] == second["result"] == result
    assert first["result_payload_sha256"] == second["result_payload_sha256"] == before
    assert first["report_sha256"] != second["report_sha256"]
    assert digest(result) == before


def test_runtime_change_refuses_report(checkout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observed = Observation.start("test", package=checkout / "src/grid")
    monkeypatch.setattr(provenance, "runtime", lambda: {"python": "changed"})
    with pytest.raises(DomainError, match="changed during run"):
        observed.finish("replay", {"synthetic": True})


def test_missing_host_fields_remain_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(path: Path, limit: int) -> tuple[bytes, str]:
        raise OSError("synthetic host metadata unavailable")

    monkeypatch.setattr(provenance, "read_file", unavailable)
    report = provenance.host()
    assert report["cpu_model"] is None and report["memory_total_kib"] is None
    assert "not deployment sizing" in str(report["scope"])


def test_source_digest_matches_existing_operational_algorithm() -> None:
    package = Path(provenance.__file__).resolve().parents[1]
    assert source_identity(package).python_sha256 == source_digest()


@pytest.mark.parametrize(
    "unsafe", ["symlink", "directory_link", "fifo", "large", "count", "entries", "total"]
)
def test_unsafe_or_unbounded_sources_are_refused(
    checkout: Path, unsafe: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = checkout / "src/grid"
    path = package / "extra.py"
    if unsafe == "symlink":
        path.symlink_to(package / "strategy.py")
    elif unsafe == "directory_link":
        (package / "linked").symlink_to(package, target_is_directory=True)
    elif unsafe == "fifo":
        os.mkfifo(path)
    elif unsafe == "large":
        monkeypatch.setattr(provenance, "MAX_FILE_BYTES", 4)
    elif unsafe == "entries":
        monkeypatch.setattr(provenance, "MAX_ENTRIES", 1)
    elif unsafe == "total":
        monkeypatch.setattr(provenance, "MAX_SOURCE_BYTES", 4)
    else:
        monkeypatch.setattr(provenance, "MAX_FILES", 1)
    with pytest.raises(DomainError):
        source_identity(package)


@pytest.mark.parametrize("failure", ["timeout", "oversize"])
def test_git_child_is_bounded_and_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    executable, marker = tmp_path / "fake-git", tmp_path / "pid"
    executable.write_text(
        "#!"
        + sys.executable
        + "\nimport os,time\n"
        + "open("
        + repr(str(marker))
        + ",'w').write(str(os.getpid()))\n"
        + ("time.sleep(60)\n" if failure == "timeout" else "os.write(1, b'x'*100000)\n")
    )
    executable.chmod(0o700)
    monkeypatch.setattr(git_identity, "GIT", str(executable))
    monkeypatch.setattr(git_identity, "TIMEOUT", 0.5)
    monkeypatch.setattr(git_identity, "MAX_OUTPUT", 1024)
    began = time.monotonic()
    with pytest.raises(DomainError):
        git_identity.read(tmp_path, ("rev-parse", "--verify", "HEAD^{commit}"))
    assert time.monotonic() - began < 3
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)


def test_git_plumbing_allowlist_refuses_other_commands(tmp_path: Path) -> None:
    with pytest.raises(DomainError, match="unsupported local Git command"):
        git_identity.read(tmp_path, ("fetch",))


def test_report_hash_detects_provenance_or_result_changes(checkout: Path) -> None:
    observed = Observation.start("test", package=checkout / "src/grid")
    report: Any = json.loads(json.dumps(plain(observed.finish("replay", {"pnl": "0"}))))
    expected = report.pop("report_sha256")
    report["provenance"]["git"]["revision_verified"] = True
    assert digest(report) != expected
    report["result"]["pnl"] = "999"
    assert digest(report["result"]) != report["result_payload_sha256"]
