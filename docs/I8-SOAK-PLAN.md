# Proposed next I8 gate — public-paper harness and observed run

**Not approved or started.** I8 preparation authorization excludes exchange
access and the long-running soak. This is a concrete proposal for the next gate;
approval of it would not authorize private-account access, real/sandbox trading,
deployment, notifications, transfers, paid infrastructure or I9 canary.

## Proposed work and evidence boundary

Implement and locally validate a bounded public-data collector and paper forward
harness, then execute one supervised run of at least **48 observed wall-clock
hours**, with a hard stop at 49 hours. Use the existing workspace and approved
patched mock runtime; no systemd installation. Keep the coordinator alive to
observe checkpoints; a detached process alone does not establish a completed
run. If the environment cannot support that duration, report partial evidence
and stop rather than accelerate clocks or silently restart the acceptance window.

The first candidate is **OKX spot BTC/USDT (`BTC-USDT`)**, selected because the
previous authorized I3 pass observed its metadata and book. This is a candidate,
not an account/venue certification. Collect a new versioned candidate dataset
with capture times, raw/unified identity, provenance hashes, rules and book
validation. Do not modify `/opt/datasets` or treat old public observations as
current fee, account or execution evidence.

The harness does not yet exist. It must be implemented and reviewed under this
next approval, preserving strict separation between public observations,
invented paper funds/fees/fills and unverified private capabilities. A public
book must never make a real account's `account_verified` flag true. Keep paper
allocations explicitly labeled `paper-okx`; all orders remain in a synthetic
execution model. Use no private or sandbox order endpoints.

## Exact external access and budgets

Only unauthenticated HTTPS GETs to `www.okx.com`:

| Purpose | Path and exact query | Schedule/bound |
|---|---|---|
| Spot instrument | `/api/v5/public/instruments?instType=SPOT&instId=BTC-USDT` | Startup, reviewed restart, and at most hourly; 51 attempts total. |
| Book | `/api/v5/market/books?instId=BTC-USDT&sz=20` | At most one attempt per five seconds; no catch-up burst. |

Use one reusable client/session, one in-flight request, zero WebSocket
subscriptions, no redirects, proxies, pagination, authenticated headers or
ambient credentials. Cap **all wire attempts, including failures/retries, at
36,000** across both endpoints and restarts. Reuse the existing pinned CCXT
4.5.91 public boundary; add persistent budget accounting before the first call.
Per-response decoded body cap: 64 KiB; request timeout: 5 seconds. On 429 honor
Retry-After, with a minimum 60-second wait and no retry outside the global cap.
Three consecutive transport/validation failures pause paper admission and
require an observed recovery checkpoint; three unsuccessful checkpoints stop
the run. Do not bypass regional blocking or choose another domain automatically.

Before the first authorized feed request, verify the current official API
documentation/rate policy for these paths. Any required new path, authentication,
venue, symbol or higher budget needs a separately reviewed change. No such
exchange request or verification was made during I8 preparation.

## Paper-only financial and fill assumptions

Proposed fixture: 1,000 synthetic USDT, BTC initially zero, 100 USDT reserve,
900 USDT maximum committed, 1% geometric spacing, three lower levels, ten lots
maximum and one aggregate sell. Use the observed executable minima/precision;
if the minimum lot cannot meet constraints, reject it. Do not top up or enlarge
lots to force eligibility. The paper return target remains +10% aggregate net
NAV, with final execution return reported separately.

Use a disclosed **assumed** 0.2% quote fee on each side solely for paper
experiments; real account fee status stays UNKNOWN. Do not claim these as OKX
fees. Use only fresh, internally consistent public books (maximum age 10 s).
Conservative paper fills require a subsequent observation crossing the order
price, debit disclosed fees, avoid same-observation two-sided round trips and
never imply queue priority or actual liquidity. Record the exact fill-model
version and uncertain/unfilled orders. Public books cannot establish fills or
ownership for a real account.

Proposed bounded paper exit: use fresh bid limit, floor at 80% of the observed
trigger bid, maximum 5% deviation from each fresh reference, 30-second order TTL,
three attempts and a 120-second exit window. No widening, market orders or
automatic resume; blocked inventory remains visible. These are explicit test
choices for approval, not defaults or investment guidance. Validate them locally
before the run; inability to represent them blocks execution of this proposal.

## Host, storage, retention and observation

Use a unique private directory under `/tmp/grid-i8-observed/`, outside Git and
all original trees, with mode 0700 and files 0600. Capture exact code/lock/fixture
hashes, Python/SQLite source approval, host/kernel/filesystem/cgroup limits,
monotonic start and UTC timestamps. The current container filesystem is only a
candidate; it does not establish production NVMe or future host suitability.

Proposed experiment caps: 512 MiB process RSS, 512 MiB total run directory,
16 MiB WAL, eight pending writer operations, 16 intake notices, 32 local alerts,
20 asyncio tasks and at most 50% of one core averaged over a minute. Require
2 GiB free storage before starting. Sample resources every 60 seconds and before
database/backup growth; enforce byte/count bounds on writes and input buffers.
Stop on a breach; do not buy capacity or increase caps automatically. Target-host
production thresholds remain an independent acceptance input.

Keep append-only provenance/data/health/checkpoint records within the run cap.
Take one verified backup at hour 12 and one at hour 36 into the same bounded
directory; restore each into a distinct verification file. Retain all run
evidence for seven days after review. No automated deletion of originals,
unreviewed log rotation or deletion of failed-run evidence. If retention cannot
be assured on `/tmp`, resolve a durable private destination before the first
request and record it in the final execution manifest.

Observe and record checkpoints at startup and hours 1, 6, 12, 24, 36 and 48,
plus every injected fault and recovery. Record wall-clock coverage, gaps,
successful/failed endpoint counts, resources, owned paper balances, unknowns,
orders, fills, fees, NAV/return/shortfall, residuals and alert evictions. The
report format extends the preparation manifest with real observation provenance;
synthetic elapsed time cannot satisfy the observed duration.

## Fault schedule and stop criteria

At hour 6, intentionally skip public polling for 60 seconds. At hour 24, shut
down the paper process, preserve the ledger/client-budget journal and restart
from the complete roster; require reconciliation and explicit reviewed resume.
At hour 36, inject a 60-second local 429/backoff response in the harness, labeled
**synthetic injection**, without sending extra server requests. Perform backup
restore verification at the scheduled hours. Disk-full/permission/corruption
drills remain in separate disposable mock databases; never fill the host volume.

Immediately stop new paper exposure on stale/unknown evidence, unexplained
balance drift, duplicate submission, multiple possibly active sells, invalid
minimum/fee handling, missing ownership, audit/storage failure, failed restore,
regressing clocks, cap breach or inability to supervise the run. Preserve orders,
state and evidence. Cancel the run on unresolved faults; report exact elapsed
time and findings as partial/failed evidence. Restarting does not erase a fault
or create a pass. End-of-run inventory/open paper orders stay explicit.

## Review outcome and separately gated private evidence

The public-paper report can establish observed duration, public feed behavior,
paper conservation, resource and restart/restore evidence. It **cannot alone
complete I8 acceptance**. The intended-first-venue matrix must still show private
order identity, order/trade recovery, actual fees, account ownership and private
event capability as NOT TESTED until observed with authorized account access.

After public-paper review, prepare a separate account-specific plan for the
missing rows. It must name the intended account, exact read-only permissions
and endpoints, any source of historical order/fill evidence, credential handling
and request budgets. Sandbox order/cancel tests, if needed, require a further
explicitly stated authorization. No credentials are to be discovered or
requested during preparation/public-paper execution. No passing mock test or
unsupported capability can be recorded as an observed private pass.

Only a reviewed minimum 48-hour observed run, zero unexplained drift/invariant
breaches, verified restore and the required first-venue matrix can close I8.
I9 live canary still requires separate venue/account/funding/risk signoff.
GitHub remains Free-only, local validation remains distinct from pending hosted
CI, and the unchanged Actions account lock must not be retried.
