import asyncio
from copy import deepcopy

import pytest

from research_backend.app import storage
from research_backend.app.arrival_capture import (
    CaptureClock, CaptureConfig, CaptureManager, Worker, aggregate_m1, claim, observe, renew, seal,
)
from research_backend.app.arrival_policy import RECORDED_ARRIVAL, RECORDED_MODE, SIMULATION_MODE
from research_backend.app.db import one, rows, transaction
from research_backend.app.indicator_history import load_reference
from research_backend.app.smt_causal import ArchiveRequest, FrozenFeed
from test_smt_causal import LEFT, RIGHT, archive, bars

ROOT = "/api/market/arrival"
SETTINGS = {"timeframe":"M1","symbols":["EURUSD"],"calendar":{"kind":"continuous"}}


class Clock:
    def __init__(self, now=1):
        self.now = now
        self.fail = False

    def sample(self):
        if self.fail:
            raise ValueError("CLOCK_DISCONTINUITY")
        return self.now, self.now


def candle(instant, price=12, **changes):
    return {"time":instant,"open":price,"high":price+1,"low":price-1,"close":price,
            "volume":9,"closed":True,**changes}


def provider(values, *, feed="mt5:fixture", basis="BID"):
    def read(symbol, timeframe, limit, *, include_latest, provenance):
        assert include_latest is False and limit == 32 and timeframe == "M1"
        provenance.update(feed_id=feed,price_basis=basis)
        return list(values)
    return read


def worker(clock=None, **settings):
    config=CaptureConfig(**{**SETTINGS,**settings})
    clock=clock or Clock()
    return Worker(config,claim(config,clock.now),clock.now,clock)


def capture_scope(status, symbol="EURUSD"):
    return {"provider":"MT5","symbol":symbol,"timeframe":"M1",
            "feed_id":status["feed_id"],"price_basis":status["price_basis"]}


def frozen(scope, sealed, cutoff):
    return ArchiveRequest(symbol=scope["symbol"],source="MT5_SAVED",timeframe="M1",feed_id=scope["feed_id"],
                          price_basis="BID",partitions=sealed["partitions"],start=sealed["start"],end=sealed["end"],
                          calendar=sealed["calendar"],arrival_manifest=sealed["id"],cutoff=cutoff,
                          availability_mode=RECORDED_MODE)


def test_forward_duplicate_correction_and_frozen_rewind(client):
    current=worker()
    current.clock.now=61_050
    first=observe(current,provider([candle(0)]),"EURUSD")
    scope=capture_scope(first)
    assert first["writes"]==1 and first["last_persisted_ms"]==61_050
    manifest=seal(scope,0,60,100,current.config.calendar)
    current.clock.now=70_400
    duplicate=observe(current,provider([candle(0)]),"EURUSD")
    assert duplicate["duplicates"]==1 and duplicate["writes"]==0
    current.clock.now=75_003
    observe(current,provider([candle(0,14)]),"EURUSD")
    corrected=seal(scope,0,60,100,current.config.calendar)
    assert manifest["id"]!=corrected["id"]
    assert corrected["data_quality"]=={"observations":2,"corrections":1}
    assert load_reference(frozen(scope,corrected,61),0,60,60)["rows"]==[]
    for sealed,cutoff,value,at in [(manifest,100,12,62),(corrected,75,12,62),(corrected,76,14,76)]:
        history=load_reference(frozen(scope,sealed,cutoff),0,60,60)
        bar, stamp=history["rows"][0]
        assert (bar.high,bar.available_at,stamp)==(value+1,at,at)
    original, change=rows("SELECT * FROM arrival_observations ORDER BY sequence")
    assert change["previous_sequence"]==original["sequence"]
    assert change["fingerprint"]!=original["fingerprint"]
    assert original["observed_ms"]==61_050 and change["observed_ms"]==75_003
    readiness=client.post(ROOT+"/readiness",json={"cutoff":76,
        "primary":FrozenFeed.model_validate(frozen(scope,corrected,76).model_dump(
            exclude={"cutoff","frozen","availability_mode"})).model_dump()})
    assert readiness.status_code==200,readiness.text
    assert readiness.json()["primary"]["status"]=="READY_RECORDED"


@pytest.mark.parametrize("bad", [candle(60),candle(0,closed=False),candle(1),candle(0, volume=-1)])
def test_developing_misaligned_or_untrustworthy_observation_never_persists(client,bad):
    current=worker()
    current.clock.now=61_000
    with pytest.raises(ValueError,match="INVALID_PROVIDER_CANDLE|PROVIDER_NOMINAL_CLOSE_IN_FUTURE"):
        observe(current,provider([bad]),"EURUSD")
    assert rows("SELECT * FROM arrival_observations")==[]


def test_start_during_existing_history_and_reconnect_gap(client):
    current=worker(Clock(65_000))
    current.clock.now=135_000
    first=observe(current,provider([candle(0),candle(60)]),"EURUSD")
    scope=capture_scope(first)
    assert rows("SELECT candle_open FROM arrival_observations")==[{"candle_open":60}]
    current.scopes["EURUSD"]={**first,"state":"DISCONNECTED"}
    # The real manager holds its lease even while the provider is disconnected.
    with transaction() as db:
        renew(db,current.sessions["EURUSD"],190_000)
    current.clock.now=250_000
    delayed=observe(current,provider([candle(180),candle(60)]),"EURUSD")
    assert delayed["state"]=="DEGRADED" and delayed["missing_slots"]==[120]
    sealed=seal(scope,60,240,300,current.config.calendar)
    records=load_reference(frozen(scope,sealed,300),60,240,60)
    assert [(bar.time,at) for bar,at in records["rows"]]==[(60,135),(180,250)]
    assert records["evidence"]["recovery_observations"]==2


def test_restart_reacquires_and_preserves_first_receipt(client):
    current=worker()
    current.clock.now=61_000
    observe(current,provider([candle(0)]),"EURUSD")
    with pytest.raises(ValueError,match="already active"):
        worker(Clock(62_000))
    restarted=worker(Clock(152_000))
    restarted.clock.now=185_000
    observe(restarted,provider([candle(0),candle(120)]),"EURUSD")
    assert rows("SELECT persisted_ms FROM arrival_receipts ORDER BY sequence")==[
        {"persisted_ms":61_000},{"persisted_ms":185_000}]
    assert restarted.sessions!=current.sessions


def test_storage_interruption_does_not_publish_unreceipted_row(client,monkeypatch):
    current=worker()
    current.clock.now=61_000
    observed=0
    def fail_after_write():
        nonlocal observed
        observed+=1
        if observed==2:
            raise ValueError("CLOCK_DISCONTINUITY")
        return 61_000,61_000
    monkeypatch.setattr(current.clock,"sample",fail_after_write)
    with pytest.raises(ValueError,match="CLOCK_DISCONTINUITY"):
        observe(current,provider([candle(0)]),"EURUSD")
    assert len(rows("SELECT * FROM arrival_observations"))==1 and not rows("SELECT * FROM arrival_receipts")
    with pytest.raises(ValueError,match="No bounded recorded evidence"):
        seal(capture_scope({"feed_id":one("SELECT feed_id FROM arrival_observations")["feed_id"],"price_basis":"BID"}),
             0,60,90,current.config.calendar)
    monkeypatch.setattr(current.clock,"sample",lambda:(74_000,74_000))
    recovered=observe(current,provider([candle(0)]),"EURUSD")
    assert recovered["duplicates"]==1 and recovered["last_persisted_ms"]==74_000


@pytest.mark.parametrize("wall_next", [19,25_000_000_000])
def test_clock_regression_and_large_jump(wall_next):
    clocks=iter((20_000_000_000,wall_next))
    mono=iter((0,100,1_000_000_100))
    clock=CaptureClock(wall=lambda:next(clocks),monotonic=lambda:next(mono))
    assert clock.sample()==(20_000,0)
    with pytest.raises(ValueError,match="CLOCK_DISCONTINUITY"):
        clock.sample()


def test_provider_scope_mutation_and_checksum_corruption_fail_closed(client):
    current=worker()
    current.clock.now=61_000
    first=observe(current,provider([candle(0)]),"EURUSD")
    current.scopes["EURUSD"]=first
    for feed,basis in (("mt5:changed","BID"),("mt5:fixture","LAST_TRADE")):
        with pytest.raises(ValueError,match="FEED_IDENTITY_CHANGED"):
            observe(current,provider([candle(0)],feed=feed,basis=basis),"EURUSD")
    scope=capture_scope(first)
    manifest=seal(scope,0,60,80,current.config.calendar)
    request=frozen(scope,manifest,80)
    for update in ({"arrival_manifest":"a"*64},{"price_basis":"ASK"},{"timeframe":"M5"}):
        assert not load_reference(request.model_copy(update=update),0,60,60)["rows"]
    dataset=one("SELECT * FROM datasets WHERE id=?",(manifest["partitions"][0]["id"],))
    path=storage.MARKET_DIR/dataset["relative_path"]
    path.write_bytes(path.read_bytes()+b"bad")
    assert load_reference(request,0,60,60)["evidence"]["status"]=="SOURCE_INTEGRITY_FAILURE"


def test_manager_bounded_capture_is_idempotent_and_stops_all_scopes(client):
    async def run():
        clock=Clock()
        observed=[]
        def read(symbol,frame,limit,*,include_latest,provenance):
            clock.now=max(61_000,clock.now)+1
            observed.append((symbol,frame,include_latest))
            if symbol=="GBPUSD" and sum(row[0]==symbol for row in observed)==1:
                raise RuntimeError("disconnected")
            return provider([candle(0)])(symbol,frame,limit,include_latest=include_latest,provenance=provenance)
        manager=CaptureManager(provider=read,clock_factory=lambda:clock)
        settings=CaptureConfig(**{**SETTINGS,"symbols":["EURUSD","GBPUSD"],"poll_seconds":1})
        started=await manager.start(settings)
        duplicate=await manager.start(settings)
        assert started["scopes"]==duplicate["scopes"] and manager.status()["queue_depth"]==0
        until=asyncio.get_running_loop().time()+7
        while len(rows("SELECT * FROM arrival_receipts"))<2 and asyncio.get_running_loop().time()<until:
            await asyncio.sleep(.04)
        assert (await manager.stop())["state"]=="STOPPED"
        assert not rows("SELECT * FROM arrival_leases")
        assert {r["symbol"] for r in rows("SELECT * FROM arrival_observations")}=={"EURUSD","GBPUSD"}
        assert all(not latest for _,_,latest in observed)
    asyncio.run(run())


def test_simulated_smt_delay_identity_and_purpose_are_separate(client):
    # Use a past, completed aligned window so simulation never invents quotes.
    start=1_735_689_600
    body=archive(bars(LEFT,start=start),bars(RIGHT,start=start),start=start,end=start+720,recorded=False)
    url="/api/indicators/smt/strict"
    recorded=client.post(url,json=body).json()
    assert not recorded["events"] and recorded["reason"]=="UNVERIFIED_AVAILABILITY"
    simulated={**body,"availability_mode":SIMULATION_MODE}
    result=client.post(url,json=simulated).json()
    event=result["events"][0]
    assert event["confirmed_at"]==start+600
    assert event["historically_simulation_eligible"] and not event["historically_eligible"]
    assert event["revisions"][-1]["timing"]["strategy_eligibility"]=="SIMULATION_ONLY_ELIGIBLE"
    delayed=deepcopy(simulated)
    delayed["comparison"]["delay_seconds"]=5
    later=client.post(url,json=delayed).json()
    assert later["events"][0]["candidate_at"]==start+545
    assert later["events"][0]["confirmed_at"]==start+665
    assert later["calculation_id"]!=result["calculation_id"]
    assert client.post(url,json=delayed).json()["snapshot"]==later["snapshot"]
    delayed["comparison"]["delay_seconds"]=-1
    assert client.post(url,json=delayed).status_code==422


def test_recorded_aggregation_waits_for_complete_constituents_and_publication(client):
    from arrival_fixtures import trusted_archive

    raw = [candle(time, price=12+i, available_at=time+63) for i,time in enumerate(range(0,600,60))
           if time != 420]
    source = trusted_archive(raw,provider="MT5",feed="recorded-minute",symbol="EURUSD",timeframe="M1",
                             start=0,end=600,cutoff=700)
    combined = aggregate_m1(source,clock=Clock(705_150))
    assert combined["timeframe"]=="M5" and combined["feed_id"]!=source["feed_id"]
    assert combined["data_quality"]["observations"]==1
    request = ArchiveRequest(symbol="EURUSD",timeframe="M5",feed_id=combined["feed_id"],
        source="MT5_SAVED",price_basis="BID",calendar=combined["calendar"],partitions=combined["partitions"],
        arrival_manifest=combined["id"],start=0,end=600,cutoff=705,availability_mode=RECORDED_MODE)
    assert not load_reference(request,0,600,300)["rows"]
    bar,at=load_reference(request.model_copy(update={"cutoff":706}),0,600,300)["rows"][0]
    assert (bar.open,bar.high,bar.low,bar.close,at)==(12,17,11,16,706)
    part=one("SELECT * FROM datasets WHERE id=?",(combined["partitions"][0]["id"],))
    row=storage.read_partition(part)[0]
    assert row["aggregation_policy"]=="RECORDED_M1_TO_M5_V1" and len(row["constituents"])==5
    assert row["volume"]==45


def test_simulation_registry_rejects_recorded_consumption():
    from research_backend.app.arrival_policy import SIMULATION_AUTHORITY
    from research_backend.app.causal_timing import strategy_eligible
    from research_backend.app.smt_causal import _timed_event
    from research_backend.app.strategy_evidence import EvidenceRegistry
    from test_strict_causal_timing import ARCHIVE

    registry=EvidenceRegistry(purpose=SIMULATION_MODE)
    view=registry.calculation("structure","s",ARCHIVE,"simulated",400)
    event=_timed_event("s","CONFIRMED",0,300,320,320,400,reason="ELIGIBLE",authority=SIMULATION_AUTHORITY)
    event.update(type="PIVOT",status="confirmed",algorithm_version=1)
    view.publish(event,"structure",ARCHIVE,"s",predicate="FORMATION")
    assert strategy_eligible(event,400,registry=view,purpose=SIMULATION_MODE)
    assert not strategy_eligible(event,400,registry=view)
    assert event["timing"]["availability_basis"]=="MODELED_NOMINAL_CLOSE_PLUS_DELAY"
    assert not strategy_eligible(event,400,registry=EvidenceRegistry())
