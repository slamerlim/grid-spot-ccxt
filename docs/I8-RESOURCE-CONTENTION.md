# I8 offline resource contention and schema-cache optimization

This increment follows `4c44624b203ee25f2c271f1fc4d46b62aeeb9d2b` under the
owner's instruction to continue mock development after the completed public
hour. It implements a bounded research/fill-contention experiment toward
ADR-0001 benchmark 6 and Q18. It does not certify deployment sizing or close
the original I8/first-venue/I9 gates. No exchange requests, accounts, orders,
installed services or paid GitHub capabilities are involved.

## Experiment and financial evidence

Run using the approved local Python/SQLite test environment:

```sh
uv run --locked python -m grid.operations contention --code-revision "$(git rev-parse HEAD)" --sqlite-source-id '2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9' --output /tmp/grid-contention-new/report.json
```

The coordinator runs the 1-venue and 7-venue cases sequentially. Each has one
funded allocation per logical venue, one authoritative `AsyncWriter`, one
`MockExecutor` and the finite `SyntheticExchange`. Every allocation receives
1,000 invented QUOTE and submits one minimum LIMIT BUY for one base unit at
100. Forty distinct executions of 0.025 base fill that same seed; they are
partial executions, not enlarged or repeated entry orders. Fees are explicitly
zero in this fixture and its synthetic market clock is fixed. Real monotonic
time is measured independently for resource/lag observations.

One distinct fill per scope is reconciled while a separate research process
has an in-flight replay job. Then the remaining fills are created, followed by
64 private hints against the 16-entry intake. The 48 refused hints invalidate
readiness; actual fill facts remain in the bounded synthetic venue. The
coordinator stops research, confirms the owned child's Linux stopped state,
and reconciles all scopes. New order dispatch must remain blocked while each
scope is unresolved. Before research resumes, intake/writer queues must be
drained and executor reconciliation complete. CPU ticks must not advance while
the research child is confirmed stopped.

The experiment checks each scoped trade identity against durable fills and
verifies cash 900, base 1 and position cost 100 per allocation. It repeats
reconciliation, closes and reopens the financial writer, then compares the
exact financial rows/hash again. Only the original seeds may have been sent.
This exercises 40 and 280 distinct fills, not one fill counted through duplicate
notifications. It is a clean writer restart; crash/power-loss acceptance still
depends on the separately documented fault tests and deployment evidence.

The worker runs the existing basket replay three times over 1,280 generated
minute candles. Each pass starts fresh. All hashes must match an unpaced run
of the same immutable configuration and source. These are repeated workload
passes, not three compounded trading cycles. Each pass reads validation plus
replay, totaling 184,320 charged logical archive bytes across the three passes.
The report retains the full replay config, input manifests, repeat hashes,
financial fixture hash, resource observations and invariant results.

Lag is measured from synthetic fact creation through completion of that scope's
reconciliation service. It is an upper bound for each included commit, not a
per-transaction latency probe or actual exchange-to-ledger latency. The declared
fixture ceiling is 10 seconds. Timing failures are valid measurements: publish
the report with `resource_checks_passed=false` and return exit 1. Input, lifecycle
or financial failures return exit 2 without a completed report; interruption
returns 130 when caught. Exit 0 means the measured fixture checks passed, not
production readiness. Unit tests verify status consistency instead of assuming
shared-host timing is deterministic.

## Resource limits and their exact scope

The disposable worker has a 30-second process CPU-time ceiling, 512 MiB address-
space ceiling, one CPU from its inherited affinity, nice level 19, 32 open-file
limit, and zero core/file-output limits. Stricter inherited soft or hard limits
are retained; requested ceilings never relax them. The worker reads back applied
limits before announcing readiness. It starts with a minimal environment and isolated
Python invocation. The financial owner retains its original scheduling limits.

A context-local integer token bucket charges every raw history record before
access, including both validation and replay. It permits 65,536 logical bytes
per second, a 4,096-byte burst and the total byte cap above. Long pauses can
refill only the burst allowance. An exhausted budget raises before the next
record access; nested contexts restore their preceding budget even on failure.
Ordinary research commands have no active budget unless explicitly run within
this controlled worker. Unpaced replay behavior and financial hashes remain
unchanged.

These controls have important distinct meanings:

| Control / observation | Meaning and limit |
|---|---|
| `RLIMIT_CPU` | Lifetime process CPU seconds; not a CPU bandwidth quota |
| `RLIMIT_AS` | Virtual address-space ceiling; not an exact RSS quota |
| Affinity / nice | Restricted worker CPU choice and scheduling priority; no reserved parent CPU |
| Logical byte pacing | Archive record requests only; not kernel readahead, import/config I/O, page-cache or device bandwidth |
| `/proc` RSS and CPU ticks | Samples around overlap/pause; not continuous peak monitoring |
| `ru_maxrss` | Worker lifetime RSS peak, including startup |
| `/proc/self/io` | Worker lifetime kernel counters; cached logical reads may yield zero device read bytes |
| Writer depth | Post-service samples, not proof of transient queue peaks |

These distinctions follow the [Python resource API](https://docs.python.org/3.12/library/resource.html).
Kernel CPU bandwidth, memory and device-I/O controllers have separate semantics
in the [cgroup v2 documentation](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html).
The experiment does not create or reconfigure host cgroups. The inspected
workspace leaf advertised CPU, memory and PID controllers, without an IO
controller. This is capability evidence, not an assertion that no other
environment could supply properly delegated limits.

The child uses a bounded ready/active/done pipe protocol. Pressure pauses only
its owned process, with PID/start-time identity checks; in-flight kernel I/O may
still finish after stopping user-space work. Graceful failure/cancellation kills
and reaps the child, including cancellation during process creation. No retry
loop starts replacement workers. The enclosing case has a 60-second timeout;
individual protocol/cleanup waits are also bounded. Temporary mock inputs and
ledgers are removed on ordinary cleanup. A process kill can retain diagnostic
files; never label them a completed report or delete original data as recovery.

A crash helper reproduced a stopped child surviving a killed parent before the
lifecycle guard was added, then explicitly killed/reaped that diagnostic child.
The worker now sets and reads back Linux `PR_SET_PDEATHSIG=SIGKILL` before
readiness, checking its expected parent both before and after installation to
close the startup race. A separate subreaper test kills the owning parent while
the worker is stopped and verifies worker termination. This signal follows
the creating parent thread, as documented in the
[Linux parent-death API](https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html).
The test helper's subreaper setting is confined to its own disposable process;
the coordinator and host process tree are not reconfigured.

## Measured bottleneck and financial compatibility

The first seven-venue version preserved its fills but failed the declared
10-second lag threshold. Profiling a one-venue 40-fill case found 122,231 calls
to `typing.get_type_hints`, taking about 11.39 cumulative seconds under profiling.
This repeated schema reflection accounted for a large part of that profiled
run; profiler overhead makes those figures unsuitable as uninstrumented latency.

`persistence/codec.py` now lazily caches only type definitions from its fixed
54-class allowlist, in an LRU bounded to that registry size. Cached mappings
are read-only. No balances, reservations, snapshots, payloads, fills or market
facts enter the cache. Every payload still undergoes type/schema, constructor,
numeric, nesting, size, canonical-byte and normalization checks. Runtime schema
hot mutation is unsupported; restart on application-code changes.

Seventeen representative context/state/universe/portfolio/fact snapshots produced
identical bytes and decoded values with the previous committed codec loaded
independently. Warm-cache tests still reject corrupted financial types and
unknown records, and attempts to mutate cached definitions fail. Existing
golden financial records and the full regression suite remain required evidence.
One subsequent seven-venue run with the unchanged 40 fills per scope observed
about 3.78 seconds maximum lag. This is a local observation, not a controlled
speedup ratio, universal latency promise or verified deployment SLO.

## Remaining scope and validation

The inner schema is `grid-resource-contention-v1`, wrapped in the existing
`grid-research-run-v1` provenance envelope with operation `resource-contention`.
Parent process CPU excludes children; each worker reports its own CPU separately.
Source/runtime snapshots and Git-byte checks retain their existing limitations.
No whole-process memory or loaded-dependency attestation is implied.

This harness covers a bounded synthetic seed-fill storm and one research worker.
It does not cover 1,000 funded allocations, a sustained mixed BUY/SELL workload,
real private feeds, multiple competing researchers, hardware power loss, cold
NVMe reads, kernel device bandwidth quotas or deployed RSS limits. The existing
candidate-selection matrix remains separate. Do not run unrestricted research
beside a future trading owner until actual deployment quotas and workload
acceptance have been demonstrated. Full Q18/ADR hardware acceptance remains open.

Final local validation on 2026-11-04 UTC:

| Check | Observed result |
|---|---|
| Python 3.12.16 full suite | 990 passed in 226.15 seconds |
| Python 3.13.18 full suite | 990 passed in 234.93 seconds |
| Source stability across both suites | Identical application digest before and after |
| Ruff lint/format and strict mypy | Passed; 232 formatted files and 176 typechecked files |
| Bandit over application/scripts and tracked-file secret scan | Passed; secret baseline unchanged |
| Locked resolution and dependency advisory audit | 66 packages; no known vulnerabilities reported |
| Wheel and source distribution | 82 application modules and 236 required source/test/document/manifest files byte-verified |
| Original specifications | All four original SHA-256 fingerprints unchanged |

The final application digest was
`02e5780cadcdcc8fc18ae593fbf28689c3de26456b869fd8fdb1ac0a79403837`.
Full-suite receipts and logs remain outside Git under
`/tmp/grid-i8-contention-final-checks/`, with final stems
`3.12-1793830672529287513` and `3.13-1793830672523699370`.
Earlier receipts there describe intermediate code and are not the final result.
Tests exercise actual CPU/address-space refusal in disposable children, stricter
inherited limits, launch cancellation, stopped-child cleanup, parent death and
its startup race, exhausted reads and all-or-fail report publication. A deliberate
zero lag threshold verifies that failed timing remains a published measurement
with exit 1. No generated ledger or data payload was included in the wheel.

After both suites finished, the standalone command exited 0 and published
`/tmp/grid-i8-contention-20261104T222148Z/report.json`:

| Measurement | 1 venue | 7 venues |
|---|---:|---:|
| Distinct durable fills | 40 | 280 |
| Maximum measured fill lag | 0.5320 s | 3.7894 s |
| Worker replay CPU time | 0.7404 s | 0.7381 s |
| Worker replay wall time, including pause | 3.2396 s | 6.5987 s |
| Worker lifetime peak RSS | 31.45 MiB | 34.95 MiB |
| Charged logical archive bytes | 184,320 | 184,320 |
| Kernel device read bytes | 0 | 0 |

Both cases passed every declared check, rejected 48 excess hints, verified the
child's stop, preserved exact reopened financial hashes and retained identical
results across all three resumed research passes. Zero device read bytes is
consistent with cached inputs; it does not establish cold-device performance.
The report's artifact SHA-256 is
`ddc2d1bbc7f50b5ce3da7ba7048e9284e017b53967c79a7ae63dbea40cf06ff9`.
It records the final source bytes before this increment was committed, so its
supplied base revision `4c44624` correctly has `revision_verified=false` and
`source_matches_head=false`. Do not relabel that historical provenance after
publication. These measurements establish only this finite synthetic workload.

Hosted CI remains pending under the unchanged account lock; use local validation
and GitHub Free-only `[skip ci]` publication. Neither the dependency audit nor
these local checks establish full I8 or deployment acceptance.

Rollback by omitting the new `contention` operation or reverting the increment
through an ordinary new commit. There is no database migration, dependency
change, service installation or altered financial serialization to undo.
