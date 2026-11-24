"""Bid/ask replay for the existing next-bar breakout rule."""

from bisect import bisect_left
from decimal import Decimal
from itertools import pairwise
from operator import itemgetter

from .backtest import breakout_signal
from .indicator_coverage import MarketSchedule, open_intervals
from .historical_import import FEED


def executable_side(buy: bool) -> str:
    return "ask" if buy else "bid"


def executable_price(quote: dict, buy: bool) -> Decimal:
    return Decimal(quote[f"{executable_side(buy)}_units"]) / quote["scale"]


def replay_coverage(market, ticks, provider, symbol, step, gap_seconds):
    candles, coverage = market["candles"], market["coverage"]
    start = candles[0]["time"] if coverage["truncated"] else coverage["start"]
    end = coverage["end"]
    known = provider == "DUKASCOPY" and market["feed_id"] == FEED and symbol in ("EURUSD", "GBPUSD")
    calendar = MarketSchedule(kind="forex_weekly" if known else "unknown")
    sessions = open_intervals(start, end, calendar)
    gaps = []
    for gap in coverage["gaps"]:
        left, right = max(start, gap["start"]), min(end, gap["end"])
        if left < right:
            status = "UNKNOWN_SCHEDULE" if sessions is None else (
                "MISSING_OPEN" if any(a < right and left < b for a, b in sessions) else "KNOWN_CLOSED")
            gaps.append(dict(start=left, end=right, status=status))
    first = bisect_left(ticks, start * 1000, key=lambda q: q["time_ms"])
    last = bisect_left(ticks, end * 1000, key=lambda q: q["time_ms"])
    if sessions is not None:
        # A contradictory observation makes the calendar unsafe to use.
        for left, right in zip([start, *(b for _, b in sessions)], [*(a for a, _ in sessions), end]):
            if left >= right:
                continue
            index = bisect_left(ticks, left * 1000, key=lambda q: q["time_ms"])
            if ((index < last and ticks[index]["time_ms"] < right * 1000)
                    or any(c["time"] < right and left < c["time"] + step for c in candles)):
                raise ValueError("UNTRUSTED: archive contradicts weekly session calendar")
    return dict(calendar_version="DUKASCOPY_FX_NY17_V1" if known else "UNKNOWN_V1",
                schedule=calendar.model_dump(), provider=provider, symbol=symbol, feed=market["feed_id"],
                required_period=[start, end], observed_quotes=last-first,
                first_quote_ms=ticks[first]["time_ms"] if first < last else None,
                last_quote_ms=ticks[last-1]["time_ms"] if first < last else None, gaps=gaps,
                gap_policy="BOUNDED_QUOTE_GAP_V1", requested_gap_seconds=gap_seconds,
                max_quote_gap_ms=1000 * min(step, gap_seconds, 60))


def replay_breakout(candles: list[dict], ticks: list[dict], step: int, target_r: float, *,
                    slip_bps: Decimal, commission_bps: Decimal, max_bars: int = 12,
                    max_quote_gap_seconds: int = 60, coverage=None) -> dict:
    if len(candles) < 25 or not ticks:
        raise ValueError("Tick replay requires at least 25 closed candles and archived quotes")
    if step <= 0 or max_bars < 1 or max_quote_gap_seconds < 1:
        raise ValueError("Invalid tick replay coverage parameters")
    candle_times = [candle["time"] for candle in candles]
    if any(later <= earlier for earlier, later in zip(candle_times, candle_times[1:])):
        raise ValueError("Tick replay requires chronologically ordered candles")

    # The matching feed's bid candles establish continuous observed intervals.
    # Closures and missing candle intervals both split execution coverage.
    session_ends = [candle_times[-1] + step] * len(candles)
    for index in range(len(candles) - 2, -1, -1):
        candle_end = candle_times[index] + step
        session_ends[index] = (session_ends[index + 1]
                               if candle_end == candle_times[index + 1] else candle_end)
    max_gap_ms = min(step, max_quote_gap_seconds) * 1000
    priority = itemgetter("time_ms", "source_order")
    ordered = ticks if coverage is not None else sorted(ticks, key=priority)
    times = [quote["time_ms"] for quote in ordered]
    if any(priority(a) >= priority(b) for a, b in pairwise(ordered)):
        raise ValueError("Tick source order is ambiguous")
    if any(not 0 < quote["bid_units"] <= quote["ask_units"] or quote["scale"] <= 0 for quote in ordered):
        raise ValueError("Tick archive contains invalid bid/ask prices")
    slip = slip_bps / 10_000
    commission = commission_bps / 10_000
    multiple = Decimal(str(target_r))
    trades = []
    unresolved = []
    next_entry = 0
    gaps = coverage["gaps"] if coverage else []
    frontier = next((gap["start"] for gap in gaps if gap["status"] != "KNOWN_CLOSED"), None)
    reopenings = {gap["end"] for gap in gaps if gap["status"] == "KNOWN_CLOSED"}
    for index in range(2, len(candles) - 1):
        signal = candles[index]
        if frontier is not None and signal["time"] >= frontier:
            break
        if signal["time"] in reopenings:
            continue
        setup = breakout_signal(candles[index - 1], signal)
        if not setup:
            continue
        begin = (signal["time"] + step) * 1000
        if begin < next_entry:
            continue
        side, stop_price = setup
        outcome = {"status": "UNRESOLVED", "signal_time": signal["time"], "side": side,
                   "entry_time_ms": None, "entry_window_start_ms": begin}
        deadline = begin + max_bars * step * 1000
        session_end = session_ends[index] * 1000
        start_index = bisect_left(times, begin)
        end_index = bisect_left(times, session_end)
        if candle_times[index] - candle_times[index - 1] != step:
            unresolved.append({**outcome, "reason": "SIGNAL_CANDLE_GAP"})
            break
        if begin >= session_end:
            unresolved.append({**outcome, "reason": "ENTRY_CANDLE_GAP"})
            break
        if (start_index >= end_index or times[start_index] >= begin + step * 1000
                or times[start_index] - begin > max_gap_ms):
            unresolved.append({**outcome, "reason": "MISSING_ENTRY_QUOTE"})
            break

        stop = Decimal(str(stop_price))
        direction = 1 if side == "BUY" else -1
        first = ordered[start_index]
        entry_quote = executable_price(first, direction > 0)
        entry = entry_quote * (1 + direction * slip)
        risk = direction * (entry - stop)
        if risk <= 0:
            continue
        target = entry + direction * risk * multiple
        outcome["entry_time_ms"] = first["time_ms"]
        previous_time = first["time_ms"]
        for j in range(start_index, end_index):
            quote = ordered[j]
            if quote["time_ms"] - previous_time > max_gap_ms:
                unresolved.append({**outcome, "reason": "INTRADAY_QUOTE_GAP",
                                   "last_covered_time_ms": previous_time,
                                   "next_quote_time_ms": quote["time_ms"]})
                break
            previous_time = quote["time_ms"]
            exit_quote = executable_price(quote, direction < 0)
            stop_hit = direction * (exit_quote - stop) <= 0
            target_hit = direction * (exit_quote - target) >= 0
            time_limit = quote["time_ms"] >= deadline
            if stop_hit or target_hit or time_limit:
                exit_fill = exit_quote * (1 - direction * slip)
                fee = (entry + exit_fill) * commission
                result_r = (direction * (exit_fill - entry) - fee) / risk
                trades.append({
                    "entry_time":first["time_ms"] // 1000, "entry_time_ms":first["time_ms"],
                    "exit_time_ms":quote["time_ms"], "entry_source_order":first["source_order"],
                    "exit_source_order":quote["source_order"], "side":side, "entry":float(entry), "stop":float(stop),
                    "target":float(target), "exit":float(exit_fill), "result_r":round(float(result_r),4),
                    "exit_reason":"TIME_LIMIT" if time_limit else "STOP" if stop_hit else "TARGET",
                })
                next_entry = quote["time_ms"] + 1
                break
        else:
            reason = ("CANDLE_SESSION_BOUNDARY" if session_end < (candle_times[-1] + step) * 1000
                      else "EXECUTION_DATA_ENDED")
            unresolved.append({**outcome, "reason": reason, "last_covered_time_ms": previous_time})
        if unresolved:
            # An unknown exit also makes the next eligible entry unknown.
            break

    missing = len(unresolved)
    realized = [trade["result_r"] for trade in trades]
    total = sum(realized)
    count = len(trades)
    conditional = round(total/count, 4) if count else None
    incomplete = bool(missing) or frontier is not None
    expectancy = None if incomplete else conditional
    return {
        "strategy":"Previous-bar breakout with bid/ask tick execution", "trades":trades, "count":count,
        "eligible_signals":count+missing, "missing_tick_windows":missing, "unresolved_outcomes":unresolved, "ambiguous_excluded":0,
        "ambiguous_rate":0 if count and not incomplete else None, "expectancy_r":expectancy,
        "expectancy_bounds_r":{"worst":expectancy,"best":expectancy} if expectancy is not None else None,
        "total_r":None if incomplete else round(total,4),
        "win_rate":round(sum(value>0 for value in realized)/count*100,2) if count and not incomplete else None,
        "data_note":"Quoted bid/ask, adverse slippage and commission per side. Execution uses continuous matching-feed candle intervals and bounded quote gaps. Replay stops at the first unresolved window. Quote coverage does not prove executable size.",
        "execution_model":{"version":2,"max_quote_gap_ms":max_gap_ms,"slippage_bps":str(slip_bps),"commission_bps_per_side":str(commission_bps)},
    }


def validate_bid_extremes(candles: list[dict], ticks: list[dict], step: int) -> None:
    """Reject a bid outside the matching UTC candle, even when archive coverage is partial."""
    bounds = {candle["time"]: (Decimal(str(candle["low"])), Decimal(str(candle["high"])))
              for candle in candles}
    for quote in ticks:
        if quote["scale"] <= 0:
            raise ValueError("UNTRUSTED: invalid quote price scale")
        timestamp = quote["time_ms"] // (step * 1000) * step
        if timestamp in bounds:
            low, high = bounds[timestamp]
            bid = Decimal(quote["bid_units"]) / quote["scale"]
            if not low <= bid <= high:
                raise ValueError(f"UNTRUSTED: bid {bid} contradicts candle [{low}, {high}] at UTC epoch {timestamp}")
