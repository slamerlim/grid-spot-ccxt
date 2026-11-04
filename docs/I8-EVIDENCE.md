# I8 operational preparation — local evidence

This is the historical preparation report. The later approved one-hour public
observation and continued mock development are recorded in `I8-PAPER-EVIDENCE.md`;
their authorization supersedes this report's preparation-only stop boundary.

The owner explicitly selected **“Approve I8 operational preparation with local
validation.”** This iteration implements that offline scope. It does not
complete I8 acceptance: the observed minimum 48-hour soak and intended-first-venue
private/account evidence remain unfulfilled. LIVE remains disabled.

## Delivered scope

One mock supervisor now owns the single financial writer, executor, universe
and portfolio controllers. Startup/shutdown halt is durable; typed local
controls are revisioned, idempotent and atomic with order-state changes.
Placement claims require the current operational revision as well as the
existing independent universe/portfolio guards. Status includes owned funds,
order uncertainty, NAV/return/shortfall, residuals and retained-fill fee totals.
Local alerts, admission queues, recovery attempts, concurrent ticks, backups
and shutdown waiting are bounded.

The CLI publishes finite synthetic dry-run/benchmark manifests outside Git.
Temporary storage drills, schema v5 migration/restore, a syntax-checked but
uninstalled systemd template, runbooks and a separately gated observed-run plan
are included. Dependencies, imported specifications, example economic policy,
original legacy/data trees and CI automation remain unchanged.

No exchange endpoint/public feed, private credentials, real/sandbox order,
cancellation, transfer, external notification, service installation/deployment,
live financial database or long-running soak was used. Advisory-service checks,
official systemd documentation and authorized Git publication are distinct from
exchange access. No GitHub paid feature or account-lock retry was used.

## Runtime and executed checks

uv 0.13.7; Python 3.12.16 (`/tmp/grid-i2-py312`) and 3.13.18
(`/tmp/grid-i2-py313`); SQLite 3.53.1 with the existing mock-test source approval;
CCXT 4.5.91 and the unchanged 66-package lock. This runtime approval applies to
synthetic storage, not production deployment provenance.

| Check | Result |
|---|---|
| Full pytest, Python 3.12.16 | **610 passed**, no failures/skips, 110.15 s |
| Full pytest, Python 3.13.18 | **610 passed**, no failures/skips, 114.45 s |
| New operational cases | 28, included in both full suites; three real SIGKILL boundaries |
| Ruff lint / formatting | PASS; 149 Python files formatted |
| Strict mypy | PASS; 114 application/test source files |
| `uv lock --check` | PASS; 66 packages, lock unchanged |
| Bandit | PASS; no findings or added suppressions |
| Dependency advisory audit | PASS; no known vulnerabilities found |
| Tracked/staged secret scan | PASS; baseline unchanged |
| Wheel and source distribution | PASS |
| Offline doctor | Exit 0; iteration `I8 preparation`, `live: DISABLED`, `ready: false` |
| `systemd-analyze verify deploy/grid-mock.service` | Exit 0 on systemd 255 (255.4-1ubuntu8.18); no unit installed or started |
| Imported specs, dependency/config/CI inputs | Unchanged from I7; imported-source fingerprint tests pass |
| Hosted GitHub Actions | PENDING; known account lock not retried |

Reproduce with README commands and explicit environment selection:

```sh
UV_PYTHON=3.12.16 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py312 /tmp/grid-tooling/uv run --locked pytest
UV_PYTHON=3.13.18 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py313 /tmp/grid-tooling/uv run --locked pytest
```

Both complete suites ran concurrently; timings include that contention. The
benchmark was executed separately after them. Other commands were README Ruff,
mypy, Bandit, lock check, secret hook, export/audit, build and doctor checks.
The all-groups audit input was `/tmp/grid-i8-audit-requirements.txt`; pip-audit's
generic no-hash-export warning does not change locked installation. Tests retain
disabled internet sockets and permit AF_UNIX only for asyncio fixtures.

## New fault and control evidence

| Area | Executed evidence | Limit |
|---|---|---|
| Audited controls / Q11, Q20 | Explicit confirmed resume; distinct pause, BUY cancellation, hold, retirement and global halt; stale revision refusal; exact replay; old resume receipt cannot clear a later local halt | Typed in-process synthetic API, not operator authentication or remote control |
| Admission / Q09, Q17 | Halt while an already claimed send awaits acknowledgement; responsive writer; overlapping tick refusal; removed callback cannot bypass the ledger receipt; stale facts/clock regression block admission | Controlled interleavings, not every possible schedule |
| Portfolio / Q01, Q12 | 1,000 baseline → 1,100 trigger/final through two actual fake fills; LIMIT exit; final +10%, zero shortfall and zero base residual; all prior I7 tests retained | Invented prices/zero fees; no profitability or real execution claim |
| Recovery / Q13, Q18 | Three-attempt outage budget, explicit recovery reset, seven scopes with one unavailable owner, bounded intake/alert eviction, RSS refusal | Fake REST and injected limits; no live endpoint/429/subscription capacity measurement |
| Shutdown / Q09, Q19 | Accepted fake send with lost ack survives close/reopen and reconciles once; cancellation between run ticks closes owners; pending shutdown retains writer lock until admitted work completes | Controller restart is separate from real SIGKILL evidence; no guarantee that a hung OS call can be terminated safely |
| Backup ownership / Q19 | One backup at a time; canceled caller detaches; shutdown joins its verifier; second backup rejected without another restore task | Temporary files and one local process, not off-host disaster recovery |
| Actual SQLite FULL / Q19 | Constrain temporary database `max_page_count`; fill bounded audited requests until actual `SQLITE_FULL`; no extra send, funds retained, local halt survives failed persistence | Database page-limit fault, not filling the host volume or testing physical NVMe exhaustion |
| Permissions / Q19 | World-accessible backup directory rejected before file creation; failed backup stays unfresh and admission halts | Private-path safety guard, not an OS EACCES or read-only mount drill |
| Crash atomicity / Q19 | Three real child-process SIGKILLs: operations after-posting, before-commit and after-commit; BUY revocation, command receipt and operational revision agree | Disposable local storage; not hardware power loss |
| Migration / Q19 | v4 backup migrates to v5 without losing portfolio data; v5 restore retains halt; existing v1/v2/v3 fixtures updated only for the additive v5 table | No deployed ledger upgraded; no downgrade-in-place support |
| Report / Q18, Q20 | Observed versus simulated duration, explicit candidate/owner/client counts, output overwrite refusal, bad-source refusal with fixed error text, no synthetic report can complete the soak | Post-step sampling can miss transient peaks; public/private evidence remains absent |

The complete suite retains earlier accounting/fee/precision, crash, schema,
network, redaction and dependency-boundary tests. No safety test or runtime
allowlist was weakened. Review corrected detached backup ownership and shutdown
cleanup, receipt replay side effects and serialized derived return fields.
The host-reporting call and soak-result field name were clarified to avoid
static-check false positives, without adding suppressions. The benchmark was
extended to ten ticks to exercise all seven owners under the existing global
uncertainty guard.

## Bounded experiments

Final reports are retained outside Git at
`/tmp/grid-i8-preparation/benchmark-final.json` and
`/tmp/grid-i8-preparation/dry-run-final.json`. Their SHA-256 digests are,
respectively:

```text
dc4aa96e95c71f3e6153957b5203d9be5e1de8a73ed12d6be0d2463409fb0df6
64032bf7da5849734e30ea259e30c7e1b81999532c085f871b9b1ad87d40343a
```

Both identify the exact executed `grid` Python source tree as SHA-256
`6dce80bc85a5293f0a8f01d122dc11bd19eacb9b8c5adbdfcafd65dca4b0d6e2`.
The supplied Git reference was the I7 parent
`dbe067c91238079e473afd5c43e00386d4b3e6d8`; these were runs of the I8 working tree,
not claims that I7 contained this code. The fixture/caps hash is
`07b308e640e47567eed13228ac1b83acca1317c55518f0d379f2085586237168`.

The host reported Linux 6.8.0-124-generic, x86_64, 32 logical/affinity CPUs,
131,596,828 KiB physical memory and **ext4** for the temporary directory.
586,854,105,088 bytes were free before the benchmark. The inspected cgroup-v2
CPU/memory cap files were absent, which does not prove unlimited resources.
These facts do not identify NVMe hardware or an approved deployment host.

The benchmark ran from 2026-11-04 02:21:16.688 UTC to 02:21:52.753 UTC:
**36.065 observed seconds**, 35.786 process CPU seconds. Each case assessed all
its candidates as eligible and selected them in the pure ranking workload,
while separately supervising only the listed funded allocations. Ten ticks
followed initialization; each case has 11 post-step samples.

| Logical venues | Candidates | Funded owners | Selection ms | Case wall s | CPU s | Sampled RSS max MiB | Longest tick ms |
|---|---|---|---|---|---|---|---|
| 1 | 10 | 1 | 1.94 | 1.324 | 1.305 | 30.7 | 115.7 |
| 1 | 100 | 1 | 16.63 | 1.351 | 1.339 | 32.2 | 118.2 |
| 1 | 1,000 | 1 | 170.10 | 1.547 | 1.534 | 38.4 | 118.3 |
| 7 | 10 | 7 | 1.77 | 10.499 | 10.408 | 38.0 | 1,039.0 |
| 7 | 100 | 7 | 17.50 | 10.559 | 10.498 | 38.1 | 1,042.3 |
| 7 | 1,000 | 7 | 174.73 | 10.781 | 10.697 | 41.0 | 1,039.7 |

All cases stayed below the declared sampled 512 MiB RSS / 4 MiB WAL caps;
maximum sampled WAL was 296,672 bytes. Two asyncio tasks and zero queued writer/
intake items were observed at sample points; this is not a continuous peak
measurement. Process lifetime RSS uses a separate kernel counter in the raw
report. The one-owner cases made 11 fake page reads and one fake LIMIT submission;
seven-owner cases made 77 reads and seven submissions. Each used one synthetic
gateway, zero CCXT clients and zero external API calls. No case ended with its
global admission halt raised.

The seven-owner tick costs about one second on this fixture and consumes roughly
one core during the tight experiment. **The nominal 10 ms scheduling interval
is not achieved.** The observed ticks remain below the explicit 5 s soft limit,
but there is no production throughput/CPU-headroom claim. Target sizing and
scheduling need measured review before a deployed workload. The service
template's 50% CPU ceiling was not applied to these runs.

The final dry run followed from 02:21:52.927 to 02:21:54.394 UTC:
**1.467 observed seconds**, 1.439 CPU seconds and **40 simulated milliseconds**.
It observed one controller close/reopen, zero process restarts, one injected
fake disconnect, two fake LIMIT submissions/fills, 1,000 baseline / 1,100 trigger
/ 1,100 final NAV, +10% final return, zero exit shortfall and zero base residual.
The backup was restored and hashed at a 41-request cutoff, then a final halt
was recorded. Sampled RSS max was 35,090,432 bytes and WAL max 296,672 bytes.
All dry-run conservation/restart/limit/restore checks passed. Its report, like
the benchmark, explicitly records `observed_soak_complete=false` and
`i8_acceptance_complete=false`; neither duration is a 48-hour soak.

## Review decision and remaining gates

**Preparation is ready for review; full I8 acceptance and live deployment remain
on hold.** See `I8-DESIGN.md` for exact semantics and `I8-SOAK-PLAN.md` for the
proposed next scope. The later public-paper harness/run is not implemented or
authorized by this preparation. Even an observed public-paper run cannot invent
missing real account/order/fee/private-event evidence.

Resource measurements are local fixture evidence. There is no seven-CCXT-client
or 1,000-funded-allocation benchmark, installed isolation test, production CPU/
disk sizing, hardware-fault guarantee, external alert destination or real order
recovery certification. Retained status fee totals are not full-history totals.
Shutdown can report pending ownership rather than forcibly kill an admitted
writer operation. The framework remains mock-only under all live flags.

Hosted CI is still pending. The known account lock has not been retried; local
checks are not a hosted pass. Publication uses `[skip ci]` and GitHub Free only.
Rollback preserves v5 audit history and uses scoped code/docs reverts plus a
separately verified compatible backup if needed. No production rollback or
live change is involved. Stop after preparation review; further external access,
observed soak and I9 canary require their own explicit authorization.
