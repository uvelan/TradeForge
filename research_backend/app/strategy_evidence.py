"""Canonical request-scoped evidence issued by the analytical engines."""
from bisect import bisect_right, insort_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json

VERSION = 3
SOURCE_FIELDS = ("source", "symbol", "timeframe", "feed_id", "price_basis", "dataset_id")
ACTIVATION = {"WHOLE_CANDLE", "CLOSE_ONLY", "FORMATION", "AGGREGATED_REFERENCE"}
STRUCTURE = {"PIVOT", "BOS", "CHOCH", "COUNTERTREND_BREAK"}
STATES = {
    "liquidity": {"SWEPT", "BROKEN"},
    "pool": {"ACTIVE", "SWEPT", "BROKEN"},
    "order_block": {"ACTIVE", "TOUCHED", "PARTIALLY_MITIGATED", "FULLY_MITIGATED"},
    "fvg": {"ACTIVE", "PARTIALLY_MITIGATED", "FULLY_MITIGATED"},
    "crt": {"ACTIVE", "HIGH_SWEPT_RECLAIMED", "LOW_SWEPT_RECLAIMED",
            "EXPANSION_OBSERVED", "OPPOSITE_BOUNDARY_REACHED"},
}
LINEAGE = {"pool": {"FORMING", "SWEEP_CANDIDATE"}}


@dataclass(frozen=True)
class Summary:
    signature: str
    scope: tuple
    config: str
    category: str
    family: str | None
    available: int
    attested: bool
    eligible: bool


class EvidenceRegistry:
    def __init__(self, reserve=None):
        self.reserve = reserve
        self.records = {}
        self.current = defaultdict(list)
        self.counts = Counter()
        self.gaps = defaultdict(list)

    @staticmethod
    def signature(event):
        timing = event.get("timing")
        if not isinstance(timing, dict):
            return None
        payload = {key:value for key,value in event.items() if key != "local_witness"}
        try:
            return sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
        except (ValueError, TypeError):
            return None

    def publish(self, event, category, scope, config, *, family=None, active=False,
                predicate, dependencies=(), coverage=True, lineage=False):
        timing = event["timing"]
        version_valid = timing.get("timing_policy") == "STRICT_CAUSAL" and timing.get("timing_policy_version") == VERSION
        dependencies = tuple(dependencies)
        timing.update(evidence_category=category, configuration_fingerprint=config,
                      predicate=predicate, lineage_dependencies=list(dependencies),
                      timing_policy_version=VERSION, coverage="ESTABLISHED" if coverage else "UNKNOWN")
        event_id = event["id"]
        source = tuple(scope.get(key) for key in SOURCE_FIELDS)
        available = timing["actual_availability"]
        start, end = timing["observation_start"], timing["observation_end"]
        barrier = timing["dependency_available_at"]
        kind, state = event.get("type"), event.get("state")
        supported = ((kind in STRUCTURE and event.get("status") == "confirmed") if category == "structure"
                     else state in STATES.get(category, ()) and
                     (category not in {"liquidity","pool"} or event.get("status") == "confirmed"))
        supported_lineage = lineage and state in LINEAGE.get(category, ())
        expected = (
            ("FORMATION" if kind == "PIVOT" else "CLOSE_ONLY") if category == "structure" else
            "CLOSE_ONLY" if category in {"liquidity","pool"} and state == "BROKEN" else
            "AGGREGATED_REFERENCE" if category == "crt" and state == "ACTIVE" else
            "FORMATION" if category == "pool" and state in {"ACTIVE","FORMING"} or
                          category in {"order_block","fvg"} and state == "ACTIVE" else "WHOLE_CANDLE")
        shape = (version_valid and isinstance(event_id,str) and bool(event_id) and predicate in ACTIVATION and predicate == expected and isinstance(config,str) and bool(config)
                 and all(isinstance(value,str) and value for value in source[:-1]) and (source[-1] is None or isinstance(source[-1],str))
                 and type(event.get("algorithm_version")) is int
                 and event.get("algorithm_version") in ({1,2} if category == "structure" else {1})
                 and all(type(value) is int for value in (available,start,end,barrier,timing.get("evaluation_cutoff")))
                 and 0 <= start < end <= available <= timing["evaluation_cutoff"]
                 and event.get("available_at") == available and event.get("confirmation_time") == end)
        temporal = ((barrier <= start) if predicate == "WHOLE_CANDLE" else
                    (barrier < end) if predicate == "CLOSE_ONLY" else barrier <= available)
        required = tuple(dict.fromkeys((*timing["required_dependencies"], *dependencies)))
        compatible = True
        allowed_parents = {"order_block":{"structure"}, "pool":{"structure"}, "liquidity":{"pool"}}
        activation = timing["required_dependencies"]
        if supported and predicate in {"WHOLE_CANDLE","CLOSE_ONLY"}:
            compatible = bool(activation) and max(
                (self.records[key].available if key in self.records else -1 for key in activation), default=-1
            ) == barrier
        for dependency in required:
            self.counts["dependency_lookups"] += 1
            known = self.records.get(dependency)
            # Parent engine configurations may differ from a dependent engine. The edge
            # explicitly pins the resolved configuration for such cross-engine dependencies.
            compatible &= bool(dependency != event_id and known and known.attested and
                               known.available <= available and known.scope == source and
                               (known.category in allowed_parents.get(category, ())
                                or known.category == category and known.config == config))
        timing["dependency_configurations"] = {
            key:self.records[key].config for key in required if key in self.records
        }
        direct = (shape and temporal and coverage and timing["availability_basis"] == "RECORDED" and
                  timing["source_authority"] == "CHECKSUM_VERIFIED_ARCHIVE")
        claimed = timing["strategy_eligibility"]
        attested = direct and compatible and (supported and claimed == "ELIGIBLE" or
                                              supported_lineage and claimed in {"RESEARCH_ONLY","ELIGIBLE"})
        eligible = attested and supported
        if attested and supported_lineage and not supported:
            timing.update(strategy_eligibility="RESEARCH_ONLY", eligibility_reason="RESEARCH_ONLY")
        elif claimed == "ELIGIBLE" and not eligible:
            reason = ("DEPENDENCY_UNAVAILABLE" if not compatible else "INVALID_TEMPORAL_EVIDENCE")
            timing.update(strategy_eligibility=reason, eligibility_reason=reason)
        summary = Summary(self.signature(event),source,config,category,family,available,attested,eligible)
        existing = self.records.get(event_id)
        if existing is not None:
            if existing != summary:
                raise ValueError("Conflicting canonical strategy evidence publication")
            self.counts["publication_reuses"] += 1
            return
        if summary.signature is None:
            return
        if self.reserve is not None:
            self.reserve(512)
        self.records[event_id] = summary
        self.counts["publications"] += 1
        if family is not None:
            insort_right(self.current[family],(available,event_id if active and attested else None),key=lambda item:item[0])

    def gap(self, category, config, scope, available):
        source = tuple(scope.get(key) for key in SOURCE_FIELDS)
        history = self.gaps[category,config,source]
        if not history or history[-1] != available:
            insort_right(history,available)

    def current_at(self, event_id, cutoff):
        record = self.records.get(event_id)
        if not record or not record.eligible or record.available > cutoff:
            return False
        history = self.current.get(record.family, ())
        index = bisect_right(history,cutoff,key=lambda item:item[0])-1
        gaps = self.gaps.get((record.category,record.config,record.scope), ())
        gap = bisect_right(gaps,cutoff)-1
        return (index >= 0 and history[index][1] == event_id
                and (gap < 0 or gaps[gap] < record.available))

    def eligible(self, event, cutoff, *, current=False):
        self.counts["consumer_lookups"] += 1
        if not isinstance(event,dict) or not isinstance(event.get("id"),str) or type(cutoff) is not int:
            return False
        record = self.records.get(event["id"])
        if not record or not record.eligible or record.available > cutoff or record.signature != self.signature(event):
            return False
        return not current or self.current_at(event["id"],cutoff)
