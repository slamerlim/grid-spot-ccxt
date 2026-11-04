# I8 public-paper evidence — one observed hour completed

The owner instructed: "approve harness and perform a quick run for 1 hour,
then continue development". The requested hour completed on **2026-11-04**.
The earlier interrupted attempt remains separately recorded. Both attempts
share the original 750-request / three-instrument-request budget.

This establishes a bounded public-feed and operational observation, **not active
paper-trading performance or full I8 acceptance**. Price minimum, price maximum
and minimum notional were UNKNOWN. Admission remained closed: zero allocations,
orders, fills, fees or cancellations; the invented 1,000 USDT stayed unallocated.
No private-account access, real/sandbox trading, transfers, deployment or paid
GitHub feature was used. LIVE remains disabled.

## Final observed results

Completed directory:
`/opt/grid-runs/i8-public-hour/20261104T143725Z-replacement-f411b819`.
Retained `report.json`, `audit.json` and `review.json` distinguish the worker
report, independent journal audit and final read-only storage/process review.

| Measurement | Observed result |
|---|---|
| UTC window | 2026-11-04 14:37:26.088821–15:37:26.519755 |
| Journal start-to-finish monotonic duration | 3,600.432429 s |
| Replacement wire attempts / responses | 462 / 462; all HTTP 200 |
| Replacement metadata / book requests | 2 / 460; 460 validated books |
| Cumulative attempts, including interrupted run | **629 / 750**, all HTTP 200 |
| Cumulative instrument attempts | **3 / 3** |
| Unanswered attempts / feed failures | 0 / 0 |
| Minimum request interval | 7.555392 s; required minimum 7.5 s |
| First book after start / last book before finish | 7.721470 s / 1.008871 s |
| First-to-last book coverage | 3,591.702088 s |
| Maximum book gap | 67.150735 s, including scheduled local pauses |
| Process restarts | One planned restart; exit 75 then exit 0 |
| Final paper capital / unallocated-only return | 1,000 synthetic USDT / 0%; no active strategy |
| Real orders / private account evidence | 0 / NOT TESTED |

Only the approved unauthenticated OKX BTC/USDT instrument/book GET paths were
available. The audit verified response hashes, hash-chain continuity, source-book
freshness/order, endpoint IDs, request spacing, cooldowns, original deadlines
and the interrupted parent's immutable hash/counters. No request fell inside
either hold or after the one-hour window. A valid journal is not venue readiness.

## Operational drills and observation limits

The 60-second local disconnect began at elapsed 600.124 s. The local synthetic
429/backoff began at 2,700.392 s; it caused no extra server request. Polling
resumed after each hold without a feed failure.

Backups 20 and 50 were independently restored at elapsed 1,200.793 s and
3,000.804 s. Both had request watermark 1. After shutdown, a read-only review
checked the main ledger, both backups and both restored files: SQLite integrity,
foreign keys and schema v5 passed; semantic hashes agreed; raw file hashes were
unchanged by inspection. Each contained only the invented 1,000 USDT pool and
no allocations/orders/fills/fees. Common semantic hash:

`916467bd20df7e3d447d9ba5a26fa216ec7cf408c0ebf9df1bbd3063f4efabc4`.

The restart was requested at elapsed 1,800.791 s and resumed at 1,801.457 s
(0.666452 s later), preserving source, host, journal, funds, budget and deadline.
The transient coordinator recorded 121 actual worker-liveness checks, with a
maximum gap of 30.001 s. Worker PIDs were 100641 and 103119; coordinator PID
100639 (start tick 17515722). All finished; no service was installed.

Agent observations were separate: 32 retained checks with a maximum gap of
1,377.008 s during orchestration interruption. Do not claim uninterrupted agent
presence. Coordinator and worker records continued throughout that gap.
There were 69 worker resource/fault checkpoints, with a maximum gap of
**60.665167 s**. The nominal minute cadence slightly exceeded 60 s; this is not
a strict timing acceptance pass. Review scheduling before a longer workload.

| Sampled resource | Maximum | Experiment cap |
|---|---:|---:|
| RSS | 129,888,256 bytes (123.87 MiB) | 512 MiB |
| WAL | 296,672 bytes | 16 MiB |
| Run directory during observation | 3,588,410 bytes | 512 MiB |
| Asyncio tasks | 2 | 20 |
| CPU over recorded minute intervals | 0.2975% of one core | 50% |

These are sampled local measurements, not continuous peaks or production-capacity
certification. Directory measurements precede final audit/review artifacts.
With no active allocation, this hour did not exercise executor load, fill posting,
portfolio exits, order recovery or alert pressure. Their mocks are separate evidence.

## Provenance and first interrupted attempt

The completed run used frozen checkout `/tmp/grid-i8-hour-replacement-code`,
commit `11a6cdb9f615bc4a2389f6bf211f8a4ab63944b7`.

- Observed Grid Python source SHA-256:
  `7cd8ba65f9aa4cac5fa55b8c9583811d95caec3b3f10b636a8cc61b8f3ddf766`.
- Unchanged lock SHA-256:
  `7a6b4436e9a5719c3095818fc7c81b03239d85fa3bcf8ac320fd4d401888a576`.
- Completed journal file SHA-256:
  `5b62b9b23317d5ecb635c29c2928e443979b833d5919fbeb348cd88ab959d5b7`.
- Final journal-chain hash:
  `2c362673245fc3fb6cd9cd032abef67977fcdb114ac306fa2da9d10b3b3a0e7b`.
- Follow-up audit implementation source SHA-256:
  `ccf6cb71fd8c54d9fa45be33aab251fdd784f961233e2885548127b2dd91b5f5`.

First directory: `/opt/grid-runs/i8-public-hour/20261104T141045Z-03505495`.
Frozen commit: `0ec646e85d0a63dcede7fe6552f39d92a3dbd37d`; source hash:
`ef6dacab706c2fe474fb86a7a6173953ad35974f58aac58cf90e6e2cf8ba3f80`.
It started at 14:10:45.809736 UTC; the last observation was at elapsed 898.697 s
(about 14 minutes 59 seconds). Its process disappeared after the orchestrator
interrupted the turn. This remains **partial-interrupted**.

It spent 167 GET attempts (one instrument, 166 validated books), all HTTP 200,
and completed the minute-10 disconnect. Offline closure verified ledger integrity
and restored backup 99. The later closure timestamp is not observed coverage.
Closed journal file SHA-256:
`2fd9d65376373bbf181dde928741ee7480f98877bbc230f8e638a4f688f3f3cd`.
The exclusive replacement claim carried all 167 attempts into the same budget.
No further replacement or public run follows from this completed approval.

Both directories and frozen source checkouts remain retained. Evidence files
are owner-only, outside Git and original data trees. Retain at least seven days
after review; no automatic deletion. Raw reports/data/databases are not committed.

## Continued development and local validation

Branch: `i8/public-paper-hour`. Harness commit `0ec646e…`, observation/recovery/
audit commit `11a6cdb…`, and follow-up commit `b2a4c8b…` were pushed and their
remote SHAs verified. Follow-up work adds `gridctl paper-audit`, durable paper
exit-reference refusal across price recovery/restart, corrected readiness wording
and malformed-evidence validation. A reproduced non-object payload previously
caused an AttributeError; it now returns a sanitized refusal. Hash-valid malformed
envelopes and invalid carried-over counters cannot pass audit.

Final code checks used uv 0.13.7, the unchanged 66-package lock, CCXT 4.5.91
and the previously approved mock SQLite 3.53.1 builds:

| Check | Result |
|---|---|
| Full pytest, Python 3.12.16 | **652 passed**, 117.56 s; no failures/skips |
| Full pytest, Python 3.13.18 | **652 passed**, 120.38 s; no failures/skips |
| Ruff lint / format | PASS; 170 Python files |
| Strict mypy | PASS; 131 application/test sources |
| Bandit, application and coordinator | PASS; two documented narrow subprocess annotations |
| Lock check / tracked secret scan | PASS; lock and secret baseline unchanged |
| Wheel / source distribution | PASS; `/tmp/grid-i8-paper-audit-final-dist/` |
| Offline doctor | `I8 public-paper`, `live: DISABLED`, `ready: false` |
| Both retained journals through current auditor | PASS; first remains partial, replacement observed |
| Final main/backup/restored SQLite read-only review | PASS; unchanged files and identical financial contents |
| Hosted GitHub Actions | PENDING; unchanged account lock not retried |

Coverage includes actual SIGKILL tests at fake acceptance/fill/posting boundaries,
budget carryover, single-restart coordination and parent-death refusal. Local
suites use synthetic inputs and disabled Internet sockets. Dependencies did not
change; the earlier harness advisory audit found no known vulnerabilities.
Local checks are not a hosted-CI pass. Publication uses ordinary authorized SSH
pushes and `[skip ci]`; GitHub remains Free-only. No PR API was used.

## Remaining acceptance boundaries

The requested quick run is complete. Continue authorized offline/mock work;
additional exchange requests need separately scoped authorization. The original
minimum 48-hour observed acceptance run remains outstanding. Actual account
ownership, fees, private events, order/trade recovery and execution semantics
remain NOT TESTED. Public unknowns require evidence, not invented unrestricted
rules. Before another observed workload, address checkpoint jitter and establish
what eligible paper activity it can actually exercise.

A future private-evidence plan must identify the intended account, exact read-only
permissions/endpoints, historical evidence source, secret handling and budgets
before requesting authorization. No account or credentials have been discovered.
I9 still requires separate venue/account/funding/risk approval; neither this hour
nor passing mocks authorize trading or deployment.
