"""TF-I09A contract helpers for already qualified evidence, without market evaluation."""
from bisect import bisect_right
from itertools import islice
from typing import Literal

from pydantic import Field, StrictInt, ValidationError, model_validator

from .strategy_catalog import BINDING_POLICY_VERSION
from .strategy_definitions import (
    Contract, Digest, ExactEvidenceReference, Key, Outcome, Selection, StrategyDecisionTrace,
    TimeBound, TradedRole,
)


class BoundEvidence(Contract, frozen=True):
    reference: ExactEvidenceReference
    family: str | None
    market_event_id: str
    observed_at: StrictInt = Field(ge=0)

    def family_key(self):
        ref = self.reference
        return (ref.calculation_id, ref.category, ref.configuration, tuple(ref.source), self.family)


class AttemptBinding(Contract, frozen=True):
    attempt_id: Digest
    strategy_version_id: str = Field(pattern=r"^SV-[a-f0-9]{32}$")
    alias: Key
    evidence: BoundEvidence
    activated_at: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def causal_activation(self):
        if self.activated_at < self.evidence.reference.available_at:
            raise ValueError("An alias cannot activate before its evidence becomes available")
        return self


class PinnedProducer(Contract, frozen=True):
    dependency: Key
    calculation_id: Digest
    calculation_snapshot: Digest


class AttemptDecisionTrace(StrategyDecisionTrace):
    contract_version: Literal[2]
    binding_policy_version: Literal[BINDING_POLICY_VERSION]
    attempt_id: Digest
    traded_role: TradedRole
    direction: Literal["LONG", "SHORT"]
    producers: list[PinnedProducer] = Field(max_length=24)
    bindings: list[AttemptBinding] = Field(max_length=96)
    context_activated_at: StrictInt | None = Field(ge=0)
    setup_activated_at: StrictInt | None = Field(ge=0)
    trigger_activated_at: StrictInt | None = Field(ge=0)


def expiry_status(window: TimeBound, origin, cutoff, *, traded_role: TradedRole, bar_count=None):
    """Return whether the pending attempt has expired. The final limit is inclusive.

    The supplied bar count must represent complete eligible observations from the
    frozen traded-role series and declared frame, strictly after the origin.
    """
    if origin is None or cutoff < origin or traded_role not in {"PRIMARY","COMPARISON"}:
        return Outcome.UNKNOWN
    if window.unit == "SECONDS":
        elapsed = cutoff - origin
    elif type(bar_count) is int and bar_count >= 0:
        elapsed = bar_count
    else:
        return Outcome.UNKNOWN
    return Outcome.SATISFIED if elapsed > window.count else Outcome.NOT_SATISFIED


def exact_record(registry, event, snapshot, cutoff):
    """Verify the exact signed projection and the selected producer snapshot."""
    if not isinstance(event, dict):
        return None
    reference = event.get("evidence_reference")
    if not isinstance(reference, dict) or reference.get("calculation_snapshot") != snapshot:
        return None
    record = registry.resolve(reference)
    if (record is None or record.available > cutoff or cutoff > registry.calculations[snapshot].cutoff
            or reference.get("availability_mode") != registry.purpose
            or record.signature != registry.signature(event)):
        return None
    return record


def verified_family(registry, evidence):
    record = registry.resolve(evidence.reference.model_dump(mode="json", exclude_none=True))
    if record and record.eligible and record.family and record.family == evidence.family:
        return record
    return None


def select_candidate(registry, events, *, snapshot, cutoff, policy: Selection, age, limit,
                     current=False, bound=None, coverage_known):
    """Order an already predicate-matched, causally complete prefix for one rule.

    A caller owns the attempt, positive proof and availability cursor. Overflow
    or conflicting inputs are unknown. The selected bound family never falls back
    to an unrelated observation.
    """
    if (not coverage_known or not 1 <= limit <= 128 or not 1 <= age <= 86400 or
            policy not in ("EARLIEST_ELIGIBLE", "LATEST_ELIGIBLE", "BOUND_FAMILY") or
            (policy == "BOUND_FAMILY") != (bound is not None) or
            bound is not None and not verified_family(registry, bound)):
        return Outcome.UNKNOWN, None
    choices = {}
    for index, event in enumerate(islice(events, limit + 1)):
        if index == limit:
            return Outcome.UNKNOWN, None
        ref = event.get("evidence_reference") if isinstance(event, dict) else None
        record = exact_record(registry, event, snapshot, cutoff)
        if record is None:
            return Outcome.UNKNOWN, None
        if not record.eligible or cutoff - record.available > age:
            continue
        if current and not registry.current_at(ref, cutoff, purpose=registry.purpose):
            continue
        try:
            evidence = BoundEvidence(
                reference=ref, family=record.family,
                market_event_id=event.get("market_event_id") or record.family or ref["event_id"],
                observed_at=event["timing"]["observation_start"],
            )
        except ValidationError:
            return Outcome.UNKNOWN, None
        if bound is not None and (evidence.family_key() != bound.family_key() or
                                  record.available < bound.reference.available_at):
            continue
        choices[ref["event_id"]] = evidence
    if not choices:
        return Outcome.UNKNOWN, None
    order = lambda evidence: (evidence.reference.available_at, evidence.observed_at, evidence.reference.event_id)
    choose = max if policy == "LATEST_ELIGIBLE" else min
    return Outcome.SATISFIED, choose(choices.values(), key=order)


def crt_family_validity(registry, bound, revision, *, snapshot, cutoff, coverage_known):
    """Classify a selected CRT family's exact latest revision at a cutoff.

    Terminal producer projections may block an entry without supplying positive
    evidence to a rule. Missing, uncertain or ambiguous revisions remain unknown.
    """
    if not coverage_known or bound.reference.category != "crt" or not verified_family(registry, bound):
        return Outcome.UNKNOWN
    record = exact_record(registry, revision, snapshot, cutoff)
    if (record is None or record.category != "crt" or
            (record.reference["calculation_id"], record.config, record.scope, record.family) !=
            (bound.reference.calculation_id, bound.reference.configuration, tuple(bound.reference.source), bound.family)):
        return Outcome.UNKNOWN
    producer = registry.calculations[snapshot]
    if not record.reference or revision["timing"]["coverage"] != "ESTABLISHED":
        return Outcome.UNKNOWN
    if registry.current_at(record.reference, cutoff, purpose=registry.purpose):
        return Outcome.SATISFIED
    history = producer.current.get(record.family, ())
    index = bisect_right(history, cutoff, key=lambda item: item[0]) - 1
    if (index >= 0 and history[index] == (record.available, None) and
            record.state in {"INVALIDATED", "EXPIRED", "OPPOSITE_BOUNDARY_REACHED"}):
        return Outcome.NOT_SATISFIED
    return Outcome.UNKNOWN


def activate_crt_attempt(registry, reference, revision, *, snapshot, cutoff, age,
                         coverage_known, attempt_id, strategy_version_id, alias):
    """Bind a historical ACTIVE reference only if its family is usable at creation."""
    if reference.reference.category != "crt" or not 1 <= age <= 86400:
        return Outcome.UNKNOWN, None
    initial = verified_family(registry, reference)
    if not initial or initial.state != "ACTIVE" or not 0 <= cutoff - initial.available <= age:
        return Outcome.UNKNOWN, None
    validity = crt_family_validity(registry, reference, revision, snapshot=snapshot,
                                   cutoff=cutoff, coverage_known=coverage_known)
    if validity != Outcome.SATISFIED:
        return validity, None
    return validity, AttemptBinding(attempt_id=attempt_id, strategy_version_id=strategy_version_id,
                                    alias=alias, evidence=reference, activated_at=cutoff)
