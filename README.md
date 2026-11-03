# GRID Spot CCXT

**I4 mock execution and recovery implemented. LIVE: DISABLED.**

This repository establishes the specification, reproducible Python environment,
strict safe configuration, offline diagnostics and a deterministic Decimal
strategy core for a spot grid bot. The single-owner SQLite ledger assigns
dedicated funds, posts normalized fills and fees, and persists reservations and
command uncertainty atomically. Tests use synthetic funds and temporary databases.
The read-only adapter now supports explicitly invoked, bounded public discovery
through CCXT. A public pass observed BTC/USDT metadata on all seven venues and
validated books on six; XT's book was rejected. A mock-only executor now consumes
durable intents, captures LIMIT payloads and reconciles synthetic order/trade
snapshots through the ledger. It has no authenticated or sandbox/live transport.
The owner approved I4 with local validation while hosted CI remains pending;
see [the approved plan](docs/I4-PLAN.md), [design](docs/I4-DESIGN.md) and
[evidence](docs/I4-EVIDENCE.md). No additional exchange probes were performed in I4.

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

The I2 integration tests require an approved patched linked SQLite build.
The isolated test environments used Python **3.12.16** and **3.13.18**, both
linking SQLite **3.53.1** with the source ID pinned in the synthetic fixture.
An older system Python can run doctor but must refuse ledger creation. Set both
`UV_PYTHON` and `UV_PROJECT_ENVIRONMENT` explicitly when testing another interpreter;
see the [runbook](docs/RUNBOOK.md). An unfamiliar build fails the fixture gate;
inspect its provenance before updating that test approval. Do not skip positive
integration tests or weaken the runtime guard.

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
- [Runbook](docs/RUNBOOK.md) covers offline checks, explicit public probes and rollback.
- [I0 evidence](docs/I0-EVIDENCE.md) records executed checks and remaining gates.
- [I1 design](docs/I1-DESIGN.md) defines math, fee reserves, event contracts and bounds.
- [I2 approved plan](docs/I2-PLAN.md), [ledger design](docs/I2-DESIGN.md) and
  [local evidence](docs/I2-EVIDENCE.md) cover transactions, crash tests and restore.
- [I3 approved plan](docs/I3-PLAN.md), [adapter design](docs/I3-DESIGN.md) and
  [local evidence](docs/I3-EVIDENCE.md) distinguish public, mocked and private coverage.
- [I4 approved plan](docs/I4-PLAN.md), [execution design](docs/I4-DESIGN.md) and
  [local evidence](docs/I4-EVIDENCE.md) cover mock sends, scoped identities and recovery.
- [I5 proposal](docs/I5-PLAN.md) is the next review gate; it is not authorization.

The linked SQLite runtime must be checked, not inferred from Python's version.
The official [SQLite WAL guidance](https://www.sqlite.org/wal.html#walreset)
identifies fixed releases; a matching version is only one prerequisite, not
proof of safe storage, approved production build provenance or live readiness.
Mock ledger/restore tests do not certify a deployment filesystem or exchange
reconciliation. I5 requires a separate approval.

Do not put credentials, account state, local configuration, research data or
generated reports in Git. This repository does not request exchange credentials
and contains no service deployment or trading command.
