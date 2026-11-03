# GRID Spot CCXT

**I0 bootstrap only. LIVE: DISABLED. No trading engine or exchange order path exists.**

This repository establishes the specification, reproducible Python environment,
strict safe configuration, offline diagnostics and CI for a spot grid bot. It is
not ready for live or sandbox trading. The owner approved I1 conditional on
successful I0 hosted CI; see [the I1 plan](docs/I1-PLAN.md). I1 implementation
remains pending that condition.

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

## Specifications and evidence

- [PRD v2](docs/PRD_GRID_SPOT_CCXT_FINAL_v2.md),
  [storage ADR](docs/ADR-0001-NVMe-Storage-and-Caching.md), and
  [acceptance gates](docs/ITERATIONS_AND_ACCEPTANCE_FINAL.md) are imported unchanged.
- [Requirements trace](docs/I0-REQUIREMENTS-TRACE.md) maps future functionality to tests.
- [Exchange capability plan](docs/EXCHANGE_CAPABILITIES.md) distinguishes untested
  behavior from installed library metadata.
- [Legacy review](docs/LEGACY_REVIEW.md) records the prior review's provenance and
  limitations; no legacy executable or dataset is imported into Git.
- [Runbook](docs/RUNBOOK.md) covers safe operation and rollback of I0.
- [I0 evidence](docs/I0-EVIDENCE.md) records executed checks and remaining gates.

The linked SQLite runtime must be checked, not inferred from Python's version.
The official [SQLite WAL guidance](https://www.sqlite.org/wal.html#walreset)
identifies fixed releases; a matching version is only one prerequisite, not
proof of safe storage, approved build provenance or live readiness. A ledger,
backup/restore, controller lock and recovery tests belong to later iterations.

Do not put credentials, account state, local configuration, research data or
generated reports in Git. This repository does not request exchange credentials
for I0 and contains no service deployment or trading command.
