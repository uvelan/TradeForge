"""UTC temporal rules and the fail-closed strategy evidence boundary."""
from dataclasses import dataclass
from typing import Literal

TimingMode = Literal["LEGACY_RESEARCH", "STRICT_CAUSAL"]
LEGACY = "LEGACY_RESEARCH"
STRICT = "STRICT_CAUSAL"


@dataclass(frozen=True)
class TemporalEvidence:
    start: int
    end: int
    available: int
    dependency: int
    cutoff: int
    recorded: bool
    authority: str
    correction: int | None = None

    def __post_init__(self):
        if self.start > self.end or self.available < max(self.end, self.dependency):
            raise ValueError("Invalid temporal evidence interval or availability")

    def interaction(self, *, close_only=False):
        if self.available > self.cutoff:
            return "NOT_YET_AVAILABLE"
        # Equal open allows a full candle. Equal close cannot prove the reference preceded the close.
        if self.dependency >= self.end and close_only:
            return "DEPENDENCY_UNAVAILABLE"
        if not close_only and self.start < self.dependency:
            return "STRADDLING_OBSERVATION"
        return "ELIGIBLE"

    def contract(self, *, reason=None, close_only=False, references=()):
        decision = self.interaction(close_only=close_only)
        if decision != "NOT_YET_AVAILABLE" and reason is not None:
            decision = reason
        if decision == "ELIGIBLE":
            if self.authority != "CHECKSUM_VERIFIED_ARCHIVE":
                decision = "UNVERIFIED_SOURCE"
            elif not self.recorded:
                decision = "UNVERIFIED_AVAILABILITY"
        return {"timing_policy":"STRICT_CAUSAL", "timing_policy_version":2,
                "observation_start":self.start, "observation_end":self.end,
                "nominal_confirmation_time":self.end, "actual_availability":self.available,
                "availability_basis":"RECORDED" if self.recorded else "ASSUMED_CANDLE_CLOSE_OR_DEPENDENCY",
                "dependency_available_at":self.dependency, "first_eligible_interaction_time":self.available,
                "evaluation_cutoff":self.cutoff, "source_correction_time":self.correction,
                "source_authority":self.authority, "required_dependencies":list(references),
                "strategy_eligibility":decision, "eligibility_reason":decision}


def evidence(bar, step, available, dependency, cutoff, authority):
    recorded = getattr(bar, "available_at", None) == available
    return TemporalEvidence(bar.time, bar.time+step, available,
                            dependency, cutoff, recorded, authority)


def strategy_eligible(event, cutoff):
    """Require known strict, complete, verified evidence at the consumer's own cutoff."""
    if not isinstance(event, dict):
        return False
    timing = event.get("timing")
    if not isinstance(timing, dict):
        return False
    available = timing.get("actual_availability")
    state = event.get("state","")
    if not isinstance(state,str):
        return False
    start,end,dependency=(timing.get(key) for key in
        ("observation_start","observation_end","dependency_available_at"))
    return (timing.get("timing_policy") == STRICT and timing.get("timing_policy_version") == 2
            and timing.get("strategy_eligibility") == "ELIGIBLE"
            and timing.get("availability_basis") == "RECORDED"
            and timing.get("source_authority") == "CHECKSUM_VERIFIED_ARCHIVE"
            and all(type(stamp) is int for stamp in (start,end,available,dependency,cutoff))
            and 0 <= start <= end <= available <= cutoff and 0 <= dependency <= available
            and timing.get("eligibility_reason") == "ELIGIBLE"
            and event.get("status") not in {"provisional","invalidated"}
            and state not in {"AMBIGUOUS","UNCERTAIN","EXPIRED","INVALIDATED","FORMING"}
            and not state.endswith("_CANDIDATE"))
