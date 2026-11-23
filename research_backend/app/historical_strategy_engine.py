"""Bounded historical evaluation with fresh, exact as-of successor proofs."""
import json
from bisect import bisect_right
from datetime import UTC, datetime

from .analysis_evidence import ResourceLimit, canonical
from .db import one
from .historical_strategy_inputs import CHECKPOINT_VERSION, CRT_SMT_METHOD, MAX_CHECKPOINT_BYTES, MAX_ORACLE_CUTOFFS, OPPORTUNITY_ACCOUNTING_VERSION
from .market import FRAME_MIN
from .strategy_evaluation_sources import collect
from .strategy_evaluator import EvaluationMachine, NO, TERMINAL, UNKNOWN
from .strategy_evidence import fingerprint


def normalize_prefix(value):
    """Normalize snapshot coordinates for the analytical benchmark only."""
    if isinstance(value,dict):
        return {key:normalize_prefix(item) for key,item in value.items()
                if key not in {"calculation_snapshot","evaluation_cutoff"}}
    if isinstance(value,list):
        return [normalize_prefix(item) for item in value]
    return value


def publication_prefix(evidence, cutoff):
    return {key:{category:[(normalize_prefix(row),view.records[row["id"]].eligible)
                           for row in events[:bisect_right(stamps,cutoff)]]
                 for category,(stamps,events) in evidence.events[view.snapshot].items() if stamps[0]<=cutoff}
            for key,view in evidence.views.items()}


def asof_selection(selection, cutoff):
    """Retain exact pinned partition attestations that can contribute at cutoff."""
    series = {}
    for key,feed in selection["series"].items():
        step = FRAME_MIN[feed["timeframe"]]*60
        last_open = cutoff-step-feed["delay_seconds"]
        parts = []
        for part in feed["partitions"]:
            row = one("SELECT first_time,last_time,sha256 FROM datasets WHERE id=?",(part["id"],))
            if not row or row["sha256"]!=part["sha256"]:
                raise ValueError("As-of partition attestation changed")
            first,last = (int(datetime.fromisoformat(row[field]).astimezone(UTC).timestamp())
                          for field in ("first_time","last_time"))
            if first<=last_open and last>=feed["start"]:
                parts.append(part)
        if not parts:
            raise ValueError("No partition contributes before the as-of cutoff")
        series[key] = {**feed,"partitions":parts}
    return {**selection,"series":series}


def checkpoint(run, evidence, frontier, attempts, spent, terminal_time, processed, dynamic, accounting=None, observations=()):
    payload = {"version":CHECKPOINT_VERSION,"run_id":run["run_id"],
               "selection":run["identity"]["frozen_selection"],"frontier":frontier,"attempts":attempts,
               "spent":spent,"terminal_time":terminal_time,"processed":processed,"dynamic":sorted(dynamic),
               "snapshots":{key:view.snapshot for key,view in evidence.views.items()}}
    if accounting is not None:
        payload["opportunity_accounting"] = {**accounting,"batch_digest":fingerprint(observations)}
    encoded = canonical(payload)
    if len(encoded.encode())>MAX_CHECKPOINT_BYTES:
        raise ResourceLimit("Historical checkpoint exceeds the byte budget")
    return (encoded,fingerprint(payload),observations) if accounting is not None else (encoded,fingerprint(payload))


def unresolved_reasons(accounting):
    """Older collected checkpoints retained a count without its cause."""
    if "unresolved_by_reason" in accounting:
        return accounting["unresolved_by_reason"]
    count = accounting["unresolved_inspections"]
    return {"NOT_RECORDED":count} if count else {}


def restore(run, evidence, saved):
    data = json.loads(saved["payload"])
    identity = run["identity"]
    if (fingerprint(data)!=saved["digest"] or data["version"]!=CHECKPOINT_VERSION
            or data["run_id"]!=run["run_id"] or data["selection"]!=identity["frozen_selection"]
            or data["frontier"]!=saved["frontier"]
            or not identity["evaluation_start"]<=data["frontier"]<=identity["cutoff"]
            or data["snapshots"]!={key:view.snapshot for key,view in evidence.views.items()}
            or not 0<=data["attempts"]<=identity["max_attempts"]
            or data["attempts"]!=len(set(data["spent"])) or data["attempts"]!=len(data["spent"])
            or bool(data["attempts"])!=(data["terminal_time"] is not None)
            or (data["terminal_time"] is not None and
                not identity["evaluation_start"]<=data["terminal_time"]<=data["frontier"])
            or not 1<=data["processed"]<=MAX_ORACLE_CUTOFFS
            or len(data["dynamic"])>2
            or any(not data["frontier"]<stamp<=identity["cutoff"] for stamp in data["dynamic"])):
        raise ValueError("Historical checkpoint is corrupt or incompatible")
    accounting = data.get("opportunity_accounting")
    if (run.get("opportunity_accounting_version") is not None)!=(accounting is not None):
        raise ValueError("Historical checkpoint has incompatible opportunity accounting")
    if accounting is not None:
        progress = accounting.get("families",{})
        if (accounting.get("version")!=OPPORTUNITY_ACCOUNTING_VERSION
                or not 0<=accounting.get("unresolved_inspections",-1)<=data["processed"]
                or sorted(item[0] for item in progress.values())!=list(range(1,len(progress)+1))
                or any(item[1] not in {0,1,2} for item in progress.values())):
            raise ValueError("Historical checkpoint has invalid opportunity accounting")
        reasons = unresolved_reasons(accounting)
        if (not isinstance(reasons,dict)
                or any(not reason or type(count) is not int or count<1 for reason,count in reasons.items())
                or sum(reasons.values())!=accounting["unresolved_inspections"]):
            raise ValueError("Historical checkpoint has invalid opportunity uncertainty counts")
    return data



def make_machine(run, evidence, definition, after=None, spent=()):
    identity = run["identity"]
    started = identity["evaluation_start"] if after is None else after+1
    return EvaluationMachine(definition,evidence,identity,started,identity["cutoff"],
                             rearm_rule=run["rearm_rule"] if after is not None else None,
                             rearm_after=after,spent_families=spent)


def ledger_entry(run, machine, frontier):
    trace = machine.decision(frontier,0)
    if trace["attempt_id"]:
        state = trace["state"]
        trace["historical_attempt_state"] = (
            state if state in {"TRIGGER_QUALIFIED","INVALIDATED","EXPIRED","INSUFFICIENT_EVIDENCE"}
            else "PENDING_AT_END"
        )
    decision_id = "HD-"+fingerprint({key:value for key,value in trace.items() if key!="resource_counts"} |
                                   {"run_id":run["run_id"]})
    intent = trace["entry_intent"]
    if intent:
        intent.update(historical_run_id=run["run_id"],decision_id=decision_id,
                      evidence_references=[row["evidence_reference"] for _,row in sorted(trace["supporting_events"].items())])
    return {"id":decision_id,"trace":trace,"intent":intent}


def successor_schedule(run, evidence, definition):
    identity = run["identity"]
    end = identity["cutoff"]
    schedule = make_machine(run,evidence,definition).boundaries(end)
    reserve = 0
    if identity["method"]==CRT_SMT_METHOD:
        # One initial reference and at most one new H1 root per subsequent hour.
        # Each attempt can schedule one setup release and one expiry boundary.
        possible = 1+end//3600-identity["evaluation_start"]//3600
        reserve = 2*min(possible,identity["max_attempts"])
    if len(schedule)+reserve>MAX_ORACLE_CUTOFFS:
        raise ResourceLimit(f"Successor window exceeds 160 as-of cutoffs ({len(schedule)} scheduled, {reserve} reserved)")
    return schedule


def account_cutoff(run, machine, cutoff, prior_terminal, accounting, observations, check):
    """Audit the designated rule in isolation from the decision machine's state."""
    rule_id = run["rearm_rule"]
    identity = run["identity"]
    probe = EvaluationMachine(machine.definition,machine.evidence,identity,machine.start,machine.end,check_budget=check)
    candidates = {}
    context = UNKNOWN
    unresolved_reason = "REQUIRED_SOURCE_COVERAGE_UNKNOWN"
    if all(lane.at(cutoff)["status"]=="COMPLETE" for lane in probe.evidence.coverage.values()):
        def observe(choices):
            # Store the earliest exact qualifying observation for each family.
            for proof in sorted(choices,key=lambda item:(item.reference.available_at,item.observed_at,
                                                         item.reference.event_id)):
                if proof.family:
                    candidates.setdefault(proof.family,proof)
        probe.observe_candidates = rule_id,observe
        lower = identity["warmup_start"] if prior_terminal is None else prior_terminal+1
        context = probe.phase("context",cutoff,lower)
        designated = probe.results[rule_id]
        # ALL can be false because of a sibling while the designated rule
        # still cannot enumerate its otherwise eligible families.
        if designated.outcome==UNKNOWN:
            unresolved_reason = designated.reason
        else:
            unresolved_reason = "CONTEXT_OUTCOME_UNKNOWN" if context==UNKNOWN else None

    binding = machine.bindings.get(rule_id) if machine.attempt else None
    bound_family = binding.proof.family if binding else None
    if binding:
        candidates.setdefault(bound_family,binding.proof)
    if unresolved_reason is not None:
        reasons = accounting.setdefault("unresolved_by_reason",unresolved_reasons(accounting))
        reasons[unresolved_reason] = reasons.get(unresolved_reason,0)+1
        accounting["unresolved_inspections"] += 1
    for family,proof in sorted(candidates.items()):
        attempt_id = None
        if family==bound_family:
            rank,disposition,reason = 2,"BOUND","ATTEMPT_BOUND"
            attempt_id = machine.attempt
        elif machine.attempt and machine.state in TERMINAL and proof.reference.available_at==cutoff:
            rank,disposition,reason = 1,"SUPPRESSED","TERMINAL_BOUNDARY"
        elif machine.attempt:
            reason = "SELECTION_POLICY" if machine.context_at==cutoff else "ATTEMPT_IN_PROGRESS"
            rank,disposition = 1,"SUPPRESSED"
        elif context==NO:
            rank,disposition,reason = 1,"SUPPRESSED","CONTEXT_NOT_SATISFIED"
        else:
            rank,disposition,reason = 0,"UNRESOLVED","CONTEXT_DISPOSITION_UNRESOLVED"

        families = accounting["families"]
        prior = families.get(family)
        if prior and prior[1]>=rank:
            continue
        ordinal = prior[0] if prior else len(families)+1
        families[family] = [ordinal,rank]
        reference = proof.reference.model_dump(mode="json",exclude_none=True)
        observations.append({"id":"HO-"+fingerprint([run["run_id"],rule_id,family]),"rule":rule_id,
            "family":family,"cursor":ordinal,"revision":rank,"cutoff":cutoff,"disposition":disposition,
            "reason":reason,"attempt_id":attempt_id,"evidence_reference":reference,
            **({"binding_evidence_reference":binding.proof.reference.model_dump(mode="json",exclude_none=True)}
               if attempt_id else {})})


def run_chunks(run, evidence, definition, publish, *, saved=None, chunk_size=32, check=lambda:None):
    identity = run["identity"]
    start,end = identity["evaluation_start"],identity["cutoff"]
    if identity["method"]=="REFERENCE_SINGLE":
        if saved:
            raise ValueError("Single reference evaluation has no partial checkpoint")
        machine = make_machine(run,evidence,definition)
        machine.check_budget = check
        machine.run(end)
        publish(checkpoint(run,evidence,end,0,[],None,1,()),
                end,[ledger_entry(run,machine,end)],True,evidence.metrics)
        return
    if identity["method"] not in {"FVG_ASOF_REFERENCE",CRT_SMT_METHOD}:
        raise ValueError("This run uses an unsupported historical calculation")

    # Full-window evidence validates coverage and identifies reachable cutoffs.
    # Decisions and persisted proofs always come from a fresh as-of archive.
    schedule = successor_schedule(run,evidence,definition)
    data = restore(run,evidence,saved) if saved else None
    frontier = data["frontier"] if data else start-1
    attempts,spent,terminal_time,processed,dynamic = (
        (data["attempts"],data["spent"],data["terminal_time"],data["processed"],set(data["dynamic"]))
        if data else (0,[],None,0,set()))
    if attempts>=identity["max_attempts"] and frontier<end:
        raise ResourceLimit("Historical attempt budget reached before the final cutoff")
    pending = set(point for point in schedule if point>frontier)
    accounting = None
    if run.get("opportunity_accounting_version")==OPPORTUNITY_ACCOUNTING_VERSION:
        accounting = data["opportunity_accounting"] if data else {
            "version":OPPORTUNITY_ACCOUNTING_VERSION,"families":{},"unresolved_inspections":0}
    observations = []
    batch = []
    while pending or dynamic:
        check()
        frontier = min(pending | dynamic)
        pending.discard(frontier)
        if processed>=MAX_ORACLE_CUTOFFS:
            raise ResourceLimit("Historical oracle cutoff budget exceeded")
        prefix = collect(definition,asof_selection(run["selection"],frontier),frontier,start,check_budget=check)
        machine = make_machine(run,prefix,definition,terminal_time,spent)
        machine.check_budget = check
        machine.run(frontier)
        if accounting is not None:
            account_cutoff(run,machine,frontier,terminal_time,accounting,observations,check)
        if machine.state in TERMINAL and machine.attempt:
            batch.append(ledger_entry(run,machine,frontier))
            binding = machine.bindings[run["rearm_rule"]]
            spent.append(binding.proof.family)
            terminal_time = frontier
            attempts += 1
            dynamic = set()
        else:
            dynamic = set(machine.dynamic_boundaries(frontier,end))
        processed += 1
        done = not (pending or dynamic)
        limit_hit = attempts>=identity["max_attempts"] and not done
        if done and (not batch or batch[-1]["trace"]["cutoff"]!=frontier):
            batch.append(ledger_entry(run,machine,frontier))
        if done or limit_hit or processed%chunk_size==0:
            publish(checkpoint(run,evidence,frontier,attempts,spent,terminal_time,processed,dynamic,
                               accounting,observations),
                    frontier,batch,done,{**evidence.metrics,"processed_cutoffs":processed,
                                         "oracle_prefixes":processed,"spent_context_families":len(spent)})
            batch = []
            observations = []
        if limit_hit:
            raise ResourceLimit("Historical attempt budget reached before the final cutoff")
