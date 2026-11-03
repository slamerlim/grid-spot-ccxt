# I0 implementation and verification evidence

This is an offline repository/bootstrap increment, not a trading implementation
or live readiness approval. The attached master prompt limits this run to I0
and an I1 plan. The full trading goal remains pending its later gates.

## Scope and preserved decisions

Implemented strict configuration, `gridctl doctor`, typed packaging, locked
dependencies, deterministic offline tests, pinned CI actions, security checks
and requirements/probe/runbook documentation. All seven venue entries are
disabled; capital, currency, grid, exit and resource choices remain unset.
No strategies, network clients, order gateway, ledger, migration, service or
research importer were implemented. `/opt/module_1_multi` and `/opt/datasets`
were not modified.

The four attachments are imported byte-for-byte, with SHA-256 fingerprints in
`SPECIFICATION_SOURCES.json`. `LEGACY_REVIEW.md` is explicitly a summary of the
prior chat audit, not a fabricated original attachment or new archive audit.
No architecture decision was changed. Minimum-lot wording and the sellable
partial-seed lifecycle are documented in the trace/I1 plan, not implemented.

## Environment and reproducibility

- Repository directory: `/opt/grid`; branch: `i0/specification-bootstrap`.
- Remote: `https://github.com/slamerlim/grid-spot-ccxt.git`.
- Initial local state: no checkout; an isolated new project was initialized.
- Remote verification: initial unauthenticated Git attempt failed; a later
  GitHub API GET returned HTTP 200, public repository, size 0, default branch
  `main`. A subsequent `git ls-remote` succeeded with no refs. That later result
  supersedes the draft-time 404 and early authentication failure for reading.
- Base revision: none, because the verified remote has no commits/refs.
- uv 0.13.7 was obtained from the official PyPI wheel and verified against its
  published SHA-256 before execution. One `uv.lock` pins dependency artifacts.
- Main interpreter: CPython 3.12.3, linked SQLite 3.45.1.
- Additional test interpreter: CPython 3.13.18, linked SQLite 3.53.1, in an
  isolated `/tmp/grid-i0-py313` environment. The system interpreter was not replaced.
- Official CCXT 4.5.91 and PyYAML 6.0.3; no separate paid WebSocket dependency.
- Commands below used `/tmp/grid-tooling/uv` in this workspace. README uses `uv`
  on PATH. Builds and temporary audit exports are ignored or outside the repo.

## Checks actually executed

| Command or check | Observed result |
| --- | --- |
| `uv lock` then `uv sync --locked` | PASS; reproducible pinned project/development environment installed |
| Fresh `uv sync --locked` with `UV_PYTHON=3.13`, separate environment | PASS; same lock installed on second interpreter |
| `uv run --locked ruff check .` | PASS on both Python environments |
| `uv run --locked ruff format --check .` | PASS on both Python environments |
| `uv run --locked mypy` | PASS, 8 application/test source files, both environments |
| `uv run --locked pytest` | PASS, 104 tests on Python 3.12.3 and 104 on Python 3.13.18, no skips |
| `uv run --locked bandit -q -r src` | PASS after explicit safe-loader handling |
| Locked all-groups export + `pip-audit --strict --disable-pip --no-deps` | PASS; no known vulnerabilities reported by the advisory service; not a guarantee of absence |
| `detect-secrets-hook --no-verify --cores 2 --baseline .secrets.baseline` | PASS after review of documented false positives; no credential verification calls |
| `uv build --no-sources` | PASS; source distribution and wheel built |
| Package archive inspection | PASS; wheel contains only five application modules and distribution metadata; no state, credentials, data or legacy binaries |
| Install built wheel without dependencies into the separate test environment; run from `/tmp` | PASS; installed CLI runs independently of source checkout |
| `gridctl doctor --json --check` from installed wheel | Expected exit 1; LIVE DISABLED, ready false, missing prerequisites explicit |
| Independent adversarial CLI review | PASS; eight text/JSON runs for malformed explicit YAML tags, timestamps, huge integers and deep nesting return 2 without input/path/traceback leaks |
| Specification attachment byte/hash comparison | PASS; all four unchanged |
| Git ignore behavior | PASS; sensitive/state/research paths excluded while sample, docs and lock remain includable |

Pytest disables sockets globally. The 104 tests comprise 65 strict configuration
tests, 36 doctor/CLI tests and three supplementary scope/provenance/ignore
guards. They use temporary files and mocked runtime metadata/environment; they
do not claim to simulate trading. An AST scope guard is supplementary evidence,
not a complete proof of absence of arbitrary side effects.

Initial failures were investigated rather than hidden: independent review found
YAML constructor exceptions that could expose input; explicit tags and malformed
inputs now fail with generic errors. Bandit initially could not recognize the
custom SafeLoader passed through `yaml.load`; the loader is now explicitly
constructed/disposed. Secret scanning initially flagged four document SHA-256
fingerprints and two synthetic error-redaction strings. Only those four
fingerprints are in the reviewed baseline; the fake test strings have inline
allowlist comments. No scanner plugin is disabled and no real secret is exempted.

An initial ad-hoc archive inspection accidentally included `dist/.gitignore`,
and a wheel smoke command used a relative path from `/tmp`; both commands were
corrected and rerun successfully. Neither was a package failure.

## Documentation and exchange claims checked

The official [CCXT Pro manual](https://github.com/ccxt/ccxt/wiki/ccxt.pro.manual)
confirms that streaming is bundled free with CCXT. Offline imports of `ccxt`
and `ccxt.pro` succeeded; all seven named classes exist in each installed module.
No exchange objects were instantiated and **no exchange/network method was
probed**. Account options, permissions, markets, fees, private streams, client-ID
semantics, pagination and outgoing order payloads remain NOT_TESTED/UNKNOWN.

The official [SQLite WAL documentation](https://www.sqlite.org/wal.html#walreset)
confirms the WAL-reset fix in 3.51.3 and the specified backports. The host's
3.45.1 fails the version policy. The additional interpreter's 3.53.1 passes the
numeric threshold only; build approval, local filesystem, WAL/FULL, locking,
backup/restore and crash tests are still unverified. Doctor says so explicitly.

Network use in I0 was limited to public documentation, package distribution,
advisory services and repository access checks. There were no exchange requests,
exchange credentials or live funds. Financial invariants Q01–Q20 remain NOT RUN:
there is no financial state, simulated fill engine or order path yet.

## Publication, rollback and next gate

### Initial publication attempt (historical)

The following records the initial HTTPS attempt before SSH access was
configured. The successful publication update below supersedes its access
status; the original failure remains part of the evidence.

Implementation commit: `51f1fad9f2a5210fdeaf5f58623759e68198ff8d`
(`chore(i0): bootstrap the specified offline CCXT grid project`). A subsequent
documentation-only commit records the publication result below; `git log -2`
identifies both revisions. Commits use the explicit local agent identity
`Codex <codex@localhost>`, not an assumed personal GitHub author identity.

Actual attempted publication command:

```sh
GIT_TERMINAL_PROMPT=0 git push --set-upstream origin i0/specification-bootstrap
```

Result: exit **128**, `fatal: could not read Username for 'https://github.com':
terminal prompts disabled`. Subsequent public GitHub API reads returned an
empty branch list and **zero Actions runs**. No remote branch, commit, PR,
deployment or hosted CI pass is claimed. Install/connect GitHub with repository
write authority or configure a credential helper through a trusted interactive
mechanism; do not paste tokens into chat, files or shell history. After access is
confirmed, push this existing branch, inspect the actual CI result and fix any
failures. The empty repository has no base branch for a PR yet; establish its
default-branch/review flow without inventing existing history or force pushing.

The local implementation is reviewable; authenticated push, PR creation and an
observed hosted CI run remain separate evidence. GitHub is publicly readable,
but this workspace has no configured Git credential helper/token/SSH agent and
the GitHub connection has not been confirmed. A public GET does not prove write
authority. Do not call hosted CI passed based on the local commands above.

The local commit history and final delivery report identify the exact reviewed
revision and any actual push result. No remote deployment is performed.

Rollback is a reviewed `git revert` of the scoped I0 commit(s), followed by
`uv sync --locked`; no database migration or order cancellation is involved.
Preserve existing legacy/data files and unrelated work.

### SSH access and publication verified

On 2026-11-03, the owner authorized a repository-specific SSH deploy key with
write access for agents in this workspace. The private key is stored outside
Git with mode `0600`; repository-local configuration selects it and pins
GitHub's officially published Ed25519 host key. `AGENTS.md` and the runbook
describe the same-host, same-OS-account scope and ordinary publishing commands.
The default author for subsequent commits is
`GRID Automation <grid-automation@localhost>`.

Observed results:

- SSH authenticated as `slamerlim/grid-spot-ccxt`.
- `git push --dry-run origin HEAD` succeeded.
- `git push --set-upstream origin HEAD` created
  `refs/heads/i0/specification-bootstrap` and configured its upstream.
- `git ls-remote` confirmed that the remote branch matched local commit
  `45671b2d9bafedd593fcc33a9e3f0fb03288354f`, including the original I0 commits.
- GitHub reported `i0/specification-bootstrap` as the repository's default
  branch after this first push. No force push or history rewrite was used.
- Local checks were repeated: lockfile consistency, Ruff lint/format, mypy,
  **104 tests on Python 3.12.3**, Bandit, tracked-file secret scan, dependency
  audit and package build all passed. This repeat did not rerun Python 3.13;
  the earlier Python 3.13 evidence above remains historical.

The resulting [hosted I0 workflow](https://github.com/slamerlim/grid-spot-ccxt/actions/runs/41713360115)
reported failure for both matrix jobs before any steps ran. Both job annotations
state: **"The job was not started because your account is locked due to a
billing issue."** This is a GitHub account blocker, not an executed test failure.
No hosted test pass is claimed. This annotation does not show that the workflow
uses a paid feature or that payment is required. Hosted CI needs restored free
account access; no paid workaround is authorized. The runbook records the
free-support route and forbids repeated attempts against an unchanged lock.

### GitHub Free constraint

On 2026-11-03 the owner explicitly required GitHub Free only and prohibited
paid GitHub features. Repository metadata confirmed public visibility; the
workflow uses standard `ubuntu-latest` runners, which
[GitHub documents as free for public repositories](https://docs.github.com/en/actions/concepts/billing-and-usage).
A job-level visibility condition now skips execution for private repositories.
No paid plan, larger runner, payment setup, metered overage, remote cache or
artifact upload is introduced. This is a configuration review, not an account
billing audit or a hosted test pass. The reported billing lock can affect free
access as well; earlier instructions to resolve it must not be interpreted as
instructions to pay or upgrade.

Recommendation: **GitHub publication is verified and local I0 checks pass;
HOLD the full I0 release gate until hosted CI executes successfully.** On
2026-11-03 the owner explicitly approved `I1-PLAN.md`, including its sellable
partial-seed anchoring rule, initially conditional on I0 hosted CI passing.
The owner subsequently selected **"Proceed with local validation for I1"**,
explicitly replacing that start prerequisite. This leaves the I0 hosted gate
pending and authorizes only I1 pure strategy/mock tests. See `I1-EVIDENCE.md`
for the implementation and local results. Live and sandbox trading remain
outside this run under every configuration. Earlier statements in this evidence
file describe the historical I0 snapshot.
