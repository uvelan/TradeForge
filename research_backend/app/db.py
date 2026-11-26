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
CREATE TABLE IF NOT EXISTS smt_watch_checkpoint (
    generation INTEGER PRIMARY KEY, payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE TABLE IF NOT EXISTS smt_monitor_lease (
    id INTEGER PRIMARY KEY CHECK(id=1) REFERENCES smt_monitor(id) ON DELETE CASCADE,
    generation INTEGER NOT NULL,
    owner TEXT NOT NULL,
    expires_ns INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL CHECK(symbol IN ('EURUSD','GBPUSD','USDJPY','XAUUSD')),
    direction TEXT NOT NULL CHECK(direction IN ('BUY','SELL')),
    status TEXT NOT NULL CHECK(status IN ('PLANNED','REVIEWED','CANCELLED','CLOSED')) DEFAULT 'PLANNED',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_versions (
    strategy_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version > 0),
    version_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('DRAFT','READY_FOR_EVALUATION','ARCHIVED')),
    configuration_fingerprint TEXT NOT NULL,
    definition TEXT NOT NULL CHECK(json_valid(definition)),
    PRIMARY KEY(strategy_id,version)
);
CREATE TABLE IF NOT EXISTS strategy_evaluations (
    id TEXT PRIMARY KEY,
    strategy_version_id TEXT NOT NULL REFERENCES strategy_versions(version_id),
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_decisions (
    run_id TEXT NOT NULL REFERENCES strategy_evaluations(id),
    cutoff INTEGER NOT NULL CHECK(cutoff >= 0),
    id TEXT NOT NULL UNIQUE,
    previous_id TEXT REFERENCES strategy_decisions(id),
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id,cutoff)
);
CREATE TABLE IF NOT EXISTS month_replay_manifests (
    session TEXT PRIMARY KEY, month TEXT NOT NULL, binding TEXT NOT NULL CHECK(json_valid(binding))
);
CREATE TABLE IF NOT EXISTS month_replay_steps (
    session TEXT NOT NULL, idx INTEGER NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)),
    digest TEXT NOT NULL, PRIMARY KEY(session,idx)
);
CREATE TABLE IF NOT EXISTS statement_versions (
 id TEXT PRIMARY KEY, series TEXT NOT NULL, version INTEGER NOT NULL, previous TEXT REFERENCES statement_versions(id),
 raw BLOB NOT NULL, UNIQUE(series, version)
);
CREATE TABLE IF NOT EXISTS statement_progress (
 id TEXT PRIMARY KEY REFERENCES statement_versions(id), cursor INTEGER NOT NULL, state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS statement_traces (
 id TEXT REFERENCES statement_versions(id), ordinal INTEGER NOT NULL, row_id TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(id,ordinal), UNIQUE(id,row_id)
);
CREATE TABLE IF NOT EXISTS statement_reports (
 id TEXT PRIMARY KEY REFERENCES statement_versions(id), payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_versions (
    id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL REFERENCES trades(id),
    version INTEGER NOT NULL CHECK(version>0),
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    created_at TEXT NOT NULL,
    UNIQUE(trade_id,version)
);
CREATE INDEX IF NOT EXISTS plan_signal_link ON plan_versions(json_extract(payload,'$.signal_reference.event_key'));
CREATE TABLE IF NOT EXISTS decision_review_steps (
    trade_id TEXT NOT NULL, version INTEGER NOT NULL, step TEXT NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload)), created_at TEXT NOT NULL,
    PRIMARY KEY(trade_id,version,step),
    FOREIGN KEY(trade_id,version) REFERENCES plan_versions(trade_id,version)
);
CREATE TABLE IF NOT EXISTS plan_paper_events (
    trade_id TEXT NOT NULL, version INTEGER NOT NULL, sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 1024),
    payload TEXT NOT NULL CHECK(json_valid(payload)), PRIMARY KEY(trade_id,sequence),
    FOREIGN KEY(trade_id,version) REFERENCES plan_versions(trade_id,version)
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
    trade_id TEXT REFERENCES trades(id),
    plan_version INTEGER,
    knowledge_cutoff TEXT NOT NULL,
    location TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK ((trade_id IS NOT NULL AND plan_version IS NOT NULL)
        OR (trade_id IS NULL AND plan_version IS NULL))
);
CREATE TABLE IF NOT EXISTS recorded_package_sources (
    package_id TEXT PRIMARY KEY REFERENCES ai_packages(id),
    replay_session TEXT NOT NULL REFERENCES month_replay_manifests(session)
);
CREATE INDEX IF NOT EXISTS recorded_package_sessions ON recorded_package_sources(replay_session);
CREATE TABLE IF NOT EXISTS ai_reviews (
    id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL REFERENCES ai_packages(id),
    assessment TEXT NOT NULL CHECK(assessment IN ('SUPPORTED','CONDITIONAL','REJECTED','INSUFFICIENT_DATA')),
    response TEXT NOT NULL CHECK(json_valid(response)),
    verification TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(package_id, response)
);
CREATE TABLE IF NOT EXISTS ai_review_originals (
  review_id TEXT PRIMARY KEY REFERENCES ai_reviews(id),
  body BLOB NOT NULL,
  sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_review_integrity (
  review_id TEXT PRIMARY KEY REFERENCES ai_review_originals(review_id),
  status TEXT NOT NULL,
  duplicate_key TEXT,
  corrects_review_id TEXT REFERENCES ai_reviews(id)
);
CREATE TABLE IF NOT EXISTS ai_review_decisions (
    review_id TEXT NOT NULL REFERENCES ai_reviews(id),
    sequence INTEGER NOT NULL CHECK(sequence>0),
    decision TEXT NOT NULL CHECK(decision IN ('ACCEPTED','REJECTED','NEEDS_FOLLOW_UP')),
    rationale TEXT NOT NULL CHECK(length(rationale) BETWEEN 1 AND 2500),
    created_at TEXT NOT NULL,
    PRIMARY KEY(review_id, sequence)
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
CREATE TABLE IF NOT EXISTS recorded_strategy_checkpoints (
    session_id TEXT NOT NULL, cutoff_ms INTEGER NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)),
    payload_hash TEXT NOT NULL, PRIMARY KEY(session_id,cutoff_ms)
);
CREATE INDEX IF NOT EXISTS recorded_checkpoints_by_run ON recorded_strategy_checkpoints(json_extract(payload,'$.binding.run_id'));
CREATE TABLE IF NOT EXISTS historical_bi5_sources (
    sha256 TEXT PRIMARY KEY NOT NULL,
    payload BLOB NOT NULL CHECK(length(payload) BETWEEN 1 AND 40000000)
);
CREATE TABLE IF NOT EXISTS historical_imports (
    feed_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    source_start INTEGER NOT NULL,
    source_end INTEGER NOT NULL CHECK(source_end > source_start),
    source_sha256 TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    manifest TEXT NOT NULL CHECK(json_valid(manifest)),
    PRIMARY KEY(feed_id,symbol,source_start,source_end)
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
CREATE TABLE IF NOT EXISTS journal_signal_links (
    entry_id TEXT PRIMARY KEY REFERENCES journal_entries(id),
    event_key TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chart_drawing_versions (
    scope TEXT NOT NULL,
    revision INTEGER NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    created_at TEXT NOT NULL,
    PRIMARY KEY(scope,revision)
);
CREATE TABLE IF NOT EXISTS backtest_runs (
    id TEXT PRIMARY KEY,
    summary TEXT NOT NULL CHECK(json_valid(summary)),
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS backtest_time ON backtest_runs(created_at DESC,id);
CREATE TABLE IF NOT EXISTS replay_sessions (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    source TEXT NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replay_confluence_receipts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES replay_sessions(id),
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replay_checkpoints (
    session_id TEXT PRIMARY KEY REFERENCES replay_sessions(id),
    position INTEGER NOT NULL,
    bookmarks TEXT NOT NULL CHECK(json_valid(bookmarks)),
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS replay_context ON replay_sessions(symbol,timeframe,source,created_at DESC);
CREATE TABLE IF NOT EXISTS chart_snapshots (
    id TEXT PRIMARY KEY,
    metadata TEXT NOT NULL CHECK(json_valid(metadata)),
    png BLOB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshot_signal ON chart_snapshots(json_extract(metadata,'$.signal_reference.event_key'));
CREATE TABLE IF NOT EXISTS journal_entry_context (
    entry_id TEXT PRIMARY KEY REFERENCES journal_entries(id),
    payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE TABLE IF NOT EXISTS learning_attempts (
    lesson_id TEXT PRIMARY KEY,
    choice TEXT NOT NULL CHECK(choice IN ('confirmed','not_yet')),
    hypothesis TEXT NOT NULL CHECK(length(hypothesis) BETWEEN 10 AND 1000),
    journal_id TEXT NOT NULL UNIQUE REFERENCES journal_entries(id),
    submitted_at TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS arrival_sessions (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    policy TEXT NOT NULL,
    started_ms INTEGER NOT NULL,
    calendar TEXT NOT NULL CHECK(json_valid(calendar))
);
CREATE TABLE IF NOT EXISTS arrival_leases (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES arrival_sessions(id),
    expires_ms INTEGER NOT NULL,
    PRIMARY KEY(symbol,timeframe)
);
CREATE TABLE IF NOT EXISTS arrival_observations (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES arrival_sessions(id),
    provider TEXT NOT NULL,
    feed_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    price_basis TEXT NOT NULL,
    candle_open INTEGER NOT NULL,
    nominal_close INTEGER NOT NULL,
    observed_ms INTEGER NOT NULL,
    elapsed_ms INTEGER NOT NULL,
    content TEXT NOT NULL CHECK(json_valid(content)),
    fingerprint TEXT NOT NULL,
    previous_sequence INTEGER REFERENCES arrival_observations(sequence),
    recovery INTEGER NOT NULL CHECK(recovery IN (0,1))
);
CREATE INDEX IF NOT EXISTS arrival_scope ON arrival_observations(
    provider,feed_id,symbol,timeframe,price_basis,candle_open,sequence);
CREATE TABLE IF NOT EXISTS arrival_receipts (
    sequence INTEGER PRIMARY KEY REFERENCES arrival_observations(sequence),
    persisted_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS arrival_publications (
    sequence INTEGER PRIMARY KEY REFERENCES arrival_receipts(sequence),
    receipt_committed_by_ms INTEGER NOT NULL CHECK(receipt_committed_by_ms >= 0),
    witness_session_id TEXT NOT NULL REFERENCES arrival_sessions(id)
);
CREATE TABLE IF NOT EXISTS arrival_manifests (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE TABLE IF NOT EXISTS arrival_partitions (
    dataset_id TEXT PRIMARY KEY REFERENCES datasets(id),
    sha256 TEXT NOT NULL,
    policy TEXT NOT NULL,
    scope TEXT NOT NULL CHECK(json_valid(scope)),
    sessions TEXT NOT NULL CHECK(json_valid(sessions))
);
'''

IMMUTABLE_TABLES = ['ai_review_integrity','recorded_package_sources','statement_versions','statement_traces','statement_reports','month_replay_manifests','month_replay_steps','decision_review_steps','plan_paper_events','plan_versions', 'trade_events', 'ai_packages', 'ai_reviews', 'ai_review_originals', 'ai_review_decisions', 'imported_fills', 'journal_entries', 'smt_event_history', 'smt_source_corrections', 'smt_alerts', 'journal_signal_links', 'chart_drawing_versions', 'backtest_runs', 'replay_sessions', 'chart_snapshots', 'journal_entry_context']
IMMUTABLE_TABLES += ["replay_confluence_receipts", "learning_attempts", "arrival_sessions", "arrival_observations", "arrival_receipts",
                     "arrival_publications", "arrival_manifests", "arrival_partitions", "historical_imports", "historical_bi5_sources", "recorded_strategy_checkpoints", "strategy_versions", "strategy_evaluations", "strategy_decisions"]


def connect():
    db = sqlite3.connect(str(DATABASE), timeout=10, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA synchronous=FULL')
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


def upgrade_package_subject(db):
    # Rebuild only the package parent. Child FK definitions and IDs stay intact.
    if any(r["name"] == "trade_id" and not r["notnull"] for r in db.execute("PRAGMA table_info(ai_packages)")):
        return
    db.execute("PRAGMA foreign_keys=OFF")
    try:
        db.execute("BEGIN IMMEDIATE")
        if not any(r["name"] == "trade_id" and not r["notnull"] for r in db.execute("PRAGMA table_info(ai_packages)")):
            start = SCHEMA.index("CREATE TABLE IF NOT EXISTS ai_packages")
            ddl = SCHEMA[start:SCHEMA.index(";",start)]
            db.execute(ddl.replace("IF NOT EXISTS ai_packages", "ai_packages_new"))
            cols = "id,trade_id,plan_version,knowledge_cutoff,location,sha256,created_at"
            db.execute(f"INSERT INTO ai_packages_new({cols}) SELECT {cols} FROM ai_packages")
            db.execute("DROP TABLE ai_packages")
            db.execute("ALTER TABLE ai_packages_new RENAME TO ai_packages")
            for op in ("UPDATE", "DELETE"):
                db.execute(f"CREATE TRIGGER protect_ai_packages_{op.lower()} BEFORE {op} ON ai_packages "
                           "BEGIN SELECT RAISE(ABORT,'immutable record'); END")
            for table in ("ai_packages","ai_reviews"):
                if db.execute(f"PRAGMA foreign_key_check({table})").fetchone():
                    raise RuntimeError("Package migration found an invalid evidence reference")
        db.commit()
    finally:
        db.rollback()
        db.execute("PRAGMA foreign_keys=ON")


def initialize():
    with transaction() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.executescript(SCHEMA)
        upgrade_package_subject(db)
        from .historical_strategy_store import install
        install(db)
        for table in IMMUTABLE_TABLES:
            for op in ('UPDATE','DELETE'):
                name = f'protect_{table}_{op.lower()}'
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS {name}
                    BEFORE {op} ON {table} BEGIN
                    SELECT RAISE(ABORT,'immutable record'); END''')

        from .review_integrity import backfill
        backfill(db)


def rows(query: str, args: tuple=()) -> list[dict]:
    with transaction() as db:
        return [dict(r) for r in db.execute(query, args).fetchall()]


def one(query: str, args: tuple=()) -> dict | None:
    with transaction() as db:
        r = db.execute(query, args).fetchone()
        return dict(r) if r is not None else None
