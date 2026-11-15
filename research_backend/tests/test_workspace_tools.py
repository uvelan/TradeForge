from datetime import UTC, datetime

from research_backend.app.storage import write_partition
from research_backend.tests.test_tick_replay import inputs


def test_saved_drawings_are_scoped_versioned_and_conflict_checked(client):
    params={"scope":"DUKASCOPY_SAVED:EURUSD:M5"}
    path="/api/drawings"
    initial=client.get(path,params=params).json()
    assert initial=={"revision":0,"schema_version":1,"items":[]}
    drawing={"id":"1"*36,"kind":"trend","anchors":[{"time":300,"price":"1.10000"},
             {"time":600,"price":"1.10200"}],"label":"Trend","color":"#52dfb5","created_at":10,"updated_at":10}
    snapshot={**initial,"items":[drawing]}
    written=client.post(path,params=params,json=snapshot)
    assert written.status_code==200,written.text
    assert written.json()["items"][0]["anchors"]==drawing["anchors"]
    assert client.post(path,params=params,json=snapshot).status_code==409
    assert client.get(path,params={"scope":"DUKASCOPY_SAVED:GBPUSD:M5"}).json()["items"]==[]
    assert client.post(path,params=params,json={**written.json(),"items":[drawing,drawing]}).status_code==422


def test_replay_manifest_keeps_completed_peer_and_checkpoints(client,monkeypatch):
    from research_backend.app import main

    now=int(datetime.now(UTC).timestamp())//300*300
    def market(symbol,timeframe,source,**kwargs):
        step=main.FRAME_MIN[timeframe]*60
        bars=[{"time":now-step,"open":10,"high":12,"low":9,"close":11},
              {"time":now,"open":11,"high":12,"low":10,"close":11}]
        return {"candles":bars,"source":"MT5","feed_id":"broker","synthetic":False,"latest_unconfirmed":True,"price_basis":"BID"}

    monkeypatch.setattr(main,"market_candles",market)
    url="/api/replay/sessions"
    created=client.post(url,json={"symbol":"EURUSD","timeframe":"M5","source":"MT5",
                                   "views":[{"view":"2","symbol":"GBPUSD","timeframe":"H1"}]})
    assert created.status_code==201,created.text
    saved=created.json()
    assert saved["market"]["candles"][0]["time"]==now-300
    assert len(saved["market"]["candles"])==1
    assert saved["views"]["2"]["candles"][0]["available_at"]==now
    session=url+"/"+saved["id"]
    assert client.post(session+"/checkpoint",json={"position":1}).status_code==422
    assert client.post(session+"/checkpoint",json={"position":0,"bookmarks":[0,0]}).json()["bookmarks"]==[0]
    assert client.get(session).json()["checkpoint"]["position"]==0
    assert client.get(url,params={"symbol":"EURUSD","timeframe":"M5","source":"MT5"}).json()[0]["id"]==saved["id"]


def test_saved_run_quote_path_stops_at_exact_source_order(client):
    candles,ticks=inputs()
    candles[3].update(high=106,low=98)
    for tick in ticks:tick["time"]=tick["time_ms"]//1000
    write_partition("DUKASCOPY","test","EURUSD","candles","M1","BID",candles)
    quote=write_partition("DUKASCOPY","test","EURUSD","ticks",None,"BID_ASK_UNITS",ticks)
    path="/api/backtest/runs"
    payload={"source":"DUKASCOPY_SAVED","timeframe":"M1","tick_dataset_id":quote["id"]}
    saved=client.post(path,json=payload)
    assert saved.status_code==201,saved.text
    snapshot=saved.json()
    assert client.post(path,json=payload).json()["id"]==snapshot["id"]
    base=f"{path}/{snapshot['id']}"
    assert client.get(base).json()["market"]["candles"]==snapshot["market"]["candles"]
    response=client.get(base+"/trades/0/quotes").json()
    assert [row["source_order"] for row in response["items"]]==[1,2]
    assert response["total"]==2


def test_archive_tick_run_merges_and_pins_verified_partitions(client):
    candles, ticks = inputs()
    candles[3].update(high=106, low=98)
    for tick in ticks:
        tick["time"] = tick["time_ms"] // 1000
    write_partition("DUKASCOPY", "test", "EURUSD", "candles", "M1", "BID", candles)
    inputs_by_partition = [(ticks[:2], 0, 181_000), (ticks[1:], 180_000, 1_000_000)]
    shards = []
    for index, (quotes, start, end) in enumerate(inputs_by_partition):
        records = [{**quote, "source_sha256": str(index) * 64, "source_window_start_ms": start,
                    "source_window_end_ms": end, "ask_volume": 1, "bid_volume": 1} for quote in quotes]
        shards.append(write_partition("DUKASCOPY", "test", "EURUSD", "ticks", None, "BID_ASK_UNITS", records))
    write_partition("DUKASCOPY", "different-feed", "EURUSD", "ticks", None, "BID_ASK_UNITS", ticks)
    path = "/api/backtest/runs"
    payload = {"source": "DUKASCOPY_SAVED", "timeframe": "M1", "tick_archive_range": True}
    saved = client.post(path, json=payload)
    assert saved.status_code == 201, saved.text
    snapshot = saved.json()
    assert snapshot["result"]["lineage"]["tick_partitions"] == [
        {"id": part["id"], "sha256": part["sha256"]} for part in shards
    ]
    assert snapshot["result"]["trades"][0]["exit_reason"] == "TARGET"
    url = f"{path}/{snapshot['id']}/trades/0/quotes"
    page = client.get(url, params={"limit": 1}).json()
    second = client.get(url, params={"limit": 1, "offset": page["next_offset"]}).json()
    assert [(row["time_ms"], row["source_order"]) for row in page["items"] + second["items"]] == [
        (180_000, 0), (181_000, 0)
    ]
    assert page["total"] == 2 and second["next_offset"] is None
    assert client.get(url, params={"limit": 1}).json() == page

    conflicting = {**ticks[1], "ask_units": 103, "source_sha256": "f" * 64,
                   "source_window_start_ms": 180_000, "source_window_end_ms": 181_000,
                   "ask_volume": 1, "bid_volume": 1}
    write_partition("DUKASCOPY", "test", "EURUSD", "ticks", None, "BID_ASK_UNITS", [conflicting])
    rejected = client.post(path, json=payload)
    assert rejected.status_code == 422
    assert "Conflicting tick quotes" in rejected.json()["detail"]
    assert client.get(url, params={"limit": 1}).json() == page

    from research_backend.app.db import one
    from research_backend.app.storage import partition_path
    partition = partition_path(one("SELECT * FROM datasets WHERE id=?", (shards[0]["id"],)))
    with partition.open("ab") as stored:
        stored.write(b"corruption")
    assert client.get(url).status_code == 409


def test_plan_revisions_check_previous_version_and_no_trade_finalizes(client,plan):
    initial=client.post("/api/trades",json=plan).json()
    url="/api/trades/"+initial["id"]
    revision=client.post(url+"/revisions?expected_version=1",json={**plan,"strategy":"Revised breakout"})
    assert revision.status_code==200,revision.text
    assert revision.json()["versions"][0]["version"]==2
    assert client.post(url+"/revisions?expected_version=1",json=plan).status_code==409
    status=client.post(url+"/decline",json={"reason":"Spread exceeded planned limit"})
    assert status.status_code==200,status.text
    assert status.json()["events"][-1]["kind"]=="NO_TRADE"
    assert client.post(url+"/revisions?expected_version=2",json=plan).status_code==409


def test_chart_capture_links_to_plan_exports_and_journal_revisions(client,plan):
    import io
    import zipfile
    from base64 import b64encode
    from struct import pack
    from zlib import compress, crc32

    def chunk(kind,data):
        return pack(">I",len(data))+kind+data+pack(">I",crc32(kind+data))
    raw=(bytes.fromhex("89504e470d0a1a0a")+chunk(b"IHDR",pack(">IIBBBBB",1,1,8,0,0,0,0))
         +chunk(b"IDAT",compress(bytes([0,0])))+chunk(b"IEND",b""))
    png=b64encode(raw).decode()
    corrupt=raw[:42]+bytes([raw[42]^1])+raw[43:]
    rejected=client.post("/api/chart-snapshots",json={"png":b64encode(corrupt).decode(),"symbol":"EURUSD","source":"DEMO","timeframe":"M5"})
    assert rejected.status_code==422
    capture=client.post("/api/chart-snapshots",json={"png":png,"symbol":"EURUSD","source":"DEMO","timeframe":"M5"})
    assert capture.status_code==201,capture.text
    assert capture.json()["authority"]=="USER_CHART_CAPTURE"
    snapshot_id=capture.json()["id"]
    image=client.get(f"/api/chart-snapshots/{snapshot_id}/image")
    assert image.headers["content-type"]=="image/png"
    created=client.post("/api/trades",json={**plan,"snapshot_id":snapshot_id})
    assert created.status_code==201,created.text
    trade_id=created.json()["id"]
    assert client.post("/api/trades",json={**plan,"timeframe":"M1","snapshot_id":snapshot_id}).status_code==422
    package=client.post(f"/api/ai/exports/{trade_id}").json()["package_id"]
    response=client.get(f"/api/ai/packages/{package}/download")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("data/chart.png")==image.content
    journal={"kind":"CHART","title":"Observed session","content":"Review and retain evidence.","tags":["London","signal"],
             "snapshot_id":snapshot_id,"plan_reference":{"trade_id":trade_id,"version":1}}
    first=client.post("/api/journal",json=journal).json()
    followup={**journal,"title":"Corrected observation","supersedes":first["id"]}
    revision=client.post("/api/journal",json=followup)
    assert revision.status_code==201,revision.text
    assert client.get("/api/journal/entries/"+first["id"]).json()["title"]=="Observed session"
    assert client.post("/api/journal",json=followup).status_code==409
    assert client.get("/api/workspace/search",params={"q":"Corrected observation"}).json()["items"][0]["type"]=="journal"
