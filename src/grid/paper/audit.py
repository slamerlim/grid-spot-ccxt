"""Read-only verification and coverage summary for a finished paper journal."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from grid.domain.numbers import DomainError
from grid.paper.journal import CAP_BYTES, MAX_RECORDS, RECORD_BYTES
from grid.persistence.storage import private_path
from grid.research.artifacts import canonical, publish


def rows(directory: Path) -> tuple[list[dict[str, Any]], str, str]:
    path = private_path(directory / "observations.jsonl", new=False)
    result: list[dict[str, Any]] = []
    previous = "0" * 64
    file_hash = hashlib.sha256()
    with path.open("rb") as stream:
        # Refuse an active writer; never create, truncate, migrate or repair state.
        fcntl.flock(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        if path.stat().st_size > CAP_BYTES:
            raise DomainError("audit input byte cap")
        while raw := stream.readline(RECORD_BYTES + 1):
            file_hash.update(raw)
            if len(raw) > RECORD_BYTES or not raw.endswith(b"\n") or len(result) >= MAX_RECORDS:
                raise DomainError("audit record bound or incomplete tail")
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise DomainError("audit record must be an object")
            if (
                not isinstance(row.get("kind"), str)
                or not row["kind"]
                or not isinstance(row.get("payload"), dict)
                or any(type(row.get(key)) is not int for key in ("sequence", "ms", "ns"))
                or row["ms"] < 0
                or row["ns"] < 0
            ):
                raise DomainError("audit record envelope is invalid")
            checksum = row.pop("sha256")
            if (
                row.get("sequence") != len(result) + 1
                or row.get("previous") != previous
                or hashlib.sha256(canonical(row)).hexdigest() != checksum
            ):
                raise DomainError("audit hash chain mismatch")
            if result and (row["ms"] < result[-1]["ms"] or row["ns"] < result[-1]["ns"]):
                raise DomainError("audit clock regression")
            result.append(row)
            previous = checksum
    return result, previous, file_hash.hexdigest()


def audit(directory: Path) -> dict[str, object]:
    data, checksum, file_checksum = rows(directory)
    if not data or data[0]["kind"] != "start" or data[-1]["kind"] != "finished":
        raise DomainError("audit requires a finished run")
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in data:
        groups.setdefault(row["kind"], []).append(row)
    if len(groups["start"]) != 1 or len(groups["finished"]) != 1:
        raise DomainError("audit requires one observation window")
    start, finish = data[0], data[-1]
    attempts, responses = groups.get("attempt", []), groups.get("response", [])
    books, checkpoints = groups.get("book", []), groups.get("checkpoint", [])
    instrument_count = sum(r["payload"]["endpoint"] == 0 for r in attempts)
    prior_attempts = start["payload"].get("prior_attempts", 0)
    prior_instruments = start["payload"].get("prior_instruments", 0)
    if (
        type(prior_attempts) is not int
        or type(prior_instruments) is not int
        or not 0 <= prior_instruments <= prior_attempts <= 750
    ):
        raise DomainError("audit prior budget counters are invalid")
    if prior_attempts:
        parent, _, parent_hash = rows(Path(start["payload"]["replacement_of"]))
        parent_attempts = [r for r in parent if r["kind"] == "attempt"]
        if (
            parent_hash != start["payload"]["prior_journal_file_sha256"]
            or len(parent_attempts) != prior_attempts
            or sum(r["payload"]["endpoint"] == 0 for r in parent_attempts) != prior_instruments
            or parent[-1]["payload"].get("result") != "partial-interrupted"
        ):
            raise DomainError("audit replacement provenance mismatch")
    if prior_attempts + len(attempts) > 750 or prior_instruments + instrument_count > 3:
        raise DomainError("audit wire budget exceeded")
    by_sequence = {r["sequence"]: r for r in attempts}
    answered: set[int] = set()
    for response in responses:
        value = response["payload"]
        claim = by_sequence.get(value["attempt"])
        body = bytes.fromhex(value["body_hex"])
        if (
            claim is None
            or claim["sequence"] in answered
            or response["sequence"] <= claim["sequence"]
            or len(body) > 65536
            or hashlib.sha256(body).hexdigest() != value["sha256"]
        ):
            raise DomainError("audit response provenance mismatch")
        answered.add(claim["sequence"])
    intervals = [(b["ns"] - a["ns"]) / 10**9 for a, b in zip(attempts, attempts[1:], strict=False)]
    for attempt in attempts:
        if (
            type(attempt["payload"]["endpoint"]) is not int
            or attempt["payload"]["endpoint"] not in (0, 1)
            or not 0 <= attempt["ns"] - start["ns"] < 3600 * 10**9
            or not 0 <= attempt["ms"] - start["ms"] < 3600000
        ):
            raise DomainError("audit endpoint or deadline breach")
        for hold in groups.get("hold", []):
            if hold["ns"] <= attempt["ns"] < hold["payload"]["until_ns"]:
                raise DomainError("audit request inside cooldown")
    if intervals and min(intervals) < (7.5 if prior_attempts else 5):
        raise DomainError("audit request spacing breach")
    for row in data:
        if abs((row["ms"] - start["ms"]) - (row["ns"] - start["ns"]) // 1000000) > 2000:
            raise DomainError("audit clock divergence")
    previous_source = 0
    for row in books:
        book = row["payload"]
        stamp = book["source_ms"]
        if (
            type(stamp) is not int
            or stamp <= previous_source
            or not 0 <= book["received_ms"] - stamp <= 10000
            or book["received_ns"] > row["ns"]
        ):
            raise DomainError("audit stale or duplicate source book")
        previous_source = stamp
    book_gaps = [(b["ns"] - a["ns"]) / 10**9 for a, b in zip(books, books[1:], strict=False)]
    checkpoint_gaps = [
        (b["ns"] - a["ns"]) / 10**9 for a, b in zip(checkpoints, checkpoints[1:], strict=False)
    ]
    observed = start["payload"].get("observed_clock") is True
    result = finish["payload"]
    if result["observed_soak_complete"] is not False:
        raise DomainError("one-hour report cannot satisfy I8 soak acceptance")
    if result["result"] == "one-hour-observed" and (
        not observed
        or finish["ns"] - start["ns"] < 3600 * 10**9
        or finish["ms"] - start["ms"] < 3600000
    ):
        raise DomainError("audit observed duration claim is unsupported")
    metrics = [r["payload"] for r in checkpoints]
    backups = groups.get("backup", [])
    return {
        "audit": "verified-journal",
        "journal_chain_sha256": checksum,
        "journal_file_sha256": file_checksum,
        "source_sha256": start["payload"].get("source_sha256"),
        "lock_sha256": start["payload"].get("lock_sha256"),
        "start_utc": start["payload"].get("utc"),
        "end_utc": result.get("utc"),
        "elapsed_seconds": (finish["ns"] - start["ns"]) / 10**9,
        "observed_clock": observed,
        "run_result": result["result"],
        "i8_acceptance_complete": False,
        "wire_attempts": len(attempts),
        "prior_interrupted_attempts": prior_attempts,
        "cumulative_wire_attempts": prior_attempts + len(attempts),
        "cumulative_instrument_attempts": prior_instruments + instrument_count,
        "instrument_attempts": instrument_count,
        "book_attempts": len(attempts) - instrument_count,
        "responses": len(responses),
        "attempts_without_response": len(attempts) - len(answered),
        "http_status_counts": dict(Counter(str(r["payload"]["status"]) for r in responses)),
        "validated_books": len(books),
        "minimum_request_interval_seconds": min(intervals, default=0),
        "maximum_book_gap_seconds": max(book_gaps, default=0),
        "book_coverage_seconds": (books[-1]["ns"] - books[0]["ns"]) / 10**9 if books else 0,
        "first_book_after_start_seconds": (books[0]["ns"] - start["ns"]) / 10**9 if books else None,
        "last_book_before_finish_seconds": (finish["ns"] - books[-1]["ns"]) / 10**9
        if books
        else None,
        "checkpoints": len(checkpoints),
        "maximum_checkpoint_gap_seconds": max(checkpoint_gaps, default=0),
        "rss_peak_sampled_bytes": max((m["rss_bytes"] for m in metrics), default=0),
        "wal_peak_sampled_bytes": max((m["wal_bytes"] for m in metrics), default=0),
        "directory_peak_sampled_bytes": max((m["directory_bytes"] for m in metrics), default=0),
        "task_peak_sampled": max((m["tasks"] for m in metrics), default=0),
        "cpu_peak_minute_fraction": max(
            (m["cpu_fraction"] for m in metrics if m["label"] == "minute"), default=0
        ),
        "holds": [r["payload"] for r in groups.get("hold", [])],
        "feed_failures": [r["payload"] for r in groups.get("feed-failure", [])],
        "process_restarts": len(groups.get("resumed", [])),
        "verified_backup_records": [r["payload"] for r in backups],
        "final_paper_status": result.get("paper"),
        "private_account_evidence": "NOT TESTED",
        "real_orders": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    publish(args.output, audit(args.directory))


if __name__ == "__main__":
    main()
