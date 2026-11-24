"""Reviewed exclusions keep an exact, immutable subset of the verified gap-free roster."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

from scripts import research_sweep as runner
from scripts import research_sweep_sources as sources


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


@pytest.fixture
def reviewed_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setattr(sources, "CPP_SCANNERS", ())
    data = tmp_path / "data"
    data.mkdir()
    start = 1_704_067_200_000
    for venue in sources.VENUES:
        step = 120_000 if venue in {"bitfinex", "xt"} else 60_000
        (data / f"{venue}_USDT_BTC.bin").write_bytes(
            b"".join(
                sources.RECORD.pack(timestamp, 100, 101, 99, 100)
                for timestamp in (start, start + step)
            )
        )
    (data / "htx_USDT_UNCLEAR.bin").write_bytes(sources.RECORD.pack(start, 10, 10, 10, 10))
    rows = sources.inventory_sources(data)
    for row in rows:
        audit = sources.audit_source(row, use_numpy=False)
        row.update(
            audit=audit,
            audit_accepted=True,
            sha256=audit["sha256"],
            rules={"min_amount": "1", "min_cost": "5"},
            rules_snapshot={"snapshot_sha256": "a" * 64},
        )
    original = tmp_path / "original.json"
    write_json(
        original,
        {
            "schema": "grid-research-sweep-sources-v1",
            "status": "READY",
            "sources": rows,
            "blockers": [],
            "totals": {"sources": len(rows)},
        },
    )
    sources.select_verified_sources(original, tmp_path / "verified")
    gap = sources.select_gap_free_sources(
        tmp_path / "verified/manifest.json", tmp_path / "gap", audit_threads=1
    )
    parent = tmp_path / "gap/manifest.json"
    evidence = tmp_path / "screening.json"
    write_json(evidence, {"checked": [row["id"] for row in gap["sources"]]})
    review = tmp_path / "review.json"
    write_json(
        review,
        {
            "schema": sources.REVIEW_SCHEMA,
            "source_manifest_path": str(parent),
            "source_manifest_sha256": hashlib.sha256(parent.read_bytes()).hexdigest(),
            "policy": {
                "selection_not_based_on_pnl": True,
                "screening_thresholds": {"unresolved_identity": "exclude"},
            },
            "evidence_files": [
                {"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
            ],
            "decisions": [
                {
                    "id": row["id"],
                    "sha256": row["sha256"],
                    "exclude": row["symbol"] == "UNCLEAR/USDT",
                    "reasons": ["identity unresolved"] if row["symbol"] == "UNCLEAR/USDT" else [],
                    "evidence": {"row": index},
                }
                for index, row in enumerate(gap["sources"])
            ],
        },
    )
    return parent, review


def test_reviewed_selection_is_exact_and_preserves_ancestry(
    tmp_path: Path, reviewed_inputs: tuple[Path, Path]
) -> None:
    parent_path, review_path = reviewed_inputs
    parent_bytes, review_bytes = parent_path.read_bytes(), review_path.read_bytes()
    parent = json.loads(parent_bytes)
    originals = {row["path"]: Path(row["path"]).read_bytes() for row in parent["sources"]}
    result = sources.select_reviewed_sources(parent_path, review_path, tmp_path / "selected")
    assert result["status"] == "READY"
    assert result["schema"] == sources.REVIEWED_SCHEMA
    assert result["historical_rules_verified"] is False
    assert result["research_readiness"] == sources.REVIEWED_READINESS
    assert result["history_policy"] == sources.GAP_FREE_POLICY
    assert result["totals"]["inventoried"] == 6
    assert result["totals"]["selected"] == 5
    assert result["totals"]["excluded"] == 1
    assert result["totals"]["selected_records"] == 10
    assert result["sources"] == [
        row for row in parent["sources"] if row["symbol"] != "UNCLEAR/USDT"
    ]
    assert result["excluded"][0]["id"] == "htx:UNCLEAR/USDT"
    assert result["excluded"][0]["reasons"] == ["identity unresolved"]
    assert len(result["coverage"]["sources"]) == 5
    assert all(row["duration_minutes"] == 2 for row in result["coverage"]["sources"])
    assert all(row["full_utc_months"] == 0 for row in result["coverage"]["sources"])
    sources.verify_reviewed_selection(result)
    sources.verify_gap_free_selection(parent)
    assert parent_path.read_bytes() == parent_bytes
    assert review_path.read_bytes() == review_bytes
    assert all(Path(path).read_bytes() == raw for path, raw in originals.items())
    assert len(parent["excluded"]) == 2
    with pytest.raises(FileExistsError):
        sources.select_reviewed_sources(parent_path, review_path, tmp_path / "selected")


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "unknown",
        "wrong_source_hash",
        "unknown_outcome",
        "missing_reason",
        "blank_reason",
        "retained_unclear",
        "pnl_based",
        "unknown_thresholds",
        "missing_evidence",
        "wrong_evidence_hash",
        "missing_evidence_hash",
        "wrong_parent_hash",
    ],
)
def test_invalid_review_cannot_publish(
    tmp_path: Path, reviewed_inputs: tuple[Path, Path], mutation: str
) -> None:
    parent, review_path = reviewed_inputs
    review = json.loads(review_path.read_bytes())
    if mutation == "missing":
        review["decisions"].pop()
    elif mutation == "duplicate":
        review["decisions"][-1] = review["decisions"][0]
    elif mutation == "unknown":
        review["decisions"][0]["id"] = "htx:INVENTED/USDT"
    elif mutation == "wrong_source_hash":
        review["decisions"][0]["sha256"] = "0" * 64
    elif mutation == "unknown_outcome":
        review["decisions"][0]["exclude"] = 1
    elif mutation in {"missing_reason", "blank_reason"}:
        review["decisions"][0].update(
            exclude=True, reasons=[] if mutation == "missing_reason" else [" "]
        )
    elif mutation == "retained_unclear":
        review["decisions"][0].update(exclude=False, reasons=["identity unresolved"])
    elif mutation == "pnl_based":
        review["policy"]["selection_not_based_on_pnl"] = False
    elif mutation == "unknown_thresholds":
        review["policy"]["screening_thresholds"] = {}
    elif mutation == "missing_evidence":
        review["evidence_files"] = []
    elif mutation == "wrong_evidence_hash":
        review["evidence_files"][0]["sha256"] = "0" * 64
    elif mutation == "missing_evidence_hash":
        review["evidence_files"][0]["sha256"] = None
    elif mutation == "wrong_parent_hash":
        review["source_manifest_sha256"] = "0" * 64
    write_json(review_path, review)
    with pytest.raises(ValueError):
        sources.select_reviewed_sources(parent, review_path, tmp_path / "selected")
    assert not (tmp_path / "selected/manifest.json").exists()


@pytest.mark.parametrize("mutation", ["rules", "omission", "reasons", "counts", "policy"])
def test_published_partition_cannot_be_tampered(
    tmp_path: Path, reviewed_inputs: tuple[Path, Path], mutation: str
) -> None:
    parent, review = reviewed_inputs
    result = sources.select_reviewed_sources(parent, review, tmp_path / "selected")
    altered = copy.deepcopy(result)
    if mutation == "rules":
        altered["sources"][0]["rules"]["min_cost"] = "500"
    elif mutation == "omission":
        altered["sources"].pop()
    elif mutation == "reasons":
        altered["excluded"][0]["reasons"] = ["different reason"]
    elif mutation == "counts":
        altered["totals"]["selected"] = 1
    elif mutation == "policy":
        altered["review_policy"]["screening_thresholds"] = {"unknown": "ignore"}
    with pytest.raises(ValueError, match="differs from its audited decisions"):
        sources.verify_reviewed_selection(altered)


@pytest.mark.parametrize("changed", ["review", "evidence", "parent", "source"])
def test_changed_provenance_rejects_previously_selected_sources(
    tmp_path: Path, reviewed_inputs: tuple[Path, Path], changed: str
) -> None:
    parent, review = reviewed_inputs
    result = sources.select_reviewed_sources(parent, review, tmp_path / "selected")
    selected = result["sources"][0]
    path = {
        "parent": parent,
        "review": review,
        "evidence": tmp_path / "screening.json",
        "source": Path(selected["path"]),
    }[changed]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        sources.verify_reviewed_selection(result)


@pytest.mark.parametrize("linked", ["parent", "review", "evidence", "directory"])
def test_review_provenance_refuses_symlinks(
    tmp_path: Path, reviewed_inputs: tuple[Path, Path], linked: str
) -> None:
    parent, review = reviewed_inputs
    alias = tmp_path / "alias"
    if linked == "parent":
        alias.symlink_to(parent)
        parent = alias
    elif linked == "review":
        alias.symlink_to(review)
        review = alias
    else:
        value = json.loads(review.read_bytes())
        if linked == "directory":
            alias.symlink_to(tmp_path, target_is_directory=True)
            target = alias / "screening.json"
        else:
            alias.symlink_to(tmp_path / "screening.json")
            target = alias
        value["evidence_files"][0]["path"] = str(target)
        write_json(review, value)
    with pytest.raises(ValueError, match="symbolic links"):
        sources.select_reviewed_sources(parent, review, tmp_path / "selected")


def test_losing_a_whole_venue_is_explicitly_blocked(
    tmp_path: Path, reviewed_inputs: tuple[Path, Path]
) -> None:
    parent, review = reviewed_inputs
    value = json.loads(review.read_bytes())
    for decision in value["decisions"]:
        if decision["id"].startswith("gate:"):
            decision.update(exclude=True, reasons=["identity unresolved"])
    write_json(review, value)
    result = sources.select_reviewed_sources(parent, review, tmp_path / "selected")
    assert result["status"] == "BLOCKED"
    assert result["totals"]["selected"] == 4
    assert result["blockers"][0]["kind"] == "coverage"
    with pytest.raises(ValueError, match="differs from its audited decisions"):
        sources.verify_reviewed_selection(result)


def test_changed_review_at_publication_cannot_publish(
    tmp_path: Path, reviewed_inputs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, review = reviewed_inputs
    original = sources._review_derivation

    def change_after_derivation(p: dict[str, Any], r: dict[str, Any]) -> dict[str, Any]:
        result = original(p, r)
        review.write_bytes(review.read_bytes() + b" ")
        return result

    monkeypatch.setattr(sources, "_review_derivation", change_after_derivation)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        sources.select_reviewed_sources(parent, review, tmp_path / "selected")
    assert not (tmp_path / "selected/manifest.json").exists()


@pytest.fixture
def current_rule_snapshots(tmp_path: Path, reviewed_inputs: tuple[Path, Path]) -> Path:
    parent, review = reviewed_inputs
    sources.select_reviewed_sources(parent, review, tmp_path / "reviewed")
    directory = tmp_path / "catalogues"
    directory.mkdir()
    for venue in sources.GAP_FREE_VENUES:
        host, endpoint, query = sources.ENDPOINTS[venue]
        pairs = list(query)
        if venue == "bingx":
            pairs.append(("timestamp", "1795299589590"))
        url = "https://" + host + endpoint + ("?" + urlencode(pairs) if pairs else "")
        response = directory / f"{venue}-response-1.bin"
        response.write_bytes(b"{}")
        market: dict[str, Any] = {
            "symbol": "BTC/USDT",
            "base": "BTC",
            "quote": "USDT",
            "spot": True,
            "type": "spot",
            "active": True,
            "precision": {"price": "0.01", "amount": "0.001"},
            "precision_mode": 4,
            "limits": {"amount": {"min": "0.001"}, "cost": {"min": "5"}},
            "info": {},
        }
        if venue == "okx":
            market["limits"]["cost"]["min"] = None
            market["info"] = {
                "instType": "SPOT",
                "minSz": "0.001",
                "lotSz": "0.001",
                "tickSz": "0.01",
            }
        elif venue == "bingx":
            market["info"] = {"minQty": "0.001", "minNotional": "5"}
        elif venue == "gate":
            market["info"] = {"min_base_amount": "0.001", "min_quote_amount": "5"}
        elif venue == "htx":
            market["info"] = {"limit-order-min-order-amt": "0.001"}
        write_json(
            directory / f"{venue}-current.json",
            {
                "schema": 2,
                "status": "COLLECTED",
                "exchange": venue,
                "ccxt_version": sources.RESEARCH_CCXT_VERSION,
                "historical_rules_verified": False,
                "fetched_utc": datetime.now(UTC).isoformat(),
                "requests": [
                    {
                        "url": url,
                        "method": "GET",
                        "status": 200,
                        "bytes": 2,
                        "path": str(response),
                        "sha256": hashlib.sha256(response.read_bytes()).hexdigest(),
                    }
                ],
                "markets": {"USDT_BTC": market},
            },
        )
    return directory


def test_current_rules_selection_pins_five_snapshots_and_preserves_parent(
    tmp_path: Path, current_rule_snapshots: Path
) -> None:
    parent = tmp_path / "reviewed/manifest.json"
    before = parent.read_bytes()
    result = sources.select_current_rules_sources(
        parent, current_rule_snapshots, tmp_path / "latest"
    )
    assert result["schema"] == sources.CURRENT_RULES_SCHEMA
    assert result["status"] == "READY"
    assert result["research_readiness"] == sources.CURRENT_RULES_READINESS
    assert result["historical_rules_verified"] is False
    assert result["totals"]["selected"] == 5
    assert result["totals"]["excluded"] == 1
    assert result["totals"]["by_venue"] == {venue: 1 for venue in sources.GAP_FREE_VENUES}
    assert all(row["rules"]["min_amount"] == "0.001" for row in result["sources"])
    assert all(
        row["rules_snapshot"]["ccxt_version"] == sources.RESEARCH_CCXT_VERSION
        for row in result["sources"]
    )
    assert parent.read_bytes() == before
    sources.verify_current_rules_selection(result)
    with pytest.raises(FileExistsError):
        sources.select_current_rules_sources(parent, current_rule_snapshots, tmp_path / "latest")


@pytest.mark.parametrize("outcome", ["missing", "inactive", "incomplete"])
def test_current_rules_missing_inactive_or_incomplete_are_excluded(
    tmp_path: Path, current_rule_snapshots: Path, outcome: str
) -> None:
    path = current_rule_snapshots / "gate-current.json"
    snapshot = json.loads(path.read_bytes())
    market = snapshot["markets"]["USDT_BTC"]
    if outcome == "missing":
        snapshot["markets"] = {"USDT_ETH": {**market, "symbol": "ETH/USDT", "base": "ETH"}}
    elif outcome == "inactive":
        market["active"] = False
    else:
        market["info"]["min_quote_amount"] = None
        market["limits"]["cost"]["min"] = None
    write_json(path, snapshot)
    result = sources.select_current_rules_sources(
        tmp_path / "reviewed/manifest.json", current_rule_snapshots, tmp_path / "latest"
    )
    assert result["status"] == "BLOCKED"  # Gate has no other pair in this fixture.
    assert result["totals"]["selected"] == 4
    assert result["totals"]["excluded"] == 2
    assert result["excluded"][-1]["id"] == "gate:BTC/USDT"
    assert result["excluded"][-1]["reasons"]
    with pytest.raises(ValueError, match="differs from pinned snapshots"):
        sources.verify_current_rules_selection(result)


@pytest.mark.parametrize("mutation", ["version", "host", "response", "snapshot"])
def test_current_rules_rejects_untrusted_or_changed_catalogue(
    tmp_path: Path, current_rule_snapshots: Path, mutation: str
) -> None:
    path = current_rule_snapshots / "okx-current.json"
    value = json.loads(path.read_bytes())
    if mutation == "version":
        value["ccxt_version"] = "4.5.91"
    elif mutation == "host":
        value["requests"][0]["url"] = "https://evil.example/api/v5/public/instruments?instType=SPOT"
    elif mutation == "response":
        Path(value["requests"][0]["path"]).write_bytes(b"different")
    else:
        value["markets"] = {}
    write_json(path, value)
    with pytest.raises((ValueError, OSError)):
        sources.select_current_rules_sources(
            tmp_path / "reviewed/manifest.json", current_rule_snapshots, tmp_path / "latest"
        )
    assert not (tmp_path / "latest/manifest.json").exists()


@pytest.mark.parametrize("changed", ["parent", "snapshot", "source"])
def test_current_rules_published_manifest_detects_changed_inputs(
    tmp_path: Path, current_rule_snapshots: Path, changed: str
) -> None:
    parent = tmp_path / "reviewed/manifest.json"
    result = sources.select_current_rules_sources(
        parent, current_rule_snapshots, tmp_path / "latest"
    )
    path = {
        "parent": parent,
        "snapshot": current_rule_snapshots / "okx-current.json",
        "source": Path(result["sources"][0]["path"]),
    }[changed]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises((ValueError, OSError)):
        sources.verify_current_rules_selection(result)


@pytest.fixture
def flat_hour_inputs(tmp_path: Path, current_rule_snapshots: Path) -> tuple[Path, Path]:
    parent = tmp_path / "reviewed/manifest.json"
    sources.select_current_rules_sources(parent, current_rule_snapshots, tmp_path / "latest")
    latest = tmp_path / "latest/manifest.json"
    manifest = json.loads(latest.read_bytes())
    audit = tmp_path / "pair-audit.jsonl"
    with audit.open("w") as stream:
        for source in manifest["sources"]:
            stream.write(
                json.dumps(
                    {
                        **{
                            key: source[key]
                            for key in (
                                "id",
                                "venue",
                                "symbol",
                                "path",
                                "sha256",
                                "records",
                                "bytes",
                                "first_ms",
                                "last_ms",
                            )
                        },
                        "identity_before": source["identity"],
                        "identity_after": source["identity"],
                        "errors": {},
                        "repeated_candles": {
                            "flat": {
                                "longest_minutes": 0,
                                "runs_at_least_minutes": {"60": 0},
                                "top": [],
                            }
                        },
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
    return latest, audit


def test_flat_hour_selection_pins_parent_audit_and_rules(
    tmp_path: Path, flat_hour_inputs: tuple[Path, Path]
) -> None:
    parent_path, audit_path = flat_hour_inputs
    before_parent, before_audit = parent_path.read_bytes(), audit_path.read_bytes()
    parent = json.loads(before_parent)
    result = sources.select_flat_hour_sources(parent_path, audit_path, tmp_path / "flat")
    assert result["schema"] == sources.FLAT_HOUR_SCHEMA
    assert result["status"] == "READY"
    assert result["flat_audit"]["threshold_minutes"] == 60
    assert result["flat_audit"]["matched_parent_sources"] == 5
    assert result["flat_audit"]["excluded_parent_sources"] == 0
    assert result["sources"] == parent["sources"]
    assert result["rules_collection"] == parent["rules_collection"]
    assert result["source_hash_audit"] == parent["source_hash_audit"]
    assert result["totals"] == parent["totals"]
    sources.verify_flat_hour_selection(result)
    assert parent_path.read_bytes() == before_parent
    assert audit_path.read_bytes() == before_audit
    with pytest.raises(FileExistsError):
        sources.select_flat_hour_sources(parent_path, audit_path, tmp_path / "flat")


@pytest.mark.parametrize("longest,expected", [(59, True), (60, False), (61, False)])
def test_flat_hour_threshold_is_inclusive(
    tmp_path: Path, flat_hour_inputs: tuple[Path, Path], longest: int, expected: bool
) -> None:
    parent_path, audit_path = flat_hour_inputs
    parent = json.loads(parent_path.read_bytes())
    source = parent["sources"][0]
    source["records"] = max(source["records"], longest)
    audit = {
        row["id"]: {
            "repeated_candles": {
                "flat": {
                    "longest_minutes": longest if row["id"] == source["id"] else 0,
                    "top": [{"value": longest, "timestamp_ms": source["first_ms"]}]
                    if row["id"] == source["id"] and longest
                    else [],
                }
            }
        }
        for row in parent["sources"]
    }
    result = sources._flat_hour_derivation(parent, audit)
    assert (source["id"] in {row["id"] for row in result["sources"]}) is expected
    assert result["totals"]["selected"] == (5 if expected else 4)
    if not expected:
        assert result["excluded"][-1]["reasons"] == [sources.FLAT_HOUR_REASON]


@pytest.mark.parametrize("changed", ["parent", "audit", "source", "manifest"])
def test_flat_hour_selection_rejects_changed_evidence(
    tmp_path: Path, flat_hour_inputs: tuple[Path, Path], changed: str
) -> None:
    parent_path, audit_path = flat_hour_inputs
    result = sources.select_flat_hour_sources(parent_path, audit_path, tmp_path / "flat")
    if changed == "manifest":
        result["sources"][0]["rules"]["min_cost"] = "500"
    else:
        path = {
            "parent": parent_path,
            "audit": audit_path,
            "source": Path(result["sources"][0]["path"]),
        }[changed]
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises((ValueError, OSError)):
        sources.verify_flat_hour_selection(result)


@pytest.mark.parametrize("malformed", ["missing", "duplicate", "wrong_hash", "bad_run"])
def test_flat_hour_selection_refuses_incomplete_audit(
    tmp_path: Path, flat_hour_inputs: tuple[Path, Path], malformed: str
) -> None:
    parent_path, audit_path = flat_hour_inputs
    records = [json.loads(line) for line in audit_path.read_text().splitlines()]
    if malformed == "missing":
        records.pop()
    elif malformed == "duplicate":
        records[-1] = records[0]
    elif malformed == "wrong_hash":
        records[0]["sha256"] = "0" * 64
    else:
        records[0]["repeated_candles"]["flat"]["runs_at_least_minutes"]["60"] = 1
    audit_path.write_text("".join(json.dumps(row) + "\n" for row in records))
    with pytest.raises(ValueError):
        sources.select_flat_hour_sources(parent_path, audit_path, tmp_path / "flat")
    assert not (tmp_path / "flat/manifest.json").exists()


def test_flat_hour_cli_passes_exact_threshold_to_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[dict[str, Any], Path]] = []

    def fake_worker(request: dict[str, Any], output: Path) -> dict[str, str]:
        calls.append((request, output))
        return {"status": "READY"}

    monkeypatch.setattr(runner, "worker", fake_worker)
    base = [
        "research_sweep",
        "select",
        "--source-output",
        str(tmp_path / "parent"),
        "--output",
        str(tmp_path / "selected"),
    ]
    monkeypatch.setattr(sys, "argv", [*base, "--exclude-flat-run-minutes", "60"])
    assert runner.main() == 0
    assert calls == [
        (
            {
                "action": "select",
                "source_output": str(tmp_path / "parent"),
                "exclude_flat_run_minutes": 60,
            },
            tmp_path / "selected",
        )
    ]
    for argv in (
        [*base, "--exclude-flat-run-minutes", "59"],
        ["research_sweep", "matrix-prepare", *base[2:], "--exclude-flat-run-minutes", "60"],
    ):
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit):
            runner.main()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"exclude_flat_run_minutes": 59},
        {"exclude_flat_run_minutes": True},
        {"exclude_flat_run_minutes": 60, "exclusion_review": "/review.json"},
        {"exclude_flat_run_minutes": 60, "rules_snapshot_dir": "/catalogues"},
    ],
)
def test_flat_hour_worker_rejects_invalid_or_conflicting_options(
    tmp_path: Path,
    flat_hour_inputs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    extra: dict[str, Any],
) -> None:
    parent, audit = flat_hour_inputs
    monkeypatch.setenv("GRID_RESEARCH_HEAVY_LOCK", str(tmp_path / "heavy.lock"))
    monkeypatch.setattr(runner, "check_resources", lambda *_args: None)
    monkeypatch.setattr(sources, "DEFAULT_FLAT_AUDIT", audit)
    receipt = runner.worker(
        {"action": "select", "source_output": str(parent.parent), **extra},
        tmp_path / "invalid-selection",
    )
    assert receipt["status"] == "FAIL"
    assert "flat-hour selection requires only" in receipt["error"]
    assert not (tmp_path / "invalid-selection/preparation/manifest.json").exists()


def test_flat_hour_worker_publishes_child_from_cli_request(
    tmp_path: Path,
    flat_hour_inputs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, audit = flat_hour_inputs
    monkeypatch.setenv("GRID_RESEARCH_HEAVY_LOCK", str(tmp_path / "heavy.lock"))
    monkeypatch.setattr(runner, "check_resources", lambda *_args: None)
    monkeypatch.setattr(sources, "DEFAULT_FLAT_AUDIT", audit)
    receipt = runner.worker(
        {
            "action": "select",
            "source_output": str(parent.parent),
            "exclude_flat_run_minutes": 60,
        },
        tmp_path / "valid-selection",
    )
    assert receipt["status"] == "READY"
    assert receipt["history_policy"] == sources.GAP_FREE_POLICY
    assert receipt["research_admission"] == sources.CURRENT_RULES_READINESS
    selected = runner.read_json(tmp_path / "valid-selection/preparation/manifest.json")
    assert selected["schema"] == sources.FLAT_HOUR_SCHEMA
    sources.verify_flat_hour_selection(selected)
