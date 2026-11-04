# I8 offline follow-up — portfolio controller in historical replay

Base: `84caf18916079be654417d81993d3af3ee5165c2`, branch
`i8/public-paper-hour`. This increment continues the authorized offline/mock
development. It uses invented markets and temporary mock financial databases.
It performs no exchange requests, account access, orders, service installation
or deployment. The completed public hour is not rerun. GitHub remains Free-only;
hosted CI remains pending under the unchanged account lock.

## Behavior and compatibility

The optional portfolio replay feeds historical bars into the existing
`AsyncWriter`/`Ledger`, `MockExecutor`, `SyntheticExchange` and
`PortfolioController`. It does not duplicate their financial reducer, reservation,
ownership, fee, cancel/reconciliation or exit rules. All declared pools, including
unallocated cash, remain in the consolidated cycle. New BUY claims use the
controller's existing net NAV admission check and +10% latch. Ordinary orders
must resolve before the bounded portfolio LIMIT exit can proceed.

```sh
uv run --locked gridctl backtest --config /tmp/study/replay.json --portfolio /tmp/study/portfolio.json --output /tmp/study/new-result.json
```

`python -m grid.research replay` accepts the same option. Without `--portfolio`,
the original pure-domain replay and its engine schema remain unchanged. Both
modes use the existing `grid-research-run-v1` provenance envelope and exclusive
outside-Git publication. Portfolio mode's inner result has the distinct schema
`grid-portfolio-research-v1`. The two modes' hashes and metrics are not
interchangeable: original mark-to-close accounting does not include the
controller's conservative exit valuation, funding admission or exit coordination.

Portfolio mode creates a new private temporary directory and one mock SQLite
writer per invocation. The supplied source ID must match an explicitly approved,
patched linked SQLite runtime; it is not automatically accepted from discovery.
The directory is removed on normal return and handled error. A killed process
can leave a scratch directory; inspect it before removal. This is a restart from
immutable inputs, not resumable financial recovery or a production ledger.
No existing operator ledger is opened or attached.
The inherited temporary root is checked against the same protected Git/data/
public-evidence paths as report outputs before a scratch directory is created;
an unsafe `TMPDIR` is refused rather than used for financial scratch files.

## Explicit inputs and modeled chronology

The normal `grid-replay-config-v1` bundle supplies capital, allocation contexts,
fee constraints, source provenance, UTC range, spread, latency and OHLC scenario.
The additional `grid-portfolio-replay-v1` JSON is produced by
`grid.research.portfolio_config.bundle(Settings(...))` and the existing artifact
publisher. Its strict reader rejects unknown/missing/duplicate fields, floats
where integers/Decimals are required, and files over 1 MiB. Review these fields:

| Field | Meaning |
|---|---|
| `policy` | Existing codec-encoded `PortfolioPolicy`: reporting currency, every owned pool and allocation's exit rule, optional conversion sinks, explicit dust limits, freshness/evaluation/exit deadlines |
| `liquidity` | Exactly one row per scope: codec-encoded scope, quoted Decimal `nav_depth` and `fill_per_bar`; both positive synthetic assumptions |
| `conversions` | Codec-encoded tuple of explicit `Conversion` contracts for non-reporting assets; no assumed stablecoin parity |
| `actions_per_bar` | 1–64 attempted dispatches per allocation per minute |
| `venue_capacity` | 1–5,000 retained synthetic orders, trades and cancellation requests each |
| `max_database_bytes` | 1–512 MiB cap checked against temporary files after each simulated bar |
| `sqlite_source_id` | Reviewed exact linked SQLite source ID, checked by the existing storage guard |

Use `grid.persistence.codec.dumps` for the typed policy/scope/conversion values;
do not substitute plain object JSON for those fields. `Settings.checked(replay)`
validates and canonicalizes ownership before execution. It requires the policy
to own every declared capital pool, one allocation per account/symbol, the same
reporting quote across allocations, and `no_transfers`. Ordinary allocations
must have positive funding and pass the ledger's executable seed reservation.
Conversion sinks must start empty and remain explicitly supervised. The existing
mock executor's XT/Bitfinex compound-rule refusal remains in effect. No
configuration is venue certification or an approval of a trading budget.

Each source must have exact, matching UTC-minute coverage and validated provenance.
Bars are merged by UTC and canonical scope; a complete portfolio minute is
assembled before valuation. Previously submitted orders are tested against the
bar using the existing strict or touch/buy-priority scenario. A shared per-market
capacity limits the sum of fills in that minute, including partial fills. The
remaining synthetic order quantity also bounds each fill when account-level
backoff leaves the ledger projection temporarily behind the venue projection.

New orders become fill-eligible only after the configured number of later bars.
Cancel finality has the same delay; it settles at a bar boundary before that
bar's fills. Orders can fill while a cancellation is still pending. After fills,
fresh synthetic close bid/ask and explicitly assumed depth/FX/rules/fees are
supplied, actual trades are reconciled through the ledger, the controller
evaluates/advances, and bounded LIMIT intents are dispatched. A newly created
opposite-side order cannot fill in that same bar. Uncertain cancellations retain
the executor's account backoff and block replacement. Unknown NAV pauses new
exposure; unexpected submission, reconciliation or storage errors abort the run.

These are declared simulation conventions, not observed order-book depth,
volume, queue position, account fee evidence or intra-minute ordering. Static FX
and fee/rule assumptions are refreshed on the synthetic clock; no historical
conversion series is invented. Fee settlement counts/quantization use the
supplied contracts. Exceeding a fee-reservation bound refuses success even if
the synthetic fee rate happens to be zero. The scenarios are not guaranteed PnL
bounds. No future bar selects a favorable execution path.

## Results and resource limits

The controller supplies baseline, trigger, exit start, slots, residuals, final
valuation and blocked reasons. The report also serializes final return and exit
shortfall, last known NAV, unknown-NAV bar count, observed drawdown and its
completeness flag, average committed-capital fraction, inventory-time fraction,
turnover, actual modeled fees, final allocation and unallocated pool balances,
orders, fills, cancellations and interval summaries. Event triples contain
zero-based bar index, kind and synthetic payload; their chain has a separate
hash. The complete deterministic result has its own checksum, separate from the
CLI's observed source/runtime/host envelope.

Drawdown uses known NAV before and after each bar's controller/dispatch actions
in that order, including the trigger observation when subsequent cancellation
uncertainty prevents a post-action valuation. Intervals retain both snapshots.
`nav_unknown_bars` counts unknown post-action valuations; any such bar keeps
`drawdown_complete=false`. Known points do not fill gaps or claim intra-bar
price-path coverage. The 1,120-to-1,050 fixture records an observed 6.25% decline
while retaining its unknown bars.

There is one consolidated cycle. Completion ends simulated activity and flushes
the final interval; subsequent input bars are still decoded/precision-checked
and exhausted to verify source identity/checksums. `simulated_bars`,
`consumed_source_minutes` and `simulation_end_ms_exclusive` distinguish these
intervals. Metrics cover the simulated portion. At EOF an active or blocked
cycle stays unfinished: no forced liquidation, invented terminal fills or
automatic next cycle. `final_return` and `exit_shortfall` are null until actual
modeled completion. A +10% trigger is never a guaranteed final return.

The existing input bounds remain 64 allocations, 192 capital pools, 1,000,000
minutes and 10,000 intervals. The synthetic gateway and report are additionally
bounded (32 MiB serialized result); the mock executor has finite account request,
pagination and backoff budgets. Source streaming does not load the full archive.
SQLite uses the existing bounded writer queue/WAL configuration. Its byte check
is sampled after a bar and may overshoot by that bar's work; serialization limits
are checked after encoding. These are not hard OS disk/RAM/CPU/IO quotas. Large
studies can hit bounds and fail instead of producing a successful report. No
claim is made that maximum-sized inputs fit the cap or complete within a given
time. The full contention/resource benchmark is still outstanding.

## Validation and remaining work

The 38 new cases cover a 1,000 baseline reaching 1,120, then finishing at 1,050;
late BUY ownership under the global barrier; partial seed/exit fills; shared
account backoff with a stale partial-fill projection; losing positions and idle
cash in NAV; missing assumed depth; floor/deadline-blocked exit; base/quote/token
fees and exceeded settlement bounds; capacity and SQLite approval refusal;
canonical scope ordering and low ambient Decimal precision; strict bundles;
the actual CLI process/envelope; unchanged source bytes/mtime; ledger failures
before/after commit with temporary-owner cleanup; and changed input after cycle
completion still preventing a successful report. Existing domain, portfolio,
execution and persistence tests remain required.
Three additional path cases refuse protected temporary roots before creating
anything, including when a caller's inherited temporary-directory setting is unsafe.

Final local validation used the unchanged application fingerprint below. The
final test matrix was repeated after an interrupted observation lost its process
handles; no pytest processes remained, and no unobserved run was counted as a
pass. Durable logs and result receipts are outside Git in
`/tmp/grid-i8-portfolio-final-checks/`:

- Python 3.12: `3.12-1793822344811203213.log` and matching `.json` receipt.
- Python 3.13: `3.13-1793822344805990817.log` and matching `.json` receipt.

| Final check | Result |
|---|---|
| Full pytest, Python 3.12.16 | **911 passed**, 380.70 s; no failures/skips |
| Full pytest, Python 3.13.18 | **911 passed**, 404.70 s; no failures/skips |
| Ruff lint/format | Passed; 210 formatted Python files |
| Strict mypy | Passed; 159 source/test files |
| Bandit (`src`, `scripts`) | Passed; no new suppression |
| Secret scan with staged additions | Passed; baseline unchanged |
| Lock/dependency advisory audit | Unchanged 66-package graph; no known vulnerabilities found |
| Imported specifications and attachments | Original manifest hashes unchanged |
| Hosted CI / exchange execution | Not run; known account lock unchanged, offline-only scope |

Application-source SHA-256:
`ca93731d872d856d78170ddeea58176ebe15a0830f40994caa71ce809db479d7`.
Both final test receipts verify that source fingerprint before and after their
child test process. The child command was `python -m pytest -q`, launched through
the locked `uv` environments `/tmp/grid-i2-py312` and `/tmp/grid-i2-py313` with
the respective explicit `UV_PYTHON` version. Reproduction uses the commands in
README with those interpreter/environment selections; other runtimes must pass
the existing SQLite approval gate rather than skipping integration tests.

Wheel/sdist destination: `/tmp/grid-i8-portfolio-dist/`, outside Git. Packaging
checks compare all 73 application modules plus source-archive documentation and
tests with the working tree, and exclude generated databases/history/journals.
Delivery is a scoped commit on the existing development branch; no PR is created.

Hosted CI is not retried. This increment does not complete the third OHLC scenario,
independent-lot comparison, walk-forward/regime/sensitivity/survivorship studies,
full metrics/baselines for this new mode, constrained contention or full storage
benchmark matrix. Nor does it close I8's observed 48-hour/first-venue gates or I9.

Rollback can revert the optional adapter without a schema migration. Preserve
reports with their explicit schema and assumptions; do not rewrite historical
evidence or downgrade an active operator ledger. Existing source-digest restart
compatibility rules still apply.
