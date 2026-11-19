from datetime import date
from hashlib import sha256

import pytest

from research_backend.app import historical_csv, storage
from research_backend.app.db import one, rows


DAY = date(2025,1,2)


def export(minutes):
    lines = ["Etc/UTC,Open,High,Low,Close,Volume"]
    for minute in minutes:
        lines.append(f"2025-01-02T00:{minute:02}:00+00:00,1.10001,1.10003,1.1,1.10002,12500")
    return ("\n".join(lines)+"\n").encode()


def test_web_minutes_keep_volume_and_materialize_only_complete_m5(client):
    payload = export([4,0,3,2,1,6,7,8,9,0])
    name = historical_csv.source_name("EURUSD",DAY)
    result = historical_csv.import_csv(payload,"EURUSD",DAY,name)
    assert result["source"]["sha256"] == sha256(payload).hexdigest()
    assert result["quality"]["valid_m1"] == 9 and result["quality"]["duplicates"] == 1
    assert historical_csv.import_csv(payload,"EURUSD",DAY,name) == result
    m5 = one("SELECT * FROM datasets WHERE timeframe='M5'")
    assert storage.read_partition(m5) == [
        {"time":1735776000,"open":1.10001,"high":1.10003,"low":1.1,"close":1.10002,"volume":62500}]
    ask_name = historical_csv.source_name("EURUSD",DAY,"ASK")
    ask = historical_csv.import_csv(payload,"EURUSD",DAY,ask_name,side="ASK")
    assert ask["feed_id"] != result["feed_id"]
    assert len(rows("SELECT id FROM datasets")) == 4
    query = client.get("/api/market/candles",params={"source":"DUKASCOPY_SAVED"})
    assert query.status_code == 200 and query.json()["price_basis"] == "BID"


@pytest.mark.parametrize("pattern,substitution",[(b"+00:00",b"+05:30"),(b"12500",b"NaN"),
    (b"1.10001",b"1.100001"),(b"High",b"HI"),(b"00:00:00",b"00:00:01")])
def test_web_export_is_strictly_validated(pattern,substitution):
    with pytest.raises(ValueError):
        historical_csv.read_minutes(export([0]).replace(pattern,substitution),"EURUSD",DAY)


def test_conflicting_web_minutes_or_source_replacement_fail(client):
    name = historical_csv.source_name("EURUSD",DAY)
    historical_csv.import_csv(export([0,1]),"EURUSD",DAY,name)
    with pytest.raises(ValueError,match="Overlapping historical"):
        historical_csv.import_csv(export(range(5)),"EURUSD",DAY,name)
    with pytest.raises(ValueError,match="Original download name"):
        historical_csv.import_csv(export([0,1]),"EURUSD",DAY,historical_csv.source_name("GBPUSD",DAY))
    with pytest.raises(ValueError,match="Conflicting"):
        historical_csv.read_minutes(export([0])+export([0]).splitlines()[1].replace(b"12500",b"12501"),
                                    "EURUSD",DAY)
