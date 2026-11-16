"""Resolve continuity using the canonical UTC/IANA market calendar."""
from .indicator_coverage import MarketSchedule, WEEK, open_intervals

POLICY_VERSION = 1


def continuity_reason(previous: int, current: int, step: int, schedule: MarketSchedule):
    gap_start = previous+step
    if schedule.kind == "unknown":
        return "UNKNOWN_SCHEDULE"
    if schedule.kind == "continuous" or current-gap_start >= WEEK:
        return "MISSING_OPEN_CANDLES"
    intervals = open_intervals(previous, current+step, schedule)
    if intervals is None:
        return "UNVERIFIABLE_CLOSURE"
    if any(left % step or right % step for left, right in intervals):
        return "UNVERIFIABLE_CLOSURE"
    if not all(any(left <= begin and begin+step <= right for left, right in intervals)
               for begin in (previous, current)):
        return "UNVERIFIABLE_CLOSURE"
    if any(max(left, gap_start) < min(right, current) for left, right in intervals):
        return "MISSING_OPEN_CANDLES"
    return "SCHEDULED_CLOSURE"
