import json

from research_backend.app.db import transaction
from research_backend.app.historical_import import FEED, import_bi5
from research_backend.tests.test_historical_import import DAY, packet


def test_coverage_resolves_only_unique_claim_for_requested_partition(client):
    imported = import_bi5(packet([(1, 110000, 110004, 1)]), "EURUSD", DAY, 0)
    candle = imported["candle_datasets"][0]
    url = "/api/datasets/" + candle["id"] + "/coverage"
    baseline = client.get(url)
    assert baseline.status_code == 200
    def insert(start, text):
        with transaction() as db:
            db.execute("INSERT INTO historical_imports VALUES(?,?,?,?,?,?,?)",
                       (FEED,"EURUSD",start,start+3600,"0"*64,"test",text))
    insert(2, '{"candle_datasets":["broken"]}')
    assert client.get(url).json() == baseline.json()
    insert(3, json.dumps(imported))
    rejected = client.get(url)
    assert rejected.status_code == 409
    assert "CONFLICTING_IMPORT_CLAIM" in rejected.json()["detail"]
    assert candle["id"] in rejected.json()["detail"]
