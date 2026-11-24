# GRID results viewer

The old HTTP backtest interface has been removed. Its replacement is a compact,
Streamlit table for the completed selected configuration sweep. Nginx
is stopped, disabled and masked; Streamlit serves the same path directly on
`127.0.0.1:80`. Public HTTP access is not used.

## Connect

Run on the client computer:

```sh
ssh -p 15407 -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8888:127.0.0.1:80 root@103.1.225.114
```

Keep that terminal open and visit
**http://127.0.0.1:8888/module1/backtesting/**.
The SSH service and its port are unchanged.

## Table

- **Результат** selects the whole portfolio or one of five exchanges.
- **Период** selects overall history or one of 53 calendar months. Monthly PnL
  and fees belong to that month; monthly Sharpe/Sortino are cumulative from
  history start through that month. Monthly views do not substitute whole-run
  drawdown or holding duration for unavailable monthly measurements.
- **Поля для фильтрации** accepts every available column, including columns
  hidden from the table. Multiple conditions are combined with AND. Numbers
  support exact equality, inequality, inclusive bounds, ranges and missing
  values. Text supports literal case-insensitive contains, equality and missing
  values. A missing exchange/global TP means disabled. Missing PF/risk values
  retain their saved reason in the corresponding status column.
- **Сортировать по** and **По убыванию** apply exact server-side sorting; missing
  values remain last. The main table pages through 100 rows at a time.
- **Столбцы таблицы** controls the main table's profitability/loss fields and
  configuration parameters. Spent, trade counts and Sharpe/Sortino remain on
  configuration detail pages. Display numbers are rounded;
  numeric filters and sidebar sorting use exact fractions, never binary floats.
  Dates and date-filter inputs use Europe/Moscow unless an explicit timezone
  is entered. A range includes both endpoints.
- Filters, sort order and visible-column choices are retained when switching
  between totals and months wherever the selected fields remain available.
- **CSV с точными значениями** downloads the filtered table with exact stored
  numbers (ratios where needed), independently of visual rounding.
- **PnL / пик spent, %** is visible by default and uses the selected period's
  net PnL and peak spent. **Макс. просадка / пик spent, %** divides the stored
  lifetime maximum monetary drawdown by lifetime peak spent. This is an
  unfunded research model with no initial-capital NAV denominator. A zero
  denominator gives a missing percentage. Monthly maximum drawdowns were not
  stored, so they are not inferred from monthly endpoints.
- The main table uses the native Streamlit dataframe again: header sorting,
  resizing, scrolling and search remain available. A single click highlights a
  cell; **double-click anywhere in a data row** opens that configuration's
  separate page. It contains all six
  portfolio/venue totals, a full monthly table, exact CSV downloads, and six
  charts with UTC months on the X axis: cumulative/monthly net PnL, spent and
  monthly peak spent, monthly PnL percentage, cumulative Sharpe/Sortino and
  monthly profit factor. The detail scope can be changed independently. Row
  selection uses the underlying configuration ID after filtering/sorting;
  links also open configurations directly using `?config=CONFIG_ID`.
- Whole-history portfolio net PnL determines the two best/worst cards at the top,
  independently of the table's filters. Exact comparisons and canonical ordinal
  resolve ties; both cards link to the configuration page.
- Separate top cards rank configurations by exact **median monthly PnL %** over
  complete UTC months. Only configurations with a defined result for every
  complete month enter this comparison; the number compared and months used are
  displayed. The whole-history PnL cards remain separate.
- Monthly PnL percentage and PF medians use complete UTC months only. PnL uses
  that month's peak spent; a zero denominator is omitted. PF uses available net
  closure ratios only, including a valid zero, and shows included/missing counts.
  Partial months remain in financial totals. An even median averages its two
  central exact values. Medians cover the full history of the selected scope.
- The closed-position PF excludes open inventory. An undefined PF with status
  `no_losses` means no losing *closed* positions, not no losses in the portfolio.
  The separate positive/negative full-month ratio sums changes in marked net
  PnL, including open-position valuation. Its two month counts are shown beside
  it. The page also identifies the exact published study SHA-256.

The current service publication contains 59,367 configurations, 109 selected
pairs on BingX, Gate, HTX, KuCoin and OKX, and a single captured set of
public CCXT rules applied to each pair's full history. It is an assumption-based
simulation, not certified historical performance. Original seven-venue files
remain preserved separately; the viewer reads one publication at a time and
binds its cache and median derivative to that publication's hashes.

The current study applies a **60-consecutive-minute flat OHLC filter**:
an exchange+pair is excluded if 60 successive one-minute records have the same
`O=H=L=C` price, including runs spanning read blocks or ending at EOF. The
complete local audit selected **109 of 881 parent pairs**, **70,628,589 records** and
five exchanges (BingX 15, Gate 5, HTX 11, KuCoin 26, OKX 52). The new matrix
uses the same dated public CCXT rule snapshot with seven BUY depths from 30%
to 60%. The full run and independent audit completed for all 59,367 rows.
Validation and short calibration passed. The owner waived the load test and
weekly rehearsal. [Active publication](/opt/grid-runs/results-viewer-ssh/depth-matrix-20261122-nc93lrj3/final-publication.json).
The previous 4,480-configuration publication remains available separately.

## Detailed replay

**Рассчитать подробности и графики** opens a confirmation with the fixed
configuration, TP settings, roster and original full horizon. Only **Да** submits
a job. **Нет**, Escape and dismissal do nothing. Before calculation, monthly
drawdown is **Ещё не рассчитано**. No full-matrix run can be started here.

The separate `grid-results-detail-worker` service processes one configuration on
all 109 selected sources. It retains the shared research heavy lock, full SHA
audits, exact arithmetic and trade event order. One calculation thread owns the
configuration; bounded streaming input and parallel source auditing are reused.
Browser/SSH closure does not cancel it. The page's one-second progress fragment
shows audit/replay/verification/publication, records, history time, speed, elapsed
time, replay ETA, memory/disk and the latest checkpoint. Stop requests go to the
owned C++ worker; saving has no fixed 60-second kill deadline. Resume is explicit
and requires compatible code/binary/source identities. Service restart marks
unfinished work interrupted and never automatically resumes it.

Monthly drawdown is the maximum shortfall from the **lifetime** net PnL peak
attained during that month, including the opening shortfall. The historical
peak is carried between months: previous peak 100, monthly path 80→70→90 gives
30 USDT, not 10. The percentage divides by the month's peak spent. A zero
exposure denominator retains money drawdown and an explicit missing-percentage
reason. Complete-month median percentages show included/missing month counts.

The `matrix-detail` worker action is a derivative of a completed publication,
not an exception to strict `_study` admission. It separately binds parent result,
rules/source manifest, original and replay code/binary hashes and risk definition.
All old financial fields, months, trade hashes and canonical pair fingerprint
must match exactly. Monthly maximum drawdowns must reproduce lifetime maximum
drawdown; the sampled chart extrema must agree too. Only a verified receipt
publishes the derivative. Original results/checkpoints are never rewritten.

Financial details, all pair summaries and chart frames use `details.bin`
(positional MessagePack, exact integer ratios, zlib-3, length/SHA frames).
`details.sqlite` is an offset/checksum index only. Its commits follow binary
fsync and record the committed boundary. C++ also writes framed binary pair and
chart scratch, not financial JSON. Small JSON requests/status/receipts remain;
existing checkpoint recovery retains its separate exact format. Publication
failure never makes incomplete frames visible as completed results.

The configuration page adds monthly drawdowns and their median, all six scope
summaries, all pair totals and detailed PnL/spent/current-drawdown trajectories.
Charts retain up to 2,000 bins, with ordered first/last/extreme points. Risk is
computed over all O/H/L/C observations and post-aggregate-exit observations before
chart reduction. No full trade journal is retained. Reopening uses the verified
binary cache; the main table overlays drawdowns only for completed detail jobs.

The derivative root is `/opt/grid-runs/results-viewer-details/`, with a 64 GiB
combined artifact limit, 64 GiB worker RSS ceiling and 32 GiB free RAM/disk floors.
The UI remains limited to 2 GiB. Its only write access is the detail request/cache
directory. The background worker has no exchange transport and writes only its
artifact directory and the existing heavy lock.

## Sharpe and Sortino

The completed engine uses changes in net marked PnL in USDT over complete UTC
calendar months, including open-position valuation and actual simulated fees.
Sharpe divides their mean by their sample standard deviation (N−1). Sortino
divides the same mean by the square root of the sum of squared negative monthly
PnLs divided by N, counting positive and zero months in N. Both multiply by
sqrt(12), with zero risk-free rate and target. These are PnL-based ratios; no
fixed initial balance or varying monthly capital denominator is introduced.

Monthly ratios are cumulative through the row's month. Partial months remain
in financial totals and charts but not risk observations. Fewer than two
complete months, zero variance or no negative observations retain the saved
missing-value reason. Exact monetary moments and squared ratios were already
stored; the completed engine's displayed square roots have 24 decimal places.
The viewer does not recalculate or replace them. Its cumulative PnL uses exact
sums of the stored month changes and is checked against the saved final PnL.

Only a selected detail replay can be started/stopped/resumed. There are no matrix
launch, exchange-access or original financial-result write actions. All 4,480
configurations remain visible without a default profit sort.

USDT amounts come from execution prices times minimum valid pair quantities.
No initial cash constraint is imposed: spent measures open acquisition cost,
not a deposit. One coin bought at 100 and sold at 110 yields 9.58 USDT after
0.20+0.22 fees. Peak spent excludes fees and is not an exact required starting
wallet balance. Reported percentages use the stated spent denominator.

## Data and performance

The source files are `full/results.bin` (2,358,618,279 bytes) and its
14,532,608-byte SQLite index under
`/opt/grid-runs/depth-matrix-20261122-nc93lrj3/`. Opening the archive checks the publication status,
complete index, selected IDs, full file hashes and committed binary boundary.
Every read frame is length/checksum checked. SQLite is opened with `mode=ro`
and `query_only`; the result writer is never instantiated.

One immutable archive description and at most six table views are cached in
memory. A projection skips unselected monthly records inside the MessagePack
reader, retaining exact numbers only for the selected scope/period. It does not
materialize the full monthly matrix in RAM or create a second result database.
Details read just the selected configuration's verified binary frame; up to
eight configurations are cached. No strategy replay is needed for monthly
PnL/PF/risk tables/charts. Additional intramonth drawdowns/trajectories require
the explicitly confirmed detail replay. Missing chart values remain gaps, not zeros.
An independent binary median index covers 356,202 configuration and scope summaries
in 17,291,437 bytes. It includes the exact full-month marked-PnL ratio and is
bound to the current publication hashes. Loading it avoids recomputing monthly
summaries on page visits; a cached table avoids another archive scan.

The completed computation's historical identities remain unchanged. Viewer
installation updates the project's single `uv.lock` for its separate viewer
runtime; it does not rebuild or relabel the completed financial calculation.

## Installation and service

Dependencies use the existing project and lock:

```sh
cd /opt/grid
UV_PROJECT_ENVIRONMENT=/opt/grid-tools/results-viewer /opt/grid-tools/module1/bin/uv sync --locked --no-default-groups --group viewer
```

The installed template is
[`grid-results-viewer.service`](../deploy/grid-results-viewer.service).
It runs as the existing unprivileged `grid-backtest` user, with only the bind
capability needed for port 80, read-only system paths, a 2 GiB service ceiling
and no public listener. The existing managed Python installation is bound read
only into `/opt/grid-tools/backtest-runtime` inside the service namespace.
The service has traverse-only ACL access to the original private result-parent
folders; the original result files and database are not rewritten.
Streamlit watches application sources and reruns open pages when they change.
The source-file stamp also invalidates cached results after a code update.
The detail worker uses the same unprivileged user and managed Python bind, with
its own 64 GiB systemd ceiling, no network address families beyond AF_UNIX and
an unlimited service stop grace period for checkpoint completion. Both service
templates are under `deploy/`; Nginx remains masked.

```sh
systemctl status grid-results-viewer
journalctl -u grid-results-viewer -n 50 --no-pager
systemctl restart grid-results-viewer
systemctl status grid-results-detail-worker
```

To stop the viewer: `systemctl disable --now grid-results-viewer`. Nginx remains
masked. Retired UI files/configurations were preserved in the deployment backup
under `/opt/grid-runs/results-viewer-ssh/`; old run artifacts are untouched.

## Local verification

```sh
GRID_SWEEP_TEST_BINARY=/opt/grid-builds/calendar-fix-test/grid_backtest uv run --locked pytest -q tests/research/test_results_viewer.py tests/research/test_results_viewer_jobs.py tests/research/test_research_sweep_statistics.py
MYPYPATH=src:tests:. uv run --locked mypy --explicit-package-bases src tests scripts/results_viewer.py scripts/results_viewer_data.py scripts/results_viewer_jobs.py
uv run --locked ruff check .
```

The removed HTTP-handler tests were retired with that interface. Pure archive,
financial totals, progress, FX and performance tests remain. New tests cover
exact binary projections, file immutability, publication/hash failures, numeric
precision, all-column filters/sorting, missing values and Streamlit interaction.
Browser verification and deployment receipts are in
`/opt/grid-runs/results-viewer-ssh/`.

The original detailed-replay deployment receipt is
`/opt/grid-runs/results-viewer-ssh/detail-implementation/deployment-receipt.json`.
Its 225 Python regressions, release/sanitizer CTest checks and 107-month browser
checks apply to the preserved seven-venue archive. For the current five-venue
publication, 55 focused viewer/detail/statistics regressions, Ruff and mypy
passed. An actual 4,480-row Streamlit test read the new binary publication and
median derivative; selected pages showed undefined closed PF without NaN and
the exact marked-PnL month ratio. The installed services and unprivileged
service-user reader both validated the 881-source publication, and the loopback
HTTP route returned 200. No detail replay was started during this viewer update.
Neither original nor current results.bin and results.sqlite were rewritten.

The previous 4,480-configuration publication has a separate 1,388,797-byte median index.
Its complete five-batch binary catalog passed an additional read-only SQLite
integrity check and an independent SHA-256 check of all 109 selected files.
The new main and detail pages loaded without exceptions in Streamlit AppTest;
the native table opens details only on a real browser double-click, while the
leader cards use ordinary links. A small event bridge resolves the clicked
visual row from the native grid's accessibility table, including native sorting
and scrolling; it does not replace the dataframe or navigate on single-click.
The configuration column is pinned and retained by the sidebar column selector.
If it is explicitly hidden using the native column toolbar, double-click asks
to restore it rather than choosing a different row. JavaScript tests cover
header/empty-space rejection, changed sort order, scrolling and reordered cells. The main table shows only configuration
parameters and direct profit/loss measures; other fields remain available for
filtering and sorting. All 43 viewer/detail regressions passed with the compiled
offline sweep executable. The detail worker's unprivileged account can read the
new selection's parent manifests and all 109 selected source files.
The loopback health route returns `ok`, and both installed services point to
the new publication. The restored table was also exercised in local headless Chromium: single-click
retains the overview, double-click opens the matching configuration, venue/month
switching and another browser tab remain independent, and browser Back restores
the overview. This does not claim inspection on the user's laptop.
