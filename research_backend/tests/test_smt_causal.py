from copy import deepcopy
from datetime import UTC, datetime

import pytest

from research_backend.app.db import one
from research_backend.app.indicator_history import RECORDED_ARRIVAL
from research_backend.app.smt import divergence_leader, nearest_peer, signed_change
from research_backend.app.storage import write_partition
from research_backend.app.smt_causal import _timed_event
from research_backend.app.strategy_evidence import EvidenceRegistry

URL = "/api/indicators/smt/strict"
LEFT = [10, 11, 14, 11, 10, 11, 16, 11, 10, 11, 11, 11]
RIGHT = [22, 24, 29, 24, 22, 24, 27, 24, 22, 24, 24, 24]


def bars(values, *, start=0, delayed=None, missing=(), unrecorded=(), partial=()):
    delayed = delayed or {}
    return [{"time": start+i*60, "available_at": start+(i+1)*60+delayed.get(i, 0),
             "availability_policy": "ASSUMED" if i in unrecorded else RECORDED_ARRIVAL,
             "closed": i not in partial, "open": x, "close": x, "high": x+1, "low": x-1}
            for i, x in enumerate(values) if i not in missing]


def archive(left, right, *, start=0, end=720, **rules):
    body = {"period": 2, "alignment_bars": 0, "cutoff": end, **rules}
    for side, symbol, sequence in zip(("primary", "comparison"), ("EURUSD", "GBPUSD"), (left, right)):
        part = write_partition("DUKASCOPY", f"clock-{symbol}", symbol, "candles", "M1", "BID", sequence)
        body[side] = {"source": "DUKASCOPY_SAVED", "feed_id": f"clock-{symbol}", "price_basis": "BID",
                      "symbol": symbol, "timeframe": "M1", "calendar": {"kind": "continuous"},
                      "partitions": [{"id": part["id"], "sha256": part["sha256"]}], "start": start, "end": end}
    return body


def query(client, body, **options):
    response = client.post(URL, json={**body, **options})
    assert response.status_code == 200, response.text
    return response.json()


def event(result, direction="bearish"):
    return next(entry for entry in result["events"] if entry["direction"] == direction)


@pytest.mark.parametrize(("left", "right", "kind", "prices"), [
    (LEFT, RIGHT, "bearish", (15, 17, 30, 28)),
    ([50-x for x in LEFT], [60-x for x in RIGHT], "bullish", (35, 33, 30, 32)),
    (RIGHT, LEFT, "bearish", (30, 28, 15, 17)),
])
def test_positive_divergence_preserves_wicks_and_exact_producers(client, left, right, kind, prices):
    entry = event(query(client, archive(bars(left), bars(right))), kind)
    points = [(entry["pivots"][side][point]["time"], entry["pivots"][side][point]["price"])
              for side in ("primary", "comparison") for point in ("previous", "current")]
    assert points == list(zip([120, 360, 120, 360], prices))
    assert (entry["candidate_at"], entry["confirmed_at"]) == (540, 600)
    assert entry["historically_eligible"] and entry["current_eligible"]
    confirmed = entry["revisions"][-1]
    links = confirmed["timing"]["dependency_references"]
    references = {key: ref for key, ref in links.items() if ref["category"] == "smt_pivot"}
    assert links["smt:match"] == entry["matching_window"]["attestation"]
    assert links["smt:previous"] == entry["revisions"][0]["evidence_reference"]
    assert sorted(ref["available_at"] for ref in references.values()) == [300, 300, 540, 540]
    reference = confirmed["evidence_reference"]
    for alias, producer in references.items():
        assert producer["source"] == reference["participants"][alias.split(":")[0]]
        assert producer["calculation_snapshot"] != reference["calculation_snapshot"]
        assert producer["source_authority"] == "CHECKSUM_VERIFIED_ARCHIVE"
    assert len({ref["calculation_snapshot"] for ref in references.values()}) == 2


@pytest.mark.parametrize("side", ["primary", "comparison"])
def test_delayed_constituent_and_post_activation_bar(client, side):
    kwargs = {side: {"delayed": {4: 15, 8: 10, 10: 25}}}
    body = archive(bars(LEFT, **kwargs.get("primary", {})), bars(RIGHT, **kwargs.get("comparison", {})))
    assert not query(client, body, cutoff=549)["events"]
    candidate = event(query(client, body, cutoff=550))
    assert candidate["candidate_at"] == 550
    assert candidate["pivots"][side]["previous"]["available_at"] == 315
    assert candidate["pivots"][side]["current"]["available_at"] == 550
    assert candidate["pivots"][side]["current"]["nominal_confirmation"] == 540
    assert event(query(client, body, cutoff=684))["confirmed_at"] is None
    confirmed = event(query(client, body, cutoff=685))
    assert confirmed["confirmed_at"] == 685
    assert confirmed["revisions"][-1]["timing"]["observation_start"] == 600


@pytest.mark.parametrize("condition", ["missing", "partial", "unrecorded"])
@pytest.mark.parametrize("side", ["primary", "comparison"])
def test_required_window_does_not_close_across_missing_partial_or_unrecorded_candle(client, condition, side):
    left = {condition: (7,)} if side == "primary" else {}
    right = {condition: (7,)} if side == "comparison" else {}
    result = query(client, archive(bars(LEFT, **left), bars(RIGHT, **right)))
    assert not result["events"] and not result["current"]
    expected = "UNVERIFIED_AVAILABILITY" if condition == "unrecorded" else "MISSING_OPEN_CANDLES"
    assert result["sources"][side]["coverage"]["status"] == expected


@pytest.mark.parametrize("index", [9, 10])
def test_laggard_only_invalidation_and_rewind(client, index):
    right = RIGHT.copy()
    right[index] = 32
    body = archive(bars(LEFT, missing=(index,)), bars(right))
    before = event(query(client, body, cutoff=(index+1)*60-1))
    after = event(query(client, body, cutoff=(index+1)*60))
    rewind = event(query(client, body, cutoff=(index+1)*60-1))
    assert after["revisions"][-1]["state"] == "SMT_INVALIDATED"
    assert after["revisions"][-1]["available_at"] == (index+1)*60
    assert not after["current_eligible"]
    assert after["historically_eligible"] is (index == 10)
    assert after["id"] == before["id"] == rewind["id"]
    assert before["revisions"] == rewind["revisions"]
    assert after["revisions"][0]["id"] == before["revisions"][0]["id"]


def test_unknown_laggard_gap_disables_current_without_removing_history(client):
    entry = event(query(client, archive(bars(LEFT), bars(RIGHT, missing=(10,)))))
    assert entry["historically_eligible"]
    assert entry["current_state"] == "UNCERTAIN"
    assert entry["current_reason"] == "MISSING_LAGGARD_OBSERVATION"
    assert entry["current_eligible"] is False


def test_expiry_and_late_confirmation_never_revives_expired_candidate(client):
    body = archive(bars(LEFT, delayed={9: 61}), bars(RIGHT), expiry_bars=2)
    entry = event(query(client, body))
    assert [state["state"] for state in entry["revisions"]] == ["SMT_CANDIDATE", "EXPIRED"]
    assert entry["confirmed_at"] is None
    assert entry["revisions"][-1]["available_at"] == 660
    assert not entry["current_eligible"]


def test_straddling_wick_excluded_from_post_activation_lifecycle(client):
    right = RIGHT.copy()
    right[9] = 40
    body = archive(bars(LEFT, delayed={8: 1}), bars(right))
    entry = event(query(client, body))
    assert entry["candidate_at"] == 541
    assert entry["confirmed_at"] is None
    assert entry["current_state"] == "UNCERTAIN"
    assert entry["current_reason"] == "AMBIGUOUS_ACTIVATION_WICK"


@pytest.mark.parametrize("relationship", ["INVERSE", "AUTO"])
def test_rejects_undefined_relationship(client, relationship):
    body = archive(bars(LEFT), bars(RIGHT), relationship=relationship)
    assert client.post(URL, json=body).status_code == 422


@pytest.mark.parametrize(("field", "value"), [("price_basis", "ASK"), ("timeframe", "M3"),
                                             ("calendar", {"kind": "forex_weekly"}), ("source", "MT5_SAVED")])
def test_incompatible_feed_policies_are_refused(client, field, value):
    body = archive(bars(LEFT), bars(RIGHT))
    body["comparison"][field] = value
    assert query(client, body)["reason"] == "INCOMPATIBLE_PROVIDER_PRICE_BASIS_TIMEFRAME_OR_CALENDAR"


def test_client_cannot_supply_availability_or_substitute_live_comparison(client):
    body = archive(bars(LEFT), bars(RIGHT))
    for key, value in [("candles", bars(RIGHT)), ("source", "MT5")]:
        invalid = {**body, "comparison": {**body["comparison"], key: value}}
        assert client.post(URL, json=invalid).status_code == 422


@pytest.mark.parametrize("side", ["primary", "comparison"])
def test_source_bytes_and_manifest_are_verified_for_each_feed(client, side):
    from research_backend.app import storage

    body = archive(bars(LEFT), bars(RIGHT))
    manifest = body[side]["partitions"][0]
    altered = deepcopy(body)
    altered[side]["partitions"][0]["sha256"] = "0"*64
    assert query(client, altered)["reason"] == "PARTITION_MANIFEST_MISMATCH"
    dataset = one("SELECT relative_path FROM datasets WHERE id=?", (manifest["id"],))
    path = storage.MARKET_DIR / dataset["relative_path"]
    path.write_bytes(path.read_bytes() + b"corrupt")
    assert query(client, body)["status"] == "SOURCE_INTEGRITY_FAILURE"


def test_duplicate_candles_are_refused(client):
    left = bars(LEFT)
    assert query(client, archive(left+[left[5]], bars(RIGHT)))["reason"] == "DUPLICATE_ARCHIVE_CANDLE"


def test_replay_cutoff_pagination_and_alert_isolation(client):
    body = archive(bars(LEFT), bars(RIGHT))
    journal = client.get("/api/indicators/smt/history").json()
    alerts = client.get("/api/indicators/smt/alerts").json()
    assert not query(client, body, cutoff=299)["events"]
    assert not query(client, body, cutoff=539)["events"]
    before = event(query(client, body, cutoff=599))
    after = event(query(client, body, cutoff=600))
    assert before["id"] == after["id"]
    assert before["confirmed_at"] is None and after["confirmed_at"] == 600
    page = query(client, body, limit=0)
    assert page["events"] == [] and page["delivery"]["total"] > 0
    assert page["current"] == query(client, body)["current"]
    assert page["alerts"] == query(client, body)["alerts"] == []
    assert client.get("/api/indicators/smt/history").json() == journal
    assert client.get("/api/indicators/smt/alerts").json() == alerts


def test_market_event_remains_stable_for_unrelated_rolling_prefix(client):
    start, prefix = 6000, 120
    left = bars([10]*prefix + LEFT, start=start)
    right = bars([22]*prefix + RIGHT, start=start)
    body = archive(left, right, start=start, end=start+(prefix+12)*60)
    original = event(query(client, body))
    for shift in range(1, 101):
        for side in ("primary", "comparison"):
            body[side]["start"] = start+shift*60
        rolled = event(query(client, body))
        assert rolled["id"] == original["id"]
        assert rolled["revisions"][0]["id"] == original["revisions"][0]["id"]
        assert rolled["revisions"][0]["evidence_reference"] != original["revisions"][0]["evidence_reference"]


def test_correction_of_nonextreme_wick_window_invalidates_snapshot_and_cursor(client):
    left = bars(LEFT[:9] + [11, 19, 11, 10, 11, 11, 11])
    right = bars(RIGHT[:9] + [24, 26, 24, 23, 24, 24, 24])
    body = archive(left, right, end=960)
    result = query(client, body, limit=1)
    cursor = result["delivery"]["next_cursor"]
    assert cursor
    older = query(client, body, limit=1, cursor=cursor)
    assert older["events"][0]["id"] != result["events"][0]["id"]
    assert older["current"] == result["current"]
    left[7] = {**left[7], "open": 11.25}
    corrected = archive(left, right, end=960)
    changed = query(client, corrected)
    assert changed["snapshot"] != result["snapshot"]
    assert older["events"][0]["id"] not in {entry["id"] for entry in changed["events"]}
    assert client.post(URL, json={**corrected, "cursor": cursor}).status_code == 409


def test_numerical_tolerances_and_peer_order():
    assert signed_change(101, 100, 1) == 100
    assert divergence_leader(100, -101, 100) is None
    assert divergence_leader(0, -1, 0) is None
    assert divergence_leader(1, 0, 0) is None
    assert divergence_leader(-101, 101, 100) == "comparison"
    assert nearest_peer([60, 180], 120, 60, 0) == 0
    assert nearest_peer([60, 180], 120, 60, 1) == 1
    assert nearest_peer([60, 180], 120, 59, 0) is None


def test_late_nearest_pivot_cannot_replace_a_provisional_match(client):
    left = [10] * 19
    left[4], left[11] = 14, 16
    right = [22] * 19
    right[4], right[10], right[13] = 29, 27, 26
    # The near pivot arrives after the farther pivot's right-hand confirmation.
    body = archive(bars(left), bars(right, delayed={10: 345}), end=1140, alignment_bars=2)
    assert not query(client, body, cutoff=1004)["events"]
    candidate = event(query(client, body, cutoff=1005))
    assert candidate["candidate_at"] == 1005
    assert candidate["matching_window"]["end"] == 960
    assert candidate["pivots"]["comparison"]["current"]["time"] == 600
    assert candidate["pivots"]["comparison"]["current"]["price"] == 28
    assert event(query(client, body, cutoff=1140))["id"] == candidate["id"]


def test_scheduled_dst_weekend_does_not_count_as_confirmation_bars(client):
    friday_close = int(datetime(2025, 3, 7, 22, tzinfo=UTC).timestamp())
    sunday_open = int(datetime(2025, 3, 9, 21, tzinfo=UTC).timestamp())
    start = friday_close-540
    left = bars(LEFT[:9], start=start)+bars([11, 11], start=sunday_open)
    right = bars(RIGHT[:9], start=start)+bars([24, 24], start=sunday_open)
    body = archive(left, right, start=start, end=sunday_open+120)
    for side in ("primary", "comparison"):
        body[side]["calendar"] = {"kind": "forex_weekly"}
    assert event(query(client, body, cutoff=sunday_open-1))["confirmed_at"] is None
    confirmed = event(query(client, body, cutoff=sunday_open+60))
    assert confirmed["candidate_at"] == friday_close
    assert confirmed["confirmed_at"] == sunday_open+60


def test_exact_aliases_isolate_identical_ids_and_non_smt_consumers():
    registry = EvidenceRegistry()
    scopes = {side: {"source": "DUKASCOPY_SAVED", "symbol": symbol, "feed_id": symbol,
                     "timeframe": "M1", "price_basis": "BID"}
              for side, symbol in (("primary", "EURUSD"), ("comparison", "GBPUSD"))}
    root_scope = {**scopes["primary"], "source": "SMT_PAIR", "symbol": "pair", "feed_id": "pair"}
    producers = {side: registry.calculation("smt-pivots", "p", scope, "data", 900)
                 for side, scope in scopes.items()}
    keys, refs = [], []
    for side, producer in producers.items():
        for label, end in (("local-a", 240), ("local-b", 480)):
            pivot = _timed_event(label, "CONFIRMED", end-120, end, end, end, 900, reason="ELIGIBLE")
            producer.publish(pivot, "smt_pivot", scopes[side], "p", predicate="FORMATION")
            keys.append(f"{side}:{label}")
            refs.append(pivot["evidence_reference"])

    def prepare(consumer):
        match = _timed_event("matching", "MATCH_CLOSED", 0, 480, 480, 480, 900, refs=keys, reason="RESEARCH_ONLY")
        match["coverage_prefix"] = {"start": 0, "previous_end": 240, "end": 480,
                                    "digest": "1"*64, "previous_digest": "2"*64}
        invalid_match = {**match, "id": "missing-coverage", "timing": dict(match["timing"])}
        consumer.publish(invalid_match, "smt_match", root_scope, "s", family="family", predicate="FORMATION",
                         producer_references=refs, lineage=True, coverage=False)
        consumer.publish(match, "smt_match", root_scope, "s", family="family", predicate="FORMATION",
                         producer_references=refs, lineage=True)
        candidate = _timed_event("candidate", "SMT_CANDIDATE", 0, 480, 480, 480, 900, refs=keys,
                                 reason="RESEARCH_ONLY")
        consumer.publish(candidate, "smt", root_scope, "s", family="family", predicate="FORMATION",
                         producer_references=refs, lineage=True, smt_links={"match": match["evidence_reference"]})
        return ({"match": match["evidence_reference"], "previous": candidate["evidence_reference"]},
                invalid_match["evidence_reference"])

    def confirm(consumer, references, name, barrier=480, links=None):
        signal = _timed_event(name, "SMT_CONFIRMED", 480, 540, 540, barrier, 900, refs=keys)
        signal["confirmation_bars"] = 1
        consumer.publish(signal, "smt", root_scope, "s", family="family", predicate="WHOLE_CANDLE",
                         producer_references=references, smt_links=links)
        return signal

    smt = registry.calculation("smt", "s", root_scope, "paired", 900)
    assert not registry.eligible(confirm(smt, refs, "unpinned"), 900)
    smt.pin_smt_producers(producers["primary"], producers["comparison"], scopes)
    links, invalid_match = prepare(smt)
    assert not registry.eligible(confirm(smt, refs, "forward", barrier=479, links=links), 900)
    assert not registry.eligible(confirm(smt, refs[:3]+[refs[1]], "swapped", links=links), 900)
    assert not registry.eligible(confirm(smt, refs, "missing-candidate", links={"match": links["match"]}), 900)
    assert not registry.eligible(confirm(smt, refs, "unauthorized-match", links={**links, "match": refs[0]}), 900)
    assert not registry.eligible(confirm(smt, refs, "unattested-match", links={**links, "match": invalid_match}), 900)
    other = registry.calculation("smt-pivots", "p", scopes["comparison"], "changed", 900)
    foreign = _timed_event("local-b", "CONFIRMED", 360, 480, 480, 480, 900, reason="ELIGIBLE")
    other.publish(foreign, "smt_pivot", scopes["comparison"], "p", predicate="FORMATION")
    assert not registry.eligible(confirm(smt, refs[:3]+[foreign["evidence_reference"]], "other-snapshot",
                                         links=links), 900)
    signal = confirm(smt, refs, "pinned", links=links)
    assert registry.eligible(signal, 540)
    assert not registry.eligible({**signal, "market_event_id": "forged"}, 900)
    assert not registry.eligible(signal, 539)
    assert not registry.eligible(confirm(smt, refs[:3]+[signal["evidence_reference"]], "cycle", links=links), 900)
    assert not EvidenceRegistry().eligible(signal, 900)
    normal = registry.calculation("fvg", "f", scopes["primary"], "data", 900)
    normal.pin_producer("smt_pivot", other)
    fvg = _timed_event("fvg", "ACTIVE", 480, 540, 540, 480, 900, refs=["local-b"], reason="ELIGIBLE")
    normal.publish(fvg, "fvg", scopes["primary"], "f", predicate="FORMATION",
                   producer_references=[foreign["evidence_reference"]])
    assert not registry.eligible(fvg, 900)
