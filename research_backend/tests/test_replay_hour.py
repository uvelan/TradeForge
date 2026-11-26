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


def session(client, start=START, end=START + 7200, **extra):
    response = client.post(URL, json=dict(symbol="EURUSD", source="DUKASCOPY_SAVED", timeframe="M5",
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
