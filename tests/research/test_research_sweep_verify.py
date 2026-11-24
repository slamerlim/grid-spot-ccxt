"""Publication audit catches missing coverage and changed exact accounting."""

from __future__ import annotations

import hashlib
import json
import struct
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from scripts.research_sweep_verify import verify_job

VENUES = ("okx", "htx", "gate", "xt", "bingx", "kucoin", "bitfinex")
LAST_MS = 1_700_000_040_000
ZERO_HASH = "0" * 64


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metrics(scale: int = 1) -> dict[str, Any]:
    return {
        "realized_gross": "0/1",
        "unrealized_gross": "0/1",
        "fees": str(Fraction(scale, 500)),
        "total_pnl": str(Fraction(-scale, 500)),
        "spent": str(Fraction(scale)),
        "open_value": str(Fraction(scale)),
        "peak_spent": str(Fraction(scale)),
        "peak_spent_ms": LAST_MS,
        "return_pct": "-1/5",
        "max_drawdown": str(Fraction(scale, 500)),
        "max_drawdown_ms": LAST_MS,
        "longest_hold_ms": 0,
        "buy_count": scale,
        "sell_count": 0,
    }


def _fixture(root: Path) -> tuple[Path, Path]:
    job, selection = root / "job", root / "selection.json"
    engine = job / "engine"
    engine.mkdir(parents=True)
    sources = []
    for venue in VENUES:
        path = root / f"{venue}.bin"
        path.write_bytes(struct.pack("<qffff", LAST_MS, 1.0, 1.0, 1.0, 1.0))
        sources.append(
            {
                "id": f"{venue}:X/USDT",
                "venue": venue,
                "symbol": "X/USDT",
                "path": str(path),
                "records": 1,
                "bytes": 24,
                "last_ms": LAST_MS,
                "sha256": _hash(path),
            }
        )
    configs = [
        {"id": f"s{s:02d}-t{t:02d}", "step_pct": str(s / 2), "tp_pct": str(t / 2)}
        for s in range(1, 21)
        for t in range(4, 31)
    ]
    _write(selection, {"status": "READY", "sources": sources})
    selection_sha = _hash(selection)
    binary_sha = "a" * 64
    code = {"calculation.cpp": "b" * 64}
    code_sha = hashlib.sha256(
        json.dumps(code, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    _write(
        job / "study-manifest.json",
        {
            "preparation_sha256": selection_sha,
            "binary_sha256": binary_sha,
            "code_sha256": code,
            "configurations": configs,
        },
    )
    _write(job / "admission.json", {"preparation_sha256": selection_sha})
    evidence = {}
    for stage in ("validate", "calibrate", "endurance", "rehearsal"):
        path = root / f"{stage}-receipt.json"
        _write(
            path,
            {
                "action": stage,
                "status": "PASS" if stage in ("validate", "calibrate") else "COMPLETE",
                "preparation_sha256": selection_sha,
                "binary_sha256": binary_sha,
                "code_sha256": code_sha,
            },
        )
        evidence[stage] = {"receipt": str(path), "sha256": _hash(path)}
    plan = {
        "mode": "full",
        "sources": sources,
        "configurations": configs,
        "threads": 32,
        "block_bytes": 1024**2,
        "output_dir": str(engine),
    }
    _write(job / "plan.json", plan)
    _write(engine / "plan.json", plan)
    _write(
        job / "receipt.json",
        {
            "action": "full",
            "status": "COMPLETE",
            "result_dir": str(engine),
            "preparation_sha256": selection_sha,
            "binary_sha256": binary_sha,
            "code_sha256": code_sha,
            "evidence": evidence,
        },
    )
    index = {}
    results = []
    with (engine / "pairs.jsonl").open("wb") as stream:
        for config in configs:
            begin = stream.tell()
            for source in sources:
                row = {
                    **_metrics(),
                    "config_id": config["id"],
                    "source_id": source["id"],
                    "venue": source["venue"],
                    "symbol": source["symbol"],
                    "last_price_ms": LAST_MS + 3000,
                    "last_price": "1/1",
                    "open_qty": "1/1",
                    "trade_hash": ZERO_HASH,
                }
                stream.write((json.dumps(row, sort_keys=True) + "\n").encode())
            index[config["id"]] = {"offset": begin, "end": stream.tell()}
            results.append(
                {
                    "id": config["id"],
                    "step_pct": config["step_pct"],
                    "tp_pct": config["tp_pct"],
                    "trade_hash": ZERO_HASH,
                    "total": _metrics(7),
                    "venues": [{**_metrics(), "venue": venue} for venue in VENUES],
                }
            )
    _write(engine / "pairs.index.json", index)
    summary = {
        "status": "COMPLETE",
        "mode": "full",
        "source_count": len(sources),
        "configuration_count": len(configs),
        "input_records": len(sources),
        "processed_records": len(sources),
        "duration_stopped": False,
        "audit": {
            "source_sha256_verified": True,
            "records": len(sources),
            "bytes": len(sources) * 24,
        },
        "build_sha256": binary_sha,
        "resources": {"threads": 32, "block_bytes": 1024**2},
        "configurations": results,
    }
    _write(engine / "summary.json", summary)
    _write(
        job / "execution.json",
        {
            "exit_code": 0,
            "summary_sha256": _hash(engine / "summary.json"),
            "plan_sha256": _hash(job / "plan.json"),
            "binary_sha256": binary_sha,
        },
    )
    return job, selection


def test_verifies_complete_exact_roster(tmp_path: Path) -> None:
    job, selection = _fixture(tmp_path)
    report = verify_job(job, selection, rehash_sources=True)
    assert report["status"] == "VERIFIED"
    assert report["pair_rows"] == 540 * 7
    assert report["processed_records"] == 7
    assert report["threads"] == 32


def test_rejects_changed_pair_finances(tmp_path: Path) -> None:
    job, selection = _fixture(tmp_path)
    path = job / "engine/pairs.jsonl"
    data = path.read_bytes().replace(b'"buy_count": 1', b'"buy_count": 2', 1)
    path.write_bytes(data)
    with pytest.raises(ValueError, match="aggregate buy_count mismatch"):
        verify_job(job, selection)


def test_rejects_incomplete_records(tmp_path: Path) -> None:
    job, selection = _fixture(tmp_path)
    path = job / "engine/summary.json"
    summary = json.loads(path.read_text())
    summary["processed_records"] -= 1
    _write(path, summary)
    execution_path = job / "execution.json"
    execution = json.loads(execution_path.read_text())
    execution["summary_sha256"] = _hash(path)
    _write(execution_path, execution)
    with pytest.raises(ValueError, match="record coverage incomplete"):
        verify_job(job, selection)


def test_rehash_catches_changed_source_with_same_end_price(tmp_path: Path) -> None:
    job, selection = _fixture(tmp_path)
    path = tmp_path / "okx.bin"
    path.write_bytes(struct.pack("<qffff", LAST_MS, 2.0, 2.0, 1.0, 1.0))
    with pytest.raises(ValueError, match="source hash changed"):
        verify_job(job, selection, rehash_sources=True)


def test_accepts_completed_compatible_resume(tmp_path: Path) -> None:
    job, selection = _fixture(tmp_path)
    receipt_path, plan_path = job / "receipt.json", job / "plan.json"
    receipt, plan = json.loads(receipt_path.read_text()), json.loads(plan_path.read_text())
    receipt.update(action="resume", completed_stage="full")
    plan["resume"] = True
    _write(receipt_path, receipt)
    _write(plan_path, plan)
    (job / "engine/plan.json").unlink()
    execution_path = job / "execution.json"
    execution = json.loads(execution_path.read_text())
    execution["plan_sha256"] = _hash(plan_path)
    _write(execution_path, execution)
    report = verify_job(job, selection, rehash_sources=True)
    assert report["status"] == "VERIFIED"
    assert report["resumed"] is True


def _new_policy(job: Path) -> None:
    policy = {
        "id": "optimized-600s-v1",
        "endurance_seconds": 600,
        "minimum_checkpoints": 2,
        "recovery_required": True,
    }
    for name in ("study-manifest.json", "admission.json", "receipt.json"):
        path = job / name
        value = json.loads(path.read_text())
        value["acceptance_policy"] = policy
        _write(path, value)
    receipt_path = job / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    for stage, evidence in receipt["evidence"].items():
        path = Path(evidence["receipt"])
        proof = json.loads(path.read_text())
        proof["acceptance_policy"] = policy
        if stage == "validate":
            proof["recovery"] = {"passed": True, "checks": [{"name": "resume", "passed": True}]}
        if stage == "endurance":
            summary_path = path.parent / "endurance-engine/summary.json"
            _write(summary_path, {"timings": {"replay_seconds": 601}, "checkpoint_count": 2})
            _write(
                path.parent / "execution.json",
                {"exit_code": 0, "summary_sha256": _hash(summary_path)},
            )
            proof["result_dir"] = str(summary_path.parent)
        _write(path, proof)
        evidence["sha256"] = _hash(path)
    _write(receipt_path, receipt)


def test_new_policy_requires_observed_duration_checkpoints_and_recovery(tmp_path: Path) -> None:
    job, selection = _fixture(tmp_path)
    _new_policy(job)
    assert verify_job(job, selection)["status"] == "STRUCTURE_VERIFIED"
    path = tmp_path / "endurance-engine/summary.json"
    _write(path, {"timings": {"replay_seconds": 601}, "checkpoint_count": 1})
    _write(tmp_path / "execution.json", {"exit_code": 0, "summary_sha256": _hash(path)})
    with pytest.raises(ValueError, match="duration/checkpoints"):
        verify_job(job, selection)


def test_new_policy_cannot_borrow_legacy_stage_receipt(tmp_path: Path) -> None:
    job, selection = _fixture(tmp_path)
    _new_policy(job)
    path = tmp_path / "rehearsal-receipt.json"
    proof = json.loads(path.read_text())
    proof.pop("acceptance_policy")
    _write(path, proof)
    receipt = json.loads((job / "receipt.json").read_text())
    receipt["evidence"]["rehearsal"]["sha256"] = _hash(path)
    _write(job / "receipt.json", receipt)
    with pytest.raises(ValueError, match="rehearsal policy"):
        verify_job(job, selection)
