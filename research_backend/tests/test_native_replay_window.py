from datetime import UTC, datetime
from hashlib import sha256
import json


import pytest

from research_backend.app import db, storage

SESSIONS = "/api/replay/sessions"
START = 1_577_836_800
REQUEST = {"symbol": "EURUSD", "timeframe": "M1", "source": "DUKASCOPY_SAVED"}


def bar(i, step=60, **extra):
    return {"time": START + i * step, "open": 10., "high": 12., "low": 9., "close": 11., **extra}


def partition(candles, symbol="EURUSD", frame="M1", feed="native"):
    return storage.write_partition("DUKASCOPY", feed, symbol, "candles", frame, "BID", candles)


def create(client, **extra):
    return client.post(SESSIONS, json={**REQUEST, "window_bars": 5000, **extra})


def test_explicit_boundaries_pin_complete_partition_and_reopen(client):
    archive = partition([bar(i) for i in range(5001)])
    for count in (600, 601, 5000):
        response = create(client, window_bars=count, start=START, end=START + count * 60)
        assert response.status_code == 201, response.text
        frozen = response.json()
        market = frozen["market"]
        assert [b["time"] for b in market["candles"]] == [START+i*60 for i in range(count)]
        assert market["coverage"] == {"start": START, "end": START+count*60, "gaps": [],
                                      "truncated": False, "duplicate_bars": 0}
        assert market["partitions"] == [{"id": archive["id"], "sha256": archive["sha256"]}]
    url = SESSIONS + "/" + frozen["id"]
    selection = {"view": "primary", "time": START+4999*60}
    checkpoint = {"position": 4999, "bookmarks": [0, 4999], "selected": selection}
    assert client.post(url+"/checkpoint", json=checkpoint).json() == checkpoint
    # Even when source bytes disappear, a pinned future candle survives a rewind.
    (storage.MARKET_DIR / archive["relative_path"]).unlink()
    assert client.post(url+"/checkpoint", json={"position": 0, "bookmarks": [4999]}).json()["selected"] == selection
    restored = client.get(url).json()
    assert restored["market"] == market
    assert restored["checkpoint"] == {"position": 0, "bookmarks": [4999], "selected": selection}
    for update in ({"position": 5000}, {"position": 0, "bookmarks": [5000]},
                   {"position": 0, "selected": {"view": "4", "time": START}},
                   {"position": 0, "selected": {"view": "primary", "time": START+1}}):
        assert client.post(url+"/checkpoint", json=update).status_code == 422
    assert client.post(url+"/checkpoint", json={"position": 0, "selected": None}).json() == {"position": 0, "bookmarks": []}


def test_reject_over_bound_for_primary_and_companion_atomically(client):
    partition([bar(i) for i in range(5001)])
    partition([bar(0)], symbol="GBPUSD")
    interval = {"start": START, "end": START+5001*60}
    for context in ({}, {"symbol": "GBPUSD", "views": [{"view": "3", "symbol": "EURUSD", "timeframe": "M1"}]}):
        response = create(client, **interval, **context)
        assert response.status_code == 422 and "5000" in response.json()["detail"]
    for invalid in (5001, 0, 1.5, True):
        assert create(client, window_bars=invalid).status_code == 422
    assert db.one("SELECT count(*) AS n FROM replay_sessions")["n"] == 0
    recent = create(client).json()["market"]
    assert len(recent["candles"]) == 5000 and recent["candles"][0]["time"] == START+60
    assert recent["coverage"]["truncated"] and recent["coverage"]["start"] == START+60


def test_shards_overlap_delay_gap_and_independent_panes(client):
    # Standard hourly M1 shards. Overlap two files and leave one observed minute missing.
    for first in range(0, 5001, 60):
        partition([bar(i) for i in range(first, min(first+60, 5001)) if i != 4000])
    duplicate = partition([bar(59), bar(60)])
    peer = partition([bar(0, step=300, available_at=START+333), bar(2, step=300, available_at=START+920)],
                     symbol="GBPUSD", frame="M5")
    payload = {"views": [{"view": "2", "symbol": "GBPUSD", "timeframe": "M5"},
                         {"view": "3", "symbol": "XAUUSD", "timeframe": "M1"}]}
    for interval in ({}, {"start": START, "end": START+5001*60}):
        response = create(client, **payload, **interval)
        assert response.status_code == 201, response.text
        frozen = response.json()
        market = frozen["market"]
        assert len(market["candles"]) == 5000
        assert market["coverage"]["gaps"] == [{"start": START+4000*60, "end": START+4001*60}]
        assert market["coverage"]["duplicate_bars"] == 2
        assert len(market["partitions"]) == 85
        overlapping = market["candles"][59]
        contributors = [market["partitions"][index] for index in overlapping["source_partition_indexes"]]
        assert len(contributors) == 2
        assert {"id": duplicate["id"], "sha256": duplicate["sha256"]} in contributors
        assert overlapping["availability_basis"] == "MODELED_NOMINAL_CLOSE"
        assert {"id": duplicate["id"], "sha256": duplicate["sha256"]} in market["partitions"]
        companion = frozen["views"]["2"]
        assert companion["partitions"] == [{"id": peer["id"], "sha256": peer["sha256"]}]
        assert [b["available_at"] for b in companion["candles"]] == [START+333, START+920]
        assert "error" in frozen["views"]["3"]
    url = SESSIONS+"/"+frozen["id"]+"/checkpoint"
    selected = {"view": "2", "time": START+600}
    assert client.post(url, json={"position": 0, "selected": selected}).json()["selected"] == selected


def test_legacy_capture_identity_and_reader_limits(client):
    partition([bar(i) for i in range(601)])
    frozen = client.post(SESSIONS, json=REQUEST).json()
    assert len(frozen["market"]["candles"]) == 600
    assert frozen["request"] == {**REQUEST, "start": None, "end": None, "views": [], "signal_reference": None}
    encoded = json.dumps({k: frozen[k] for k in ("schema_version","request","market","views")},
                         sort_keys=True, separators=(",",":"))
    assert frozen["id"] == sha256(encoded.encode()).hexdigest()
    assert client.post(SESSIONS, json={**REQUEST, "start": START, "end": START+601*60}).status_code == 422
    assert client.get("/api/market/candles", params={**REQUEST, "limit": 5000}).status_code == 422


def test_overlap_integrity_and_incomplete_visibility(client):
    now = int(datetime.now(UTC).timestamp())
    archive = partition([bar(0, available_at=START+70), bar(2, available_at=now+300)])
    market = create(client, start=START, end=START+180).json()["market"]
    assert [b["available_at"] for b in market["candles"]] == [START+70]
    assert market["coverage"]["gaps"] == [{"start": START+60, "end": START+180}]
    partition([bar(0, available_at=START+71)])
    assert create(client).status_code == 409
    (storage.MARKET_DIR / archive["relative_path"]).write_bytes(b"checksum changed")
    assert create(client).status_code == 409


def test_genuinely_out_of_order_and_tied_arrivals_have_an_independent_clock(client):
    pin = partition([bar(0, available_at=START+200), bar(1, available_at=START+123),
                     bar(2, available_at=START+200)])
    response = create(client)
    assert response.status_code == 201, response.text
    pane = response.json()["market"]
    assert [b["time"] for b in pane["candles"]] == [START, START+60, START+120]
    assert pane["replay_cutoffs"] == [START+123, START+200, START+200]
    assert [b["time"] for b in pane["candles"] if b["available_at"] <= pane["replay_cutoffs"][0]] == [START+60]
    assert all(b["availability_basis"] == "ARCHIVE_SUPPLIED" and b["source_partition_indexes"] == [0]
               for b in pane["candles"])
    assert pane["partitions"] == [{"id": pin["id"], "sha256": pin["sha256"]}]


def test_native_evidence_reopens_only_the_frozen_contributing_partition(client):
    from research_backend.tests.test_replay_hour import bars, publish, receipt, START as start
    pin = publish(bars("EURUSD", count=2010))
    later = publish(bars("EURUSD", count=12, start=start+2010*300))
    response = create(client, timeframe="M5")
    assert response.status_code == 201, response.text
    # The selected hour never used the later artifact.
    (storage.MARKET_DIR / later["relative_path"]).unlink()
    detail = receipt(client, response.json()["id"], start+3600)
    assert detail["status"] == "COMPLETE"
    assert all(child["partitions"] == [{"id": pin["id"], "sha256": pin["sha256"]}] for child in detail["children"])

@pytest.mark.parametrize(("budget", "amount"), [("MAX_BYTES", 100), ("MAX_ROWS", 1), ("MAX_FILES", 1)])
def test_oversized_native_source_work_fails_before_session_publication(client, monkeypatch, budget, amount):
    from research_backend.app import replay_archive
    partition([bar(0), bar(1)])
    partition([bar(2)])
    monkeypatch.setattr(replay_archive, budget, amount)
    response = create(client)
    assert response.status_code == 409 and "budget" in response.json()["detail"]
    assert db.one("SELECT count(*) AS n FROM replay_sessions")["n"] == 0


def test_intrinsically_unclosed_and_future_bars_never_enter_the_snapshot(client):
    now = int(datetime.now(UTC).timestamp())
    partition([bar(0, closed=True), bar(1, closed=False),
               {**bar(2, closed=True), "time": (now//60+1)*60}])
    result = create(client)
    assert result.status_code == 201, result.text
    assert [b["time"] for b in result.json()["market"]["candles"]] == [START]


def test_saved_mt5_uses_its_own_feed_and_refuses_mixed_or_quarantined_history(client, monkeypatch):
    from research_backend.app import archive_provenance
    rows = [bar(i) for i in range(601)]
    mt = storage.write_partition("MT5", "broker-A", "EURUSD", "candles", "M1", "BID", rows)
    first = create(client, source="MT5_SAVED")
    assert first.status_code == 201, first.text
    pane = first.json()["market"]
    assert pane["feed_id"] == "broker-A" and pane["source"] == "MT5_SAVED" and len(pane["candles"]) == 601
    monkeypatch.setattr(archive_provenance, "SYNTHETIC_PARTITIONS", {mt["id"]})
    denied = create(client, source="MT5_SAVED")
    assert denied.status_code == 409 and "QUARANTINED" in denied.json()["detail"]
    monkeypatch.setattr(archive_provenance, "SYNTHETIC_PARTITIONS", set())
    storage.write_partition("MT5", "broker-B", "EURUSD", "candles", "M1", "BID", [bar(600)])
    mixed = create(client, source="MT5_SAVED")
    assert mixed.status_code == 409 and "incompatible feeds" in mixed.json()["detail"]


def test_repeated_native_panes_verify_one_physical_snapshot(client, monkeypatch):
    part = partition([bar(0)])
    reads = []
    original = storage.verified_partition_file

    def count(part, **kwargs):
        reads.append(part["id"])
        return original(part, **kwargs)

    monkeypatch.setattr(storage, "verified_partition_file", count)
    views = [{"view": str(index), "symbol": "EURUSD", "timeframe": "M1"} for index in (2, 3, 4)]
    response = create(client, views=views)
    assert response.status_code == 201, response.text
    frozen = response.json()
    assert all(pane == frozen["market"] for pane in frozen["views"].values())
    assert reads == [part["id"]]


def test_native_explicit_capture_obeys_requested_clock_and_only_counts_whole_slots(client):
    partition([bar(i, available_at=START+delay) for i, delay in
               [(0, 60), (1, 200), (2, 201), (3, 240)]])
    partition([bar(0, step=3600)], symbol="GBPUSD", frame="H1")
    response = create(client, start=START+10, end=START+200,
                      views=[{"view":"2", "symbol":"GBPUSD", "timeframe":"H1"}])
    assert response.status_code == 201, response.text
    session = response.json()
    primary = session["market"]
    assert [b["time"] for b in primary["candles"]] == [START+60]
    assert primary["replay_cutoffs"] == [START+200]  # Arrival exactly at the exclusive candle-range end is known.
    assert primary["coverage"] == {"start": START+10, "end": START+200, "slot_start": START+60,
                                    "slot_end": START+180, "truncated": False, "duplicate_bars": 0,
                                    "gaps": [{"start": START+120, "end": START+180}]}
    assert "No complete H1 candle slot" in session["views"]["2"]["error"]
    no_hour = create(client, symbol="GBPUSD", timeframe="H1", start=START+600, end=START+2400)
    assert no_hour.status_code == 422 and "No complete H1 candle slot" in no_hour.json()["detail"]


def test_peer_range_slots_and_arrivals_are_bounded_by_the_same_exclusive_end(client):
    partition([bar(1), bar(2)])
    partition([bar(1, step=300, available_at=START+601),
               bar(2, step=300, available_at=START+962),
               bar(3, step=300, available_at=START+1200)], symbol="GBPUSD", frame="M5")
    response = create(client, start=START+10, end=START+961,
                      views=[{"view":"2", "symbol":"GBPUSD", "timeframe":"M5"}])
    assert response.status_code == 201, response.text
    peer = response.json()["views"]["2"]
    assert [(b["time"], b["available_at"]) for b in peer["candles"]] == [(START+300, START+601)]
    assert peer["coverage"]["start"] == START+10 and peer["coverage"]["end"] == START+961
    assert (peer["coverage"]["slot_start"],peer["coverage"]["slot_end"]) == (START+300, START+900)
    assert peer["coverage"]["gaps"] == [{"start":START+600, "end":START+900}]


def test_explicit_terminal_clock_reaches_later_companion_and_restores_5001st_step(client):
    partition([bar(i) for i in range(5000)])
    end = START+5001*60
    partition([bar(5000, available_at=end)], symbol="GBPUSD")
    response = create(client, start=START, end=end, views=[{"view":"2","symbol":"GBPUSD","timeframe":"M1"}])
    assert response.status_code == 201, response.text
    session = response.json()
    pane = session["market"]
    clock = pane["replay_cutoffs"]
    assert len(pane["candles"]) == 5000 and len(clock) == 5001
    assert clock[-2:] == [end-60, end] and pane["terminal_cutoff"] == end
    peer = session["views"]["2"]
    assert peer["replay_cutoffs"] == [end] and "terminal_cutoff" not in peer
    assert clock[-2] < peer["candles"][0]["available_at"] == clock[-1]
    endpoint = SESSIONS+"/"+session["id"]
    selected = {"view":"2", "time":end-60}
    checkpoint = {"position":5000, "bookmarks":[4999,5000], "selected":selected}
    assert client.post(endpoint+"/checkpoint", json=checkpoint).json() == checkpoint
    restored = client.get(endpoint).json()
    assert restored["checkpoint"] == checkpoint and restored["market"]["replay_cutoffs"] == clock
    assert client.post(endpoint+"/checkpoint", json={"position":5001}).status_code == 422
    assert client.post(endpoint+"/checkpoint", json={"position":5000,"bookmarks":[5001]}).status_code == 422
