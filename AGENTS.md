# GRID implementation rules

Read the full PRD, ADR-0001, acceptance plan and master implementation prompt
under `docs/` before editing. They are authoritative over the legacy simulator.
User instructions take precedence. Preserve imported specifications verbatim;
record clarifications separately with evidence and approval requirements.

## Current boundary: I0 only

This increment establishes packaging, offline diagnostics, safe configuration,
tests, documentation and CI. No strategy/executor, exchange network calls,
credentials, deployment or database creation. Explicit user approval is required
before I1, per the attached master prompt. Live remains unavailable even when a
user sets an environment variable. Never claim future Q01–Q20 gates have passed.

## Invariants for subsequent approved iterations

- Spot only, limit only; geometric grid; minimum executable buy lots; one
  possibly active aggregate sell per allocation; no increasing lot sizing.
- Confirmed fills drive progression. Persist reservations/intents before sends;
  unknown submissions/cancellations block replacement and require reconciliation.
- Decimal or currency quanta for finances. Dedicated allocated funds only.
- One controller/writer, patched SQLite WAL/FULL, bounded queues/caches/retries.
- A +10% net NAV trigger starts a bounded limit exit, not a guaranteed profit.
- Retiring symbols/venues retain supervision of orders and inventory.
- Immutable historical inputs; conservative and explicitly uncertain OHLC fills.
- No credentials, private keys, wallet state, datasets or generated reports in Git.

## Development

Use Python 3.12/3.13, `uv` and the single `uv.lock`. Run the commands documented
in README before committing. Tests must be offline by default and deterministic;
use temporary paths and mock external data. Typecheck application and tests.
Keep commits scoped and describe behavior, validation and remaining limitations.
Do not modify `/opt/module_1_multi` or `/opt/datasets`.

Parallel work is allowed with disjoint file ownership. Report actual evidence;
unavailable repository access or capabilities are BLOCKED/NOT TESTED, not PASS.
