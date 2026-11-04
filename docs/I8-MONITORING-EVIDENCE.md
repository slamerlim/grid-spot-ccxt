# I8 offline follow-up — checkpoint timing and CPU windows

Scope: continue the owner's authorized mock development after the completed
one-hour observation. No additional exchange request, credential access, order,
service installation or deployment occurred. GitHub remains Free-only; hosted
CI remains pending under the unchanged lock. The full project goal remains open.

Base: `a368e01090e99dae5f297fb974e7543e4a2d552c`, branch `i8/public-paper-hour`.
This increment changes the paper runner/auditor, their tests and relevant docs.
No dependency, financial schema, strategy, funding or request allowance changes.

## Problem and implemented behavior

The completed public hour measured checkpoint gaps up to 60.665167 seconds.
The old loop waited until 60 seconds had elapsed before sampling, adding loop
and request delays to each interval. Its CPU baseline also reset at every
checkpoint, including backup/fault snapshots, so an intervening snapshot could
prevent the next CPU sample from reaching the minimum measurement duration.

Monitoring version 2 targets a resource snapshot at 50 seconds, schedules idle
sleep against that deadline and checks after a feed read before paper admission.
A gap exceeding 60 seconds is latched in the journal and makes the run partial.
The same check during final cleanup prevents a delayed final sample being
reported as success. There is no catch-up polling, deadline extension or new
request budget. A process cannot guarantee a deadline while descheduled or
blocked in an OS operation; it refuses further admission after detecting the gap.

CPU sampling uses an independent process-local baseline. Additional snapshots
retain that baseline until at least 60 seconds have been measured. Each sample
records the actual duration, fraction and completeness; only a complete window
can pass/fail the 50% limit. Partial process-boundary windows are not claimed as
complete minute measurements. No synthetic time is called observed time.

The auditor verifies the new measurement fields, computes timing/CPU faults
independently of the stored fault flag and refuses contradictory completion
claims. It still reports partial faulty runs. Legacy evidence remains version 1;
the new rules are not retroactively applied or presented as already observed.

## Executed validation

| Check | Result |
|---|---|
| Full pytest, Python 3.12.16 | **659 passed**, 117.99 s; no failures/skips |
| Full pytest, Python 3.13.18 | **659 passed**, 122.37 s; no failures/skips |
| Focused paper suite | 49 passed, 8.66 s |
| Ruff / formatting | PASS; 170 files |
| Strict mypy | PASS; 131 application/test sources |
| Bandit, src and scripts | PASS |
| Lock check | PASS; 66 packages unchanged |
| Wheel / source distribution | PASS; `/tmp/grid-i8-monitoring-dist/` |
| Hosted CI | PENDING; not retried |

Regression cases include complete mock schedules with 5.000/5.101-second
processing delays, a 61-second suspension before admission, and 80% CPU usage
over 60 seconds despite an intervening snapshot. The audit rejects completed
reports with measured timing/CPU breaches even if their fault flag is empty;
the corresponding partial reports remain inspectable. Existing accounting,
crash, restart, budget and source-freshness tests also pass.

A bounded standalone experiment retained its mock report and audit at
`/tmp/grid-i8-monitoring-mock-q7pg08u5/`. It used synthetic incomplete market
rules, temporary invented funds and an accelerated clock with 5.101-second
processing steps: 3,603.985 **simulated** seconds, 124 checkpoints, maximum
checkpoint gap **51.01 s**, maximum completed CPU window **61.212 s**, one
simulated reopen and **zero network calls**. Its result is `mock-clock-complete`,
with I8 acceptance false. These values are not a new observed-hour result.

Application source SHA-256:
`bdab8e39831be62574d3b24f9ff17fba3d5e0fc4d8ec6d8af4099a0e494936d5`.
Mock journal file SHA-256:
`cf0ebffc8d60d58ee27bf8b2c0ad38dc01d2f46fb8855e2e7399528f3f9b3414`.

Reproduce with README's explicit Python environment/check commands; the focused
command is `uv run --locked pytest tests/paper -q`. `gridctl paper-audit` can
inspect retained evidence without changing it. Rollback is a scoped code/docs
revert, preserving all journals, databases and approval history. Never resume
an existing observation with changed source.

## Remaining implementation and acceptance work

The observation's unknown market constraints and inactive paper strategy remain
unchanged. No actual account fees, private events or execution were validated.
The minimum 48-hour gate and I9 canary still need their own authorization and
evidence. This correction is eligible for future observed testing, not proof
that host scheduling or production resource acceptance has passed.

PRD section 10 still requires an operator CLI beyond doctor and paper-audit.
Typed controls/status already exist in `grid.operations`, but a running mock
owner has no bounded local command interface. That is substantive remaining
offline development: expose those controls through the existing single writer
with explicit revisions, idempotent requests, actor/reason records and sensitive
action confirmation. Do not substitute direct independent database writes or
enable authenticated trading to fill this gap.
