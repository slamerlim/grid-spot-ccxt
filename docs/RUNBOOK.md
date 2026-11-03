# I0 developer runbook

I0 provides an offline configuration/diagnostic CLI and reproducible development
checks. It has no daemon, strategy, exchange client, database or live path. This
is not a production operating runbook. No exchange credentials are needed.

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
distinguished from observed venue behavior. All Q01–Q20 financial/execution gates
remain future work; see the trace and capability registry.

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
The owner's existing I1 approval is still conditional on a successful I0 CI
run; a skipped/private job or local test run does not satisfy that condition.

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

The owner approved `I1-PLAN.md` and its partial-seed anchor decision on
2026-11-03, conditional on I0 hosted CI passing. Verify and record that passing
workflow run and commit before implementing I1; do not ask for the same approval
again. Future operational commands, systemd hardening, backup/restore
drills, alerts and live arming belong to their later accepted iterations; none
can be exercised through this I0 CLI.
