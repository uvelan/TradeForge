"""Isolated durable storage for the offline paper ledger."""
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from .paper_ledger import PaperLedger, PaperOrder, PaperQuote, PaperSide

_APPLICATION_ID = 0x54504650


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class PaperRepository:
    """One dedicated SQLite file, bound to an immutable opening policy."""

    def __init__(self, path: Path, opening: PaperLedger):
        if opening.events:
            raise ValueError("Paper repository requires an empty opening ledger")
        self.path = path
        self.opening = opening
        self._opening = _canonical(asdict(opening))

    def _run(self, action=None):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            identity, = db.execute("PRAGMA application_id").fetchone()
            version, = db.execute("PRAGMA user_version").fetchone()
            if identity == 0 and version == 0 and not db.execute(
                "SELECT 1 FROM sqlite_schema LIMIT 1"
            ).fetchone():
                db.execute(f"PRAGMA application_id = {_APPLICATION_ID:d}")
                db.execute("PRAGMA user_version = 1")
                db.execute("CREATE TABLE paper_policy (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
                db.execute("CREATE TABLE paper_events (sequence INTEGER PRIMARY KEY, order_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)")
                db.execute("INSERT INTO paper_policy VALUES (1,?)", (self._opening,))
                db.execute("""CREATE TRIGGER paper_event_no_update BEFORE UPDATE ON paper_events
                    BEGIN SELECT RAISE(ABORT,'Paper events are immutable'); END""")
                db.execute("""CREATE TRIGGER paper_event_no_delete BEFORE DELETE ON paper_events
                    BEGIN SELECT RAISE(ABORT,'Paper events are immutable'); END""")
            elif (identity, version) != (_APPLICATION_ID, 1):
                raise ValueError("Not a supported paper database")
            if db.execute("SELECT payload FROM paper_policy WHERE id=1").fetchone() != (self._opening,):
                raise ValueError("Paper database opening policy differs")
            ledger = self.opening
            for seq, key, payload in db.execute(
                "SELECT sequence, order_id, payload FROM paper_events ORDER BY sequence LIMIT 1025"
            ):
                raw = json.loads(payload)
                intent = raw["order"]
                order = PaperOrder(**(intent | {"side": PaperSide(intent["side"])}))
                time = raw["decision_at_ms"]
                if raw["quote"] is None:
                    restored, calculated = ledger.cancel(order, decision_at_ms=time)
                else:
                    restored, calculated = ledger.apply(order, PaperQuote(**raw["quote"]), decision_at_ms=time)
                if (seq != len(ledger.events) + 1 or len(restored.events) != seq
                        or order.id != key or _canonical(asdict(calculated)) != payload):
                    raise ValueError("Paper event replay mismatch")
                ledger = restored
            if action is None:
                return ledger
            updated, event = action(ledger)
            if len(updated.events) != len(ledger.events):
                db.execute("INSERT INTO paper_events VALUES (?,?,?)",
                           (len(updated.events), event.order.id, _canonical(asdict(event))))
            return event

    def read(self):
        return self._run()

    def apply(self, order, quote, *, decision_at_ms):
        return self._run(lambda ledger: ledger.apply(order, quote, decision_at_ms=decision_at_ms))

    def cancel(self, order, *, decision_at_ms):
        return self._run(lambda ledger: ledger.cancel(order, decision_at_ms=decision_at_ms))
