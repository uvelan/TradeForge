from datetime import UTC,datetime
import lzma
import struct
import pytest

from research_backend.app.historical_import import import_bi5
from research_backend.app.db import rows


def source(hour, values):
    raw=lzma.compress(b"".join(struct.pack(">IIIff",ms,bid+2,bid,1,1) for ms,bid in values))
    return import_bi5(raw,"EURUSD",datetime(2025,11,13,tzinfo=UTC),hour)


@pytest.mark.parametrize("late",[False,True])
def test_two_source_frontier_and_retry(client,late):
    # Qualification and ASK entry in the first file, BID target or missing evidence in the second.
    a=source(13,[(3595000,110000),(3596000,110012),(3597000,110013)])
    b=source(14,[(90000 if late else 100,110230)])
    path="/api/month-replay/2025-11"
    report=client.get(path).json()
    sid=report["session"]
    url=path+"/"+sid+"/advance"
    i=0
    while True:
        result=client.post(url,params={"index":i})
        assert result.is_success,result.text
        r=result.json()
        if r["events"]:
            break
        i=r["next_index"]
    decision,entry=r["events"]
    assert (decision["kind"],entry["kind"],entry["price_units"])==("QUALIFIED","ENTRY",110015)
    assert entry["quote"]["source_sha256"]==a["source"]["sha256"]
    assert client.get(path,params={"session":sid}).json()["checkpoint"]==r
    end=client.post(url,params={"index":r["next_index"]}).json()
    assert client.post(url,params={"index":i}).json()==r
    assert client.post(url,params={"index":r["next_index"]}).json()==end
    e,=end["events"]
    if late:
        assert e["kind"]=="UNRESOLVED" and end["state"]["unresolved"]
        assert e["quote"]==entry["quote"]
    else:
        assert (e["kind"],e["price_units"],e["remaining"])==("TARGET",110230,0)
        assert e["quote"]["source_sha256"]==b["source"]["sha256"]
    assert e["decision_id"]==decision["id"]
    ref=e["quote"]
    lookup=path+"/"+sid+"/quote"
    assert client.get(lookup,params={"time_ms":ref["time_ms"],"ordinal":ref["ordinal"]}).json()==ref
    assert client.get(lookup,params={"time_ms":ref["time_ms"],"ordinal":ref["ordinal"]+1}).status_code==404
    assert len(rows("SELECT * FROM month_replay_steps WHERE session=? AND idx=?",(sid,i)))==1
    # A later import makes a new inventory but must not rebind an old checkpoint.
    source(15,[(1000,110000)])
    assert client.get(path).json()["session"]!=sid
    assert client.get(path,params={"session":sid}).json()["checkpoint"]==end
    from research_backend.app import storage
    dataset=rows("SELECT relative_path FROM datasets WHERE id=?",(ref["partition"]["id"],))[0]
    file=storage.MARKET_DIR/dataset["relative_path"]
    file.write_bytes(file.read_bytes()+b"changed")
    denied=client.get(lookup,params={"time_ms":ref["time_ms"],"ordinal":ref["ordinal"]})
    assert denied.status_code==409 and "verification failed" in denied.json()["detail"]
    assert client.get(path,params={"session":sid}).json()["checkpoint"]==end



def test_month_entry_quote_can_already_breach_protective_stop(client):
    from research_backend.app.month_replay import initial, run_batch
    h=3600
    segment=dict(start=h,end=h+600,status="REGISTERED",pin={"source_sha256":"a","partition":{"id":"b","sha256":"c"}})
    quotes=[dict(time_ms=h*1000+i,bid_units=b,ask_units=a,source_order=i)
            for i,(b,a) in enumerate([(110000,110002),(110010,110012),(110011,110112)])]
    state=initial()
    events,_=run_batch(state,segment,quotes,"control")
    assert [(e["kind"],e["quote"]["ordinal"]) for e in events]==[("QUALIFIED",1),("ENTRY",2),("STOP",2)]
    assert events[-1]["price_units"]==110011 and state["position"] is None
