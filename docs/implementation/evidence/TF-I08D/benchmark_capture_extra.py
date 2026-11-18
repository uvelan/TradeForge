"""Measure injected slow storage and frozen correction fetch in isolation."""
from contextlib import contextmanager
import json
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
from time import sleep

from research_backend.app import arrival_capture, config, db, storage
from research_backend.app.arrival_policy import RECORDED_MODE
from research_backend.app.indicator_history import load_reference
from research_backend.app.smt_causal import ArchiveRequest

support=runpy.run_path(str(Path(__file__).with_name("benchmark.py")))
sample,stats,Clock,START=(support[key] for key in ("sample","stats","Clock","START"))


def main():
    with TemporaryDirectory() as scratch:
        root=Path(scratch)
        db.DATABASE=config.DATABASE=root/"isolated.sqlite3"
        storage.MARKET_DIR=root/"market"
        db.initialize()
        cfg=arrival_capture.CaptureConfig(symbols=["EURUSD","GBPUSD"],timeframe="M1",
                                           calendar={"kind":"continuous"})
        clock=Clock(START*1000)
        active=arrival_capture.Worker(cfg,arrival_capture.claim(cfg,clock.now),clock.now,clock)
        stamp=START
        price=12
        delays=[]
        original=arrival_capture.transaction

        def provider(symbol,timeframe,limit,*,include_latest,provenance):
            provenance.update(feed_id="mt5:injected",price_basis="BID")
            return [{"time":stamp,"open":price,"high":price+1,"low":price-1,"close":price,"volume":9}]

        @contextmanager
        def slow():
            with original() as sql:
                yield sql
                sleep(.005)

        arrival_capture.transaction=slow
        try:
            for index in range(33):
                stamp=START+60*index
                clock.now=(stamp+62)*1000
                took,result=sample(lambda:[arrival_capture.observe(active,provider,symbol) for symbol in cfg.symbols])
                if index>=3:
                    delays.append(took)
        finally:
            arrival_capture.transaction=original

        before=clock.now//1000
        price=14
        clock.now+=3000
        correction=arrival_capture.observe(active,provider,"EURUSD")
        after=clock.now//1000
        scope=dict(provider="MT5",symbol="EURUSD",timeframe="M1",price_basis="BID",feed_id=correction["feed_id"])
        sealed=arrival_capture.seal(scope,stamp,stamp+60,after,cfg.calendar)
        query=ArchiveRequest(source="MT5_SAVED",symbol="EURUSD",timeframe="M1",price_basis="BID",
            feed_id=scope["feed_id"],calendar=cfg.calendar,start=stamp,end=stamp+60,arrival_manifest=sealed["id"],
            partitions=sealed["partitions"],cutoff=before,availability_mode=RECORDED_MODE)
        fetch=[]
        for index in range(33):
            took,result=sample(lambda:[load_reference(query.model_copy(update={"cutoff":cutoff}),stamp,stamp+60,60)
                                         for cutoff in (before,after)])
            assert [entry["rows"][0][0].close for entry in result]==[12,14]
            if index>=3:
                fetch.append(took)
        print(json.dumps({"samples":30,"injected_5ms_each_capture_transaction_pair_ms":stats(delays),
                          "correction_before_after_lookup_pair_ms":stats(fetch),"queue_depth":0,
                          "corrected_manifest_observations":sealed["data_quality"]["observations"]},sort_keys=True))


if __name__=="__main__":
    main()
