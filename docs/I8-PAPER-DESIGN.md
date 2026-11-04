# I8 bounded public-paper harness

The owner approved the harness and a one-hour observed run, then continued
development. `I8-SOAK-PLAN.md` records the duration/budget amendment. This is
partial I8 evidence; live execution, account access and deployment remain disabled.

## Public boundary and eligibility

`python -m grid.paper` permits only the existing OKX BTC/USDT instrument and
20-level book GETs. One CCXT 4.5.91 instance/session runs per process. The explicit
observed-hour profile permits 750 wire attempts, at most three instrument calls,
five-second spacing, five-second timeout and 64 KiB response bodies. Probe
defaults retain their original 32-request and five-minute maximum settings.
There are no credentials, private calls, order endpoints, redirects or proxies.

The official documentation was checked on 2026-11-04: public instruments require
no authentication, allow `instType=SPOT` and `instId`, and specify 20 requests per
two seconds per IP/instrument type. The ordinary book endpoint specifies 40 per
two seconds per IP, supports `sz=20`, and reports base-currency spot depth and a
generation timestamp. The experiment's spacing is considerably lower.
[OKX public instruments](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments),
[OKX order book](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-order-book).

Every wire attempt is fsynced into a single-owner, hash-chained journal BEFORE
network access. Failures consume budget. Restarts preserve the original deadline,
spacing, cooldown and endpoint counters. A partial/altered journal refuses reuse;
an uncertain local write poisons the current writer. HTTP Retry-After delays are
preserved with a minimum 60-second hold; malformed values refuse continuation.
Failures halt paper admission, record a checkpoint and wait at least 60 seconds.
Three total failed checkpoints stop this short experiment, a stricter bound than
three consecutive failures. No regional-domain workaround exists.

Public metadata is NOT complete trading authority. Missing price, amount or
notional bounds, precision, inactive/mismatched identity or unmapped constraints
prevent paper allocation. UNKNOWN never becomes zero or unlimited. A rejected
candidate leaves the invented 1,000 USDT in its ledger pool, records the missing
fields and continues feed observation. Its report must say zero active paper
allocations/fills if that is what happened. Account-specific constraints and
actual fees remain NOT TESTED regardless of public results.

## Financial and synthetic-world ownership

The existing SQLite AsyncWriter remains the only financial writer. The separate
journal contains public observations, resource checkpoints and the fake venue's
external order/trade events, not an alternative balance ledger. Synthetic event
acceptance is durable before its ACK. Replay reconstructs that fake external
world before the existing reconciliation-first supervisor can resume.

For complete mock/public constraints, the paper account is named `paper-okx` /
`invented-account`. Its explicit fixture uses 1,000 USDT, a 100 reserve, 900 maximum
commitment, 1% spacing, three lower levels and ten lots. The minimum lot comes
from the supplied constraints; it is not enlarged to force eligibility. Fees are
an ASSUMED 0.2% in USDT per side, rounded up at 0.00000001 USDT. This is not an OKX
fee claim. The immutable paper contract and fee assumption are checked on each
fresh book; their validation time is separate from the preserved timestamp of
the cached public metadata (maximum one hour).

Fill model `strict-subsequent-book-full-depth-v1`: only an already accepted order
can fill; a later fresh book must strictly cross its limit and show enough
visible crossed depth for the whole remainder. Fill at the limit, at most one
order per observation, and charge the assumed fee. Touches do not fill. Pending
cancels may race a fill and settle afterward. This does not prove traded volume,
queue priority or actual execution. Duplicate/stale books cannot create fills.

I7's +10% net NAV trigger and LIMIT-only exit remain in use. The paper profile
has a 30-second TTL, three attempts, a 120-second window and no widening. Its
existing 95% trigger-anchor lower bound is stricter than the proposed 80% floor;
a fresh-reference check additionally blocks a candidate outside the current bid's
5% band. In the follow-up, this refusal is durably latched in the observation
journal and halts the financial supervisor. A later in-band price or process
restart cannot clear it. Unfilled/blocked inventory stays explicit. No automatic liquidation at
the end of the hour is invented.

## Schedule, evidence and recovery

Real CLI timing uses UTC plus monotonic time; a two-second divergence or regression
refuses continuation. The minute-30 process exits with code 75 after checkpoint
and cleanup. A supervising agent must explicitly start `--resume` within 60
seconds, using the same source and boot identity. Only one planned restart is
accepted. A different source, completed/failed run or expired window cannot be
silently resumed. A mock clock report is labeled `mock-clock-complete`; it never
claims an observed hour or a 48-hour acceptance pass. For the replacement run,
the transient coordinator below performs that preauthorized restart.

The minute-10 disconnect and minute-45 429 are local 60-second injections. Neither
causes an extra HTTP request. Minutes 20 and 50 create and independently restore
SQLite backups. Their semantic hashes, request watermarks and journal-prefix
hashes are retained. Every minute and fault boundary records actual resources,
public counts, eligibility and synthetic financial status.

The 512 MiB RSS/directory, 16 MiB WAL, queue 8, intake 16, alert 32, task 20 and
50% single-core minute CPU caps remain enforced. Require 2 GiB free disk. The
bounded journal permits 10,000 records of at most 256 KiB, with no rotation or
automatic deletion. Outputs are private files in a private directory outside
Git/original datasets, retained at least seven days after review.

Use a frozen committed checkout for the observed run. Record the commit and
launch command externally alongside the journal; the start record captures
Python source/lock/CCXT hashes, approved mock SQLite identity, host and time.
The report records actual coverage/gaps and real order count zero. Incomplete
cleanup or any fault yields partial evidence, never a completed soak.

Local validation is authoritative for this approved increment. Hosted CI remains
pending under the unchanged account lock; publish with `[skip ci]`, GitHub Free
only. A separate first-account evidence plan and the original observed 48-hour
gate remain necessary before I8 acceptance; I9 is outside this approval.

The first actual attempt was interrupted by the orchestration environment after
about 15 minutes. Offline recovery verifies its ledger and a backup before marking
it partial. The one permitted replacement carries its spent requests forward,
uses a slower 7.5-second cadence and preserves both evidence directories. The
new `scripts/supervise_paper.py` transient coordinator observes the worker every
30 seconds and restarts it only once after exit 75. A Linux pidfd lets the worker
detect coordinator death before its next poll. No systemd service is installed.
`grid.paper.audit` independently checks finished hash chains, response hashes,
budgets (including the parent attempt), source freshness, spacing and duration;
it refuses an active writer and never changes the journal.
Record envelopes and carried-over counters are validated separately from their
hashes: a valid hash cannot make a malformed payload, boolean clock/sequence or
negative budget valid. The CLI returns a fixed refusal without echoing input.
The coordinator's two narrow Bandit annotations cover the reviewed subprocess
import/call: the executable is `sys.executable`, the module is fixed, user values
are separate argv elements and `shell=False`. No financial/network subprocess
interface was added to the application packages.
