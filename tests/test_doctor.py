import json
import sqlite3
import sys
from collections.abc import Iterator, Mapping
from importlib import metadata
from pathlib import Path
from typing import NoReturn

import pytest

from grid.cli import main
from grid.config import parse_config
from grid.doctor import DoctorReport, run_doctor


@pytest.fixture(autouse=True)
def deterministic_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIVE", raising=False)
    monkeypatch.delenv("GRID_LIVE", raising=False)
    monkeypatch.setattr(sys, "version_info", (3, 12, 0, "final", 0))
    monkeypatch.setattr("platform.python_version", lambda: "3.12.0")
    monkeypatch.setattr(sqlite3, "sqlite_version", "3.51.3")
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 51, 3))

    def fake_version(package: str) -> str:
        assert package == "ccxt"
        return "4.5.0-test"

    monkeypatch.setattr(metadata, "version", fake_version)


def statuses(report: DoctorReport) -> dict[str, str]:
    return {check.code: check.status for check in report.checks}


def test_doctor_always_blocks_live_and_reports_only_offline_evidence() -> None:
    report = run_doctor(parse_config({}), environ={})
    checks = statuses(report)
    assert not report.ready
    assert checks["offline_boundary"] == "BLOCKED"
    assert checks["sqlite_version_threshold"] == "PASS"
    assert checks["sqlite_build_provenance"] == "NOT_TESTED"
    assert checks["venue_capabilities"] == "NOT_TESTED"
    assert checks["strategy_and_execution"] == "NOT_TESTED"
    assert checks["operator_signoff"] == "BLOCKED"
    assert report.ccxt_version == "4.5.0-test"
    assert report.sqlite_version == "3.51.3"
    assert "LIVE: DISABLED" in report.to_text()


@pytest.mark.parametrize("name", ["LIVE", "GRID_LIVE"])
@pytest.mark.parametrize("value", ["true", "1", "yes", "ON", " True ", "unexpected"])
def test_live_environment_flags_cannot_enable_runtime(name: str, value: str) -> None:
    report = run_doctor(parse_config({}), environ={name: value})
    assert statuses(report)["live_request"] == "BLOCKED"
    assert not report.ready
    assert report.to_dict()["live"] == "DISABLED"
    assert "unexpected" not in report.to_text()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "OFF", " False "])
def test_false_flags_are_not_live_requests(value: str) -> None:
    report = run_doctor(parse_config({}), environ={"LIVE": value, "GRID_LIVE": value})
    assert statuses(report)["live_request"] == "PASS"
    assert not report.ready


def test_config_live_request_remains_disabled() -> None:
    report = run_doctor(parse_config({"live": True}), environ={})
    assert statuses(report)["live_request"] == "BLOCKED"
    assert not report.ready


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ((3, 44, 6), "BLOCKED"),
        ((3, 50, 7), "BLOCKED"),
        ((3, 51, 2), "BLOCKED"),
        ((3, 51, 3), "PASS"),
        ((3, 52, 0), "PASS"),
    ],
)
def test_sqlite_threshold_does_not_approve_backports_or_provenance(
    monkeypatch: pytest.MonkeyPatch, version: tuple[int, int, int], expected: str
) -> None:
    monkeypatch.setattr(sqlite3, "sqlite_version_info", version)
    monkeypatch.setattr(sqlite3, "sqlite_version", ".".join(map(str, version)))
    report = run_doctor(parse_config({}), environ={})
    assert statuses(report)["sqlite_version_threshold"] == expected
    assert statuses(report)["sqlite_build_provenance"] == "NOT_TESTED"
    assert not report.ready


def test_missing_ccxt_metadata_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_version(package: str) -> NoReturn:
        raise metadata.PackageNotFoundError(package)

    monkeypatch.setattr(metadata, "version", missing_version)
    report = run_doctor(parse_config({}), environ={})
    assert report.ccxt_version is None
    assert statuses(report)["ccxt_metadata"] == "BLOCKED"


def test_unsupported_python_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 11, 0, "final", 0))
    report = run_doctor(parse_config({}), environ={})
    assert statuses(report)["python_runtime"] == "BLOCKED"


class _OnlyLiveFlags(Mapping[str, str]):
    def __getitem__(self, key: str) -> str:
        assert key in {"LIVE", "GRID_LIVE"}, "doctor attempted a credential/environment read"
        return "false"

    def __len__(self) -> int:
        return 2

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("doctor must not enumerate the environment")


def test_doctor_does_not_read_credentials_or_open_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("doctor attempted a database connection")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    report = run_doctor(parse_config({}), environ=_OnlyLiveFlags())
    assert not report.ready


def test_cli_text_is_successful_diagnostics(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor"]) == 0
    output = capsys.readouterr()
    assert "LIVE: DISABLED" in output.out
    assert "Live readiness: BLOCKED" in output.out
    assert not output.err


def test_cli_json_check_is_nonzero_and_creates_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["doctor", "--json", "--check"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == 1
    assert report["mode"] == "offline"
    assert report["live"] == "DISABLED"
    assert report["ready"] is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("json_output", [False, True])
def test_cli_config_errors_are_sanitized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], json_output: bool
) -> None:
    secret = "DO_NOT_DISCLOSE_INPUT_VALUE"  # pragma: allowlist secret - redaction test marker
    path = tmp_path / secret
    path.write_text(f'grid:\n  spacing: "{secret}"\n', encoding="utf-8")
    args = ["doctor", "--config", str(path)] + (["--json"] if json_output else [])
    assert main(args) == 2
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    if json_output:
        report = json.loads(output.out)
        assert report["live"] == "DISABLED"
        assert report["ready"] is False
        assert "error" in report
    else:
        assert "LIVE: DISABLED" in output.err


def test_cli_explicit_config_respects_live_blocker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("live: true", encoding="utf-8")
    assert main(["doctor", "--config", str(path), "--json", "--check"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any(
        check["code"] == "live_request" and check["status"] == "BLOCKED"
        for check in report["checks"]
    )


def test_no_live_command_exists() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["start"])
    assert caught.value.code == 2


@pytest.mark.parametrize("args", [["TOP_SECRET_MARKER"], ["doctor", "--TOP_SECRET_MARKER"]])
def test_invalid_cli_arguments_are_not_echoed(
    args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(args)
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert "TOP_SECRET_MARKER" not in output.out + output.err
