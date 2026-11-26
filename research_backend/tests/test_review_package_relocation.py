"""Verify both review package formats after a real offline workspace restore."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import shutil
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from research_backend.app import db, domain, storage
from research_backend.tests.test_month_replay import source
from scripts.restore_local_workspace import restore
from tradeforge.api import create_app


def _select_profile(monkeypatch, root):
    research = root / "research"
    monkeypatch.setenv("TRADEFORGE_STORAGE", str(root))
    monkeypatch.setenv("TRADEFORGE_DATA_DIR", str(research))
    monkeypatch.setattr(db, "DATABASE", research / "tradeforge.sqlite3")
    monkeypatch.setattr(storage, "MARKET_DIR", research / "market")
    monkeypatch.setattr(domain, "EXPORT_DIR", research / "ai-exports")
    monkeypatch.setattr(domain, "JOURNAL_DIR", research / "journal")
    monkeypatch.setattr(domain, "JOURNAL_FILE", domain.JOURNAL_DIR / "journal.md")
    for directory in (storage.MARKET_DIR, domain.EXPORT_DIR, domain.JOURNAL_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def _new_export(client, plan, kind):
    if kind == "pre-trade":
        trade = client.post("/research/api/trades", json=plan)
        assert trade.status_code == 201, trade.text
        return client.post(f"/research/api/ai/exports/{trade.json()['id']}"), None
    source(13, [(3595000, 110000), (3596000, 110012), (3597000, 110013)])
    source(14, [(100, 110230)])
    path = "/research/api/month-replay/2025-11"
    report = client.get(path)
    assert report.status_code == 200, report.text
    route = f"{path}/{report.json()['session']}"
    index = 0
    for _ in range(25):
        response = client.post(f"{route}/advance", params={"index": index})
        assert response.status_code == 200, response.text
        batch = response.json()
        index = batch["next_index"]
        targets = [event for event in batch["events"] if event["kind"] == "TARGET"]
        if targets:
            cutoff = targets[0]["quote"]["time_ms"]
            break
    else:
        pytest.fail("Recorded replay did not reach the fixed target")
    return client.post(f"{route}/packages", params={"cutoff_ms": cutoff}), route


@pytest.mark.parametrize("kind", ["pre-trade", "recorded"])
def test_restore_review_package_under_new_absolute_root(tmp_path, monkeypatch, plan, kind):
    original = tmp_path / "original"
    restored = tmp_path / "restored"
    _select_profile(monkeypatch, original)
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        export, replay = _new_export(client, plan, kind)
        assert export.status_code == 201, export.text
        package = export.json()
        package_id = package["package_id"]
        download = package["download_url"]
        detail = f"/research/api/ai/packages/{package_id}/recorded"
        listing = f"{replay}/packages" if replay else "/research/api/ai/packages"
        before = client.get(download)
        assert before.status_code == 200, before.text
        original_zip = before.content
        assert sha256(original_zip).hexdigest() == package["sha256"]
        with ZipFile(BytesIO(original_zip)) as archive:
            review = archive.read("response/AI_REVIEW_TEMPLATE.json")
        if replay:
            response = client.get(detail)
            assert response.status_code == 200, response.text
            recorded = response.json()
            assert len(recorded["template"]["claims"]) == 1
        saved = db.one("SELECT * FROM ai_packages WHERE id=?", (package_id,))
        assert Path(saved["location"]).parent == domain.EXPORT_DIR

    report = restore(original, restored)
    assert report["references"]["counts"]["exports"] == 1
    copied = restored / "research/ai-exports" / f"{package_id}.zip"
    assert copied.read_bytes() == original_zip
    _select_profile(monkeypatch, restored)
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        # Never fall back to the issuer's copy, even if it still exists.
        copied.unlink()
        assert Path(saved["location"]).read_bytes() == original_zip
        assert client.get(download).status_code == 404
        copied.write_bytes(original_zip)
    shutil.rmtree(original)

    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        assert db.one("SELECT * FROM ai_packages WHERE id=?", (package_id,)) == saved
        assert [item["id"] for item in client.get(listing).json()] == [package_id]
        response = client.get(download)
        detail_response = client.get(detail) if replay else None
        headers = {"Content-Type": "application/json"}
        result = client.post("/research/api/ai/reviews", content=review, headers=headers)
        observed = {"download": response.status_code, "review": result.status_code}
        expected = {"download": 200, "review": 201}
        if detail_response is not None:
            observed["detail"] = detail_response.status_code
            expected["detail"] = 200
        assert observed == expected
        assert response.content == original_zip
        if detail_response is not None:
            assert detail_response.json() == recorded
        review_id = result.json()["id"]
        assert client.get(f"/research/api/ai/reviews/{review_id}/original").content == review
        assert client.post("/research/api/ai/reviews", content=review, headers=headers).json()["id"] == review_id
        assert copied.read_bytes() == original_zip

        copied.write_bytes(original_zip + b"damaged")
        assert client.get(download).status_code == 409
        if replay:
            assert client.get(detail).status_code == 409
        assert client.post("/research/api/ai/reviews", content=review, headers=headers).status_code == 409
        assert len(client.get("/research/api/ai/reviews").json()) == 1
        assert db.one("SELECT * FROM ai_packages WHERE id=?", (package_id,)) == saved
