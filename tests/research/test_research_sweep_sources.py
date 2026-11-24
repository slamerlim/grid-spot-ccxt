"""Research preflight keeps every requested source and never guesses missing rules."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from exchange.test_ccxt import FakeWire, payloads

from grid.exchange.ccxt_transport import WireReply
from scripts import research_sweep_sources as sources

START = 1704067200000


@pytest.fixture(autouse=True)
def no_host_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "CPP_SCANNERS", ())


def write_source(
    root: Path, name: str, prices: list[tuple[int, float, float, float, float]]
) -> Path:
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_bytes(b"".join(sources.RECORD.pack(*row) for row in prices))
    return path


def market(symbol: str = "BTC/USDT") -> dict[str, Any]:
    return {
        "id": symbol.replace("/", "_"),
        "symbol": symbol,
        "base": symbol.split("/")[0],
        "quote": "USDT",
        "spot": True,
        "type": "spot",
        "contract": False,
        "precision_mode": "tick_size",
        "price_precision": "0.01",
        "amount_precision": "0.001",
        "limits": {"amount": {"min": "0.001"}, "cost": {"min": "5"}},
    }


def snapshot(directory: Path, entries: dict[str, Any]) -> None:
    directory.mkdir(exist_ok=True)
    (directory / "htx.json").write_text(
        json.dumps(
            {
                "schema": 2,
                "exchange": "htx",
                "fetched_utc": "2026-11-19T00:00:00Z",
                "ccxt_version": sources.CCXT_VERSION,
                "markets": entries,
            }
        )
    )


def test_inventory_keeps_short_gapped_unicode_and_up_suffix(tmp_path: Path) -> None:
    root = tmp_path / "crypto_1m"
    points = [(START, 100.0, 100.0, 100.0, 100.0)]
    for name in ("htx_USDT_JUP.bin", "xt_USDT_牛来.bin", "okx_USDT_A_B.bin"):
        write_source(root, name, points)
    write_source(root, "htx_BTC_ETH.bin", points)
    write_source(root, "binance_USDT_BTC.bin", points)
    rows = sources.inventory_sources(tmp_path)
    assert [row["id"] for row in rows] == ["htx:JUP/USDT", "okx:A_B/USDT", "xt:牛来/USDT"]
    assert all(row["records"] == 1 and not row["inventory_errors"] for row in rows)


def test_source_audit_hashes_all_records_and_keeps_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sources, "CHUNK_RECORDS", 2)
    path = write_source(
        tmp_path,
        "htx_USDT_BTC.bin",
        [
            (START, 100, 101, 99, 100),
            (START + 60000, 100, 100, 100, 100),
            (START + 240000, 99, 101, 98, 100),
        ],
    )
    before = path.read_bytes()
    row = sources.inventory_sources(tmp_path)[0]
    result = sources.audit_source(row, use_numpy=False)
    assert result["complete"] and result["errors"] == {}
    assert result["gap_intervals"] == 1 and result["missing_minutes"] == 2
    assert result["largest_gap_minutes"] == 2
    assert result["sha256"] == hashlib.sha256(before).hexdigest()
    assert path.read_bytes() == before


def test_corrupt_record_and_source_change_are_visible(tmp_path: Path) -> None:
    path = write_source(
        tmp_path,
        "htx_USDT_BTC.bin",
        [
            (START, 100, 99, 98, 100),
            (START, float("nan"), 101, 99, 100),
        ],
    )
    row = sources.inventory_sources(tmp_path)[0]
    audit = sources.audit_source(row, use_numpy=False)
    assert audit["errors"]["invalid_ohlc"] == 2
    assert audit["errors"]["invalid_price"] == 1
    assert audit["errors"]["nonincreasing_timestamp"] == 1
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="identity changed"):
        sources.audit_source(row)


def fake_scanner(tmp_path: Path, code: str) -> Path:
    path = tmp_path / "scanner"
    path.write_text(f"#!{sys.executable}\n" + code)
    path.chmod(0o700)
    return path


def test_cpp_fallback_retains_complete_hash_and_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_source(tmp_path, "htx_USDT_BTC.bin", [(START, 100, 101, 99, 100)])
    row = sources.inventory_sources(tmp_path)[0]
    stats = {
        "schema": "grid-source-scan-v1",
        "complete": True,
        "bytes": 24,
        "records": 1,
        "accepted": 1,
        "rejected": 0,
        "reasons": {},
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "first_ms": START,
        "last_ms": START,
        "gap_count": 0,
        "missing_minutes": 0,
    }
    scanner = fake_scanner(tmp_path, "print(" + repr(json.dumps(stats)) + ")\n")
    monkeypatch.setattr(sources, "CPP_SCANNERS", (scanner,))

    def unavailable(name: str) -> None:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(importlib, "import_module", unavailable)
    result = sources.audit_source(row)
    assert result["complete"] and result["errors"] == {}
    assert result["sha256"] == stats["sha256"] and result["records"] == 1
    assert result["largest_gap_minutes"] is None
    assert result["scanner"]["sha256"] == hashlib.sha256(scanner.read_bytes()).hexdigest()


def test_cpp_permissive_calendar_does_not_admit_future_records(tmp_path: Path) -> None:
    future = 4102444800000
    path = write_source(tmp_path, "htx_USDT_BTC.bin", [(future, 100, 101, 99, 100)])
    row = sources.inventory_sources(tmp_path)[0]
    stats = {
        "schema": "grid-source-scan-v1",
        "complete": True,
        "bytes": 24,
        "records": 1,
        "accepted": 1,
        "rejected": 0,
        "reasons": {},
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "first_ms": future,
        "last_ms": future,
        "gap_count": 0,
        "missing_minutes": 0,
    }
    scanner = fake_scanner(tmp_path, "print(" + repr(json.dumps(stats)) + ")\n")
    assert sources._audit_cpp(row, scanner)["errors"] == {"future_timestamp_present": 1}


@pytest.mark.parametrize(
    "code, reason",
    [
        ("print('x' * 100000)\n", "output bound"),
        ("import time; time.sleep(30)\n", "timeout"),
        ("print('{}')\n", "malformed"),
    ],
)
def test_cpp_scanner_has_bounded_output_deadline_and_required_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str, reason: str
) -> None:
    write_source(tmp_path, "htx_USDT_BTC.bin", [(START, 100, 101, 99, 100)])
    row = sources.inventory_sources(tmp_path)[0]
    scanner = fake_scanner(tmp_path, code)
    monkeypatch.setattr(sources, "SCANNER_SECONDS", 0.25)
    with pytest.raises(ValueError, match=reason):
        sources._audit_cpp(row, scanner)


def test_missing_rules_block_whole_universe_without_removing_source(tmp_path: Path) -> None:
    root, snapshots, output = tmp_path / "data", tmp_path / "rules", tmp_path / "out"
    for name in ("htx_USDT_BTC.bin", "htx_USDT_JUP.bin"):
        write_source(root, name, [(START, 100, 100, 100, 100)])
    snapshot(snapshots, {"USDT_BTC": market()})
    result = sources.prepare_sources(root, output, [snapshots])
    assert result["status"] == "BLOCKED" and not result["universe_reduced"]
    assert result["totals"]["sources"] == 2 and result["totals"]["rules_ready"] == 1
    assert result["blockers"] == [
        {"id": "htx:JUP/USDT", "kind": "rules", "reason": "no matching saved market rules"}
    ]
    assert all(row["sha256"] for row in result["sources"])
    with pytest.raises(FileExistsError):
        sources.prepare_sources(root, output, [snapshots])


def test_ready_manifest_requires_rules_and_complete_audit(tmp_path: Path) -> None:
    root, snapshots = tmp_path / "data", tmp_path / "rules"
    write_source(root, "htx_USDT_JUP.bin", [(START, 100, 100, 100, 100)])
    snapshot(snapshots, {"USDT_JUP": market("JUP/USDT")})
    ready = sources.prepare_sources(root, tmp_path / "ready", [snapshots])
    assert ready["status"] == "READY"
    inventory = sources.prepare_sources(root, tmp_path / "inventory", [snapshots], audit=False)
    assert inventory["status"] == "BLOCKED"
    assert "not requested" in inventory["blockers"][0]["reason"]


def test_explicit_selection_retains_seven_venues_and_all_exclusion_reasons(tmp_path: Path) -> None:
    rows = [
        {
            "id": f"{venue}:BTC/USDT",
            "venue": venue,
            "symbol": "BTC/USDT",
            "path": f"/immutable/{venue}.bin",
            "audit_accepted": True,
            "sha256": "a" * 64,
            "bytes": 24,
            "records": 1,
            "rules": {"min_amount": "1", "min_cost": "5"},
            "rules_snapshot": {"snapshot_sha256": "b" * 64},
        }
        for venue in sources.VENUES
    ]
    rows.append(
        {
            **rows[0],
            "id": "okx:UNKNOWN/USDT",
            "symbol": "UNKNOWN/USDT",
            "path": "/immutable/unknown.bin",
            "rules": None,
            "rules_snapshot": None,
        }
    )
    parent = tmp_path / "original.json"
    parent.write_text(
        json.dumps(
            {
                "schema": "grid-research-sweep-sources-v1",
                "status": "BLOCKED",
                "sources": rows,
                "blockers": [
                    {"id": "okx:UNKNOWN/USDT", "kind": "rules", "reason": "min_amount unknown"}
                ],
                "totals": {"sources": len(rows)},
            }
        )
    )
    selected = sources.select_verified_sources(parent, tmp_path / "selection")
    assert selected["status"] == "READY"
    assert selected["totals"]["selected"] == 7
    assert selected["excluded"] == [
        {
            "id": "okx:UNKNOWN/USDT",
            "venue": "okx",
            "sha256": "a" * 64,
            "reason": "min_amount unknown",
        }
    ]
    sources.verify_selection(selected)
    corrupted = copy.deepcopy(selected)
    corrupted["excluded"] = []
    with pytest.raises(ValueError, match="roster differs"):
        sources.verify_selection(corrupted)
    corrupted = copy.deepcopy(selected)
    corrupted["totals"]["selected_records"] += 1
    with pytest.raises(ValueError, match="roster differs"):
        sources.verify_selection(corrupted)
    damaged_parent = json.loads(parent.read_text())
    damaged_parent["blockers"].append(
        {"id": "okx:UNKNOWN/USDT", "kind": "data", "reason": "changed source"}
    )
    parent.write_text(json.dumps(damaged_parent))
    damaged = sources.select_verified_sources(parent, tmp_path / "data_blocked")
    assert damaged["status"] == "BLOCKED"
    forged = {**damaged, "status": "READY", "blockers": []}
    with pytest.raises(ValueError, match="roster differs"):
        sources.verify_selection(forged)
    rows[0]["rules"] = None
    parent.write_text(
        json.dumps(
            {
                "schema": "grid-research-sweep-sources-v1",
                "sources": rows,
                "blockers": [
                    {"id": "okx:UNKNOWN/USDT", "kind": "rules", "reason": "min_amount unknown"},
                    {"id": "okx:BTC/USDT", "kind": "rules", "reason": "min_cost unknown"},
                ],
                "totals": {"sources": len(rows)},
            }
        )
    )
    blocked = sources.select_verified_sources(parent, tmp_path / "missing_venue")
    assert blocked["status"] == "BLOCKED"
    assert "okx" in blocked["blockers"][0]["reason"]


def test_selection_does_not_exclude_corrupt_data_as_unknown_rules(tmp_path: Path) -> None:
    parent = tmp_path / "original.json"
    parent.write_text(
        json.dumps(
            {
                "schema": "grid-research-sweep-sources-v1",
                "sources": [
                    {
                        "id": "htx:BTC/USDT",
                        "venue": "htx",
                        "audit_accepted": False,
                        "rules": None,
                    }
                ],
                "blockers": [{"id": "htx:BTC/USDT", "kind": "data", "reason": "invalid price"}],
                "totals": {"sources": 1},
            }
        )
    )
    selected = sources.select_verified_sources(parent, tmp_path / "selected")
    assert selected["status"] == "BLOCKED"
    assert selected["totals"]["excluded"] == 0
    assert any(item["kind"] == "data" for item in selected["blockers"])


def gap_parent(tmp_path: Path, *, extra: bool = True) -> Path:
    """All seven venues were admitted; only five have continuous own histories."""
    dataset = tmp_path / "data"
    for index, venue in enumerate(sources.VENUES):
        start = START + index * 60_000
        stamps = [start, start + (120_000 if venue in {"bitfinex", "xt"} else 60_000)]
        write_source(dataset, f"{venue}_USDT_BTC.bin", [(t, 100, 101, 99, 100) for t in stamps])
    if extra:
        write_source(dataset, "htx_USDT_SHORT.bin", [(START, 100, 100, 100, 100)])
        write_source(
            dataset,
            "htx_USDT_GAP.bin",
            [
                (START, 100, 100, 100, 100),
                (START + 60_000, 100, 100, 100, 100),
                (START + 180_000, 100, 100, 100, 100),
            ],
        )
    rows = sources.inventory_sources(dataset)
    for row in rows:
        audit = sources.audit_source(row, use_numpy=False)
        row.update(
            audit=audit,
            audit_accepted=True,
            sha256=audit["sha256"],
            rules={"min_amount": "1", "min_cost": "5"},
            rules_snapshot={"snapshot_sha256": "a" * 64},
        )
    prepared = tmp_path / "preparation.json"
    prepared.write_text(
        json.dumps(
            {
                "schema": "grid-research-sweep-sources-v1",
                "status": "READY",
                "sources": rows,
                "blockers": [],
                "totals": {"sources": len(rows)},
            }
        )
    )
    sources.select_verified_sources(prepared, tmp_path / "parent")
    return tmp_path / "parent" / "manifest.json"


def test_gap_free_selection_keeps_own_spans_exact_rows_and_exclusion_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sources, "CHUNK_RECORDS", 2)
    parent_path = gap_parent(tmp_path)
    original = parent_path.read_bytes()
    progress = []
    selected = sources.select_gap_free_sources(
        parent_path,
        tmp_path / "selected",
        audit_threads=2,
        progress=lambda done, total, name: progress.append((done, total, name)),
    )
    assert selected["status"] == "READY"
    assert selected["expected_venues"] == list(sources.GAP_FREE_VENUES)
    assert selected["totals"]["selected"] == 6
    assert selected["totals"]["excluded"] == 3
    assert selected["research_readiness"] == "PENDING_PRICE_AND_HISTORICAL_RULES_REVIEW"
    assert selected["historical_rules_verified"] is False
    parent = json.loads(original)
    assert selected["sources"] == [
        row for row in parent["sources"] if row["audit"]["gap_intervals"] == 0
    ]
    assert selected["source_manifest_sha256"] == hashlib.sha256(original).hexdigest()
    assert len(progress) == len(parent["sources"])
    assert progress[-1][:2] == (9, 9)
    assert {row["reason"] for row in selected["excluded"]} == {"missing_minutes_in_own_span"}
    assert (
        next(r for r in selected["excluded"] if r["id"] == "htx:GAP/USDT")["missing_minutes"] == 1
    )
    short = next(row for row in selected["coverage"]["sources"] if row["id"] == "htx:SHORT/USDT")
    assert short["duration_minutes"] == 1 and short["full_utc_months"] == 0
    assert parent_path.read_bytes() == original
    sources.verify_gap_free_selection(selected)
    sources.verify_selection(parent)
    with pytest.raises(FileExistsError):
        sources.select_gap_free_sources(parent_path, tmp_path / "selected")


@pytest.mark.parametrize("use_numpy", [False, True])
@pytest.mark.parametrize("minutes", [1, 60_000])
def test_gap_audit_crosses_read_boundaries_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_numpy: bool,
    minutes: int,
) -> None:
    monkeypatch.setattr(sources, "CHUNK_RECORDS", 2)
    write_source(
        tmp_path,
        "htx_USDT_BTC.bin",
        [
            (START, 100, 101, 99, 100),
            (START + 60_000, 100, 101, 99, 100),
            (START + (minutes + 2) * 60_000, 100, 101, 99, 100),
        ],
    )
    row = sources.inventory_sources(tmp_path)[0]
    audit = sources.audit_source(row, use_numpy=use_numpy)
    assert audit["errors"] == {} and audit["complete"] is True
    assert audit["gap_intervals"] == 1 and audit["missing_minutes"] == minutes


@pytest.mark.parametrize("timestamps", [[START, START], [START + 60_000, START]])
def test_duplicate_or_reversed_timestamp_is_not_a_gap_filter(
    tmp_path: Path,
    timestamps: list[int],
) -> None:
    write_source(tmp_path, "htx_USDT_BTC.bin", [(t, 100, 100, 100, 100) for t in timestamps])
    row = sources.inventory_sources(tmp_path)[0]
    audit = sources.audit_source(row, use_numpy=False)
    assert audit["errors"]["nonincreasing_timestamp"] == 1
    row.update(audit_accepted=True, sha256=audit["sha256"])
    with pytest.raises(ValueError, match="structural"):
        sources._gap_audit(row, audit)


@pytest.mark.parametrize(
    "field, value",
    [
        ("complete", False),
        ("gap_intervals", None),
        ("missing_minutes", True),
        ("gap_intervals", -1),
        ("records", 1.0),
        ("sha256", "f" * 64),
        ("errors", {"nonincreasing_timestamp": 1}),
    ],
)
def test_gap_selection_blocks_unknown_changed_or_invalid_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    parent = gap_parent(tmp_path)
    original = sources.audit_source

    def faulty(row: Any) -> dict[str, Any]:
        result = original(row, use_numpy=False)
        if row["id"] == "htx:BTC/USDT":
            result[field] = value
        return result

    monkeypatch.setattr(sources, "audit_source", faulty)
    result = sources.select_gap_free_sources(parent, tmp_path / "selected")
    assert result["status"] == "BLOCKED"
    assert any(item["id"] == "htx:BTC/USDT" for item in result["blockers"])
    with pytest.raises(ValueError, match="differs"):
        sources.verify_gap_free_selection(result)


def test_gap_selection_refuses_changed_source_and_parent(tmp_path: Path) -> None:
    parent_path = gap_parent(tmp_path)
    selected = sources.select_gap_free_sources(parent_path, tmp_path / "selected")
    path = Path(selected["sources"][0]["path"])
    path.write_bytes(path.read_bytes())  # Same bytes but changed source identity.
    with pytest.raises(ValueError, match="changed after audit"):
        sources.verify_gap_free_selection(selected)
    result = sources.select_gap_free_sources(parent_path, tmp_path / "after_change")
    assert result["status"] == "BLOCKED"
    parent_path.write_bytes(parent_path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="parent changed"):
        sources.verify_gap_free_selection(selected)


@pytest.mark.parametrize(
    "section", ["sources", "source_audits", "coverage", "totals", "expected_venues"]
)
def test_gap_selection_verification_reconstructs_whole_derivation(
    tmp_path: Path, section: str
) -> None:
    parent_path = gap_parent(tmp_path)
    selected = sources.select_gap_free_sources(parent_path, tmp_path / "selected")
    if section in {"sources", "source_audits", "expected_venues"}:
        selected[section] = selected[section][:-1]
    elif section == "coverage":
        selected[section]["sources"][0]["full_utc_months"] = 1
    else:
        selected[section]["selected"] = 6.0
    with pytest.raises(ValueError):
        sources.verify_gap_free_selection(selected)


def test_gap_selection_requires_exact_five_venue_coverage(tmp_path: Path) -> None:
    parent_path = gap_parent(tmp_path)
    parent = json.loads(parent_path.read_text())
    prepared_path = Path(parent["source_manifest_path"])
    prepared = json.loads(prepared_path.read_text())
    row = next(row for row in prepared["sources"] if row["venue"] == "bitfinex")
    Path(row["path"]).write_bytes(sources.RECORD.pack(START, 100, 100, 100, 100))
    updated = next(r for r in sources.inventory_sources(tmp_path / "data") if r["id"] == row["id"])
    audit = sources.audit_source(updated, use_numpy=False)
    row.update(updated, audit=audit, audit_accepted=True, sha256=audit["sha256"])
    prepared_path.write_text(json.dumps(prepared))
    sources.select_verified_sources(prepared_path, tmp_path / "extra_parent")
    result = sources.select_gap_free_sources(
        tmp_path / "extra_parent" / "manifest.json", tmp_path / "new"
    )
    assert result["status"] == "BLOCKED"
    assert "bitfinex" in result["totals"]["by_venue"]
    assert any(item["kind"] == "coverage" for item in result["blockers"])


def test_gap_selection_missing_venue_does_not_relax_required_roster(tmp_path: Path) -> None:
    parent_path = gap_parent(tmp_path)
    parent = json.loads(parent_path.read_text())
    prepared_path = Path(parent["source_manifest_path"])
    prepared = json.loads(prepared_path.read_text())
    row = next(row for row in prepared["sources"] if row["venue"] == "okx")
    Path(row["path"]).write_bytes(
        b"".join(sources.RECORD.pack(t, 100, 100, 100, 100) for t in (START, START + 120_000))
    )
    updated = next(r for r in sources.inventory_sources(tmp_path / "data") if r["id"] == row["id"])
    audit = sources.audit_source(updated, use_numpy=False)
    row.update(updated, audit=audit, audit_accepted=True, sha256=audit["sha256"])
    prepared_path.write_text(json.dumps(prepared))
    sources.select_verified_sources(prepared_path, tmp_path / "missing_parent")
    result = sources.select_gap_free_sources(
        tmp_path / "missing_parent" / "manifest.json", tmp_path / "new"
    )
    assert result["status"] == "BLOCKED"
    assert "okx" not in result["totals"]["by_venue"]


def test_gap_selection_checks_early_sources_again_at_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_path = gap_parent(tmp_path)
    rows = json.loads(parent_path.read_text())["sources"]
    original = sources.audit_source

    def mutate_early(row: Any) -> dict[str, Any]:
        audited = original(row, use_numpy=False)
        if row["id"] == rows[-1]["id"]:
            early = Path(rows[0]["path"])
            early.write_bytes(early.read_bytes())
        return audited

    monkeypatch.setattr(sources, "audit_source", mutate_early)
    result = sources.select_gap_free_sources(parent_path, tmp_path / "changed", audit_threads=1)
    assert result["status"] == "BLOCKED"
    assert any("changed after full audit" in row["reason"] for row in result["blockers"])


def test_gap_selection_verifier_does_not_rehash_full_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = gap_parent(tmp_path)
    selected = sources.select_gap_free_sources(parent, tmp_path / "selected")

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("load must use saved complete audit plus source identities")

    monkeypatch.setattr(sources, "audit_source", unexpected)
    sources.verify_gap_free_selection(selected)


def test_gap_selection_parent_hash_and_audit_metadata_cannot_be_omitted(tmp_path: Path) -> None:
    parent = gap_parent(tmp_path)
    selected = sources.select_gap_free_sources(parent, tmp_path / "selected")
    for key in ("source_manifest_sha256", "audit"):
        broken = copy.deepcopy(selected)
        del broken[key]
        with pytest.raises(ValueError, match="provenance"):
            sources.verify_gap_free_selection(broken)


def test_gap_selection_cannot_bind_code_changed_during_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = gap_parent(tmp_path)
    auditor = tmp_path / "auditor.py"
    auditor.write_text("original auditor")
    monkeypatch.setattr(sources, "__file__", str(auditor))

    def change_auditor(done: int, total: int, name: str) -> None:
        if done == total:
            auditor.write_text("changed auditor")

    with pytest.raises(ValueError, match="auditor changed"):
        sources.select_gap_free_sources(parent, tmp_path / "selected", progress=change_auditor)
    assert not (tmp_path / "selected" / "manifest.json").exists()


def test_gap_coverage_full_months_and_early_end_have_separate_definitions() -> None:
    jan_end = START + 31 * 86_400_000
    covered = sources._gap_coverage(
        {
            "id": "htx:BTC/USDT",
            "venue": "htx",
            "first_ms": START,
            "last_ms": jan_end - 60_000,
        },
        jan_end + 60_000,
    )
    assert covered["full_utc_months"] == 1 and covered["complete_utc_days"] == 31
    assert covered["ends_before_archive_last_day"] is True
    assert covered["endpoint_lag_minutes"] == 2
    covered = sources._gap_coverage(
        {
            "id": "htx:BTC/USDT",
            "venue": "htx",
            "first_ms": START + 60_000,
            "last_ms": jan_end - 60_000,
        },
        jan_end - 60_000,
    )
    assert covered["full_utc_months"] == 0 and covered["complete_utc_days"] == 30
    assert covered["ends_before_archive_last_day"] is False


def test_unknown_minimum_never_defaults_to_zero() -> None:
    row = market()
    row["limits"]["cost"]["min"] = None
    with pytest.raises(ValueError, match="OKX spot instrument rules"):
        sources.normalize_rules("okx", "BTC/USDT", row)
    row["info"] = {
        "instType": "SPOT",
        "minSz": "0.001",
        "lotSz": "0.001",
        "tickSz": "0.01",
    }
    rules = sources.normalize_rules("okx", "BTC/USDT", row)
    assert rules["min_cost"] == "0"
    assert rules["known_absent_evidence"]["min_cost"] == sources.NO_SEPARATE_COST_MINIMUM["okx"]
    row["info"]["minSz"] = "0.002"
    with pytest.raises(ValueError, match="disagree"):
        sources.normalize_rules("okx", "BTC/USDT", row)


def test_bingx_native_minimum_quantity_is_exact_and_conflicts_fail() -> None:
    row = market()
    row["limits"]["amount"]["min"] = None
    row["info"] = {"minQty": "0.0001826", "minNotional": "5"}
    assert sources.normalize_rules("bingx", "BTC/USDT", row)["min_amount"] == "0.0001826"
    row["info"]["minQty"] = 0.0001826
    with pytest.raises(ValueError, match="exact value unavailable"):
        sources.normalize_rules("bingx", "BTC/USDT", row)
    row["info"]["minQty"] = "0.0001826"
    row["info"]["minNotional"] = "6"
    with pytest.raises(ValueError, match="disagrees"):
        sources.normalize_rules("bingx", "BTC/USDT", row)


def test_existing_funded_rejection_does_not_hide_known_research_minima() -> None:
    row = market("JUP/USDT")
    row["rules_error"] = "POLICY_REFUSED"
    assert sources.normalize_rules("gate", "JUP/USDT", row)["min_cost"] == "5"
    row["spot"] = None
    with pytest.raises(ValueError, match="spot identity"):
        sources.normalize_rules("gate", "JUP/USDT", row)


def test_bitfinex_effective_price_and_amount_precision_are_distinct() -> None:
    row = market()
    row.update(
        precision_mode=3,
        price_precision=5,
        amount_precision=8,
        exchange_listed=True,
        info=[None, None, None, "0.001"],
    )
    result = sources.normalize_rules("bitfinex", "BTC/USDT", row)
    assert result == {
        "price_mode": "significant",
        "price_tick": "0.00000001",
        "price_digits": 5,
        "max_price_decimals": 8,
        "amount_tick": "0.00000001",
        "min_amount": "0.001",
        "min_cost": "5",
    }
    row["amount_precision"] = "8.5"
    with pytest.raises(ValueError):
        sources.normalize_rules("bitfinex", "BTC/USDT", row)


def test_bitfinex_requires_exchange_listing_and_native_minimum() -> None:
    row = market()
    row.update(precision_mode=3, price_precision=5, amount_precision=8)
    row["limits"]["cost"]["min"] = None
    row["info"] = [None, None, None, "0.001"]
    with pytest.raises(ValueError, match="exchange spot listing"):
        sources.normalize_rules("bitfinex", "BTC/USDT", row)
    row["exchange_listed"] = True
    rules = sources.normalize_rules("bitfinex", "BTC/USDT", row)
    assert rules["min_cost"] == "0"
    row["info"][3] = "0.002"
    with pytest.raises(ValueError, match="disagrees"):
        sources.normalize_rules("bitfinex", "BTC/USDT", row)


def test_xt_explicit_absence_and_nonzero_offset() -> None:
    row = market()
    row["limits"]["cost"]["min"] = None
    row["info"] = {"filters": [{"filter": "QUOTE_QTY", "min": None}]}
    assert sources.normalize_rules("xt", "BTC/USDT", row)["min_cost"] == "0"
    row["info"]["filters"].append({"filter": "QUANTITY", "min": "0.0005"})
    with pytest.raises(ValueError, match="offset"):
        sources.normalize_rules("xt", "BTC/USDT", row)


@pytest.mark.parametrize("venue", sources.VENUES)
def test_pinned_ccxt_collects_only_one_allowed_spot_catalog(
    venue: str,
    tmp_path: Path,
    async_sockets: None,
) -> None:
    async def run() -> None:
        payload = copy.deepcopy(payloads(venue)[0])
        if venue == "gate":
            payload = [payload]
        replies: list[WireReply | Exception] = [WireReply(200, json.dumps(payload).encode())]
        if venue == "bitfinex":
            replies.append(WireReply(200, b'[["BTCUST"]]'))
        wire = FakeWire(replies)
        result = await sources.collect_catalog(venue, tmp_path, wire=wire)
        assert result["status"] == "COLLECTED", result.get("error")
        expected = 2 if venue == "bitfinex" else 1
        assert len(wire.urls) == len(result["requests"]) == expected
        assert wire.closed
        assert "USDT_BTC" in result["markets"]
        assert result["markets"]["USDT_BTC"]["spot"] is True
        if venue == "bitfinex":
            assert result["markets"]["USDT_BTC"]["exchange_listed"] is True
        sources.check_catalog_url(venue, wire.urls[0])
        if venue == "bitfinex":
            sources.check_catalog_url(venue, wire.urls[1])
        assert len(list(tmp_path.glob("*-response-1.bin"))) == 1

    asyncio.run(run())


def test_catalog_refuses_redirect_private_extra_query_and_post() -> None:
    for url, method in (
        ("https://www.okx.com/api/v5/account/balance", "GET"),
        ("https://www.okx.com/api/v5/public/instruments?instType=SWAP", "GET"),
        ("https://www.okx.com/api/v5/public/instruments?instType=SPOT&instId=BTC-USDT", "GET"),
        ("https://www.okx.com/api/v5/public/instruments?instType=SPOT", "POST"),
        ("https://www.okx.com.evil.test/api/v5/public/instruments?instType=SPOT", "GET"),
    ):
        with pytest.raises(ValueError):
            sources.check_catalog_url("okx", url, method)
    for url in (
        sources.BITFINEX_SPOT_URL + "?symbol=BTCUST",
        "https://api.bitfinex.com/v2/conf/pub:list:pair:exchange",
    ):
        with pytest.raises(ValueError):
            sources.check_catalog_url("bitfinex", url)


def test_failed_catalog_keeps_evidence_without_retry(tmp_path: Path, async_sockets: None) -> None:
    async def run() -> None:
        wire = FakeWire([WireReply(429, b'{"error":"rate limited"}')])
        result = await sources.collect_catalog("okx", tmp_path, wire=wire)
        assert result["status"] == "FAILED"
        assert len(wire.urls) == 1
        assert result["requests"][0]["status"] == 429
        assert result["markets"] == {}
        assert list(tmp_path.glob("okx-*.json"))

    asyncio.run(run())
