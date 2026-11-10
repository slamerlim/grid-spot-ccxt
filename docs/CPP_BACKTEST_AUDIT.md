# C++ backtest infrastructure audit

Inspected starting branch `i8/public-paper-hour`, HEAD
`cd48dd01734d2b84ad13651e907e72fc437a0cbc`, clean worktree.
The checkout contains no C++ research engine. The external legacy
`/opt/module_1_multi/module.cpp` is not reused: its event format and accounting
do not satisfy the approved financial contract. Module 2 is excluded entirely.

Reusable Python components are `domain/market.py`, `domain/reducer.py`,
`research/history.py`, `research/replay.py`, `research/portfolio_replay.py`,
portfolio valuation/controls, metrics and the existing deterministic tests.
Current strict replay requires complete provenance, contiguous aligned history
and bounded samples. The new request permits separately labeled exploratory
full-history replay; it does not authorize weakening strict existing admission.

Missing research features are the exact no-seed and fresh-reference single-BUY
policies, automatic symbol/exchange TP precedence, full-history streaming under
explicit data-quality caveats, and a parity-tested C++ execution path. See
`MODULE1_RESEARCH_SPEC_v1.md` for the pre-return contract and traceability matrix.

Decision: one standalone C++20 executable under `cpp/backtest/`, using CMake and
CTest, bounded raw reads and deterministic exact financial arithmetic. Reuse
existing Python semantics through differential tests. No Python embedding,
RPC service, live database or exchange SDK is added. No whole-archive conversion.

Observed initial tools: GCC 13.3.0 and Python 3.12.3; CMake and uv are absent from
PATH. The retained `.venv` links SQLite 3.45.1, so it cannot establish the existing
patched-SQLite acceptance gates. Earlier `/tmp` test environments are gone.
Local tooling restoration and actual test results will be recorded in
`CPP_BACKTEST_VERIFICATION.md`; no earlier suite result is a pass for new code.

## Measured reference and restored tools

Local tooling now lives outside the repository at `/opt/grid-tools/`: uv 0.13.7,
CMake 3.31.6, GCC 13.3.0, and locked Python 3.12.16/3.13.18 environments linked
to the previously approved synthetic SQLite 3.53.1 build. No dependency lock,
production build approval or live startup guard was changed.

The existing Python Simulator processed the same 4,096 actual consecutive
1INCH/USDT bars three times in 0.569649, 0.574946 and 0.571675 seconds, with an
identical financial result hash. Its profile identified precision checks and
exact decimal rounding as the dominant measured cost; the reducer itself used
a smaller share. This supports a narrow compiled research path, not a rewrite
of the Python live core. The input is the common-start window, selected before
comparative returns. There is no global portfolio TP in this timing reference.

Receipts, raw profile, exact executed script and frozen v1 scenario are under
`/opt/grid-runs/module1-20261110/python-reference-profile/`. The current config
records later pre-return clarifications without relabeling that earlier run.
The C++ source scan is a different workload and must not be compared with these
strategy timings as a replay speedup.

Live domain/execution/exchange/ledger/operations behavior remains unchanged.
The owner explicitly authorized this offline C++ work despite the older ADR's
deferral. That exception does not enable a C++ live daemon or change any live gate.
