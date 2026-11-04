"""Strict portfolio settings, artifact integrity and the actual offline CLI boundary."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn

import pytest

from grid.cli import main
from grid.domain.numbers import DomainError
from grid.portfolio.model import CyclePhase, Nav
from grid.research import portfolio_replay
from grid.research.artifacts import digest, publish
from grid.research.config import bundle as replay_bundle
from grid.research.history import Bar
from grid.research.portfolio_config import bundle, load
from grid.research.portfolio_replay import Engine, run

from .conftest import MakeReplay
from .test_portfolio_replay import exit_scenario, settings_for

pytestmark = pytest.mark.usefixtures("async_sockets")


def test_explicit_bundle_roundtrip(make_replay: MakeReplay, tmp_path: Path) -> None:
    settings = settings_for(exit_scenario(make_replay))
    path = tmp_path / "portfolio.json"
    publish(path, bundle(settings))
    assert load(path) == settings


@pytest.mark.parametrize(
    "change", ["extra", "missing", "bool", "float", "depth", "duplicate", "schema", "fx", "large"]
)
def test_strict_settings_reject_ambiguous_or_unbounded_inputs(
    make_replay: MakeReplay, tmp_path: Path, change: str
) -> None:
    data: dict[str, Any] = bundle(settings_for(exit_scenario(make_replay)))
    if change == "extra":
        data["unknown"] = "untrusted_marker"
    elif change == "missing":
        del data["sqlite_source_id"]
    elif change == "bool":
        data["actions_per_bar"] = True
    elif change == "float":
        data["actions_per_bar"] = 2.0
    elif change == "depth":
        data["liquidity"][0]["nav_depth"] = "0"
    elif change == "duplicate":
        data["liquidity"].append(data["liquidity"][0])
    elif change == "schema":
        data["schema"] = "unknown"
    elif change == "fx":
        data["conversions"] = data["policy"]
    elif change == "large":
        data["schema"] = "x" * 1048576
    path = tmp_path / "portfolio.json"
    path.write_text(json.dumps(data))
    with pytest.raises(DomainError):
        load(path)


def test_duplicate_json_keys_refused(make_replay: MakeReplay, tmp_path: Path) -> None:
    payload = json.dumps(bundle(settings_for(exit_scenario(make_replay))))
    path = tmp_path / "portfolio.json"
    path.write_text('{"actions_per_bar": 1, ' + payload[1:])
    with pytest.raises(DomainError):
        load(path)


def test_actual_cli_process_publishes_envelope_preserving_input_files(
    make_replay: MakeReplay, tmp_path: Path
) -> None:
    spec = exit_scenario(make_replay)
    config, settings, output = (
        tmp_path / name for name in ("replay.json", "portfolio.json", "result.json")
    )
    publish(config, replay_bundle(spec))
    publish(settings, bundle(settings_for(spec)))
    inputs = (config, settings, spec.allocations[0].source.path)
    original = tuple((p.read_bytes(), p.stat().st_mtime_ns) for p in inputs)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "grid",
            "backtest",
            "--config",
            str(config),
            "--portfolio",
            str(settings),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_bytes())
    assert report["schema"] == "grid-research-run-v1"
    assert report["result"]["schema"] == "grid-portfolio-research-v1"
    assert report["result"]["cycle"]["phase"] == "exit_complete"
    assert report["result"]["final_return"] == "0.05"
    assert digest(report["result"]) == report["result_payload_sha256"]
    assert (
        digest({k: v for k, v in report.items() if k != "report_sha256"}) == report["report_sha256"]
    )
    assert tuple((p.read_bytes(), p.stat().st_mtime_ns) for p in inputs) == original
    assert not list(tmp_path.glob(".grid-research-*"))


def test_bad_settings_are_sanitized_without_report(
    make_replay: MakeReplay, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, settings, output = (
        tmp_path / name for name in ("replay.json", "settings.json", "result.json")
    )
    publish(config, replay_bundle(exit_scenario(make_replay)))
    settings.write_text('{"secret_marker": true}')
    assert (
        main(
            [
                "backtest",
                "--config",
                str(config),
                "--portfolio",
                str(settings),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert "secret_marker" not in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("stage", ["start:before_commit", "start:after_commit"])
def test_ledger_failure_removes_temporary_owner_and_has_no_result(
    make_replay: MakeReplay, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    spec = exit_scenario(make_replay)
    directory = tmp_path / "scratch"
    directory.mkdir()
    monkeypatch.setattr(portfolio_replay, "gettempdir", lambda: str(directory))

    def fail(point: str) -> None:
        if point == stage:
            raise OSError("synthetic disk failure")

    with pytest.raises(OSError, match="synthetic disk failure"):
        run(spec, settings_for(spec), fault=fail)
    assert not list(directory.iterdir())


def test_changed_source_after_cycle_completion_is_still_rejected(
    make_replay: MakeReplay, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = exit_scenario(make_replay)
    original_step = Engine.step
    changed = False

    async def mutate(engine: Engine, bars: tuple[Bar, ...]) -> Nav | None:
        nonlocal changed
        value = await original_step(engine, bars)
        if (await engine.cycle()).phase is CyclePhase.COMPLETE and not changed:
            source = spec.allocations[0].source.path
            source.write_bytes(source.read_bytes()[:-1] + b"\x00")
            changed = True
        return value

    monkeypatch.setattr(Engine, "step", mutate)
    with pytest.raises(DomainError, match="source changed"):
        run(spec, settings_for(spec))
    assert changed


def test_source_coverage_refused_before_temporary_database(
    make_replay: MakeReplay, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = exit_scenario(make_replay)
    spec = replace(spec, start_ms=spec.start_ms + 60000, end_ms=spec.end_ms + 60000)
    directory = tmp_path / "scratch"
    directory.mkdir()
    monkeypatch.setattr(portfolio_replay, "gettempdir", lambda: str(directory))
    with pytest.raises(DomainError, match="eligible exact UTC coverage"):
        run(spec, settings_for(spec))
    assert not list(directory.iterdir())


@pytest.mark.parametrize("target", ["git", "original", "public"])
def test_protected_scratch_root_refused_before_directory_creation(
    make_replay: MakeReplay, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    spec = exit_scenario(make_replay)
    root = tmp_path / "git"
    if target == "git":
        (root / ".git").mkdir(parents=True)
    elif target == "original":
        root = Path("/opt/datasets")
    else:
        root = Path("/opt/grid-runs/i8-public-hour")

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("scratch creation reached before protected-root rejection")

    # Guard the factory as well: a regression must never write to original/evidence roots.
    monkeypatch.setattr(portfolio_replay, "gettempdir", lambda: str(root))
    monkeypatch.setattr(portfolio_replay, "TemporaryDirectory", forbidden)
    with pytest.raises(DomainError, match="outside Git"):
        run(spec, settings_for(spec))
