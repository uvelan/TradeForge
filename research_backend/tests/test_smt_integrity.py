import pytest

from research_backend.app.causal_timing import strategy_eligible
from research_backend.app.strategy_evidence import EvidenceRegistry

from test_smt_causal import LEFT, RIGHT, archive, bars, event, query


@pytest.fixture
def smt_registry(monkeypatch):
    seen = {}
    calculate = EvidenceRegistry.calculation

    def capture(self, engine, config, source, history, cutoff):
        view = calculate(self, engine, config, source, history, cutoff)
        if engine == "smt":
            seen[view.snapshot] = view
        return view

    monkeypatch.setattr(EvidenceRegistry, "calculation", capture)
    return seen


def test_delayed_session_prefix_availability(client):
    body = archive(bars([10]*5+LEFT, delayed={0: 940}), bars([22]*5+RIGHT), end=1020)
    before = query(client, body, cutoff=999)
    assert not before["events"]
    arrival = event(query(client, body, cutoff=1000))
    after = event(query(client, body, cutoff=1020))
    assert (arrival["candidate_at"], arrival["confirmed_at"], after["confirmed_at"]) == (1000, None, None)


def test_canonical_uncertainty_matches_api(client, smt_registry):
    result = query(client, archive(bars(LEFT), bars(RIGHT, missing=(10,))))
    entry = event(result)
    confirmation = next(row for row in entry["revisions"] if row["state"] == "SMT_CONFIRMED")
    view = smt_registry[result["snapshot"]]
    assert strategy_eligible(confirmation, 720, registry=view)
    assert entry["current_eligible"] is False
    assert strategy_eligible(confirmation, 720, registry=view, current=True) is entry["current_eligible"]
    assert strategy_eligible(confirmation, 659, registry=view, current=True)
    assert not strategy_eligible(confirmation, 660, registry=view, current=True)
    assert strategy_eligible(confirmation, 720, registry=view)


@pytest.mark.parametrize(("delayed_left", "delayed_right"), [
    ({0: 940}, {}),
    ({}, {0: 940}),
    ({0: 910, 5: 640}, {1: 880}),
    ({}, {9: 400}),
])
def test_prefix_frontier_delays_activation_until_last_prerequisite(client, delayed_left, delayed_right):
    body = archive(bars([10]*5+LEFT+[11, 11], delayed=delayed_left),
                   bars([22]*5+RIGHT+[24, 24], delayed=delayed_right), end=1140)
    assert not query(client, body, cutoff=999)["events"]
    arrival = event(query(client, body, cutoff=1000))
    assert arrival["matching_window"]["available_at"] == arrival["candidate_at"] == 1000
    assert arrival["matching_window"]["coverage_prefix"]["start"] == 0
    assert arrival["matching_window"]["coverage_prefix"]["end"] == 840
    assert arrival["confirmed_at"] is None
    assert event(query(client, body, cutoff=1079))["confirmed_at"] is None
    complete = event(query(client, body, cutoff=1080))
    assert complete["confirmed_at"] == 1080
    assert complete["revisions"][1]["timing"]["observation_start"] == 1020
    assert complete["id"] == arrival["id"]
    assert complete["revisions"][0]["id"] == arrival["revisions"][0]["id"]
    assert complete["pivots"]["primary"]["current"]["price"] == 17


def test_delayed_quiet_slot_between_disjoint_matching_windows(client):
    left = [10]*23
    right = [22]*23
    left[3], left[12] = 14, 16
    right[3], right[12] = 29, 27
    body = archive(bars(left, delayed={7: 520}), bars(right), end=1380)
    assert not query(client, body, cutoff=999)["events"]
    observed = event(query(client, body, cutoff=1000))
    assert observed["matching_window"]["previous_available_at"] == 360
    assert observed["matching_window"]["available_at"] == 1000
    assert observed["confirmed_at"] is None


def test_delayed_unmatched_pivot_and_pre_activation_breach(client):
    left, right = [10]*23, [22]*23
    left[3], left[7], left[12] = 14, 15, 16
    right[3], right[12] = 29, 27
    original = archive(bars(left, delayed={9: 400}), bars(right), end=1380)
    assert not query(client, original, cutoff=999)["events"]
    observed = event(query(client, original, cutoff=1000))
    assert observed["pivots"]["primary"]["previous"]["time"] == 180
    assert observed["candidate_at"] == 1000
    right[15] = 34
    breached = archive(bars(left, delayed={9: 400}), bars(right), end=1380)
    assert not query(client, breached, cutoff=1000)["events"]


def test_zero_bar_confirmation_uses_corrected_frontier_and_horizon(client, smt_registry):
    body = archive(bars([10]*5+LEFT, delayed={0: 940}), bars([22]*5+RIGHT), end=1020, confirmation_bars=0)
    assert not query(client, body, cutoff=999)["events"]
    result = query(client, body, cutoff=1000)
    entry = event(result)
    assert (entry["candidate_at"], entry["confirmed_at"]) == (1000, 1000)
    confirmation = entry["revisions"][-1]
    view = smt_registry[result["snapshot"]]
    assert strategy_eligible(confirmation, 1000, registry=view, current=True)
    assert not strategy_eligible(confirmation, 1001, registry=view, current=True)
    assert strategy_eligible(confirmation, 1001, registry=view)


@pytest.mark.parametrize(("options", "deadline", "reason"), [
    ({"unrecorded": (10,)}, 660, "UNRECORDED_LAGGARD_ARRIVAL"),
    ({"delayed": {10: 35}}, 660, "MISSING_LAGGARD_OBSERVATION"),
])
def test_current_recovery_depends_on_actual_recorded_arrival(client, smt_registry, options, deadline, reason):
    body = archive(bars(LEFT), bars(RIGHT, **options))
    rewind = event(query(client, body, cutoff=659))
    gap_result = query(client, body, cutoff=deadline)
    gap = event(gap_result)
    assert rewind["current_eligible"] and gap["current_reason"] == reason
    assert gap["historically_eligible"] and not gap["current_eligible"]
    final = query(client, body, cutoff=720)
    advanced = event(final)
    confirmation = advanced["revisions"][1]
    view = smt_registry[final["snapshot"]]
    assert advanced["current_eligible"] is ("delayed" in options)
    assert strategy_eligible(confirmation, 659, registry=view, current=True)
    assert not strategy_eligible(confirmation, 694, registry=view, current=True)
    assert strategy_eligible(confirmation, 695, registry=view, current=True) is ("delayed" in options)
    assert strategy_eligible(confirmation, 720, registry=view)
    assert rewind["id"] == gap["id"] == advanced["id"]
    assert rewind["revisions"][1]["id"] == gap["revisions"][1]["id"] == confirmation["id"]
    assert event(query(client, body, cutoff=659))["revisions"] == rewind["revisions"]


def test_frozen_window_exhaustion_only_after_next_expected_close(client, smt_registry):
    body = archive(bars(LEFT), bars(RIGHT))
    assert event(query(client, body, cutoff=779))["current_eligible"]
    result = query(client, body, cutoff=780)
    entry = event(result)
    assert entry["current_reason"] == "FROZEN_EVIDENCE_WINDOW_ENDED"
    assert not entry["current_eligible"] and entry["historically_eligible"]
    view = smt_registry[result["snapshot"]]
    assert strategy_eligible(entry["revisions"][1], 779, registry=view, current=True)
    assert not strategy_eligible(entry["revisions"][1], 780, registry=view, current=True)


def test_gap_isolated_to_its_family_and_snapshot(client, smt_registry):
    left = LEFT[:9] + [11, 15, 11, 10, 11, 11, 11]
    right = RIGHT[:9] + [24, 28, 24, 23, 24, 24, 24]
    body = archive(bars(left), bars(right, missing=(13,)), end=960, confirmation_bars=0)
    result = query(client, body, cutoff=840)
    first, second = [row for row in result["events"] if row["direction"] == "bearish"]
    assert first["leader"] == "primary" and first["current_state"] == "UNCERTAIN"
    assert second["leader"] == "comparison" and second["current_state"] == "SMT_CONFIRMED"
    view = smt_registry[result["snapshot"]]
    assert not strategy_eligible(first["revisions"][1], 840, registry=view, current=True)
    assert strategy_eligible(second["revisions"][1], 840, registry=view, current=True)
    before = query(client, body, cutoff=839)
    earlier = event(before)
    assert earlier["current_eligible"]
    assert strategy_eligible(earlier["revisions"][1], 839, registry=smt_registry[before["snapshot"]], current=True)
    assert not strategy_eligible(earlier["revisions"][1], 839, registry=view)


def test_missing_non_laggard_confirmation_stays_pending(client, smt_registry):
    result = query(client, archive(bars(LEFT, missing=(9,)), bars(RIGHT)))
    entry = event(result)
    assert entry["confirmed_at"] is None
    assert entry["current_state"] == "SMT_CANDIDATE"
    assert entry["current_reason"] == "PENDING_SHARED_CONFIRMATION"
    assert not entry["current_eligible"]
    assert not strategy_eligible(entry["revisions"][0], 720, registry=smt_registry[result["snapshot"]])
