"""Benchmark schema 2 against the existing 30-sample validation method."""
import importlib.util
import json
from pathlib import Path
import platform
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from research_backend.app import db
from research_backend.app.main import app
from research_backend.app.strategy_binding import AttemptDecisionTrace
from research_backend.app.strategy_catalog import capability_catalog
from research_backend.app.strategy_definitions import StrategyDefinitionV2, validate_definition
from research_backend.app.strategy_evidence import fingerprint
from research_backend.app.strategy_routes import template_v2, templates

DEST = Path("docs/implementation/evidence/TF-I09A-integrity")
PREVIOUS = DEST.parent / "TF-I09A"
ROUTE = "/api/strategies/v1"
spec = importlib.util.spec_from_file_location("original_benchmark", PREVIOUS / "benchmark.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
measure = previous.measure


def emit(name, value):
    (DEST / name).write_text(json.dumps(value, indent=2)+"\n", encoding="utf-8")


def graph(binding):
    data = template_v2("bearish")
    reference, = data["context"]["children"]
    sample = ({**reference,"observation":"CRT_STATE","expected":"EXPANSION_OBSERVED",
               "selection":"BOUND_FAMILY","same_family_as":reference["alias"]} if binding
              else data["setup"]["children"][1])
    groups = [
        {"type":"GROUP","id":f"nested-{i}","operator":"ALL","children":[
            {**sample,"id":f"node-{i}-{j}","alias":None} for j in range(21)
        ]} for i in range(4)
    ]
    for depth in range(3):
        groups[0] = {"type":"GROUP","id":f"wrapper-{depth}","operator":"ALL","children":[groups[0]]}
    data["context"]["children"] = [reference,*groups]
    assert validate_definition(data)["valid"], validate_definition(data)["errors"]
    return data


def main():
    data = template_v2("bearish")
    max_graph, aliases = graph(False), graph(True)
    definition = validate_definition(data)["definition"]
    semantic = {key:value for key,value in definition.items() if key not in {"name","status","description"}}
    result = {"python":platform.python_version(),"platform":platform.platform(),"warmups":3,"iterations":30,
              "fixture_max_nodes":96,"fixture_max_depth":6,"fixture_same_family_links":85,
              "schema":measure(lambda:StrategyDefinitionV2.model_validate(data),lambda _:None),
              "nested_validation":measure(lambda:validate_definition(data)),
              "max_graph_validation":measure(lambda:validate_definition(max_graph)),
              "binding_graph_validation":measure(lambda:validate_definition(aliases)),
              "fingerprint_hash":measure(lambda:fingerprint(semantic),len),
              "catalog":measure(lambda:capability_catalog(2))}
    emit("schema-v2.json",StrategyDefinitionV2.model_json_schema())
    emit("attempt-trace-v2.json",AttemptDecisionTrace.model_json_schema())
    emit("catalog-v2.json",capability_catalog(2))
    emit("templates-v2.json",templates())
    with TemporaryDirectory() as directory:
        old = db.DATABASE
        try:
            db.DATABASE = Path(directory) / "benchmark.sqlite3"
            db.initialize()
            with TestClient(app) as client:
                def checked(reply):
                    assert reply.is_success, reply.text
                    return reply
                def create():
                    return checked(client.post(ROUTE,json=data))
                initial = create().json()
                url = ROUTE + "/" + initial["strategy_id"]
                version = initial["version"]
                def revise():
                    nonlocal version
                    reply = checked(client.post(url+"/revisions",json={"expected_version":version,"definition":data}))
                    version += 1
                    return reply
                size = lambda reply:len(reply.content)
                result["create"] = measure(create,size)
                result["revise"] = measure(revise,size)
                result["list"] = measure(lambda:checked(client.get(ROUTE)),size)
                result["history"] = measure(lambda:checked(client.get(url+"/history")),size)
                entry = {**data,"trade_intent":{**data["trade_intent"],"entry":{
                    "kind":"LIMIT","price":{"kind":"FIXED_ABSOLUTE","value":1.05,"basis":"BID"}}}}
                emit("api-example.json",{"method":"POST","path":ROUTE+"/validate","body":entry,
                     "response":checked(client.post(ROUTE+"/validate",json=entry)).json()})
        finally:
            db.DATABASE = old
    emit("benchmark.json",result)


if __name__ == "__main__":
    main()
