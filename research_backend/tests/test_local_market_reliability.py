"""Coverage and retry contracts on a small controlled BI5 file; provider acceptance runs locally."""
from hashlib import sha256

from research_backend.app import reviewed_archives
from research_backend.app.db import one
from research_backend.tests.test_historical_import import packet


def test_verified_file_can_have_grid_holes_and_unknown_provider_completeness(client, monkeypatch):
    payload = packet([(0, 110000, 110010, 1), (599999, 110020, 110030, 1)])
    digest = sha256(payload).hexdigest()
    monkeypatch.setitem(reviewed_archives.BI5_HOURS, digest, 13)

    def upload(data):
        return client.post("/api/market/import/reviewed", files={"file": ("source.bi5", data)})

    assert upload(payload + b"!").status_code == 422
    assert upload(b"x" * 1048577).status_code == 413
    first = upload(payload)
    assert first.status_code == 200, first.text
    assert first.json() == upload(payload).json()
    assert one("SELECT count(*) AS n FROM datasets")["n"] == 5
    assert one("SELECT payload FROM historical_bi5_sources WHERE sha256=?", (digest,))["payload"] == payload
    dataset = one("SELECT id FROM datasets WHERE timeframe='M5' AND price_basis='BID'")
    path = "/api/datasets/" + dataset["id"] + "/coverage"
    report = client.get(path).json()["origin"]
    assert report["file_observations"] == "ALL_RECORDS_VERIFIED"
    assert report["source_completeness"] == "UNVERIFIED"
    assert report["checked_bars"] == 2
    assert report["missing_slots"] == list(range(report["start"] + 600, report["end"], 300))

    from research_backend.app import main
    read = main.read_partition
    monkeypatch.setattr(main, "read_partition", lambda item: [{**bar, "high": 9} for bar in read(item)])
    rejected = client.get(path)
    assert rejected.status_code == 409
    assert "OHLC disagrees" in rejected.text
