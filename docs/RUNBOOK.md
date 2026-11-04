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

I3 rollback is a scoped code/docs/lock revert with `uv sync --locked`; no deployed
database or external order requires rollback. Preserve the GitHub Free preference
and approval history. I4–I6 were subsequently approved; `I7-PLAN.md` is the next gate.

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
I6 universe control were subsequently approved with local validation. Stop after
I6 review: I7 requires separate authorization. Operational commands, systemd hardening, deployment
storage drills, alerts and live arming belong to later accepted iterations;
none can be exercised through this CLI.

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

Use `python -m grid.research --help` and the bounded examples in README. The
research module has no exchange transport and never creates a financial database.
Keep source archives immutable and place new outputs outside Git. Do not run the
legacy converter/downloader or rewrite original files to make validation pass.
Supply the clean Git commit as `--code-revision`; uncommitted experiments need an
explicit source fingerprint and must not be labeled as a clean commit.

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

`benchmark` writes a unique small sample directory beside its result. Review
the original/derived byte-equivalence hash, reader timings, memory scope and
cache/device evidence together. Cached CPU/hash timings do not establish storage
throughput. Do not convert the archive because compressed Parquet is smaller.

If a run fails or is killed, leave originals intact. Temporary `.grid-research-*`
siblings and incomplete format sample directories are diagnostic artifacts, not
published results. Restart from the immutable input/config into a new output
name. Automatic deletion or overwrite is not part of recovery. No checkpointed
live execution or power-loss durability is claimed for research JSON.

See `I5-DESIGN.md` and `I5-EVIDENCE.md` for the precise model and measured scope.
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
See `I6-DESIGN.md` and `I6-EVIDENCE.md`. I7 requires approval of `I7-PLAN.md`.
