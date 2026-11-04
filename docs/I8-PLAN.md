# I8 proposal — operational preparation, then separately gated observed soak

**Status: approved for preparation only.** The owner explicitly selected
"Approve I8 operational preparation with local validation". This authorizes
**I8 operational preparation with local validation** only. Preparation
does not satisfy or waive the original I8 acceptance requirement for an observed
minimum 48-hour paper/sandbox soak and intended-first-venue evidence.

## Requested authorization: preparation

Use synthetic exchanges/facts, immutable existing inputs, temporary mock ledgers
and bounded local experiments. Use GitHub Free only and the existing local tools.
Hosted CI remains pending; do not retry the unchanged account lock or use paid
GitHub features. No exchange access, credentials, public feeds, sandbox/real
orders or cancellations, external notifications, transfers, installed service,
deployment, live database or long-running soak is authorized by this proposal.

Concrete deliverables:

1. An explicitly mock-only supervised runner combining universe, portfolio,
   executor and single-writer lifecycle. Bound its scheduling, caches, queues,
   recovery retries and shutdown, and retain responsibility for uncertain orders.
   Expose typed local status/control with audited revisions and distinct pause,
   reconcile, retirement and exit semantics. No live arming path.
2. Local structured operational status and alert records for stale evidence,
   unknown orders, blocked exits, reconciliation drift, resource/backlog limits,
   storage faults and backup freshness. Include trigger/final NAV, shortfall,
   residuals and actual fees without logging credentials. No alerts are sent
   externally; any future destination needs explicit authorization.
3. Bounded synthetic recovery and load runs for one/seven venue owners and
   10/100/1,000 candidates where feasible. Record actual host profile, duration,
   CPU/RSS/WAL/queue measurements and refusal behavior. Define fixture resource
   caps before running; do not infer production sizing from small tests or
   invent target-host acceptance thresholds.
4. Temporary mock-storage backup/restore and disk-full/permission/fault drills
   confined to disposable locations. Preserve original datasets, imported specs
   and financial history. Produce a reviewed least-privilege systemd template
   and operations/runbooks as files only; do not install or start a service.
5. A reproducible soak manifest/report format and a short synthetic dry run that
   distinguishes simulated elapsed time from observed wall time. Prepare a
   concrete later execution plan with first venue/symbols, source endpoints,
   public polling/subscription budgets, account requirements if any, destination,
   retention/disk/CPU limits, restart/disconnect schedule and stop conditions.

Run both Python suites and README lint/type/security/build checks. Publish exact
preparation evidence, any limitations and the later soak plan; stop for review.
Retain known lock/dependency versions unless evidence justifies a scoped change.

## Separate gate: actual venue evidence and minimum 48-hour soak

The owner must approve the concrete later run and its external access before it
starts. Read-only public feeds require authorization and bounded endpoint usage.
Authenticated read-only probes need authorized account access and permissions;
sandbox orders/cancellations require their own explicit approval. Do not discover
credentials. Never substitute private events from a synthetic venue for observed
real account evidence, or public candles for actual execution/queue evidence.

Record at least 48 observed wall-clock hours, injected disconnect/restart events,
restore evidence, complete ownership/fill/fee reconciliation, resource metrics,
and zero unexplained balance drift or invariant breaches. Interrupted or shorter
runs are partial evidence, not a pass. The intended first venue's matrix must
identify supported/unsupported/unverified order identity, fees, recovery and
private events. Missing real evidence keeps I8 acceptance incomplete and all
venues paper-only. No local test, synthetic dry run or document completes this gate.

Live canary/deployment remains I9 with separate per-venue/account/funding/risk
signoff, irrespective of any I8 approval. Roll back preparation by a scoped
code/docs revert; preserve audit/approval history and GitHub Free restrictions.
