"""Immutable replay evidence across cutoff, archive, and pane boundaries."""
import pytest

from research_backend.app import storage
from research_backend.tests.test_replay_hour import START, URL, publish, session

STEP = 300
TREND = [10, 11, 12, 11, 10, 11, 12, 13, 12, 11, 12, 13, 14, 13, 12, 13, 14, 15, 14, 13, 12, 10]


def bars(prices, base=1.1):
    result = []
    for i, price in enumerate(prices):
        value = round(base + price / 1000, 5)
        result.append(dict(time=START + i * STEP, open=value, high=value, low=value, close=value))
    return result


def request(client, key, cutoff, **params):
    return client.get(f"{URL}/{key}/structure-liquidity", params={"cutoff": cutoff, **params})


def receipt(client, key, cutoff, **params):
    response = request(client, key, cutoff, **params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("displacement,expected_mss", [(0.00100, True), (0.00099, False)])
def test_choch_displacement_boundary_delayed_delivery_append_and_exact_pins(client, displacement, expected_mss):
    values = bars(TREND)
    last = values[-1]
    last["open"] = last["high"] = round(last["close"] + displacement, 5)
    for row in values:
        row["available_at"] = None
    end = last["time"] + STEP
    last["available_at"] = end + 9
    left, right = publish(values[:10]), publish(values[9:])
    key = session(client, end=end + STEP)
    prior = receipt(client, key, end + 8)
    assert {e["type"] for e in prior["events"]} == {"BOS"}
    at = receipt(client, key, end + 9)
    breaks = [e for e in at["events"] if e["observation_time"] == last["time"]]
    assert [e["type"] for e in breaks] == (["CHOCH", "MSS"] if expected_mss else ["CHOCH"])
    choch = breaks[0]
    assert choch["anchor_time"] == START + 14 * STEP
    assert choch["level_price"] == values[14]["low"]
    assert (choch["direction"], choch["confirmation_time"], choch["available_at"]) == ("BEARISH", end, end+9)
    if expected_mss:
        assert breaks[-1]["supporting_ids"][0] == choch["id"]
    selected_id = breaks[-1]["id"]
    selected = receipt(client, key, end + 9, event_id=selected_id)["selected"]
    assert selected["id"] == selected_id
    pivot_id = choch["supporting_ids"][0]
    assert [(i, role["role"]) for i, row in enumerate(selected["evidence"]) for role in row["roles"]
            if role["event_id"] == pivot_id] == [
        (12, "PIVOT_LEFT"), (13, "PIVOT_LEFT"), (14, "PIVOT_ANCHOR"), (15, "PIVOT_RIGHT"),
        (16, "PIVOT_RIGHT"), (16, "PIVOT_CONFIRMATION")]
    assert selected["evidence"][-1]["roles"] == [{"role": "EVENT_OBSERVATION", "event_id": selected_id}]
    assert [c["time"] for c in selected["evidence"]] == [v["time"] for v in values]
    assert selected["evidence"][-1]["availability_basis"] == "ARCHIVE_SUPPLIED"
    assert selected["evidence"][0]["availability_basis"] == "MODELED_NOMINAL_CLOSE"
    for i, child in enumerate(selected["evidence"]):
        parts = [left] if i < 9 else [right] if i > 9 else sorted([left, right], key=lambda p: p["id"])
        assert child["partitions"] == [{k: p[k] for k in ("id", "sha256")} for p in parts]
        assert child["source_id"] == f"{parts[0]['id']}:{values[i]['time']}"
    assert receipt(client, key, end + 8, event_id=selected_id) == prior
    publish([dict(time=end, open=1.11, high=1.112, low=1.11, close=1.112)])
    extended = session(client, end=end + 2*STEP)
    assert extended != key
    assert receipt(client, extended, end + 9, event_id=selected_id)["selected"] == selected
    assert receipt(client, extended, end + 2*STEP, event_id=selected_id)["selected"] == selected
    assert receipt(client, key, end + 9, event_id=selected_id)["selected"] == selected


def test_liquidity_cutoff_peers_and_canonical_ids(client):
    values = bars([10, 11, 12, 11, 10, 11, 12, 11, 10, 11])
    values[-1]["high"] = 1.113
    publish(values)
    mirror = [{**v, "open": 3-v["open"], "close": 3-v["close"], "high": 3-v["low"], "low": 3-v["high"]}
              for v in values]
    publish(mirror, "GBPUSD")
    end = values[-1]["time"] + STEP
    key = session(client, end=end, views=[dict(view="comparison", symbol="GBPUSD", timeframe="M5")])
    saved = client.get(f"{URL}/{key}").json()
    ids = set()
    for view, classification, side in [("primary", "EQH", "BSL"), ("comparison", "EQL", "SSL")]:
        lane = saved["market"] if view == "primary" else saved["views"][view]
        at = receipt(client, key, end, view=view)
        pool = next(e for e in at["events"] if e["type"] == classification)
        sweep = next(e for e in at["events"] if e["type"] == "SWEPT")
        assert (sweep["anchor_time"], sweep["observation_time"], sweep["liquidity_side"]) == (
            values[2]["time"], values[-1]["time"], side)
        detail = receipt(client, key, end, view=view, event_id=sweep["id"])["selected"]
        roles = {(i, w["role"], w["event_id"]) for i, row in enumerate(detail["evidence"]) for w in row["roles"]}
        for index, producer in zip((2, 6), pool["supporting_ids"], strict=True):
            assert (index, "PIVOT_ANCHOR", producer) in roles
            assert (index + 2, "PIVOT_CONFIRMATION", producer) in roles
        assert {(8, "LEVEL_ACTIVATION", pool["id"]), (8, "LEVEL_REVISION", pool["id"]),
                (9, "EVENT_OBSERVATION", sweep["id"])} <= roles
        assert len(pool["supporting_ids"]) == 2 and sweep["supporting_ids"][:2] == pool["supporting_ids"]
        prior = receipt(client, key, end-1, view=view, event_id=sweep["id"])
        assert prior["selected"] is None and pool in prior["events"]
        assert not ids.intersection(e["id"] for e in at["events"])
        ids.update(e["id"] for e in at["events"])
        canonical = client.post("/api/indicators/evaluate", json={
            **{k: lane[k] for k in ("source", "symbol", "timeframe", "feed_id", "price_basis", "partitions", "candles")},
            "timing_policy": "STRICT_CAUSAL", "frozen": True, "cutoff": end,
            "indicators": [{"id": name, "structure": at["rules"]["structure"]}
                           for name in ("market_structure", "liquidity_sweeps")]})
        assert canonical.status_code == 200, canonical.text
        structure, liquid = canonical.json()["results"]
        assert pool["id"] in {r["id"] for r in liquid["pools"]}
        assert sweep["id"] in {r["id"] for r in liquid["sweeps"]}
        assert {e["id"] for e in at["events"] if e["type"] == "WICK_CANDIDATE"} == {
            e["id"] for e in structure["breaks"] if e["type"] == "WICK_CANDIDATE"}
    assert request(client, key, end+1).status_code == 422
    assert request(client, key, end, view="3").status_code == 422


def test_paging_exact_old_pin_and_damaged_source(client):
    values = bars(TREND * 13)
    partition = publish(values)
    end = values[-1]["time"] + STEP
    key = session(client, end=end)
    latest = receipt(client, key, end)
    assert latest["total"] > 50 and len(latest["events"]) == 50
    page = latest
    events = latest["events"]
    while page["previous_before"] is not None:
        page = receipt(client, key, end, before=page["previous_before"])
        events = page["events"] + events
    assert len(events) == len({e["id"] for e in events}) == latest["total"]
    assert [e["available_at"] for e in events] == sorted(e["available_at"] for e in events)
    old = events[0]
    assert old not in latest["events"]
    pinned = receipt(client, key, end, event_id=old["id"])["selected"]
    assert {k: v for k, v in pinned.items() if k != "evidence"} == old
    (storage.MARKET_DIR / partition["relative_path"]).write_bytes(b"corrupted fixture")
    failure = request(client, key, end, event_id=old["id"])
    assert failure.status_code == 409 and partition["id"] in failure.json()["detail"]
