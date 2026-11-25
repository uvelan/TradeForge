from copy import deepcopy
from hashlib import sha256
import json

from research_backend.app.db import one, transaction
from research_backend.app.storage import write_partition


URL = "/api/chart-snapshots"


def test_snapshot_archive_pins_and_legacy_identity(client):
    import base64
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    image = (bytes.fromhex("89504e470d0a1a0a") + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
             + chunk(b"IDAT", zlib.compress(bytes(2))) + chunk(b"IEND", b""))
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
