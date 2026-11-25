import json
import sqlite3
import pytest
from research_backend.app.db import rows, transaction
from test_plan_paper import journey


@pytest.mark.parametrize("conflict", [True, False])
def test_paper_import_claim_scope(client, journey, conflict, tmp_path, monkeypatch):
    trade, _, cmd = journey
    from research_backend.app import db as store
    path = "/api/paper/plans/" + trade["id"] + "/actions"
    clone = tmp_path / "baseline.sqlite3"
    from contextlib import closing
    with transaction() as db, closing(sqlite3.connect(clone)) as target:
        db.backup(target)
    with monkeypatch.context() as patch:
        patch.setattr(store, "DATABASE", clone)
        baseline = client.post(path, json=cmd)
        assert baseline.status_code == 200
    with transaction() as db:
        src = dict(db.execute("SELECT * FROM historical_imports").fetchone())
        db.execute("INSERT INTO historical_imports VALUES(?,?,?,?,?,?,?)",
                   (src["feed_id"], "EURUSD", 3, 3603, "0"*64, "test",
                    src["manifest"] if conflict else '{"candle_datasets":["broken"]}'))
    path = "/api/paper/plans/" + trade["id"] + "/actions"
    reply = client.post(path, json=cmd)
    if conflict:
        assert reply.status_code == 409 and "CONFLICTING_IMPORT_CLAIM" in reply.text
        assert not rows("SELECT * FROM plan_paper_events")
    else:
        assert reply.status_code == 200, reply.text
        assert reply.content == baseline.content
        assert reply.json()["event"]["price_units"] == 112007
        assert client.post(path,json=cmd).json() == reply.json()
        assert len(rows("SELECT * FROM plan_paper_events")) == 1


def test_no_trade_blind_then_immutable_assessment(client, journey):
    trade, _, _ = journey
    trade_id = trade["id"]
    url = f"/api/learning/reviews/{trade_id}/1"
    assert client.get(url).status_code == 404
    assert client.post("/api/trades/"+trade_id+"/decline",json={"reason":"Hidden original reason"}).is_success
    before = client.get(url).json()
    assert set(before) == {"phase","prompt","prompt_hash"}
    assert len(before["prompt"]["lanes"][0]["bars"]) == 1
    assert "Hidden original reason" not in json.dumps(before)
    assert client.post(url+"/reveal").status_code == 409
    payload = {"assessment":"Pass and wait for confirmation","prompt_hash":before["prompt_hash"]}
    assessed = client.post(url+"/assess",json=payload).json()
    assert assessed["phase"] == "ASSESSED" and "revealed" not in assessed
    assert client.post(url+"/assess",json=payload).json() == assessed
    assert client.post(url+"/assess",json=payload|{"assessment":"Rewritten"}).status_code == 409
    assert client.post(url+"/reveal").json()["revealed"]["reason"] == "Hidden original reason"
    drill = {"classification":"RULE_FOLLOWED","objective":"Rehearse the confirmation rule","due":"2020-01-01"}
    assert client.post(url+"/practice",json=drill).json()["phase"] == "DUE"
    assert client.post(url+"/complete").json()["phase"] == "DRILL_COMPLETED"
    assert len(rows("SELECT * FROM decision_review_steps")) == 4
    assert not rows("SELECT * FROM plan_paper_events")
