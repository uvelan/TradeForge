"""UTC temporal rules and the fail-closed strategy evidence boundary."""
from dataclasses import dataclass
from typing import Literal
from .arrival_policy import RECORDED_AUTHORITY, RECORDED_MODE, SIMULATION_AUTHORITY, SIMULATION_ELIGIBLE

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
            if self.authority not in {RECORDED_AUTHORITY,SIMULATION_AUTHORITY}:
                decision = "UNVERIFIED_SOURCE"
            elif not self.recorded:
                decision = "UNVERIFIED_AVAILABILITY"
            elif self.authority == SIMULATION_AUTHORITY:
                decision = SIMULATION_ELIGIBLE
        basis = ("MODELED_NOMINAL_CLOSE_PLUS_DELAY" if self.authority == SIMULATION_AUTHORITY and self.recorded
                 else "UNVERIFIED_CANDLE_CLOSE_OR_DECLARED" if self.authority != RECORDED_AUTHORITY
                 else "RECORDED" if self.recorded else "ASSUMED_CANDLE_CLOSE_OR_DEPENDENCY")
        return {"timing_policy":"STRICT_CAUSAL", "timing_policy_version":3,
                "observation_start":self.start, "observation_end":self.end,
                "nominal_confirmation_time":self.end, "actual_availability":self.available,
                "availability_basis":basis,
                "dependency_available_at":self.dependency, "first_eligible_interaction_time":self.available,
                "evaluation_cutoff":self.cutoff, "source_correction_time":self.correction,
                "source_authority":self.authority, "required_dependencies":list(references),
                "strategy_eligibility":decision, "eligibility_reason":decision}


def evidence(bar, step, available, dependency, cutoff, authority):
    recorded = getattr(bar, "available_at", None) == available
    return TemporalEvidence(bar.time, bar.time+step, available,
                            dependency, cutoff, recorded, authority)



def strategy_eligible(event, cutoff, *, registry=None, current=False, purpose=RECORDED_MODE):
    """Only a publication in the current analytical request can satisfy eligibility."""
    return registry is not None and registry.eligible(event, cutoff, current=current, purpose=purpose)
