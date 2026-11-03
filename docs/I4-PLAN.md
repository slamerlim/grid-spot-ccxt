# I4 review proposal — mock executor and recovery

**Status: approved on 2026-11-04.** The owner explicitly selected
"Approve I4 with local validation" for this plan. Approval ends after I4 review.
Hosted CI remains pending. This proposal uses local validation and GitHub Free
only; it does not waive the later paper/soak or live canary gates.

## Proposed authorization

Implement the order-intent executor, private-event intake and recovery supervisor
using synthetic exchanges and temporary I2 ledgers only. No authenticated
exchange connections, public re-probes, sandbox/live orders, cancellations,
transfers, deployment or real financial databases are included. Actual network
trading and account access require separate explicit scope and approval.

## Concrete implementation

1. Reuse I1's intents/reducer and I2's durable attempt/unknown-state protocol.
   One executor owns sends. Persist reservations and uncertainty before invoking
   a fake gateway; crashes/timeouts never cause blind resubmission.
2. Define a typed limit-only gateway and deterministic fake venue with a captured
   outgoing payload. Reject market types, market helpers, hidden type overrides,
   derivative symbols, stale rule generations and missing account fee evidence.
   Verify exact decimal formatting after exchange precision conversion using
   offline CCXT fixtures; never connect the public I3 transport to write methods.
   Address Bitfinex's compound price precision and XT's offset/protection rules
   explicitly before any future venue admission; generic metadata is insufficient.
3. Implement bounded event intake and REST reconciliation interfaces for fake
   account snapshots. Establish scoped external/internal order mapping, actual
   trade identity, fee completeness, pagination limits, reconciliation epochs
   and duplicate/conflict handling before I2 posting.
4. Preserve the aggregate SELL slot through submission uncertainty, cancel
   races and late fills. Cancellation acknowledgement alone does not authorize
   replacement; reconcile terminal state and executed fills first.
5. Implement bounded supervision: disconnect/unsupported WS falls back to fake
   REST with budgets, pauses new exposure and retains inventory supervision.
   Quiet streams, empty/incomplete pages and unresolved IDs never authorize
   new exposure. Startup reconciles before sending any new BUY.
6. Run fault scripts for accepted-but-timeout orders, process death around each
   durable/send boundary, duplicate/out-of-order fills, cancel/full-sell races,
   late BUY fills, fee/cash conservation, 429/timeouts and queue overload.
   Preserve unknown states on restart and prove no second SELL or overspend.

## Exit evidence

Map relevant Q01/Q04–Q10/Q13/Q17–Q19 checks to actual executed tests. Run both
supported Python suites, lint/types, security and build checks locally. Publish
scoped commits with `[skip ci]`, a design/evidence pack and an I5 proposal; stop
at review. Unverified exchange behavior remains NOT_TESTED. XT's rejected public
book and missing per-venue/account rules remain live-readiness blockers; no
automatic additional public probe is included in this I4 request.

Rollback is a scoped code/docs revert with its lock. All ledger tests use
temporary synthetic databases; no deployed schema or external order is changed.
