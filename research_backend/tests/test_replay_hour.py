"""Public saved-candle replay API, with isolated controlled Parquet partitions."""
from datetime import UTC, datetime

import pytest

from research_backend.app import storage

START = int(datetime(2025, 11, 13, 13, tzinfo=UTC).timestamp())
URL = "/api/replay/sessions"


def bars(symbol, count=24, start=START):
    base = 100 if symbol == "EURUSD" else 200
    return [{"time": start + i * 300, "open": base + i, "close": base + i + 1,
             "high": base + i + (40 if i == 2 else 2), "low": base + i - (20 if i == 4 else 1)}
            for i in range(count)]


def publish(values, symbol="EURUSD"):
    return storage.write_partition("DUKASCOPY", "controlled-replay-test", symbol, "candles", "M5", "BID", values)


def session(client, start=START, end=START + 7200, symbol="EURUSD", **extra):
    response = client.post(URL, json=dict(symbol=symbol, source="DUKASCOPY_SAVED", timeframe="M5",
                                           start=start, end=end, **extra))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def inspect(client, key, cutoff, start=START, view="primary"):
    return client.get(f"{URL}/{key}/higher-timeframe",
                      params={"view": view, "hour_start": start, "cutoff": cutoff})


def receipt(client, *args, **kwargs):
    response = inspect(client, *args, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def test_complete_hour_has_exact_contributors_across_shards_and_survives_extension(client):
    eur = bars("EURUSD")
    first = publish(eur[:7])
    second = publish(eur[6:12])
    peer = publish(bars("GBPUSD", 12), "GBPUSD")
    views = [{"view": "comparison", "symbol": "GBPUSD", "timeframe": "M5"}]
    key = session(client, end=START + 3600, views=views)
    for lane in ("primary", "comparison"):
        forming = receipt(client, key, START + 3599, view=lane)
        assert (forming["status"], forming["available_at"], forming["ohlc"], forming["children"],
                forming["missing_slots"]) == ("FORMING", None, None, [], [])
    confirmed = receipt(client, key, START + 3600)
    assert (confirmed["start"], confirmed["end"], confirmed["available_at"]) == (START, START + 3600, START + 3600)
    assert confirmed["ohlc"] == {"open": 100, "high": 142, "low": 84, "close": 112}
    assert confirmed["status"] == "COMPLETE" and len(confirmed["children"]) == 12
    peer_receipt = receipt(client, key, START + 3600, view="comparison")
    assert peer_receipt["ohlc"] == {"open": 200, "high": 242, "low": 184, "close": 212}
    for i, (child, other) in enumerate(zip(confirmed["children"], peer_receipt["children"], strict=True)):
        sources = [first] if i < 6 else [second] if i > 6 else sorted((first, second), key=lambda p: p["id"])
        assert child["partitions"] == [{k: p[k] for k in ("id", "sha256")} for p in sources]
        assert other["partitions"] == [{k: peer[k] for k in ("id", "sha256")}]
        assert child["source_id"] == f"{sources[0]['id']}:{START + i * 300}"
        assert (child["time"], child["available_at"]) == (START + i * 300, START + (i + 1) * 300)
    publish(eur[12:])
    extended = session(client)
    next_hour = receipt(client, extended, START + 7200, START + 3600)
    assert next_hour["ohlc"] == dict(open=112, high=125, low=111, close=124)
    assert receipt(client, extended, START + 7199, START + 3600)["status"] == "FORMING"
    assert receipt(client, extended, START + 3600) == {**confirmed, "session_id": extended}
    assert receipt(client, key, START + 3600) == confirmed


@pytest.mark.parametrize("scenario", ["missing", "delayed"])
def test_missing_and_delayed_children_remain_indistinguishable_until_delivery(client, scenario):
    values = bars("EURUSD")
    i = 5 if scenario == "missing" else 11
    if scenario == "missing":
        del values[i]
    else:
        for bar in values:
            bar["available_at"] = None
        values[i]["available_at"] = values[i]["time"] + 320
    publish(values)
    key = session(client)
    for cutoff in (START + 3600, START + 3619):
        result = receipt(client, key, cutoff)
        assert (result["status"], result["reason"], result["missing_slots"]) == (
            "UNRESOLVED", "MISSING_OPEN_CANDLES", [START + i * 300])
        assert (result["available_at"], result["ohlc"], result["children"]) == (None, None, [])
    after = receipt(client, key, START + 3620)
    if scenario == "delayed":
        assert after["status"] == "COMPLETE" and after["available_at"] == START + 3620
        assert after["children"][-1]["available_at"] == START + 3620
    else:
        assert after["status"] == "UNRESOLVED"
    assert receipt(client, key, START + 3619) == result


def test_flat_hour_is_valid_and_damaged_archive_never_supplies_child_proof(client):
    flat = [{"time": START + t * 300, "open": 1, "high": 1, "low": 1, "close": 1} for t in range(12)]
    partition = publish(flat)
    key = session(client)
    assert receipt(client, key, START + 3600)["ohlc"] == dict(open=1, high=1, low=1, close=1)
    (storage.MARKET_DIR / partition["relative_path"]).write_bytes(b"controlled archive damage")
    rejected = inspect(client, key, START + 3600)
    assert rejected.status_code == 409
    assert partition["id"] in rejected.json()["detail"]
    assert "checksum" in rejected.json()["detail"].lower()


def test_calendar_contradiction_closed_hour_and_scope_boundaries(client):
    saturday = START + 2 * 86400
    publish(bars("EURUSD", 12, saturday))
    key = session(client, saturday, saturday + 7200)
    contradiction = receipt(client, key, saturday + 3600, saturday)
    assert (contradiction["status"], contradiction["reason"], contradiction["ohlc"]) == (
        "UNRESOLVED", "SCHEDULE_MISMATCH", None)
    closed = receipt(client, key, saturday + 7200, saturday + 3600)
    assert (closed["status"], closed["ohlc"], closed["children"]) == ("CLOSED", None, [])
    for start, cutoff in [(saturday - 3600, saturday), (saturday + 1, saturday + 3600),
                          (saturday, saturday + 7201), (saturday + 3600, saturday + 3599)]:
        assert inspect(client, key, cutoff, start).status_code == 422
    assert inspect(client, key, saturday + 3600, saturday, "comparison").status_code == 422


def test_partial_replay_window_cannot_borrow_archived_candles_before_session(client):
    publish(bars("EURUSD"))
    key = session(client, start=START + 600)
    result = receipt(client, key, START + 3600)
    assert (result["status"], result["reason"], result["missing_slots"]) == (
        "UNRESOLVED", "MISSING_OPEN_CANDLES", [START, START + 300])
    assert result["ohlc"] is None and result["children"] == []


@pytest.mark.parametrize(("primary", "peer"), [("EURUSD", "GBPUSD"), ("GBPUSD", "EURUSD")])
def test_delayed_primary_clock_inspects_on_time_peer_at_the_same_cutoff(client, primary, peer):
    end = START + 3600
    primary_bars = bars(primary, 12)
    for row in primary_bars:
        row["available_at"] = row["time"] + 300
    primary_bars[-1]["available_at"] = end + 20
    primary_partition = publish(primary_bars, primary)
    peer_partition = publish(bars(peer, 12), peer)
    key = session(client, end=end, symbol=primary, views=[dict(view="comparison", symbol=peer, timeframe="M5")])

    expected_ohlc = {"EURUSD": dict(open=100, high=142, low=84, close=112),
                     "GBPUSD": dict(open=200, high=242, low=184, close=212)}
    for cutoff in (end, end + 19, end + 20):
        primary_hour = receipt(client, key, cutoff)
        peer_hour = receipt(client, key, cutoff, view="comparison")
        assert (peer_hour["status"], peer_hour["symbol"], peer_hour["cutoff"], peer_hour["available_at"]) == (
            "COMPLETE", peer, cutoff, end)
        assert peer_hour["ohlc"] == expected_ohlc[peer]
        assert [child["source_id"] for child in peer_hour["children"]] == [
            f"{peer_partition['id']}:{START + i * 300}" for i in range(12)]
        if cutoff < end + 20:
            assert primary_hour["status"] == "UNRESOLVED"
            assert primary_hour["missing_slots"] == [end - 300]
            assert primary_hour["ohlc"] is None and primary_hour["children"] == []
        else:
            assert primary_hour["status"] == "COMPLETE"
            assert primary_hour["ohlc"] == expected_ohlc[primary]
            assert [child["source_id"] for child in primary_hour["children"]] == [
                f"{primary_partition['id']}:{START + i * 300}" for i in range(12)]
            assert primary_hour["available_at"] == primary_hour["children"][-1]["available_at"] == cutoff
    for view in ("primary", "comparison"):
        assert inspect(client, key, end + 21, view=view).status_code == 422
        assert inspect(client, key, end + 20, start=end, view=view).status_code == 422


def test_shorter_peer_keeps_its_own_hour_range_and_the_primary_clock(client):
    publish(bars("EURUSD", 36, START - 3600))
    publish(bars("GBPUSD", 12), "GBPUSD")

    # With no explicit bounds, each pane captures its own recent archive range.
    def captured(primary, peer):
        return session(client, start=None, end=None, symbol=primary,
                       views=[dict(view="comparison", symbol=peer, timeframe="M5")])

    late_cutoff = START + 7200
    long_primary = captured("EURUSD", "GBPUSD")
    peer_hour = receipt(client, long_primary, late_cutoff, view="comparison")
    assert (peer_hour["status"], peer_hour["available_at"], peer_hour["cutoff"]) == (
        "COMPLETE", START + 3600, late_cutoff)
    assert inspect(client, long_primary, late_cutoff, start=START + 3600, view="comparison").status_code == 422
    assert receipt(client, long_primary, late_cutoff, start=START + 3600)["status"] == "COMPLETE"

    short_primary = captured("GBPUSD", "EURUSD")
    assert inspect(client, short_primary, START - 1, start=START - 3600, view="comparison").status_code == 422
    assert inspect(client, short_primary, START + 3601, view="comparison").status_code == 422
    assert receipt(client, short_primary, START + 3600, view="comparison")["status"] == "COMPLETE"
