"""Offline recorded STOP entries over checksum-pinned generated BI5 observations."""
import pytest

from research_backend.app.offline_market_entry import validate_market_entries
from test_bounded_crt_smt_successors import canonical_successors
from test_offline_market_entry import publish, source_records
from test_recorded_limit_entry import QUAL, bi5, count
from test_recorded_market_entry import BASE, controlled, prepare, shard

pytestmark = pytest.mark.parametrize("controlled", [(BASE+3600, 90)], indirect=True)


@pytest.mark.parametrize("direction,threshold,side,miss,exact,gap", [
    ("bullish", 1.16274, "ASK", (116270,116273), (116271,116274), (116271,116276)),
    ("bearish", 1.16272, "BID", (116273,116276), (116272,116275), (116270,116275)),
])
@pytest.mark.parametrize("adverse_gap", [False, True])
def test_recorded_stop_uses_first_executable_side(controlled, direction, threshold, side, miss, exact, gap, adverse_gap):
    run, intent = controlled(kind="STOP", direction=direction, entry_price=threshold)
    before = source_records(run)
    bid, ask = gap if adverse_gap else exact
    spec, rows = prepare(bi5([(-1000,*miss), (200,*miss), (400,bid,ask), (4000,*exact)]), 14, QUAL)
    pieces = [shard(rows[:3]), shard(rows[1:])]
    direct, split, reverse = [validate_market_entries(run, recorded=spec | {"partitions": pins})
                              for pins in ([spec["source"]],pieces,pieces[::-1])]
    for response in (direct,split,reverse):
        r, = response["results"]
        assert response["simulated_orders"] == response["simulated_fills"] == 1
        assert r["identity"]["policy"]["id"] == "OFFLINE_RECORDED_FIXED_PRICE_STOP_ENTRY"
        assert r["status"] == "SIMULATED_FILLED" and r["source"] == intent
        assert r["order"]["trigger_side"] == side
        assert r["order"]["stop_price_units"] == (116274 if side=="ASK" else 116272)
        assert (r["fill"]["side"],r["fill"]["price_units"],r["fill"]["time_ms"],
                r["fill"]["quote"]["source_order"]) == (side,ask if side=="ASK" else bid,QUAL+400,2)
        assert r["fill"]["quote"]["time_ms"] == r["fill"]["quote"]["available_at_ms"] == QUAL+400
        assert r["recorded_evidence"]["last_inspected_priority"] == [QUAL+400,2]
    assert split == reverse == validate_market_entries(run, recorded=spec | {"partitions": pieces})
    assert count() == 2 and source_records(run) == before


@pytest.mark.parametrize("case,status", [
    ("trigger_miss","AMBIGUOUS_QUOTE_ORDER"),
    ("miss_trigger","AMBIGUOUS_QUOTE_ORDER"),
    ("later_conflict","SIMULATED_FILLED"),
    ("deadline_touch","SIMULATED_FILLED"),
    ("expiry","VERIFIED_EXPIRED"),
    ("gap","INSUFFICIENT_QUOTE_COVERAGE"),
    ("omitted_then_conflict",None),
    ("changed_then_conflict",None),
    ("tie_changes_volume",None),
])
def test_recorded_stop_bounds_and_prefix(controlled, case, status):
    run, intent = controlled(kind="STOP", direction="bullish", entry_price=1.16274)
    before = source_records(run)
    trigger_ms = {"deadline_touch":90000,"expiry":90001,"gap":70000}.get(case,40000)
    points = [(-1000,116270,116273), (200,116270,116273)]
    if case in {"deadline_touch","expiry"}:
        points.append((45000,116270,116273))
    points += [(trigger_ms,116270,116276),(trigger_ms+1,116270,116276)]
    spec, rows = prepare(bi5(points),14,QUAL)
    anchor, miss = rows[:2]
    trigger, later = rows[-2:]
    earlier = rows[:-2]
    if case == "omitted_then_conflict":
        earlier = [anchor]
    elif case == "changed_then_conflict":
        earlier = [anchor,miss | {"ask_units":116272}]
    diagnostic = {
        "trigger_miss":trigger | {"ask_units":116273},
        "miss_trigger":miss | {"ask_units":116276},
        "later_conflict":later | {"ask_units":116273},
        "omitted_then_conflict":trigger | {"ask_units":116273},
        "changed_then_conflict":trigger | {"ask_units":116273},
        "tie_changes_volume":trigger | {"ask_volume":99.0},
    }.get(case,later)
    pieces = [shard([*earlier,trigger,later]),shard([diagnostic])]
    responses = []
    for layout in (pieces,pieces[::-1]):
        request = spec | {"partitions":layout}
        if status is None:
            with pytest.raises(ValueError,match="omits or adds|disagrees|source metadata"):
                validate_market_entries(run,recorded=request)
            assert count() == 0
            continue
        response = validate_market_entries(run,recorded=request)
        assert response == validate_market_entries(run,recorded=request)
        r, = response["results"]
        assert r["status"] == status and r["source"] == intent
        assert response["simulated_fills"] == int(status=="SIMULATED_FILLED")
        if status=="SIMULATED_FILLED":
            assert (r["fill"]["time_ms"],r["fill"]["price_units"])==(QUAL+trigger_ms,116276)
        else:
            assert r["fill"] is None
        responses.append(response)
    assert responses == [] or responses[0] == responses[1]
    assert count() == int(status is not None) and source_records(run) == before


def test_recorded_stop_rejects_fractional_tick(controlled):
    run,_ = controlled(kind="STOP",direction="bullish",entry_price=1.162741)
    spec,_ = prepare(bi5([(-1000,116270,116273),(200,116270,116276)]),14,QUAL)
    response = validate_market_entries(run,recorded=spec)
    r, = response["results"]
    assert r["status"] == "UNSUPPORTED_STOP_PRICE_GRID"
    assert r["order"] is r["fill"] is None
    assert response["simulated_orders"] == response["simulated_fills"] == 0
