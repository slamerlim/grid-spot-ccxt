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

## Rollback and next gate

I0 has no migrations, financial database, service or deployed orders. To undo an
I0 change, review and revert its scoped Git commit on a branch, then restore the
matching locked environment using `uv sync --locked --dev`. Preserve unrelated
work and the immutable legacy/data paths. No database restore, asset liquidation,
remote reset or secret rotation is part of I0 rollback.

The next artifact is the I1 review plan. The master prompt explicitly requires
approval before implementing I1. Resolve its partial-seed anchor decision during
that review. Future operational commands, systemd hardening, backup/restore
drills, alerts and live arming belong to their later accepted iterations; none
can be exercised through this I0 CLI.
