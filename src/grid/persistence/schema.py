"""Append-only migration definition; checksums bind the exact SQL and schema shape."""

import hashlib

VERSION = 4
STATEMENTS = (
    "CREATE TABLE migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL) STRICT",
    "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT",
    """CREATE TABLE runs (
        id TEXT PRIMARY KEY, closed INTEGER NOT NULL CHECK(closed IN (0,1))) STRICT""",
    """CREATE TABLE pools (
        venue TEXT NOT NULL, account TEXT NOT NULL, currency TEXT NOT NULL,
        amount TEXT NOT NULL, PRIMARY KEY(venue,account,currency)) STRICT""",
    """CREATE TABLE allocations (
        id TEXT PRIMARY KEY, scope TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
        context TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>=1),
        recovery INTEGER NOT NULL CHECK(recovery IN (0,1))) STRICT""",
    """CREATE TABLE requests (
        id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL) STRICT""",
    """CREATE TABLE events (
        seq INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE REFERENCES requests(id),
        allocation TEXT REFERENCES allocations(id), payload TEXT NOT NULL,
        accepted INTEGER NOT NULL CHECK(accepted IN (0,1))) STRICT""",
    """CREATE TABLE orders (
        allocation TEXT NOT NULL REFERENCES allocations(id), id INTEGER NOT NULL,
        side TEXT NOT NULL CHECK(side IN ('buy','sell')),
        unresolved INTEGER NOT NULL CHECK(unresolved IN (0,1)), payload TEXT NOT NULL,
        PRIMARY KEY(allocation,id)) STRICT""",
    """CREATE UNIQUE INDEX one_unresolved_sell ON orders(allocation)
        WHERE side='sell' AND unresolved=1""",
    """CREATE TABLE reservations (
        allocation TEXT NOT NULL, order_id INTEGER NOT NULL, currency TEXT NOT NULL,
        principal TEXT NOT NULL, fee TEXT NOT NULL,
        PRIMARY KEY(allocation,order_id,currency),
        FOREIGN KEY(allocation,order_id) REFERENCES orders(allocation,id)) STRICT""",
    """CREATE TABLE fills (
        allocation TEXT NOT NULL, trade_id TEXT NOT NULL, order_id INTEGER NOT NULL,
        payload TEXT NOT NULL, PRIMARY KEY(allocation,trade_id),
        FOREIGN KEY(allocation,order_id) REFERENCES orders(allocation,id)) STRICT""",
    """CREATE TABLE fees (
        allocation TEXT NOT NULL, trade_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
        currency TEXT NOT NULL, amount TEXT NOT NULL, quote_value TEXT,
        PRIMARY KEY(allocation,trade_id,ordinal),
        FOREIGN KEY(allocation,trade_id) REFERENCES fills(allocation,trade_id)) STRICT""",
    "CREATE INDEX fills_by_order ON fills(allocation,order_id,trade_id)",
    """CREATE TABLE balances (
        allocation TEXT NOT NULL REFERENCES allocations(id), currency TEXT NOT NULL,
        amount TEXT NOT NULL, PRIMARY KEY(allocation,currency)) STRICT""",
    """CREATE TABLE positions (
        allocation TEXT PRIMARY KEY REFERENCES allocations(id),
        quantity TEXT NOT NULL, cost TEXT NOT NULL, realized TEXT NOT NULL) STRICT""",
    """CREATE TABLE journal (
        request_id TEXT NOT NULL REFERENCES requests(id), account_key TEXT NOT NULL,
        currency TEXT NOT NULL, kind TEXT NOT NULL, delta TEXT NOT NULL,
        PRIMARY KEY(request_id,account_key,currency,kind)) STRICT""",
    """CREATE TABLE intents (
        id TEXT PRIMARY KEY, allocation TEXT NOT NULL REFERENCES allocations(id),
        order_id INTEGER, kind TEXT NOT NULL CHECK(kind IN ('place','cancel','reconcile')),
        payload TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('queued','uncertain','resolved')),
        attempt_run TEXT REFERENCES runs(id),
        FOREIGN KEY(allocation,order_id) REFERENCES orders(allocation,id)) STRICT""",
    "CREATE INDEX journal_by_account ON journal(account_key,currency,kind)",
    "CREATE INDEX unresolved_intents ON intents(allocation,id) WHERE status!='resolved'",
    """CREATE TABLE checkpoints (
        allocation TEXT PRIMARY KEY REFERENCES allocations(id), version INTEGER NOT NULL,
        watermark INTEGER NOT NULL, snapshot_hash TEXT NOT NULL) STRICT""",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()
# Never change the original migration or its checksum.
IDENTITY_STATEMENTS = (
    """CREATE TABLE order_bindings (
        allocation TEXT NOT NULL, order_id INTEGER NOT NULL,
        venue TEXT NOT NULL, account TEXT NOT NULL, symbol TEXT NOT NULL,
        external_id TEXT NOT NULL, client_id TEXT NOT NULL,
        PRIMARY KEY(allocation,order_id), UNIQUE(venue,account,symbol,external_id),
        UNIQUE(venue,account,client_id),
        FOREIGN KEY(allocation,order_id) REFERENCES orders(allocation,id)) STRICT""",
    """CREATE TABLE trade_keys (
        venue TEXT NOT NULL, account TEXT NOT NULL, symbol TEXT NOT NULL,
        external_id TEXT NOT NULL, allocation TEXT NOT NULL, trade_id TEXT NOT NULL,
        PRIMARY KEY(venue,account,symbol,external_id), UNIQUE(allocation,trade_id),
        FOREIGN KEY(allocation,trade_id) REFERENCES fills(allocation,trade_id)) STRICT""",
)
UNIVERSE_STATEMENTS = (
    """CREATE TABLE universe_revisions (
        revision INTEGER PRIMARY KEY CHECK(revision>=1),
        request_id TEXT NOT NULL UNIQUE REFERENCES requests(id), payload TEXT NOT NULL) STRICT""",
)
PORTFOLIO_STATEMENTS = (
    """CREATE TABLE portfolio_revisions (
        revision INTEGER PRIMARY KEY CHECK(revision>=1),
        cycle_id TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE REFERENCES requests(id),
        payload TEXT NOT NULL) STRICT""",
    """CREATE TABLE portfolio_flows (
        cycle_id TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY(cycle_id,id)) STRICT""",
)
MIGRATIONS = (
    (1, CHECKSUM, STATEMENTS),
    (2, hashlib.sha256("\n".join(IDENTITY_STATEMENTS).encode()).hexdigest(), IDENTITY_STATEMENTS),
    (3, hashlib.sha256("\n".join(UNIVERSE_STATEMENTS).encode()).hexdigest(), UNIVERSE_STATEMENTS),
    (4, hashlib.sha256("\n".join(PORTFOLIO_STATEMENTS).encode()).hexdigest(), PORTFOLIO_STATEMENTS),
)
SEMANTIC_QUERIES = (
    ("pools", "SELECT * FROM pools ORDER BY venue,account,currency"),
    ("allocations", "SELECT * FROM allocations ORDER BY id"),
    ("requests", "SELECT * FROM requests ORDER BY id"),
    ("events", "SELECT * FROM events ORDER BY seq"),
    ("orders", "SELECT * FROM orders ORDER BY allocation,id"),
    ("reservations", "SELECT * FROM reservations ORDER BY allocation,order_id,currency"),
    ("fills", "SELECT * FROM fills ORDER BY allocation,trade_id"),
    ("fees", "SELECT * FROM fees ORDER BY allocation,trade_id,ordinal"),
    ("balances", "SELECT * FROM balances ORDER BY allocation,currency"),
    ("positions", "SELECT * FROM positions ORDER BY allocation"),
    ("journal", "SELECT * FROM journal ORDER BY request_id,account_key,currency,kind"),
    ("intents", "SELECT * FROM intents ORDER BY id"),
    ("checkpoints", "SELECT * FROM checkpoints ORDER BY allocation"),
    ("order_bindings", "SELECT * FROM order_bindings ORDER BY allocation,order_id"),
    ("trade_keys", "SELECT * FROM trade_keys ORDER BY venue,account,symbol,external_id"),
    ("universe_revisions", "SELECT * FROM universe_revisions ORDER BY revision"),
    ("portfolio_revisions", "SELECT * FROM portfolio_revisions ORDER BY revision"),
    ("portfolio_flows", "SELECT * FROM portfolio_flows ORDER BY cycle_id,id"),
)
