from decimal import Decimal

from research_backend.app.tick_replay import replay_breakout


def inputs():
    candles = [{"time":i*60, "open":100, "high":101, "low":99, "close":100} for i in range(30)]
    candles[2].update(high=104, close=103)

    def tick(time_ms, order, bid, ask):
        return {"time_ms": time_ms, "source_order": order, "bid_units": bid, "ask_units": ask, "scale": 1}

    return candles, [
        tick(179_000, 0, 100, 101),
        tick(180_000, 1, 100, 101),
        tick(181_000, 2, 106, 107),
        tick(181_000, 3, 98, 99),
        tick(900_000, 4, 100, 101),
    ]


def test_bid_ask_replay_uses_source_order_and_costs():
    candles, ticks = inputs()
    clean = replay_breakout(candles,ticks,60,2,slip_bps=Decimal(0),commission_bps=Decimal(0))
    trade, = clean["trades"]
    assert (trade["entry"], trade["exit"], trade["exit_reason"], trade["result_r"]) == (101, 106, "TARGET", 2.5)
    costed = replay_breakout(candles,ticks,60,2,slip_bps=Decimal(10),commission_bps=Decimal(5))
    trade, = costed["trades"]
    assert trade["entry"] == 101.101 and trade["exit"] == 105.894
    assert 0 < trade["result_r"] < 2.5
    ticks[2]["source_order"],ticks[3]["source_order"] = 3,2
    reversed_order = replay_breakout(candles,ticks,60,2,slip_bps=Decimal(0),commission_bps=Decimal(0))
    assert reversed_order["trades"][0]["exit_reason"] == "STOP"


def test_short_quote_coverage_suppresses_expectancy():
    candles, ticks = inputs()
    result = replay_breakout(candles,ticks[:-1],60,2,slip_bps=Decimal(0),commission_bps=Decimal(0))
    assert result["missing_tick_windows"] == 1
    assert result["expectancy_r"] is None and result["count"] == 0


def test_tick_backtest_endpoint_returns_execution_lineage(client):
    from research_backend.app.storage import write_partition

    candles, ticks = inputs()
    for tick in ticks:
        tick["time"] = tick["time_ms"] // 1000
    candle = write_partition("DUKASCOPY", "test", "EURUSD", "candles", "M1", "BID", candles)
    quotes = write_partition("DUKASCOPY", "test", "EURUSD", "ticks", None, "BID_ASK_UNITS", ticks)
    params = {"source":"DUKASCOPY_SAVED", "timeframe":"M1", "tick_dataset_id":quotes["id"], "slippage_bps":"10"}
    response = client.get("/api/backtest", params=params)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["lineage"]["tick_partition"]["id"] == quotes["id"]
    assert result["lineage"]["candle_partitions"][0]["id"] == candle["id"]
    assert result["trades"][0]["entry"] == 101.101
