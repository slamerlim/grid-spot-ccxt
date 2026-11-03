# GRID implementation rules

Read the full PRD, ADR-0001, acceptance plan and master implementation prompt
under `docs/` before editing. They are authoritative over the legacy simulator.
User instructions take precedence. Preserve imported specifications verbatim;
record clarifications separately with evidence and approval requirements.

## Current boundary: I1 with local validation

On 2026-11-03 the owner approved `docs/I1-PLAN.md`, including its partial-seed
rule, then explicitly selected "Proceed with local validation for I1" to
replace the hosted-CI prerequisite for starting this iteration. Implement I1
pure Decimal strategy code and mock-event tests; do not request the same
approval again. Hosted CI remains pending, not passed. Record local checks
and remaining coverage precisely. Stop after I1 review; I2, exchange calls,
exchange credentials, execution, database creation and deployment remain
outside this approval. Live remains unavailable under every configuration.

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

## GitHub Free only

The owner uses GitHub Free and prohibits paid GitHub features. Never activate,
trial, buy or use paid plans/products/runners or metered overages; never add
payment details, raise spending budgets or recommend payment as a prerequisite.
Use local tools and verified free GitHub capabilities. Ordinary commit/push
authorization is not permission to spend.

The public repository's standard `ubuntu-latest` runner is free under the
verified GitHub terms. CI must skip private repositories before runner
allocation; never assume unused private-repository allowance. Keep remote
cache/artifact uploads disabled and do not add paid integrations. A billing-lock
message does not establish that an upgrade or payment is needed. Do not retry
a known lock without evidence of changed access; report local checks separately
and use free support/account review when needed. While the lock is unchanged,
use the documented `[skip ci]` commit directive for necessary authorized pushes
so publication does not retry it. A skipped run is not a CI pass. Use the I1
local-validation authorization above. See `docs/RUNBOOK.md` for sources and recovery
guidance.

## GitHub access for workspace agents

The user authorized reusable GitHub access and ordinary commits/pushes for work
within an approved task. Git access alone does not authorize new iterations or
live trading; the I1 authorization is recorded above. Use the
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
