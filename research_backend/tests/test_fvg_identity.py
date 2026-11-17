import pytest

from test_fvg import ABC, run


FLAT = (99, 100, 98, 99)
ABOVE = (106, 107, 106, 106)
PARTIAL = (106, 107, 103.75, 106)
INDEX = 1802
VALUES = [FLAT] * 1800 + ABC + [PARTIAL] + [ABOVE] * 196


def rolling(advance=0, values=None, **kwargs):
    data = (VALUES if values is None else values) + [ABOVE] * advance
    return run(data[-2000:], times=[i * 60 for i in range(advance, advance + 2000)],
               rules={"max_age_bars": 500, **kwargs.pop("rules", {})}, **kwargs)


def formation(result):
    return next(z for z in result["formations"] if z["c_time"] == INDEX * 60)


def revision(result):
    return next(z for z in result["current_fvgs"]["items"] if z["c_time"] == INDEX * 60)


def test_100_rolling_advances_keep_formation_partial_revision_and_book():
    assert len(VALUES) == 2000
    base = rolling()
    original = formation(base)
    revised = revision(base)
    assert original["identity_policy_version"] == revised["identity_policy_version"] == 2
    assert (original["lower"], original["upper"], original["available_at"]) == (100, 105, 108180)
    assert (revised["mitigation_pct"], revised["remaining_upper"], revised["available_at"]) == (25, 103.75, 108240)
    assert original["fvg_id"] == revised["fvg_id"]
    for advance in range(1, 101):
        result = rolling(advance)
        assert formation(result)["id"] == original["id"]
        assert revision(result)["id"] == revised["id"]
        assert result["current_fvgs"]["items"] == base["current_fvgs"]["items"]
        assert result["fvg_page"]["snapshot"] != base["fvg_page"]["snapshot"]


def test_delivery_limits_keep_identity_and_archive_window_options_are_retrieval_only():
    original = rolling()
    restricted = rolling(delivery={"formation_limit": 0, "revision_limit": 0, "current_limit": 1})
    assert not restricted["formations"] and not restricted["revisions"]
    assert restricted["current_fvgs"]["items"][0]["id"] == revision(original)["id"]
    extended = rolling(rules={"history_bars": 10000, "history_start": 0})
    assert formation(extended)["id"] == formation(original)["id"]
    assert revision(extended)["id"] == revision(original)["id"]
    assert extended["fvg_page"]["snapshot"] != original["fvg_page"]["snapshot"]


def test_rewind_and_cutoff_change_snapshot_without_rewriting_earlier_identity():
    ready = rolling(cutoff=108180)
    later = rolling(cutoff=108240)
    assert not rolling(cutoff=108179)["formations"]
    assert ready["revisions"] == []
    assert formation(ready)["id"] == formation(later)["id"] == formation(rolling())["id"]
    assert revision(later)["id"] == revision(rolling())["id"]
    assert rolling(cutoff=108180) == ready
    assert rolling(cutoff=108240) == later


@pytest.mark.parametrize("index,value,changes_formation,changes_revision", [
    (1, (99, 100, 97, 99), False, False),
    (1800, (99, 100, 97, 99), True, True),
    (1801, (100, 106, 100, 104), True, True),
    (1802, (105, 110, 105, 107), True, True),
    (1803, (106, 107, 103.75, 105), False, True),
])
def test_exact_market_corrections(index, value, changes_formation, changes_revision):
    old = rolling()
    updated = VALUES.copy()
    updated[index] = value
    new = rolling(values=updated)
    assert (formation(old)["id"] != formation(new)["id"]) == changes_formation
    assert (revision(old)["id"] != revision(new)["id"]) == changes_revision
    assert (formation(old)["input_fingerprint"] != formation(new)["input_fingerprint"]) == changes_formation
    assert old["fvg_page"]["snapshot"] != new["fvg_page"]["snapshot"]


def test_atr_warmup_correction_changes_true_ranges_calculation_and_identity():
    values = [(99,100,98,99)] * 3 + ABC
    corrected = [(99,102,96,99), *values[1:]]
    before = run(values, rules={"atr_period": 2, "atr_multiple": 1})["formations"][-1]
    after = run(corrected, rules={"atr_period": 2, "atr_multiple": 1})["formations"][-1]
    assert (before["c_time"], after["c_time"]) == (300, 300)
    assert (before["atr_at_formation"], after["atr_at_formation"]) == (4.75, 4.875)
    assert before["fvg_id"] != after["fvg_id"]
    assert run(corrected, rules={"atr_period": 2, "atr_multiple": 1.04})["formations"] == []


def test_dataset_price_basis_configuration_and_original_availability_are_identity_inputs():
    market = {"source":"MT5_SAVED", "feed_id":"verified", "dataset_id":"archive-A", "price_basis":"BID"}
    base = run(provenance=market)["formations"][0]
    alternatives = [
        run(provenance={**market, "dataset_id":"archive-B"}),
        run(provenance={**market, "price_basis":"ASK"}),
        run(provenance=market, rules={"mitigation":"close"}),
        run(provenance=market, delays={120:30}, cutoff=210),
    ]
    assert len({base["id"], *(result["formations"][0]["id"] for result in alternatives)}) == 5
    delayed = alternatives[-1]["formations"][0]
    assert (delayed["confirmation_time"], delayed["available_at"]) == (180, 210)


def test_equal_boundaries_at_distinct_times_are_distinct_zones():
    values = ABC + [(106,107,100,105), FLAT, FLAT] + ABC
    zones = [event for event in run(values)["formations"]
             if (event["lower"],event["upper"],event["direction"]) == (100,105,"BULLISH")]
    assert [event["c_time"] for event in zones] == [120, 480]
    assert len({event["fvg_id"] for event in zones}) == 2
