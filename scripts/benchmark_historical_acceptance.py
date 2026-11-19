"""Bounded timings for real web CSV, synthetic BI5 and saved analytical queries."""
import argparse
from contextlib import contextmanager
from datetime import date, datetime, UTC
import json
import lzma
from pathlib import Path
import runpy
from statistics import median, quantiles
from struct import pack
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc

from fastapi.testclient import TestClient

from research_backend.app import db, storage
from research_backend.app.historical_csv import CSV_FEED, complete_m5, import_csv, read_minutes, source_name
from research_backend.app.historical_import import import_bi5
from scripts.verify_historical_acceptance import DAY, READY, SMT, SYMBOLS, get_data, post, scope


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = 30
rss_peak = runpy.run_path(str(ROOT/"docs/implementation/evidence/TF-I08C/benchmark.py"))["rss_peak"]


def timing(callback):
    samples = []
    for i in range(SAMPLES+3):
        started = perf_counter()
        result = callback()
        elapsed = (perf_counter()-started)*1000
        if i>=3:
            samples.append(elapsed)
    return {"p50_ms":round(median(samples),3),
            "p95_ms":round(quantiles(samples,n=100,method="inclusive")[94],3)},result


def memory(callback):
    tracemalloc.start()
    try:
        callback()
        return {"python_peak_bytes":tracemalloc.get_traced_memory()[1],"native_peak_kib":rss_peak()}
    finally:
        tracemalloc.stop()


@contextmanager
def isolated():
    saved = db.DATABASE,storage.MARKET_DIR
    with TemporaryDirectory(prefix="tf-i08e-benchmark-") as target:
        root = Path(target)
        db.DATABASE,storage.MARKET_DIR = root/"archive.sqlite3",root/"market"
        try:
            db.initialize()
            yield
        finally:
            db.DATABASE,storage.MARKET_DIR = saved


def sqlite_rows():
    return [json.loads(row["manifest"]) for row in db.rows("SELECT manifest FROM historical_imports ORDER BY source_start")]


def imports(source):
    day = date(2026,11,13)
    symbol = "EURUSD"
    path = source/"EURUSD_2026-11-13_M1_BID.csv"
    payload = path.read_bytes()
    name = source_name(symbol,day)
    csv_decode,(_,minutes,_) = timing(lambda:read_minutes(payload,symbol,day))
    csv_derive,m5 = timing(lambda:complete_m5(minutes))

    def publish():
        with isolated():
            return import_csv(payload,symbol,day,name)

    csv_publish,manifest = timing(publish)
    with isolated():
        import_csv(payload,symbol,day,name)
        csv_duplicate,_ = timing(lambda:import_csv(payload,symbol,day,name))
    csv_mem = memory(publish)

    synthetic = lzma.compress(b"".join(pack(">IIIff",index*123,110010+index%8,110000+index%7,1,1)
                                       for index in range(25_000)))
    synthetic_day = datetime(2025,1,2,tzinfo=UTC)
    decode,ticks = timing(lambda:storage.decode_bi5(synthetic,symbol,synthetic_day))
    frame1,bars1 = timing(lambda:storage.aggregate_ticks(ticks,1))
    frame5,bars5 = timing(lambda:storage.aggregate_ticks(ticks,5))

    def import_ticks():
        with isolated():
            return import_bi5(synthetic,symbol,synthetic_day,0)

    publish_ticks,tick_result = timing(import_ticks)
    with isolated():
        import_bi5(synthetic,symbol,synthetic_day,0)
        duplicate_ticks,_ = timing(lambda:import_bi5(synthetic,symbol,synthetic_day,0))
    tick_mem = memory(import_ticks)
    return {
        "real_csv":{"m1":len(minutes),"m5":len(m5),"source_bytes":len(payload),"decode":csv_decode,
                    "derive_m5":csv_derive,"publish_including_setup":csv_publish,"duplicate":csv_duplicate,
                    "partitions":len(manifest["candle_datasets"]),**csv_mem},
        "synthetic_bi5":{"decoded":len(ticks),"source_bytes":len(synthetic),"decode":decode,"m1":frame1,
                         "m5":frame5,"m1_bars":len(bars1),"m5_bars":len(bars5),
                         "publish_including_setup":publish_ticks,"duplicate":duplicate_ticks,
                         "partitions":1+len(tick_result["candle_datasets"]),**tick_mem},
    }


def analyses(evidence):
    body = json.loads((evidence/"frozen-requests.json").read_text(encoding="utf-8"))["M5-S2"]["request"]
    start = int(datetime(2026,11,9,tzinfo=UTC).timestamp())
    end = start+4*DAY+22*3600
    selection = sqlite_rows()
    feeds = []
    for symbol in SYMBOLS:
        chosen = [item for item in selection if item["feed_id"] == CSV_FEED["BID"]
                  and item["source"]["symbol"] == symbol and start <= item["source"]["start"] < end]
        parts,_ = get_data(chosen,"M5")
        feeds.append(scope(symbol,"M5",parts,start,end))
    wide = {"primary":feeds[0],"comparison":feeds[1],"cutoff":end}
    client = TestClient(app)
    try:
        lanes = {}
        for label,request in (
            ("real_one_day_simulation",body),
            ("real_five_session_incomplete_simulation",{**wide,"availability_mode":"HISTORICAL_SIMULATION"}),
            ("same_real_candles_strict_recorded_gate",{**body,"availability_mode":"STRICT_RECORDED"}),
        ):
            callback = lambda:post(client,SMT,request)
            metrics,result = timing(callback)
            resources = result.get("resources",{})
            lanes[label] = {**metrics,**memory(callback),"status":result["status"],"reason":result["reason"],
                            "fingerprint":result.get("snapshot"),"checksums":resources.get("checksum_attempts"),
                            "decoded":resources.get("decoded_records"),
                            "bytes":len(json.dumps(result,separators=(",",":")).encode()),
                            "partitions":sum(len(request[side]["partitions"]) for side in ("primary","comparison"))}
        readiness = post(client,READY,wide)
        assert "HISTORICAL_SIMULATION" not in readiness["joint_modes"]
        return {"lanes":lanes,"five_session_request":wide,
                "five_session_missing_slots":{key:readiness[key]["modes"]["HISTORICAL_SIMULATION"]["missing_slots"]
                                              for key in ("primary","comparison")}}
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources",type=Path,required=True)
    parser.add_argument("--evidence",type=Path,required=True)
    args = parser.parse_args()
    result = {"samples":SAMPLES,"warmups":3,"import":imports(args.sources),"analysis":analyses(args.evidence)}
    print(json.dumps(result,indent=2))


if __name__ == "__main__":
    main()
