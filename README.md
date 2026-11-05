# GRID Spot CCXT

**I8 harness and one-hour public observation completed; full acceptance pending. LIVE: DISABLED.**

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
An optional [mapped synthetic adapter](docs/I8-DURABLE-CLIENT-IDS.md) now commits
separate external client IDs before an attempt and tests lookup/recovery while
retaining the original internal IDs. Its text-ID policy is mock evidence only.
Its [unified order/trade integration](docs/I8-UNIFIED-RECONCILIATION.md) reuses
shared parsers for CCXT observations before ownership validation and mock posting.
[Terminal-order contradictions](docs/I8-TERMINAL-RECOVERY.md) in current recovery
snapshots block new posting and replacement SELLs, including after compaction.
The public adapter uses [CCXT's documented string-number mode](docs/I8-CCXT-UNIFIED-NUMBERS.md)
and converts unified financial values to Decimal at the application boundary.
The owner approved I8 public-paper work with local validation while hosted CI remains pending.
Offline research now validates bounded immutable OHLC windows, replays the Decimal
strategy under explicit synthetic fill assumptions, and compares raw/Arrow/Parquet
samples. See [the approved plan](docs/I5-PLAN.md), [design](docs/I5-DESIGN.md) and
[evidence](docs/I5-EVIDENCE.md). I5 performs no exchange calls.

The mock universe controller now selects pre-funded allocations under explicit
manual, automatic or hybrid policies. Deny and eligibility checks override pins.
Policy revisions retire excluded allocations atomically while preserving their
orders, funds and inventory; current evidence is checked again at the BUY send
claim. Bounded single-flight discovery reuses the read-only client pool.
I6 uses synthetic observations only and adds no trading command or exchange calls.
See [I6 design](docs/I6-DESIGN.md) for integration, ranking, limits and restart rules.

The portfolio supervisor values every owned allocation and explicit pool using
fresh conservative prices and fees. Aggregate net NAV at +10% atomically freezes
new BUYs and starts bounded LIMIT-only liquidation after cancellation and
reconciliation. Trigger NAV, final return, exit shortfall and approved residuals
are distinct. Cashflows require classification; conversion allocations preserve
asset ownership without fabricating fills. See [I7 design](docs/I7-DESIGN.md) and
[local evidence](docs/I7-EVIDENCE.md). These APIs use synthetic facts and the mock
executor. The mock operational supervisor now schedules reconciliation and exits,
persists audited controls, exposes bounded local status/alerts and halts on
storage/overload faults. Restart requires explicit reviewed resume. See
[I8 design](docs/I8-DESIGN.md) and [preparation evidence](docs/I8-EVIDENCE.md).
The minimum 48-hour observed soak and first-venue account evidence remain pending.
The owner approved a one-hour public-data check followed by mock development.
The [paper harness](docs/I8-PAPER-DESIGN.md) persists the shared request budget,
uses the existing financial writer and refuses paper admission when public market
constraints are incomplete. The shorter run cannot close the 48-hour gate.
The [completed observation](docs/I8-PAPER-EVIDENCE.md) collected 460 validated
books with no feed failures and verified restart/backup recovery. Missing public
constraints prevented all paper orders; this was not a trading-performance test.
Further public runs are not covered by that completed authorization.

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

## Offline historical research

Keep generated data/configurations/reports outside Git. For a deliberately
**synthetic** demonstration (the constants are not production defaults):

```sh
uv run --locked python -m grid.research demo --output-directory /tmp/grid-research-demo --code-revision "$(git rev-parse HEAD)"
uv run --locked gridctl backtest --config /tmp/grid-research-demo/replay.json --output /tmp/grid-research-demo/result.json
uv run --locked gridctl bench-storage /tmp/grid-research-demo/synthetic.bin --venue synthetic --symbol SYNTH/USD --count 7 --code-revision "$(git rev-parse HEAD)" --output /tmp/grid-research-demo/storage.json
```

Use a new output directory/result name on each run; publication refuses overwrite.
To inspect an original window without approving its provenance:

```sh
uv run --locked python -m grid.research validate /opt/datasets/crypto_1m/bingx_USDT_BTC.bin --venue bingx --symbol BTC/USDT --count 32768 --format-evidence /opt/datasets.md --code-revision "$(git rev-parse HEAD)" --output /tmp/grid-history-validation.json
```

Validation exit 0 means eligible under supplied provenance, 1 means the report
contains blocking findings/unknowns, and 2 means the operation was refused or
failed. A filename is only a mapping hint. Do not assert identity/collection
evidence to bypass missing provenance. Replay refuses rejected records, gaps,
unknown provenance, unlike quote currencies and incomplete UTC coverage.

`gridctl bench-storage` accepts the same source arguments and writes a small raw/Arrow IPC/
Parquet comparison and derived samples beside the report, outside Git. It is a
storage measurement, not approval to trade that source. PyArrow 26.0.1 is pinned
in the optional `research` group, included by `dev` for tests; it is not a runtime
trading dependency. A minimal installation can use `uv sync --locked --no-dev`;
add `--group research` only for format benchmarks. See the design for strict
configuration bundles, limits, uncertainty and reporting conventions.
The existing `python -m grid.research replay` and `benchmark` entry points remain
available. Known occupied/protected result paths are refused before processing;
an interrupted run may retain samples or an already published report. Inspect
artifacts before another attempt. Parquet decoding is sequential, but these
commands do not impose OS CPU/RSS/IO quotas or coordinate with a trading owner.
See [CLI behavior and validation](docs/I8-RESEARCH-CLI.md).
Optional `--utc-start-ms/--utc-end-ms` and
`--random-windows N --window-minutes M --query-seed S` add bounded UTC query
comparisons using the same timestamp index across raw mmap, Arrow and Parquet.
Every window must reproduce raw bytes, including empty gap results. Timings
include lookup/open/read/decode/hash/close, with index preparation reported
separately; caches are warmed and uncontrolled. See
[query bounds and observed evidence](docs/I8-STORAGE-QUERIES.md).
`--merge-plan PLAN.json` instead compares a declared roster of up to 1,000
distinct source files chronologically across raw mmap, Arrow and Parquet. It
retains gaps and deterministic ties, closes readers between bounded blocks and
requires identical per-source/merged hashes. It cannot combine with single-source
arguments. The measured 100/1,000-source synthetic cases favored raw mmap;
see [merge plans, limits and evidence](docs/I8-STORAGE-MERGES.md).
Research CLI reports now use a `grid-research-run-v1` envelope: read the existing
engine output under `result`. `provenance` records observed source hashes, local
Git comparison, runtime and host details. Its separate report hash changes with
observations; the inner deterministic replay result/hash stays unchanged. A
supplied revision is verified only when it exactly matches the local HEAD and
the compared source/manifests match that commit. See
[research provenance and compatibility](docs/I8-RESEARCH-PROVENANCE.md).

Optional `backtest --portfolio /path/to/reviewed-portfolio.json` uses the same
ledger, mock executor and portfolio controller to model the consolidated +10%
trigger and bounded LIMIT exit. It requires explicit synthetic liquidity/FX,
exit policy and approved SQLite source identity, and creates a temporary mock
ledger. Its distinct result schema records blocked exits and final shortfalls;
it never forces completion at EOF. See [portfolio replay](docs/I8-PORTFOLIO-RESEARCH.md)
for inputs, chronology, limits and validation.

Add `--portfolio-cycles N` (1–32) for an explicit bounded continuation after
reconciled exits. Funds and accounting carry forward; the series report separates
each cycle's return from the return on the original funds. It also works with
`--compare-scenarios`. See [continuous portfolio replay](docs/I8-PORTFOLIO-SERIES.md)
for restart rules, schemas and shared limits. The default remains one cycle.

Add `--compare-scenarios` to either backtest mode to run the three fixed OHLC
conventions sequentially. The result retains all scenarios and shows a shared
NAV range only when their valuations and endpoints are comparable. Scenario
labels do not rank profits or certify execution. See
[scenario comparison conventions](docs/I8-RESEARCH-SCENARIOS.md).
Reports expose gross sums of net realized wins/losses. Portfolio replay also
includes committed-fill statistics and funded cash/buy-hold references through
the same evaluation endpoint and NAV constraints. Unknown benchmark valuations
remain null. See [performance definitions](docs/I8-RESEARCH-PERFORMANCE.md).
Portfolio reports also show allocation inventory losses, exposure and observed
drawdown, with internal conversion transfers separated from earnings and unknown
marks retained as null. See [allocation risk definitions](docs/I8-ALLOCATION-RISK.md).

`backtest --compare-lots` compares the ordinary basket against a fixed-anchor
independent-lot research reference using the bundle's OHLC model and shared
funding limits. It excludes `--portfolio` and `--compare-scenarios`; production
keeps its single aggregate SELL. See [reference conventions and limits](docs/I8-INDEPENDENT-LOT-REFERENCE.md).

`backtest --study PLAN.json` evaluates declared chronological training/test folds,
spacing candidates and separate fee/fill/latency scenarios. It selects spacing
from training NAV only and retains every held-out result with fresh funds per
window. Add `--portfolio` and optionally `--portfolio-cycles N` for consolidated
cycles within each window, using a common endpoint and withholding selection
when training NAV is unknown. Add `--compare-lots` without portfolio settings
to compare basket and independent-lot studies, each selecting spacing from its
own training NAV and retaining every test result. Work limits count both policies.
`--compare-scenarios` remains excluded because the plan declares scenarios. See
[basket study plans](docs/I8-RESEARCH-STUDIES.md),
[portfolio study rules](docs/I8-PORTFOLIO-STUDIES.md) and
[paired lot-study evidence](docs/I8-LOT-STUDIES.md).

## Offline operational experiments

The pinned SDK request audit uses invented markets/accounts and intercepts
requests before signing or networking:

```sh
uv run --locked python -m grid.exchange.order_audit --code-revision "$(git rev-parse HEAD)" --output /tmp/grid-sdk-audit-new/report.json
```

Exit 1 retains failed fixture checks, including numeric conversion and client-ID
findings; it does not submit an order. See [SDK audit evidence and limits](docs/I8-SDK-ORDER-AUDIT.md).

Add `--bingx-text` to run a fixed paired BingX decimal-string preparation trial.
It retains unmodified controls and checks candidate unsigned query text without
signing or sending. Exit 0 covers candidate fixtures only; all venues remain
NOT_READY. See [preparation scope and evidence](docs/I8-BINGX-TEXT-PREPARATION.md).

The finite synthetic runner uses temporary mock databases and makes no exchange
calls. Select the reviewed patched runtime described below; the system Python
and an old `.venv` may correctly refuse ledger creation. With that environment:

```sh
uv run --locked python -m grid.operations dry-run --code-revision "$(git rev-parse HEAD)" --sqlite-source-id '2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9' --output /tmp/grid-operations-new/dry-run.json
uv run --locked python -m grid.operations benchmark --code-revision "$(git rev-parse HEAD)" --sqlite-source-id '2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9' --output /tmp/grid-operations-new/benchmark.json
uv run --locked python -m grid.operations contention --code-revision "$(git rev-parse HEAD)" --sqlite-source-id '2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9' --output /tmp/grid-operations-new/contention.json
```

The contention command reconciles distinct synthetic partial fills while a
separate bounded research process runs, pauses under pressure and resumes.
It reports measurements and failures without claiming deployed RSS/device quotas.
See [contention limits, lifecycle and evidence](docs/I8-RESOURCE-CONTENTION.md).
Add `--mixed-rounds 3` to run three BUY/SELL rounds on each of 1/7 synthetic
venues, including lower buys, partial sells, cancellation races and late buys.
This optional mode accepts 1–8 rounds and retains measured timing failures.
See [mixed workload and reconciliation evidence](docs/I8-MIXED-CONTENTION.md).

The source identity is approved for the existing **mock test builds only**; do
not substitute an unfamiliar runtime's identity without provenance review.
Use new result filenames on each run. Reports distinguish observed wall time,
simulated time, evaluated candidates and funded allocations, and always leave
I8 acceptance incomplete. Fees/fills/financial settings are invented fixtures.

`deploy/grid-mock.service` is an uninstalled, inert review template. Existing
typed controls are also exposed by the finite local mock operator described below.
The [observed-run plan](docs/I8-SOAK-PLAN.md) records the approved one-hour
public-data scope. The original 48-hour acceptance run and private-account
access require separate authorization.

## Local mock operator

`gridctl operator` adds status and audited controls for a running synthetic owner.
It uses a private Linux Unix socket, keeps the existing single financial writer,
and makes no exchange calls. Start a finite session in a new directory using the
approved mock runtime above:

```sh
uv run --locked gridctl operator serve-mock --directory /tmp/grid-operator-session-new --seconds 120 --sqlite-source-id '2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9'
```

In another terminal, use the `socket` path printed in the ready JSON:

```sh
uv run --locked gridctl operator status --socket /path/from/ready.json
uv run --locked gridctl operator --help
```

The owner starts halted and contains one invented 1,000-QUOTE allocation with no
automatic fills. Views include `allocations`, `venues`, `symbols`, `markets` and `universe`.
Controls include `pause`, `cancel-buys`, `stop`, `reconcile`, `retire`, `resume`
and `halt`; all require an observed revision, targets, reason and private saved
request. Sensitive actions require a second invocation with `--confirm` after
review. See [the command procedure](docs/RUNBOOK.md#local-mock-operator-procedure),
[design](docs/I8-OPERATOR-DESIGN.md) and [evidence](docs/I8-OPERATOR-EVIDENCE.md).
The [manual portfolio LIMIT exit](docs/I8-MANUAL-EXIT.md) is available as
`limit-liquidate --all`, with explicit cycle/policy approval. Use repeated
`--allocation ID` selectors for [scoped exits](docs/I8-ALLOCATION-EXIT.md) while
the portfolio remains active. Residual inventory stays supervised;
only a confirmed resume can reopen a completed allocation. [Approved scoped dust](docs/I8-SCOPED-DUST.md)
requires fresh revalidation and retains its inventory, cost and shared allowance
after resume. It never becomes fictitious sale proceeds or permission for a larger buy.
The [hot configuration commands](docs/I8-HOT-CONFIG.md) expose venue enable/disable,
selection mode, allow/deny/pin/unpin, capacity and selection refresh. Each requires
operational and universe revisions plus a second confirmation. Deny/disable must
specify how existing inventory should be managed. The [storage controls](docs/I8-STORAGE-CONTROLS.md)
provide owner-mediated `backup`, separate `restore-verify`, and durable
`storage-status` receipts. Copies always use new private files; interrupted jobs
remain visible for review. Historical research uses the separate commands above;
study coverage and deployment resource isolation remain incomplete.
This fixture does not establish trading or I8 acceptance.

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
fingerprints. Two synthetic redaction-test strings and three golden SHA-256
checksums of invented cycle fixtures have explicit local allowlist comments.
No real credential is allowlisted; new findings fail CI.
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
- [Current requirements review](docs/IMPLEMENTATION-REVIEW.md) maps local evidence
  to Q01–Q20 and records unfinished research, resource and external acceptance work.
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
- [I5 approved plan](docs/I5-PLAN.md), [research design](docs/I5-DESIGN.md) and
  [local evidence](docs/I5-EVIDENCE.md) cover data validation, replay and sample storage.
- [I6 approved plan](docs/I6-PLAN.md), [universe design](docs/I6-DESIGN.md) and
  [local evidence](docs/I6-EVIDENCE.md) cover selection, hot changes and cache bounds.
- [I7 approved plan](docs/I7-PLAN.md), [portfolio design](docs/I7-DESIGN.md) and
  [local evidence](docs/I7-EVIDENCE.md) cover valuation, cycle exits and recovery.
- [I8 approved preparation plan](docs/I8-PLAN.md), [design](docs/I8-DESIGN.md) and
  [evidence](docs/I8-EVIDENCE.md) cover mock operations, storage drills and bounded
  local experiments. [The amended observed-run plan](docs/I8-SOAK-PLAN.md),
  [paper design](docs/I8-PAPER-DESIGN.md) and [paper evidence](docs/I8-PAPER-EVIDENCE.md)
  cover the approved one-hour public observation and continued mock development.

The linked SQLite runtime must be checked, not inferred from Python's version.
The official [SQLite WAL guidance](https://www.sqlite.org/wal.html#walreset)
identifies fixed releases; a matching version is only one prerequisite, not
proof of safe storage, approved production build provenance or live readiness.
Mock ledger/restore tests do not certify a deployment filesystem or exchange
reconciliation. Observed soak/venue access and I9 live work need separate approval.

Do not put credentials, account state, local configuration, research data or
generated reports in Git. This repository does not request exchange credentials
and contains no installed service or live trading command.
