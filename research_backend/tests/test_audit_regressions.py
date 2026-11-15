from datetime import UTC, datetime
import io
import json
import lzma
from pathlib import Path
import struct
import zipfile

from research_backend.app.backtest import breakout_backtest
from research_backend.app.db import transaction
from research_backend.app.schemas import ReviewV1


def test_ambiguous_only_backtest_reports_bounds():
    candles = [{"time": i * 60, "open": 100, "high": 101, "low": 99, "close": 100} for i in range(30)]
    candles[2].update(high=104, close=103)
    candles[3].update(high=106, low=98)
    result = breakout_backtest(candles)
    assert result["eligible_signals"] == result["ambiguous_excluded"] == 1
    assert result["ambiguous_rate"] == 100
    assert result["expectancy_r"] is None and result["win_rate"] is None
    assert result["expectancy_bounds_r"] == {"worst": -1, "best": 2}


def test_analytics_includes_oldest_closed_trade(client):
    with transaction() as db:
        for index in range(201):
            uid = f"TEST-{index}"
            stamp = f"{index:04d}"
            db.execute("INSERT INTO trades VALUES (?, 'EURUSD', 'BUY', 'CLOSED', ?, ?)", (uid, stamp, stamp))
            db.execute("INSERT INTO trade_outcomes VALUES (?, ?, ?, '', ?)",
                       (uid, "-100" if index == 0 else "10", "-10" if index == 0 else "1", stamp))
    result = client.get("/api/analytics").json()
    assert result["closed"] == result["trades"] == 201
    assert result["expectancy_r"] == 0.945
    assert result["net_pnl_usd"] == "1900"


def test_published_review_schema_round_trips_export(client, plan):
    uid = client.post("/api/trades", json=plan).json()["id"]
    package = client.post(f"/api/ai/exports/{uid}").json()["package_id"]
    response = client.get(f"/api/ai/packages/{package}/download")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        template = json.loads(archive.read("response/AI_REVIEW_TEMPLATE.json"))
    published = json.loads((Path(__file__).resolve().parents[2] / "docs/schemas/ai-review-v1.schema.json").read_text())
    published.pop("$schema")
    assert published == ReviewV1.model_json_schema()
    assert client.post("/api/ai/reviews", json=template).status_code == 201
    template["findings"][0]["metric_refs"] = ["invented"]
    assert client.post("/api/ai/reviews", json=template).status_code == 422
    template["findings"][0]["metric_refs"] = ["maximum_planned_loss_usd"]
    template["findings"][0]["metric_claims"] = {"maximum_planned_loss_usd": "51"}
    assert client.post("/api/ai/reviews", json=template).status_code == 422
    template["findings"][0]["metric_claims"]["maximum_planned_loss_usd"] = "50"
    assert client.post("/api/ai/reviews", json=template).status_code == 201


def test_completed_bi5_final_bar_and_adjacent_partition_range(client):
    packet = b"".join(struct.pack(">IIIff", minute * 60_000, 108550, 108540, 1.0, 1.0)
                      for minute in range(0, 60, 5))
    payload = lzma.compress(packet, format=lzma.FORMAT_ALONE)

    def load(day, hour):
        response = client.post(f"/api/market/import/bi5?symbol=EURUSD&day={day}&hour={hour}",
                               files={"file": ("ticks.bi5", payload)})
        assert response.status_code == 200, response.text
        assert response.json()["candle_rows"] == 12
        return response.json()

    first = load("2025-01-02", 23)
    assert load("2025-01-02", 23)["tick_dataset"]["id"] == first["tick_dataset"]["id"]
    load("2025-01-03", 0)
    assert len(client.get("/api/datasets").json()) == 4
    start = int(datetime(2025, 1, 2, 23, tzinfo=UTC).timestamp())
    result = client.get("/api/market/candles", params={
        "source": "DUKASCOPY_SAVED", "start": start, "end": start + 7200, "limit": 24,
    }).json()
    assert len(result["partitions"]) == 2
    assert result["coverage"]["gaps"] == []
    assert result["candles"][-1]["time"] == start + 6900
    recent = client.get("/api/market/candles", params={"source":"DUKASCOPY_SAVED","limit":24}).json()
    assert recent["partitions"] == result["partitions"]
    assert recent["candles"] == result["candles"]
