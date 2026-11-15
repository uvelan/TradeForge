"""Public Binance Spot candles for the research terminal."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx


INTERVALS = {
    "M1": "1m", "M3": "3m", "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "H4": "4h", "D1": "1d",
}
KLINES_URL = "https://data-api.binance.vision/api/v3/klines"


def historical_candles(timeframe: str, limit: int) -> list[dict]:
    """Return completed BTC/USDT candles with UTC opening times."""
    cutoff = int(datetime.now(UTC).timestamp() * 1000)
    response = httpx.get(KLINES_URL, params={
        "symbol": "BTCUSDT", "interval": INTERVALS[timeframe], "limit": min(limit + 1, 1000),
    }, timeout=8)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Invalid Binance candle response")
    candles = []
    previous = -1
    for row in payload:
        try:
            opened, closed = int(row[0]), int(row[6])
            opening, high, low, close = (Decimal(row[index]) for index in (1, 2, 3, 4))
        except (IndexError, KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise ValueError("Invalid Binance candle") from error
        prices = (opening, high, low, close)
        if (opened <= previous or closed < opened
                or not all(p.is_finite() and p > 0 for p in prices)
                or not low <= min(opening, close) <= max(opening, close) <= high):
            raise ValueError("Invalid Binance candle bounds or order")
        previous = opened
        if closed < cutoff:
            candles.append({"time": opened // 1000, "open": float(opening), "high": float(high),
                            "low": float(low), "close": float(close)})
    return candles[-limit:]
