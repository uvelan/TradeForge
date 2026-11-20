"""Immutable historical research ledger and fenced worker state."""
import json
from time import time

from .analysis_evidence import ResourceLimit, canonical
from .db import one, transaction, utc_now
from .historical_strategy_inputs import LEASE_SECONDS, MAX_LEDGER_BYTES, MAX_QUEUE, MAX_SAVED_RUNS

SCHEMA = """
CREATE TABLE IF NOT EXISTS historical_strategy_runs (
    id TEXT PRIMARY KEY,
    strategy_version_id TEXT NOT NULL REFERENCES strategy_versions(version_id),
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS historical_worker_state (
    run_id TEXT PRIMARY KEY REFERENCES historical_strategy_runs(id),
    state TEXT NOT NULL CHECK(state IN ('QUEUED','RUNNING','CANCELLING','CANCELLED','COMPLETED','FAILED','RESOURCE_LIMIT_EXCEEDED')),
    owner TEXT,
    lease_until REAL,
    frontier INTEGER,
    ledger_bytes INTEGER NOT NULL DEFAULT 0,
    metrics TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metrics)),
    diagnostic TEXT
);
CREATE TABLE IF NOT EXISTS historical_strategy_checkpoints (
    run_id TEXT NOT NULL REFERENCES historical_strategy_runs(id),
    frontier INTEGER NOT NULL,
    digest TEXT NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    PRIMARY KEY(run_id,frontier)
);
CREATE TABLE IF NOT EXISTS historical_strategy_decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES historical_strategy_runs(id),
    cutoff INTEGER NOT NULL,
    attempt_id TEXT,
    state TEXT NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    UNIQUE(run_id,cutoff)
);
CREATE TABLE IF NOT EXISTS historical_strategy_attempts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES historical_strategy_runs(id),
    decision_id TEXT NOT NULL UNIQUE REFERENCES historical_strategy_decisions(id),
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS historical_strategy_intents (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES historical_strategy_runs(id),
    decision_id TEXT NOT NULL UNIQUE REFERENCES historical_strategy_decisions(id),
    attempt_id TEXT NOT NULL UNIQUE REFERENCES historical_strategy_attempts(id),
    payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE INDEX IF NOT EXISTS historical_attempt_run ON historical_strategy_attempts(run_id,id);
CREATE INDEX IF NOT EXISTS historical_intent_run ON historical_strategy_intents(run_id,id);
"""
IMMUTABLE = ("historical_strategy_runs","historical_strategy_checkpoints","historical_strategy_decisions",
             "historical_strategy_attempts","historical_strategy_intents")


class LeaseLost(Exception):
    """An operation arrived after cancellation, expiry or ownership change."""


def install(db):
    db.executescript(SCHEMA)
    for table in IMMUTABLE:
        for op in ("UPDATE","DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS protect_{table}_{op.lower()}
                BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable record'); END""")


def enqueue(run):
    run_id = run["run_id"]
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM historical_strategy_runs WHERE id=?",(run_id,)).fetchone():
            return run_id
        check_queue(db)
        if db.execute("SELECT COUNT(*) FROM historical_strategy_runs").fetchone()[0]>=MAX_SAVED_RUNS:
            raise ResourceLimit("Historical run retention limit reached")
        db.execute("INSERT INTO historical_strategy_runs VALUES (?,?,?,?)",
                   (run_id,run["identity"]["strategy_version_id"],canonical(run),utc_now()))
        db.execute("INSERT INTO historical_worker_state(run_id,state) VALUES (?,'QUEUED')",(run_id,))
    return run_id


def check_queue(db):
    if db.execute("SELECT COUNT(*) FROM historical_worker_state WHERE state='QUEUED'").fetchone()[0]>=MAX_QUEUE:
        raise ResourceLimit("Historical queue is full")


def claim(owner):
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        now = time()
        db.execute("""UPDATE historical_worker_state SET state=CASE state WHEN 'CANCELLING' THEN 'CANCELLED' ELSE 'FAILED' END,
                    owner=NULL,lease_until=NULL,diagnostic='Worker lease expired'
                    WHERE state IN ('RUNNING','CANCELLING') AND lease_until<=?""",(now,))
        if db.execute("SELECT 1 FROM historical_worker_state WHERE state IN ('RUNNING','CANCELLING') LIMIT 1").fetchone():
            return None
        item = db.execute("""SELECT s.run_id FROM historical_worker_state s JOIN historical_strategy_runs r ON r.id=s.run_id
                             WHERE s.state='QUEUED' ORDER BY r.created_at,s.run_id LIMIT 1""").fetchone()
        if not item:
            return None
        db.execute("UPDATE historical_worker_state SET state='RUNNING',owner=?,lease_until=? WHERE run_id=?",
                   (owner,now+LEASE_SECONDS,item["run_id"]))
        return item["run_id"]


def status(run_id):
    record = one("SELECT * FROM historical_worker_state WHERE run_id=?",(run_id,))
    if record:
        record["metrics"] = json.loads(record["metrics"])
    return record


def heartbeat(run_id, owner):
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        now = time()
        return db.execute("""UPDATE historical_worker_state SET lease_until=? WHERE run_id=?
                             AND owner=? AND state='RUNNING' AND lease_until>?""",
                          (now+LEASE_SECONDS,run_id,owner,now)).rowcount==1


def finish(run_id, owner, outcome, diagnostic):
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""UPDATE historical_worker_state SET state=CASE state WHEN 'CANCELLING' THEN 'CANCELLED' ELSE ? END,
                    owner=NULL,lease_until=NULL,diagnostic=? WHERE run_id=? AND owner=?
                    AND state IN ('RUNNING','CANCELLING') AND lease_until>?""",
                   (outcome,str(diagnostic)[:512],run_id,owner,time()))


def cancel(run_id):
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        now = time()
        return db.execute("""UPDATE historical_worker_state
                             SET state=CASE WHEN state='RUNNING' AND lease_until>? THEN 'CANCELLING' ELSE 'CANCELLED' END,
                                 owner=CASE WHEN state='RUNNING' AND lease_until>? THEN owner END,
                                 lease_until=CASE WHEN state='RUNNING' AND lease_until>? THEN lease_until END
                             WHERE run_id=? AND state IN ('QUEUED','RUNNING')""",(now,now,now,run_id)).rowcount==1


def resume(run_id):
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        check_queue(db)
        return db.execute("""UPDATE historical_worker_state SET state='QUEUED',owner=NULL,lease_until=NULL,diagnostic=NULL
                             WHERE run_id=? AND state IN ('FAILED','CANCELLED')""",(run_id,)).rowcount==1


def latest_checkpoint(run_id):
    return one("SELECT * FROM historical_strategy_checkpoints WHERE run_id=? ORDER BY frontier DESC LIMIT 1",(run_id,))


def publish(run_id, owner, snapshot, frontier, entries, completed, metrics):
    payload,digest = snapshot
    encoded = [(entry,canonical(entry["trace"]),canonical(entry["intent"]) if entry["intent"] else None)
               for entry in entries]
    size = len(payload.encode())+sum(len(trace.encode())+(len(intent.encode()) if intent else 0)
                                     for _,trace,intent in encoded)
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        lease = db.execute("SELECT * FROM historical_worker_state WHERE run_id=?",(run_id,)).fetchone()
        if (not lease or lease["state"]!="RUNNING" or lease["owner"]!=owner or lease["lease_until"]<=time()):
            raise LeaseLost()
        previous = lease["frontier"]
        if previous is not None and frontier<=previous:
            prior = db.execute("SELECT digest FROM historical_strategy_checkpoints WHERE run_id=? AND frontier=?",
                               (run_id,frontier)).fetchone()
            if prior and prior["digest"]==digest:
                return
            raise ValueError("Conflicting historical checkpoint")
        if size+lease["ledger_bytes"]>MAX_LEDGER_BYTES:
            raise ResourceLimit("Historical run ledger exceeds its byte budget")
        for entry,trace_json,intent_json in encoded:
            trace = entry["trace"]
            cursor = trace["cutoff"]
            if cursor>frontier or previous is not None and cursor<=previous:
                raise ValueError("Decision is outside its committed checkpoint interval")
            db.execute("INSERT INTO historical_strategy_decisions VALUES (?,?,?,?,?,?)",
                       (entry["id"],run_id,cursor,trace["attempt_id"],trace["state"],trace_json))
            if trace["attempt_id"]:
                db.execute("INSERT INTO historical_strategy_attempts VALUES (?,?,?,?)",
                           (trace["attempt_id"],run_id,entry["id"],trace["historical_attempt_state"]))
            if intent_json:
                db.execute("INSERT INTO historical_strategy_intents VALUES (?,?,?,?,?)",
                           (entry["intent"]["id"],run_id,entry["id"],trace["attempt_id"],intent_json))
        db.execute("INSERT INTO historical_strategy_checkpoints VALUES (?,?,?,?)",(run_id,frontier,digest,payload))
        if lease["lease_until"]<=time():
            raise LeaseLost()
        db.execute("""UPDATE historical_worker_state SET state=?,frontier=?,ledger_bytes=ledger_bytes+?,metrics=?,
                          owner=CASE WHEN ? THEN NULL ELSE owner END,
                          lease_until=CASE WHEN ? THEN NULL ELSE lease_until END
                      WHERE run_id=?""",("COMPLETED" if completed else "RUNNING",frontier,size,canonical(metrics),
                                         completed,completed,run_id))
