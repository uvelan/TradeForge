"""Adversarial ordering and compatibility checks for offline management."""
import json
from pathlib import Path
from itertools import count
from uuid import UUID
import pytest
from test_bounded_crt_smt_successors import canonical_successors
from research_backend.app.db import transaction
from test_offline_first_exit import calculate, position, snapshots
from test_offline_market_entry import Q, publish, quote, validate
from test_offline_trailing_stop import trailing_case, replay as trail
from test_offline_partial_exit import partial_case, replay as partial
from test_offline_time_exit import time_case, DEADLINE, COVERED

def test_second_tighten_before_same_ms_breach(trailing_case):
    case=trailing_case
    _,_,entry,first,_,_,stop,_,side=case
    if side=="BID":
        tighten=quote(Q+3,Q+3,7,110420,110425)
        hit=quote(Q+3,Q+3,8,110365,110375)
        closest=stop+20
    else:
        tighten=quote(Q+3,Q+3,7,110175,110180)
        hit=quote(Q+3,Q+3,8,110225,110235)
        closest=stop-20
    for rows in ([entry,first,tighten,hit],[hit,first,entry,tighten]):
        row,n=trail(case,rows,3)
        assert n==1
        assert row["exit"]["threshold_units"]==closest
        assert row["ratchet"]["quote"]["source_order"]==7
        assert row["exit"]["quote"]["source_order"]==8

def test_ambiguous_terminal_after_verified_partial(partial_case):
    c=partial_case
    _,_,entry,reduce,stop,_,_,side=c
    neutral=stop | ({"bid_units":110215} if side=="BID" else {"ask_units":110405})
    for rows in ([entry,reduce,stop,neutral],[neutral,stop,reduce,entry]):
        r,n,full=partial(c,rows,4)
        assert (r["status"],n,full)==("AMBIGUOUS_QUOTE_ORDER",1,0)
        assert r["remaining_fraction"]=={"numerator":7,"denominator":8}
        assert r["partial_exit"]["quote"]==reduce | {"scale":100000}

def test_duplicate_deadline_then_later_price_stop(time_case):
    c=time_case
    _,_,entry,side,_=c
    when=Q+DEADLINE
    expires=quote(when,when,8,110310,110315)
    later=quote(when,when,9,110210,110220) if side=="BID" else quote(when,when,9,110405,110410)
    for rows in ([entry,expires,expires,later],[later,expires,entry,expires]):
        r,n=trail(c,rows,DEADLINE,COVERED)
        assert n==1
        assert r["exit"]["reason"]=="TIME_EXIT"
        assert r["exit"]["quote"]["source_order"]==8


LEGACY_BASELINE = (Path(__file__).resolve().parents[2] / "docs/implementation/evidence"
                   / "TF-I10A2-management-ordering/legacy-unsupported.json")


@pytest.mark.parametrize("rule,policy", [
    ("trailing_pips", "OFFLINE_TRAILING_FIRST_EXIT"),
    ("partial_exit", "OFFLINE_PARTIAL_EXIT"),
    ("time_exit", "OFFLINE_ELAPSED_TIME_FIRST_EXIT"),
])
def test_legacy_unsupported_payload_survives_new_policy(position, monkeypatch, rule, policy):
    baseline = json.loads(LEGACY_BASELINE.read_text(encoding="utf-8"))
    prior = baseline["legacy_rows"][rule]
    ids = count(prior["uuid_seed"])
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4", lambda: UUID(int=next(ids)))
    tape = baseline["tape"]
    run, intent = position(management=prior["management"])
    entry, = validate(run, tape, intent["id"])["results"]
    assert entry["id"] == prior["entry_result_id"]
    original = snapshots(run, entry)
    assert json.loads(prior["payload"])["status"] == "UNSUPPORTED_EXIT_MANAGEMENT"
    with transaction() as conn:
        conn.execute("INSERT INTO offline_first_exit_results VALUES (?,?,?)",
                     (prior["result_id"], entry["id"], prior["payload"]))

    next_result, = calculate(run, tape, entry, baseline["cutoff_ms"])["results"]
    assert next_result["status"] == "SIMULATED_EXIT"
    assert next_result["identity"]["policy"]["id"] == policy
    assert next_result["id"] != prior["result_id"]
    assert calculate(run, tape, entry, baseline["cutoff_ms"])["results"] == [next_result]
    assert snapshots(run, entry) == original
    with transaction() as conn:
        rows = dict(conn.execute("""
            SELECT id, CAST(payload AS BLOB)
            FROM offline_first_exit_results WHERE entry_result_id=?
        """, (entry["id"],)).fetchall())
    assert rows.keys() == {prior["result_id"], next_result["id"]}
    assert rows[prior["result_id"]] == prior["payload"].encode("utf-8")
