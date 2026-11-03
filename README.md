# GRID Spot CCXT

**I1 pure strategy implemented. LIVE: DISABLED. No exchange order path exists.**

This repository establishes the specification, reproducible Python environment,
strict safe configuration, offline diagnostics and a deterministic Decimal
strategy core for a spot grid bot. I1 returns proposed limit/cancel/reconcile
commands from synthetic metadata and normalized events. It has no executor or
financial database and is not ready for live or sandbox trading. The owner
explicitly authorized local validation for I1 while hosted CI is unavailable;
see [the approved plan](docs/I1-PLAN.md) and [I1 evidence](docs/I1-EVIDENCE.md).

The intended system is one Python process with official free CCXT (including its
bundled `ccxt.pro`), one SQLite financial writer, Decimal accounting, geometric
minimum-lot buys, one aggregate sell per allocation, and a consolidated +10% NAV
exit trigger using limit orders only. A trigger does not guarantee an exit price
or realized profit. Seven target venues are OKX, HTX, Gate, XT, BingX, KuCoin and
Bitfinex; none is certified by this bootstrap.

## Install and inspect offline

Install Python 3.12 or 3.13 and **uv 0.13.7** from its
[official distribution](https://docs.astral.sh/uv/getting-started/installation/).
From the project directory:

```sh
uv sync --locked
uv run --locked gridctl doctor --config config.example.yaml
uv run --locked gridctl doctor --config config.example.yaml --json
uv run --locked gridctl doctor --config config.example.yaml --check
```

Dependency installation uses PyPI; doctor and tests make no exchange/network
calls. Doctor creates no database, reads no credentials and cannot enable live
trading. `--check` deliberately exits 1 while live prerequisites are blocked;
normal diagnostics exit 0, and malformed configuration exits 2. Financial values
in configuration are quoted decimal strings or `null`. The example contains no
invented budget, trading spacing, account allocation or exit policy.

## Verify

```sh
uv lock --check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
uv run --locked bandit -q -r src
git ls-files -z | xargs -0 uv run --locked detect-secrets-hook --no-verify --cores 2 --baseline .secrets.baseline
uv export --locked --all-groups --no-emit-project --no-hashes --output-file /tmp/grid-audit-requirements.txt
uv run --locked pip-audit --strict --disable-pip --no-deps -r /tmp/grid-audit-requirements.txt
uv build --no-sources
```

The vulnerability audit calls advisory services; unavailable advisory results
are not a pass. The test suite disables sockets and uses temporary paths and
mock runtime metadata. It does not claim Q01–Q20 financial/exchange acceptance.
The secret-scanner baseline permits only the four reviewed specification SHA-256
fingerprints. Two synthetic redaction-test strings have explicit inline
allowlist comments. No real credential is allowlisted; new findings fail CI.
CI runs the same checks on Python 3.12 and 3.13 with read-only repository
permissions and no live secrets. Do not equate local checks with an observed
GitHub Actions run; actual evidence is recorded per iteration.

**GitHub Free only:** no paid GitHub features, trials, runners or usage overages
are authorized. This public repository uses standard Ubuntu runners, which
[GitHub documents as free](https://docs.github.com/en/actions/concepts/billing-and-usage).
The workflow skips private repositories and uploads no caches or artifacts.
If free hosted checks are unavailable, use the local commands above and report
hosted CI as unavailable; do not request payment or claim local tests are hosted
CI. See the [runbook](docs/RUNBOOK.md) for the account-lock guidance.

## Specifications and evidence

- [PRD v2](docs/PRD_GRID_SPOT_CCXT_FINAL_v2.md),
  [storage ADR](docs/ADR-0001-NVMe-Storage-and-Caching.md), and
  [acceptance gates](docs/ITERATIONS_AND_ACCEPTANCE_FINAL.md) are imported unchanged.
- [I0 requirements trace](docs/I0-REQUIREMENTS-TRACE.md) records the original mapping;
  [I1 evidence](docs/I1-EVIDENCE.md) maps the implemented pure-domain checks.
- [Exchange capability plan](docs/EXCHANGE_CAPABILITIES.md) distinguishes untested
  behavior from installed library metadata.
- [Legacy review](docs/LEGACY_REVIEW.md) records the prior review's provenance and
  limitations; no legacy executable or dataset is imported into Git.
- [Runbook](docs/RUNBOOK.md) covers offline checks and rollback of I0/I1.
- [I0 evidence](docs/I0-EVIDENCE.md) records executed checks and remaining gates.
- [I1 design](docs/I1-DESIGN.md) defines math, fee reserves, event contracts and bounds.
- [I2 review plan](docs/I2-PLAN.md) proposes the single-owner ledger and mock
  crash/restore tests; I2 implementation awaits separate approval.

The linked SQLite runtime must be checked, not inferred from Python's version.
The official [SQLite WAL guidance](https://www.sqlite.org/wal.html#walreset)
identifies fixed releases; a matching version is only one prerequisite, not
proof of safe storage, approved build provenance or live readiness. A ledger,
backup/restore, controller lock and recovery tests belong to later iterations.

Do not put credentials, account state, local configuration, research data or
generated reports in Git. This repository does not request exchange credentials
for I0 and contains no service deployment or trading command.
