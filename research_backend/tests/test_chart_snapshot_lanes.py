import base64
from copy import deepcopy
from hashlib import sha256
import json

import pytest

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


def _decision_window(client, count, peer_status):
    start = 1_765_152_000
    context = dict(symbol="EURUSD", timeframe="M5", source="DUKASCOPY_SAVED")
    lanes = []
    for role, symbol, price in (("primary", "EURUSD", 1.1), ("comparison", "GBPUSD", 1.3)):
        if role == "comparison" and peer_status == "SOURCE_ERROR":
            continue
        bars = [dict(time=start+i*300, open=price, high=price, low=price, close=price) for i in range(count)]
        if peer_status == "NO_VISIBLE_OBSERVATIONS":
            bars[0]["available_at"] = start + (599 if role == "primary" else 600)
        part = write_partition("DUKASCOPY", "decision-window", symbol, "candles", "M5", "BID", bars)
        lanes.append(dict(role=role, symbol=symbol, timeframe="M5", feed_id="decision-window", price_basis="BID",
                          partitions=[{"id":part["id"],"sha256":part["sha256"]}], bars=bars))
    response = client.post("/api/replay/sessions", json=dict(**context, start=start, end=start+count*300,
        window_bars=5000, views=[dict(view="comparison",symbol="GBPUSD",timeframe="M5")]))
    assert response.status_code == 201, response.text
    session = response.json()
    capture = dict(**context, png=base64.b64encode(png_bytes()).decode(),
                   decision_replay=dict(session_id=session["id"],position=0,view="primary",time=start))
    return start, session, capture, lanes


@pytest.mark.parametrize("status", ["SOURCE_ERROR", "NO_VISIBLE_OBSERVATIONS"])
def test_decision_capture_without_visible_comparison(client, status):
    start, session, capture, lanes = _decision_window(client, 3, status)
    cutoff = session["market"]["replay_cutoffs"][0]
    primary = {**lanes[0], "bars":lanes[0]["bars"][:1]}
    # Same primary-only upload as the original public API reproduction.
    for evidence in ({"lanes":[primary]}, {"replay_lanes":["primary"]}):
        response = client.post(URL, json={**capture, **evidence, "cutoff":cutoff})
        assert response.status_code == 201, response.text
        shot = response.json()
        assert shot["lanes"][0]["bar_times"] == [start]
        assert shot["comparison_gap"]["status"] == status
        assert shot["comparison_gap"]["symbol"] == "GBPUSD"
        if status == "SOURCE_ERROR":
            assert shot["comparison_gap"]["error"] == session["views"]["comparison"]["error"]
    if status == "NO_VISIBLE_OBSERVATIONS":
        assert cutoff == start+599
        assert session["market"]["replay_cutoffs"][1] == start+600
        at = {**capture, "cutoff":start+600, "decision_replay":{**capture["decision_replay"], "position":1}}
        before = one("SELECT count(*) AS n FROM chart_snapshots")
        for evidence in ({"lanes":[primary]}, {"replay_lanes":["primary"]}, {}):
            assert client.post(URL,json={**at,**evidence}).status_code == 409
        assert one("SELECT count(*) AS n FROM chart_snapshots") == before
        paired = client.post(URL,json={**at,"replay_lanes":["primary","comparison"]})
        assert paired.status_code == 201 and "comparison_gap" not in paired.json()
        assert paired.json()["lanes"][1]["bar_times"] == [start,start+300]
        assert client.get(URL+"/"+shot["id"]).json() == shot


@pytest.mark.parametrize("n", [2000, 2001, 5000])
def test_large_decision_retains_early_candle_and_ordinary_upload_bound(client, n):
    start, session, capture, lanes = _decision_window(client, 5000, "COVERED")
    capture.update(cutoff=start+n*300, decision_replay=dict(session_id=session["id"], position=n-1,
                                                            view="comparison", time=start+300))
    upload = [{**lane, "bars":lane["bars"][:n]} for lane in lanes]
    response = client.post(URL,json={**capture,"lanes":upload})
    assert response.status_code == (201 if n == 2000 else 422), response.text[:300]
    replay = client.post(URL,json={**capture,"replay_lanes":["primary","comparison"]})
    assert replay.status_code == 201, replay.text
    saved = replay.json()
    if n == 2000:
        assert saved == response.json()
    for item, original in zip(saved["lanes"], lanes, strict=True):
        assert item["partitions"] == original["partitions"]
        assert item["bar_times"] == [start+i*300 for i in range(n)]
    assert saved["decision_replay"] == capture["decision_replay"]

def test_replay_lane_reference_requires_pins_and_exclusive_input(client):
    from research_backend.app import storage

    start, session, capture, lanes = _decision_window(client, 2, "COVERED")
    body = {**capture, "cutoff":start+300, "replay_lanes":["primary","comparison"]}
    for changed in ({"decision_replay":None}, {"lanes":lanes}):
        assert client.post(URL, json={**body, **changed}).status_code == 422
    # Damage only the peer file after freezing. The immutable lane captures
    # original prices, but publishing a new verified screenshot still checks the file.
    dataset = one("SELECT relative_path FROM datasets WHERE id=?", (lanes[1]["partitions"][0]["id"],))
    path = storage.MARKET_DIR / dataset["relative_path"]
    with path.open("ab") as file:
        file.write(b"corrupted fixture")
    response = client.post(URL, json=body)
    assert response.status_code == 409 and lanes[1]["partitions"][0]["id"] in response.text
    assert one("SELECT COUNT(*) AS n FROM chart_snapshots")["n"] == 0


def test_plan_can_use_only_the_bookmarked_peer_or_original_chart(client, plan):
    start, session, capture, _ = _decision_window(client, 2, "COVERED")
    capture["decision_replay"]["view"] = "comparison"
    response = client.post(URL, json={**capture, "cutoff":start+300, "replay_lanes":["primary","comparison"]})
    assert response.status_code == 201, response.text
    body = {**plan, "snapshot_id":response.json()["id"], "timeframe":"M5"}
    for symbol, expected in (("EURUSD", 201), ("GBPUSD", 201), ("USDJPY", 422)):
        saved = client.post("/api/trades", json={**body, "symbol":symbol})
        assert saved.status_code == expected, saved.text
