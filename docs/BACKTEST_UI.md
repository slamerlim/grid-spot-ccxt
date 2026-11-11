# Backtest interface

The interface runs the existing Module 1 C++ research engine on existing files,
shows results, and compares up to four runs. No new trading engine, database,
JavaScript framework, account connection or paid service is required.

## Start

Open **http://103.1.225.114/module1/backtesting**. Sign in as **`grid`**, using
the password in `/etc/grid-backtest-ui/access.json` on this host. That file is
readable only by root and stays outside Git. The requested HTTP connection is
unencrypted, including the login; authentication does not provide encryption.

The installed service is running and starts after reboot. No terminal process
or port forwarding is needed for this URL.

For manual localhost operation instead, first stop the installed service to
release its port and output-directory lock:

```sh
sudo systemctl stop grid-backtest-ui
cd /opt/grid
/opt/grid-tools/module1-py312/bin/python -m scripts.backtest_web
```

Then open **http://127.0.0.1:8765** and keep the terminal open. After stopping
that process, restore the hosted interface with `sudo systemctl start grid-backtest-ui`.

For manual localhost mode from remote VS Code, open its **Ports** panel, forward
**8765**, then open `http://127.0.0.1:8765` on your computer. An SSH alternative,
run on your computer, is `ssh -N -L 8765:127.0.0.1:8765 USER@HOST`; replace the
user and host with your existing connection. Keep forwarded access local.
The Python listener still accepts only localhost/127.0.0.1 hostnames. The
installed proxy supplies that backend Host and preserves the browser Origin.
The service explicitly permits the public origin while retaining its request-token check.

For another prepared checkout/environment, run
`uv run --locked python -m scripts.backtest_web --binary /absolute/path/grid_backtest`.
Build instructions are in [the C++ README](../cpp/backtest/README.md).
The default executable on this host is
`/opt/grid-builds/module1/release-public-guards-v1/grid_backtest`.
Use `--port 8766` if needed and open the matching URL.

## Run and compare

1. Give the run a recognizable name. Select one or more markets.
2. Choose dates. **All form and displayed dates use Europe/Moscow (UTC+3)**;
   the engine and downloaded evidence use UTC. The start is inclusive and the
   end exclusive. **Last 7 days** is a quick starting point; **Full overlap**
   chooses the selected files' shared available span, retaining every gap.
3. Set capital and cash reserve **per market**, grid spacing, basket minimum
   profit, seed policy, lower BUY policy, pending levels and maximum held lots.
   With ten markets and 1,000 USDT per market, total starting funding is 10,000.
   The maximum committed cost is capital minus the cash reserve.
4. Select the OHLC convention. Open **Costs & reentry** for hypothetical BUY
   and SELL fees, spread, place/cancel delay and reentry policy.
5. Click **Run backtest**. One new run executes at a time in this workspace.
   You can browse saved results while it runs. There is no invented progress
   percentage. Full histories can take tens of minutes.
6. Use **Reuse** on a saved run, change a setting and run again. Tick up to
   four completed runs for the NAV chart and side-by-side settings and metrics.
   Differences in markets, dates or funding are explicitly flagged. Costs and
   execution assumptions remain visible rather than being silently normalized.
7. Click a run name for allocation results, coverage, final exit/cycle state,
   exact input and JSON downloads. **Export CSV** downloads the comparison
   table. Its displayed money is rounded; `stdout.json` preserves exact strings.

The global **+10% net NAV trigger stays enabled and fixed**. It initiates bounded
LIMIT liquidation; it does not promise execution or +10% realized profit.
Automatic exchange/symbol TP tiers remain disabled in this simple interface.
Orders use minimum lots with one aggregate SELL. Pending-level count applies
only to the multi-BUY policy. Open-order capacity is `max(8, levels + 2)`.
The basket minimum profit is an absolute USDT floor, alongside the geometric
target; it is not a percentage or a portfolio profit promise.

## Data and interpretation

The menu contains the ten previously structurally validated files from
`research/runs/module1-20261110/selection_manifest.json`: OKX 1INCH/AGLD/APE,
HTX MINA/XTZ/ELA, Gate WPAY/BGSC and KuCoin AKT/OP, all quoted in USDT.
It does not silently admit the rest of the archive. A new window is counted
with read-only timestamp searches; the engine then verifies complete source
hashes and chronology while replaying the selected window. Inputs under
`/opt/datasets/` are never written or copied into a new data format.

The existing 22 trials are loaded from
`/opt/grid-runs/module1-20261110/full-study-v4-notices/` when available.
Their retained input/result hashes and accounting are checked before showing
financial values. **PASS means validated simulation output, not profitability
or live readiness.** Invalid, failed, cancelled and interrupted attempts retain
their status without being assigned a zero return. Existing studies are read
at server startup; restart the UI to reload a study changed externally.

Every result remains **UNVERIFIED SOURCE / ASSUMPTION-BASED**. OHLC has no volume,
queue position, spread history or intraminute execution order. Spot identity,
historical venue precision/minima and actual account fees remain unverified.
The UI inherits the declared synthetic rules: price tick `1e-8`, amount tick
`1e-4`, minimum BUY/SELL notional 5 USDT and quote-denominated fees. The primary
model allows full remaining eligible fills without historical depth evidence.
The advanced fields change explicit assumptions; they cannot recover missing
facts. There is no automatic optimizer or claim of out-of-sample testing.

The comparison emphasizes total net NAV, realized **and** unrealized PnL,
observed drawdown, fees and remaining position cost. Net NAV estimates proceeds
from sellable inventory after forecast exit costs; untradeable residuals stay
owned but contribute no estimated liquidation proceeds. Unknown valuations
stay **Unknown**, never zero. Profit factor stays **Undefined** when there are
no realized losses. A positive realized basket profit can coexist with a
negative total return from unsold inventory.

The chart uses the engine's first, daily and final NAV samples. Unknown sampled
NAV breaks its line; additional gaps may exist between plotted samples.
Drawdown is measured at every observed timestamp group and can still miss
intrabar or missing-period risk. A single selected run also shows the funded
buy-and-hold reference as a dashed line. This is a hypothetical benchmark,
not proven executable LIMIT entry. Different executable hashes are disclosed
when comparing the older frozen study with newer UI runs.

## Storage, cancellation and recovery

New runs live in **`/opt/grid-runs/backtest-ui/run-…/`**, outside Git. Each keeps
its settings/job, effective configuration, selected source manifest, TSV input,
copied engine, result, receipt and logs. Failed and cancelled runs are retained.
The server does not delete old runs. Use `--output /another/external/directory`
for a separate workspace, and `--no-study` to omit the existing study.
Output under datasets or a Git repository is refused.

**Cancel run** terminates the owned worker and its engine, retaining the attempt.
Closing the browser tab does not cancel. `sudo systemctl stop grid-backtest-ui`
stops the hosted app and cancels its active run. In manual mode, **Ctrl+C** in
the server terminal does the same. Completed runs return on restart.
Runs without a final receipt are marked **INTERRUPTED** and never auto-resumed.
An output-directory lock prevents another UI from owning the same workspace.
If the server is forcibly killed, its inherited-lock worker may finish under
its existing timeout before that directory can be opened again; no second run
is automatically started. A hard host crash cannot be treated as a completed run.

The existing child driver caps each run at six hours, 512 MiB address space,
and 32 MiB per engine output file. These are process limits, not trading
deployment certification or CPU/device quotas. The password-protected UI is
one operator workspace, with no separate user accounts or per-user data.

## Installed HTTP access

The owner's subsequent request for this public URL authorizes hosting the
research UI only; live trading and bot deployment remain excluded.

- `grid-backtest-ui.service` runs as unprivileged `grid-backtest`, listening on
  `127.0.0.1:8765`. Its filesystem is read-only except for the UI run directory
  and private temporary storage. It has a 1 GiB memory limit, 32-task limit and
  bounded restarts. The existing interpreter is mounted into a private readable
  path; the owner's home remains inaccessible. Traversal permission was added
  for this account to `/opt/grid-runs`; dataset permissions were unchanged.
- Nginx listens on `103.1.225.114:80`. Only `/module1/backtesting/` proxies to the
  app; page, assets, API and downloads all require login. Other paths return
  404. Credentials are stripped before proxying to Python.
- Installed configuration: `/etc/systemd/system/grid-backtest-ui.service` and
  `/etc/nginx/sites-available/grid-backtest-ui`. Source templates are
  [the service unit](../deploy/grid-backtest-ui.service) and
  [the Nginx route](../deploy/grid-backtest-ui.nginx.conf). The password hash is
  `/etc/nginx/auth/grid-backtesting.htpasswd`; credentials are never committed.

Check or restart:

```sh
sudo systemctl status grid-backtest-ui nginx
sudo journalctl -u grid-backtest-ui -n 50 --no-pager
sudo systemctl restart grid-backtest-ui
sudo nginx -t && sudo systemctl reload nginx
```

For rollback, stop/disable `grid-backtest-ui`, remove only the
`/etc/nginx/sites-enabled/grid-backtest-ui` symlink, then validate and reload
Nginx. Retain all run directories. The manual localhost workflow remains
available after stopping the service.

References: official [Nginx proxy](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)
and [HTTP Basic authentication](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html)
documentation.

## Implementation and validation

The local server is `scripts/backtest_web.py`; its static HTML, CSS and
JavaScript are under `scripts/backtest_web_static/`. It reuses the already
pinned `aiohttp`, the existing TSV exporter, bounded child runner, source and
financial validation. Python trading code, C++ strategy code, schemas and
dependency manifests are unchanged.

Focused checks:

```sh
/opt/grid-tools/module1-py312/bin/pytest -q tests/research/test_backtest_web.py
/opt/grid-tools/module1-py312/bin/mypy --explicit-package-bases scripts/backtest_web.py tests/research/test_backtest_web.py
/opt/grid-tools/module1-py312/bin/ruff check scripts/backtest_web.py tests/research/test_backtest_web.py
node --check scripts/backtest_web_static/app.js
```

Tests cover invalid parameters, date/market limits, exact configuration export,
source mutation, gaps, output boundaries, one-worker ownership, retained failures,
restart, cancellation/reaping, localhost request protection and download paths.
Browser checks and actual archive-run evidence are retained outside Git at
`/opt/grid-runs/backtest-ui-qa/`. Hosted CI remains pending; GitHub Free only.

Initial local UI validation on 2026-11-11 (Moscow): the full regression run
passed 1,711 tests; 21 focused UI tests passed on Python 3.12 and 3.13. Lint, formatting,
type checks, Bandit, the secret hook, locked dependency audit and packaging pass.
Chromium checks pass at 1440px and 390px, including submission, comparison,
reuse, CSV download and page reload. Four short validation runs are retained
with names starting **UI check**. They replay OKX 1INCH/USDT from
2025-01-01 03:00 to 2025-01-08 03:00 Moscow, 10,080 candles each:

| Spacing | Starting NAV | Ending net NAV | Net return | Engine elapsed |
|---|---:|---:|---:|---:|
| 1% | 1,000 USDT | 997.853693941914 | −0.2146306058086% | 2.55–2.59 s |
| 1.5% | 1,000 USDT | 998.646379129158 | −0.1353620870842% | 2.54 s |

Three 1% repeats have identical input and result hashes. These are UI/runner
validation trials, not a strategy recommendation or independent holdout study.
All ten original source identities and full-file SHA-256 hashes still match
the frozen manifest after the checks. See `real-run-summary.json` and
`browser-receipt.json` in the external QA directory for exact run IDs/hashes.

The subsequent HTTP-hosting regression passed **1,720 tests** in 592.46 seconds;
all 28 focused tests also passed on both Python versions, including
public-origin matching, retained CSRF checks and rejection
of foreign hosts. Browser verification through the requested public-IP URL
passed login, the subpath redirect, assets/API, all 26 prior saved runs, a real
10,080-candle replay under the unprivileged service account, and result download.
The additional **Hosted route check** finished in 2.58 seconds and is retained.
Unauthenticated access returns 401; unrelated paths return 404. Both services
are enabled at boot; no host reboot was performed. Namespace inspection confirms
read-only dataset mounts and a writable UI output mount. All ten original file
identities and hashes still match. Evidence is in
`/opt/grid-runs/backtest-ui-hosting-qa/receipt.json`. Browser checks ran on this
host using its public-IP URL; independent off-host reachability was not verified.
