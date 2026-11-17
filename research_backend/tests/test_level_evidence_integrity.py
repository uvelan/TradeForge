"""Independent examples for canonical level evidence and archive corrections."""
from datetime import UTC, datetime

import pytest

from research_backend.app.analysis_evidence import ResourceLimit
from research_backend.app.indicator_coverage import DAY, WEEK, period_start
from research_backend.app.indicator_engine import Evaluation, EvidenceBar, evaluate
from test_analysis_evidence import by_id, configured_request
from test_crt import START, payload


PERIODS = {"previous_day": DAY, "previous_week": WEEK}
SCHEDULE = {"kind": "continuous"}
LEVELS = ["session_asia", "session_london", "session_new_york", *PERIODS]
MANIFEST = [{"id": "primary", "sha256": "a" * 64}]


def case(level, *, custom=False, policy="shared"):
    data = configured_request(policy)
    spec = {"id": level}
    if level in PERIODS:
        spec["schedule"] = SCHEDULE
    elif custom:
        spec["session"] = {"timezone": "UTC", "start": "10:00", "end": "11:00"}
    data["indicators"].append(spec)
    return data


def results(data, loader=None):
    return by_id(evaluate(Evaluation.model_validate(data), loader))


def archive(*, changed=None, checksum="b"*64, delay=0, status=None):
    def load(request, start, end, step):
        if status:
            return {"rows": [], "evidence": {"status": status, "reason": "fixture",
                                               "context_authority": "UNAVAILABLE"}}
        rows = []
        for index, stamp in enumerate(range(start, end, step)):
            bar = EvidenceBar(time=stamp, open=1.1030, high=1.1050, low=1.1010, close=1.1030)
            if index == 1 and changed:
                bar = bar.model_copy(update=changed)
            rows.append((bar, stamp+step+(delay if index == 0 else 0)))
        return {"rows": rows, "evidence": {"context_authority": "CHECKSUM_VERIFIED_ARCHIVE",
                "context_partitions": [{"id": "history", "sha256": checksum}]}}
    return load


def saved_case(level):
    data = case(level)
    data.update(source="MT5_SAVED", feed_id="test-feed", price_basis="BID",
                frozen=True, partitions=MANIFEST, load_reference=True)
    return data


@pytest.mark.parametrize("level,custom", [(level, False) for level in LEVELS]+[("session_asia",True)])
def test_shared_levels_reuse_exact_observation_contract(level, custom):
    data = saved_case(level) if level in PERIODS else case(level,custom=custom)
    if level not in PERIODS and not custom:
        start = int(datetime(2026,11,1,tzinfo=UTC).timestamp())
        count = (START-start)//300
        data["candles"] = payload([(102,104,101,103)]*count,start=start)["candles"]+data["candles"]
    outcome = results(data, archive())
    crt = outcome["candle_range_theory"]
    standalone = outcome[level]["analysis"]
    assert crt["context_configuration"]["dependency_snapshots"][level] == standalone["calculation_snapshot"]
    key = level+"_high"
    references = [event["context"][key] for event in crt["crt_sweeps"] if key in event["context"]]
    assert references
    observations = {item["id"]:item for item in standalone["level_observations"]}
    for ref in references:
        assert ref == observations[ref["observation_id"]]
        assert ref["configuration_fingerprint"] == standalone["configuration_fingerprint"]
        assert ref["available_at"] >= ref["confirmation_time"]
        assert ref["price"] == (1.105 if level in PERIODS else 105 if custom else 104)


def test_independent_context_keeps_its_own_session_configuration():
    outcome = results(case("session_asia",custom=True,policy="independent"))
    crt = outcome["candle_range_theory"]
    assert crt["context_configuration"]["levels"]["session_asia"]["origin"] == "baseline"
    assert crt["context_configuration"]["dependency_snapshots"]["session_asia"] != outcome["session_asia"]["analysis"]["calculation_snapshot"]


@pytest.mark.parametrize("level,label,changed,expected", [
    ("previous_day","High",{"high":1.1070},1.1070),
    ("previous_week","Low",{"low":1.0990},1.0990),
    ("previous_day","High",{"close":1.1035},1.1050),
    ("previous_week","Low",{"close":1.1035},1.1010),
])
def test_archive_correction_revises_evidence_even_with_same_prices(level,label,changed,expected):
    data = saved_case(level)
    old = results(data,archive())[level]
    new = results(data,archive(changed=changed))[level]
    end = period_start(data["cutoff"], PERIODS[level])
    before,after = [next(item for item in obj["analysis"]["level_observations"]
                         if item["label"] == label and item["reference_end"] == end) for obj in (old,new)]
    assert new["series"][label][-1]["value"] == after["price"] == expected
    assert before["configuration_fingerprint"] == after["configuration_fingerprint"]
    for field in ("id","evidence_fingerprint","calculation_snapshot"):
        assert before[field] != after[field]
    assert after["observation_authority"] == "CLIENT_SUPPLIED_UNVERIFIED"
    assert after["reference_authority"] == "CHECKSUM_VERIFIED_ARCHIVE"
    assert after["authority"] == "MIXED_OBSERVATION_AND_REFERENCE"
    assert after["context_partitions"] == [{"id":"history","sha256":"b"*64}]


def test_independent_snapshot_identity_inputs():
    level = "previous_day"
    data = saved_case(level)
    before = results(data,archive())[level]["analysis"]
    checksum = results(data,archive(checksum="c"*64))[level]["analysis"]
    for params in ({"partitions":[{"id":"other","sha256":"d"*64}]},
                   {"feed_id":"other-feed"}, {"price_basis":"MID"},
                   {"candles":[{**data["candles"][0],"close":103.5},*data["candles"][1:]]}):
        after = results({**data,**params},archive())[level]["analysis"]
        assert before["configuration_fingerprint"] == after["configuration_fingerprint"]
        assert before["calculation_snapshot"] != after["calculation_snapshot"]
        expected_same = "feed_id" not in params and "price_basis" not in params
        assert (before["level_observations"][0]["id"]==after["level_observations"][0]["id"]) == expected_same
    assert before["level_observations"][0]["id"] != checksum["level_observations"][0]["id"]


@pytest.mark.parametrize("failure", ["REFERENCE_ABSENT","SOURCE_INTEGRITY_FAILURE","HISTORY_LIMIT"])
def test_optional_failed_reference_never_borrows_an_older_level(failure):
    level = "previous_day"
    data = saved_case(level)
    start = period_start(START,DAY)-2*DAY
    data["candles"] = payload([(102,104,101,103)]*((START-start)//300),start=start)["candles"]+data["candles"]

    def limited(*args):
        raise ResourceLimit("fixture budget")

    outcome = results(data,limited if failure=="HISTORY_LIMIT" else archive(status=failure))
    crt = outcome["candle_range_theory"]
    assert crt["context_configuration"]["failures"][level]["status"] == failure
    assert crt["crt_sweeps"][-1]["state"] == "HIGH_SWEPT_RECLAIMED"
    assert level+"_high" not in crt["crt_sweeps"][-1]["context"]
    assert outcome[level]["analysis"]["level_observations"]
    assert all(item["valid_until"] <= START for item in outcome[level]["analysis"]["level_observations"])


def test_delayed_archive_availability_and_corrected_context_cursor():
    level = "previous_day"
    data = saved_case(level)
    start = START - 3*3600
    data["candles"] = payload([(102,105,100,103)]*36,start=start)["candles"]+data["candles"]
    data["indicators"][0]["crt_delivery"] = {"formation_limit":1}
    end = period_start(data["cutoff"],DAY)
    delay = 11*3600+1+DAY-300
    first = results(data,archive(delay=delay))
    reference = first[level]["analysis"]["level_observations"][0]
    assert reference["confirmation_time"] == end
    assert reference["available_at"] == end+11*3600+1
    crt = first["candle_range_theory"]
    assert all(level+"_high" not in item["context"]
               for item in crt["crt_sweeps"] if item["available_at"] < reference["available_at"])
    cursor = crt["crt_page"]["formations"]["next_cursor"]
    assert cursor
    data["indicators"][0]["crt_delivery"]["formation_cursor"] = cursor
    with pytest.raises(ValueError,match="evidence changed"):
        results(data,archive(delay=delay,changed={"close":1.1035}))
