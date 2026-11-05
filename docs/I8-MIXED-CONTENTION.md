# I8 follow-up — repeated mixed order flows under research pressure

Implemented 2026-11-05 from base `24bae1d1e6b5ea62700d4b78cbb45854cd099268`
on `i8/public-paper-hour`, within the owner's instruction to continue offline
development after the completed public hour. This increment adds a finite mixed
BUY/SELL workload and reduces redundant reconciliation writes after an observed
latency failure. It introduces no exchange access, dependency, schema migration,
live sender, installed service or deployment.

## Workload and bounds

`python -m grid.operations contention --mixed-rounds N` selects the new mode.
The original seed-partial experiment remains the default without that flag.
The new inner schema is `grid-mixed-contention-v1`, inside the existing
`grid-research-run-v1` envelope with operation `resource-contention`. All result
paths are exclusive; exit 0 means all declared checks passed, exit 1 retains a
completed measurement with failed checks, and exit 2 refuses/fails publication.
A second-case failure cannot publish the first case as a complete experiment.

The command runs one allocation on one synthetic venue, then seven allocations
on seven synthetic venues. Each receives 1,000 invented QUOTE units. The existing
fixture varies market precision/rules by venue; there is one minimum seed BUY
per allocation. Actual fixture fees are explicitly zero, and fills occur at
their submitted LIMIT prices. Neither the fills nor their returns are forecasts.

Each of 1–8 rounds exercises this sequence:

1. Reconcile and dispatch the ordinary aggregate SELL and three lower minimum
   BUY orders through the existing mock executor and sole SQLite writer.
2. Start one bounded replay child. Fill the highest lower BUY, request cancellation
   of the old SELL, prove that an acknowledgement alone blocks replacement, then
   partially fill that SELL during the unresolved cancellation.
3. Offer 64 hints to the 16-entry intake, retaining 48 overload refusals. Stop
   the owned child, verify its process identity/state and unchanged CPU ticks,
   and prove overload blocks new sends. Settle cancellations, reconcile all
   pending fills, and drain pressure before resuming research.
4. Submit the successor aggregate SELL only after old-order finality; post two
   partial fills. Drain old BUY orders while respecting each cancellation barrier.
   In every nonfinal round, a BUY fills during cancellation, leaving owned
   inventory for the next round without a second seed.
5. Reconcile the complete history again and require unchanged financial rows.
   Finish and reap the research child before the next round. The final round
   suppresses new exposure, drains all orders/inventory and checks exact financial
   equality after closing and reopening the writer.

Every submission is checked for LIMIT type, minimum BUY size and at most one
externally open SELL. After service, an independent cash/base calculation uses
only committed synthetic trade identities; it checks conservation, available
quote and the internal SELL slot. The final durable trade set must equal every
invented trade exactly. No external order may remain open.

The workload has bounded dispatch/cancel loops, 1,000 synthetic orders/trades,
20 reconciliation pages of up to 128 records with a separate 1,000-record cap,
and a 240-second wall timeout per case. A timeout is failure, not a partial pass.
Each round's worker replays the same 1,280-bar synthetic archive three times.
Every result must match the unpaced oracle for that case. The worker retains
the existing lifecycle guards, including SIGKILL on parent death, bounded pipe
messages, cleanup on cancellation and verified kill/reap of a stopped child.

Per worker, the enforced CPU-time ceiling is 30 seconds, address space is 512
MiB, affinity is one allowed CPU and niceness is 19. The logical archive-read
budget is 184,320 bytes, burst 4,096 bytes, with pacing at 65,536 bytes/second
for one venue and 9,362 for seven. This deliberate pacing keeps research present
during the early cancel/fill phase. It does not guarantee overlap throughout
every later phase. The child is reaped before the next worker starts.

These are CPU-time/address-space ceilings and logical-read pacing, not deployed
CPU bandwidth, RSS or physical-device quotas. Parent CPU/memory are not capped
by this experiment. Parent task/WAL/process samples can miss transient peaks;
parent lifetime peak RSS is not an independent per-case memory measurement.
The existing resource document explains the unchanged worker mechanisms.

## Failed measurement and reconciliation change

The first three-round run completed in 251.45 seconds and exited **1**. Its
seven-venue maximum fill lag was **10.802 seconds**, exceeding the declared
10-second limit. Every financial, identity, order and worker-lifecycle check
passed. The failed report is retained; its timing failure is not waived.

The executor previously re-bound and re-posted every historical fill/terminal
report on every complete snapshot. Even idempotent events created fresh request
records and rewrote current snapshots. Repeated rounds accumulated this work.

The executor now constructs a bounded validation plan inside its sole writer:

- Every fetched page, request/client identity, attempted order, order amount,
  scope, generation and complete-history omission check is still validated.
- A binding is reusable only when the complete stored allocation/order/venue/
  account/symbol/external/client tuple exactly matches the expected tuple, with
  no competing binding under either identity.
- A fill is reusable only when both the scoped external trade key and immutable
  stored fill row exist, the order ID and canonical Fill payload match exactly,
  and its external order has the verified binding. Missing/conflicting halves
  refuse the whole snapshot before any new fill from it is posted.
- A terminal report is redundant only when the durable order is already terminal
  and the incoming terminal cumulative amount agrees with its posted fills and
  FILLED/REJECTED amount rules. Open, partial, uncertain and changed facts still
  take the normal application path. All incoming trades still contribute to
  complete quantity, actual-fee and settlement-limit validation.

There is no retained financial cache or TTL shortcut. Reconciliation still
durably holds the allocation, fetches complete authoritative mock history,
posts every new fact, handles unsent revocations, validates fees/stream epoch/
uncertain intentions, and durably records final reconciliation. SQLite schema,
financial posting and state-machine rules are unchanged. Fewer duplicate
request/event audit rows and resulting version increments are intentional;
request counts are not promised to match the previous executor.

New regression tests cover compacted in-memory history with durable fills,
three duplicate complete snapshots, altered old fill prices/fees, mismatched
trade identities, a missing trade key and a corrupted binding. A valid new fill
sorted before a contradictory old one must remain unposted. Open partial orders
still receive current reports and later distinct fills still post normally.
Existing crash-boundary and recovery tests remain required.

A separate compatibility probe loaded the previous committed executor from
`git show HEAD:src/grid/execution/executor.py` into an isolated Python module.
On the same immutable fixture/configuration per pair, old and new executors
produced identical complete portfolio reports for an exit, delayed BUY fills,
partial fills and nonzero quote fees. All four reached the same completed exit;
the partial case retained six fills and each other case three. The retained
receipt is `/tmp/grid-i8-mixed-portfolio-compatibility-v2/receipt.json`.
Its predecessor directory contains a refused diagnostic fixture with invalid
zero latency; it is not evidence of a completed comparison. The published
application and its approved latency bounds were unchanged by that probe.

## Comparable local measurements

The same three-round command was rerun after that change, without changing the
workload, worker limits, synthetic clock, 10-second lag threshold or financial
fixture. It completed in **80.56 seconds**, exited **0**, and passed every
declared check. Neither measured run competed with the full regression suites.
These are single local observations, not a controlled statistical speedup study
or a latency guarantee for deployment.

| Three-round measurement | 1 venue before | 1 venue after | 7 venues before | 7 venues after |
|---|---:|---:|---:|---:|
| Distinct durable fills | 15 | 15 | 105 | 105 |
| BUY / SELL fills | 6 / 9 | 6 / 9 | 42 / 63 | 42 / 63 |
| Maximum fill lag | 1.0144 s | 0.1867 s | 10.8020 s | 2.0875 s |
| Maximum service wall time | 1.0184 s | 0.1783 s | 1.0590 s | 0.1940 s |
| LIMIT submissions | 19 | 19 | 133 | 133 |
| Cancellation requests | 12 | 12 | 84 | 84 |
| Synthetic page calls | 62 | 62 | 434 | 434 |
| Late BUY fills | 2 | 2 | 14 | 14 |
| Additional seeds | 0 | 0 | 0 | 0 |

Lag is real monotonic time from an invented fill arriving to its committed
identity being observed by the writer-owned inspection. It includes queued
work on other scopes; it is distinct from service wall time. The fixture's
freshness/rate clock advances 1,001 milliseconds per operation and is explicitly
synthetic. This does not demonstrate actual exchange rate-limit compliance.

Both runs rejected 144 excess hints per case and held the SELL peak to one.
Final base inventory and position cost were zero, and all orders were drained.
Every per-round financial hash, final/reopened hash, final cash/base/cost/realized
tuple, order/fill/page count, fixture digest and declared work cap matched
between the two runs. The final financial hashes are:

- One venue: `d54ebf0169791e2c0465151a0eff010f53ed603cc43ba650ec3614cb22887fb2`.
- Seven venues: `2f19e629fd74afa53eab5380b56be5ab96ac77fbd5c15aefc86d0e48265237ed`.

Financial receipts hash both ending money and canonical durable fill rows.
The one-venue fixture ends at 1,051.25 QUOTE; the seven venue balances vary with
their declared precision/rules. This invented profitable path is a consistency
oracle, not evidence of strategy returns.

Retained local artifacts, outside Git:

- `/tmp/grid-i8-mixed-contention-before/`: failed report, CLI receipt and summary.
  Report SHA-256: `e5e0023b7d8146cbca505f2209f71692a33eea6bb6ed0f8bc22e3639098fd7bd`.
- `/tmp/grid-i8-mixed-contention-after/`: passing report, CLI receipt and summary.
  Report SHA-256: `89a2445aa45f895a73c0b80d4196109e1b8b3c47e58e2cb97fc2a3ef1d312f81`.
- `/tmp/grid-i8-mixed-contention-compatibility.json`: exact field/hash comparison.

The before application SHA-256 is
`7d0e4f625be4fe1cbdf08fd75feac019fdd069a297b84aa66e6088209017f7e2`;
after is `d1d9ecd96a04ce2fc536006e87881e65fb0710a786836b6237790408f70227a3`.
Both reports name base `24bae1d` while truthfully reporting the uncommitted
source mismatch. Do not rewrite this historical provenance after committing.
The measured host was Linux 6.8.0-124-generic, x86_64, 32 allowed CPUs, ext4;
the report retains observed host/runtime details. This is not deployment evidence.

## Reproduction, validation and remaining scope

Use the locked, reviewed mock Python/SQLite runtime from the runbook, and a new
output path:

```sh
uv run --locked python -m grid.operations contention --mixed-rounds 3 --code-revision "$(git rev-parse HEAD)" --sqlite-source-id '2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9' --output /tmp/grid-mixed-review-new/report.json
```

Application/tests remained frozen throughout both complete suites, and both
process handles reached successful terminal results:

| Python | Result | Pytest elapsed seconds |
|---|---|---:|
| 3.12.16 | 1,171 passed; zero failed/skipped | 440.98 |
| 3.13.18 | 1,171 passed; zero failed/skipped | 451.73 |

The wrapper `/tmp/grid-mixed-contention-check.py` invoked `python -m pytest -q`
through each existing locked environment and checked application SHA-256 before
and after. Both equal the measured after-source hash above. Receipts/logs are
under `/tmp/grid-i8-mixed-contention-final-checks/`, with stems
`3.12-1793841466758405728` and `3.13-1793841466759761588`; wrapper elapsed times
were 441.488270 and 452.264448 seconds. Nineteen new cases cover the mixed
workload and immutable-history validation. Completed additional checks:

- Ruff lint and format: pass, 249 formatted files; strict mypy: pass, 187 files.
- Bandit over application/scripts and `uv lock --check`: pass, 66 locked packages.
- Full-group locked export plus strict `pip-audit`: no known vulnerabilities
  reported by the advisory check; this is not a claim of vulnerability absence.
- Secret scan over all 259 tracked/new files: pass, baseline unchanged.
- All four original specifications match their recorded SHA-256 fingerprints
  and supplied attachments. Dependency manifests and secret baseline match HEAD.
- Wheel: built with `uv build --wheel --no-sources`; all 85 application modules
  byte-verified, with no generated databases or market-data payloads.

Wheel/source archives are under `/tmp/grid-i8-mixed-contention-dist/`. The source
archive is built after final documentation with `uv build --sdist --no-sources`.
`/tmp/grid-mixed-contention-packages.py` verifies exact bytes for all 85 wheel
application modules and 254 required source/test/document/root-manifest files
in the source archive, plus absence of generated database/data payloads. The
verification receipt with both artifact SHA-256 values is
`/tmp/grid-i8-mixed-contention-package-verification.json`. The final secret scan
and `git diff --check` precede the scoped commit.

The new harness tests exercise repeated 1/7-venue flows, independent money
checks, exact reopen, limits before artifact creation, stopped-child exception/
cancellation cleanup, corrupted worker results, forged external reopen, failed
timing publication, exclusive outputs and sanitized all-or-fail CLI behavior.
No test treats host-dependent elapsed timing as a universal performance pass.

This finite isolated executor workload does not test the portfolio supervisor,
consolidated +10% exit or external alert delivery. Existing separate portfolio
tests remain the evidence for those mocked financial paths. It does not cover
1,000 funded allocations, a sustained fixed-rate fill stream, multiple concurrent
researchers, actual private venue feeds, production quotas, cold NVMe contention,
hardware power loss or the required observed 48-hour soak. Clean writer reopen
is distinct from the existing crash/fault tests. Temporary inputs/ledgers are
removed on ordinary cleanup; retained JSON includes their manifests and hashes.

For rollback, review and revert this scoped commit through normal Git history;
there is no database migration or host service to undo. Preserve measurement
artifacts and original inputs. Reverting the optimization restores the measured
slow historical-posting behavior and does not erase its failed latency result.
GitHub Free-only local validation applies; the known hosted-CI lock is unchanged.
No hosted CI retry, paid feature, additional public request, I9 approval or
deployment is implied. Full I8 acceptance remains incomplete.

Publication follows the established authorized scoped commit/push workflow;
no PR is created. The commit includes `[skip ci]` to avoid retrying the unchanged
account lock. A skipped hosted run is not a CI pass. Ordinary revert preserves
published history, and no force-push is part of this workflow.
