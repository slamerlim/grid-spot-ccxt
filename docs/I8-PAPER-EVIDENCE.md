# I8 public-paper evidence — execution in progress

The owner's instruction was: "approve harness and perform a quick run for 1 hour,
then continue development". The one-hour run is partial evidence relative to the
original 48-hour I8 acceptance requirement. Private account access, actual
orders/cancels, transfers, deployment and I9 remain outside this approval.

## Published implementation and validation

Branch `i8/public-paper-hour`, preparation parent
`8590b7671a592abd627f4056934ed96aa39aa5ac`.

- Harness commit: `0ec646e85d0a63dcede7fe6552f39d92a3dbd37d`.
- Durable observation/recovery/audit commit:
  `11a6cdb9f615bc4a2389f6bf211f8a4ab63944b7`.
- Both pushed; remote branch SHA verified. No PR created: this workspace has
  authorized Git SSH access, not an authenticated PR API. No CI retry or paid
  GitHub capability was used; both commit messages contain `[skip ci]`.
- Before first launch: full suite 622 passed on Python 3.12.16 (111.37 s) and
  3.13.18 (116.79 s). A subsequent cleanup fix passed 13 paper tests on each.
- Before replacement: 27 paper tests passed on Python 3.13.18 (6.52 s), and
  30 paper/static-boundary tests on Python 3.12.16 (6.61 s). This includes three
  actual SIGKILL tests at fake acceptance/fill/posting boundaries, budget carryover,
  parent-death refusal and single-restart coordinator behavior.
- Strict mypy (131 application/test sources), Bandit and staged secret scan
  passed before replacement. One import-formatting issue in the coordinator's
  explanatory comment was corrected in the follow-up; final Ruff is clean.
  The coordinator's two narrow subprocess
  annotations are justified in `I8-PAPER-DESIGN.md`. Dependencies did not change;
  the advisory audit found no known vulnerabilities and wheel/sdist builds passed
  during harness validation.

Follow-up validation before the replacement completed: **640 passed** on
Python 3.12.16 (117.59 s) and **640 passed** on Python 3.13.18 (121.34 s), with
no failures or skips. This includes the durable exit-reference refusal across
price recovery/restart and the read-only CLI evidence audit. Later targeted
diagnostic-refusal checks passed in the 44-test audit/doctor selection on both
versions (0.19/0.24 s). Final lint, formatting (170 files), mypy (131 sources),
Bandit, staged secret scan and wheel/sdist build passed. The observed run remains
separate from these local checks.

## First attempt: interrupted, not a completed hour

Directory: `/opt/grid-runs/i8-public-hour/20261104T141045Z-03505495`.
Frozen source: `/tmp/grid-i8-hour-code`, commit `0ec646e…`.
Grid source hash:
`ef6dacab706c2fe474fb86a7a6173953ad35974f58aac58cf90e6e2cf8ba3f80`.

Started 2026-11-04 14:10:45.809736 UTC. The process/exec handle were absent after
the orchestrator interrupted the turn. The last retained observation was at
898.697 elapsed seconds (approximately 14 minutes 59 seconds). This is a
`partial-interrupted` attempt, never a one-hour or 48-hour pass.

167 GET attempts all returned HTTP 200; 166 books validated. The minute-10 local
disconnect completed. No paper allocation or order was created: price minimum,
price maximum and minimum notional remained UNKNOWN. Invented cash remained
1,000 USDT. Offline recovery checked ledger integrity and independently restored
backup 99. Both `report.json` and `audit.json` remain in the directory.
Closed journal file SHA-256:
`2fd9d65376373bbf181dde928741ee7480f98877bbc230f8e638a4f688f3f3cd`.

## One linked replacement: still in progress

Directory:
`/opt/grid-runs/i8-public-hour/20261104T143725Z-replacement-f411b819`.
Frozen source: `/tmp/grid-i8-hour-replacement-code`, commit `11a6cdb…`.
Grid source hash:
`7cd8ba65f9aa4cac5fa55b8c9583811d95caec3b3f10b636a8cc61b8f3ddf766`.
Unchanged lock hash:
`7a6b4436e9a5719c3095818fc7c81b03239d85fa3bcf8ac320fd4d401888a576`.

Start: 2026-11-04 14:37:26.088 UTC approximately; exact timestamp is in the
journal. End of polling window: approximately 15:37:26 UTC. Prior 167 wire
attempts and one instrument attempt count toward the aggregate 750/3 ceilings.
Replacement polling is at least 7.5 seconds apart. The prior directory contains
the exclusive replacement claim; no further replacement is authorized by it.

Transient coordinator PID 100639, `/proc` start tick 17515722; first worker PID
100641. `coordinator.json` pins its identity, `observer-notes.jsonl` records actual
worker checks every 30 seconds, `agent-checkpoints.jsonl` records separate agent
observations, and `worker-output.jsonl` contains resource/fault checkpoints.
At the last recorded agent check near minute 5: coordinator live, 40 replacement
requests, 39 books, 207 cumulative requests, zero feed failures, about 118 MiB RSS.
These are interim numbers, not final evidence.

Observe that exact PID and start identity; do not infer death from a tool timeout.
The coordinator owns the one planned minute-30 exit-75 restart. Do not start a
second worker manually. The worker stops if its coordinator dies. No installed
service or private account is involved. Record final status, fault/backup/restart
results and a read-only audit before claiming the hour completed. Keep all
artifacts for at least seven days after review; no automatic deletion.

## Remaining evidence and work

The original minimum 48-hour acceptance run and the intended-first-account
matrix remain incomplete. Actual fees, account ownership, order identity,
order/trade recovery, private streams and execution behavior are NOT TESTED.
Public books alone cannot close those rows. Hosted CI remains pending under
the unchanged account lock; local tests are separate evidence.

After the hour, finish the evidence review, run final checks and publish the
follow-up. The follow-up adds the offline `gridctl paper-audit` command and
updates doctor/runbook wording. The paper exit-reference refusal is now latched
across later observations/restarts and covered by a mock regression test. This
path is not exercised by the ineligible public candidate.
This document will be replaced with final measured results, not inferred passes.
