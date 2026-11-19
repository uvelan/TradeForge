"""Measure TF-I09A definition operations and emit deterministic contract examples."""
import gc
import json
from pathlib import Path
from statistics import median, quantiles
from tempfile import TemporaryDirectory
from time import perf_counter_ns
import tracemalloc
import platform

from fastapi.testclient import TestClient

from research_backend.app import db
from research_backend.app.main import app
from research_backend.app.strategy_catalog import capability_catalog
from research_backend.app.strategy_definitions import StrategyDefinition, validate_definition
from research_backend.app.strategy_evidence import fingerprint
from research_backend.app.strategy_routes import template, templates

ROUTE = "/api/strategies/v1"


def json_size(value):
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


def measure(operation, size=json_size, repeats=30):
    for _ in range(3):
        operation()
    times = []
    for _ in range(repeats):
        start = perf_counter_ns()
        result = operation()
        times.append((perf_counter_ns() - start) / 1_000_000)
    gc.collect()
    tracemalloc.start()
    try:
        operation()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {"p50_ms":round(median(times),3),
            "p95_ms":round(quantiles(times,n=100,method="inclusive")[94],3),
            "peak_python_bytes":peak,"response_bytes":size(result)}


def graph():
    data = template("bearish")
    rule = data["context"]["children"][0]
    groups = [
        {"type":"GROUP","id":f"nested-{group}","operator":"ALL","children":[
            {**rule,"id":f"reference-{group}-{n}"} for n in range(count)
        ]} for group,count in enumerate((21,21,21,22))
    ]
    for level in range(3):
        groups[0] = {"type":"GROUP","id":f"wrapper-{level}","operator":"ALL","children":[groups[0]]}
    data["context"]["children"] = groups
    validation = validate_definition(data)
    assert validation["valid"], validation["errors"]
    return data


def main():
    dest = Path("docs/implementation/evidence/TF-I09A")
    data = template("bearish")
    graph_fixture = graph()
    normalized = validate_definition(data)['definition']
    semantic = {key:value for key,value in normalized.items() if key not in {'name','description','status'}}

    def emit(name, value):
        (dest / name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")

    result = {"python":platform.python_version(),"platform":platform.platform(),
              "warmups":3,"iterations":30,"fixture_max_nodes":96,"fixture_max_depth":6,
              "schema":measure(lambda:StrategyDefinition.model_validate(data), lambda _:None),
              "nested_validation":measure(lambda:validate_definition(data)),
              "max_graph_validation":measure(lambda:validate_definition(graph_fixture)),
              "fingerprint_hash":measure(lambda:fingerprint(semantic),lambda value:len(value)),
              "catalog":measure(capability_catalog)}
    emit("strategy-schema.json", StrategyDefinition.model_json_schema())
    emit("condition-catalog.json", capability_catalog())
    emit("templates.json", templates())
    with TemporaryDirectory() as directory:
        previous = db.DATABASE
        try:
            db.DATABASE = Path(directory) / "benchmark.sqlite3"
            db.initialize()
            with TestClient(app, raise_server_exceptions=True) as client:
                def checked(response):
                    assert response.is_success,response.text
                    return response
                def create():
                    return checked(client.post(ROUTE,json=data))
                initial = create().json()
                url = ROUTE + "/" + initial["strategy_id"]
                current = initial["version"]
                def revise():
                    nonlocal current
                    reply = checked(client.post(url+"/revisions",json={
                        "expected_version":current,"definition":data,
                    }))
                    current += 1
                    return reply
                size = lambda response:len(response.content)
                result["create"] = measure(create,size)
                result["revise"] = measure(revise,size)
                result["list"] = measure(lambda:checked(client.get(ROUTE)),size)
                result["history"] = measure(lambda:checked(client.get(url+"/history")),size)
                emit("api-example.json",{"method":"POST","path":ROUTE+"/validate",
                                         "body":data,"response":checked(client.post(ROUTE+"/validate",json=data)).json()})
        finally:
            db.DATABASE = previous
    emit("benchmark.json",result)


if __name__ == "__main__":
    main()
