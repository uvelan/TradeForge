"""Causal SMT detection on two independently confirmed pivot streams."""

from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass

from .indicators import Bar, swing_points


@dataclass(frozen=True)
class Pivot:
    time: int
    price: float
    confirmed_at: int


def confirmation_timing(event: dict, rules: dict) -> dict:
    """Explain the recorded timing, including for older immutable journal rows."""
    latest_pivot = max(event[side][point]["confirmed_at"]
                       for side in ("primary", "comparison") for point in ("previous", "current"))
    matching_closed = event["transitions"][0]["time"]
    confirmed_at = event["confirmed_at"]
    return {
        "pivot_right_bars": rules["period"],
        "all_pivots_confirmed_at": latest_pivot,
        "alignment_window_bars": rules["alignment_bars"],
        "matching_closed_at": matching_closed,
        "matching_wait_seconds": matching_closed - latest_pivot,
        "required_shared_confirmation_bars": rules["confirmation_bars"],
        "confirmation_wait_seconds": None if confirmed_at is None else confirmed_at - matching_closed,
        "reason": "Confirm all four pivots, close the UTC matching window, then wait for the configured shared closed bars.",
    }


def pivots(bars, values, period, step):
    result = []
    for index, price in enumerate(values):
        if price is None:
            continue
        start = index - 2 * period
        if all(bars[i].time - bars[i - 1].time == step for i in range(start + 1, index + 1)):
            result.append(Pivot(bars[index - period].time, price, bars[index].time + step))
    return result


def detect_smt(primary: list[Bar], comparison: list[Bar], period: int, step: int, *,
               alignment_bars: int = 2, tolerance_bps: float = 0.5, confirmation_bars: int = 1) -> list[dict]:
    if not primary or not comparison:
        return []
    left = {bar.time: bar for bar in primary}
    right = {bar.time: bar for bar in comparison}
    shared = sorted(left.keys() & right.keys())
    cutoff = min(primary[-1].time, comparison[-1].time) + step
    window = alignment_bars * step
    events = []
    primary_swings, comparison_swings = swing_points(primary, period), swing_points(comparison, period)
    for label, values in primary_swings.items():
        high = label == "Swing high"
        direction = "bearish" if high else "bullish"
        sign = 1 if high else -1
        peers = pivots(comparison, comparison_swings[label], period, step)
        times = [pivot.time for pivot in peers]
        next_peer = 0
        previous = None
        for pivot in pivots(primary, values, period, step):
            # Wait until every possible comparison pivot in the window could be confirmed.
            candidate_at = pivot.confirmed_at + window
            if candidate_at > cutoff:
                break
            lo = bisect_left(times, pivot.time - window, lo=next_peer)
            hi = bisect_right(times, pivot.time + window, lo=lo)
            if lo == hi:
                continue
            match = min(range(lo, hi), key=lambda i: (abs(times[i] - pivot.time), times[i]))
            peer = peers[match]
            next_peer = match + 1
            prior = previous
            previous = (pivot, peer)
            if prior is None:
                continue
            prior_left, prior_right = prior
            a = sign * (pivot.price - prior_left.price) / prior_left.price * 10_000
            b = sign * (peer.price - prior_right.price) / prior_right.price * 10_000
            if not (a > tolerance_bps and b < -tolerance_bps or b > tolerance_bps and a < -tolerance_bps):
                continue
            primary_leads = a > tolerance_bps
            laggard_series, reference = (right, prior_right.price) if primary_leads else (left, prior_left.price)
            laggard_pivot = peer if primary_leads else pivot

            def broke_reference(bar: Bar) -> bool:
                extreme = bar.high if high else bar.low
                return sign * (extreme - reference) / reference * 10_000 > tolerance_bps

            if any(broke_reference(bar) for timestamp, bar in laggard_series.items()
                   if laggard_pivot.time < timestamp < candidate_at):
                continue
            transitions = [{"status": "SMT_CANDIDATE", "time": candidate_at}]
            confirmed_at = candidate_at if confirmation_bars == 0 else None
            if confirmed_at is not None:
                transitions.append({"status": "SMT_CONFIRMED", "time": confirmed_at})
            for count, timestamp in enumerate(shared[bisect_left(shared, candidate_at):], 1):
                if broke_reference(laggard_series[timestamp]):
                    transitions.append({"status": "SMT_INVALIDATED", "time": timestamp + step})
                    break
                if confirmed_at is None and count == confirmation_bars:
                    confirmed_at = timestamp + step
                    transitions.append({"status": "SMT_CONFIRMED", "time": confirmed_at})
            events.append({
                "id": f"{direction}:{prior_left.time}:{pivot.time}:{prior_right.time}:{peer.time}",
                "direction": direction, "leader": "primary" if primary_leads else "comparison",
                "primary": {"previous": asdict(prior_left), "current": asdict(pivot)},
                "comparison": {"previous": asdict(prior_right), "current": asdict(peer)},
                "confirmed_at": confirmed_at, "status": transitions[-1]["status"], "transitions": transitions,
            })
    return sorted(events, key=lambda event: (event["transitions"][0]["time"], event["id"]))
