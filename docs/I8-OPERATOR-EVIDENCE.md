# I8 offline follow-up — local mock operator controls

Historical evidence for the first CLI increment. The subsequent manual portfolio
LIMIT command and its additional checks are recorded in `I8-MANUAL-EXIT.md`;
scoped exits and global handoff are recorded in `I8-ALLOCATION-EXIT.md`.

Scope: the owner's authorized continuation of offline/mock development after
the completed one-hour public run. Base: `b43f4c0bd58f087350df3cd540a5326108a25395`,
branch `i8/public-paper-hour`. No additional exchange request, credential access,
real/sandbox order, service installation or deployment occurred. GitHub remains
Free-only; hosted CI is pending under its unchanged account lock and was not retried.
The full implementation goal remains open.

## Implemented behavior

`gridctl operator` exposes five views and seven existing audited controls through
a private same-user Linux Unix socket. Only the existing mock supervisor and
synthetic exchange are accepted. The CLI never opens a second financial writer.
Every mutation uses explicit targets, an operational revision, OS-derived actor,
reason and durable request identity; sensitive actions need a saved preview and
second invocation. Bounded framing, clients and deadlines limit the local channel.

The finite invented-account owner starts halted and can reopen under the same
source/user/boot. Fake venue effects are journaled before their acknowledgement
and replayed before financial reconciliation. The existing paper replay helper
was moved to `execution/replay.py` for reuse without changing its event semantics.
Accepted synthetic orders survive forced process death without being resubmitted.
Old successful resume requests cannot clear a restart or subsequent halt.

No dependency or financial schema change is introduced. Generated sessions and
requests remain outside Git. Design and complete limitations are in
`I8-OPERATOR-DESIGN.md`; the README/runbook contain the operator procedure.

## Validation

The 22 additional tests cover the following behaviors, supplementing the existing
659-test accounting, execution, recovery, research and operational suite:

- Separate CLI/owner processes, SIGKILL after a journaled synthetic acceptance,
  restart with a new endpoint, preserved accepted order and no duplicate submit.
- All five views/seven controls, confirmation without a prepared request,
  private durable request files, UID-derived audit, changed/stale request refusal,
  concurrent revision claims with one winner, and exact replay after lost ACK.
- Later/restart halt protection when replaying a prior resume, active-owner lock,
  bounded slow clients, oversized/duplicate/nonfinite/unknown protocol messages,
  sanitized errors, socket permission/symlink refusal and inode-safe cleanup.
- Cleanup retains journal ownership while financial shutdown is pending and
  releases it after successful financial closure even if endpoint cleanup fails.
- The sole new application socket exception constructs only AF_UNIX streams;
  runtime IPC tests permit Unix sockets while retaining the IP-socket prohibition.

An initial process-test assertion incorrectly treated a durable working intent
as already accepted by the fake venue. The corrected test waits for the durable
venue event before injecting SIGKILL. No acceptance evidence is inferred from a
status count alone. Bandit also identified an assertion in the accept-loop startup;
it was replaced with an explicit runtime guard and security checks passed.

| Check | Result |
|---|---|
| Full pytest, Python 3.12.16 | **681 passed**, 123.64 s; no failures/skips |
| Full pytest, Python 3.13.18 | **681 passed**, 128.94 s; no failures/skips |
| Focused IPC/process/boundary/paper regression pass | 74 passed, 14.72 s |
| Final-source operations/paper/doctor/boundaries, Python 3.12.16 | **138 passed**, 38.20 s |
| Final-source operations/paper/doctor/boundaries, Python 3.13.18 | **138 passed**, 39.22 s |
| Ruff / formatting | PASS |
| Strict mypy | PASS; 137 application/test sources |
| Bandit, src and scripts | PASS after explicit runtime guard correction |
| Lock check | PASS; 66 packages unchanged |
| Dependency advisory audit | PASS; no known vulnerabilities found |
| Secret scan | PASS; tracked/new files, existing baseline unchanged |
| Wheel / source distribution | PASS; `/tmp/grid-i8-operator-dist/` |
| Hosted CI | PENDING; not retried |

Both supported Python environments link the existing reviewed mock SQLite 3.53.1
build. These local checks do not certify production storage, OS isolation or
exchange capabilities. Original/imported specification bytes match all four
recorded hashes. Both retained public-hour journal hashes remain unchanged.

Final review then kept endpoint cleanup retryable after a failure while still
releasing a successfully closed financial owner, and corrected doctor's wording
to state that the public-hour authorization is completed. The focused final-source
checks in the table cover those changes; they add no financial mutation or
exchange capability. Final application source SHA-256:
`ddf6f858e0b7398e01eede27eb450ba4514cabfde2a86d2b08142ccbb05fa242`.

## Standalone mock demonstration

A separate finite CLI demonstration retained its private report and requests at
`/tmp/grid-operator-demo-6rjzxzt1/`. It requested 12 seconds and observed
12.299336 seconds including startup. Exit code was 0, the endpoint was removed,
and the journal contains exactly **one synthetic submission, zero fills and zero
exchange requests**. The workflow inspected status, prepared/confirmed resume,
waited for fake acceptance, halted, then replayed the old resume and verified
that admission remained halted. Invented cash/zero fee settings are fixtures.
This is neither a public-data observation nor a trading-performance measurement.

Application source SHA-256 for that demonstration:
`f5c098fecdb43156238d70a9b353006004968491c2d7c1952558b078ced2d4db`.
Journal SHA-256:
`a6d21e61ee7f1b754368460dcd1d2fc89e6eaf3a98c31e19505131de97f00188`.

## Remaining implementation and acceptance

PRD section 10 is still incomplete: manual LIMIT liquidation, audited hot
configuration CLI, owner-mediated backup/restore verification and unified
research/storage aliases remain. The current views inspect a fixed invented
fixture. They provide no venue discovery, account fee evidence, real order-event
evidence, UTC production audit or deployment approval.

The completed public hour and its missing market constraints remain as recorded
in `I8-PAPER-EVIDENCE.md`. Further public/private access, the minimum 48-hour
acceptance run and I9 retain their separate gates. Continue authorized offline
implementation; do not report full I8/PRD acceptance or retry paid/locked CI.
