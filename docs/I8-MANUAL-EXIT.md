# I8 offline follow-up — explicit manual portfolio LIMIT exit

Scope: continued mock development authorized after the completed public hour.
Base: `a7d2017a60ac32332541b3b44825101fc06b6c86`, branch `i8/public-paper-hour`.
This adds `gridctl operator limit-liquidate` for the **whole current bot-owned
portfolio cycle**. It makes no exchange calls and cannot enable sandbox/live
trading. GitHub remains Free-only; the unchanged hosted-CI lock is not retried.

## Behavior and approval boundary

Previously the portfolio exit latch could only represent an observed +10% NAV
trigger. Manual exit is now a distinct recorded cause, permitting an operator
to request a bounded exit below that target, including at a loss. It uses the
existing cancellation, reconciliation, LIMIT pricing, fee, dust and completion
logic; it does not fabricate threshold evidence or alter the grid strategy.

The command requires an operational revision, full allocation roster, actor from
the local peer UID, reason, exact saved request, cycle ID and exit-policy SHA-256.
The first invocation saves a preview and exits 3; a second identical invocation
with `--confirm` can submit it. An initial `--confirm` is refused. Inspect the
current cycle's complete policy in status before reviewing the preview.

The policy digest and unique cycle ID bind the approval to the intended rules.
Ordinary NAV evaluations change the portfolio's state revision but do not change
its immutable policy or invalidate a prepared approval. A different policy,
cycle, stale operational revision or changed request identity is refused. Exact
receipt replay is permitted after state advances and does not request another
exit. The current cycle phase/cause is returned alongside the historical receipt.

This command accepts only the complete supervised roster. A partial selection
is refused; it is never widened to the rest of the account. Per-allocation manual
liquidation and the universe's per-symbol bounded-exit retirement option remain
unfinished. Bot ownership is the registered cycle roster and explicitly included
pools, never all assets found in an exchange wallet.

Before acceptance, current account/rule/book/fee/conversion evidence and reconciled
ownership must permit a fresh NAV. Stale/missing evidence, unknown ownership or
unclassified cashflows refuse the request without a partial manual latch. Use
the distinct pause/cancel/reconcile controls to resolve such a preflight refusal.
No fallback price or fee is invented. An existing admission halt or held scope
is preserved; manual liquidation does not impersonate resume. Review a separate
confirmed resume if those controls intentionally block exit sends.

## Atomicity, execution and reporting

One existing writer transaction commits the manual cause/approval, exit-request
NAV and adjusted baseline, exit slots, allocation modes, unsent-order revocations,
required cancellation intents, operational revision, actor/reason audit and
idempotent receipt. A fault between the portfolio and operational postings rolls
back the entire request. There is no nested request transaction or second writer.

The shared exit latch prevents new ordinary BUY claims. Proven unsent orders can
be revoked locally; possibly live orders retain ownership until authoritative
mock reconciliation. Existing TP sells and pending buys must resolve before exit
SELL placement. A cancellation ACK alone never frees the sell slot. Later partial
fills, late fills, uncertain submissions and restart all use the existing ledger
and mock executor. Price floor, maximum drift, minimum net proceeds, TTL, total
duration, order count, fee reserves and residual approval remain enforced.

Status reports `exit_cause` as `operator`, `nav_target` or null, and reports
`target_reached_at_exit` independently. For manual exits, the existing stored
`trigger` field is the fresh NAV at the accepted exit request, not proof of +10%.
Final return and exit shortfall still derive from actual reconciled mock fills
and classified cashflows. An accepted command is not proof of a completed exit;
blocked orders, residuals and liabilities remain visible. No new cycle starts
automatically.

## Stored-record compatibility and rollback

The SQL schema and dependencies are unchanged. Cycle snapshots gain an explicit
optional `manual_exit` approval record. The codec omits only that field when it
is null, and accepts its absence only on Cycle. Existing automatic-cycle bytes,
hashes, threshold proof and backup semantics remain unchanged. Unknown fields,
missing required fields and redundant null encodings are still refused. Manual
approval is retained throughout subsequent exit snapshots; the ledger verifies
that its digest still matches the policy. Actor/reason remain in durable history.

Compatibility tests use two hashes captured with the unmodified base revision
before implementation. Automatic and active cycle encodings still match them
exactly. Manual records introduce a new action/record field that older runtimes
cannot interpret. Once a ledger contains them, do not downgrade its runtime or
erase the new records. A code rollback requires a compatible reader or a separate,
verified pre-change backup for a disposable mock session, preserving newer
history for inspection. No existing ledger or historical report is rewritten.

## Executed validation

Initial focused checks: **51 passed**, 19.45 seconds. The expanded command,
process and compatibility checks then passed **47 tests**, 17.31 seconds.
These focused selections overlap; they are not added to form a total count.

The initial full run recorded 706 passes and one process-test failure on a
refused confirmation. A deterministic follow-up reproduced safe refusal while
reconciliation holds ownership, with no manual transaction committed. Once that
pass finishes, the same saved request can succeed. The process test now inspects
status before explicitly repeating that exact request, with a finite ten-attempt
test bound. It never regenerates the ID. The focused process/IPC pass after this
correction was **25 passed**, 8.29 seconds. No production automatic retry or
relaxed reconciliation guard was introduced.

New coverage includes:

- A 1,000-QUOTE invented cycle with one confirmed 100-QUOTE seed, manual exit at
  a 90-QUOTE bid, partial fill and late fill during cancellation, then a 0.4-base
  replacement only after terminal reconciliation. Final cash is **990 QUOTE**,
  return **−1%**, base zero and manual target flag false. All three captured
  submissions (seed and two sells) are LIMIT. Assumed fees are explicitly zero.
- Exit price floor, minimum net proceeds and duration exhaustion produce a
  blocked exit without a false completion or another submission.
- Lost exit acknowledgement, reopen with startup halt, authoritative synthetic
  recovery and exact receipt replay without duplicate submission or resume.
- Confirmation, cycle/policy/revision/evidence refusals; full-roster enforcement
  across two synthetic venues; current NAV sampling does not change approval.
- Actual SIGKILL after the portfolio latch, after combined postings, before
  commit and after commit: revocations, exit cause and control audit are either
  all committed or all absent. No partial manual transition is accepted.
- Backup/restore retains the manual cause and receipt; a changed approval digest
  is rejected by the ledger. Canonical compatibility and threshold guards hold.
- Separate CLI processes require policy/cycle arguments and a second invocation.
  A stale book returns a fixed refusal while keeping the control channel alive.
  A deterministic concurrent reconciliation refuses first admission and permits
  the same request after recovery, without changing its durable identity.

| Check | Result |
|---|---|
| Full pytest, Python 3.12.16 | **708 passed**, 136.29 s; no failures/skips |
| Full pytest, Python 3.13.18 | **708 passed**, 141.53 s; no failures/skips |
| Ruff / strict mypy / Bandit | PASS; mypy checks 139 application/test sources |
| Dependency lock / advisory audit | PASS; 66 packages unchanged, no known vulnerabilities |
| Secret scan | PASS; baseline unchanged, two documented checksum false positives |
| Wheel and source distribution | PASS; `/tmp/grid-i8-manual-exit-dist/` |
| Hosted CI | PENDING; unchanged lock, not retried |

Application source SHA-256:
`763bb528c17f63223e663ba10865308e50b64ca601270135ad50a7c19e981c09`.
All four original/imported specifications and both retained public-run journals
match their recorded hashes. No PR is created for this increment; publication
uses the acceptance plan's scoped-commit option on the existing review branch.

The secret scanner flagged the two pre-change synthetic cycle hashes as
high-entropy strings. Their provenance was verified against the captured fixture
digests, and only those literals received next-line false-positive annotations.
The baseline is unchanged; no credential was added or allowlisted.

Reproduce with README's explicit Python environment and local check commands.
The focused command is `pytest tests/operations/test_liquidation.py
tests/operations/test_local.py tests/operations/test_console_process.py
tests/portfolio/test_manual_codec.py -q` on one line. Tests use temporary databases
and invented facts, with IP sockets disabled in the test process. No public or
authenticated exchange method was called by this increment.

## Remaining requirements

This improves Q01/Q05/Q09/Q10/Q12/Q19/Q20 mock coverage and PRD section 10; it is
not full acceptance of those requirements against exchanges. Per-allocation
manual exits, audited hot-configuration CLI, owner-mediated backup/restore CLI
and unified research/storage commands remain. Production UTC audit, actual fees,
private account events, the observed minimum 48-hour gate and I9 remain unverified
or separately gated. Continue offline development; keep deployment and trading
disabled. The completed public-hour authorization remains spent.
