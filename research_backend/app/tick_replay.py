"""Bid/ask replay for the existing next-bar breakout rule."""

from bisect import bisect_left
from decimal import Decimal

from .backtest import breakout_signal


def replay_breakout(candles: list[dict], ticks: list[dict], step: int, target_r: float, *,
                    slip_bps: Decimal, commission_bps: Decimal, max_bars: int = 12) -> dict:
    if len(candles) < 25 or not ticks:
        raise ValueError("Tick replay requires at least 25 closed candles and archived quotes")
    ordered = sorted(ticks, key=lambda quote: (quote["time_ms"], quote["source_order"]))
    times = [quote["time_ms"] for quote in ordered]
    if len({(quote["time_ms"], quote["source_order"]) for quote in ordered}) != len(ordered):
        raise ValueError("Tick source order is ambiguous")
    if any(not 0 < quote["bid_units"] <= quote["ask_units"] or quote["scale"] <= 0 for quote in ordered):
        raise ValueError("Tick archive contains invalid bid/ask prices")
    slip = slip_bps / 10_000
    commission = commission_bps / 10_000
    multiple = Decimal(str(target_r))
    trades = []
    missing = 0
    next_entry = 0
    for index in range(2,len(candles)-max_bars-1):
        signal = candles[index]
        setup = breakout_signal(candles[index-1], signal)
        if not setup:
            continue
        begin = (signal["time"] + step) * 1000
        if begin < next_entry:
            continue
        side, stop_price = setup
        stop = Decimal(str(stop_price))
        direction = 1 if side == "BUY" else -1
        deadline = begin + max_bars * step * 1000
        start_index = bisect_left(times, begin)
        end_index = bisect_left(times, deadline)
        if (times[0] > begin or times[-1] < deadline or start_index >= end_index
                or times[start_index] >= begin + step * 1000):
            missing += 1
            continue
        first = ordered[start_index]
        entry_quote = Decimal(first["ask_units"] if direction > 0 else first["bid_units"]) / first["scale"]
        entry = entry_quote * (1 + direction * slip)
        risk = direction * (entry - stop)
        if risk <= 0:
            continue
        target = entry + direction * risk * multiple
        for j in range(start_index, end_index):
            quote = ordered[j]
            exit_quote = Decimal(quote["bid_units"] if direction > 0 else quote["ask_units"]) / quote["scale"]
            stop_hit = direction * (exit_quote - stop) <= 0
            target_hit = direction * (exit_quote - target) >= 0
            if stop_hit or target_hit or j == end_index-1:
                exit_fill = exit_quote * (1 - direction * slip)
                fee = (entry + exit_fill) * commission
                result_r = (direction * (exit_fill - entry) - fee) / risk
                trades.append({
                    "entry_time":first["time_ms"] // 1000, "entry_time_ms":first["time_ms"],
                    "exit_time_ms":quote["time_ms"], "side":side, "entry":float(entry), "stop":float(stop),
                    "target":float(target), "exit":float(exit_fill), "result_r":round(float(result_r),4),
                    "exit_reason":"STOP" if stop_hit else "TARGET" if target_hit else "TIME_LIMIT",
                })
                next_entry = quote["time_ms"] + 1
                break
    realized = [trade["result_r"] for trade in trades]
    total = sum(realized)
    count = len(trades)
    conditional = round(total/count, 4) if count else None
    expectancy = None if missing else conditional
    return {
        "strategy":"Previous-bar breakout with bid/ask tick execution", "trades":trades, "count":count,
        "eligible_signals":count+missing, "missing_tick_windows":missing, "ambiguous_excluded":0,
        "ambiguous_rate":0 if count else None, "expectancy_r":expectancy,
        "expectancy_bounds_r":{"worst":expectancy,"best":expectancy} if expectancy is not None else None,
        "total_r":None if missing else round(total,4),
        "win_rate":round(sum(value>0 for value in realized)/count*100,2) if count and not missing else None,
        "data_note":"Quoted bid/ask, adverse slippage and commission per side. Fills use the first triggering quote; quote coverage does not prove executable size.",
        "execution_model":{"version":1,"slippage_bps":str(slip_bps),"commission_bps_per_side":str(commission_bps)},
    }
