import base64
from copy import deepcopy
from hashlib import sha256
import json

from research_backend.app.db import one, transaction
from research_backend.app.storage import write_partition


URL = "/api/chart-snapshots"


def png_bytes():
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    image = (bytes.fromhex("89504e470d0a1a0a") + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
             + chunk(b"IDAT", zlib.compress(bytes(2))) + chunk(b"IEND", b""))
    return image


def test_snapshot_archive_pins_and_legacy_identity(client):
    image = png_bytes()
    request = dict(symbol="EURUSD", source="DUKASCOPY_SAVED", timeframe="M5", cutoff=600,
                   png=base64.b64encode(image).decode())
    legacy = client.post(URL, json=request).json()
    meta = dict(symbol="EURUSD", source="DUKASCOPY_SAVED", timeframe="M5", cutoff=600, signal_reference=None,
                width=1, height=1, authority="USER_CHART_CAPTURE")
    raw = json.dumps(meta, sort_keys=True, separators=(",", ":"))
    old_id = sha256(raw.encode() + image).hexdigest()
    assert legacy == {"id": old_id, **meta}

    lanes = []
    for role, symbol, close in [("primary", "EURUSD", 1.1), ("comparison", "GBPUSD", 1.3)]:
        bar = dict(time=300, open=close, high=close, low=close, close=close)
        part = write_partition("DUKASCOPY", "test-feed", symbol, "candles", "M5", "BID", [bar])
        lanes.append(dict(role=role, symbol=symbol, timeframe="M5", price_basis="BID", feed_id="test-feed",
                          bars=[bar], partitions=[{"id": part["id"], "sha256": part["sha256"]}]))
    saved = client.post(URL, json={**request, "lanes": lanes})
    assert saved.status_code == 201, saved.text
    assert client.get(URL + "/" + saved.json()["id"]).json() == saved.json()
    for field, value in [("feed_id", "other"), ("price_basis", "ASK"), ("partitions", lanes[0]["partitions"]),
                         ("partitions", [{"id": lanes[1]["partitions"][0]["id"], "sha256": "0"*64}]),
                         ("bars", [{**lanes[1]["bars"][0], "close": 1.1}])]:
        changed = deepcopy(lanes)
        changed[1][field] = value
        assert client.post(URL, json={**request, "lanes": changed}).status_code == 409
    assert client.post(URL, json={**request, "lanes": lanes, "cutoff": 599}).status_code == 409
    assert client.post(URL, json={**request, "lanes": lanes[::-1]}).status_code == 422
    assert client.post(URL, json={**request, "lanes": lanes}).json() == saved.json()
    assert one("SELECT COUNT(*) AS n FROM chart_snapshots")["n"] == 2
    assert client.get(URL + "/" + old_id).json() == legacy
    assert one("SELECT metadata,png FROM chart_snapshots WHERE id=?", (old_id,)) == {"metadata":raw,"png":image}

    # A pre-fix linked receipt could describe a later bar at an earlier cutoff.
    # Recreate its original serialization and keep the immutable row inspectable.
    ref = {"event_key": "a"*64, "status": "SMT_CONFIRMED"}
    provenance = {"timeframe": "M5"}
    for lane in lanes:
        provenance[lane["role"]] = {key: lane[key] for key in ("symbol","feed_id","price_basis","partitions")}
        provenance[lane["role"]]["source"] = request["source"]
    transition = {"status": ref["status"], "time": 450}
    original = {k:v for k,v in saved.json().items() if k != "id"}
    original.update(signal_reference=ref, cutoff=599)
    serialized = json.dumps(original, sort_keys=True, separators=(",", ":"))
    original_id = sha256(serialized.encode()+image).hexdigest()
    with transaction() as db:
        db.execute("INSERT INTO smt_event_history VALUES(?,?,?,?,?)",
                   (ref["event_key"], "scope", ref["status"], 450, json.dumps(
                       {"provenance":provenance, "transitions":[transition]})))
        db.execute("INSERT INTO chart_snapshots VALUES(?,?,?,?)", (original_id, serialized, image, "2026-11-13"))
    linked = {**request, "lanes":lanes, "signal_reference":ref}
    assert client.post(URL, json={**linked, "cutoff":599}).status_code == 409
    assert one("SELECT COUNT(*) AS n FROM chart_snapshots")["n"] == 3
    frozen = client.post(URL, json=linked)
    assert frozen.status_code == 201 and frozen.json()["chart_context"] == "FROZEN"
    retro = client.post(URL, json={**linked, "cutoff":None})
    assert retro.status_code == 201
    assert {k:retro.json()[k] for k in ("chart_context","cutoff","signal_transition_at","chart_evidence_through")} == {
        "chart_context":"RETROSPECTIVE", "cutoff":None, "signal_transition_at":450, "chart_evidence_through":600}
    assert client.get(URL + "/" + original_id).json() == {"id":original_id, **original}
    assert one("SELECT metadata,png FROM chart_snapshots WHERE id=?", (original_id,)) == {"metadata":serialized,"png":image}


def test_frozen_links_check_the_requested_transition_with_or_without_lanes(client):
    lanes = []
    provenance = {"timeframe":"M5"}
    for role, symbol, price in [("primary","EURUSD",1.1), ("comparison","GBPUSD",1.3)]:
        bar = dict(time=300, open=price, high=price, low=price, close=price)
        part = write_partition("DUKASCOPY","test-feed",symbol,"candles","M5","BID",[bar])
        lane = dict(symbol=symbol, partitions=[{"id":part["id"],"sha256":part["sha256"]}],
                    feed_id="test-feed", price_basis="BID")
        provenance[role] = {**lane, "source":"DUKASCOPY_SAVED"}
        lanes.append({**lane, "role":role, "timeframe":"M5", "bars":[bar]})
    request = dict(png=base64.b64encode(png_bytes()).decode(), symbol="EURUSD",
                   timeframe="M5", source="DUKASCOPY_SAVED")
    transitions = []
    for i,status in enumerate(("SMT_CANDIDATE","SMT_CONFIRMED","SMT_INVALIDATED","SMT_SOURCE_CORRECTED")):
        t = 700 + i*100
        transitions.append({"status":status, "time":t})
        ref = {"event_key":"d"*64, "status":status}
        payload = json.dumps({"provenance":provenance, "transitions":transitions})
        with transaction() as db:
            if status == "SMT_SOURCE_CORRECTED":
                db.execute("INSERT INTO smt_source_corrections VALUES(?,?,?,?)",(ref["event_key"],"scope",t,payload))
            else:
                db.execute("INSERT INTO smt_event_history VALUES(?,?,?,?,?)",(ref["event_key"],"scope",status,t,payload))
        for fields in ({}, {"lanes":lanes}):
            linked = {**request, **fields, "signal_reference":ref}
            before = one("SELECT COUNT(*) AS n FROM chart_snapshots")["n"]
            rejected = client.post(URL,json={**linked,"cutoff":t-1})
            assert (rejected.status_code,rejected.json()["detail"]) == (409,"Signal revision unavailable at chart cutoff")
            assert one("SELECT COUNT(*) AS n FROM chart_snapshots")["n"] == before
            saved = client.post(URL,json={**linked,"cutoff":t})
            assert saved.status_code == 201 and saved.json()["signal_transition_at"] == t
            assert client.post(URL,json={**linked,"cutoff":t}).json() == saved.json()
            assert one("SELECT COUNT(*) AS n FROM chart_snapshots")["n"] == before+1


def test_paired_decision_reopens_original_cutoff_and_rejects_other_candles(client):
    start = 1_577_836_800
    lanes = []
    for role, symbol, price in (("primary", "EURUSD", 1.1), ("comparison", "GBPUSD", 1.3)):
        bars = [dict(time=start+i*300, open=price, high=price, low=price, close=price) for i in range(3)]
        part = write_partition("DUKASCOPY", "decision-test", symbol, "candles", "M5", "BID", bars)
        lanes.append(dict(role=role, symbol=symbol, timeframe="M5", price_basis="BID", feed_id="decision-test",
                          bars=bars[:1], partitions=[{"id":part["id"], "sha256":part["sha256"]}]))
    context = dict(symbol="EURUSD", timeframe="M5", source="DUKASCOPY_SAVED")
    request = dict(**context, start=start, end=start+900, window_bars=5000,
                   views=[dict(view="comparison", symbol="GBPUSD", timeframe="M5")])
    for alignment in (0, 2):
        frozen = client.post("/api/replay/sessions", json={**request, "smt_rules":{"alignment_bars":alignment}}).json()
        assert frozen["request"]["smt_rules"]["alignment_bars"] == alignment
        ref = dict(session_id=frozen["id"], position=0, view="comparison", time=start)
        snapshot = dict(**context, cutoff=start+300, png=base64.b64encode(png_bytes()).decode(),
                        lanes=lanes, decision_replay=ref)
        saved = client.post(URL, json=snapshot)
        assert saved.status_code == 201, saved.text
        ident = saved.json()["id"]
        for bad in ({"decision_replay":{**ref,"time":start+300}}, {"decision_replay":{**ref,"position":2}},
                    {"lanes":lanes[:1]}, {"cutoff":start+301}):
            assert client.post(URL, json={**snapshot, **bad}).status_code == 409
        session_url = "/api/replay/sessions/" + frozen["id"]
        assert client.post(session_url+"/checkpoint", json={"position":2,"bookmarks":[]}).is_success
        assert client.get(session_url).json()["request"]["smt_rules"] == frozen["request"]["smt_rules"]
        assert client.get(URL+"/"+ident).json() == saved.json()
    assert one("SELECT COUNT(*) AS n FROM chart_snapshots")["n"] == 2
