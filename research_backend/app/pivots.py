"""Shared pivot predicate for legacy swing output and market structure."""
from typing import Literal

Side = Literal["HIGH", "LOW"]


def pivot_matches(values: list[float], center: int, side: Side, tolerance_bps: float = 0,
                  ties: str = "strict") -> bool:
    price = values[center]
    tolerance = price * tolerance_bps / 10_000
    sign = 1 if side == "HIGH" else -1
    left = [sign * (price - value) for value in values[:center]]
    right = [sign * (price - value) for value in values[center + 1:]]
    if ties == "strict":
        return all(delta > tolerance for delta in left + right)
    return (all(delta > tolerance for delta in left) and all(delta >= -tolerance for delta in right)
            and any(delta > tolerance for delta in left + right))
