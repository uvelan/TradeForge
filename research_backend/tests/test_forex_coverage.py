"""Controlled partitions. Genuine file counts are checked by the Windows acceptance runner."""
from datetime import UTC, datetime
import lzma
import struct

from research_backend.app import config, db, storage
from research_backend.app.historical_import import FEED, import_bi5
from scripts import month_archive

DAY = datetime(2025,11,13,tzinfo=UTC)
START = int(DAY.timestamp())


def window(client, start="2025-11-13", end="2025-11-14"):
    response = client.get("/api/forex/coverage",params={"start":start,"end":end})
    assert response.status_code == 200, response.text
    return response.json()["hours"]


def candles(symbol, times, *, feed=FEED, price=1.2, delay=0):
    bars = [dict(time=t, open=price,high=price,low=price,close=price,volume=1,
                 available_at=t+300+delay) for t in times]
    return storage.write_partition("DUKASCOPY",feed,symbol,"candles","M5","BID",bars)


def bi5(milliseconds):
    return lzma.compress(b"".join(struct.pack(">IIIff",ms,120002,120000,1,1) for ms in milliseconds))


def test_actual_slots_overlaps_and_exclusive_end(client):
    slots = list(range(START,START+3600,300))
    candles("EURUSD",slots)
    candles("EURUSD",slots[2:5])
    candles("GBPUSD",slots[1:-1])
    candles("EURUSD",[START+86400])
    hour = window(client)[0]
    assert (hour["expected"],hour["eur"]["count"],hour["gbp"]["count"],hour["paired"]) == (12,12,10,10)
    assert hour["gbp"]["missing"] == [[START,slots[1]],[slots[-1],START+3600]]
    assert hour["paired_ranges"] == [[slots[1],slots[-1]]]
    assert hour["quotes"]["gaps"] == [[START*1000,(START+3600)*1000]]
    assert len(hour["eur"]["pins"]) == 2 and hour["status"] == "Partial"
    assert sum(h["eur"]["count"] for h in window(client)) == 12
    candles("EURUSD",slots[2:4],price=1.21)
    conflict = window(client)[0]
    assert conflict["status"]=="Conflicting evidence" and not conflict["paired_ranges"]
    assert "Conflicting candles" in conflict["eur"]["error"]


def test_different_feeds_and_late_final_child(client):
    candles("EURUSD",[START+3300],delay=20)
    candles("GBPUSD",[START+3300])
    hour = window(client)[0]
    assert hour["paired"]==1 and hour["paired_ranges"]==[]
    candles("EURUSD",[START+3600],feed="second")
    candles("GBPUSD",[START+3600])
    different = window(client)[1]
    assert different["paired"]==0 and different["pair_error"] and different["paired_ranges"]==[]


def test_recorded_gaps_asymmetric_and_closure(client):
    import_bi5(bi5([0,60000,120001,300000,300001]),"EURUSD",DAY,0)
    candles("GBPUSD",[START])
    hour = window(client)[0]
    assert hour["eur"]["count"]==2 and hour["paired"]==1 and hour["quotes"]["count"]==5
    assert hour["gbp"]["missing"][0][0] == START+300
    assert hour["quotes"]["gaps"] == [[START*1000+60001,START*1000+120001],
        [START*1000+120002,START*1000+300000],[START*1000+300002,(START+3600)*1000]]
    assert hour["paired_ranges"]==[[START,START+300]]
    assert hour["quotes"]["pins"][0]["feed"]==FEED
    weekend = window(client,"2025-11-15","2025-11-16")
    assert all(h["status"]=="Scheduled closure" and h["expected"]==0 and not h["quotes"]["gaps"] for h in weekend)


def test_retry_resume_cache_cooldown_and_conflict(client,tmp_path,monkeypatch):
    monkeypatch.setattr(config,"STORAGE_ROOT",tmp_path)
    folder=tmp_path/"t06-input"/"2025-11-13"
    folder.mkdir(parents=True)
    (folder/"00h_ticks.bi5").write_bytes(bi5([1,100]))
    calls=[]
    def unavailable(day,name):
        calls.append(name)
        return b"",{"http":429,"curl_exit":22,"retry_after_seconds":180}
    monkeypatch.setattr(month_archive,"download",unavailable)
    def retry(h):
        result=client.post("/api/forex/retry",params={"hour":START+h*3600})
        assert result.status_code==200,result.text
        return result.json()
    assert retry(0)["status"]=="Imported cached hour"
    pins=window(client)[0]["quotes"]["pins"]
    assert retry(1)["status"]=="PROVIDER_COOLDOWN" and retry(1)["status"]=="PROVIDER_COOLDOWN_ACTIVE"
    assert len(calls)==1
    # A new request can resume with a manually supplied hour, even while the network is cooling down.
    (folder/"01h_ticks.bi5").write_bytes(bi5([1,100]))
    assert retry(1)["status"]=="Imported cached hour"
    for _ in range(2):
        assert retry(0)["status"]==retry(1)["status"]=="Already registered"
    assert len(calls)==1 and window(client)[0]["quotes"]["pins"]==pins
    assert len(db.rows("SELECT * FROM historical_imports"))==2
    files={"file":("00h_ticks.bi5",bi5([42]),"application/octet-stream")}
    response=client.post("/api/market/import/bi5?symbol=EURUSD&day=2025-11-13&hour=0",files=files)
    assert response.status_code>=400 and window(client)[0]["quotes"]["pins"]==pins

    (folder/"00h_ticks.bi5").write_bytes(bi5([42]))
    conflicting=client.post("/api/forex/retry",params={"hour":START})
    assert conflicting.status_code==409 and "checksum mismatch" in conflicting.text
    assert window(client)[0]["quotes"]["pins"]==pins


def test_manual_hour_binding_and_healthy_hour(client,monkeypatch):
    from hashlib import sha256
    from research_backend.app import forex_coverage
    raw=bi5([*range(0,3600000,60000),3599999])
    def submit(symbol,name):
        return client.post("/api/forex/bi5",params={"symbol":symbol,"hour":START},
                           files={"file":(name,raw,"application/octet-stream")})
    assert submit("EURUSD","01h_ticks.bi5").status_code==422
    monkeypatch.setitem(forex_coverage.BI5_HOURS,sha256(raw).hexdigest(),0)
    assert submit("GBPUSD","00h_ticks.bi5").status_code==409
    first=submit("EURUSD","00h_ticks.bi5")
    assert first.status_code==200 and submit("EURUSD","00h_ticks.bi5").json()==first.json()
    candles("GBPUSD",range(START,START+3600,300))
    hour=window(client)[0]
    assert hour["status"]=="Healthy" and hour["paired"]==12 and hour["quotes"]["gaps"]==[]


def test_coverage_freeze_checks_the_inspected_feed_and_both_pin_sets(client):
    original = {symbol:candles(symbol,[START,START+300]) for symbol in ("EURUSD","GBPUSD")}
    hour = window(client)[0]
    binding, = hour["chart_selections"]
    request = dict(start=START,end=START+600,source="DUKASCOPY_SAVED",symbol="EURUSD",timeframe="M5",
                   views=[dict(view="comparison",symbol="GBPUSD",timeframe="M5")],window_bars=12)
    candles("EURUSD",[START,START+300],feed="independent-alternative",price=1.47)
    # A generic chart still rejects mixing the two sources.
    assert client.post("/api/replay/sessions",json=request).status_code==409
    request["coverage_selection"]=binding
    session = client.post("/api/replay/sessions",json=request)
    assert session.status_code==201,session.text
    record = session.json()
    for symbol,pane in (("EURUSD",record["market"]),("GBPUSD",record["views"]["comparison"])):
        expected = [{k:original[symbol][k] for k in ("id","sha256")}]
        preview = client.get("/api/forex/chart",params=dict(symbol=symbol,start=START,end=START+600,
                            feed_id=FEED,pin=binding[symbol]))
        assert preview.status_code==200
        assert pane["partitions"]==preview.json()["partitions"]==expected
        assert [bar["close"] for bar in pane["candles"]]==[1.2,1.2]
    # Same price, same feed, additional provenance on the comparison lane.
    candles("GBPUSD",[START])
    stale = client.post("/api/replay/sessions",json=request)
    assert stale.status_code==409 and "GBPUSD chart evidence changed" in stale.text
    assert len(db.rows("SELECT id FROM replay_sessions"))==1
    assert client.get("/api/replay/sessions/"+record["id"]).json()==record
