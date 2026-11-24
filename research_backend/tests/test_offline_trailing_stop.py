"""Service-level checks for fixed-distance trailing stops on pinned quotes."""
import pytest

from test_bounded_crt_smt_successors import canonical_successors
from test_offline_first_exit import ENTRY_LONG, ENTRY_SHORT, calculate, position, snapshots
from test_offline_market_entry import Q, publish, quote, tape, validate


@pytest.fixture(params=["bullish","bearish"])
def trailing_case(position,request):
    long = request.param=="bullish"
    run,intent = position(request.param,management={"trailing_pips":5})
    if long:
        return run,intent,ENTRY_LONG,quote(Q+3,Q+3,6,110400,110410),350,340,110350,110214,"BID"
    return run,intent,ENTRY_SHORT,quote(Q+3,Q+3,6,110190,110200),250,260,110250,110406,"ASK"


def replay(case,rows,cutoff,intervals=None):
    run,intent,*_ = case
    data = tape(rows) if intervals is None else tape(rows,intervals)
    entry, = validate(run,data,intent["id"])["results"]
    assert entry["status"]=="SIMULATED_FILLED"
    before = snapshots(run,entry)
    outcome = calculate(run,data,entry,Q+cutoff)
    assert calculate(run,data,entry,Q+cutoff)==outcome
    assert snapshots(run,entry)==before
    return outcome["results"][0],outcome["simulated_exits"]


def test_ratchet_reads_executable_side_without_lookahead(trailing_case):
    c = trailing_case
    _,_,first,advance,_,gap,stop,initial,side = c
    opposite = quote(Q+4,Q+4,7,110370,110375) if side=="BID" else quote(Q+4,Q+4,7,110225,110230)
    closing = quote(Q+5,Q+5,8,110000+gap,110350) if side=="BID" else quote(Q+5,Q+5,8,110250,110000+gap)
    rows = [first,advance,opposite,closing]
    pre,count = replay(c,rows,2)
    assert (pre["active_stop_units"],pre["ratchet"],count)==(initial,None,0)
    armed,count = replay(c,rows,4)
    assert (armed["active_stop_units"],count)==(stop,0)
    assert armed["ratchet"]["quote"]==advance | {"scale":100000}
    assert armed["identity"]["policy"]["id"]=="OFFLINE_TRAILING_FIRST_EXIT"
    after,count = replay(c,rows,5)
    assert count==1 and after["ratchet"]==armed["ratchet"]
    event = after["exit"]
    assert (event["reason"],event["threshold_units"],event["price_units"],event["side"])==(
        "TRAILING_STOP",stop,110000+gap,side)
    assert replay(c,rows,6)[0]["exit"]==event
    assert replay(c,rows,4)[0]==armed


@pytest.mark.parametrize("reverse",[False,True])
def test_tied_stop_and_miss_after_ratchet(trailing_case,reverse):
    c = trailing_case
    _,_,first,advance,_,_,stop,_,side = c
    if side=="BID":
        hit,miss = (quote(Q+3,Q+3,7,bid,stop+2) for bid in (stop,stop+1))
    else:
        hit,miss = (quote(Q+3,Q+3,7,stop-2,ask) for ask in (stop,stop-1))
    rows = [first,advance,hit,miss]
    row,count = replay(c,list(reversed(rows)) if reverse else rows,3)
    assert (row["status"],row["active_stop_units"],row["exit"],count)==(
        "AMBIGUOUS_QUOTE_ORDER",stop,None,0)
    assert row["ratchet"]["quote"]==advance | {"scale":100000}


def test_gap_before_ratchet_cannot_publish(trailing_case):
    c = trailing_case
    _,_,first,advance,_,_,_,initial,_ = c
    advance = advance | {"time_ms":Q+4,"available_at_ms":Q+4}
    row,count = replay(c,[first,advance],4,[(Q+1,Q+2),(Q+4,Q+5)])
    assert (row["status"],row["active_stop_units"],row["ratchet"],count)==(
        "INSUFFICIENT_QUOTE_COVERAGE",initial,None,0)


def test_fractional_trail_rejected_and_combination_admitted(position):
    for management,status in (
        ({"trailing_pips":5.01},"UNREPRESENTABLE_EXIT_LEVELS"),
        ({"trailing_pips":5,"break_even_at_r":1},"OPEN_AT_CUTOFF"),
    ):
        run,intent = position(management=management)
        row,n = replay((run,intent),[ENTRY_LONG],3)
        assert (row["status"],n)==(status,0)
