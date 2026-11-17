import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from research_backend.app.analysis_evidence import AnalysisCoordinator, ArchiveSession, EvidenceIndex, ResourceLimit
from research_backend.app.indicator_engine import Evaluation, EvidenceBar, evaluate
from test_crt import START, payload


def test_approved_golden_singles_and_combined_are_unchanged():
    root=Path(__file__).resolve().parents[2]/"docs"/"implementation"/"evidence"/"TF-I08"
    spec=importlib.util.spec_from_file_location("approved_indicator_goldens",root/"golden.py")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected=json.loads((root/"golden-approved.json").read_text(encoding="utf-8-sig"))
    assert module.fingerprints()==expected


def configured_request(policy="shared", *, zero=False):
    ref=(102,105,100,103)
    center=(102,103,101,102)
    values=[ref]+[center]*8+[(102,104,101,103),(103,103.5,102,102.5),center,
                           (104,106,103.5,104.5),center]
    data=payload(values,rules={"include_context":True})
    data["indicators"][0]["context"]={"policy":policy}
    rules={"algorithm_version":2,"left":1,"right":1}
    data["indicators"].extend([
        {"id":"market_structure","structure":rules,
         "structure_delivery":{"pivot_limit":0,"break_limit":0} if zero else {}},
        {"id":"liquidity_sweeps","structure":rules,"liquidity_delivery":{"pool_limit":0,"sweep_limit":0} if zero else {}},
        {"id":"order_blocks","structure":rules,"ob":{"include_context":True},"context":{"policy":policy},
         "ob_delivery":{"formation_limit":0,"revision_limit":0} if zero else {}},
        {"id":"fair_value_gap","fvg_delivery":{"formation_limit":0,"revision_limit":0} if zero else {}},
    ])
    return data


def by_id(result):
    return {item["id"]:item for item in result["results"]}


def test_shared_refs_resolve_to_canonical_ids_and_survive_zero_history():
    first=evaluate(Evaluation.model_validate(configured_request()))
    all_results=by_id(first)
    crt=all_results["candle_range_theory"]
    sweep=crt["crt_sweeps"][0]
    structure=all_results["market_structure"]
    event=structure["breaks"][-1]
    assert (event["type"],event["break_time"],event["level_price"],event["break_price"])==(
        "BOS",START+3600,104,104.5)
    ref=sweep["context"]["BOS"]
    assert ref["id"]==ref["observation_id"]==event["id"]
    assert ref["witness_id"]!=ref["id"]
    assert ref["configuration_fingerprint"]==structure["analysis"]["configuration_fingerprint"]
    assert ref["calculation_snapshot"]==structure["event_page"]["snapshot"]
    assert ref["authority"]=="CLIENT_SUPPLIED_UNVERIFIED"
    assert (ref["observation_time"],ref["confirmation_time"],ref["first_eligible_at"])==(
        START+3600,START+3900,START+3900)
    assert crt["context_configuration"]["structure"]["left"]==1
    assert crt["context_configuration"]["policy"]=="shared"
    assert first["diagnostics"]["executions"]["structure"]==1
    assert first["diagnostics"]["executions"]["fvg"]==1
    assert first["diagnostics"]["executions"]["liquidity"]==1
    assert first["diagnostics"]["executions"]["order_blocks"]==1
    zero=by_id(evaluate(Evaluation.model_validate(configured_request(zero=True))))
    assert zero["candle_range_theory"]["crt_sweeps"]==crt["crt_sweeps"]
    assert zero["market_structure"]["pivots"]==[] and zero["market_structure"]["breaks"]==[]
    assert zero["order_blocks"]["ob_formations"]==[]


@pytest.mark.parametrize("changed",[
    {"left":2},{"tolerance_bps":1},{"market_schedule":{"kind":"continuous"}},{"algorithm_version":1},
])
def test_different_structure_configurations_are_never_reused(changed):
    data=configured_request()
    data["indicators"][2]["structure"]={**data["indicators"][2]["structure"],**changed}
    result=evaluate(Evaluation.model_validate(data))
    assert result["diagnostics"]["executions"]["structure"]==2
    output=by_id(result)
    assert output["liquidity_sweeps"]["analysis"]["configuration_fingerprint"]!=output["market_structure"]["analysis"]["configuration_fingerprint"]
    assert output["candle_range_theory"]["crt_formations"]


def test_explicit_independent_context_publishes_configuration():
    data=configured_request("independent")
    data["indicators"][0]["context"]["structure"]={"algorithm_version":2,"left":2,"right":1}
    result=evaluate(Evaluation.model_validate(data))
    cfg=by_id(result)["candle_range_theory"]["context_configuration"]
    assert cfg["structure"]["left"]==2 and cfg["policy"]=="independent"
    data["indicators"][0]["context"]["policy"]="shared"
    with pytest.raises(ValidationError):
        Evaluation.model_validate(data)


def test_as_of_filters_both_axes_configuration_and_source_and_orders_ties():
    index=EvidenceIndex()
    for id_,observed,available,source,config in [
        ("a",90,100,"A","one"),("b",90,100,"A","one"),("later",91,110,"A","one"),
        ("future-observation",99,101,"A","one"),("other-source",90,101,"B","one"),
        ("other-config",90,101,"A","two"),
    ]:
        index.add("events",{"id":id_,"observation_time":observed,"available_at":available,
                            "source_identity":{"feed":source},"configuration_fingerprint":config})
    index.finish()
    filters={"category":"events","source_identity":{"feed":"A"},"configuration":"one"}
    assert index.as_of(99,91,**filters)=={}
    assert index.as_of(109,91,**filters)["events"]["id"]=="b"
    assert index.as_of(110,91,**filters)["events"]["id"]=="later"


def test_reader_scope_cutoff_manifest_limits_and_rows_are_isolated():
    request=Evaluation.model_validate(payload([(102,105,100,103)]))
    calls=[]
    def loader(current,start,end,step):
        calls.append((current.feed_id,current.price_basis,current.cutoff,tuple(current.partitions)))
        return {"rows":[(EvidenceBar(time=START,open=102,high=105,low=100,close=103),START+300)],"evidence":{}}
    coordinator=AnalysisCoordinator(request,loader)
    first=coordinator.history(request,START,START+300,300)
    assert coordinator.history(request,START,START+300,300) is first
    for change in ({"feed_id":"other"},{"price_basis":"BID"},{"cutoff":START+600},
                   {"timeframe":"M15"},{"frozen":True}):
        coordinator.history(request.model_copy(update=change),START,START+300,300)
    assert len(calls)==6
    with pytest.raises(ValidationError):
        first["rows"][0][0].high=99
    with pytest.raises(ResourceLimit,match="20,000"):
        coordinator.history(request,START,START+20_000*300,300)
    assert len(calls)==6


def test_partition_cache_is_read_only_and_aggregate_budget_stops_reader():
    session=ArchiveSession()
    part=dict(id="1",sha256="a"*64,relative_path="p.parquet",provider="MT5",feed_id="f",
              symbol="EURUSD",timeframe="M5",price_basis="BID",rows_count=1)
    reads=[]
    def read(record):
        reads.append(record["id"])
        return [{"time":START}]
    batch=session.read(part,read)
    assert session.read(part,read) is batch
    with pytest.raises(TypeError):
        batch[0]["time"]=0
    for i in range(2,51):
        session.read({**part,"id":str(i)},read)
    with pytest.raises(ResourceLimit):
        session.read({**part,"id":"51"},read)
    assert (len(reads),session.counts["checksum_attempts"],session.counts["partition_reuses"])==(50,50,1)


def test_overlapping_archive_windows_share_verified_partition(client):
    from research_backend.app import storage
    start=START//86400*86400-86400
    data=payload([(102,105,100,103)]*432,start=start,feed="overlap",basis="BID",
                 rules={"history_bars":10000,"history_start":start})
    chunks=(data["candles"][:300],data["candles"][300:])
    parts=[storage.write_partition("MT5","overlap","EURUSD","candles","M5","BID",chunk)
           for chunk in chunks]
    data.update(source="MT5_SAVED",frozen=True,candles=[],load_reference=True,
                partitions=[{key:p[key] for key in ("id","sha256")} for p in parts])
    data["indicators"].append({"id":"previous_day","schedule":{"kind":"continuous"}})
    response=client.post("/api/indicators/evaluate",json=data)
    assert response.status_code==200,response.text
    result=response.json()
    archive=result["diagnostics"]["archive"]
    assert (archive["checksum_attempts"],archive["partition_decodes"],archive["partition_reuses"],
            archive["decoded_records"])==(2,2,1,432)
    assert by_id(result)["previous_day"]["coverage"]["status"]=="COMPLETE"
    path=storage.MARKET_DIR/parts[0]["relative_path"]
    path.write_bytes(path.read_bytes()+b"broken")
    broken=by_id(client.post("/api/indicators/evaluate",json=data).json())
    assert broken["candle_range_theory"]["crt_formations"]==[]
    assert "SOURCE_INTEGRITY_FAILURE" in broken["previous_day"]["warnings"]


def test_calculation_cycle_fails_explicitly():
    request=Evaluation.model_validate(payload([(102,105,100,103)]))
    coordinator=AnalysisCoordinator(request,None)
    with pytest.raises(ValueError,match="cycle"):
        coordinator.run("crt","cycle",[],lambda:coordinator.run("crt","cycle",[],lambda:None))


def test_context_selection_changes_snapshot_and_rejects_old_cursor(client):
    data=configured_request()
    base=START-(START%3600)
    data["candles"]=payload([(102,105,100,103)]*36,start=base)["candles"]
    data["cutoff"]=base+36*300
    data["indicators"][0]["crt_delivery"]={"formation_limit":1}
    first=client.post("/api/indicators/evaluate",json=data).json()["results"][0]
    cursor=first["crt_page"]["formations"]["next_cursor"]
    assert cursor
    data["indicators"][1]["structure"]={**data["indicators"][1]["structure"],"left":2}
    second=client.post("/api/indicators/evaluate",json=data).json()["results"][0]
    assert first["crt_formations"][0]["range_id"]==second["crt_formations"][0]["range_id"]
    assert first["crt_page"]["snapshot"]!=second["crt_page"]["snapshot"]
    data["indicators"][0]["crt_delivery"]["formation_cursor"]=cursor
    assert client.post("/api/indicators/evaluate",json=data).status_code==409


def test_shared_crt_session_context_uses_effective_declared_hours():
    data=configured_request()
    custom={"timezone":"UTC","start":"10:00","end":"11:00"}
    data["indicators"].append({"id":"session_asia","session":custom})
    result=evaluate(Evaluation.model_validate(data))
    crt=by_id(result)["candle_range_theory"]
    assert crt["context_configuration"]["levels"]["session_asia"]=={"origin":"selected","session":custom}
    context=crt["crt_sweeps"][0]["context"]
    assert context["session_asia_high"]["confirmation_time"]==START+3600
    assert result["diagnostics"]["reuse"]["levels"]==1


def test_optional_dependency_failure_is_explicit_and_preserves_reference():
    data=configured_request()
    data["indicators"][4]["fvg"]={"history_bars":10000,"history_start":START}
    request=Evaluation.model_validate(data)
    def failed(*args):
        return {"rows":[],"evidence":{"status":"SOURCE_INTEGRITY_FAILURE","context_authority":"UNAVAILABLE"}}
    result=by_id(evaluate(request,failed))
    assert result["fair_value_gap"]["formations"]==[]
    crt=result["candle_range_theory"]
    assert crt["context_configuration"]["failures"]=={"fvg":"SOURCE_INTEGRITY_FAILURE"}
    assert crt["crt_sweeps"][0]["state"]=="HIGH_SWEPT_RECLAIMED"
    assert "fvg" not in crt["crt_sweeps"][0]["context"]
