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
    def __init__(self, definition, evidence, identity, start, selection_end):
        self.definition = definition
        self.evidence = evidence
        self.graph = RuleGraph(definition)
        self.identity = identity
        self.start = start
        self.end = selection_end
        self.role = definition.trade_intent.role
        self.results = {}
        self.phase_results = {}
        self.latches = {}
        self.tentative = {}
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
        bound = None
        if rule.same_family_as:
            parent = self.results.get(self.graph.aliases[rule.same_family_as])
            if not parent or parent.outcome!=YES:
                return RuleResult(UNKNOWN,"BOUND_FAMILY_UNAVAILABLE")
            bound = parent.proof
        prerequisites = []
        for edge in self.graph.temporal[rule.id]:
            parent = self.results.get(edge.before)
            if not parent or parent.outcome!=YES:
                reason = "TEMPORAL_PREDECESSOR_UNKNOWN" if not parent or parent.outcome==UNKNOWN else "TEMPORAL_PREDECESSOR_FALSE"
                outcome = UNKNOWN if not parent or parent.outcome==UNKNOWN else NO
                self.temporal[(edge.before,rule.id,edge.label)] = [outcome.value,reason]
                return RuleResult(outcome,reason)
            anchor = parent.proof.reference.available_at
            if self.graph.phases[edge.before]!=phase:
                anchor = max(anchor,parent.selected_at)
            prerequisites.append((edge,anchor))
        policy = rule.selection
        pinned = self.tentative.get(rule.id)
        if pinned and pinned.proof and rule.evidence.state=="HISTORICAL":
            visible = cutoff-pinned.proof.reference.available_at<=rule.evidence.max_age_seconds
            return pinned if visible else RuleResult(UNKNOWN,"PINNED_EVIDENCE_STALE")

        stamps, events = self.evidence.events[view.snapshot].get(spec["category"],((),()))
        age_start = cutoff-rule.evidence.max_age_seconds
        end = bisect_right(stamps,cutoff)
        activating_crt = not self.attempt and rule.alias and rule.alias==self.definition.attempt.crt_reference_alias
        eligible = []
        uncertain = False
        outside = False
        start = bisect_left(stamps,max(lower,age_start))
        for index in range(start,end):
            self.scans += 1
            if self.scans>MAX_SCANS:
                raise ResourceLimit("Combined rule evidence scan budget exceeded")
            event = events[index]
            record = view.records[event["id"]]
            if not projection_matches(spec,event) or (bound and record.family!=bound.family):
                continue
            if rule.evidence.state=="CURRENT" and not view.current_at(event["evidence_reference"],cutoff,purpose=SIMULATION_MODE):
                # A superseded event cannot supply a current predicate. Unknown
                # SMT state is handled by its conservative absence lane below.
                continue
            if (not record.eligible or event["evidence_reference"]["source_authority"]!=SIMULATION_AUTHORITY):
                uncertain = True
                continue
            matches = predicate(rule,spec,event)
            if matches is None:
                uncertain = True
                continue
            if not matches:
                continue
            if activating_crt:
                proof = BoundEvidence(reference=event["evidence_reference"], family=record.family,
                    market_event_id=event.get("market_event_id") or record.family,
                    observed_at=event["timing"]["observation_start"])
                revision = self.latest_family(proof,cutoff)
                validity = crt_family_validity(self.evidence.registry,proof,revision,snapshot=view.snapshot,
                    cutoff=cutoff,coverage_known=True,purpose=SIMULATION_MODE)
                if validity!=YES:
                    uncertain |= validity==UNKNOWN
                    continue
            available = record.available
            for edge,anchor in prerequisites:
                within = self.in_window(edge.within,anchor,available) if available>anchor else False
                if within is None:
                    uncertain = True
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
                limit=self.definition.attempt.candidate_limit,current=rule.evidence.state=="CURRENT",
                bound=bound,coverage_known=True,purpose=SIMULATION_MODE)
            if outcome==YES:
                event = next(row for row in eligible if row["id"]==proof.reference.event_id)
                result = RuleResult(YES,"CANONICAL_EVIDENCE",proof,event,cutoff)
                for edge,_ in prerequisites:
                    self.temporal[(edge.before,rule.id,edge.label)] = [YES.value,"CAUSAL_WINDOW_SATISFIED"]
                if self.attempt and rule.alias and (phase=="setup" or phase=="trigger") and rule.evidence.state=="HISTORICAL":
                    self.tentative[rule.id] = result
                return result
            return RuleResult(UNKNOWN,"EXACT_EVIDENCE_INELIGIBLE")
        for edge,_ in prerequisites:
            self.temporal[(edge.before,rule.id,edge.label)] = [
                UNKNOWN.value if uncertain else NO.value, "NO_COMPATIBLE_CAUSAL_CANDIDATE"]
        if uncertain:
            return RuleResult(UNKNOWN,"INELIGIBLE_OR_INCOMPLETE_EVIDENCE")
        if spec["category"]=="smt":
            return RuleResult(UNKNOWN,"SMT_ABSENCE_NOT_ESTABLISHED")
        if age_start>lower and start>bisect_left(stamps,lower):
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

    def latch(self, phase):
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

    def run(self, cutoff):
        pending = list(self.evidence.timeline)
        known = set(pending)
        heapify(pending)
        while pending:
            stamp = heappop(pending)
            self.at(stamp)
            if self.state in TERMINAL:
                break
            origin = self.context_at if self.definition.attempt.expiry_origin=="CONTEXT_ACTIVATION" else self.setup_at
            if origin is not None and self.definition.expiry.unit=="SECONDS":
                deadline = origin+self.definition.expiry.count+1
                if stamp<deadline<=cutoff and deadline not in known:
                    known.add(deadline)
                    heappush(pending,deadline)
        supporting = {f"{result.proof.reference.calculation_snapshot}:{result.proof.reference.event_id}":result.event
                      for result in self.results.values() if result.proof}
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
            "bound_crt":self.crt_binding.model_dump(mode="json") if self.crt_binding else None,
            "crt_latest_reference":self.latest_crt["evidence_reference"] if self.latest_crt else None,
            "producers":[{"dependency":key,"calculation_id":view.calculation_id,"snapshot":view.snapshot}
                         for key,view in sorted(self.evidence.views.items())],
            "coverage":[lane.at(cutoff) for lane in self.evidence.coverage.values()],
            "temporal":[{"before":key[0],"after":key[1],"relation":key[2],"outcome":value[0],"reason":value[1]}
                        for key,value in sorted(self.temporal.items())],
            "supporting_events":supporting,"entry_intent":self.intent(),
            "resource_counts":{**self.evidence.metrics,"candidate_scans":self.scans,"exact_lookups":self.lookups},
        }
