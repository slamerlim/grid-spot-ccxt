# GRID implementation rules

Read the full PRD, ADR-0001, acceptance plan and master implementation prompt
under `docs/` before editing. They are authoritative over the legacy simulator.
User instructions take precedence. Preserve imported specifications verbatim;
record clarifications separately with evidence and approval requirements.

## Current boundary: I0 only

This increment establishes packaging, offline diagnostics, safe configuration,
tests, documentation and CI. No strategy/executor, exchange network calls,
exchange credentials, deployment or database creation. Explicit user approval
is required before I1, per the attached master prompt. Live remains unavailable
even when a user sets an environment variable. Never claim future Q01–Q20 gates
have passed.

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

## GitHub access for workspace agents

The user authorized reusable GitHub access and ordinary commits/pushes for work
within an approved task. This does not authorize I1 or live trading. Use the
existing `origin` for `slamerlim/grid-spot-ccxt`; do not force-push or rewrite
published history as part of routine development.

In the prepared `/opt/grid` checkout, repository-local Git configuration selects
a dedicated SSH deploy key outside the repository. Agents running as the same
OS account on this host can reuse it after the owner enables write access in
GitHub. A different host, account or fresh clone needs its own authentication.
Never copy the private key into project files, logs, messages or commits.

The local default author is `GRID Automation <grid-automation@localhost>`, a
neutral automation identity, not the owner's personal GitHub identity. Follow
the checks above, review the staged changes, commit with a descriptive message,
then use `git push --set-upstream origin HEAD`. Check the command's result and
remote commit before reporting publication. See `docs/RUNBOOK.md` for setup,
verification and revocation; a configured remote alone is not proof of access.
