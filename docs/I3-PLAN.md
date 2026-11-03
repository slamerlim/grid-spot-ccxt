# I3 review proposal — read-only CCXT venue discovery

**Status: proposed, not authorized.** I2 approval ends at its review handoff.
This proposal does not authorize a network probe, credential access or order.
Hosted CI remains pending; retain local validation and GitHub Free only.

## Proposed authorization

Implement read-only CCXT adapters and deterministic mock tests, then perform
bounded public market/order-book discovery for the seven target venues where
the pinned official CCXT distribution supports it. Stop after I3 evidence.
No submissions, cancellations, transfers, withdrawals, sandbox trades or live
deployment are included. Authenticated read-only probes remain NOT TESTED unless
the owner separately provides and authorizes specific account access; do not
discover existing credentials automatically or ask for secrets in chat.

## Concrete work

1. Inspect the installed CCXT 4.5.91 interfaces and current official documentation
   before choosing methods. Keep the single lock and long-lived client ownership
   per venue/account. No paid CCXT/GitHub products or separate proprietary feed.
2. Build explicit mappers for spot market identity, unleveraged eligibility,
   price/amount precision modes, buy/sell minima/maxima and known/unknown fee
   facts. Reject missing or ambiguous evidence. Do not guess a zero fee, unlimited
   balance or supported spot account from a successful public request.
3. Add a read-only method allowlist, bounded concurrency/timeouts/retries, rate
   budgets and deterministic mocked public/private response fixtures. Creation,
   cancellation and other write methods remain unavailable to the adapter.
4. If public probes are approved, use a small explicit request budget for
   OKX, HTX, Gate, XT, BingX, KuCoin and Bitfinex. Record venue/version, public
   spot metadata and a small book sample, method and actual result without
   secrets. Stop/reduce probing on rate limits or unavailable access. Do not
   call all instruments or perform an unrestricted universe scan.
5. Update `EXCHANGE_CAPABILITIES.md` with separate advertised capabilities,
   observed public results, mocked behaviors and untested private functionality.
   Unsupported/private-stream paths, particularly XT, remain explicit unknowns
   with a bounded REST fallback design; a `has` flag is not observed support.
6. Run both supported Python suites plus lint/types/security/build checks locally,
   publish evidence with `[skip ci]` while the account lock is unchanged, and stop
   before the I4 executor and event-recovery gate.

## Acceptance and limits

- Tests prove method restrictions, bounded client reuse/closure, precision and
  currency mapping, missing fee/rule rejection, stale input handling, rate-budget
  refusal and failure classification using synthetic responses.
- Public network results are reported per venue, including unavailable outcomes;
  neither mocked success nor installed library metadata becomes a venue PASS.
- No funding assignment is inferred from an exchange wallet balance. I2 ledger
  ownership and reservations remain authoritative; no live financial database is
  created as part of discovery.
- Actual authenticated spot account types, fee tier/currency, private stream
  behavior, order/trade deduplication and balances stay NOT TESTED without the
  separately authorized account probes. No credentials are required to complete
  a useful public/mocked I3 handoff, and those gaps remain explicit.
- No change to the approved I1 strategy or I2 financial invariants is implied.
  Any discovered incompatibility is documented before changing those contracts.

Rollback is a scoped code/docs revert with the matching lock; no external order
or operator-state mutation is part of this proposal. I4 and all trading remain
separately gated.
