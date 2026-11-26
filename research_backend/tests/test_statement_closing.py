"""Boundaries and cross-version API reads on genuinely persisted V1 reports."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from research_backend.app import db
from research_backend.tests.statement_v1_probe import cases, EXAMPLE

ROOT = "/api/statements"
EXPECTED = {
    "control": ("RECONCILED", "spec1", 1098, 0),
    "exact": ("RECONCILED", "spec1", 1098, 0),
    "one_micro_late": ("UNRESOLVED", None, None, None),
    "period_only": ("RECONCILED", "spec1", 1098, 0),
    "successor": ("RECONCILED", "spec1", 1098, 0),
    "gap": ("UNRESOLVED", None, None, None),
    "inside": ("UNRESOLVED", "next", 2196, 1098),
}


def check(case, report):
    pos = report["positions"][0]
    assert (report["status"], pos.get("spec"), report["margin_minor"],
            report["margin_residual_minor"]) == EXPECTED[case]
    assert report["balance_residual_minor"] == 0
    assert report["equity_residual_minor"] == (None if case in ("gap", "one_micro_late") else 0)
    assert report["valuation_policy"] == "STATEMENT_CLOSING_VALUATION_V2"
    assert pos["valuation_cutoff"] == "2025-11-15T00:00:00.000000+00:00"
    if case != "one_micro_late":
        assert pos["terms_at"] == "2025-11-14T23:59:59.000000+00:00"
    if case in ("gap", "one_micro_late"):
        reason = "MISSING_OR_OVERLAPPING_DATED_SPEC" if case == "gap" else "STALE_OR_UNAVAILABLE_MARK"
        assert {"source":"EURUSD","reason":reason} in report["issues"]
    if case == "inside":
        assert pos["leverage"] == "50" and pos["spec_start"] == pos["terms_at"]


def test_fresh_closing_matrix(client):
    for case,s in cases():
        before=len(client.get(ROOT).json())
        response=client.post(ROOT,json=s)
        if case=="submicro":
            assert response.status_code==422 and len(client.get(ROOT).json())==before
            continue
        assert response.status_code==201
        path=ROOT+"/"+response.json()["id"]
        result=client.post(path+"/advance?cursor=0").json()
        check(case,result["report"])
        assert result["original_v1_report"] is None
        assert result==client.get(path).json()==client.post(path+"/advance?cursor=0").json()


@pytest.mark.parametrize("path",[("marks","as_of"),("marks","available"),("rates","at"),("rates","available"),
                                 ("rows","at"),("specs","start"),("specs","end")])
def test_precision_before_insertion(client,path):
    s=json.loads(EXAMPLE.read_bytes())
    s["rates"]=[dict(id="fx",source="control",base="EUR",quote="USD",rate="1",at=s["start"],available=s["end"])]
    group,field=path
    s[group][0][field]=s[group][0][field].replace("Z",".0000001Z")
    response=client.post(ROOT,json=s)
    assert response.status_code==422 and "microseconds" in response.json()["detail"]
    assert client.get(ROOT).json()==[]


def test_closing_point_mark_and_exact_offset(client):
    s=dict(cases())["successor"]
    s["marks"][0].update(as_of="2025-11-15T01:00:00.0000000+01:00",available=s["end"])
    identity=client.post(ROOT,json=s).json()["id"]
    report=client.post(f"{ROOT}/{identity}/advance?cursor=0").json()["report"]
    pos=report["positions"][0]
    assert (report["status"],pos["spec"],pos["terms_at"])==("RECONCILED","spec1","2025-11-14T23:59:59.999999+00:00")


def test_reopen_v1_database_and_repeat_after_restart(tmp_path,monkeypatch):
    output=subprocess.check_output([sys.executable,str(Path(__file__).with_name("statement_v1_probe.py")),
                                    "--directory",str(tmp_path)],text=True)
    originals={r["case"]:r for r in map(json.loads,output.splitlines())}
    monkeypatch.setattr(db,"DATABASE",tmp_path/"tradeforge.sqlite3")
    def persisted():
        with db.transaction() as conn:
            return [tuple(row) for row in conn.execute(
                "SELECT v.id,v.raw,r.payload,p.state FROM statement_versions v JOIN statement_reports r USING(id) "
                "JOIN statement_progress p USING(id) ORDER BY id")]
    old=persisted()
    from research_backend.app.main import app
    prior={}
    for cycle in range(2):
        with TestClient(app) as client:
            for case,s in cases():
                orig=originals[case]
                path=ROOT+"/"+orig["id"]
                detail=client.get(path).json()
                stored=detail["original_v1_report"]
                assert stored==json.loads(next(row[2] for row in old if row[0]==orig["id"]))
                assert detail==client.post(path+"/advance?cursor=0").json()
                retry=client.post(ROOT,json=s)
                if case=="submicro":
                    assert retry.status_code==422 and detail["report"] is None
                    assert detail["assessment_error"]["value"]==s["marks"][0]["available"]
                    assert stored["status"]=="RECONCILED"
                else:
                    assert retry.status_code==201 and retry.json()==detail
                    check(case,detail["report"])
                if cycle:
                    assert prior[case]==detail
                prior[case]=detail
            assert len(client.get(ROOT).json())==len(old)
        assert persisted()==old

    code = """import json, sys
from fastapi.testclient import TestClient
from research_backend.app.main import app
with TestClient(app) as client:
    print(json.dumps([client.get('/api/statements/'+key).json() for key in sys.argv[1:]]))
"""
    after_restart = subprocess.check_output(
        [sys.executable, "-c", code, *(item["id"] for item in prior.values())],
        env={**os.environ, "TRADEFORGE_DATA_DIR":str(tmp_path), "TRADEFORGE_STORAGE":str(tmp_path)},
        text=True,
    )
    assert json.loads(after_restart) == list(prior.values())
    assert persisted() == old


def test_terms_change_after_mark_before_delivery(client):
    s = dict(cases())["inside"]
    switch = "2025-11-14T23:59:59.500000Z"
    s["specs"][0]["end"] = s["specs"][1]["start"] = switch
    s["marks"][0]["available"] = s["end"]
    identity = client.post(ROOT,json=s).json()["id"]
    report = client.post(f"{ROOT}/{identity}/advance?cursor=0").json()["report"]
    assert report["status"] == "RECONCILED" and report["margin_minor"] == 1098
    assert report["positions"][0]["spec"] == "spec1"
