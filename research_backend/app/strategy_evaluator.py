"""Bounded chronological strategy decisions over exact canonical evidence."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from operator import eq, ge, gt, le, lt
from heapq import heapify, heappop, heappush

from .analysis_evidence import ResourceLimit
from .arrival_policy import SIMULATION_AUTHORITY, SIMULATION_MODE
from .strategy_binding import BoundEvidence, activate_crt_attempt, crt_family_validity, expiry_status, select_candidate
from .strategy_catalog import OBSERVATIONS, ROLE_OBSERVATIONS
from .strategy_definitions import Outcome, logical_outcome
from .strategy_evidence import fingerprint
from .strategy_current_state import CurrentKind
from .strategy_evaluation_sources import MAX_TIMELINE

YES, NO, UNKNOWN = Outcome
PHASES = ("context","setup","invalidation","trigger")
TERMINAL = {"TRIGGER_QUALIFIED","INVALIDATED","EXPIRED","NO_TRADE"}
OPERATORS = {"EQ":eq,"GTE":ge,"GT":gt,"LTE":le,"LT":lt}
CATALOG = {**OBSERVATIONS,**ROLE_OBSERVATIONS}
MAX_SCANS = 1_000_000


@dataclass(frozen=True)
class RuleResult:
    outcome: Outcome
    reason: str
    proof: object = None
    event: dict | None = None
    selected_at: int | None = None

    def trace(self, rule, phase):
        ref = self.proof.reference.model_dump(mode="json",exclude_none=True) if self.proof else None
        return {"id":rule.id,"phase":phase,"observation":rule.observation,"dependency":rule.dependency,
                "outcome":self.outcome.value,"reason":self.reason,"evidence_reference":ref,
                "family":self.proof.family if self.proof else None,
                "observed_at":self.proof.observed_at if self.proof else None,
                "available_at":self.proof.reference.available_at if self.proof else None,
                "selected_at":self.selected_at}


@dataclass(frozen=True)
class Edge:
    before: str
    after: str
    within: object = None
    label: str = "AFTER"


class RuleGraph:
    def __init__(self, definition):
        self.rules = {}
        self.phases = {}
        self.aliases = {}
        self.predecessors = defaultdict(list)
        self.temporal = defaultdict(list)
        self.groups = {phase:getattr(definition,phase) for phase in PHASES}
        for phase,group in self.groups.items():
            self.register(group,phase)
        for link in definition.temporal:
            before,after = (link.then,link.first) if link.relation=="BEFORE" else (link.first,link.then)
            self.add(Edge(before,after,link.within,link.relation))
        for rule in self.rules.values():
            if rule.same_family_as:
                self.predecessors[rule.id].append(self.aliases[rule.same_family_as])
        self.order = []
        done = set()
        visiting = set()

        def visit(identifier):
            if identifier in visiting:
                raise ValueError("Cyclic strategy dependency")
            if identifier in done:
                return
            visiting.add(identifier)
            for parent in self.predecessors[identifier]:
                visit(parent)
            self.order.append(identifier)
            done.add(identifier)
            visiting.remove(identifier)

        for identifier in sorted(self.rules):
            visit(identifier)

    def add(self, edge):
        self.predecessors[edge.after].append(edge.before)
        self.temporal[edge.after].append(edge)

    def register(self, node, phase):
        if node.type=="RULE":
            self.rules[node.id] = node
            self.phases[node.id] = phase
            if node.alias:
                self.aliases[node.alias] = node.id
            return
        for child in node.children:
            self.register(child,phase)
        if node.operator=="SEQUENCE":
            for before,after in zip(node.children,node.children[1:]):
                self.add(Edge(before.id,after.id,label="SEQUENCE"))
            if node.within:
                self.add(Edge(node.children[0].id,node.children[-1].id,node.within,"SEQUENCE_WINDOW"))

    def outcome(self, node, results):
        if node.type=="RULE":
            return results[node.id].outcome
        operator = "ALL" if node.operator=="SEQUENCE" else node.operator
        return logical_outcome(operator,(self.outcome(child,results) for child in node.children))


def projection_matches(spec, event):
    if spec["category"]=="smt" and event.get("state")!="SMT_CONFIRMED":
        return False
    if any((event.get(name) not in value if isinstance(value,list) else event.get(name)!=value)
           for name,value in spec.get("where",{}).items()):
        return False
    return spec["field"] in event


def predicate(rule, spec, event):
    value = event[spec["field"]]
    if spec.get("projection")=="OPPOSITE_PAIR_ROLE":
        value = {"primary":"comparison","comparison":"primary"}.get(value)
    if spec["value_type"]=="number" and type(value) not in {float,int}:
        return None
    return OPERATORS[rule.operator](value,rule.expected)


class EvaluationMachine:
    def __init__(self, definition, evidence, identity, start, selection_end, *, check_budget=None):
        self.definition = definition
        self.check_budget = check_budget
        self.evidence = evidence
        self.graph = RuleGraph(definition)
        self.identity = identity
        self.start = start
        self.end = selection_end
        self.role = definition.trade_intent.role
        self.results = {}
        self.phase_results = {}
        self.latches = {}
        self.events_by_id = {snapshot:{row["id"]:row for _,rows in lanes.values() for row in rows}
                             for snapshot,lanes in evidence.events.items()}
        self.bindings = {}
        self.binding_status = {}
        self.temporal = {}
        self.timeline = []
        self.context_at = self.setup_at = self.trigger_at = None
        self.attempt = self.crt_binding = self.latest_crt = None
        self.state = "WAITING_FOR_CONTEXT"
        self.reason = "CONTEXT_PENDING"
        self.invalidation = self.expiry = UNKNOWN
        self.scans = self.lookups = 0
        self.crt_revisions = {}
        tracked = definition.attempt.crt_reference_alias
        if tracked:
            rule = self.graph.rules[self.graph.aliases[tracked]]
            view = evidence.views[rule.dependency]
            stamps, rows = evidence.events[view.snapshot].get("crt",((),()))
            for row in rows:
                self.crt_revisions.setdefault(row["range_id"],[]).append(row)
            for rows in self.crt_revisions.values():
                rows.sort(key=lambda row:(row["available_at"],row["revision"]))

    def transition(self, cutoff, state, reason):
        if not self.timeline or self.timeline[-1][1:]!=[state,reason]:
            self.timeline.append([cutoff,state,reason])
        self.state,self.reason = state,reason

    def bars_since(self, bound, origin, cutoff):
        lane = self.evidence.coverage[f"{self.role}:{bound.timeframe}"]
        if lane.at(cutoff)["status"]!="COMPLETE":
            return None
        return bisect_right(lane.observed,cutoff)-bisect_right(lane.observed,origin)

    def in_window(self, bound, begin, end):
        if bound is None:
            return True
        if bound.unit=="SECONDS":
            return end-begin <= bound.count
        count = self.bars_since(bound,begin,end)
        return None if count is None else count<=bound.count

    def latest_family(self, proof, cutoff):
        rows = self.crt_revisions.get(proof.family,())
        index = bisect_right(rows,cutoff,key=lambda row:row["available_at"])-1
        return rows[index] if index>=0 else None

    def select(self, rule, phase, cutoff, lower):
        spec = CATALOG[rule.observation]
        view = self.evidence.views[rule.dependency]
        established = self.bindings.get(rule.id)
        bound = None
        if rule.same_family_as:
            parent_id = self.graph.aliases[rule.same_family_as]
            parent = self.results.get(parent_id)
            if not parent or parent.outcome!=YES:
                return RuleResult(UNKNOWN,"BOUND_FAMILY_UNAVAILABLE")
            bound = self.bindings.get(parent_id,parent).proof
        prerequisites = []
        for edge in self.graph.temporal[rule.id]:
            parent = self.results.get(edge.before)
            if not parent or parent.outcome!=YES:
                reason = "TEMPORAL_PREDECESSOR_UNKNOWN" if not parent or parent.outcome==UNKNOWN else "TEMPORAL_PREDECESSOR_FALSE"
                outcome = UNKNOWN if not parent or parent.outcome==UNKNOWN else NO
                self.temporal[(edge.before,rule.id,edge.label)] = [outcome.value,reason]
                return RuleResult(outcome,reason)
            predecessor = self.bindings.get(edge.before,parent)
            anchor = predecessor.proof.reference.available_at
            if self.graph.phases[edge.before]!=phase:
                anchor = max(anchor,predecessor.selected_at)
            prerequisites.append((edge,anchor))
        policy = rule.selection
        stamps, events = self.evidence.events[view.snapshot].get(spec["category"],((),()))
        age_start = cutoff-rule.evidence.max_age_seconds
        end = bisect_right(stamps,cutoff)
        activating_crt = not self.attempt and rule.alias and rule.alias==self.definition.attempt.crt_reference_alias
        eligible = []
        uncertainty = None
        outside = False
        current = rule.evidence.state=="CURRENT"
        seen = set()
        start = 0 if current else bisect_left(stamps,max(lower,age_start))
        indexes = range(end-1,-1,-1) if current else range(start,end)
        for index in indexes:
            self.scans += 1
            if self.check_budget and self.scans%1024==0:
                self.check_budget()
            if self.scans>MAX_SCANS:
                raise ResourceLimit("Combined rule evidence scan budget exceeded")
            event = events[index]
            record = view.records[event["id"]]
            key = (record.category,record.config,record.scope,record.family)
            if ((bound and record.family!=bound.family)
                    or (established and record.family!=established.proof.family)):
                continue
            if current:
                if key in seen:
                    continue
                seen.add(key)
                state = view.current_state(event["evidence_reference"],cutoff,purpose=SIMULATION_MODE)
                if established:
                    self.binding_status[rule.id] = state.kind.value
                if state.kind == CurrentKind.INACTIVE:
                    if established:
                        return RuleResult(NO,"BOUND_FAMILY_TERMINAL")
                    continue
                if state.kind != CurrentKind.ACTIVE:
                    # SMT direction and role are fixed across its lifecycle.
                    if spec["category"]!="smt" or predicate(rule,spec,event) is not False:
                        uncertainty = uncertainty or state.reason
                    continue
                event = self.events_by_id[view.snapshot][state.revision]
                record = view.records[state.revision]
            if established and not current and event["id"]!=established.proof.reference.event_id:
                continue
            if not projection_matches(spec,event):
                if (event.get("state") in {"UNCERTAIN","AMBIGUOUS"}
                        and rule.expected not in {"ACTIVE","FORMING"}):
                    uncertainty = uncertainty or record.state
                continue
            matches = predicate(rule,spec,event)
            if matches is None:
                uncertainty = uncertainty or "INVALID_FIELD_PROJECTION"
                continue
            if not matches:
                continue
            if not record.eligible or event["evidence_reference"]["source_authority"]!=SIMULATION_AUTHORITY:
                uncertainty = uncertainty or event["timing"]["eligibility_reason"]
                continue
            if current and record.available < max(lower,age_start):
                uncertainty = uncertainty or ("BOUND_EVIDENCE_STALE" if record.available<age_start else
                                              "CURRENT_MATCH_PRECEDES_PHASE")
                continue
            if activating_crt:
                proof = BoundEvidence(reference=event["evidence_reference"], family=record.family,
                    market_event_id=event.get("market_event_id") or record.family,
                    observed_at=event["timing"]["observation_start"])
                revision = self.latest_family(proof,cutoff)
                validity = crt_family_validity(self.evidence.registry,proof,revision,snapshot=view.snapshot,
                    cutoff=cutoff,coverage_known=True,purpose=SIMULATION_MODE)
                if validity!=YES:
                    if validity==UNKNOWN:
                        uncertainty = uncertainty or "CRT_REVISION_UNCERTAIN"
                    continue
            available = record.available
            for edge,anchor in prerequisites:
                within = self.in_window(edge.within,anchor,available) if available>anchor else False
                if within is None:
                    uncertainty = uncertainty or "TEMPORAL_WINDOW_UNRESOLVED"
                if not within:
                    outside = True
                    break
            else:
                eligible.append(event)
                if len(eligible)>self.definition.attempt.candidate_limit:
                    return RuleResult(UNKNOWN,"CANDIDATE_LIMIT_EXCEEDED")
        if eligible:
            self.lookups += len(eligible)
            outcome,proof = select_candidate(self.evidence.registry,eligible,snapshot=view.snapshot,
                cutoff=cutoff,policy=policy,age=rule.evidence.max_age_seconds,
                limit=self.definition.attempt.candidate_limit,current=False,
                bound=bound,coverage_known=True,purpose=SIMULATION_MODE)
            if outcome==YES:
                event = next(row for row in eligible if row["id"]==proof.reference.event_id)
                previous = self.results.get(rule.id)
                selected_at = (previous.selected_at if previous and previous.proof==proof else cutoff)
                if established and established.proof==proof:
                    selected_at = established.selected_at
                result = RuleResult(YES,"CANONICAL_EVIDENCE",proof,event,selected_at)
                for edge,_ in prerequisites:
                    self.temporal[(edge.before,rule.id,edge.label)] = [YES.value,"CAUSAL_WINDOW_SATISFIED"]
                return result
            return RuleResult(UNKNOWN,"EXACT_EVIDENCE_INELIGIBLE")
        for edge,_ in prerequisites:
            self.temporal[(edge.before,rule.id,edge.label)] = [
                UNKNOWN.value if uncertainty else NO.value, "NO_COMPATIBLE_CAUSAL_CANDIDATE"]
        if uncertainty:
            return RuleResult(UNKNOWN,uncertainty)
        if any(category==spec["category"] and bisect_right(gaps,cutoff)>bisect_left(gaps,lower)
               for (category,_,_),gaps in view.gaps.items()):
            return RuleResult(UNKNOWN,"PRODUCER_COVERAGE_GAP")
        if spec["category"]=="smt":
            return RuleResult(UNKNOWN,"SMT_ABSENCE_NOT_ESTABLISHED")
        if not current and age_start>lower and start>bisect_left(stamps,lower):
            return RuleResult(UNKNOWN,"EVIDENCE_WINDOW_STALE")
        return RuleResult(NO,"CAUSAL_WINDOW_NOT_MET" if outside else "NO_MATCH_COMPLETE_COVERAGE")

    def phase(self, phase, cutoff, lower):
        for id_ in self.graph.order:
            if self.graph.phases[id_]!=phase:
                continue
            self.results[id_] = (self.latches[id_] if id_ in self.latches else
                                 self.select(self.graph.rules[id_],phase,cutoff,lower))
        outcome = self.graph.outcome(self.graph.groups[phase],self.results)
        self.phase_results[phase] = outcome.value
        return outcome

    def bind(self, phase):
        for id_, rule in self.graph.rules.items():
            result = self.results.get(id_)
            if self.graph.phases[id_]==phase and rule.alias and result and result.outcome==YES:
                self.bindings.setdefault(id_,result)

    def latch(self, phase):
        self.bind(phase)
        self.latches.update((id_,self.results[id_]) for id_ in self.graph.order if self.graph.phases[id_]==phase)

    def activate(self, cutoff):
        tracked = self.definition.attempt.crt_reference_alias
        attempt_id = "ATT-"+fingerprint([self.identity,cutoff])
        if tracked:
            rule = self.graph.rules[self.graph.aliases[tracked]]
            proof = self.results[rule.id].proof
            if proof is None:
                return UNKNOWN
            outcome,binding = activate_crt_attempt(self.evidence.registry,proof,self.latest_family(proof,cutoff),
                snapshot=self.evidence.views[rule.dependency].snapshot,cutoff=cutoff,
                age=rule.evidence.max_age_seconds,coverage_known=True,attempt_id=attempt_id[4:],
                strategy_version_id=self.identity["strategy_version_id"],alias=tracked,purpose=SIMULATION_MODE)
            if outcome!=YES:
                return outcome
            self.crt_binding = binding
            self.latches[rule.id] = self.results[rule.id]
        self.context_at = cutoff
        self.attempt = attempt_id
        self.bind("context")
        if self.definition.attempt.context_mode=="LATCHED":
            self.latch("context")
        return YES

    def family_validity(self, cutoff):
        if not self.crt_binding:
            return YES
        proof = self.crt_binding.evidence
        self.latest_crt = self.latest_family(proof,cutoff)
        return crt_family_validity(self.evidence.registry,proof,self.latest_crt,
            snapshot=self.latest_crt["evidence_reference"]["calculation_snapshot"] if self.latest_crt else
                     proof.reference.calculation_snapshot,cutoff=cutoff,coverage_known=True,purpose=SIMULATION_MODE)

    def pinned_current_guard(self, cutoff):
        for key,binding in self.bindings.items():
            rule = self.graph.rules[key]
            if key not in self.latches or rule.evidence.state!="CURRENT":
                continue
            reference = binding.proof.reference.model_dump(mode="json",exclude_none=True)
            view = self.evidence.views[rule.dependency]
            status = view.current_state(reference,cutoff,purpose=SIMULATION_MODE)
            self.binding_status[key] = status.kind.value
            if status.kind==CurrentKind.INACTIVE:
                self.results[key] = RuleResult(NO,"BOUND_FAMILY_TERMINAL")
                self.transition(cutoff,"INVALIDATED","BOUND_FAMILY_TERMINAL")
                return False
            if status.kind!=CurrentKind.ACTIVE or cutoff-view.records[status.revision].available>rule.evidence.max_age_seconds:
                self.results[key] = RuleResult(UNKNOWN,status.reason if status.kind!=CurrentKind.ACTIVE else "BOUND_EVIDENCE_STALE")
                self.transition(cutoff,"INSUFFICIENT_EVIDENCE","BOUND_CURRENT_EVIDENCE_UNKNOWN")
                return False
            self.results[key] = binding
        return True

    def at(self, cutoff):
        if any(lane.at(cutoff)["status"]!="COMPLETE" for lane in self.evidence.coverage.values()):
            self.transition(cutoff,"INSUFFICIENT_EVIDENCE","REQUIRED_SOURCE_COVERAGE_UNKNOWN")
            return
        if not self.attempt:
            context = self.phase("context",cutoff,self.identity["warmup_start"])
            if context!=YES or self.activate(cutoff)!=YES:
                state = ("NO_TRADE" if cutoff==self.end and context==NO else
                         "WAITING_FOR_CONTEXT" if context==NO else "INSUFFICIENT_EVIDENCE")
                self.transition(cutoff,state,"CONTEXT_OR_REFERENCE_UNAVAILABLE")
                return
            self.transition(cutoff,"WAITING_FOR_SETUP","CONTEXT_ACTIVATED")
        family = self.family_validity(cutoff)
        if family==UNKNOWN:
            self.transition(cutoff,"INSUFFICIENT_EVIDENCE","BOUND_REFERENCE_UNCERTAIN")
            return
        if family==NO:
            state = "EXPIRED" if self.latest_crt.get("state")=="EXPIRED" else "INVALIDATED"
            self.transition(cutoff,state,"BOUND_REFERENCE_TERMINAL")
            return
        if self.definition.attempt.context_mode=="CONTINUOUS":
            context = self.phase("context",cutoff,self.identity["warmup_start"])
            if context!=YES:
                self.transition(cutoff,"INVALIDATED" if context==NO else "INSUFFICIENT_EVIDENCE",
                                "CONTINUOUS_CONTEXT_FAILED")
                return

        if not self.pinned_current_guard(cutoff):
            return

        setup = self.phase("setup",cutoff,self.context_at) if self.setup_at is None else YES
        if setup==YES and self.setup_at is None:
            self.setup_at = cutoff
            self.latch("setup")
            self.transition(cutoff,"SETUP_OBSERVED","SETUP_ACTIVATED")
        self.invalidation = self.phase("invalidation",cutoff,self.context_at)
        if self.invalidation==YES:
            self.transition(cutoff,"INVALIDATED","EXPLICIT_INVALIDATION")
            return
        if self.invalidation==UNKNOWN:
            self.transition(cutoff,"INSUFFICIENT_EVIDENCE","INVALIDATION_UNKNOWN")
            return
        origin = self.context_at if self.definition.attempt.expiry_origin=="CONTEXT_ACTIVATION" else self.setup_at
        if origin is not None:
            bound = self.definition.expiry
            count = self.bars_since(bound,origin,cutoff) if bound.unit=="ELIGIBLE_BARS" else None
            self.expiry = expiry_status(bound,origin,cutoff,traded_role=self.role,bar_count=count)
            if self.expiry!=NO:
                self.transition(cutoff,"EXPIRED" if self.expiry==YES else "INSUFFICIENT_EVIDENCE",
                                "SETUP_EXPIRY_REACHED" if self.expiry==YES else "EXPIRY_COVERAGE_UNKNOWN")
                return
        if setup!=YES:
            state = ("INSUFFICIENT_EVIDENCE" if setup==UNKNOWN else
                     "NO_TRADE" if cutoff==self.end else "WAITING_FOR_SETUP")
            self.transition(cutoff,state,"SETUP_NOT_ESTABLISHED")
            return
        if cutoff<=self.setup_at:
            self.transition(cutoff,"WAITING_FOR_TRIGGER","TRIGGER_REQUIRES_LATER_EVIDENCE")
            return
        trigger = self.phase("trigger",cutoff,self.setup_at+1)
        if trigger==YES:
            self.trigger_at = cutoff
            self.transition(cutoff,"TRIGGER_QUALIFIED","CANONICAL_TRIGGER_QUALIFIED")
        else:
            state = ("INSUFFICIENT_EVIDENCE" if trigger==UNKNOWN else
                     "NO_TRADE" if cutoff==self.end else "WAITING_FOR_TRIGGER")
            self.transition(cutoff,state,"TRIGGER_NOT_ESTABLISHED")

    def intent(self):
        if self.trigger_at is None:
            return None
        trade = self.definition.trade_intent
        binding = self.definition.instruments.primary if self.role=="PRIMARY" else self.definition.instruments.comparison
        return {"id":"INT-"+fingerprint([self.attempt,self.trigger_at]),
                "status":"AWAITING_EXECUTION_VALIDATION","attempt_id":self.attempt,
                "strategy_version_id":self.identity["strategy_version_id"],"role":self.role,"symbol":binding.symbol,
                "direction":trade.direction,"entry":trade.entry.model_dump(mode="json"),
                "price_basis":self.definition.execution.price_basis,"qualified_at":self.trigger_at,
                "earliest_policy":trade.earliest,"strictly_after":self.trigger_at,
                "order_expiry":trade.expiry.model_dump(mode="json"),
                "risk_policy_fingerprint":fingerprint(self.definition.risk.model_dump(mode="json")),
                "management_policy_fingerprint":fingerprint(self.definition.management.model_dump(mode="json"))}

    def expire_during_uncertainty(self, cutoff):
        if self.state != "INSUFFICIENT_EVIDENCE" or self.definition.expiry.unit != "SECONDS":
            return
        origin = self.context_at if self.definition.attempt.expiry_origin=="CONTEXT_ACTIVATION" else self.setup_at
        if expiry_status(self.definition.expiry,origin,cutoff,traded_role=self.role)==YES:
            self.expiry = YES
            self.transition(cutoff,"EXPIRED","SETUP_EXPIRY_DURING_UNCERTAINTY")

    def boundaries(self, cutoff):
        points = {stamp for stamp in self.evidence.timeline if stamp<=cutoff} | {cutoff}
        for view in {view.snapshot:view for view in self.evidence.views.values()}.values():
            for family in view.smt_uncertainty.values():
                for boundary in family:
                    for stamp in (boundary.start,boundary.recovered_at):
                        if stamp is not None and self.start<=stamp<=cutoff:
                            points.add(stamp)
        for rule in self.graph.rules.values():
            view = self.evidence.views[rule.dependency]
            stamps,_ = self.evidence.events[view.snapshot].get(CATALOG[rule.observation]["category"],((),()))
            age = rule.evidence.max_age_seconds + 1
            points.update(stamp+age for stamp in stamps if self.start<=stamp+age<=cutoff)
            if self.check_budget:
                self.check_budget()
            if len(points)>MAX_TIMELINE:
                raise ResourceLimit("Evaluation boundaries exceed the timeline budget")
        return points

    def run(self, cutoff, *, probes=()):
        pending = sorted(self.boundaries(cutoff) | set(probes))
        known = set(pending)
        heapify(pending)
        while pending:
            stamp = heappop(pending)
            if self.check_budget:
                self.check_budget()
            self.at(stamp)
            self.expire_during_uncertainty(stamp)
            if self.state in TERMINAL:
                break
            origin = self.context_at if self.definition.attempt.expiry_origin=="CONTEXT_ACTIVATION" else self.setup_at
            if len(known)>MAX_TIMELINE:
                raise ResourceLimit("Evaluation boundaries exceed the timeline budget")
            if self.setup_at is not None and stamp<self.setup_at+1<=cutoff and self.setup_at+1 not in known:
                known.add(self.setup_at+1)
                heappush(pending,self.setup_at+1)
            if origin is not None and self.definition.expiry.unit=="SECONDS":
                deadline = origin+self.definition.expiry.count+1
                if stamp<deadline<=cutoff and deadline not in known:
                    known.add(deadline)
                    heappush(pending,deadline)
        supporting = {f"{result.proof.reference.calculation_snapshot}:{result.proof.reference.event_id}":result.event
                      for result in (*self.results.values(),*self.bindings.values()) if result.proof}
        if self.latest_crt:
            ref = self.latest_crt["evidence_reference"]
            supporting[f"{ref['calculation_snapshot']}:{ref['event_id']}"] = self.latest_crt
        return {
            "state":self.state,"reason":self.reason,"attempt_id":self.attempt,"terminal":self.state in TERMINAL,
            "cutoff":cutoff,"direction":self.definition.trade_intent.direction,
            "context_activated_at":self.context_at,"setup_activated_at":self.setup_at,"trigger_activated_at":self.trigger_at,
            "transitions":self.timeline,"invalidation":self.invalidation.value,"expiry":self.expiry.value,
            "phase_results":self.phase_results,
            "rules":[self.results[key].trace(self.graph.rules[key],self.graph.phases[key])
                     for key in sorted(self.results)],
            "binding_policy_version":2,
            "attempt_bindings":[{"rule":key,"alias":self.graph.rules[key].alias,
                                  "family":value.proof.family,"activated_at":value.selected_at,
                                  "validity":self.binding_status.get(key),"evidence_state":self.graph.rules[key].evidence.state,
                                  "reference":value.proof.reference.model_dump(mode="json",exclude_none=True)}
                                 for key,value in sorted(self.bindings.items())],
            "bound_crt":self.crt_binding.model_dump(mode="json") if self.crt_binding else None,
            "crt_latest_reference":self.latest_crt["evidence_reference"] if self.latest_crt else None,
            "producers":[{"dependency":key,"calculation_id":view.calculation_id,"snapshot":view.snapshot}
                         for key,view in sorted(self.evidence.views.items())],
            "coverage":[lane.at(cutoff) for lane in self.evidence.coverage.values()],
            "temporal":[{"before":key[0],"after":key[1],"relation":key[2],"outcome":value[0],"reason":value[1]}
                        for key,value in sorted(self.temporal.items())],
            "supporting_events":supporting,"entry_intent":self.intent(),
            "resource_counts":{**self.evidence.metrics,"candidate_scans":self.scans,"exact_lookups":self.lookups,"timeline_boundaries":len(known)},
        }
