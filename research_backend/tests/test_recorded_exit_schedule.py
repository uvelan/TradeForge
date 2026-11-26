"""Calendar guard across the hour boundary; a single BI5 source cannot span it."""
from datetime import UTC,datetime
from types import SimpleNamespace

from research_backend.app import recorded_market_entry as recorded
from research_backend.app.offline_first_exit import simulate_first_exit
from research_backend.app.strategy_definitions import ManagementPolicy


def test_open_recorded_position_cannot_cross_fx_closure(monkeypatch):
    boundary = int(datetime(2025,11,14,22,tzinfo=UTC).timestamp())*1000
    quotes = [dict(time_ms=boundary-1000,source_order=0,bid_units=116270,ask_units=116274,scale=100000),
              dict(time_ms=boundary+1000,source_order=1,bid_units=116165,ask_units=116170,scale=100000)]
    first = recorded.execution_quote(quotes[0],100000).model_dump(mode="json") | {"scale":100000}
    # Bind the normalized reader boundary to an archived quote fixture, not a fake
    # monolithic BI5 provider import spanning the Friday close.
    monkeypatch.setattr(recorded,"recorded_reference",lambda *_: (None,quotes))
    digest = "0"*64
    spec = recorded.RecordedWindow(start=boundary//1000-2,end=boundary//1000+2,source_sha256=digest,
                                   source={"id":digest,"sha256":digest},
                                   partitions=[{"id":digest,"sha256":digest}])
    entry = dict(id="recorded-fixture", source=dict(id="intent",symbol="EURUSD",direction="LONG"),
                 fill=dict(id="fill", time_ms=first["time_ms"], price_units=first["ask_units"], quote=first))
    model = ManagementPolicy(initial_stop={"kind":"FIXED_PIPS","distance":10},
                             target={"kind":"REWARD_MULTIPLE","multiple":3})
    window,_ = recorded.resolve_exit_window(None,spec,entry,boundary+1000)
    assert [q.time_ms for q in window.quotes]==[boundary-1000,boundary+1000]
    interval, = window.complete_delivery_intervals
    assert (interval.start_ms,interval.end_ms)==(boundary-1000,boundary-1000)
    r = simulate_first_exit(entry,model,window,digest,boundary+1000)
    assert r["status"]=="INSUFFICIENT_QUOTE_COVERAGE" and r["exit"] is None
