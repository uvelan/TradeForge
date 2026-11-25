"""Local acceptance with the original November EURUSD/GBPUSD partitions."""
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path


def stamp(hm):
    return int(datetime.fromisoformat("2026-11-13T"+hm).replace(tzinfo=UTC).timestamp())


def run(phase):
    from fastapi.testclient import TestClient
    from research_backend.app import db, indicator_routes as routes
    from research_backend.app.main import app
    from scripts.u01_comparison_archive import reference

    db.initialize()
    client = TestClient(app)
    app.state.smt_monitor = {}
    root = Path(os.environ["TRADEFORGE_DATA_DIR"])
    data = reference(date(2026,11,13), root if phase in ("start","uninterrupted") else None)
    pins = {side:data[symbol]["partition"] for side,symbol in
            (("primary","EURUSD"),("comparison","GBPUSD"))}
    window = dict(**pins,start=stamp("12:15"),end=stamp("13:40"))
    # Direct Arrow reference, independent of the detector and journal.
    for symbol,prices in (("EURUSD",(1.10549,1.10557)),("GBPUSD",(1.32316,1.32306))):
        highs={b["time"]:b["high"] for b in data[symbol]["bars"]}
        for when,price in zip(("12:35","13:05"),prices):
            pivot=stamp(when)
            assert highs[pivot]==price and all(highs[pivot+i*300]<price for i in (-2,-1,1,2))
        if symbol=="GBPUSD":
            assert highs[stamp("13:20")]==1.32297<prices[0]<highs[stamp("13:25")]==1.32328
    if phase in ("start","uninterrupted"):
        r=client.post("/api/indicators/smt/monitor/archive",json=window)
        assert r.is_success,r.text
        routes.poll_monitor(app.state.smt_monitor)
        status=client.get("/api/indicators/smt/monitor").json()
        assert status["verified_through"]==stamp("12:55"),status
        if phase=="start":
            return
    from research_backend.app import archive_smt_watch as archive, smt_monitor_lease as leases
    if phase.startswith("crash"):
        from contextlib import contextmanager
        transaction=archive.transaction
        @contextmanager
        def interrupted():
            with transaction() as connection:
                yield connection
                if phase=="crash-before":
                    os._exit(75)
            os._exit(76)
        archive.transaction=interrupted
        routes.poll_monitor({})
        raise AssertionError("Fault did not intercept the watch transaction")
    if phase.startswith("recover"):
        expired=db.one("SELECT expires_ns FROM smt_monitor_lease")
        assert expired
        leases.time_ns=lambda:expired["expires_ns"]+1
        published=db.rows("SELECT * FROM smt_alerts")
        recovered=client.get("/api/indicators/smt/monitor").json()
        if phase=="recover-before":
            assert recovered["verified_through"]==stamp("12:55") and not published
        else:
            assert recovered["verified_through"]==stamp("13:35") and published
    while True:
        routes.poll_monitor(app.state.smt_monitor)
        status=client.get("/api/indicators/smt/monitor").json()
        assert not status.get("error"),status
        if status["status"]=="COMPLETE":
            break
    rows=[]
    event_key=None
    for minute,expected in [("13:19:59",None),("13:20","SMT_CANDIDATE"),("13:24:59","SMT_CANDIDATE"),
                            ("13:25","SMT_CONFIRMED"),("13:29:59","SMT_CONFIRMED"),("13:30","SMT_INVALIDATED")]:
        r=client.get("/api/indicators/signals",params={"as_of":stamp(minute)})
        assert r.is_success,r.text
        matches=[e for e in r.json()["items"] if e["direction"]=="bearish" and e["primary"]["current"]["time"]==stamp("13:05")]
        assert [e["status"] for e in matches]==([expected] if expected else []),(minute,matches,r.text[:1000])
        from types import SimpleNamespace
        from research_backend.app.strategy_definitions import IndicatorDependency
        from research_backend.app.strategy_evaluation_sources import collect
        watch=archive.ArchiveWatch(**window)
        feeds,_=archive.inputs(watch)
        dep=IndicatorDependency(id="pair",indicator="SMT",role="PAIR",algorithm_version=1,timeframe="M5",
                                settings=watch.model_dump(include={"period","alignment_bars","confirmation_bars","tolerance_bps"}))
        strategy=collect(SimpleNamespace(indicators=[dep]),{"series":{side.upper()+":M5":feed.model_dump() for side,feed in feeds.items()}},
                         stamp(minute),stamp("13:00"))
        events=strategy.events[strategy.views["pair"].snapshot].get("smt",((),()))[1]
        receipt_ids={r["id"] for event in db.rows("SELECT payload FROM smt_event_history")
                     for body in [json.loads(event["payload"])] if body["direction"]=="bearish" and body["primary"]["current"]["time"]==stamp("13:05")
                     for r in [body["strategy_revision"]]}
        visible=[v for v in events if v["id"] in receipt_ids]
        assert (visible[-1]["state"] if visible else None)==expected
        if matches:
            e,=matches
            event_key=e["event_key"]
            revisions=[v for v in events if v["id"]==e["strategy_revision"]["id"]]
            assert len(revisions)==1 and revisions[0]["state"]==expected
            assert [e[s]["previous"]["price"] for s in pins]==[1.10549,1.32316]
            assert [e[s]["current"]["price"] for s in pins]==[1.10557,1.32306]
            assert e["chart_cutoff"]==e["transitions"][-1]["time"]
            chart=client.get(f"/api/indicators/signals/{event_key}/candles",params={"status":expected})
            assert chart.is_success,chart.text
            for side in pins:
                assert max(b["time"]+300 for b in chart.json()[side])==e["chart_cutoff"]
                assert e["provenance"][side]["partitions"]==[pins[side]]
        rows.append([minute,expected,matches[0]["strategy_revision"]["id"] if matches else None])
    before=db.rows("SELECT * FROM smt_alerts ORDER BY event_key")
    routes.poll_monitor(app.state.smt_monitor)
    assert before==db.rows("SELECT * FROM smt_alerts ORDER BY event_key")
    assert sum(a["event_key"]==event_key for a in before)==1
    receipt=[(r["event_key"],r["status"],r["event_time"],json.loads(r["payload"])["strategy_revision"])
             for r in db.rows("SELECT * FROM smt_event_history ORDER BY event_key,event_time")]
    (root/"receipt.json").write_text(json.dumps(receipt,sort_keys=True))
    alert,=[json.loads(a["payload"]) for a in before if a["event_key"]==event_key]
    assert alert["confirmed_at"]==stamp("13:25") and isinstance(alert["watch_published_at"],int)
    print(json.dumps(dict(event_key=event_key,verified_through=status["verified_through"],pins=pins,
                          checks=rows,alerts_for_family=1,poll_observed_at=alert["alert_recorded_at"],watch_published_at=alert["watch_published_at"]),indent=2))


if __name__=="__main__":
    if sys.argv[1:]==["--mt5-smoke"]:
        import MetaTrader5 as mt5
        if not mt5.initialize(timeout=3000):
            raise SystemExit("MT5 UNVERIFIED: terminal unavailable")
        try:
            for symbol in ("EURUSD","GBPUSD"):
                rates=mt5.copy_rates_from_pos(symbol,mt5.TIMEFRAME_M5,1,6)
                if rates is None or len(rates)!=6:
                    raise SystemExit(f"MT5 UNVERIFIED: incomplete M5 sample for {symbol}")
                print(symbol,len(rates),"M5 bars; last terminal-reported open",int(rates[-1]["time"]))
        finally:
            mt5.shutdown()
    elif sys.argv[1:]==["--browser"]:
        import shutil
        from scripts.u01_comparison_archive import ROOT
        with tempfile.TemporaryDirectory(prefix="tradeforge-e2e-") as tmp:
            for reopen in ("0","1"):
                env={**os.environ,"TRADEFORGE_PAIR_STORAGE":tmp,"TRADEFORGE_E2E_API_PORT":"8557","TRADEFORGE_E2E_UI_PORT":"4557",
                     "TRADEFORGE_COMPARISON_DAY":"2026-11-13","TRADEFORGE_PAIR_REOPEN":reopen,"TRADEFORGE_SMT_WATCH":"1"}
                subprocess.run([shutil.which("npm.cmd") or "npm","run","e2e","--","--config","playwright.comparison.config.ts","--retries=0"],
                               cwd=ROOT/"frontend",env=env,check=True)
    elif len(sys.argv)>1:
        run(sys.argv[1])
    else:
        receipts=[]
        for fault in ("uninterrupted","","before","after"):
            with tempfile.TemporaryDirectory() as tmp:
                env={**os.environ,"TRADEFORGE_DATA_DIR":tmp}
                def child(phase,code=0):
                    result=subprocess.run([sys.executable,"-m","scripts.verify_archive_smt_watch",phase],env=env)
                    assert result.returncode==code,(phase,result.returncode)
                if fault=="uninterrupted":
                    child("uninterrupted")
                    receipts.append((Path(tmp)/"receipt.json").read_bytes())
                    continue
                child("start")
                if fault:
                    child("crash-"+fault,75 if fault=="before" else 76)
                child("recover-"+fault if fault else "restart")
                receipts.append((Path(tmp)/"receipt.json").read_bytes())
        assert all(receipt==receipts[0] for receipt in receipts)
        print("Crash before COMMIT, crash after COMMIT, and restart matched all original revision proofs.")
