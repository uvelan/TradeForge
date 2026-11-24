from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import sqlite3
import zipfile

import pytest

from research_backend.app.db import transaction


def import_review(client,review):
    data=json.dumps(review,indent=3).encode()
    response=client.post("/api/ai/reviews",content=data,headers={"Content-Type":"application/json"})
    assert response.status_code==201,response.text
    assert client.get(f"/api/ai/reviews/{response.json()['id']}/original").content==data
    return response.json()["id"],data


def test_operator_history_keeps_review_revisions_and_original_bytes_separate(client,plan):
    uid=client.post("/api/trades",json=plan).json()["id"]
    package=client.post(f"/api/ai/exports/{uid}").json()
    with zipfile.ZipFile(BytesIO(client.get(package["download_url"].removeprefix("/research")).content)) as archive:
        review=json.loads(archive.read("response/AI_REVIEW_TEMPLATE.json"))
    first,raw=import_review(client,review)
    second,_=import_review(client,dict(review,assessment="REJECTED"))
    path=f"/api/ai/reviews/{first}/decisions"
    assert client.get(path).json()=={"review_id":first,"current_sequence":0,"decisions":[]}
    assert client.get("/api/ai/reviews/unknown/decisions").status_code==404

    body={"decision":"NEEDS_FOLLOW_UP","rationale":"Inspect source evidence","expected_sequence":0}
    alternative=dict(body,decision="REJECTED")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda item:client.post(path,json=item),[body,alternative]))
    assert sorted(r.status_code for r in results)==[201,409]
    winner=next(r.json() for r in results if r.status_code==201)
    assert client.post(path,json=dict(body,decision=winner["decision"])).json()==winner
    next_body=dict(body,decision="ACCEPTED",rationale="Operator acknowledged limitations",expected_sequence=1)
    second_row=client.post(path,json=next_body).json()
    assert second_row["sequence"]==2 and second_row["decision"]=="ACCEPTED"
    result=client.get(path).json()
    assert result["current_sequence"]==2 and result["decisions"]==[winner,second_row]
    assert client.get(path,params={"after":1,"limit":1}).json()["decisions"]==[second_row]
    assert client.get(f"/api/ai/reviews/{second}/decisions").json()["current_sequence"]==0
    assert client.get(f"/api/ai/reviews/{first}/original").content==raw
    with transaction() as db:
        stored=db.execute("SELECT verification FROM ai_reviews WHERE id=?",(first,)).fetchone()
        assert stored["verification"]=="STRUCTURE_VALID_CONTENT_UNVERIFIED"
        with pytest.raises(sqlite3.IntegrityError,match="immutable record"):
            db.execute("UPDATE ai_review_decisions SET rationale='changed' WHERE review_id=?",(first,))
        with pytest.raises(sqlite3.IntegrityError,match="immutable record"):
            db.execute("DELETE FROM ai_review_decisions WHERE review_id=?",(first,))
