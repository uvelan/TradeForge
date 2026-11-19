"""Canonical request-scoped evidence issued by the analytical engines."""
from bisect import bisect_right, insort_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import NotRequired, TypedDict
from .arrival_policy import (RECORDED_AUTHORITY, RECORDED_MODE, SIMULATION_AUTHORITY, SIMULATION_ELIGIBLE,
                             SIMULATION_MODE)

VERSION = 3
REFERENCE_VERSION = 1
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
    "smt_pivot": {"CONFIRMED"},
    "smt": {"SMT_CONFIRMED"},
}
LINEAGE = {"pool": {"FORMING", "SWEEP_CANDIDATE"}, "smt": {"SMT_CANDIDATE"}}
SMT_SIDES = ("primary", "comparison")
SMT_CATEGORIES = {"smt_pivot", "smt_match", "smt"}
SMT_ALGORITHM_VERSION = 2
SMT_LINEAGE = {
    "SMT_CANDIDATE": {None},
    "SMT_CONFIRMED": {"SMT_CANDIDATE"},
    "SMT_INVALIDATED": {"SMT_CANDIDATE", "SMT_CONFIRMED"},
    "EXPIRED": {"SMT_CANDIDATE", "SMT_CONFIRMED"},
}


def fingerprint(payload):
    return sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def history_fingerprint(rows):
    digest=sha256()
    for bar,stamp in rows:
        digest.update(bar.model_dump_json().encode())
        digest.update(f":{stamp};".encode())
    return digest.hexdigest()


class EvidenceReference(TypedDict):
    version: int
    category: str
    event_id: str
    calculation_id: str
    calculation_snapshot: str
    configuration: str
    source: list[str | None]
    available_at: int
    source_authority: str
    availability_mode: NotRequired[str]
    participants: NotRequired[dict[str, list[str | None]]]


@dataclass(frozen=True,slots=True)
class Summary:
    signature: str
    scope: tuple
    config: str
    category: str
    family: str | None
    available: int
    attested: bool
    eligible: bool
    reference: EvidenceReference | None = None
    state: str | None = None
    match: str | None = None
    pivot_edges: tuple = ()


@dataclass(frozen=True, slots=True)
class SmtUncertainty:
    start: int
    reason: str
    recovered_at: int | None = None


class EvidenceRegistry:
    def __init__(self, reserve=None, *, root=None, calculation_id=None, snapshot=None, cutoff=None,
                 purpose=RECORDED_MODE):
        self.root = root if root is not None else self
        self.calculation_id = calculation_id
        self.snapshot = snapshot
        self.cutoff = cutoff
        self.purpose = purpose if root is None else root.purpose
        self.calculations = {} if root is None else root.calculations
        self.reserve = reserve
        self.records = {}
        self.current = defaultdict(list)
        self.counts = Counter() if root is None else root.counts
        self.gaps = defaultdict(list)
        self.producer_snapshots = {}
        self.smt_producers = None
        self.smt_lifecycle = defaultdict(list)
        self.smt_uncertainty = {}

    def calculation(self, engine, configuration, source, history_fingerprint, cutoff):
        calculation_id = fingerprint([REFERENCE_VERSION,self.purpose,engine,configuration,source])
        snapshot = fingerprint([calculation_id,history_fingerprint,cutoff])
        if snapshot not in self.calculations:
            self.calculations[snapshot] = EvidenceRegistry(
                self.reserve,root=self.root,calculation_id=calculation_id,snapshot=snapshot,cutoff=cutoff)
        return self.calculations[snapshot]

    def pin_producer(self, category, producer):
        existing=self.producer_snapshots.setdefault(category,producer.snapshot)
        if existing != producer.snapshot:
            raise ValueError("Multiple producer calculations for one evidence dependency category")

    def pin_smt_producers(self, primary, comparison, participants):
        sources = {side: tuple(participants[side].get(key) for key in SOURCE_FIELDS) for side in SMT_SIDES}
        if (not self.snapshot or primary.root is not self.root or comparison.root is not self.root
                or len({self.snapshot, primary.snapshot, comparison.snapshot}) != 3
                or not primary.snapshot or not comparison.snapshot
                or sources["primary"][1] == sources["comparison"][1]):
            raise ValueError("SMT requires two independent producer calculations")
        binding = {side: (producer.snapshot, sources[side])
                   for side, producer in zip(SMT_SIDES, (primary, comparison))}
        if self.smt_producers is not None and self.smt_producers != binding:
            raise ValueError("Conflicting SMT producer binding")
        self.smt_producers = binding

    def resolve(self, reference):
        if not isinstance(reference,dict):
            return None
        snapshot,event_id=reference.get("calculation_snapshot"),reference.get("event_id")
        if not isinstance(snapshot,str) or not isinstance(event_id,str):
            return None
        producer=self.calculations.get(snapshot)
        record=producer.records.get(event_id) if producer is not None else None
        return record if record is not None and record.reference==reference else None

    def record_smt_uncertainty(self, family, boundaries):
        if not self.smt_lifecycle.get(family) or any(
            type(edge.start) is not int or not 0 <= edge.start <= self.cutoff or
            edge.recovered_at is not None and (type(edge.recovered_at) is not int or
                                              not edge.start < edge.recovered_at <= self.cutoff)
            for edge in boundaries):
            raise ValueError("Invalid SMT uncertainty boundary")
        canonical = tuple(sorted(boundaries, key=lambda edge: (edge.start, edge.reason)))
        if family in self.smt_uncertainty:
            if self.smt_uncertainty[family] != canonical:
                raise ValueError("Conflicting SMT uncertainty boundaries")
        else:
            if self.reserve is not None:
                self.reserve(128*len(canonical))
            self.smt_uncertainty[family] = canonical

    def smt_state(self, family, cutoff):
        if self.cutoff is None or cutoff > self.cutoff:
            return None, "OUTSIDE_EVALUATED_SNAPSHOT"
        history = self.smt_lifecycle.get(family, ())
        index = bisect_right(history, cutoff, key=lambda row: row[0])-1
        if index < 0:
            return None, None
        state = self.records[history[index][1]].state
        if state in {"SMT_CANDIDATE", "SMT_CONFIRMED"}:
            for edge in self.smt_uncertainty.get(family, ()):
                if edge.start <= cutoff and (edge.recovered_at is None or cutoff < edge.recovered_at):
                    return "UNCERTAIN", edge.reason
            if state == "SMT_CANDIDATE":
                return state, "PENDING_SHARED_CONFIRMATION"
        return state, None

    def _smt_lineage(self, family, state, scope, config, barrier, available, pivot_edges, links):
        if state not in SMT_LINEAGE:
            return False, {}
        expected = {"match"} if state == "SMT_CANDIDATE" else {"match", "previous"}
        if not family or set(links) != expected:
            return False, {}
        resolved = {}
        for role, ref in links.items():
            self.counts["dependency_lookups"] += 1
            row = self.resolve(ref)
            if (row is None or ref["calculation_snapshot"] != self.snapshot or not row.attested
                    or (row.family, row.scope, row.config) != (family, scope, config)):
                return False, {}
            resolved["smt:" + role] = row
        match = resolved["smt:match"]
        if match.category != "smt_match" or match.available > barrier or match.pivot_edges != pivot_edges:
            return False, {}
        history = self.smt_lifecycle.get(family, ())
        prior_ref = links.get("previous")
        prior_id = prior_ref["event_id"] if prior_ref else None
        if prior_id != (history[-1][1] if history else None):
            return False, {}
        prior = resolved.get("smt:previous")
        prior_state = prior.state if prior else None
        if prior_state not in SMT_LINEAGE[state] or (prior and (
            prior.category != "smt" or prior.match != links["match"]["event_id"] or prior.available > available
            or state == "SMT_CONFIRMED" and prior.available != barrier
        )):
            return False, {}
        return True, resolved

    @staticmethod
    def signature(event):
        timing = event.get("timing")
        if not isinstance(timing, dict):
            return None
        payload = {key:value for key,value in event.items() if key != "local_witness"}
        try:
            return fingerprint(payload)
        except (ValueError, TypeError):
            return None

    def publish(self, event, category, scope, config, *, family=None, active=False,
                predicate, dependencies=(), coverage=True, lineage=False, producer_references=(), smt_links=None):
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
        supported_lineage = lineage and (state in LINEAGE.get(category, ()) or
            category == "smt_match" and state == "MATCH_CLOSED" or
            category == "smt" and state in {"SMT_INVALIDATED", "EXPIRED"})
        expected = (
            ("FORMATION" if kind == "PIVOT" else "CLOSE_ONLY") if category == "structure" else
            "CLOSE_ONLY" if category in {"liquidity","pool"} and state == "BROKEN" else
            "AGGREGATED_REFERENCE" if category == "crt" and state == "ACTIVE" else
            "FORMATION" if category in {"smt_pivot", "smt_match"} or category == "smt" and state == "SMT_CANDIDATE" or
                          category == "smt" and state == "SMT_CONFIRMED" and event.get("confirmation_bars") == 0 or
                          category == "pool" and state in {"ACTIVE","FORMING"} or
                          category in {"order_block","fvg"} and state == "ACTIVE" else "WHOLE_CANDLE")
        shape = (version_valid and isinstance(event_id,str) and bool(event_id) and predicate in ACTIVATION and predicate == expected and isinstance(config,str) and bool(config)
                 and all(isinstance(value,str) and value for value in source[:-1]) and (source[-1] is None or isinstance(source[-1],str))
                 and type(event.get("algorithm_version")) is int
                 and event.get("algorithm_version") in ({SMT_ALGORITHM_VERSION} if category in SMT_CATEGORIES else
                                                        {1,2} if category == "structure" else {1})
                 and all(type(value) is int for value in (available,start,end,barrier,timing.get("evaluation_cutoff")))
                 and 0 <= start < end <= available <= timing["evaluation_cutoff"]
                 and event.get("available_at") == available and event.get("confirmation_time") == end)
        if category == "smt_match":
            prefix = event.get("coverage_prefix")
            shape = (shape and isinstance(prefix, dict) and prefix.get("start") == start and prefix.get("end") == end
                     and type(prefix.get("previous_end")) is int and start < prefix["previous_end"] < end
                     and all(isinstance(prefix.get(key), str) and len(prefix[key]) == 64
                             for key in ("digest", "previous_digest"))
                     and available == barrier)
        temporal = ((barrier <= start) if predicate == "WHOLE_CANDLE" else
                    (barrier < end) if predicate == "CLOSE_ONLY" else barrier <= available)
        required = tuple(dict.fromkeys((*timing["required_dependencies"], *dependencies)))
        pinned={}
        for reference in producer_references:
            if isinstance(reference,dict) and isinstance(reference.get("event_id"),str):
                pinned.setdefault(reference["event_id"], []).append(reference)
        resolved={}
        ambiguous=set()
        smt_binding = self.smt_producers if category in {"smt", "smt_match"} else None
        if smt_binding:
            for reference in producer_references:
                record = self.resolve(reference)
                if record is None or record.category != "smt_pivot" or not record.eligible:
                    continue
                for side, binding in smt_binding.items():
                    if (reference["calculation_snapshot"], record.scope) == binding:
                        resolved[f"{side}:{reference['event_id']}"] = record
        for dependency in (() if smt_binding else required):
            references=pinned.get(dependency, ())
            if references:
                if any(ref != references[0] for ref in references[1:]):
                    ambiguous.add(dependency)
                reference=references[0]
                category_snapshot=self.producer_snapshots.get(reference.get("category"))
                resolved[dependency]=(self.resolve(reference) if
                    reference.get("calculation_snapshot") in {self.snapshot,category_snapshot}
                    else None)
            else:
                resolved[dependency]=self.records.get(dependency)
        compatible = not ambiguous
        if category in {"smt", "smt_match"}:
            compatible = bool(smt_binding and len(required) == len(producer_references) == 4
                              and set(required) == set(resolved)
                              and all(sum(key.startswith(side + ":") for key in required) == 2
                                      for side in SMT_SIDES))
        elif category == "smt_pivot":
            compatible = not required and not producer_references
        allowed_parents = {"order_block":{"structure"}, "pool":{"structure"}, "liquidity":{"pool"}}
        activation = timing["required_dependencies"]
        if supported and predicate in {"WHOLE_CANDLE","CLOSE_ONLY"} and category != "smt":
            compatible = compatible and bool(activation) and max(
                (resolved[key].available if resolved.get(key) else -1 for key in activation), default=-1
            ) == barrier
        for dependency in required:
            self.counts["dependency_lookups"] += 1
            known = resolved.get(dependency)
            # Parent engine configurations may differ from a dependent engine. The edge
            # explicitly pins the resolved configuration for such cross-engine dependencies.
            compatible &= bool(dependency != event_id and known and known.attested and
                               (known.available <= barrier if smt_binding else known.available <= available) and
                               (smt_binding or known.scope == source and
                                (known.category in allowed_parents.get(category, ())
                                 or known.category == category and known.config == config)))
        match_id = None
        pivot_edges = ()
        if category in {"smt_match", "smt"} and compatible:
            pivot_edges = tuple(sorted((key, record.reference["calculation_snapshot"], record.reference["event_id"])
                                       for key, record in resolved.items()))
        if category == "smt":
            links = smt_links or {}
            valid, local_records = self._smt_lineage(family, state, source, config, barrier, available, pivot_edges, links)
            compatible &= valid
            resolved.update(local_records)
            timing["smt_lifecycle_dependencies"] = list(local_records)
            if valid:
                match_id = links["match"]["event_id"]
        if self.snapshot is None:
            timing["dependency_configurations"] = {
                key:record.config for key,record in resolved.items() if record
            }
        else:
            timing["dependency_references"] = {
                key:{**record.reference,"source":list(record.scope),
                     **({"participants": {side: list(scope) for side, scope in record.reference["participants"].items()}}
                        if "participants" in record.reference else {})}
                for key,record in resolved.items() if record and record.reference
            }
        simulated = self.purpose == SIMULATION_MODE
        basis = "MODELED_NOMINAL_CLOSE_PLUS_DELAY" if simulated else "RECORDED"
        authority = SIMULATION_AUTHORITY if simulated else RECORDED_AUTHORITY
        accepted = SIMULATION_ELIGIBLE if simulated else "ELIGIBLE"
        direct = (shape and temporal and coverage and timing["availability_basis"] == basis and
                  timing["source_authority"] == authority)
        claimed = timing["strategy_eligibility"]
        attested = direct and compatible and (supported and claimed == accepted or
                                              supported_lineage and claimed in {"RESEARCH_ONLY",accepted})
        eligible = attested and supported
        if attested and supported_lineage and not supported:
            timing.update(strategy_eligibility="RESEARCH_ONLY", eligibility_reason="RESEARCH_ONLY")
        elif claimed in {"ELIGIBLE", SIMULATION_ELIGIBLE} and not eligible:
            reason = ("DEPENDENCY_UNAVAILABLE" if not compatible else "INVALID_TEMPORAL_EVIDENCE")
            timing.update(strategy_eligibility=reason, eligibility_reason=reason)
        reference=None
        if self.snapshot is not None:
            reference={"version":REFERENCE_VERSION,"category":category,"event_id":event_id,
                       "calculation_id":self.calculation_id,"calculation_snapshot":self.snapshot,
                       "configuration":config,"source":list(source),"available_at":available,
                       "source_authority":timing["source_authority"]}
            reference["availability_mode"] = self.purpose
            if smt_binding:
                reference["participants"] = {side: list(source) for side, (_, source) in smt_binding.items()}
            event["evidence_reference"]=reference
        summary = Summary(self.signature(event),source,config,category,family,available,attested,eligible,
                          reference,state,match_id,pivot_edges)
        if reference is not None:
            # Registry state must not alias mutable response projections.
            reference={**reference,"source":list(source)}
            if smt_binding:
                reference["participants"] = {side: list(source) for side, (_, source) in smt_binding.items()}
            summary=Summary(summary.signature,source,config,category,family,available,attested,eligible,
                            reference,state,match_id,pivot_edges)
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
        if category == "smt" and attested:
            self.smt_lifecycle[family].append((available, event_id))
        if family is not None and category != "smt_match" and (category != "smt" or attested):
            insort_right(self.current[family],(available,event_id if active and attested else None),key=lambda item:item[0])

    def gap(self, category, config, scope, available):
        source = tuple(scope.get(key) for key in SOURCE_FIELDS)
        history = self.gaps[category,config,source]
        if not history or history[-1] != available:
            insort_right(history,available)

    def current_at(self, event_id, cutoff, *, purpose=RECORDED_MODE):
        if purpose != self.purpose or self.cutoff is not None and cutoff > self.cutoff:
            return False
        if isinstance(event_id,dict):
            record=self.resolve(event_id)
            return record is not None and self.calculations[event_id["calculation_snapshot"]].current_at(
                record.reference["event_id"],cutoff,purpose=purpose)
        record = self.records.get(event_id)
        if not record or not record.eligible or record.available > cutoff:
            return False
        if record.category == "smt" and self.smt_state(record.family, cutoff)[0] != "SMT_CONFIRMED":
            return False
        history = self.current.get(record.family, ())
        index = bisect_right(history,cutoff,key=lambda item:item[0])-1
        gaps = self.gaps.get((record.category,record.config,record.scope), ())
        gap = bisect_right(gaps,cutoff)-1
        return (index >= 0 and history[index][1] == event_id
                and (gap < 0 or gaps[gap] < record.available))

    def eligible(self, event, cutoff, *, current=False, purpose=RECORDED_MODE):
        self.counts["consumer_lookups"] += 1
        if (purpose != self.purpose or not isinstance(event,dict) or not isinstance(event.get("id"),str)
                or type(cutoff) is not int):
            return False
        reference=event.get("evidence_reference")
        record=self.resolve(reference) if reference is not None else self.records.get(event["id"])
        if not record or not record.eligible or record.available > cutoff or record.signature != self.signature(event):
            return False
        return not current or self.current_at(reference if reference is not None else event["id"],cutoff,purpose=purpose)
