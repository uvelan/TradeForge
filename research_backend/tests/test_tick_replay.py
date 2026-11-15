from datetime import UTC, datetime
from decimal import Decimal

import pytest

from research_backend.app.tick_replay import replay_breakout


def tick(time_ms, order, bid=100, ask=101):
    return {"time_ms": time_ms, "source_order": order, "bid_units": bid, "ask_units": ask, "scale": 1}


def inputs():
    candles = [{"time":i*60, "open":100, "high":101, "low":99, "close":100} for i in range(30)]
    candles[2].update(high=104, close=103)

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


def replay(candles, ticks, **kwargs):
    return replay_breakout(candles, ticks, 60, 2, slip_bps=Decimal(0), commission_bps=Decimal(0), **kwargs)


def assert_unresolved(result, reason):
    outcome, = result["unresolved_outcomes"]
    assert outcome["status"] == "UNRESOLVED" and outcome["reason"] == reason
    assert result["missing_tick_windows"] == 1
    for key in ("expectancy_r", "expectancy_bounds_r", "total_r", "win_rate"):
        assert result[key] is None
    return outcome


@pytest.mark.parametrize(("bid", "exit_reason"), [(106, "TARGET"), (98, "STOP")])
def test_early_exit_with_no_quotes_through_deadline(bid, exit_reason):
    candles, _ = inputs()
    result = replay(candles, [tick(180_000, 0), tick(181_000, 1, bid, bid + 1)])
    trade, = result["trades"]
    assert (trade["exit_reason"], trade["exit_time_ms"]) == (exit_reason, 181_000)
    assert result["expectancy_r"] == trade["result_r"]
    assert result["unresolved_outcomes"] == []


def test_intraday_quote_hole_blocks_later_profitable_exit():
    candles, _ = inputs()
    result = replay(candles, [tick(180_000, 0), tick(181_000, 1),
                              tick(1_381_000, 2, 106, 107), tick(1_620_000, 3)], max_bars=24)
    outcome = assert_unresolved(result, "INTRADAY_QUOTE_GAP")
    assert (outcome["last_covered_time_ms"], outcome["next_quote_time_ms"]) == (181_000, 1_381_000)
    assert result["trades"] == []


def test_missing_entry_and_incomplete_exit_quote_data():
    candles, _ = inputs()
    assert_unresolved(replay(candles, [tick(179_000, 0), tick(240_000, 1)]), "MISSING_ENTRY_QUOTE")
    ticks = [tick(time, order) for order, time in enumerate(range(180_000, 900_000, 30_000))]
    assert_unresolved(replay(candles, ticks), "EXECUTION_DATA_ENDED")
    ticks.append(tick(902_000, len(ticks)))
    trade, = replay(candles, ticks)["trades"]
    assert (trade["exit_reason"], trade["exit_time_ms"]) == ("TIME_LIMIT", 902_000)


def test_weekend_boundary_cannot_be_bridged_by_reopening_quote():
    candles, _ = inputs()
    friday = int(datetime(2026, 11, 13, 21, 54, tzinfo=UTC).timestamp())
    sunday = int(datetime(2026, 11, 15, 22, tzinfo=UTC).timestamp())
    for index, candle in enumerate(candles):
        candle["time"] = friday + index * 60 if index < 6 else sunday + (index - 6) * 60
    ticks = [tick((friday + offset) * 1000, order) for order, offset in enumerate(range(180, 360, 30))]
    ticks.append(tick(sunday * 1000, len(ticks), 106, 107))
    outcome = assert_unresolved(replay(candles, ticks), "CANDLE_SESSION_BOUNDARY")
    assert outcome["last_covered_time_ms"] < sunday * 1000
    trade, = replay(candles, [ticks[0], tick((friday + 181) * 1000, 10, 106, 107)])["trades"]
    assert trade["exit_reason"] == "TARGET"


def test_partial_run_keeps_verified_trade_and_withholds_aggregate_statistics():
    candles, ticks = inputs()
    candles[24].update(high=104, close=103)
    ticks.append(tick(1_500_000, 5))
    result = replay(candles, ticks)
    outcome = assert_unresolved(result, "EXECUTION_DATA_ENDED")
    assert outcome["entry_time_ms"] == 1_500_000
    assert result["count"] == 1 and result["eligible_signals"] == 2


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


def test_tick_backtest_endpoint_returns_unresolved_coverage(client):
    from research_backend.app.storage import write_partition

    candles, _ = inputs()
    ticks = [tick(180_000, 0), tick(182_000, 1, 106, 107)]
    for quote in ticks:
        quote["time"] = quote["time_ms"] // 1000
    write_partition("DUKASCOPY", "test", "EURUSD", "candles", "M1", "BID", candles)
    quotes = write_partition("DUKASCOPY", "test", "EURUSD", "ticks", None, "BID_ASK_UNITS", ticks)
    response = client.get("/api/backtest", params={
        "source": "DUKASCOPY_SAVED", "timeframe": "M1",
        "tick_dataset_id": quotes["id"], "max_quote_gap_seconds": 1,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert_unresolved(result, "INTRADAY_QUOTE_GAP")
    assert result["execution_model"]["max_quote_gap_ms"] == 1000


def test_tick_backtest_accepts_saved_range_across_weekend(client):
    from research_backend.app.storage import write_partition

    candles, _ = inputs()
    friday = int(datetime(2024, 11, 15, 21, 54, tzinfo=UTC).timestamp())
    sunday = int(datetime(2024, 11, 17, 22, tzinfo=UTC).timestamp())
    for index, candle in enumerate(candles):
        candle["time"] = friday + index * 60 if index < 6 else sunday + (index - 6) * 60
    for part in (candles[:6], candles[6:]):
        write_partition("DUKASCOPY", "test", "EURUSD", "candles", "M1", "BID", part)
    ticks = [tick((friday + offset) * 1000, order) for order, offset in enumerate(range(180, 360, 30))]
    ticks.append(tick(sunday * 1000, len(ticks), 106, 107))
    for quote in ticks:
        quote["time"] = quote["time_ms"] // 1000
    quotes = write_partition("DUKASCOPY", "test", "EURUSD", "ticks", None, "BID_ASK_UNITS", ticks)
    response = client.get("/api/backtest", params={
        "source": "DUKASCOPY_SAVED", "timeframe": "M1", "tick_dataset_id": quotes["id"],
        "start": friday, "end": candles[-1]["time"] + 60,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert_unresolved(result, "CANDLE_SESSION_BOUNDARY")
    assert result["candle_gaps"] == [{"start": friday + 360, "end": sunday}]
