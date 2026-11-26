"""Public CRT replay receipts against immutable, controlled saved M5 partitions."""
from datetime import UTC, datetime

import pytest

from research_backend.app import storage
from research_backend.tests.test_crt import CENTER, HIGH, LOW, REF
from research_backend.tests.test_replay_hour import START, URL, publish, session


STEP = 300


def prices(values, *, start=START, arrivals=None):
    return [{"time": start + i*STEP, "open": o, "high": h, "low": l, "close": c,
             "available_at": (arrivals or {}).get(i)} for i, (o, h, l, c) in enumerate(values)]


def query(client, key, cutoff, **params):
    return client.get(f"{URL}/{key}/crt", params={"cutoff": cutoff, **params})


def receipt(client, key, cutoff, **params):
    response = query(client, key, cutoff, **params)
    assert response.status_code == 200, response.text
    return response.json()


def test_equal_arrival_terminal_family_matches_indicator_and_exact_reference(client):
    at = lambda i: START + i*STEP
    values = prices([REF]*12 + [HIGH, CENTER, (102, 103, 100, 101)], arrivals={13: at(15), 14: at(15)})
    pins = [publish(values[:7]), publish(values[6:])]
    publish(prices([REF]*12 + [LOW]), "GBPUSD")
    key = session(client, end=at(15), views=[{"view":"comparison", "symbol":"GBPUSD", "timeframe":"M5"}])
    page = receipt(client, key, at(15))
    final, = page["ranges"]
    selected = receipt(client, key, at(15), range_id=final["range_id"])["selected"]
    chain = selected["revisions"]
    assert [(r["revision"],r["state"],r["available_at"]) for r in chain] == [
        (1,"ACTIVE",at(12)), (2,"HIGH_SWEPT_RECLAIMED",at(13)), (3,"EXPANSION_OBSERVED",at(15)),
        (4,"OPPOSITE_BOUNDARY_REACHED",at(15))]
    assert chain[-1] == final
    assert [(r["observation_time"],r["confirmation_time"]) for r in chain] == [
        (at(0),at(12)), (at(12),at(13)), (at(13),at(14)), (at(14),at(15))]
    assert [r["sweep_side"] for r in chain] == [None,"HIGH","HIGH","HIGH"]
    assert [r["reason"] for r in chain] == [
        "CLOSED_REFERENCE","SAME_CANDLE_RECLAIM","POST_RECLAIM_MIDPOINT","POST_RECLAIM_BOUNDARY"]
    lane = client.get(f"{URL}/{key}").json()["market"]
    canonical = client.post("/api/indicators/evaluate", json={
        **{k:lane[k] for k in ("source","symbol","timeframe","feed_id","price_basis","partitions","candles")},
        "timing_policy":"STRICT_CAUSAL", "frozen":True, "cutoff":at(15),
        "indicators":[{"id":"candle_range_theory","crt":page["rules"]["crt"]}]})
    assert canonical.status_code == 200, canonical.text
    result = canonical.json()["results"][0]
    original = [*result["crt_formations"], *result["crt_sweeps"], *result["crt_revisions"]]
    assert [row["id"] for row in original] == [row["id"] for row in chain]
    assert all(r["range_id"] == original[0]["range_id"] and r["reference_id"] == original[0]["reference_id"]
               for r in chain)
    assert final["reference_ohlc"] == list(REF)
    assert final["reference_start"] == START and final["reference_end"] == final["reference_available_at"] == at(12)
    assert [r["time"] for r in selected["reference_children"]] == [at(i) for i in range(12)]
    shared = selected["reference_children"][6]
    assert shared["partitions"] == sorted(({k:p[k] for k in ("id","sha256")} for p in pins), key=lambda p:p["id"])
    assert shared["source_id"] == f"{shared['partitions'][0]['id']}:{at(6)}"
    assert [r["roles"] for r in selected["evidence"]] == [
        *([[{"role":"REFERENCE_CHILD","event_id":chain[0]["id"]}]]*12),
        *([{"role":"REVISION_OBSERVATION","event_id":r["id"]}] for r in chain[1:])]
    assert shared["availability_basis"] == "MODELED_NOMINAL_CLOSE"
    assert selected["evidence"][-1]["availability_basis"] == "ARCHIVE_SUPPLIED"
    prior = receipt(client,key,at(15)-1,range_id=final["range_id"])["selected"]
    assert prior["revisions"] == chain[:2] and prior["evidence"] == selected["evidence"][:13]
    assert receipt(client,key,at(12)-1,range_id=final["range_id"])["selected"] is None
    peer = receipt(client,key,at(15),view="comparison",range_id=final["range_id"])
    assert peer["selected"] is None and peer["ranges"][0]["sweep_side"] == "LOW"
    assert peer["ranges"][0]["range_id"] != final["range_id"]


def test_delayed_reference_straddling_and_corrupt_pin(client):
    release = START + 3607
    values = prices([REF]*12+[HIGH], arrivals={11:release})
    pin = publish(values)
    key = session(client)
    assert receipt(client,key,release-1)["ranges"] == []
    formation, = receipt(client,key,release)["ranges"]
    options = {"range_id":formation["range_id"]}
    active = receipt(client,key,release,**options)["selected"]
    assert active["reference_available_at"] == active["reference_children"][-1]["available_at"] == release
    uncertain = receipt(client,key,START+3900,**options)["selected"]
    assert (uncertain["state"], uncertain["reason"], uncertain["sweep_side"]) == ("UNCERTAIN","STRADDLING_OBSERVATION",None)
    assert uncertain["revisions"][0] == formation
    assert receipt(client,key,release,**options)["selected"] == active
    assert query(client,key,START+7201).status_code == 422
    assert query(client,key,release,view="2").status_code == 422
    assert query(client,key,release,range_id="bad").status_code == 422
    (storage.MARKET_DIR/pin["relative_path"]).write_bytes(b"controlled corruption")
    failed = query(client,key,release,**options)
    assert failed.status_code == 409 and pin["id"] in failed.json()["detail"]


@pytest.mark.parametrize("quiet_close,successor,expected", [
    (0, 0, "SCHEDULED_CLOSURE"),
    (5, 5, "MISSING_OPEN_CANDLES"),
    (0, -60, "UNVERIFIABLE_CLOSURE"),
])
def test_weekend_gap_boundaries_and_rewind(client,quiet_close,successor,expected):
    friday = int(datetime(2025,11,14,22,tzinfo=UTC).timestamp())
    sunday = int(datetime(2025,11,16,22,tzinfo=UTC).timestamp())
    # Retain a completed 20:00 H1 and quiet observations up to Friday's frontier.
    start = friday-7200
    count = (friday - quiet_close*60 - start)//STEP
    values = prices([REF]*12 + [CENTER]*(count-12),start=start)
    previous = values[-1]["time"]
    after = sunday + successor*60
    known = after+STEP+8
    next_row = prices([CENTER],start=after,arrivals={0:known})
    pins = [publish(values), publish([values[-1], *next_row])]
    key = session(client,start=start,end=known+STEP)
    family = next(row for row in receipt(client,key,known-1)["ranges"] if row["reference_start"] == start)
    options = {"range_id":family["range_id"]}
    prior = receipt(client,key,known-1,**options)["selected"]
    selected = receipt(client,key,known,**options)["selected"]
    if expected == "SCHEDULED_CLOSURE":
        assert prior == selected and family["state"] == "ACTIVE"
    else:
        last = selected["revisions"][-1]
        assert (last["state"],last["reason"],last["available_at"]) == ("INVALIDATED",expected,known)
        gap = last["continuity"]
        assert (gap["previous_observed_open"],gap["next_observed_open"],gap["step_seconds"]) == (previous,after,STEP)
        assert gap["schedule"] == receipt(client,key,known)["rules"]["crt"]["market_schedule"]
        intervals = None if expected == "UNVERIFIABLE_CLOSURE" else [
            {"start":previous+STEP,"end":friday},{"start":sunday,"end":after}]
        assert gap["missing_open_intervals"] == intervals
        before_row, next_row = selected["evidence"][-2:]
        assert (before_row["time"],next_row["time"]) == (previous,after)
        assert [r["id"] for r in before_row["partitions"]] == sorted(p["id"] for p in pins)
        assert {"role":"GAP_PREVIOUS_OBSERVED","event_id":last["id"]} in before_row["roles"]
        assert {"role":"GAP_NEXT_OBSERVED","event_id":last["id"]} in next_row["roles"]
        assert receipt(client,key,known-1,**options)["selected"] == prior


def test_age_and_replacement_include_triggering_observations(client):
    # A candidate close outside the H1 never upgrades on the later inside candle.
    publish(prices([REF]*12 + [(104,106,103,106)] + [CENTER]*3))
    key = session(client)
    end = START+16*STEP
    summary, = receipt(client,key,end)["ranges"]
    detail = receipt(client,key,end,range_id=summary["range_id"])["selected"]
    assert [r["reason"] for r in detail["revisions"]] == ["CLOSED_REFERENCE","NO_SAME_CANDLE_RECLAIM","CANDIDATE_AGE"]
    counted = [c["time"] for c in detail["evidence"] if {"role":"AGE_OBSERVATION","event_id":summary["id"]} in c["roles"]]
    assert counted == [START+i*STEP for i in (13,14,15)]

    publish(prices([REF]*36), "GBPUSD")
    end = START + 36*STEP
    key = session(client,end=end,symbol="GBPUSD")
    page = receipt(client,key,end)
    replaced = next(row for row in page["ranges"] if row["reason"] == "REFERENCE_CAP_REPLACEMENT")
    replacement = next(row for row in page["ranges"] if row["reference_start"] == START+24*STEP)
    assert replaced["replacement_reference"] == replacement["reference_id"]
    detail = receipt(client,key,end,range_id=replaced["range_id"])["selected"]
    assert [c["time"] for c in detail["evidence"][12:]] == [START+i*STEP for i in range(24,36)]
    assert all(c["roles"] == [{"role":"REPLACEMENT_REFERENCE_CHILD","event_id":replaced["id"]}]
               for c in detail["evidence"][12:])


def test_missing_weekday_bar_confirms_gap_only_when_successor_arrives(client):
    values = prices([REF]*12 + [CENTER]*3)
    missing, previous = values[13]["time"], values[12]["time"]
    successor = values[14]["time"]
    known = successor + STEP + 11
    values[14]["available_at"] = known
    publish([*values[:13], values[14]])
    key = session(client,end=known+STEP)
    active, = receipt(client,key,known-1)["ranges"]
    options = {"range_id":active["range_id"]}
    prior = receipt(client,key,known-1,**options)["selected"]
    assert prior["revision"] == 1
    terminal = receipt(client,key,known,**options)["selected"]
    gap = terminal["revisions"][-1]
    assert gap["state"] == "INVALIDATED" and gap["reason"] == "MISSING_OPEN_CANDLES"
    assert gap["continuity"]["missing_open_intervals"] == [{"start":missing,"end":successor}]
    assert [row["time"] for row in terminal["evidence"][-2:]] == [previous,successor]
    assert all(row["time"] != missing for row in terminal["evidence"])
    assert receipt(client,key,known-1,**options)["selected"] == prior


def test_incomplete_hour_never_releases_range_and_reference_age_counts_quiet_bars(client):
    values = prices([REF]*12)
    publish(values[:4]+values[5:])
    key = session(client)
    assert receipt(client,key,START+3600)["ranges"] == []

    # Zero-width later H1s cannot replace the original reference.
    quiet = (103,103,103,103)
    publish(prices([REF]*12 + [quiet]*96),"GBPUSD")
    end = START + 108*STEP
    key = session(client,end=end,symbol="GBPUSD")
    final, = receipt(client,key,end)["ranges"]
    options = {"range_id":final["range_id"]}
    terminal = receipt(client,key,end,**options)["selected"]
    assert [r["reason"] for r in terminal["revisions"]] == ["CLOSED_REFERENCE","REFERENCE_AGE"]
    counted = [row["time"] for row in terminal["evidence"]
               if {"role":"AGE_OBSERVATION","event_id":final["id"]} in row["roles"]]
    assert counted == [START+i*STEP for i in range(12,108)]
    assert receipt(client,key,end-1,**options)["selected"]["state"] == "ACTIVE"
