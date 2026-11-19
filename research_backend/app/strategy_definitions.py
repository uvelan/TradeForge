"""Schema 1 strategy definitions and pure rule contracts. No market evaluation."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel, Field, StrictBool, StrictFloat, StrictInt, StrictStr, ValidationError, model_validator,
)

from .arrival_policy import RECORDED_MODE, SIMULATION_MODE
from .indicator_coverage import MarketSchedule
from .strategy_catalog import (
    AUTHORITIES, BINDING_POLICY_VERSION, FAMILY_CATEGORIES, OBSERVATIONS, REFERENCE_VERSION, REQUIRES, ROLE_OBSERVATIONS, VERSIONS, effective_settings,
)
from .strategy_evidence import fingerprint


Purpose = Literal["HISTORICAL_SIMULATION", "STRICT_RECORDED"]
Source = Literal["DUKASCOPY_SAVED", "MT5_SAVED"]
Basis = Literal["BID", "ASK", "LAST_TRADE"]
Frame = Literal["M1", "M5"]
Indicator = Literal["CRT", "SMT", "STRUCTURE", "LIQUIDITY", "FVG", "ORDER_BLOCK"]
Role = Literal["PRIMARY", "COMPARISON", "PAIR"]
StateRequirement = Literal["HISTORICAL", "CURRENT"]
Key = Annotated[StrictStr, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")]
Digest = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
Number = Annotated[StrictFloat, Field(allow_inf_nan=False)]
Positive = Annotated[Number, Field(gt=0)]
Percent = Annotated[Positive, Field(le=100)]
Payout = Annotated[Positive, Field(le=1000)]
Cost = Annotated[Number, Field(ge=0, le=10000)]
Seconds = Annotated[StrictInt, Field(ge=1, le=86400)]
Delay = Annotated[StrictInt, Field(ge=0, le=86400)]
Scalar = StrictStr | StrictInt | Number | StrictBool | None
PHASES = ("context", "setup", "trigger", "invalidation")
MAX_NODES = 96
MAX_DEPTH = 6


class Contract(BaseModel, extra="forbid"):
    pass


class InstrumentBinding(Contract):
    symbol: Literal["EURUSD", "GBPUSD"]
    source: Source
    price_basis: Basis
    modeled_delay_seconds: Delay


class StrategyCalendar(MarketSchedule, extra="forbid"):
    pass


class InstrumentBindings(Contract):
    primary: InstrumentBinding
    comparison: InstrumentBinding | None = None
    calendar: StrategyCalendar
    data_selection: Literal["FROZEN_VERIFIED_PARTITIONS"] = "FROZEN_VERIFIED_PARTITIONS"


class TimeframeBinding(Contract):
    context: Frame
    trigger: Frame


class IndicatorDependency(Contract):
    id: Key
    indicator: Indicator
    algorithm_version: StrictInt
    role: Role
    timeframe: Frame
    settings: dict[Key, Scalar] = Field(default_factory=dict, max_length=24)
    producers: list[Key] = Field(default_factory=list, max_length=4)


class EvidenceRequirement(Contract):
    state: StateRequirement
    purpose: Purpose
    max_age_seconds: Seconds


class StrategyRule(Contract):
    type: Literal["RULE"]
    id: Key
    dependency: Key
    observation: Key
    operator: Literal["EQ", "LT", "LTE", "GT", "GTE"]
    expected: StrictStr | StrictInt | Number
    evidence: EvidenceRequirement


class TimeBound(Contract):
    unit: Literal["SECONDS", "ELIGIBLE_BARS"]
    count: Annotated[StrictInt, Field(ge=1, le=86400)]
    timeframe: Frame | None = None

    @model_validator(mode="after")
    def require_grid(self):
        if self.unit == "ELIGIBLE_BARS":
            if self.timeframe is None or self.count > 500:
                raise ValueError("Eligible bars require a timeframe and a count of at most 500")
        elif self.timeframe is not None:
            raise ValueError("Elapsed seconds do not have a timeframe")
        return self


class ConditionGroup(Contract):
    type: Literal["GROUP"]
    id: Key
    operator: Literal["ALL", "ANY", "NOT", "SEQUENCE"]
    children: list[Annotated[StrategyRule | ConditionGroup, Field(discriminator="type")]] = Field(
        min_length=1, max_length=24)
    unknown_policy: Literal["PROPAGATE"] | None = None
    within: TimeBound | None = None


class TemporalLink(Contract):
    first: Key
    then: Key
    relation: Literal["AFTER", "BEFORE", "WITHIN_AFTER"]
    within: TimeBound | None = None


class Exposure(Contract):
    role: Literal["PRIMARY", "COMPARISON"]
    max_risk_percent: Percent


class RiskPolicy(Contract):
    account_risk_pct: Annotated[Positive, Field(le=2)] | None = None
    max_simultaneous_exposure_pct: Percent | None = None
    max_session_loss_pct: Percent | None = None
    min_reward_risk: Payout | None = None
    instrument_limits: list[Exposure] = Field(default_factory=list, max_length=2)


class ExecutionCosts(Contract):
    price_basis: Basis | None = None
    spread_bps: Cost | None = None
    slippage_bps: Cost | None = None
    commission_bps: Cost | None = None
    intrabar: Literal["REJECT_AMBIGUOUS", "ADVERSE_FIRST"] | None = None


class ExecutionPolicy(ExecutionCosts):
    entry_intent: Literal["MARKET", "LIMIT", "STOP"] | None = None
    earliest: Literal["AFTER_CAUSAL_AVAILABILITY"] = "AFTER_CAUSAL_AVAILABILITY"
    expiry: TimeBound | None = None


class Stop(Contract):
    kind: Literal["FIXED_PIPS"]
    distance: Payout


class Target(Contract):
    kind: Literal["REWARD_MULTIPLE"]
    multiple: Payout


class PartialExit(Contract):
    at_r: Payout
    percent: Annotated[Positive, Field(lt=100)]


class ManagementPolicy(Contract):
    initial_stop: Stop | None = None
    target: Target | None = None
    break_even_at_r: Payout | None = None
    partial_exit: PartialExit | None = None
    trailing_pips: Payout | None = None
    time_exit: TimeBound | None = None


class StrategyDefinition(Contract):
    schema_version: Literal[1]
    defaults_policy: Literal["STRATEGY_DEFAULTS_V1"]
    name: Annotated[StrictStr, Field(min_length=1, max_length=100)]
    description: Annotated[StrictStr, Field(max_length=2000)] = ""
    status: Literal["DRAFT", "READY_FOR_EVALUATION"] = "DRAFT"
    availability_modes: Annotated[list[Purpose], Field(min_length=1, max_length=1)]
    instruments: InstrumentBindings
    timeframes: TimeframeBinding
    indicators: list[IndicatorDependency] = Field(max_length=24)
    context: ConditionGroup | None = None
    setup: ConditionGroup | None = None
    trigger: ConditionGroup | None = None
    invalidation: ConditionGroup | None = None
    temporal: list[TemporalLink] = Field(default_factory=list, max_length=96)
    expiry: TimeBound | None = None
    risk: RiskPolicy = Field(default_factory=RiskPolicy)
    execution: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    management: ManagementPolicy = Field(default_factory=ManagementPolicy)


ConditionGroup.model_rebuild()


TradedRole = Literal["PRIMARY", "COMPARISON"]
Selection = Literal["EARLIEST_ELIGIBLE", "LATEST_ELIGIBLE", "BOUND_FAMILY"]


class BoundRule(StrategyRule):
    selection: Selection | None = None
    alias: Key | None = None
    same_family_as: Key | None = None


class BoundGroup(ConditionGroup):
    children: list[Annotated[BoundRule | BoundGroup, Field(discriminator="type")]] = Field(
        min_length=1, max_length=24)


BoundGroup.model_rebuild()


class FixedPrice(Contract):
    kind: Literal["FIXED_ABSOLUTE"]
    value: Annotated[Positive, Field(le=1000000)]
    basis: Basis


class MarketEntry(Contract):
    kind: Literal["MARKET"]


class LimitEntry(Contract):
    kind: Literal["LIMIT"]
    price: FixedPrice | None = None


class StopEntry(Contract):
    kind: Literal["STOP"]
    price: FixedPrice | None = None


class TradeIntent(Contract):
    role: TradedRole | None = None
    direction: Literal["LONG", "SHORT"] | None = None
    entry: Annotated[MarketEntry | LimitEntry | StopEntry, Field(discriminator="kind")] | None = None
    earliest: Literal["AFTER_CAUSAL_AVAILABILITY"] | None = None
    expiry: TimeBound | None = None


class AttemptPolicy(Contract):
    context_mode: Literal["LATCHED", "CONTINUOUS"]
    candidate_limit: StrictInt = Field(ge=1, le=128)
    expiry_origin: Literal["CONTEXT_ACTIVATION", "SETUP_ACTIVATION"]
    crt_reference_alias: Key | None = None


class StrategyDefinitionV2(StrategyDefinition):
    schema_version: Literal[2]
    defaults_policy: Literal["STRATEGY_DEFAULTS_V2"]
    context: BoundGroup | None = None
    setup: BoundGroup | None = None
    trigger: BoundGroup | None = None
    invalidation: BoundGroup | None = None
    trade_intent: TradeIntent = Field(default_factory=TradeIntent)
    execution: ExecutionCosts = Field(default_factory=ExecutionCosts)
    attempt: AttemptPolicy | None = None


class Outcome(StrEnum):
    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    UNKNOWN = "UNKNOWN"


def logical_outcome(operator, results):
    """Combine already qualified outcomes. Empty or missing evidence is unknown."""
    results = tuple(results)
    if not results:
        return Outcome.UNKNOWN
    if operator == "NOT" and len(results) == 1:
        return {Outcome.SATISFIED:Outcome.NOT_SATISFIED,
                Outcome.NOT_SATISFIED:Outcome.SATISFIED, Outcome.UNKNOWN:Outcome.UNKNOWN}[results[0]]
    if operator == "ALL":
        return (Outcome.NOT_SATISFIED if Outcome.NOT_SATISFIED in results else
                Outcome.UNKNOWN if Outcome.UNKNOWN in results else Outcome.SATISFIED)
    if operator == "ANY":
        return (Outcome.SATISFIED if Outcome.SATISFIED in results else
                Outcome.UNKNOWN if Outcome.UNKNOWN in results else Outcome.NOT_SATISFIED)
    raise ValueError("Invalid logical operator or arity")


def guarded_outcome(mandatory_gate: Outcome, optional_outcome: Outcome):
    return optional_outcome if mandatory_gate == Outcome.SATISFIED else mandatory_gate


def causal_order(first, second, cutoff, *, elapsed_limit=None):
    """Order already validated causal availability timestamps, with an inclusive bound."""
    if first is None or second is None or max(first, second) > cutoff:
        return Outcome.UNKNOWN
    elapsed = second-first
    return (Outcome.SATISFIED if elapsed>0 and (elapsed_limit is None or elapsed<=elapsed_limit)
            else Outcome.NOT_SATISFIED)


DecisionState = Literal["WAITING_FOR_CONTEXT","WAITING_FOR_SETUP","SETUP_OBSERVED","WAITING_FOR_TRIGGER",
                        "TRIGGER_QUALIFIED","INVALIDATED","EXPIRED","NO_TRADE","INSUFFICIENT_EVIDENCE"]


class StrategyEvaluationReference(Contract):
    strategy_id: Annotated[str, Field(pattern=r"^ST-[a-f0-9]{32}$")]
    strategy_version_id: Annotated[str, Field(pattern=r"^SV-[a-f0-9]{32}$")]
    configuration_fingerprint: Digest
    purpose: Purpose
    cutoff_utc: Annotated[StrictInt, Field(ge=0)]
    frozen_selection_fingerprint: Digest


class ExactEvidenceReference(Contract):
    version: Literal[REFERENCE_VERSION]
    category: Annotated[str, Field(min_length=1,max_length=50)]
    event_id: Annotated[str, Field(min_length=1,max_length=160)]
    calculation_id: Digest
    calculation_snapshot: Digest
    configuration: Digest
    source: Annotated[list[str | None],Field(min_length=6,max_length=6)]
    available_at: Annotated[StrictInt,Field(ge=0)]
    source_authority: str
    availability_mode: Purpose
    participants: dict[Literal["primary","comparison"],Annotated[list[str | None],Field(min_length=6,max_length=6)]] | None = None


class ConditionTrace(Contract):
    id: Key
    outcome: Outcome
    evidence: list[ExactEvidenceReference] = Field(max_length=24)
    reasons: list[Annotated[str, Field(max_length=120)]] = Field(max_length=20)


class StrategyDecisionTrace(Contract):
    evaluation: StrategyEvaluationReference
    state: DecisionState
    conditions: list[ConditionTrace] = Field(max_length=MAX_NODES)
    missing_evidence: list[Key] = Field(max_length=MAX_NODES)
    rejection_reasons: list[Annotated[str,Field(max_length=120)]] = Field(max_length=24)
    invalidation: Outcome
    expiry: Outcome


@dataclass(slots=True)
class Audit:
    errors: list[dict]
    rules: dict
    ids: set
    edges: dict
    nodes: int = 0
    mandatory: dict = field(default_factory=dict)
    phases: dict = field(default_factory=dict)

    def error(self,path,code,expected,actual=None,condition_id=None):
        if len(self.errors) < 100:
            self.errors.append({"path":path,"condition_id":condition_id,"code":code,
                                "expected":expected,"actual":actual})

    def edge(self, first, second):
        self.edges.setdefault(first,set()).add(second)


def input_at(payload,loc):
    for part in loc:
        if part in ("RULE","GROUP") and isinstance(payload,dict) and part not in payload:
            continue
        if isinstance(payload,dict):
            payload = payload.get(part)
        elif isinstance(payload,list) and type(part) is int and 0<=part<len(payload):
            payload = payload[part]
        else:
            return None
    return payload


def shape_errors(payload,error):
    items = []
    for item in error.errors(include_url=False,include_context=False,include_input=False)[:100]:
        loc = item["loc"]
        owner = next((node for size in range(len(loc),0,-1)
                      if isinstance(node := input_at(payload,loc[:size]),dict) and "id" in node),None)
        value = input_at(payload,loc)
        items.append({"path":".".join(str(key) for key in loc),"condition_id":owner.get("id") if owner else None,
                      "code":"SCHEMA_"+item["type"].upper(),"expected":item["msg"],
                      "actual":value if isinstance(value,(str,int,float,bool)) else None})
    return items


def inspect_node(audit, node, path, depth, deps, purpose, frames, mandatory=True):
    audit.nodes += 1
    if depth > MAX_DEPTH or audit.nodes > MAX_NODES:
        audit.error(path,"RULE_COMPLEXITY",f"At most {MAX_NODES} nodes and depth {MAX_DEPTH}",condition_id=node.id)
        return
    if node.id in audit.ids:
        audit.error(path+".id","DUPLICATE_ID","Unique condition/group ID",node.id,node.id)
    audit.ids.add(node.id)
    if isinstance(node,StrategyRule):
        audit.rules[node.id] = node
        audit.phases[node.id] = path.split(".")[0]
        if mandatory:
            audit.mandatory[node.id] = audit.phases[node.id]
        dep = deps.get(node.dependency)
        op = OBSERVATIONS.get(node.observation)
        if isinstance(node, BoundRule) and op is None:
            op = ROLE_OBSERVATIONS.get(node.observation)
        if not dep:
            audit.error(path+".dependency","MISSING_DEPENDENCY","Declared indicator dependency",node.dependency,node.id)
        if not op or dep and op["indicator"] != dep.indicator:
            audit.error(path+".observation","UNSUPPORTED_OBSERVATION","Canonical observation for the dependency",node.observation,node.id)
        else:
            if node.operator not in op["operators"]:
                audit.error(path+".operator","UNSUPPORTED_OPERATOR",op["operators"],node.operator,node.id)
            correct = (node.expected in op["values"] if op["values"] else
                       type(node.expected) in (float,int) and 0 < node.expected <= 1000000)
            if not correct:
                audit.error(path+".expected","INVALID_EXPECTED_VALUE",op["values"] or "Positive finite price",node.expected,node.id)
            if node.evidence.state not in op["state_requirements"]:
                audit.error(path+".evidence.state","UNSUPPORTED_EVIDENCE_STATE",op["state_requirements"],node.evidence.state,node.id)
        if node.evidence.purpose != purpose:
            audit.error(path+".evidence.purpose","PURPOSE_MISMATCH",purpose,node.evidence.purpose,node.id)
        return
    if node.operator == "NOT":
        if len(node.children)!=1 or node.unknown_policy!="PROPAGATE":
            audit.error(path,"INVALID_NEGATION","One child and unknown_policy PROPAGATE",condition_id=node.id)
    elif node.unknown_policy is not None:
        audit.error(path+".unknown_policy","INVALID_UNKNOWN_POLICY","Only NOT declares an unknown policy",condition_id=node.id)
    if node.within and (node.operator!="SEQUENCE" or node.within.timeframe and node.within.timeframe not in frames):
        audit.error(path+".within","INVALID_SEQUENCE_BOUND","Use a sequence and a declared observation timeframe",condition_id=node.id)
    if node.operator == "SEQUENCE":
        if len(node.children)<2 or any(not isinstance(child,StrategyRule) for child in node.children):
            audit.error(path,"INVALID_SEQUENCE","At least two event conditions, in order",condition_id=node.id)
        else:
            for first,second in zip(node.children,node.children[1:]):
                audit.edge(first.id,second.id)
    for index,child in enumerate(node.children):
        inspect_node(audit,child,f"{path}.children.{index}",depth+1,deps,purpose,frames,
                     mandatory and node.operator not in {"NOT","ANY"})


def cycle(edges):
    incoming = {node:0 for node in edges}
    for children in edges.values():
        for child in children:
            incoming[child] = incoming.get(child,0)+1
    ready = [node for node,count in incoming.items() if count==0]
    processed = 0
    while ready:
        node = ready.pop()
        processed += 1
        for child in edges.get(node,()):
            incoming[child] -= 1
            if incoming[child]==0:
                ready.append(child)
    return processed != len(incoming)


def semantic_group(group):
    if group is None:
        return None
    if group["type"] == "RULE":
        if (OBSERVATIONS.get(group["observation"]) or ROLE_OBSERVATIONS[group["observation"]])["value_type"] == "number":
            return {**group,"expected":float(group["expected"])}
        return group
    children = [semantic_group(child) for child in group["children"]]
    if group["operator"] in {"ALL","ANY"}:
        children.sort(key=lambda item:item["id"])
    return {**group,"children":children}


def phase_allows_precedence(origin, target):
    return PHASES.index(origin) <= PHASES.index(target) and (origin,target)!=("trigger","invalidation")


def validate_attempt(definition, audit, unresolved, dependencies):
    trade = definition.trade_intent
    for name in ("role", "direction", "entry", "earliest", "expiry"):
        if getattr(trade, name) is None:
            unresolved.append("trade_intent." + name)
    traded = (definition.instruments.primary if trade.role == "PRIMARY" else
              definition.instruments.comparison if trade.role == "COMPARISON" else None)
    if trade.role and traded is None:
        audit.error("trade_intent.role", "UNBOUND_TRADED_ROLE", "An explicitly declared instrument", trade.role)
    basis = definition.execution.price_basis
    if traded and basis and traded.price_basis != basis:
        audit.error("execution.price_basis", "INCOMPATIBLE_PRICE_BASIS", traded.price_basis, basis)
    if isinstance(trade.entry, (LimitEntry, StopEntry)):
        price = trade.entry.price
        if price is None:
            unresolved.append("trade_intent.entry.price")
        elif (traded and price.basis != traded.price_basis) or (basis and price.basis != basis):
            audit.error("trade_intent.entry.price.basis", "INCOMPATIBLE_PRICE_BASIS",
                        traded.price_basis if traded else basis, price.basis)

    intended = definition.risk.account_risk_pct
    instrument_cap = next((limit.max_risk_percent for limit in definition.risk.instrument_limits
                           if limit.role == trade.role), None)
    for path, limit in (("risk.instrument_limits", instrument_cap),
                        ("risk.max_session_loss_pct", definition.risk.max_session_loss_pct)):
        if intended is not None and limit is not None and intended > limit:
            audit.error(path, "RISK_CAP_BELOW_INTENT", "At least the intended per-trade risk", limit)
    management = definition.management
    if management.target:
        target = management.target.multiple
        for name, activation in (("break_even_at_r", management.break_even_at_r),
                                 ("partial_exit.at_r", management.partial_exit.at_r if management.partial_exit else None)):
            if activation is not None and activation >= target:
                audit.error("management." + name, "MANAGEMENT_AFTER_TARGET", "Strictly below the target", activation)
        partial, minimum = management.partial_exit, definition.risk.min_reward_risk
        if partial and minimum and partial.at_r < target:
            share = Decimal(str(partial.percent))/100
            gross = share*Decimal(str(partial.at_r)) + (1-share)*Decimal(str(target))
            if gross < Decimal(str(minimum)):
                audit.error("management.partial_exit", "PARTIAL_REWARD_BELOW_MINIMUM",
                            "Configured gross reward/risk at least the minimum",float(gross))

    attempt = definition.attempt
    if attempt is None:
        unresolved.append("attempt")
    aliases = {}
    for id_, rule in audit.rules.items():
        if rule.selection is None:
            unresolved.append(f"conditions.{id_}.selection")
        if (rule.selection == "BOUND_FAMILY") != (rule.same_family_as is not None):
            audit.error("conditions." + id_, "INVALID_FAMILY_SELECTION",
                        "BOUND_FAMILY and a same-family alias must be declared together", condition_id=id_)
        if (rule.alias or rule.same_family_as) and id_ not in audit.mandatory:
            audit.error("conditions." + id_, "AMBIGUOUS_BINDING_BRANCH",
                        "Evidence binding requires a guaranteed positive rule", condition_id=id_)
        if rule.alias:
            if rule.alias in aliases:
                audit.error("conditions." + id_, "DUPLICATE_ALIAS", "Unique evidence alias", rule.alias, id_)
            aliases[rule.alias] = rule

    reference = aliases.get(attempt.crt_reference_alias) if attempt else None
    if attempt and attempt.crt_reference_alias and (
            reference is None or audit.mandatory.get(reference.id) != "context" or
            reference.observation != "CRT_REFERENCE" or reference.evidence.state != "HISTORICAL" or
            reference.same_family_as is not None):
        audit.error("attempt.crt_reference_alias", "INVALID_CRT_ACTIVATION",
                    "An independent positive historical ACTIVE CRT reference alias in context", attempt.crt_reference_alias)
    if attempt and attempt.context_mode=="LATCHED":
        for id_, rule in audit.rules.items():
            if audit.phases.get(id_)=="context" and rule.observation=="CRT_REFERENCE" and rule is not reference:
                audit.error("conditions." + id_, "UNTRACKED_LATCHED_CRT_REFERENCE",
                            "Track the latched reference family explicitly", condition_id=id_)
    for id_, rule in audit.rules.items():
        parent = aliases.get(rule.same_family_as)
        if rule.same_family_as:
            child_spec = OBSERVATIONS.get(rule.observation) or ROLE_OBSERVATIONS.get(rule.observation, {})
            parent_spec = ((OBSERVATIONS.get(parent.observation) or ROLE_OBSERVATIONS.get(parent.observation, {}))
                           if parent else {})
            suitable = (parent and parent.id in audit.mandatory and id_ in audit.mandatory and
                        phase_allows_precedence(audit.mandatory[parent.id],audit.mandatory[id_]) and
                        parent.dependency == rule.dependency and
                        parent_spec.get("category") == child_spec.get("category") in FAMILY_CATEGORIES)
            if not suitable:
                audit.error("conditions." + id_, "INCOMPATIBLE_FAMILY_ALIAS",
                            "An earlier compatible alias from the same exact dependency and family category",
                            rule.same_family_as,id_)
            else:
                audit.edge(parent.id,id_)
        if rule.observation == "CRT_RECLAIM" and (reference is None or rule.same_family_as != reference.alias or
                reference.dependency != rule.dependency):
            audit.error("conditions." + id_, "CRT_RECLAIM_REQUIRES_REFERENCE",
                        "Bind reclaim to the selected CRT reference alias",rule.same_family_as,id_)
        dependency = dependencies.get(rule.dependency)
        if (reference and rule is not reference and dependency and dependency.indicator=="CRT" and
                rule.observation!="CRT_RECLAIM" and
                (rule.dependency!=reference.dependency or rule.same_family_as!=reference.alias)):
            phase = audit.phases[id_]
            code = ("CRT_INVALIDATION_REQUIRES_REFERENCE" if phase=="invalidation" else
                    "CRT_LIFECYCLE_REQUIRES_REFERENCE")
            audit.error(f"conditions.{id_}.same_family_as",code,
                        f"Use BOUND_FAMILY with tracked CRT alias {reference.alias}",rule.same_family_as,id_)
        if rule.observation in ROLE_OBSERVATIONS and (
                rule.selection!="BOUND_FAMILY" or parent is None or
                parent.observation!=ROLE_OBSERVATIONS[rule.observation]["requires_bound_observation"] or
                parent.dependency!=rule.dependency):
            audit.error(f"conditions.{id_}.same_family_as","SMT_ROLE_REQUIRES_DIVERGENCE",
                        "Use BOUND_FAMILY with a prior SMT_CONFIRMED alias from this dependency",
                        rule.same_family_as,id_)


def validate_definition(payload):
    if isinstance(payload,dict) and type(payload.get("schema_version")) is not int:
        return {"valid":False,"status":"INVALID_STRATEGY_DEFINITION","unresolved":[],
                "errors":[{"path":"schema_version","condition_id":None,"code":"UNSUPPORTED_SCHEMA_VERSION",
                           "expected":"Integer schema version 1","actual":None}]}
    try:
        schema = StrategyDefinitionV2 if isinstance(payload,dict) and payload.get("schema_version")==2 else StrategyDefinition
        definition = schema.model_validate(payload)
    except ValidationError as error:
        return {"valid":False,"status":"INVALID_STRATEGY_DEFINITION","errors":shape_errors(payload,error),"unresolved":[]}
    v2 = isinstance(definition, StrategyDefinitionV2)
    audit = Audit([],{},set(),{})
    purpose = definition.availability_modes[0]
    bindings = definition.instruments
    primary, peer = bindings.primary, bindings.comparison
    frames = {definition.timeframes.context,definition.timeframes.trigger}
    if (not definition.name.strip() or bindings.calendar.kind == "unknown"):
        audit.error("name" if not definition.name.strip() else "instruments.calendar","INVALID_SCOPE",
                    "A nonblank name and an explicit market calendar")
    for role,binding in (("primary",primary),("comparison",peer)):
        if binding is None:
            continue
        path = "instruments."+role
        if purpose == RECORDED_MODE and (binding.source!="MT5_SAVED" or binding.modeled_delay_seconds):
            audit.error(path,"RECORDED_SCOPE","MT5 saved source and zero modeled delay")
        if binding.source=="DUKASCOPY_SAVED" and binding.price_basis=="LAST_TRADE":
            audit.error(path+".price_basis","UNSUPPORTED_BASIS","BID or ASK",binding.price_basis)
        if binding.source=="MT5_SAVED" and binding.price_basis=="ASK":
            audit.error(path+".price_basis","UNSUPPORTED_BASIS","BID or LAST_TRADE",binding.price_basis)
    if peer and (peer.symbol==primary.symbol or (peer.source,peer.price_basis)!=(primary.source,primary.price_basis)):
        audit.error("instruments.comparison","INCOMPATIBLE_PAIR","Distinct approved symbols with matching source and basis")
    catalog = {dep.id:dep for dep in definition.indicators}
    if len(catalog)!=len(definition.indicators):
        audit.error("indicators","DUPLICATE_ID","Unique dependency IDs")
    settings = {}
    producer_edges = {}
    for i,dep in enumerate(definition.indicators):
        path = f"indicators.{i}"
        if dep.algorithm_version != VERSIONS[dep.indicator][0]:
            audit.error(path+".algorithm_version","UNSUPPORTED_ALGORITHM",VERSIONS[dep.indicator][0],dep.algorithm_version)
        if dep.timeframe not in frames or dep.indicator=="CRT" and dep.timeframe!="M5":
            audit.error(path+".timeframe","UNSUPPORTED_TIMEFRAME","Declared frame, and M5 for CRT",dep.timeframe)
        if ((dep.indicator=="SMT") != (dep.role=="PAIR")
                or dep.role in {"PAIR","COMPARISON"} and peer is None):
            audit.error(path+".role","INVALID_INDICATOR_BINDING","SMT uses the approved pair. Other producers bind a declared instrument",dep.role)
        expected = REQUIRES.get(dep.indicator,())
        parents = [catalog.get(key) for key in dep.producers]
        if (len(parents)!=len(expected) or any(parent is None for parent in parents) or
                any((parent.indicator,parent.role,parent.timeframe)!=(kind,dep.role,dep.timeframe)
                    for parent,kind in zip(parents,expected))):
            audit.error(path+".producers","INVALID_PRODUCER_BINDING",
                        f"Exact same-role, same-frame dependencies: {', '.join(expected) or 'none'}",dep.producers)
        producer_edges[dep.id] = set(dep.producers)
        try:
            settings[dep.id] = effective_settings(dep.indicator,dep.settings,bindings.calendar.model_dump())
        except ValidationError as error:
            for entry in error.errors(include_input=False,include_context=False,include_url=False):
                field = ".".join(str(part) for part in entry["loc"])
                audit.error(path+".settings."+field,"INVALID_INDICATOR_PARAMETER",entry["msg"],dep.settings.get(field))
        except ValueError as error:
            audit.error(path+".settings","UNSUPPORTED_INDICATOR_PARAMETER",str(error),dep.settings)
    if cycle(producer_edges):
        audit.error("indicators","CYCLIC_PRODUCERS","Acyclic producer dependency graph")
    for phase in PHASES:
        group = getattr(definition,phase)
        if group:
            inspect_node(audit,group,phase,1,catalog,purpose,frames)
    relationships = set()
    for i,link in enumerate(definition.temporal):
        path = f"temporal.{i}"
        signature = (link.first,link.then,link.relation)
        if (link.first not in audit.rules or link.then not in audit.rules or link.first==link.then
                or v2 and (link.first not in audit.mandatory or link.then not in audit.mandatory)
                or signature in relationships or (link.relation=="WITHIN_AFTER") != (link.within is not None)
                or link.within and link.within.timeframe and link.within.timeframe not in frames):
            audit.error(path,"INVALID_TEMPORAL_LINK","Distinct event IDs, unique relation and a declared bound where required")
            continue
        relationships.add(signature)
        earlier,later = (link.then,link.first) if link.relation=="BEFORE" else (link.first,link.then)
        audit.edge(earlier,later)
        if v2:
            origin,target = audit.phases[earlier],audit.phases[later]
            if not phase_allows_precedence(origin,target):
                audit.error(path,"UNSUPPORTED_PHASE_ORDER",
                            "Use context before setup, and context or setup before trigger or invalidation",
                            {"earlier": origin, "later": target})
    policies = {"risk":("account_risk_pct","max_simultaneous_exposure_pct","max_session_loss_pct","min_reward_risk"),
                "execution":(("price_basis","spread_bps","slippage_bps","commission_bps","intrabar") if v2 else
                             ("entry_intent","price_basis","spread_bps","slippage_bps","commission_bps","expiry","intrabar")),
                "management":("initial_stop","target")}
    unresolved = [name for name in (*PHASES,"expiry") if getattr(definition,name) is None]
    unresolved += [f"{group}.{field}" for group,fields in policies.items() for field in fields
                   if getattr(getattr(definition,group),field) is None]
    limits = definition.risk.instrument_limits
    limit_roles = {item.role for item in limits}
    allowed_roles = {"PRIMARY","COMPARISON"} if peer else {"PRIMARY"}
    required_roles = ({definition.trade_intent.role} & allowed_roles) if v2 else allowed_roles
    unresolved += [f"risk.instrument_limits.{role}" for role in sorted(required_roles-limit_roles)]
    if len(limit_roles)!=len(limits) or limit_roles-allowed_roles:
        audit.error("risk.instrument_limits","INVALID_EXPOSURE","Unique declared instrument limits")
    if (definition.risk.account_risk_pct and definition.risk.max_simultaneous_exposure_pct and
            definition.risk.account_risk_pct>definition.risk.max_simultaneous_exposure_pct):
        audit.error("risk.max_simultaneous_exposure_pct","INVALID_EXPOSURE","At least the risk per trade")
    if (definition.management.target and definition.risk.min_reward_risk
            and definition.management.target.multiple < definition.risk.min_reward_risk):
        audit.error("management.target.multiple","TARGET_BELOW_MINIMUM","At least the minimum reward/risk",
                    definition.management.target.multiple)
    if v2:
        validate_attempt(definition, audit, unresolved, catalog)
    elif definition.execution.price_basis and definition.execution.price_basis!=primary.price_basis:
        audit.error("execution.price_basis","INCOMPATIBLE_PRICE_BASIS",primary.price_basis,definition.execution.price_basis)
    if cycle(audit.edges):
        audit.error("temporal","CYCLIC_RULE_GRAPH","Acyclic availability and binding precedence")
    order_expiry = (("trade_intent.expiry",definition.trade_intent.expiry) if v2 else
                    ("execution.expiry",definition.execution.expiry))
    for group,bound in (("expiry",definition.expiry),order_expiry,
                        ("management.time_exit",definition.management.time_exit)):
        if bound and bound.timeframe and bound.timeframe not in frames:
            audit.error(group,"UNBOUND_EXPIRY_TIMEFRAME","Declared strategy timeframe",bound.timeframe)
    if definition.status=="READY_FOR_EVALUATION":
        for name in unresolved:
            audit.error(name,"UNRESOLVED_REQUIRED","Configure before marking ready")
    result = {"valid":not audit.errors,
              "status":"INVALID_STRATEGY_DEFINITION" if audit.errors else "VALID_STRATEGY_DEFINITION",
              "errors":audit.errors,"unresolved":unresolved,
              "restrictions":{"purpose":purpose,"source_authority":AUTHORITIES[purpose],
                              "recorded_operational":False,"evaluation_available":False,
                              "requires_schema_revision":not v2,
                              **({"binding_policy_version":BINDING_POLICY_VERSION} if v2 else {})}}
    if not audit.errors:
        normalized = definition.model_dump(mode="json")
        for dep in normalized["indicators"]:
            dep["settings"] = settings[dep["id"]]
        semantic = {key:value for key,value in normalized.items() if key not in {"name","description","status"}}
        semantic.update({phase:semantic_group(semantic[phase]) for phase in PHASES})
        semantic["risk"] = {**semantic["risk"],"instrument_limits":
                            sorted(semantic["risk"]["instrument_limits"],key=lambda item:item["role"])}
        semantic["indicators"] = sorted(semantic["indicators"],key=lambda item:item["id"])
        semantic["temporal"] = sorted(semantic["temporal"],key=lambda item:(item["first"],item["then"],item["relation"]))
        result.update(definition=normalized,configuration_fingerprint=fingerprint(semantic))
    return result
