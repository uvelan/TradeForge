"""Persisted elapsed-time exits are driven by available executable quotes."""
import pytest

from test_bounded_crt_smt_successors import canonical_successors
from test_offline_first_exit import ENTRY_LONG, ENTRY_SHORT, position
from test_offline_market_entry import Q, publish, quote
from test_offline_trailing_stop import replay

DEADLINE = 1002
DURATION = {"unit":"SECONDS","count":1}
COVERED = [(Q+1,Q+1100)]


@pytest.fixture(params=["bullish","bearish"])
def time_case(position,request):
    run,intent = position(request.param,management={"time_exit":DURATION})
    long = request.param=="bullish"
    return (run,intent,ENTRY_LONG if long else ENTRY_SHORT,"BID" if long else "ASK",
            110310 if long else 110315)


def test_delayed_quote_at_deadline_and_rewind(time_case):
    c = time_case
    _,_,first,side,units = c
    stale = quote(Q+DEADLINE-1,Q+DEADLINE,8,110310,110315)
    eligible = quote(Q+DEADLINE,Q+DEADLINE+1,9,110310,110315)
    # The entry quote was observed before it was delivered and filled.
    first = first | {"time_ms":Q+1}
    rows = [eligible,first,stale]
    prior,count = replay(c,rows,DEADLINE-1,COVERED)
    assert (prior["status"],prior["deadline_ms"],count)==("OPEN_AT_CUTOFF",Q+DEADLINE,0)
    waiting,count = replay(c,rows,DEADLINE,COVERED)
    assert (waiting["status"],waiting["exit"],count)==("AWAITING_TIME_EXIT_QUOTE",None,0)
    outcome,count = replay(c,rows,DEADLINE+1,COVERED)
    event = outcome["exit"]
    assert (outcome["status"],outcome["identity"]["policy"]["id"],count)==(
        "SIMULATED_EXIT","OFFLINE_ELAPSED_TIME_FIRST_EXIT",1)
    assert (event["reason"],event["side"],event["price_units"],event["time_ms"])==(
        "TIME_EXIT",side,units,Q+DEADLINE+1)
    assert event["threshold_units"] is None and event["threshold_price"] is None
    assert event["quote"]==eligible | {"scale":100000}
    assert replay(c,rows,DEADLINE+2,COVERED)[0]["exit"]==event
    assert replay(c,rows,DEADLINE,COVERED)[0]==waiting


def test_price_exit_gets_priority_on_deadline(time_case):
    c = time_case
    _,_,first,side,_ = c
    stop = quote(Q+DEADLINE,Q+DEADLINE,8,110210,110220) if side=="BID" else quote(
        Q+DEADLINE,Q+DEADLINE,8,110405,110410)
    outcome,count = replay(c,[first,stop],DEADLINE,COVERED)
    assert (outcome["exit"]["reason"],count)==("PROTECTIVE_STOP",1)


def test_time_exit_gap_or_tied_priority_cannot_publish(time_case):
    c = time_case
    _,_,first,_,_ = c
    a = quote(Q+DEADLINE,Q+DEADLINE,8,110310,110315)
    b = a | {"bid_units":110311}
    gap,count = replay(c,[first,a],DEADLINE,[(Q+1,Q+2),(Q+4,Q+1100)])
    assert (gap["status"],gap["exit"],count)==("INSUFFICIENT_QUOTE_COVERAGE",None,0)
    for tied in ([a,b],[b,a]):
        row,count = replay(c,[first,*tied],DEADLINE,COVERED)
        assert (row["status"],row["exit"],count)==("AMBIGUOUS_QUOTE_ORDER",None,0)
