import hashlib
import json
from pathlib import Path

import pytest

from grid.cli import main
from grid.domain.numbers import DomainError
from grid.paper.audit import audit
from grid.paper.journal import Journal
from grid.research.artifacts import canonical


def completed(directory: Path, interval_ms: int = 5000) -> None:
    journal = Journal(directory)
    start = 10000
    journal.append("start", {"observed_clock": False}, start, start * 1000000)
    for ms in (start, start + interval_ms):
        claim = journal.append("attempt", {"endpoint": 1}, ms, ms * 1000000)
        journal.append(
            "response",
            {
                "attempt": claim["sequence"],
                "status": 200,
                "body_hex": b"{}".hex(),
                "sha256": hashlib.sha256(b"{}").hexdigest(),
            },
            ms,
            ms * 1000000,
        )
    journal.append(
        "finished",
        {"result": "mock-clock-complete", "observed_soak_complete": False},
        start + 10000,
        (start + 10000) * 1000000,
    )
    journal.close()


def test_audit_refuses_active_or_unfinished_input_without_mutation(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    journal.append("start", {}, 1, 1)
    source = tmp_path / "observations.jsonl"
    before = source.read_bytes()
    with pytest.raises(BlockingIOError):
        audit(tmp_path)
    journal.close()
    with pytest.raises(DomainError, match="finished"):
        audit(tmp_path)
    assert source.read_bytes() == before


def test_audit_checks_tampering_and_policy_separately(tmp_path: Path) -> None:
    completed(tmp_path)
    source = tmp_path / "observations.jsonl"
    before = source.read_bytes()
    report = audit(tmp_path)
    assert report["wire_attempts"] == 2 and report["minimum_request_interval_seconds"] == 5
    assert report["journal_file_sha256"] == hashlib.sha256(before).hexdigest()
    assert source.read_bytes() == before
    data = [json.loads(row) for row in before.splitlines()]
    data[1]["payload"]["endpoint"] = 0
    source.write_bytes(b"\n".join(canonical(row) for row in data) + b"\n")
    with pytest.raises(DomainError, match="hash chain"):
        audit(tmp_path)


def test_hash_valid_but_too_fast_requests_do_not_pass_audit(tmp_path: Path) -> None:
    completed(tmp_path, 4999)
    with pytest.raises(DomainError, match="spacing"):
        audit(tmp_path)


def test_mock_clock_cannot_be_relabelled_an_observed_hour(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    journal.append("start", {"observed_clock": False}, 10000, 10**10)
    journal.append(
        "finished",
        {"result": "one-hour-observed", "observed_soak_complete": False},
        3610000,
        3610 * 10**9,
    )
    journal.close()
    with pytest.raises(DomainError, match="duration"):
        audit(tmp_path)


def test_cli_audit_is_read_only_and_cannot_claim_live_readiness(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    completed(tmp_path)
    source = tmp_path / "observations.jsonl"
    original = source.read_bytes()
    assert main(["paper-audit", "--directory", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["real_orders"] == 0 and report["i8_acceptance_complete"] is False
    assert source.read_bytes() == original


def test_cli_rejects_active_journal_without_echoing_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    directory = tmp_path / "not-for-diagnostics"
    directory.mkdir(mode=0o700)
    journal = Journal(directory)
    try:
        journal.append("start", {}, 1, 1)
        assert main(["paper-audit", "--directory", str(directory)]) == 2
        output = capsys.readouterr()
        assert "not-for-diagnostics" not in output.out + output.err
        assert "refused" in output.err
    finally:
        journal.close()


@pytest.mark.parametrize("invalid", ["public-directory", "non-object-row"])
def test_cli_invalid_storage_is_a_sanitized_refusal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    invalid: str,
) -> None:
    source = tmp_path / "observations.jsonl"
    source.write_text('"not-an-object"\n')
    source.chmod(0o600)
    if invalid == "public-directory":
        tmp_path.chmod(0o755)
    assert main(["paper-audit", "--directory", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert "refused" in output.err and "Traceback" not in output.err
