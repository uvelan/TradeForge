"""Local acceptance with the original November EURUSD/GBPUSD partitions."""
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path


def stamp(hm):
    return int(datetime.fromisoformat("2026-11-13T"+hm).replace(tzinfo=UTC).timestamp())


def checked_revision(client, event, window, data):
    endpoint = "/api/indicators/signals/" + event["event_key"]
    params = {"status": event["status"]}
    revision = client.get(endpoint, params=params)
    candles = client.get(endpoint + "/candles", params=params)
    assert revision.is_success and candles.is_success, (revision.text, candles.text)
    assert revision.json() == event
    chart = candles.json()
    cutoff = event["chart_cutoff"]
    prefix = []
    for side, symbol in (("primary", "EURUSD"), ("comparison", "GBPUSD")):
        expected = [bar for bar in data[symbol]["bars"] if window["start"] <= bar["time"] < cutoff]
        assert [bar["time"] for bar in expected] == list(range(window["start"], cutoff, 300))
        assert chart[side] == expected, (symbol, event["status"], cutoff)
        assert event["provenance"][side]["partitions"] == [window[side]]
        assert event["provenance"][side]["dataset_id"] == window[side]["id"]
        assert event["provenance"][side]["sha256"] == window[side]["sha256"]
        prefix.append({"symbol": symbol, "bar_count": len(expected), "last_candle": expected[-1],
                       "ohlc_sha256": sha256(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()})
    proof = event["strategy_revision"]
    assert event["provenance"]["authority"] == "HISTORICAL_SIMULATION"
    assert proof["timing"]["availability_basis"] == "MODELED_NOMINAL_CLOSE_PLUS_DELAY"
    assert proof["state"] == event["status"]
    assert proof["available_at"] == cutoff == proof["timing"]["nominal_confirmation_time"]
    return {"status": proof["state"], "revision_id": proof["id"], "modeled_available_at": cutoff,
            "supporting_candles": prefix}, chart


def reopen_confirmation(client, confirmed, original_candles, pins):
    key = confirmed["event_key"]
    latest, = client.get("/api/indicators/signals", params={"q": key}).json()["items"]
    assert latest["status"] == "SMT_INVALIDATED"
    restored, = client.get("/api/indicators/signals", params={"q": key, "as_of": stamp("13:25")}).json()["items"]
    assert restored == confirmed
    frozen = client.post("/api/replay/sessions", json={
        "symbol": "EURUSD", "timeframe": "M5", "source": "DUKASCOPY_SAVED",
        "signal_reference": {"event_key": key, "status": "SMT_CONFIRMED"},
    })
    assert frozen.status_code == 201, frozen.text
    session = frozen.json()
    assert client.get("/api/replay/sessions/" + session["id"]).json() == session
    for side, pane in (("primary", session["market"]), ("comparison", session["views"]["comparison"])):
        assert pane["partitions"] == [pins[side]]
        assert pane["candles"] == original_candles[side]
    return {"session_id": session["id"], "event_key": key, "restored_revision_id": restored["strategy_revision"]["id"],
            "latest_status": latest["status"], "as_of": stamp("13:25")}


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
    if phase=="uninterrupted":
        configuration=routes.monitor_configuration()
        lease=leases.claim(configuration["enabled_at"])
        assert lease
        try:
            watch=archive.ArchiveWatch.model_validate(configuration["archive"])
            progress=archive.advance(watch,lease,through=stamp("13:25"))
        finally:
            leases.finish(lease)
        assert progress["verified_through"]==stamp("13:25")
        signals=client.get("/api/indicators/signals").json()["items"]
        original,=[e for e in signals if e["direction"]=="bearish" and e["primary"]["current"]["time"]==stamp("13:05")]
        assert original["status"]=="SMT_CONFIRMED"
        _, original_chart=checked_revision(client,original,window,data)
        unavailable=client.get("/api/indicators/signals/"+original["event_key"],params={"status":"SMT_INVALIDATED"})
        assert unavailable.status_code==404
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
    probes=[]
    revisions_by_state={}
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
            for side in pins:
                assert e[side]["previous"]["time"]==stamp("12:35")
                assert e[side]["current"]["time"]==stamp("13:05")
                assert e[side]["current"]["confirmed_at"]==stamp("13:20")
            assert e["leader"]=="primary" and e["rules"]["alignment_bars"]==0
            assert e["chart_cutoff"]==e["transitions"][-1]["time"]
            proof, chart=checked_revision(client,e,window,data)
            revisions_by_state[expected]=proof
            if minute=="13:25":
                confirmed, confirmed_candles=e, chart
        rows.append([minute,expected,matches[0]["strategy_revision"]["id"] if matches else None])
        probes.append({"as_of":stamp(minute), "utc":minute, "status":expected,
                       "revision_id":matches[0]["strategy_revision"]["id"] if matches else None})
    rewind=reopen_confirmation(client, confirmed, confirmed_candles, pins)
    if phase=="uninterrupted":
        assert original==confirmed and original_chart==confirmed_candles
        rewind["original_verified_through"]=progress["verified_through"]
        rewind["original_invalidated_revision_http_status"]=unavailable.status_code
    lifecycle={"date_utc":"2026-11-13","event_key":event_key,"feed":"dukascopy-web-m1-v1","timeframe":"M5",
               "price_basis":"BID","watch_start":window["start"],"verified_through":status["verified_through"],
               "availability_basis":"MODELED_NOMINAL_CLOSE_PLUS_DELAY","delay_seconds":0,"pins":pins,
               "rules":confirmed["rules"],"direction":confirmed["direction"],"leader":confirmed["leader"],
               "pivots":{side:confirmed[side] for side in pins},"probes":probes,
               "revisions":list(revisions_by_state.values()),"rewind":rewind}
    (root/"signal-lifecycle.json").write_text(json.dumps(lifecycle,indent=2)+"\n",encoding="utf-8")
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
                env={**os.environ,"TRADEFORGE_PAIR_STORAGE":tmp,
                     "TRADEFORGE_E2E_API_PORT":os.environ.get("TRADEFORGE_E2E_API_PORT","8557"),
                     "TRADEFORGE_E2E_UI_PORT":os.environ.get("TRADEFORGE_E2E_UI_PORT","4557"),
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
