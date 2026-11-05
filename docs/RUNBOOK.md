# I0–I4 developer runbook

I0 provides an offline configuration/diagnostic CLI; I1 adds pure strategy
functions and mock-event tests. I2 adds a single-owner ledger tested only with
temporary synthetic databases. I3 adds an explicitly invoked public CCXT client;
I4 adds an explicitly driven mock executor and synthetic recovery supervisor.
There is no daemon, authenticated client or live path.
This is not a production operating runbook. No exchange credentials are needed.
See `I1-DESIGN.md` and `I2-DESIGN.md` for the command and persistence contracts.

## Explicit public discovery

The approved I3 pass is complete; do not run it automatically as a check, retry,
daemon or CI job. Within a separately authorized bounded discovery session, the
reproduction command is:

```sh
uv run --locked python -m grid.exchange.probe --public
```

It selects BTC/USDT only, at most two public GET attempts on each of seven venues,
with finite time/response/depth budgets and no retries. It creates no database,
loads no credentials and supports no private/write method. Normal `gridctl doctor`
and pytest remain offline. Keep generated probe reports outside Git; publish
curated, sanitized findings in the capability/evidence documents.

Inspect per-venue statuses, not just process exit zero: a completed evidence
report can contain rejected or unavailable observations. XT's first public book
was rejected; its precise cause is unresolved. Do not rerun to turn it green.
Account-specific authentication and fee/event semantics remain NOT_TESTED.

The current numeric boundary uses CCXT's documented `number = str` mode, with
Decimal conversion in the shared application mappers. See
`I8-CCXT-UNIFIED-NUMBERS.md` before changing that mode or upgrading CCXT. The
pinned book-level float conversion and string-price sorting have narrow tested
corrections; do not restore a custom SDK-wide Decimal/safe-string mode or replace
CCXT's venue parsers. Run the offline fixtures, not another public probe, when
checking this boundary. Historical public evidence is not rerun by this change.

`I8-UNIFIED-RECONCILIATION.md` documents the mapped fake's unified dictionaries
and ownership bridge. Run `pytest -q tests/execution/test_unified.py` offline to
exercise CCXT parser output through fee posting and restart. Keep account scope,
snapshot completeness, chronology and settlement valuation explicit; never use
array arrival order as a real fill sequence. This adds no private transport.

`I8-TERMINAL-RECOVERY.md` covers current reports that reopen a durable terminal
order or change its cumulative quantity. Recovery refuses the whole snapshot
before new binding/fill posting and keeps dispatch blocked. Preserve the reports
and investigate the source; do not rewrite old fills or manually free the SELL
slot. A delayed private OPEN event and a current recovery report have different
authority. Run `pytest -q tests/execution/test_reconcile_history.py` offline.

The developer-only `read_order_history` helper in `exchange/private.py` is
described in `I8-ORDER-HISTORY.md`. Its bounded unified calls return observations
only; incomplete history, changing totals and interrupted reads do not authorize
retries or replacement. The caller owns the transport's rate/HTTP byte budget
and lifecycle. Run `pytest -q tests/exchange/test_order_history.py` using the
offline fixtures. No operator command enables private access through this helper.

I3 rollback is a scoped code/docs/lock revert with `uv sync --locked`; no deployed
database or external order requires rollback. Preserve the GitHub Free preference
and approval history. I4–I7 were subsequently approved; `I8-PLAN.md` is the next gate.

## Install and diagnose

Use Python 3.12 or 3.13 and the project's single `uv.lock`. CI pins uv 0.13.7.
Run from the repository root:

```sh
uv sync --locked --dev
uv run --locked gridctl doctor
uv run --locked gridctl doctor --config config.example.yaml
uv run --locked gridctl doctor --config config.example.yaml --json
uv run --locked gridctl doctor --config config.example.yaml --check
```

The first dependency installation may access the package registry. The doctor
itself must not contact exchanges, discover credentials or create a database.
Plain doctor prints `LIVE: DISABLED` and reports missing prerequisites; JSON is
for machine inspection. No supplied config uses safe built-in diagnostic defaults
with live unavailable; this is not a trading configuration.

Exit statuses: normal diagnostic output is **0**; `--check` with unmet readiness
is **1**; invalid configuration/arguments are **2**. A successful diagnostic
command means it produced a report, not that the bot is live-ready. I0 readiness
cannot become true even if the operator supplies all future policy values.

The sample deliberately leaves required capital, spacing, currency and exit
policy unset and all venues disabled. Do not replace missing values with invented
"safe" funds. Setting a LIVE environment variable must not unlock an execution
path. Secrets do not belong in the sample or command-line arguments.

## Checks before a commit

```sh
uv lock --check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
git diff --check
git status --short
git diff --cached --stat
```

Follow the README for the complete current security/packaging command set and
the evidence report for commands actually run. Review staged content and ignore
rules before committing: no `.env` files, credentials, wallet state, SQLite
files/WAL/SHM, raw history, legacy binaries, research outputs or generated reports.
Lint/type/test success does not substitute for secret review. Keep the intended
sample config, imported text specifications and lockfile tracked.

The application/test checks run without exchange credentials or exchange
networking. Synthetic configuration fixtures and mocked metadata must be clearly
distinguished from observed venue behavior. I1 covers parts of Q01–Q11 and Q17
in pure domain tests; it does not pass the complete financial/execution gates.
See `I1-EVIDENCE.md`, the trace and the capability registry.

## Diagnostic findings and failures

- **Unsupported SQLite:** report the Python-linked version. The preparation
  environment reported 3.45.1; it fails the accepted future-live policy of
  3.51.3+ or a specifically verified fixed backport (3.50.7/3.44.6). No database
  is created or upgraded in I0. Replacing Python alone is not evidence of a fixed
  linked SQLite library.
- **Invalid config:** correct the indicated field/type without displaying secret
  values. Unknown keys, invalid financial numbers and unsupported modes must
  fail closed. A permissive parse is not permission to run future trading code.
- **Dependency/check failure:** preserve the error and environment/version in
  the evidence report; correct it before claiming the gate. Do not regenerate
  multiple lockfiles or bypass locked installs to hide a mismatch.
- **Repository inaccessible/unwritable:** retain the local branch and commits,
  record the exact actual access/push result, and report the missing permission.
  A public readable repository does not prove push authority. Do not claim a
  remote commit, PR or hosted CI run unless verified.

## GitHub Free and account availability

The owner confirmed on 2026-11-03 that this project uses GitHub Free and must
never use paid GitHub features. Do not activate paid plans or trials, use paid
runners/products or usage overages, add payment details, raise spending limits,
or recommend an upgrade/payment to make progress. Use local tools and verified
free capabilities; ordinary publishing authorization does not permit spending.

The repository was verified public. Its standard `ubuntu-latest` runners are
free under [GitHub's Actions billing guidance](https://docs.github.com/en/actions/concepts/billing-and-usage).
The workflow's job condition prevents runner allocation when the repository is
private or visibility is missing. It uses no larger runners, remote cache or
artifact uploads, or package publishing. Do not infer the availability of a
private repository's free quota.
Recheck official terms and repository visibility before changing automation.

The observed account billing-lock annotation is evidence that hosted checks
could not run; it does not establish that this workflow is paid or that an
upgrade is needed. GitHub documents that account locks can also block free-tier
access. When the account shows no subscription, balance or payment problem,
the documented route is a support review of the lock, without activating paid
features. See [GitHub's account-lock guidance](https://docs.github.com/en/billing/how-tos/troubleshooting/locked-account).
Do not send support messages on the owner's behalf without explicit permission.

Do not dispatch/rerun a workflow or make an empty commit just to retry a known
account lock. Run the local checks and report hosted CI as unavailable. Push
substantive authorized work only after confirming its automation remains free.
While the lock is unchanged, include the documented
[`[skip ci]` directive](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs)
in the commit message for necessary pushes, after confirming all workflows use
only the supported `push`/`pull_request` events. This avoids another hosted
attempt; it does not satisfy CI or authorize bypassing a release gate.
The owner explicitly replaced I1's earlier hosted-CI prerequisite by selecting
"Proceed with local validation for I1", then explicitly selected "Approve I2
with local validation" for the published I2 plan and "Approve I3 with local
validation" for the public read-only I3 plan, then "Approve I4 with local
validation" for mock execution and recovery. Hosted CI remains pending,
and the original I0 hosted acceptance gate is not reported
as passed. A skipped/private job is not a CI pass.

## GitHub publishing from this workspace

The owner requested reusable Git access for agents working in `/opt/grid`.
The prepared checkout uses these repository-local settings:

- `origin`: `git@github.com:slamerlim/grid-spot-ccxt.git`.
- Default commit author: `GRID Automation <grid-automation@localhost>`; this
  identifies automation without attributing commits to the owner's account.
- `core.sshCommand`: selects `/root/.ssh/grid-spot-ccxt_ed25519`, disables
  interactive authentication and SSH-agent fallback, and requires the pinned
  GitHub Ed25519 host key in `/root/.ssh/grid-spot-ccxt_known_hosts`.
- `push.default=simple`, `push.autoSetupRemote=true`, and
  `remote.pushDefault=origin`.

The private key is outside the repository with mode `0600`. The host key was
checked against [GitHub's published SSH key fingerprint](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints).
The owner must add the corresponding `.pub` file under repository **Settings >
Deploy keys**, with **Allow write access** enabled. See
[GitHub's deploy-key instructions](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys).
Local preparation does not prove that GitHub authorization has been completed.

This setup is reusable by agents running as the same OS account in this host's
checkout. It is not transferred by cloning the repository and does not grant
access to other users, hosts or containers. The deploy key grants Git access to
this repository; API operations such as opening PRs require separate API
authentication. A ChatGPT plugin is not required for terminal Git operations.

After owner authorization, check access without changing the remote:

```sh
git remote -v
git var GIT_AUTHOR_IDENT
git ls-remote --heads origin
git push --dry-run origin HEAD
```

Public read access or a successful dry run is not proof that a real push,
including workflow-file changes and branch rules, will succeed. Follow the
checks above, review and commit only the intended files, then publish the
current branch and compare its remote commit:

```sh
git status --short --branch
git diff --cached --check
git diff --cached
git commit -m "Describe the approved change and its purpose"
git push --set-upstream origin HEAD
git rev-parse HEAD
git ls-remote --heads origin "$(git branch --show-current)"
```

Do not force-push or alter repository protections as part of routine publishing.
If authentication fails, retain local commits and report the exact failure.
Never print private keys or paste tokens into chat. Existing I0 publication
evidence records historical failures; do not reinterpret them as successful
pushes. Report hosted CI separately from local validation.

To revoke this workspace's GitHub access, remove its deploy key in the repository
settings. Treat a compromised host as requiring key replacement. If GitHub
changes its SSH host key, verify the replacement against its official guidance
before updating the pinned host key; do not disable host-key checking.

## Rollback and next gate

I0 has no migrations, financial database, service or deployed orders. To undo an
I0 change, review and revert its scoped Git commit on a branch, then restore the
matching locked environment using `uv sync --locked --dev`. Preserve unrelated
work and the immutable legacy/data paths. No database restore, asset liquidation,
remote reset or secret rotation is part of I0 rollback.

The owner approved `I1-PLAN.md` and its partial-seed anchor decision, then
explicitly authorized local validation for I1 despite the hosted account lock.
I1 was published and I2 subsequently approved with local validation. I2's mock
ledger/restore work is covered by that approval. I3 was subsequently explicitly
approved with local validation and bounded public probes. I4 was subsequently
approved with local validation for synthetic execution/recovery. I5 research and
I6 universe control and I7 portfolio exit were subsequently approved with local
validation. I8 operational preparation was then explicitly approved with local
validation. Its mock API, temporary drills, local alerts and uninstalled service
template are described below. Stop after preparation review: observed soak,
exchange access, deployment and live arming still need separate authorization.
`gridctl` remains an offline diagnostic; the finite experiment CLI is separate.

I1 adds no migration or external side effect. To roll it back, revert its scoped
implementation and documentation commits on a branch, then run `uv sync --locked`
and the I0 checks. Retain the GitHub Free preference and the approval history;
do not reset published history or change the imported specifications.

## I2 local ledger validation and recovery

Use isolated interpreters, leaving system Python unchanged. On the prepared
host, uv is at `/tmp/grid-tooling/uv`; substitute its installed path elsewhere.
The following commands explicitly select each environment, avoiding an ambient
`UV_PYTHON` silently switching the linked SQLite runtime:

```sh
UV_PYTHON=3.12.16 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py312 uv sync --locked
UV_PYTHON=3.12.16 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py312 uv run --locked pytest
UV_PYTHON=3.13.18 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py313 uv sync --locked
UV_PYTHON=3.13.18 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py313 uv run --locked pytest
```

Run the README's remaining checks in an explicitly selected environment too.
Do not use a SQLite version number alone as production certification. The test
source ID is an explicit fixture approval for these isolated builds. Full build
provenance, local NVMe/fsync behavior and operational performance remain future
gates. Overlay/tmpfs are admitted only with the explicit mock-storage flag.

The internal API is `AsyncWriter.open(..., bounds, approved_sources,
mock_storage=True)`, then `submit` with a short ledger operation. It creates,
uses and closes its SQLite connection in one worker thread. The synchronous
`Ledger` core exists for integration and fault tests. Neither API sends orders.
Provide an existing owner-only directory and explicit bounds; never use an
operator wallet path for tests. The normal CLI never invokes these APIs.

Keep the exact request ID and inputs until an operation's outcome is known.
On cancellation, lost response or an exception after commit, call `lookup(id)`.
A found receipt means that operation committed; replaying the identical request
returns it. It does not grant permission to resend an intent. A missing receipt
means no commit for that request, but external uncertainty still needs I4
reconciliation. A full queue refuses admission explicitly. Do not drop a private
fact or invent a new request ID to work around uncertainty. No automatic retry
loop is provided for BUSY, FULL, I/O failure or an excessive pinned-reader WAL.

For the mock restore drill, call `ledger.backup(new_path)`, retain the returned
hash/watermark, then `storage.restore(backup, separate_new_path, ...)`. Both paths
must be new owner-only files in suitable directories. Restore uses the online
backup API, validates integrity/foreign keys and compares the business-record
hash before any restored owner is granted. Startup also verifies schema and
financial projections/journal totals. A restored owner always requires explicit
normalized reconciliation. Never erase an uncertain intent or copy only an
active database file while ignoring WAL. A failed backup/restore artifact is
retained for inspection and is not reported as successful; do not reuse it.

Call `compact` before in-memory order/fill history reaches its configured bound.
It keeps working-order fee evidence, live lot provenance, the current seed,
the lowest current-round ladder price and monotonic identity/reconciliation
counters. SQLite retains all history and deduplication keys. If these facts
cannot fit, the system blocks rather than evicting financial evidence. The
checkpoint result records bounded WAL progress; pinned readers must be released
before a refused operation can be considered again.

I2 introduces schema version 1 only in temporary fixtures; there is no deployed
ledger to migrate or restore. Roll back code with normal scoped revert commits
and the matching lock. There is no automatic downgrade or destructive migration.
Preserve diagnostic failure artifacts outside Git when investigating a fault.

## I4 mock execution and recovery

`MockExecutor(writer, SyntheticExchange(...), scopes, limits, evidence)` is a
test-facing API. It cannot accept the public CCXT client or an authenticated
transport. Use only synthetic funds and temporary private database directories.
`service(scope, context)` performs a bounded fake REST reconciliation;
`dispatch(scope, context)` attempts at most one queued command. Startup always
requires service first. Neither is a trading CLI or background daemon.

After a timeout, lost acknowledgement, overload, stale stream or restart, keep
the same durable intent and reconcile. `attempt_once` refuses replayed send
permission. Never clear uncertainty manually or create a new intent to evade it.
`offer(Notice(...))` is a bounded hint intake; the mock uses REST facts for money.
Call `degrade(reason)` on an unsupported/disconnected stream. When backpressure
refuses a hint or operation, the caller retains it and schedules reconciliation.
Do not infer no fills from quiet streams, heartbeats, incomplete pages or missing
IDs. A cancel ack alone never releases the old order's reservation or SELL slot.

Service calls honor per-account request budgets and retry-after/backoff. Dispatch
does not retry unknown sends. The caller owns the finite schedule and a fresh
context/clock. A failed account cannot consume another account's quota. No
automatic worker/retry loop is installed. Inspect the boolean result and
`last_error`; `degraded` reports incomplete/expired admission across owned scopes.

Migration 2 preserves v1 and adds order/trade identities. Older fixtures and
validated v1 backups upgrade transactionally when opened; every reopened owner
must reconcile. v2 backups include identity records and deduplication keys. No
destructive downgrade exists. For mock rollback, revert scoped code/docs commits
and use a compatible validated backup in a separate new path. Never remove
identity rows or uncertain intents to force startup. No deployed database was
created or migrated by I4.

See `I4-DESIGN.md` and `I4-EVIDENCE.md` for synthetic contracts, actual tests and
remaining account/venue limitations. All live gates stay closed. The owner
subsequently approved I5 with local validation; see the section below.

## I5 offline history and replay

Use `gridctl backtest --help`, `gridctl bench-storage --help` and the bounded
examples in README. The existing `python -m grid.research` commands remain
available, including `validate` and `demo`. Research has no exchange transport.
The default replay has no financial database; optional `backtest --portfolio`
creates a private temporary mock ledger using a reviewed SQLite source ID and
explicit synthetic portfolio/liquidity settings. It never opens an operator's
ledger. See `I8-PORTFOLIO-RESEARCH.md` for that separate schema and its limits.
Keep source archives immutable and place new outputs outside Git. Do not run the
legacy converter/downloader or rewrite original files to make validation pass.
Supply the clean Git commit as `--code-revision`; uncommitted experiments need an
explicit source fingerprint and must not be labeled as a clean commit.
The CLI now captures that fingerprint automatically. It compares Python files
under `src/grid`, `pyproject.toml` and `uv.lock` with the observed local HEAD;
the supplied label must equal the full HEAD SHA for `revision_verified=true`.
This verifies only the stated comparison scope, not documentation/tests, loaded
bytecode, installed dependency contents or the whole repository's cleanliness.
Review those fields rather than treating a supplied label as an assertion of fact.
An installed wheel without its source checkout retains a source fingerprint and
explicitly unverified Git identity. Unknown Git does not prevent synthetic work.

`validate` returns 0 for a fully eligible selection, 1 for reported blockers and
2 for an error/refusal. Unknown identity/collection provenance remains a blocker
even if all OHLC bytes are structurally valid. Resolve provenance with independent
evidence, not by supplying invented assertion strings. Gap-free contiguous
windows may be selected explicitly, but do not conceal gaps in the original
archive or present a selected window as a whole-file certification.

The `demo` command emits synthetic input/configuration; edit research bundles via
the typed Python API and `config.bundle`, preserving quoted Decimal values and
explicit finite capital. `replay` requires complete matching UTC coverage and
publishes only after all sources have passed the second checksum/identity pass.
Use a distinct result path for each execution scenario. Result hashes bind the
scenario, source window, constraints, fees, configuration and code reference.
New CLI outputs have schema `grid-research-run-v1`. The previous report object is
stored at `result`, including its original `result_sha256` for replay. The outer
`result_payload_sha256` hashes the entire result object, and `report_sha256`
hashes the envelope excluding only itself. Consumers of old root-level fields
must check the schema and unwrap `result`; do not rewrite old retained artifacts.
Timestamps, host data and CPU measurements intentionally change the outer hash.
Compare the inner result/hash for deterministic replay equality. Standalone engine
APIs still return their existing result schemas without a provenance envelope.

`backtest --compare-scenarios` runs the strict/no-ambiguous-fill, touch/BUY-priority
and touch/SELL-priority conventions in fixed order. It also accepts `--portfolio`.
The inner result becomes `grid-scenario-comparison-v1`; inspect the full members,
their evaluation endpoints and any unavailable shared NAV range. Do not assume
the optimistic label has the highest return or tune live policy from this output.
All models must succeed on matching manifests for publication. See
`I8-RESEARCH-SCENARIOS.md` for limits, uncertainty, schema and validation.

Portfolio replay now includes `posted_performance` from committed ledger records
and `benchmarks` for cash/buy-hold with matching owned funds and ending times.
Read unknown benchmark marks as unavailable; a large benchmark can exceed the
NAV depth or order budget while the grid position can still be valued. Root venue
fill/fee totals can include unsettled fills, unlike the new committed statistics.
Gross wins/losses sum net realized postings, and profit factor requires losses
and at least two distinct filled SELL orders. See `I8-RESEARCH-PERFORMANCE.md`.

`allocation_risk` adds paired holdings/NAV attribution, unrealized inventory
PnL, reservations, exposure and observed drawdown. Internal conversion transfers
are adjusted using their committed postings and same-time explicit FX; unknown
historical transfer values cannot be filled from later data. Read completeness
flags and known-sample denominators before comparing results. The 20,000 retained
allocation-point bound is checked before history/ledger work; use fewer intervals
or a shorter study when refused. See `I8-ALLOCATION-RISK.md` for exact definitions.

`backtest --portfolio SETTINGS.json --portfolio-cycles N` explicitly requests
1–32 consecutive cycles with the same ledger and funds. A cycle can restart only
on a later bar after reconciled `EXIT_COMPLETE`; conversion allocations remain
paused. Values above 1 return `grid-portfolio-series-v1`, distinguishing per-cycle
metrics from original-funding performance. Read `completed_cycles`, `stop_reason`
and null completion returns before treating the requested series as finished.
The interval bound includes completion points and all work/storage caps remain
shared. It supports `--compare-scenarios`; with `--study`, cycles carry within
each independently funded study window. See `I8-PORTFOLIO-SERIES.md` and
`I8-PORTFOLIO-STUDIES.md` for the interfaces and limits.

`backtest --compare-lots` produces `grid-lot-comparison-v1` with complete basket
and fixed-anchor independent-lot members on the configured OHLC model. Read the
declared replenishment and fee-escrow differences before interpreting their NAV
difference. The flag excludes both `--portfolio` and `--compare-scenarios`;
neither a portfolio-exit comparison nor a live policy switch is implemented.
See `I8-INDEPENDENT-LOT-REFERENCE.md` for command, schemas, caps and evidence.

`backtest --study PLAN.json` uses basket replay by default for declared walk-forward
spacing experiments. Each scenario fixes fees, latency and fill convention;
selection uses training total NAV only, then every held-out candidate is retained.
Each window starts fresh, so returns cannot be compounded into a continuous
equity curve. `I8-RESEARCH-STUDIES.md` documents the strict plan, chronological
boundaries, all-or-fail output and caps. `--compare-scenarios` remains excluded.
Add `--compare-lots` without portfolio settings for a paired basket/reference
study. Each policy selects spacing from its own training NAV; complete results
and matched-candidate differences are retained. Replay/bar limits count both
policies before reading history, and the combined report must fit the 64 MiB cap.
Read `I8-LOT-STUDIES.md` before interpreting selected-return differences.
For a basket study, optional `--portfolio SETTINGS.json --portfolio-cycles N`
uses controller NAV
and continuous cycles within each fresh-funded window. Every candidate is valued
through the same endpoint, including after its final allowed exit. Unknown
training NAV withholds selection for the entire fold/scenario while preserving
all outcomes; unknown selected test NAV stays null. See `I8-PORTFOLIO-STUDIES.md`.
Treat regime/universe statements as unverified declarations;
static complete histories do not resolve survivorship/delisting bias. Retain the
plan and all outcomes, including unfavorable ones. There is no live policy export.

`benchmark` writes a unique small sample directory beside its result. Review
the original/derived byte-equivalence hash, reader timings, memory scope and
cache/device evidence together. Cached CPU/hash timings do not establish storage
throughput. Do not convert the archive because compressed Parquet is smaller.
Optional `--utc-start-ms START --utc-end-ms END` selects an exact half-open UTC
minute range within the selected source. Add
`--random-windows N --window-minutes M --query-seed S` for reproducible minute
windows; explicit duration and seed are required. Total queries are capped at
32, summed selected records at 1,048,576 per repeat, and `--repeats` at 1–10
(default three). Empty gaps and duplicate windows are retained. Read
`I8-STORAGE-QUERIES.md` for the shared index, preparation costs, rotating reader
order and sample measurements. This mode does not perform calendar-month or
multi-symbol sampling, cold-cache measurements or parameter replay reuse.
For a declared multi-source comparison, use `bench-storage --merge-plan PLAN.json`
with `--code-revision`, `--output` and optional `--repeats`. The strict
`grid-storage-merge-plan-v1` plan contains only `schema` and `sources`; each source
has the existing ten source fields. This mode excludes all single-source and UTC
query options. It permits at most 1,000 distinct markets/files, 131,072 records
per source and 1,048,576 in total. It closes each block reader before yielding,
checks per-source and chronological hashes, and reports preparation separately.
See `I8-STORAGE-MERGES.md` for the plan example, bounds and 100/1,000-source
measurements. Failed samples are retained; results never overwrite existing files.
Warm synthetic measurements do not certify cold-device or trading throughput.
The main entry point is `gridctl bench-storage`; `gridctl backtest` runs the same
replay engine as the module's `replay` command. Neither opens an operator socket
or takes financial ownership. The CLI refuses an already occupied result path
before replay/conversion. It also excludes the preserved public-hour evidence
tree. A race at final publication still refuses overwrite and may leave derived
samples for inspection.

Success messages omit user paths; argument and execution failures omit raw
exception/configuration values. Exit 2 does not prove a result is absent: stdout
can fail after successful publication. SIGINT returns 130 when caught. Inspect
the specified result and retained files before a new invocation; no automatic
retry is performed. Record bounds and sequential Parquet decoding do not provide
CPU/RSS/IO isolation. Do not run research beside a future trading owner until
the constrained contention test and deployment quotas have been validated.

`python -m grid.operations contention` now provides the finite synthetic
contention experiment documented in `I8-RESOURCE-CONTENTION.md`. It uses one
research child with CPU-time/address-space ceilings and logical archive-read
pacing. It pauses research while overloaded fill hints are reconciled and
checks every distinct fill after writer reopen. Exit 1 retains a completed
measurement report whose timing checks failed; exit 2 is a refused/failed run,
and caught interruption returns 130. Inspect reports before retrying. Kernel
RSS/device quotas and on-deployment-hardware acceptance remain unverified.

`python -m grid.exchange.order_audit --code-revision REV --output NEW_PATH`
audits 91 fixed pinned-SDK cases offline, intercepting the first request before
signing/transport. It accepts no credentials or arbitrary order parameters.
Exit 1 publishes failed fixture checks; current findings include float conversion,
client-ID changes/refusal and account-mode discovery. Exit 2 refuses an incomplete
audit and never publishes a partial success. Do not respond to these findings by
supplying keys, shortening real IDs or enabling a venue. Review
`I8-SDK-ORDER-AUDIT.md`; actual authenticated serialization and recovery remain
unverified. Existing public-run authorization is not reused by this command.

Add `--bingx-text` for the paired, fixed decimal-string preparation trial in
`I8-BINGX-TEXT-PREPARATION.md`. It retains unmodified controls and independently
checks candidate unsigned query text. Exit 0 covers candidate fixtures only;
all venues remain NOT_READY. The override is confined to disposable diagnostic
clients; do not patch global SDK classes, provide credentials or enable sending.

The optional mapped fake in `I8-DURABLE-CLIENT-IDS.md` exercises durable external
client IDs without a new CLI mode or exchange access. Schema 6 retains mappings
after order completion and includes them in backup verification. An older binary
cannot open v6: preserve the upgraded database and use a reviewed pre-upgrade
backup in a new path if rollback requires an old schema. Never drop mappings or
invent replacement IDs for attempted orders after restore.

For repeated mixed BUY/SELL flows, add `--mixed-rounds 3` to `operations contention` and
choose a new output filename. The optional mode accepts 1–8 rounds and uses
one funded allocation per synthetic venue, with a separate bounded research
child in each round. Partial sells, late buys during cancellation, pressure
pauses and final draining are measured against the same 10-second fill-lag
threshold. A 240-second wall timeout applies per 1/7-venue case. Inspect
`grid-mixed-contention-v1` inside the common report envelope; the original mode
is unchanged when the flag is absent. This is a finite isolated mock-executor
workload, without the portfolio supervisor or a sustained fixed-rate feed.
`I8-MIXED-CONTENTION.md` records the retained failed measurement, the subsequent
reconciliation optimization and financial compatibility checks.

If a run fails or is killed, leave originals intact. Temporary `.grid-research-*`
siblings and incomplete format sample directories are diagnostic artifacts, not
published results. Restart from the immutable input/config into a new output
name. Automatic deletion or overwrite is not part of recovery. No checkpointed
live execution or power-loss durability is claimed for research JSON.

See `I5-DESIGN.md` and `I5-EVIDENCE.md` for the precise model and measured scope.
`I8-RESEARCH-CLI.md` records the later main CLI integration. The current gaps are
tracked in `IMPLEMENTATION-REVIEW.md`; existing replay is not a test of the I7
portfolio exit controller.
`I8-RESEARCH-PROVENANCE.md` records the envelope and bounded local Git checks.
Changes in source, manifests, observed Git identity or runtime metadata between
the before/after snapshots refuse publication. Retain any derived samples and
inspect the checkout; do not erase evidence or retry automatically. Even stable
snapshots cannot attest to a hostile same-user process or modified loaded code.
I6 was subsequently approved with local validation. Hosted CI remains pending; do not
retry the known account lock or use paid GitHub features to unblock this work.

## I6 mock universe control

`grid.universe` supplies typed selection, bounded read-only discovery and a mock
controller API; no CLI trading service is available. Use explicitly funded
synthetic allocations, reviewed `Facts` and a supplied UTC/monotonic clock.
Attach one `UniverseController` to the sole `MockExecutor`; submit facts before
configuring the manual/automatic/hybrid policy. Each policy change needs a unique
request ID, expected revision, actor, reason and timestamp. To retry an uncertain
configuration result, use the same exact request rather than assume it failed.

On fee/rule/account changes, invalidate facts or publish a strictly newer reviewed
revision. `invalidate(scope)` returns a revision tombstone; the next observation
must exceed it. Missing/stale/evicted facts block new BUY claims. Status retains
the entire supervised roster, even for disabled venues and retired symbols.
Exclusion does not cancel an uncertain external order by assumption, remove an
allocation, release its funds or liquidate its position.

After restart retain every persisted scope, reconcile financial state and supply
fresh facts. Universe membership alone does not authorize a BUY. An externally
paused/retired/exit-controlled allocation is never automatically resumed. A
canceled zero-fill seed still requires the domain's explicit new-round decision.

The v3 migration adds universe audit records without changing v1/v2 definitions.
Backups from all three versions are accepted; old versions migrate on opening.
For rollback keep v3 history and use a verified separate pre-migration backup
with matching old code. Never drop the new table or downgrade in place.
See `I6-DESIGN.md` and `I6-EVIDENCE.md`. I7 was subsequently approved with local validation.

## I7 mock portfolio supervision

Use `PortfolioController` with the existing mock executor and single writer.
The complete pre-funded roster, explicit owned pools, reporting currency,
conversion sinks, fee/FX evidence, exit price floors/drift/outcome, TTL/order/time
budgets and dust ceilings are required inputs. No operational trading CLI exists.
The example YAML intentionally supplies no invented financial policy defaults.

Register empty paused conversion sinks before the first cycle. Reconcile every
scope and supply a fresh `ValuationFacts` packet before explicit `begin`. Supply
UTC/monotonic clocks and strictly increasing observation revisions; `invalidate`
removes admission until a newer packet arrives. The supervisor caches one bounded
packet; callers schedule `evaluate` and `advance` explicitly. Nothing here starts
a background service or fetches account data.

When +10% net NAV latches the exit, service cancellations and reconcile every
ordinary order before advancing exit SELL plans. Continue posting late fills.
An acknowledgement does not prove cancellation. Missing facts, liabilities,
unknown orders, stale context, exhausted budgets and unapproved residuals keep
the cycle blocked. Do not clear the latch, widen floors, top up funds or restart
to conceal a blocked exit. There is no automatic policy-widening or budget-reset API.

Record external cashflows as unclassified first. Resolve them only with explicit
evidence and the cycle's chosen convention. Under additive baseline accounting,
only reporting-currency changes in owned pools can be posted. Internal movement
to a same-account conversion sink creates auditable ownership lots, never fake
fills or exchange transfers. Missing conversion routes stay visible and blocked.

Inspect baseline, trigger NAV/trigger baseline, final NAV/return, shortfall,
residuals, slot reasons and actual ledger fills/fees separately. Approved dust
remains owned and marked, with zero executable final NAV. A +10% trigger can end
below +10%, including a loss. An explicit new cycle requires completion and new
fresh evidence; its resume scopes are chosen explicitly.

After process death preserve the full roster, reconcile ledger/orders and supply
fresh facts. Neither a persisted admission receipt nor a previous cached packet
authorizes a repeat send. v4 adds portfolio audit/flow tables; backup/restore
supports v1–v4 additively. Retain history and use separate verified compatible
backups for rollback. Never drop tables or downgrade a financial ledger in place.

See `I7-DESIGN.md` and `I7-EVIDENCE.md`. The owner subsequently approved
`I8-PLAN.md` for offline preparation with local validation. The required observed
48-hour soak remains a distinct unfulfilled acceptance gate. Hosted CI remains
pending, with GitHub Free only and no retry of the unchanged account lock.

## I8 offline operations and recovery

Use the finite commands in README in a reviewed patched mock environment.
Reports go outside Git; original datasets and legacy code remain untouched.
`dry-run` exercises the synthetic lifecycle and verified restore; `benchmark`
measures six bounded cases. Neither starts a real exchange feed or long-running
service. Return 0 means a synthetic report was published, not that I8 passed.
Return 2 means refusal/failure; no successful report is published. Use the typed
API and local tests for detailed diagnosis rather than printing exception or
transport contents in operational logs.

For an in-process supervisor, read `status()` before acting, retain the request
ID and revision, and choose the specific typed control from `I8-DESIGN.md`.
Pause new BUYs, cancel BUYs, stop a scope, retire it and globally halt have
different effects. A halt does not liquidate inventory or undo a claimed send.
Keep every owner in the roster and reconcile late/unknown orders and fills.

On queue overflow retain the rejected input and require authoritative recovery;
do not drop it and assume it was posted. On retry exhaustion review the failure
and use a new explicit `RECONCILE`; this alone does not resume placement. On
stale evidence obtain a genuinely newer observation instead of restamping old
facts. A confirmed full-roster `RESUME` requires fresh reconciled NAV evidence.
Replaying an old successful resume cannot clear a later local halt.

On SQLite/permission/integrity/backup failure, keep admission halted, preserve
the database/WAL/owner files and diagnose the disposable environment. Do not
delete audit rows, clear unknowns or claim that a failed backup is fresh. Verify
a backup into a different private path before recording its watermark. The
status fee sums cover retained fills only; reconcile lifetime fees from the
durable financial history when reviewing a full cycle or restoring a real ledger.

Close is idempotent and shields admitted writer work. If its timeout reports
pending cleanup, ownership is still held; await completion and check the final
snapshot. Never start a second controller by deleting the owner file. After a
crash, reopen with the complete roster, retain the startup halt, reconcile all
orders/fills/fees and only then review explicit resume. A previous successful
send receipt must never authorize another network send.

The v5 additive operational table is included in backup/restore validation.
Rollback means a scoped code revert plus a separately verified compatible
backup, preserving newer history; no in-place downgrade or table deletion.
Original v1–v4 backups remain migratable. Mock migration/crash tests are not a
certification of production power-loss or account reconciliation.

`deploy/grid-mock.service` has only been syntax-checked. Its review marker and
arguments intentionally prevent casual use. Its current interpreter path must
be prepared with an approved runtime in a later authorized deployment review.
Do not create the service account/marker, install/enable the template, call
`systemctl start`, or turn the synthetic runner into a live service under the
preparation approval. Parser verification does not prove runtime isolation.

Preparation evidence is in `I8-EVIDENCE.md`. The owner subsequently approved the
public-paper harness and a one-hour observed run, then continued development.
`I8-SOAK-PLAN.md` records the current execution bounds. No I8 acceptance pass or
I9 deployment follows from local tests or the shorter run.

## Public-paper evidence and interrupted runs

The approved quick run is now complete; `I8-PAPER-EVIDENCE.md` records the final
observed hour and retained partial attempt. The commands below describe the
implemented tools, not permission to repeat public access. Offline audits remain
available; additional public runs/private access require a new approved scope.

The fixed public runner is `python -m grid.paper`; its only venue/symbol is OKX
BTC/USDT. It requires an explicit private evidence directory, approved SQLite
source identity and `--acknowledge-public-one-hour`. Never point it at a live
ledger. All outputs, including fake funds, remain outside Git/original datasets.
The source checkout used by an active run must remain frozen.

At minute 30 the worker exits 75 for its one planned restart. The transient
`scripts/supervise_paper.py` coordinator records worker liveness every 30 seconds
and performs only that preauthorized restart. Other exit codes, missing processes
and timeouts do not authorize blind retries. The worker uses a Linux pidfd to
stop when its coordinator dies. No service is installed by this tool.

A stopped, incomplete journal is partial evidence. Confirm the worker is gone;
never infer termination solely from an observation timeout. Offline closure with
`python -m grid.paper.recovery` requires the same directory and approved source,
verifies the financial ledger and a separate backup restore, and writes a
`partial-interrupted` report. It cannot turn missing time into an observed hour.
The current recovery amendment allows exactly one linked replacement with the
same aggregate 750/3 request ceilings and slower polling; it does not permit an
unbounded rerun loop. Preserve both evidence directories and the claim marker.

After a run finishes, use `gridctl paper-audit --directory PATH`. It reads the
journal without changing it and refuses an active writer, incomplete tail,
broken hash chain, stale/duplicate books or a budget/timing violation. Audit
success proves the stated checks, not I8 acceptance, real fees or execution.
For an exclusive JSON artifact, use `python -m grid.paper.audit --directory PATH
--output NEW_PATH` on one line. Reports stay outside Git; retain evidence at
least seven days after review. Unknown public constraints correctly produce an
unallocated paper cash pool and zero paper orders.

New harness runs use monitoring version 2: a 50-second sampling target with a
60-second maximum measured gap, and an independent CPU window that additional
checkpoints cannot reset. A monitoring breach latches a partial result. Inspect
actual CPU window duration/completeness; a partial window is not a minute pass.
Legacy run evidence retains its original version and measured gaps. See
`I8-MONITORING-EVIDENCE.md` for the mock-only correction; it grants no new public
run authorization.

## Local mock operator procedure

`gridctl operator serve-mock` creates a finite invented-account owner, using the
approved patched mock SQLite runtime from the checks above. It accepts 1–300
seconds and a new empty private directory outside Git/original datasets. The
README contains a complete startup example. No service is installed and the
command never constructs an exchange transport. It starts halted and prints one
JSON readiness line containing its socket path and session ID.

In another terminal, inspect `gridctl operator status --socket PATH` and record
`data.revision`. The `venues`, `symbols`, `allocations` and `markets` commands
inspect the current fixture; they do not discover markets or configure funding.
Status `working` includes durable intents awaiting a synthetic send, not only
acknowledged exchange orders. Financial values are serialized decimal strings.

To prepare a resume, make an owner-only request directory and run the following
with the **actual** socket path and revision from status. The placeholders are
deliberate; do not guess a revision from an earlier session:

```sh
mkdir -m 700 /tmp/grid-operator-requests-new
uv run --locked gridctl operator resume --socket SOCKET_PATH --revision CURRENT_REVISION --all --reason 'reviewed invented fixture' --request /tmp/grid-operator-requests-new/resume.json
```

Exit 3 means only that a request has been saved for review. Inspect the printed
action, targets, session ID and revision; repeat that exact command with
`--confirm`. Supplying `--confirm` before a request exists is refused. `cancel-buys`
and `retire` follow the same procedure. Reasons are durable audit data and appear
in previews: never include secrets. Actor is the OS UID, not a supplied username.

Other controls use the same required revision, targets, reason and request path.
Use a new request path for each new action. `--allocation ID` can replace `--all`
and may repeat for distinct allocation IDs. Global halt needs the full roster.
`pause` stops new BUY admission; `cancel-buys` separately requests cancellations;
`stop` requests stop/reconciliation and leaves the supervisor running; `retire`
retains responsibility for the allocation. None is a liquidation or market sell.

On `conflict`, inspect current status and decide whether the intended action is
still appropriate before creating a new request. On timeout or lost response,
retain the existing request and inspect status. An explicit repeat with the same
request retrieves the committed receipt if it exists. Do not regenerate the ID,
change the saved revision/reason or infer that a timed-out operation was rolled
back. The CLI performs no automatic retries. Receipt revision may precede the
response's current revision; a replayed resume cannot clear a later halt.

Normal duration expiry or Ctrl-C closes the owner. An abrupt kill leaves the
journal, ledger and endpoint evidence. To test recovery, wait until that process
is gone and invoke `serve-mock` with the same directory and `--resume`. The source
digest, OS user and host boot must still match. Use its newly printed socket.
Restart replays durable synthetic effects and reconciles with admission halted;
a fresh reviewed resume requires the new operational revision and request ID.
Do not delete an owner lock or stale endpoint to bypass a refusal. Stop when the
bounded retained-endpoint capacity is reached and inspect the disposable session.

This session generates no fills and uses a synthetic monotonic clock. Missing
backup alerts remain until a fresh owner-mediated backup is verified using the
storage commands below; do not suppress them or treat the fixture as production-ready. It cannot provide
UTC audit or observed-soak evidence. `paper-audit` refuses its different journal
kind. Design, trust boundary, bounds and remaining commands are documented in
`I8-OPERATOR-DESIGN.md`. Rollback preserves all generated state and request files.

## Manual portfolio LIMIT exit in the mock owner

Inspect `gridctl operator status --socket SOCKET_PATH`. Review the complete
`data.cycle.policy`, full allocation roster and any held scopes/halt. Obtain
`data.revision`, `data.cycle.id` and `data.cycle_policy_sha256`. With a new request
file in an existing owner-only directory outside Git:

```sh
uv run --locked gridctl operator limit-liquidate --socket SOCKET_PATH --revision CURRENT_REVISION --cycle CYCLE_ID --policy-sha256 REVIEWED_POLICY_DIGEST --all --reason 'reviewed manual portfolio exit' --request /tmp/grid-operator-requests-new/exit.json
```

The placeholders require actual status values. Exit 3 saves the preview without
submitting it. Review its session, cycle, digest, targets and reason, then repeat
the exact command with `--confirm`. A first invocation already containing
`--confirm` is refused. Routine NAV sampling does not invalidate the approved
policy; a changed policy or cycle does. A stale operational revision is a conflict.

With `--all`, this command requests exit of the entire registered portfolio
cycle, including draining allocations and its explicit pools. Replacing `--all`
with one or more `--allocation ID` selectors requests only those allocations'
principal inventory exits. Explicit selectors retain this meaning even when
they happen to cover the full roster or the roster contains only one allocation.
Existing halts and held
scopes remain in effect; a separate reviewed resume may be required for exit
sends. Missing/stale valuations or unknown ownership can refuse the request:
inspect status and use the distinct pause/cancel/reconcile controls as needed.
An accepted receipt does not mean all orders have canceled or inventory has sold.

Inspect `exit_cause`, `target_reached_at_exit`, cycle phase, slots, residuals,
final return and exit shortfall. `operator` can validly have target flag false
and a negative realized return. Unreachable limits or exhausted budgets stay
blocked; do not switch to MARKET or edit the saved approval to loosen limits.
After a lost response, retain the original request and inspect status; an
explicit exact repeat retrieves the same receipt without starting a second exit.

The additive manual snapshot cannot be interpreted by older runtimes. Preserve
the ledger and use a compatible reader; never remove manual audit records to
make a downgrade appear valid. See `I8-MANUAL-EXIT.md` and `I8-ALLOCATION-EXIT.md`
for compatibility, rollback and validation evidence.

For scoped exits, inspect `data.cycle.allocation_exits`: each entry records its
approval, actor/reason, phase, timestamps and order slot. The global cycle stays
`active`; its global cause/target/final fields do not report scoped completion.
Exit proceeds and fee balances remain in their original allocation. Completion
requires no outstanding orders/reconciliation and zero base or approved dust. Other residuals
remain `exit_blocked` unless quantified unavoidable dust is explicitly approved
under the cycle policy; see `I8-SCOPED-DUST.md`. Nothing is written off.
After completion, a separately prepared and confirmed `resume` may
release the selected latch. Resume of an unresolved exit only clears operational
holds; it cannot reactivate the grid or reset the exit's deadline/order budget.

Global NAV evaluation continues independently. A later +10% trigger or confirmed
`--all` request takes over the scoped exit, preserving its anchor, used order
count and earlier absolute deadline. Its still-valid SELL can remain working
while other allocations cancel ordinary orders; no replacement is placed before
the global reconciliation barrier clears. After takeover, the global `slots`
are authoritative and `allocation_exits` retain the handoff provenance. Unreachable
prices, stale data, uncertain orders and exhausted budgets remain visible blocks.

## Local universe configuration

Read `gridctl operator status --socket SOCKET_PATH` and
`gridctl operator universe --socket SOCKET_PATH`. Review both the operational
revision and `universe.revision`, policy, membership and existing inventory.
Every edit requires both revisions, reason, a new private request file and a
second confirmation. For example:

```sh
uv run --locked gridctl operator deny --socket SOCKET_PATH --revision OPERATIONS_REVISION --universe-revision UNIVERSE_REVISION --allocation ALLOCATION_ID --retirement manage-existing --reason 'reviewed exclusion' --request /tmp/grid-operator-requests-new/deny.json
```

Exit 3 is the saved preview. Review its exact targets and retirement choice, then
repeat with `--confirm`. `disable-venue` uses repeated `--venue ID` instead of
allocation selectors. `enable-venue`, `set-symbol-mode`, `allow`, `pin`, `unpin`,
`set-capacity` and `refresh-universe` use the same confirmation procedure; see
`gridctl operator COMMAND --help` and `I8-HOT-CONFIG.md` for their exact arguments.

Choose retirement explicitly for deny/disable. `manage-existing` cancels BUYs
while retaining take-profit management. `cancel-buys-and-hold` cancels outstanding
BUY **and SELL** orders, retains late-fill supervision and blocks new placements.
`bounded-limit-exit` additionally requires the reviewed `--cycle` and
`--policy-sha256`; if new-exit preflight fails, the entire edit is refused. It does
not silently record a partial exclusion or relax the exit bounds. Use a distinct
reviewed manage/hold request if restriction is needed while ownership is uncertain.

Allow removes explicit denial; pin never does. Confirmed readmission may reset
an empty reconciled round owned by the universe, but it cannot clear a separate
operational halt or override a scoped/global exit. A global exit can still sell
owned inventory on disabled/held venues. Inspect command receipts, both revisions,
allocation modes and orders after every edit. A successful edit is not proof that
cancel requests have become terminal or inventory has sold.

Retain a request after a lost response. Replaying it returns its historical receipt
without clearing a new startup halt. A known revision conflict requires a fresh
status review and a new request file; never alter an old request to bypass CAS.
These commands configure the existing mock roster and do not discover accounts,
allocate funds, change strategy/fee rules or enable live exchange access.

## Scoped residual review

Inspect `status` → `cycle.allocation_exits` for each phase, reason, `residual`
and `released` flag. A dust record must match the owned quantity in allocations;
its value is a cap measurement, not cash proceeds. The immutable cycle policy
needs explicit amount/value allowances and a shared dust ceiling. The fixture's
default zero allowance intentionally blocks nonzero residual completion.

A normal confirmed `resume` can release approved dust to ordinary supervision
only after fresh checks. Its receipt records the retained-dust proof. Inventory,
lots and cost remain intact, and no extra seed is bought to manufacture sellability.
A `released: true` record continues tracking the shared allowance; it does not
authorize liquidation orders. Freshness failures and cap increases can block dust
certification again. Inspect both operational halts and each allocation's mode;
completion or resume does not prove a new order is eligible. A whole-cycle exit
reassesses every owned asset, including these records, and counts inventory once.

## Local backup and restore verification

Read `operator status` for the current operational revision. Use new paths in an
existing owner-only directory outside Git and the preserved evidence/data trees:

```sh
mkdir -m 700 /tmp/grid-storage-drill-new
uv run --locked gridctl operator backup --socket SOCKET_PATH --revision CURRENT_REVISION --reason 'reviewed mock backup drill' --request /tmp/grid-storage-drill-new/backup.json --output /tmp/grid-storage-drill-new/backup.db --verify-to /tmp/grid-storage-drill-new/verified.db
```

Exit 3 is the saved preview. Review both paths and the complete-ledger scope, then
repeat exactly with `--confirm`. `--output` is a new backup file, and `--verify-to`
is another new file for a checked restored copy. The owner chooses its active
source database; the client never opens it. No command replaces an existing file.
Retain the response: `data.job.result.notices` holds the semantic hash followed
by the request count as a string. This is not the raw-file SHA-256.

For a separate check of that retained backup, inspect the new operational revision
and prepare another request and destination:

```sh
uv run --locked gridctl operator restore-verify --socket SOCKET_PATH --revision CURRENT_REVISION --reason 'reviewed separate restore drill' --request /tmp/grid-storage-drill-new/restore.json --source /tmp/grid-storage-drill-new/backup.db --destination /tmp/grid-storage-drill-new/another-restore.db --digest RETAINED_SEMANTIC_HASH --requests RETAINED_REQUEST_COUNT
```

Review and repeat with `--confirm`. This checks a backup and creates a verified
copy; it does not activate recovery, resume trading or refresh the owner's backup
stamp. Actual restored ownership still requires normal startup checks and
reconciliation. Later fills may be absent from the snapshot.

After timeout, lost acknowledgement or restart, retain the request and inspect
its `request_id` using the current socket:

```sh
uv run --locked gridctl operator storage-status --socket SOCKET_PATH --request-id SAVED_REQUEST_ID
```

`verified` is a retained historical result; inspect files or run a new separate
verification if fresh artifact evidence is needed. `incomplete` means no terminal
result: the job may still be running or may have stopped. `failed` retains the
fixed failure result and leaves admission halted. Exact request replay never
copies again and cannot clear a later halt. Ensure the prior job has stopped,
review status/artifacts, then use new paths and a new reviewed request if needed.
Never delete evidence, edit retained requests or replace an active ledger to
force a retry. Details and crash evidence are in `I8-STORAGE-CONTROLS.md`.
