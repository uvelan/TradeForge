"""Reproduction for the TF-I08D pre-change admission audit."""
from research_backend.app.storage import write_partition
from arrival_fixtures import trusted_archive

URL = "/api/indicators/evaluate"
START = 1_735_689_600
STEP = 300
ENGINES = (
    ("market_structure", "structure"), ("liquidity_sweeps", "liquidity"),
    ("fair_value_gap", "fvg"), ("order_blocks", "ob"), ("candle_range_theory", "crt"),
)


def test_report_archived_arrival_admission(client):
    from research_backend.app.indicator_engine import Evaluation
    from research_backend.app.indicator_history import load_reference

    values = [100, 101, 110, 104, 105, 102, 109, 103, 104, 117, 118, 107, 118, 104, 93, 111] * 5
    def candles(offset, timing):
        return [dict(time=START+i*STEP, open=x+offset, high=x+offset+2, low=x+offset-2, close=x+offset,
                     closed=True, **({"available_at": START+(i+1)*STEP+timing} if timing is not None else {}))
                for i, x in enumerate(values)]

    end = START+len(values)*STEP
    schedule = {"kind":"continuous"}
    for label, delay in (("explicit_without_marker", 0), ("assumed_close", None), ("invalid_early", -1),
                         ("registered_capture",0)):
        scopes = {}
        for side, symbol, offset in (("primary", "EURUSD", 0), ("comparison", "GBPUSD", 10)):
            records = candles(offset,delay)
            if label == "registered_capture":
                sealed = trusted_archive(records,provider="MT5",feed=label,symbol=symbol,timeframe="M5",
                                         start=START,end=end)
                archive = dict(partitions=sealed["partitions"],arrival_manifest=sealed["id"])
            else:
                part = write_partition("MT5", label, symbol, "candles", "M5", "BID", records)
                archive = dict(partitions=[{k:part[k] for k in ("id","sha256")}])
            scopes[side] = dict(source="MT5_SAVED", symbol=symbol, timeframe="M5", feed_id=label,
                                price_basis="BID", **archive)
        common = dict(history_bars=10_000, history_start=START)
        settings = [dict(id=engine, **{key: common}) for engine,key in ENGINES]
        for item in settings:
            if item["id"] in {"market_structure","liquidity_sweeps","order_blocks"}:
                item["structure"] = {**common,"algorithm_version":2,"market_schedule":schedule}
            if item["id"] in {"fair_value_gap","candle_range_theory"}:
                key = "fvg" if item["id"]=="fair_value_gap" else "crt"
                item[key] = {**common,"market_schedule":schedule}
        request = dict(**scopes["primary"], cutoff=end, frozen=True, load_reference=True, timing_policy="STRICT_CAUSAL",
                       candles=[], indicators=settings)
        response = client.post(URL,json=request)
        assert response.is_success, response.text
        for result in response.json()["results"]:
            timing = [entry["timing"] for value in result.values() if isinstance(value,list)
                      for entry in value if isinstance(entry,dict) and isinstance(entry.get("timing"),dict)]
            print(label, result["id"], result.get("analysis",{}).get("strategy_eligibility"),
                  sorted({item["strategy_eligibility"] for item in timing}))
            if label == "registered_capture":
                assert result["provenance"]["availability_policy"] == "SERVER_RECORDED_ARRIVAL_V2"
                assert result["provenance"]["context_authority"] == "CHECKSUM_VERIFIED_ARCHIVE"
            else:
                assert all(item["strategy_eligibility"]!="ELIGIBLE" for item in timing)
        smt = client.post("/api/indicators/smt/strict", json={
            **{side:dict(**scope,start=START,end=end,calendar={"kind":"continuous"}) for side,scope in scopes.items()},
            "cutoff":end,
        })
        assert smt.is_success, smt.text
        print(label, "SMT", smt.json().get("reason"))
        assert (smt.json().get("reason") == "UNVERIFIED_AVAILABILITY") == (label in {
            "explicit_without_marker","assumed_close"})
        forged = {**request, "arrival_manifest":None, "frozen":False, "indicators":[{"id":"fair_value_gap"}],
                  "candles":[dict(**bar, availability_policy="SERVER_RECORDED_ARRIVAL_V1") for bar in candles(0,0)]}
        response = client.post(URL,json=forged)
        assert response.is_success,response.text
        authority = response.json()["results"][0]["provenance"]["context_authority"]
        print(label, "client_forgery", authority)
        assert authority == "CLIENT_SUPPLIED_UNVERIFIED"
        history = load_reference(Evaluation.model_validate(request),START,end,STEP)
        admitted = sum(bar.available_at == at for bar,at in history["rows"])
        print(label, "verified_reader_explicit_timestamps", admitted)
        assert admitted == (80 if label=="registered_capture" else 0)
