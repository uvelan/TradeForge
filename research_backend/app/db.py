"""Small, explicit SQLite repository with per-operation connections.

Use UTC ISO 8601 with Z. Enforced immutable event/version tables.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3
from .config import DATABASE


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS smt_event_history (
    event_key TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('SMT_CANDIDATE','SMT_CONFIRMED','SMT_INVALIDATED')),
    event_time INTEGER NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    PRIMARY KEY(event_key, status)
);
CREATE INDEX IF NOT EXISTS smt_history_time ON smt_event_history(event_time DESC);
CREATE INDEX IF NOT EXISTS smt_history_scope ON smt_event_history(scope_key, event_time);
CREATE TABLE IF NOT EXISTS smt_source_corrections (
    event_key TEXT PRIMARY KEY,
    scope_key TEXT NOT NULL,
    event_time INTEGER NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE TABLE IF NOT EXISTS smt_alerts (
    event_key TEXT PRIMARY KEY,
    scope_key TEXT NOT NULL,
    event_time INTEGER NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE INDEX IF NOT EXISTS smt_alerts_scope_time ON smt_alerts(scope_key, event_time DESC);
CREATE TABLE IF NOT EXISTS smt_monitor (
    id INTEGER PRIMARY KEY CHECK(id=1),
    configuration TEXT NOT NULL CHECK(json_valid(configuration)),
    enabled_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL CHECK(symbol IN ('EURUSD','GBPUSD','USDJPY','XAUUSD')),
    direction TEXT NOT NULL CHECK(direction IN ('BUY','SELL')),
    status TEXT NOT NULL CHECK(status IN ('PLANNED','REVIEWED','CANCELLED','CLOSED')) DEFAULT 'PLANNED',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_versions (
    id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL REFERENCES trades(id),
    version INTEGER NOT NULL CHECK(version>0),
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    created_at TEXT NOT NULL,
    UNIQUE(trade_id,version)
);
CREATE TABLE IF NOT EXISTS trade_events (
    id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL REFERENCES trades(id),
    sequence INTEGER NOT NULL CHECK(sequence>0),
    kind TEXT NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    at TEXT NOT NULL,
    UNIQUE(trade_id,sequence)
);
CREATE TABLE IF NOT EXISTS ai_packages (
    id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL REFERENCES trades(id),
    plan_version INTEGER NOT NULL,
    knowledge_cutoff TEXT NOT NULL,
    location TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_reviews (
    id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL REFERENCES ai_packages(id),
    assessment TEXT NOT NULL CHECK(assessment IN ('SUPPORTED','CONDITIONAL','REJECTED','INSUFFICIENT_DATA')),
    response TEXT NOT NULL CHECK(json_valid(response)),
    verification TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(package_id, response)
);
CREATE TABLE IF NOT EXISTS trade_outcomes (
    trade_id TEXT PRIMARY KEY REFERENCES trades(id),
    pnl_usd TEXT NOT NULL,
    result_r TEXT NOT NULL,
    notes TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK(provider IN ('MT5','DUKASCOPY')),
    feed_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    timeframe TEXT,
    price_basis TEXT NOT NULL,
    rows_count INTEGER NOT NULL,
    first_time TEXT NOT NULL,
    last_time TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imported_fills (
    id TEXT PRIMARY KEY,
    broker_key TEXT NOT NULL,
    deal_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    imported_at TEXT NOT NULL,
    UNIQUE(broker_key,deal_id)
);
CREATE TABLE IF NOT EXISTS journal_entries (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('T','Q','CHART','RM','M','R')),
    sequence INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(kind,sequence)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_timeline ON trade_events(trade_id,sequence);
CREATE INDEX IF NOT EXISTS idx_plans_trade ON plan_versions(trade_id,version DESC);
CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_datasets_lookup ON datasets(provider,symbol,kind,timeframe);
CREATE INDEX IF NOT EXISTS idx_fills_symbol ON imported_fills(symbol);
CREATE INDEX IF NOT EXISTS idx_journal_time ON journal_entries(created_at DESC);
'''

IMMUTABLE_TABLES = ['plan_versions', 'trade_events', 'ai_packages', 'ai_reviews', 'imported_fills', 'journal_entries', 'smt_event_history', 'smt_source_corrections', 'smt_alerts']


def connect():
    db = sqlite3.connect(str(DATABASE), timeout=10, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=10000')
    return db


@contextmanager
def transaction():
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize():
    with transaction() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.executescript(SCHEMA)
        for table in IMMUTABLE_TABLES:
            for op in ('UPDATE','DELETE'):
                name = f'protect_{table}_{op.lower()}'
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS {name}
                    BEFORE {op} ON {table} BEGIN
                    SELECT RAISE(ABORT,'immutable record'); END''')


def rows(query: str, args: tuple=()) -> list[dict]:
    with transaction() as db:
        return [dict(r) for r in db.execute(query, args).fetchall()]


def one(query: str, args: tuple=()) -> dict | None:
    with transaction() as db:
        r = db.execute(query, args).fetchone()
        return dict(r) if r is not None else None
