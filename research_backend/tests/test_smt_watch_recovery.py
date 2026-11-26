"""Compare overlapped catch-up against one uninterrupted broker observation."""
import json
from datetime import datetime

from research_backend.app import db, indicator_routes as routes
from test_smt_authority import LEFT, RIGHT, RULES, candles


def test_incomplete_pair_then_overlap_and_restart(tmp_path,monkeypatch):
    start=1_700_000_040
    clock=[start+600]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return cls.fromtimestamp(clock[0],tz)
    monkeypatch.setattr(routes,"datetime",Clock)
    monkeypatch.setattr(routes,"time_ns",lambda:start*10**9)
    receipts=[]
    for interrupted in (False,True):
        monkeypatch.setattr(db,"DATABASE",tmp_path/f"{interrupted}.sqlite")
        db.initialize()
        routes.configure_monitor(routes.LiveSmtWatch(**RULES,primary_symbol="EURUSD",comparison_symbol="GBPUSD"))
        missing=[interrupted]
        def fetch(symbol,timeframe,limit,*,include_latest,provenance):
            provenance.update(feed_id="controlled-bid",price_basis="BID")
            bars=candles(LEFT if symbol=="EURUSD" else RIGHT,start)
            if missing[0] and symbol=="GBPUSD":
                del bars[8]
            return bars
        monkeypatch.setattr(routes,"mt5_rates",fetch)
        def checkpoint():
            return json.loads(db.one("SELECT payload FROM smt_watch_checkpoint")["payload"])
        routes.poll_monitor({})
        if interrupted:
            assert checkpoint()["gap"]=={"open_time":start+480,"symbols":["GBPUSD"]}
            assert checkpoint()["verified_through"]==start+480
            assert db.rows("SELECT * FROM smt_alerts")==[]
            routes.poll_monitor({})
            assert checkpoint()["verified_through"]==start+480
            missing[0]=False
        routes.poll_monitor({})
        assert checkpoint()["verified_through"]==start+600 and checkpoint()["gap"] is None
        routes.poll_monitor({})
        events=db.rows("SELECT * FROM smt_event_history ORDER BY event_time")
        assert [r["status"] for r in events]==["SMT_CANDIDATE","SMT_CONFIRMED"]
        assert len(db.rows("SELECT * FROM smt_alerts"))==1
        evidence=[json.loads(row["payload"]) for row in events]
        for event in evidence:
            event.pop("watch_published_at")
        receipts.append(evidence)
    assert receipts[0]==receipts[1]
