import lzma,struct
from datetime import datetime,timezone
from research_backend.app.storage import decode_bi5,aggregate_ticks

def test_daily_bi5_decode_preserves_bid_ask_and_same_second():
    raw=struct.pack('>IIIff',0,108550,108540,1.0,1.0)+struct.pack('>IIIff',500,108560,108550,1.0,1.0)
    compressed=lzma.compress(raw,format=lzma.FORMAT_ALONE)
    ticks=decode_bi5(compressed,'EURUSD',datetime(2026,11,12,tzinfo=timezone.utc))
    assert len(ticks)==2 and ticks[0]['time']==ticks[1]['time']
    assert ticks[0]['bid_units']<ticks[0]['ask_units']
    assert ticks[1]['time_ms']-ticks[0]['time_ms']==500
    candles=aggregate_ticks(ticks,5)
    assert candles[0]['open']==1.0854 and candles[0]['close']==1.0855

def test_invalid_bid_rejected():
    raw=struct.pack('>IIIff',0,100,200,1.0,1.0)
    try:
        decode_bi5(lzma.compress(raw,format=lzma.FORMAT_ALONE),'EURUSD',datetime(2026,11,12,tzinfo=timezone.utc))
        assert False
    except ValueError as e:assert 'bid/ask' in str(e)
