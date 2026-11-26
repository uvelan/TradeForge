"""Saved M5 zone receipts across actual arrival, equal arrivals and pinned file changes."""
import pytest

from research_backend.app import storage
from research_backend.tests.test_replay_hour import START, URL, publish, session

STEP = 300
BLOCK = [(9, 10, 8, 9), (10, 11, 9, 10), (11, 14, 10, 12), (10, 11, 9, 10),
         (10.5, 11, 9, 10), (12, 16, 12, 15), (13, 14, 11, 13), (13, 14, 10, 13), (13, 14, 9, 10)]
GAP = [(10, 11, 9, 10), (11, 15, 10, 14), (15, 17, 14, 16), (16, 17, 14, 15),
       (15, 16, 12.5, 15), (15, 16, 11, 13)]


def bars(values, arrivals=None):
    arrivals = arrivals or {}
    return [dict(time=START+i*STEP, open=o, high=h, low=l, close=c, available_at=arrivals.get(i))
            for i, (o, h, l, c) in enumerate(values)]


def query(client, key, cutoff, **options):
    return client.get(f"{URL}/{key}/zones", params={"cutoff": cutoff, **options})


def receipt(client, key, cutoff, **options):
    response = query(client, key, cutoff, **options)
    assert response.status_code == 200, response.text
    return response.json()


def family(result, kind, index):
    return next(z for z in result["zones"] if z["type"] == kind and z["formation_time"] == START+index*STEP)


def stamp(index):
    return START+index*STEP


def test_equal_arrival_ob_revisions_and_exact_pins_survive_terminal_rewind(client):
    values = bars(BLOCK, {6: stamp(8), 7: stamp(8)})
    first, second = publish(values[:5]), publish(values[4:])
    key = session(client, end=stamp(9))
    end = receipt(client, key, stamp(9))
    zone = family(end, "OB", 5)
    detail = receipt(client, key, stamp(9), zone_id=zone["zone_id"])["selected"]
    revisions = detail["revisions"]
    assert [(r["revision"], r["state"], r["mitigation_pct"]) for r in revisions] == [
        (1, "ACTIVE", 0), (2, "TOUCHED", 0), (3, "PARTIALLY_MITIGATED", 50), (4, "FULLY_MITIGATED", 100)]
    assert [r["available_at"] for r in revisions] == [stamp(6), stamp(8), stamp(8), stamp(9)]
    assert (zone["lower"], zone["upper"], zone["remaining_lower"], zone["remaining_upper"]) == (9, 11, 9, 9)
    structure = detail["structure"]
    assert structure["type"] == "BOS" and structure["pivot"]["pivot_time"] == stamp(2)
    assert structure["level_available_at"] == stamp(5) and structure["break_time"] == stamp(5)
    parent = client.get(f"{URL}/{key}/structure-liquidity",
                        params={"cutoff": stamp(9), "event_id": structure["id"]}).json()["selected"]
    assert parent["evidence"] == structure["evidence"]
    assert [c["time"] for c in detail["evidence"]] == [stamp(i) for i in range(9)]
    row = detail["evidence"][4]
    assert row["partitions"] == sorted([{k: p[k] for k in ("id", "sha256")} for p in (first, second)],
                                       key=lambda p: p["id"])
    assert {r["role"] for r in row["roles"]} == {"OB_CANDIDATE", "PIVOT_RIGHT", "PIVOT_CONFIRMATION"}
    assert {r["role"] for r in detail["evidence"][5]["roles"]} == {"STRUCTURE_BREAK", "REVISION_OBSERVATION"}
    assert detail["evidence"][6]["availability_basis"] == "ARCHIVE_SUPPLIED"
    for cutoff, revision in ((stamp(8), 3), (stamp(8)-1, 1), (stamp(6), 1)):
        previous = receipt(client, key, cutoff, zone_id=zone["zone_id"])["selected"]
        assert previous["id"] == revisions[revision-1]["id"] and previous["revisions"] == revisions[:revision]
        assert previous["evidence"] == detail["evidence"][:5+revision]
    assert receipt(client, key, stamp(6)-1, zone_id=zone["zone_id"])["selected"] is None
    close = family(receipt(client, key, stamp(9), mitigation="close"), "OB", 5)
    assert (close["state"], close["revision"], close["mitigation_pct"]) == ("PARTIALLY_MITIGATED", 2, 50)
    assert close["zone_id"] != zone["zone_id"]


def test_delayed_gap_wick_close_and_exact_producer_activation(client):
    values = bars(GAP, {2: stamp(3)+3})
    publish(values)
    key = session(client, end=stamp(6))
    formation = family(receipt(client, key, stamp(3)+3), "FVG", 2)
    assert [formation[k] for k in ("lower", "upper", "formation_confirmation_time", "formation_available_at")] == [
        11, 14, stamp(3), stamp(3)+3]
    assert not receipt(client, key, stamp(3)+2)["zones"]
    assert receipt(client, key, stamp(4), zone_id=formation["zone_id"])["selected"]["revision"] == 1
    result = receipt(client, key, stamp(6), zone_id=formation["zone_id"])["selected"]
    assert result["structure"] is None
    assert [r["state"] for r in result["revisions"]] == ["ACTIVE", "PARTIALLY_MITIGATED", "FULLY_MITIGATED"]
    assert [r["time"] for r in result["evidence"]] == [stamp(i) for i in (0,1,2,4,5)]
    assert [r["role"] for c in result["evidence"][:3] for r in c["roles"]] == [
        "FVG_A", "FVG_B", "FVG_C", "REVISION_OBSERVATION"]
    close = family(receipt(client, key, stamp(6), mitigation="close"), "FVG", 2)
    assert (close["revision"], close["remaining_upper"]) == (2, 13)
    lane = client.get(f"{URL}/{key}").json()["market"]
    canonical = client.post("/api/indicators/evaluate", json={
        **{k: lane[k] for k in ("source", "symbol", "timeframe", "feed_id", "price_basis", "partitions", "candles")},
        "timing_policy":"STRICT_CAUSAL", "frozen":True, "cutoff":stamp(6),
        "indicators":[{"id":"fair_value_gap", "fvg":{"market_schedule":{"kind":"forex_weekly"}}}]})
    assert canonical.status_code == 200, canonical.text
    product = canonical.json()["results"][0]
    assert [r["id"] for r in result["revisions"]] == [
        r["id"] for r in [*product["formations"], *product["revisions"]] if r["fvg_id"] == formation["zone_id"]]


def test_carried_forward_structure_proof_stops_before_equal_arrival_successor(client):
    values = bars(BLOCK, {5: stamp(7)})
    publish(values)
    key = session(client, end=stamp(9))
    zone = family(receipt(client, key, stamp(7)), "OB", 5)
    detail = receipt(client, key, stamp(9), zone_id=zone["zone_id"])["selected"]
    parent = detail["structure"]
    for cutoff in (stamp(7), stamp(9)):
        original = client.get(f"{URL}/{key}/structure-liquidity",
                              params={"cutoff": cutoff, "event_id": parent["id"]}).json()["selected"]
        assert [row["time"] for row in original["evidence"]] == [stamp(i) for i in range(6)]
        assert parent["evidence"] == original["evidence"]
    assert not any(r["observation_time"] == stamp(6) for r in detail["revisions"])


def test_equal_arrival_pivot_cannot_qualify_block(client):
    publish(bars(BLOCK, {4: stamp(6)}))
    key = session(client, end=stamp(9))
    result = receipt(client, key, stamp(6))
    assert all(zone["type"] != "OB" for zone in result["zones"])
    parent = client.get(f"{URL}/{key}/structure-liquidity", params={"cutoff": stamp(6)}).json()
    assert not any(event["type"] == "BOS" for event in parent["events"])


def test_paging_and_missing_or_corrupt_pins_fail_closed(client):
    values = bars(GAP * 35)
    partition = publish(values)
    end = values[-1]["time"]+STEP
    key = session(client, end=end)
    last = receipt(client, key, end)
    assert len(last["zones"]) == 50 < last["total"]
    all_zones = list(last["zones"])
    cursor = last["previous_before"]
    while cursor is not None:
        page = receipt(client, key, end, before=cursor)
        all_zones = page["zones"] + all_zones
        cursor = page["previous_before"]
    assert len({row["zone_id"] for row in all_zones}) == len(all_zones) == last["total"]
    first = all_zones[0]
    assert receipt(client, key, end, zone_id=first["zone_id"])["selected"]["id"] == first["id"]
    assert query(client, key, end+1).status_code == 422
    assert query(client, key, end, view="3").status_code == 422
    assert query(client, key, end, mitigation="body").status_code == 422
    (storage.MARKET_DIR / partition["relative_path"]).write_bytes(b"corrupt controlled fixture")
    failed = query(client, key, end, zone_id=first["zone_id"])
    assert failed.status_code == 409 and partition["id"] in failed.json()["detail"]
