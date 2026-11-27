from concurrent.futures import ThreadPoolExecutor
from datetime import UTC,datetime
from threading import Barrier
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
    boundary = r["next_index"]
    start_retry = Barrier(2)
    def deliver_boundary():
        start_retry.wait(timeout=10)
        response = client.post(url, params={"index":boundary})
        assert response.is_success, response.text
        return response.json()
    with ThreadPoolExecutor(2) as pool:
        first, duplicate = [pool.submit(deliver_boundary) for _ in range(2)]
        end = first.result()
        assert duplicate.result() == end
    assert client.post(url,params={"index":i}).json()==r
    assert client.post(url,params={"index":boundary}).json()==end
    assert len(rows("SELECT * FROM month_replay_steps WHERE session=? AND idx=?",(sid,boundary)))==1
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


@pytest.mark.parametrize("filled", [False, True])
def test_missing_open_partition_freezes_pending_or_open_position(client, filled):
    ticks = [(3595999,110000),(3596999,110012)]
    if filled:
        ticks.append((3597999,110013))
    first = source(13, ticks)
    source(15, [(0,110230),(100,110250),(200,110270)])
    path = "/api/month-replay/2025-11"
    report = client.get(path).json()
    assert report["open_gap_intervals"] == 0
    session = report["session"]
    route = f"{path}/{session}"
    index, ledger = 0, []
    while True:
        resp = client.post(route+"/advance", params={"index":index})
        assert resp.is_success, resp.text
        step = resp.json()
        ledger.append({"index":index, **step})
        index = step["next_index"]
        if step["completed"]:
            break

    events = [event for receipt in ledger for event in receipt["events"]]
    assert [event["kind"] for event in events] == ["QUALIFIED", *(["ENTRY"] if filled else []), "UNRESOLVED"]
    unresolved = events[-1]
    assert unresolved["decision_id"] == events[0]["id"]
    assert unresolved["remaining"] == int(filled)
    assert unresolved["quote"] == events[-2]["quote"]
    assert unresolved["quote"]["source_sha256"] == first["source"]["sha256"]
    assert unresolved["reason"] == "OPEN_SESSION_QUOTE_GAP"
    assert ("fill" in step["state"]["position"]) == filled
    assert step["quote_count"] == len(ticks) + 3
    batches = [batch for receipt in ledger for batch in receipt["coverage"]]
    missing_hour = next(hour for hour in report["binding"]["hours"] if hour["start"] == first["source"]["end"])
    assert missing_hour["status"] == "MISSING_OPEN"
    missing_statuses = [batch["status"] for batch in batches
                        if missing_hour["start"] <= batch["start"] < missing_hour["start"] + 3600]
    assert missing_statuses == ["MISSING_OPEN"] * 6
    assert len(ledger) > 24
    inspected = client.get(path, params={"session":session}).json()
    assert inspected["checkpoint"] == step
    assert inspected["open_gap_intervals"] == sum(len(batch["gaps"]) for batch in batches)

    exported = client.post(route+"/packages", params={"cutoff_ms":step["through"]*1000-1})
    assert exported.status_code == 201, exported.text
    package = client.get("/api/ai/packages/"+exported.json()["package_id"]+"/recorded").json()
    assert package["evidence"]["metrics"] == {}
    assert package["evidence"]["unavailable"]["month_expectancy"] == "NO_VALIDATED_MONTH_PERFORMANCE"
    assert package["evidence"]["unavailable"][events[0]["id"]] == "OPEN_SESSION_QUOTE_GAP"
    assert package["template"]["claims"] == []


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
