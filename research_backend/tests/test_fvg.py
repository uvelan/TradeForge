import pytest

from research_backend.app.fvg import FVGDelivery, FVGRules, evaluate_fvg
from research_backend.app.indicators import Bar
from research_backend.app.structure_pages import StaleStructureCursor


ABC = [(99, 100, 98, 99), (100, 106, 100, 105), (105, 110, 105, 108)]


def run(values=ABC, *, cutoff=None, times=None, delays=None, rules=None, delivery=None, provenance=None):
    times = times if times is not None else [60 * i for i in range(len(values))]
    cutoff = times[-1] + 60 if cutoff is None else cutoff
    delays = delays or {}
    rows = [
        (Bar(time=t, open=o, high=h, low=l, close=c), t + 60 + delays.get(t, 0))
        for t, (o, h, l, c) in zip(times, values)
        if t + 60 + delays.get(t, 0) <= cutoff
    ]
    return evaluate_fvg(rows, 60, cutoff, FVGRules(**(rules or {})),
                        FVGDelivery(**(delivery or {})), provenance or {"feed_id": "fixture"}, False)


@pytest.mark.parametrize("bearish", [False, True])
def test_exact_wick_bounds_and_monotonic_depth(bearish):
    values = ABC + [(106, 107, 103.75, 106), (106, 107, 103.75, 106),
                    (106, 107, 102.5, 106), (106, 107, 100, 105)]
    if bearish:
        values = [(210-o, 210-l, 210-h, 210-c) for o,h,l,c in values]
    result = run(values)
    zone = result["formations"][0]
    assert (zone["lower"], zone["upper"]) == ((105, 110) if bearish else (100, 105))
    assert zone["direction"] == ("BEARISH" if bearish else "BULLISH")
    events = [r for r in result["revisions"] if r["fvg_id"] == zone["fvg_id"]]
    assert [(r["observation_time"], r["state"], r["mitigation_pct"]) for r in events] == [
        (180, "PARTIALLY_MITIGATED", 25), (300, "PARTIALLY_MITIGATED", 50),
        (360, "FULLY_MITIGATED", 100)]
    assert all(z["fvg_id"] != zone["fvg_id"] for z in result["current_fvgs"]["items"])


@pytest.mark.parametrize("rules,count", [
    ({"min_price": 5}, 1), ({"min_price": 5.0001}, 0),
    ({"min_bps": 500}, 1), ({"min_bps": 500.01}, 0),
    ({"min_ticks": 10, "tick_size": .5, "tick_size_reference": "fixture"}, 1),
    ({"min_ticks": 11, "tick_size": .5, "tick_size_reference": "fixture"}, 0),
    ({"min_ticks": 1}, 0),
    ({"atr_period": 2, "atr_multiple": 1}, 1),
    ({"atr_period": 2, "atr_multiple": 2}, 0),
    ({"atr_period": 4, "atr_multiple": 1}, 0),
    ({"displacement": "directional_body_bps_v1", "min_body_bps": 500}, 1),
    ({"displacement": "directional_body_bps_v1", "min_body_bps": 501}, 0),
])
def test_thresholds(rules, count):
    result = run(rules=rules)
    assert len(result["formations"]) == count
    if rules.get("atr_period") == 2 and count:
        assert result["formations"][0]["atr_at_formation"] == 4.75


def test_touch_overlapping_and_incomplete_c_do_not_form():
    for low in (99, 100):
        assert not run(ABC[:2] + [(105, 110, low, 108)])["formations"]
    assert not run(cutoff=179)["formations"]


def test_actual_availability_forward_and_rewind_keep_identity():
    values = ABC + [(106, 107, 103.75, 106)]
    assert not run(values, cutoff=209, delays={120: 30})["formations"]
    original = run(values, cutoff=210, delays={120: 30})
    advanced = run(values, delays={120: 30})
    assert original["formations"] == advanced["formations"]
    assert not original["revisions"]
    assert advanced["revisions"][0]["mitigation_pct"] == 25
    assert original["formations"][0]["confirmation_time"] == 180
    assert original["formations"][0]["available_at"] == 210
    assert run(values, cutoff=210, delays={120: 30}) == original


@pytest.mark.parametrize("extra,rules,state,reason,depth", [
    ((99, 99, 98, 99), {}, "INVALIDATED", "GAP_THROUGH_UNOBSERVED", 0),
    ((106, 107, 98, 99), {}, "INVALIDATED", "CLOSE_BEYOND_OPPOSITE_BOUNDARY", 100),
    ((106, 107, 101, 104), {"mitigation": "close"}, "PARTIALLY_MITIGATED", "OBSERVED_DEPTH", 20),
    ((106, 107, 101, 104), {"max_age_bars": 1}, "EXPIRED", "MAX_AGE", 0),
])
def test_terminal_and_close_mode(extra, rules, state, reason, depth):
    result = run(ABC + [extra], rules=rules)
    event = result["revisions"][0]
    assert (event["state"], event["reason"], event["mitigation_pct"]) == (state, reason, depth)


def test_missing_candle_invalidates_book_and_restarts_formation():
    result = run(ABC + [(106, 107, 106, 107)], times=[0, 60, 120, 240])
    assert result["revisions"][0]["state"] == "INVALIDATED"
    assert result["revisions"][0]["mitigation_pct"] == 0
    assert len(result["formations"]) == 1
    assert not run(ABC, times=[0, 60, 180])["formations"]


def test_history_limits_keep_active_book_and_stale_cursor_fails():
    values = ABC + [(n, n+1, n, n+1) for n in range(112, 160, 2)]
    first = run(values, delivery={"formation_limit": 2})
    cursor = first["fvg_page"]["formations"]["next_cursor"]
    older = run(values, delivery={"formation_limit": 2, "formation_cursor": cursor})
    assert not ({r["id"] for r in first["formations"]} & {r["id"] for r in older["formations"]})
    assert first["current_fvgs"] == older["current_fvgs"]
    assert first["current_fvgs"]["total"] > first["current_fvgs"]["returned"]
    with pytest.raises(StaleStructureCursor):
        run(values[:-1]+[(160,161,160,161)], delivery={"formation_cursor": cursor})


def test_flat_input_never_forms_fvg():
    assert run([(100, 100, 100, 100)] * 2000)["formations"] == []


def test_scheduled_closure_preserves_active_book_but_resets_pattern():
    from datetime import UTC, datetime

    friday = int(datetime(2026, 11, 6, 21, 57, tzinfo=UTC).timestamp())
    sunday = int(datetime(2026, 11, 8, 22, 0, tzinfo=UTC).timestamp())
    schedule = {"kind": "forex_weekly", "timezone": "America/New_York",
                "sunday_open": "17:00", "friday_close": "17:00"}
    result = run(ABC + [(112, 114, 112, 113)], times=[friday, friday+60, friday+120, sunday],
                 rules={"market_schedule": schedule})
    assert len(result["formations"]) == 1
    assert not result["revisions"]
    assert result["current_fvgs"]["items"][0]["c_time"] == friday+120


def test_verified_partitions_and_corruption_with_stale_cursor(client):
    from research_backend.app import storage

    start = 1793577600
    values = ABC + [(n,n+1,n,n+1) for n in (112,114,116,118)]
    rows = [dict(time=start+60*i, open=o, high=h, low=l, close=c)
            for i,(o,h,l,c) in enumerate(values)]
    parts = [storage.write_partition("MT5","fvg_fixture","EURUSD","candles","M1","BID", piece)
             for piece in (rows[:2], rows[2:])]
    data = dict(source="MT5_SAVED",symbol="EURUSD",timeframe="M1",feed_id="fvg_fixture",
                price_basis="BID",cutoff=start+len(rows)*60,frozen=True,candles=[],
                partitions=[{k:part[k] for k in ("id","sha256")} for part in parts],
                indicators=[dict(id="fair_value_gap",fvg={"history_bars":10000,"history_start":start},
                                 fvg_delivery={"formation_limit":1})])
    response = client.post("/api/indicators/evaluate", json=data)
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["provenance"]["context_authority"] == "CHECKSUM_VERIFIED_ARCHIVE"
    assert any(z["c_time"] == start+120 and (z["lower"],z["upper"]) == (100,105)
               for z in result["current_fvgs"]["items"])
    cursor = result["fvg_page"]["formations"]["next_cursor"]
    assert cursor
    path = storage.MARKET_DIR / parts[0]["relative_path"]
    path.write_bytes(path.read_bytes() + b"corrupt")
    refused = client.post("/api/indicators/evaluate", json=data).json()["results"][0]
    assert "SOURCE_INTEGRITY_FAILURE" in refused["warnings"]
    assert refused["current_fvgs"]["items"] == []
    data["indicators"][0]["fvg_delivery"]["formation_cursor"] = cursor
    assert client.post("/api/indicators/evaluate", json=data).status_code == 409


def test_overlapping_bullish_zones_update_on_same_candle():
    values = [(99,100,98,99),(100,103,100,102),(110,111,110,111),
              (112,114,112,113),(113,114,106,113)]
    result = run(values)
    first,second = result["formations"]
    assert (first["lower"], first["upper"], second["lower"], second["upper"]) == (100,110,103,112)
    assert second["overlaps"] == [{"id":first["fvg_id"],"direction":"SAME","geometry":"PARTIAL"}]
    updates = {event["fvg_id"]:event for event in result["revisions"]}
    assert len(updates) == 2
    for zone,depth in ((first,40),(second,100*6/9)):
        event = updates[zone["fvg_id"]]
        assert (event["state"],event["observation_time"],event["available_at"]) == (
            "PARTIALLY_MITIGATED", 240, 300)
        assert event["mitigation_pct"] == pytest.approx(depth)
    assert len({event["id"] for event in updates.values()}) == 2


def test_opposite_overlapping_zones_keep_independent_identities():
    values = [(99,100,98,99),(100,110,100,108),(110,111,110,111),
              (112,114,112,113),(110,111,109,109),(106,107,105,106)]
    result = run(values)
    bearish = next(event for event in result["formations"] if event["direction"] == "BEARISH")
    bullish = result["formations"][0]
    assert (bearish["lower"],bearish["upper"],bearish["c_time"]) == (107,112,300)
    assert bearish["overlaps"] == [{"id":bullish["fvg_id"],"direction":"OPPOSITE","geometry":"PARTIAL"}]
    assert bearish["fvg_id"] != bullish["fvg_id"]
    assert "signal" not in bearish and "volume" not in bearish


@pytest.mark.parametrize("times", [[0,60,60],[0,120,60],[0,30,60]])
def test_api_rejects_duplicate_out_of_order_or_overlapping_candles(client, times):
    bars = [dict(time=t, open=o, high=h, low=l, close=c)
            for t,(o,h,l,c) in zip(times,ABC)]
    response = client.post("/api/indicators/evaluate", json=dict(
        source="DEMO",symbol="EURUSD",timeframe="M1",cutoff=300,candles=bars,
        indicators=[{"id":"fair_value_gap"}]))
    assert response.status_code == 422


def test_old_active_zone_survives_zero_history_and_dense_rolling_revisions():
    cycle = [(119,120,118,119),(120,126,120,125),(125,130,125,128),
             (126,127,124,126),(126,127,122.5,126),(125,127,120,125)]
    values = ABC+cycle*50+[(106,107,105,106)]
    rules = {"max_age_bars":500}
    normal = run(values, rules=rules)
    zero = run(values, rules=rules, delivery={"formation_limit":0,"revision_limit":0})
    assert normal["fvg_page"]["revisions"]["total"] > 60
    assert zero["current_fvgs"] == normal["current_fvgs"]
    zone = next(event for event in zero["current_fvgs"]["items"] if event["c_time"] == 120)
    assert (zone["lower"],zone["upper"],zone["state"],zone["mitigation_pct"]) == (100,105,"ACTIVE",0)
    dense = run([(n,n+1,n,n+1) for n in range(100,4100,2)])
    assert dense["fvg_page"]["formations"]["total"] == 1998
    assert dense["current_fvgs"]["total"] == 250
    assert dense["current_fvgs"]["returned"] == 12
