"""Purpose-aware classifications of canonical current-family publications."""
from dataclasses import dataclass
from enum import StrEnum


class CurrentKind(StrEnum):
    ACTIVE = "ACTIVE_ELIGIBLE"
    INACTIVE = "DEFINITIVELY_INACTIVE"
    UNKNOWN = "UNKNOWN"
    OUTSIDE = "OUTSIDE_EVALUATED_HORIZON"


@dataclass(frozen=True, slots=True)
class CurrentState:
    kind: CurrentKind
    reason: str
    revision: str | None = None


# A producer can factually end a family even though the terminal revision is
# not itself a positive strategy observation.
TERMINAL_REASONS = {
    "fvg": {"CLOSE_BEYOND_OPPOSITE_BOUNDARY", "MAX_AGE"},
    "order_block": {"CLOSE_BEYOND_FAR_EDGE", "MAX_AGE"},
    "pool": {"MAX_AGE"},
    "crt": {"REFERENCE_CAP_REPLACEMENT", "MAX_AGE", "CANDIDATE_AGE",
            "CLOSE_BEYOND_INVALIDATION", "OPPOSITE_SWEEP_BEFORE_RECLAIM"},
}

TERMINAL_ELIGIBLE = {
    "fvg": {"FULLY_MITIGATED"},
    "order_block": {"FULLY_MITIGATED"},
    "pool": {"SWEPT", "BROKEN"},
    "crt": {"OPPOSITE_BOUNDARY_REACHED"},
}


def decisively_terminal(category, state, reason, *, direct, compatible, eligible, attested):
    if category == "smt":
        return attested and state in {"SMT_INVALIDATED", "EXPIRED"}
    if eligible and state in TERMINAL_ELIGIBLE.get(category, ()):
        return True
    return (direct and compatible and state in {"INVALIDATED", "EXPIRED"}
            and reason in TERMINAL_REASONS.get(category, ()))
