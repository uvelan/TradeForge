from datetime import UTC, datetime

import httpx

from research_backend.app import binance


def test_binance_returns_closed_crypto_candles_only(client, monkeypatch):
    now = int(datetime.now(UTC).timestamp() * 1000)
    current = now // 60_000 * 60_000

    def public_get(url, *, params, timeout):
        assert url == binance.KLINES_URL
        assert params == {"symbol": "BTCUSDT", "interval": "1m", "limit": 11}
        rows = [[ts, "20", "23", "18", "21", "4", ts + 59_999] for ts in (current - 60_000, current)]
        return httpx.Response(200, json=rows, request=httpx.Request("GET", url))

    monkeypatch.setattr(binance.httpx, "get", public_get)
    query = "/api/market/candles?timeframe=M1&source=BINANCE_SPOT&limit=10&symbol="
    result = client.get(query + "BTCUSDT").json()
    assert result["source"] == "BINANCE_SPOT"
    assert result["synthetic"] is False
    assert result["candles"] == [{"time": current // 1000 - 60, "open": 20, "high": 23, "low": 18, "close": 21}]
    assert client.get(query + "EURUSD").status_code == 422
