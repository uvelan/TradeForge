"""Check pinned-quote partial execution without treating reduction as liquidation."""
import pytest

from test_bounded_crt_smt_successors import canonical_successors
from test_offline_first_exit import ENTRY_LONG, ENTRY_SHORT, calculate, position, snapshots
from test_offline_market_entry import Q, publish, quote, tape, validate


@pytest.fixture(params=["bullish","bearish"])
def partial_case(position,request):
    long = request.param=="bullish"
    run,intent = position(request.param,management={
        "partial_exit":{"at_r":1.5,"percent":12.5},
        "target":{"kind":"REWARD_MULTIPLE","multiple":3},
    })
    if long:
        return (run,intent,ENTRY_LONG,quote(Q+3,Q+3,6,110464,110470),
                quote(Q+4,Q+4,7,110210,110220),quote(Q+4,Q+4,7,110620,110625),110464,"BID")
    return (run,intent,ENTRY_SHORT,quote(Q+3,Q+3,6,110150,110156),
            quote(Q+4,Q+4,7,110400,110410),quote(Q+4,Q+4,7,110000,110005),110156,"ASK")


def replay(case,rows,cutoff,intervals=None):
    run,intent,*_ = case
    data = tape(rows) if intervals is None else tape(rows,intervals)
    entry, = validate(run,data,intent["id"])["results"]
    assert entry["status"]=="SIMULATED_FILLED"
    before = snapshots(run,entry)
    outcome = calculate(run,data,entry,Q+cutoff)
    assert calculate(run,data,entry,Q+cutoff)==outcome
    assert snapshots(run,entry)==before
    return outcome["results"][0],outcome["simulated_partial_exits"],outcome["simulated_exits"]


@pytest.mark.parametrize("terminal_index,reason",[(4,"PROTECTIVE_STOP"),(5,"REWARD_TARGET")])
def test_partial_then_terminal_only_closes_remainder(partial_case,terminal_index,reason):
    c = partial_case
    _,_,first,partial,_,_,threshold,side = c
    terminal = c[terminal_index]
    rows = [first,partial,terminal]
    before,n,exits = replay(c,rows,2)
    assert (before["status"],before["partial_exit"],n,exits)==("OPEN_AT_CUTOFF",None,0,0)
    reduced,n,exits = replay(c,rows,3)
    assert (reduced["status"],reduced["exit"],n,exits)==("OPEN_AT_CUTOFF",None,1,0)
    observed = reduced["partial_exit"]
    assert (observed["reason"],observed["threshold_units"],observed["price_units"],observed["side"])==(
        "PARTIAL_REWARD",threshold,threshold,side)
    assert observed["quote"]==partial | {"scale":100000}
    assert observed["closed_fraction"]=={"numerator":1,"denominator":8}
    remainder = {"numerator":7,"denominator":8}
    assert reduced["remaining_fraction"]==remainder
    closed,n,exits = replay(c,rows,4)
    assert (closed["status"],n,exits)==("SIMULATED_EXIT",1,1)
    assert closed["partial_exit"]==observed
    assert closed["exit"]["reason"]==reason
    assert closed["exit"]["closed_fraction"]==remainder
    assert closed["remaining_fraction"]=={"numerator":0,"denominator":1}
    assert replay(c,rows,5)[0]["exit"]==closed["exit"]
    assert replay(c,rows,3)[0]==reduced


def test_reward_gap_never_invents_intermediate_partial(partial_case):
    c = partial_case
    row,n,exits = replay(c,[c[2],c[5]],4)
    assert (row["exit"]["reason"],row["partial_exit"],n,exits)==("REWARD_TARGET",None,0,1)
    assert row["exit"]["closed_fraction"]=={"numerator":1,"denominator":1}


@pytest.mark.parametrize("reverse",[False,True])
def test_tied_partial_touch_and_miss_fail_closed(partial_case,reverse):
    c = partial_case
    _,_,entry,partial,_,_,threshold,side = c
    miss = partial | ({"bid_units":threshold-1} if side=="BID" else {"ask_units":threshold+1})
    rows = [entry,partial,miss]
    row,n,exits = replay(c,list(reversed(rows)) if reverse else rows,3)
    assert (row["status"],row["partial_exit"],row["exit"],n,exits)==(
        "AMBIGUOUS_QUOTE_ORDER",None,None,0,0)


def test_gap_after_partial_preserves_verified_reduction(partial_case):
    c = partial_case
    rows = [c[2],c[3],c[4] | {"time_ms":Q+5,"available_at_ms":Q+5}]
    row,n,exits = replay(c,rows,5,[(Q+1,Q+3),(Q+5,Q+5)])
    assert (row["status"],n,exits)==("INSUFFICIENT_QUOTE_COVERAGE",1,0)
    assert row["partial_exit"]["quote"]==c[3] | {"scale":100000}
    assert row["remaining_fraction"]=={"numerator":7,"denominator":8}
