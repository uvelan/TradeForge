"""Shared wire-format policy precedes both schema and semantic validation."""
from hashlib import sha256
from io import BytesIO
import json
import zipfile

import pytest
from research_backend.app.db import transaction

URL="/api/ai/reviews"
JSON={"Content-Type":"application/json"}


def test_pretrade_rejects_duplicate_members_even_when_schema_would_accept(client,plan):
    uid=client.post("/api/trades",json=plan).json()["id"]
    package=client.post(f"/api/ai/exports/{uid}").json()
    with zipfile.ZipFile(BytesIO(client.get(package["download_url"].removeprefix("/research")).content)) as z:
        data=json.loads(z.read("response/AI_REVIEW_TEMPLATE.json"))
    raw=json.dumps(data,separators=(",",":")).encode()
    for key in ("summary","package_id"):
        correct=data[key] if key=="package_id" else data["findings"][0][key]
        pair=json.dumps(key)+":"+json.dumps(correct)
        alt=json.dumps(key)+':"different"'
        for replacement in (alt+","+pair,pair+","+alt,pair+","+pair):
            body=raw.replace(pair.encode(),replacement.encode(),1)
            assert body!=raw
            res=client.post(URL,content=body,headers=JSON)
            assert res.status_code==422 and "DUPLICATE_JSON_KEY" in res.json()["detail"]
            with transaction() as db:
                assert [db.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in
                        ("ai_reviews","ai_review_originals","ai_review_integrity")]==[0,0,0]
    submitted=raw+b"\r\n"
    r=client.post(URL,content=submitted,headers=JSON)
    assert r.status_code==201
    rid=r.json()["id"]
    assert client.post(URL,json=data).json()["id"]==rid
    assert client.get(f"{URL}/{rid}/original").content==submitted
    assert client.get(URL).json()[0]["original_integrity"]=="UNAMBIGUOUS_JSON"
    assert client.post(URL,json=dict(data,corrects_review_id=rid)).status_code==422
    with transaction() as db:
        assert db.execute("SELECT sha256 FROM ai_review_originals").fetchone()[0]==sha256(submitted).hexdigest()


@pytest.mark.parametrize("raw",[
    br'{"irrelevant":[[{"value":1,"\u0076alue":2}]]}',
    br'{"claims":[{"unit":"USD","unit":"USD per EUR"}]}',
])
def test_duplicate_precedes_even_invalid_schema(client,raw):
    response=client.post(URL,content=raw,headers=JSON)
    assert response.status_code==422 and "DUPLICATE_JSON_KEY" in response.json()["detail"]
    assert client.get(URL).json()==[]


def test_backfill_and_linked_pretrade_correction(client,plan):
    from research_backend.app import db
    from research_backend.app.schemas import ReviewV1
    from research_backend.app.domain import _json
    uid=client.post("/api/trades",json=plan).json()["id"]
    pkg=client.post(f"/api/ai/exports/{uid}").json()
    with zipfile.ZipFile(BytesIO(client.get(pkg["download_url"].removeprefix("/research")).content)) as z:
        clean=z.read("response/AI_REVIEW_TEMPLATE.json")
    parsed=json.loads(clean)
    raw=clean.replace(b'"schema_version": 1',b'"schema_version": 7,"schema_version": 1')
    assert parsed==json.loads(raw) and raw!=clean
    rid="REVIEW-OLD"
    with transaction() as conn:
        conn.execute("INSERT INTO ai_reviews VALUES(?,?,?,?,?,?)",(rid,pkg["package_id"],parsed["assessment"],
            _json(ReviewV1.model_validate(parsed).model_dump(exclude={"corrects_review_id"})),"STRUCTURE_VALID_CONTENT_UNVERIFIED",db.utc_now()))
        conn.execute("INSERT INTO ai_review_originals VALUES(?,?,?)",(rid,raw,sha256(raw).hexdigest()))
    db.initialize()
    assert client.get(URL).json()[0]["original_integrity"]=="DUPLICATE_JSON_KEY"
    assert client.post(URL,content=clean,headers=JSON).status_code==409
    other=client.post(f"/api/ai/exports/{uid}").json()["package_id"]
    fixed=dict(parsed,corrects_review_id=rid)
    assert client.post(URL,json=dict(fixed,package_id=other)).status_code==422
    child=client.post(URL,json=fixed)
    assert child.status_code==201 and child.json()["id"]!=rid
    assert client.post(URL,json=fixed).json()["id"]==child.json()["id"]
    assert client.get(URL).json()[0]["corrects_review_id"]==rid
    assert client.get(f"{URL}/{rid}/original").content==raw
