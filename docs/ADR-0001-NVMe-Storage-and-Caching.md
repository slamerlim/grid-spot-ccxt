ADR-0001 — Embedded Storage, Caching, and Resource Ownership
Decision: Accepted for v1 design, subject to on-hardware benchmarks.
Date: 2026-11-03
Applies to: GRID Spot CCXT PRD v2.0
Primary motive: One consumer, local NVMe, correctness-first trading, large read-only research archive.
Decision in one line
SQLite WAL for money and orders; immutable validated .bin files for source history; optional Parquet/Arrow for derived research; bounded RAM/OS page cache for derived values; one live writer and one long-lived CCXT client per venue/account.
Workload	Data authority	Persistence	Expiration / invalidation
Submitted intent, reservation, fill, position, cycle, config	SQLite transactional ledger	Required, local NVMe	Never TTL-expire; correct with new records
Exchange order status, balances	External venue, reconciled into SQLite	Durable observed snapshot with provenance	Refetch after gap/restart and periodically
CCXT market precision, min size, fee tier	Venue API/CCXT mapping	Cached snapshot and revision	TTL + immediate invalidate on rejection/rule change
Top-of-book/depth, eligibility score	Public API snapshot	Small RAM LRU only	Freshness threshold and config/market generation
Private WS order messages	Venue event feed	Durable normalized events / fills	Dedup and reconcile; stream cache not authority
Legacy 1-minute candles	Original immutable files	Preserve raw bytes, checksums and manifest	Immutable per data version
Validated research segments/replay	Manifest-bound validated data	Parquet/Arrow/raw mmap only if useful	Immutable, replace via new version
Backtest results	Reproducible output + manifest/config hash	Parquet/JSON, SQLite index optional	Never overwrite a published result without version


Why this is not a custom binary database
The user's requested efficient NVMe layout is achieved without building a bespoke financial database. A hand-rolled append-only file with no transactional indexes would have to solve atomic multi-record commits, uniqueness, locking, rollback, order/cash reservations, crash recovery and migrations. All of these are correctness requirements. SQLite is itself a compact binary database on disk and uses no background server; the network latency to exchanges dominates the live order workflow.

No PostgreSQL, Redis, RocksDB, Kafka, cloud queue, dedicated ledger service, per-symbol DB, or distributed lock service in v1. Revisit with an ADR only when measurement or multi-host requirements force it.
Exact live database contract
- Require a patched SQLite runtime: >=3.51.3 (excluding any withdrawn/unapproved build) or explicitly verified 3.50.7 / 3.44.6 fixed backport. Check the linked sqlite3 library that Python actually loads, not only python --version. Abort live start if not confirmed.
- Set and verify PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON; plus bounded busy_timeout. Store state on a real local filesystem, not NFS, SMB or replicated object storage. Keep writer transactions short and free of network I/O.
- Route writes through one async serialized DB writer (single process). Group one fill + fee posting + inventory adjustment + reservation change + journal entry + state transition atomically. Use unique constraints on trade and intent identities and version-checked state transitions.
- Persist an order intent and reservation before the network call, commit it, then send. On an unknown outcome, stop and reconcile. An SQLite transaction cannot encompass an exchange API side effect.
- One live controller guarded by an OS process lock and run identity; a second process is read-only / refuses to submit. A stale crashed controller never authorizes assumptions about live order state: startup always reconciles.
- Reader processes may query committed records, but never write trading state independently. Avoid long-running read transactions blocking checkpoints. Monitor WAL size/checkpoint lag and disk-full conditions; fail closed on IO, integrity or constraint failure.
- Use SQLite's safe online backup interface (or controlled shutdown copy), consistent WAL backup and tested restore plus integrity_check. A backup without a restore test is not a readiness pass. Avoid writing credentials to SQLite; secret references belong in protected environment/config paths.
Exact cache contract
Every cache has an owner, size bound, key, source revision, populated-at timestamp, last-known-good status, stale flag, TTL/freshness policy, and invalidation triggers. No cache serves as permission to spend or sell. Relevant keys always distinguish exchange/account/market/quote currency where applicable.
- CCXT: Reuse long-lived instances, built-in market cache, rate limiter and subscriptions. An exchange client per symbol is a bug. On upgraded ccxt version, reconcile behavior and rerun capabilities before live use.
- Metadata: one in-flight refresh per (venue, market set); concurrent callers await same future. On InvalidOrder, precision/min-notional rejection, delisting or account fee update, invalidate relevant generation and block trading until verified.
- Books: Prefer rate-limited REST snapshots for candidates and order creation. Cache active symbols only, record venue timestamp when available and local monotonic receipt time; reject stale or incomplete depth. There is no mandatory 24×7 public-price WS. A valuation refresh may read a quote without causing strategy progression.
- WS: Separate private message receiving from durable posting; streams may send updates or sliding cached results. Bounded event queues must never silently drop potentially new fills. If saturated, pause new order creation, preserve/ingest on disk if possible and force full REST reconciliation; if safe ingress cannot be maintained, enter degraded fail-closed state.
- Rankings: Cache on a config/market/fee/book generation; hysteresis and minimum residency suppress symbol-selection churn. A pin bypasses ranking only, never compliance or risk.
- Research: Keep raw/validated segments immutable; use the OS page cache and mmap for raw; bounded decompression and worker pools. Avoid caching an entire archive or all permutations of parameter sweeps in Python RAM.
Concurrency and cancellation
Serialize commands at allocation level; expose one order-command gateway process-wide. Portfolio exit increases global risk generation and invalidates ordinary pending buys. Reconcile unknown submissions and cancellations before replacement. Order event handler execution order is not exchange execution order. Consume idempotent normalized fills; do not rely solely on WebSocket callback sequencing. Mark the active SELL slot as reserved/possibly-live until terminal status is proved. Do not deadlock on multi-venue reconciliation: no lock may be held across remote network waits.

Queue priorities: first preserve own private order/fill stream and risk; next reconcile and execute previously authorized exits; next normal order actions; last market discovery and backtest-related work. Fixed concurrency/queue bounds are configuration tested on real hardware, not invented global values. On overflow, pause new exposure, do not accelerate polling or spin tasks. Cancellation, reconnect and shutdown must be structured and awaited; a stopped async task must not leave orphan private subscriptions.
Resource and benchmark plan
Benchmark on the deployment NVMe, recording machine, kernel, Python, CCXT, SQLite, CPU/RAM, storage mount, configuration and input checksum:
1. Cold-ish and warm sequential scan of representative 24-byte raw candle files (where cold-cache measurement is permitted, disclose any inability to evict OS cache).
2. UTC slicing, random symbol/month reads and chronological 100/1,000-symbol merge in raw mmap, Arrow IPC and Parquet.
3. Repeated parameter replays that reuse validated immutable segments without re-reading the raw archive unnecessarily.
4. sqlite order/fill/reservation transaction throughput, latency, concurrent reads, WAL growth, backup, forced-kill recovery and restore.
5. Synthetic 1/7 venue with 10/100/1,000 candidate symbols: CCXT client count, API requests by endpoint, WS subscription count, cache hit ratio, RSS/CPU, live-event queue lag and reconnection pressure.
6. Offline backtest under constrained CPU/RSS/IO concurrently with a synthetic live fill storm; verify the trading engine remains responsive and safe.
Selection rule: Keep original raw files forever unless user explicitly requests deletion; start with raw mmap + checksum manifests. Add Parquet only for research queries demonstrated to benefit; add Arrow/packed replay shards only if measured end-to-end replay improves enough to justify extra copies. Choose by end-to-end wall time and correctness, not compressed-byte count alone. A benchmark must not block the correctness-focused I0–I4 development path.
Triggers for a future ADR
- Postgres: active writers on multiple hosts / high availability needed.
- RocksDB: persistent very high-volume KV updates measured as an SQLite bottleneck.
- C++/Rust kernel: profiling isolates CPU-bound validated replay as the significant bottleneck after simpler vectorization fixes.
- Continuous book WS: need for reliable target-venue microstructure capture and proven resource budget.
ADR acceptance checks
- Transactional fill posting produces the same ledger after replay, duplicates and injected crash at every boundary.
- An unpatched SQLite, missing FULL synchronous, corrupt DB, full disk or unusable backup refuses live startup.
- Concurrent cache misses generate one verified refresh, not duplicated rate-limited calls.
- Stale snapshots cannot authorize any limit order; a reconnect requires reconciliation before resumed placements.
- Benchmark shows byte-exact valid decoded records and reproducible accounting independent of file format.
External references
- https://www.sqlite.org/wal.html — includes March 2026 WAL-reset issue and fixed versions.
- https://github.com/ccxt/ccxt/wiki/Manual — exchange instance reuse, limits and precision.
- https://github.com/ccxt/ccxt/wiki/ccxt.pro.manual — free bundled WS module, caches and private streams.
- https://arrow.apache.org/docs/python/ipc.html
- https://parquet.apache.org/docs/overview/