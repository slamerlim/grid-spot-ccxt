"""Main research commands share engines, reject bad inputs and preserve source bytes."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import pytest

from grid.cli import main
from grid.research import __main__ as research
from grid.research.artifacts import publish
from grid.research.config import bundle
from grid.research.demo import create

from .conftest import MakeReplay


def test_backtest_alias_matches_existing_research_results(
    make_replay: MakeReplay, tmp_path: Path
) -> None:
    spec = make_replay(((100, 101, 99, 100), (100, 112, 95, 110), (110, 115, 100, 110)))
    config = tmp_path / "config.json"
    publish(config, bundle(spec))
    source = spec.allocations[0].source.path
    original, before = source.read_bytes(), source.stat()
    alias, legacy = tmp_path / "alias.json", tmp_path / "legacy.json"
    assert main(["backtest", "--config", str(config), "--output", str(alias)]) == 0
    assert research.main(["replay", "--config", str(config), "--output", str(legacy)]) == 0
    assert alias.read_bytes() == legacy.read_bytes()
    assert source.read_bytes() == original and source.stat().st_mtime_ns == before.st_mtime_ns


def test_separate_cli_processes_run_backtest_and_storage_sample(tmp_path: Path) -> None:
    config = create(tmp_path / "demo", "synthetic-cli-test")
    source = config.parent / "synthetic.bin"
    original, before = source.read_bytes(), source.stat()
    arguments = [
        ["backtest", "--config", str(config), "--output", str(tmp_path / "replay.json")],
        [
            "bench-storage",
            str(source),
            "--venue",
            "synthetic",
            "--symbol",
            "SYNTH/USD",
            "--count",
            "7",
            "--output",
            str(tmp_path / "benchmark.json"),
            "--code-revision",
            "synthetic-cli-test",
        ],
    ]
    for args in arguments:
        result = subprocess.run(
            [sys.executable, "-m", "grid", *args],
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "simulated execution only" in result.stdout
    report = json.loads((tmp_path / "benchmark.json").read_text())
    assert report["schema"] == "grid-storage-sample-v1"
    assert report["input"]["blockers"]  # Benchmark success never asserts replay provenance.
    assert all(
        t["roundtrip_sha256"] == hashlib.sha256(original).hexdigest()
        for t in report["timings"].values()
    )
    assert "uncontrolled" in report["cache"] and "disabled" in report["concurrency"]
    assert source.read_bytes() == original and source.stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.parametrize("op", ["backtest", "bench-storage"])
@pytest.mark.parametrize("target", ["existing", "symlink", "git", "protected"])
def test_destination_preflight_precedes_replay_or_derived_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, op: str, target: str
) -> None:
    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("research began before output preflight")

    monkeypatch.setattr(research, "run", forbidden)
    monkeypatch.setattr(research, "benchmark", forbidden)
    output = tmp_path / "report.json"
    if target == "existing":
        output.write_text("retain")
    elif target == "symlink":
        output.symlink_to(tmp_path / "absent")
    elif target == "git":
        (tmp_path / ".git").mkdir()
    else:
        output = Path("/opt/grid-runs/i8-public-hour/unapproved-report.json")
    args = [op, "--output", str(output)]
    args += (
        ["--config", str(tmp_path / "absent.json")]
        if op == "backtest"
        else [
            str(tmp_path / "absent.bin"),
            "--venue",
            "synthetic",
            "--symbol",
            "SYNTH/USD",
            "--count",
            "7",
            "--code-revision",
            "test",
        ]
    )
    assert main(args) == 2
    assert not list(tmp_path.glob("format-sample-*"))
    if target == "existing":
        assert output.read_text() == "retain"


@pytest.mark.parametrize("op", ["backtest", "bench-storage"])
def test_alias_argument_errors_do_not_echo_untrusted_values(
    op: str, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = "untrusted_cli_marker"
    with pytest.raises(SystemExit) as result:
        main([op, "--unknown", marker])
    assert result.value.code == 2
    output = capsys.readouterr()
    assert marker not in output.out + output.err


def test_bad_research_config_and_library_failure_are_sanitized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "untrusted_cli_marker"
    config = tmp_path / marker
    config.write_text('{"' + marker + '": true}')
    args = ["backtest", "--config", str(config), "--output", str(tmp_path / "report.json")]
    assert main(args) == 2
    assert marker not in capsys.readouterr().err

    def failure(path: Path) -> NoReturn:
        raise RuntimeError(marker)

    monkeypatch.setattr(research, "load", failure)
    assert main(args) == 2
    assert marker not in capsys.readouterr().err
    assert not (tmp_path / "report.json").exists()


def test_lost_success_output_retains_published_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = create(tmp_path / "demo", "synthetic-cli-test")
    output = tmp_path / "report.json"

    def lost_ack(*args: object, **kwargs: object) -> None:
        if kwargs.get("file") is None:
            raise BrokenPipeError("untrusted_cli_marker")
        print(args[0], file=sys.stderr)

    monkeypatch.setattr(research, "print", lost_ack, raising=False)
    assert main(["backtest", "--config", str(config), "--output", str(output)]) == 2
    assert json.loads(output.read_text())["schema"] == "grid-research-v1"
    message = capsys.readouterr().err
    assert "inspect output artifacts" in message and "untrusted_cli_marker" not in message


def test_interrupted_replay_reports_no_success_and_keeps_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = create(tmp_path / "demo", "synthetic-cli-test")
    original = config.read_bytes()
    output = tmp_path / "report.json"

    def interrupted(*args: object, **kwargs: object) -> NoReturn:
        raise KeyboardInterrupt

    monkeypatch.setattr(research, "run", interrupted)
    assert main(["backtest", "--config", str(config), "--output", str(output)]) == 130
    message = capsys.readouterr()
    assert "interrupted" in message.err and "published" not in message.out
    assert config.read_bytes() == original and not output.exists()


@pytest.mark.parametrize("op", ["backtest", "bench-storage"])
def test_alias_help_is_specific_and_creates_no_artifacts(
    op: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as result:
        main([op, "--help"])
    assert result.value.code == 0
    assert "usage: gridctl " + op in capsys.readouterr().out
    assert not list(tmp_path.iterdir())
