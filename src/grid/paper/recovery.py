"""Offline closure of an interrupted run and single-use budget carryover."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from grid.domain.numbers import DomainError
from grid.paper.audit import rows
from grid.paper.engine import PaperEngine
from grid.paper.journal import Journal, clock
from grid.research.artifacts import publish


async def interrupted(directory: Path, sources: frozenset[str]) -> None:
    journal = Journal(directory)  # Refuses a still-running owner.
    engine: PaperEngine | None = None
    try:
        if not journal.kinds("start") or journal.kinds("finished"):
            raise DomainError("only an unfinished run can be closed as interrupted")
        last = journal.rows[-1]
        engine = await PaperEngine.open(journal, sources)
        await engine.writer.submit(lambda ledger: ledger.storage.integrity())
        await engine.backup(99)
        status = await engine.status()
        await engine.close()
        ms, ns = clock()
        start = journal.kinds("start")[0]
        result = {
            "result": "partial-interrupted",
            "failure": "process-absent-after-turn-interruption",
            "utc": datetime.fromtimestamp(ms / 1000, UTC).isoformat(),
            "elapsed_ms": (ns - start["ns"]) // 1000000,
            "last_observed_elapsed_ms": (last["ns"] - start["ns"]) // 1000000,
            "last_observed_utc": datetime.fromtimestamp(last["ms"] / 1000, UTC).isoformat(),
            "observed_clock": True,
            "observed_soak_complete": False,
            "attempts": len(journal.kinds("attempt")),
            "books": len(journal.kinds("book")),
            "paper": status,
            "private_account_evidence": "NOT TESTED",
            "real_orders": 0,
            "recovery_integrity": "verified",
            "recovery_backup": 99,
        }
        journal.append("finished", result, ms, ns)
        publish(directory / "report.json", result)
    finally:
        if engine:
            await engine.close()
        journal.close()


def claim_replacement(prior: Path, destination: Path) -> dict[str, object]:
    """One linked replacement only, with a cumulative 750/3 wire budget."""
    # Holding the old owner's lock also serializes two competing replacements.
    journal = Journal(prior)
    try:
        data = journal.rows
        if (
            not data
            or data[0]["kind"] != "start"
            or data[-1]["kind"] != "finished"
            or data[-1]["payload"]["result"] != "partial-interrupted"
            or data[0]["payload"].get("prior_attempts", 0) != 0
        ):
            raise DomainError("replacement requires the original interrupted run")
        attempts = journal.kinds("attempt")
        instruments = sum(r["payload"]["endpoint"] == 0 for r in attempts)
        if len(attempts) > 250 or instruments > 1:
            raise DomainError("remaining original request budget cannot support a replacement hour")
        import hashlib

        checksum = hashlib.sha256((prior / "observations.jsonl").read_bytes()).hexdigest()
        credit: dict[str, object] = {
            "prior_attempts": len(attempts),
            "prior_instruments": instruments,
            "prior_journal_file_sha256": checksum,
            "replacement_of": str(prior.resolve()),
            "replacement_directory": str(destination.resolve()),
            "poll_interval_ms": 7500,
            "prior_result": "partial-interrupted",
        }
        publish(prior / "replacement-claim.json", credit)
        return credit
    finally:
        journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--approved-sqlite-source", required=True)
    args = parser.parse_args()
    # Independent read verification before reopening only the synthetic ledger.
    rows(args.directory)
    asyncio.run(interrupted(args.directory, frozenset({args.approved_sqlite_source})))


if __name__ == "__main__":
    main()
