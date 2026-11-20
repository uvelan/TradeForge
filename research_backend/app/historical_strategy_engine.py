"""Chronological orchestration of the authoritative mechanical evaluator."""
import json
from bisect import bisect_right
from heapq import heapify, heappop, heappush

from .analysis_evidence import ResourceLimit, canonical
from .historical_strategy_inputs import CHECKPOINT_VERSION, MAX_CHECKPOINT_BYTES, MAX_ORACLE_CUTOFFS
from .strategy_binding import AttemptBinding, BoundEvidence
from .strategy_definitions import Outcome
from .strategy_evaluation_sources import MAX_TIMELINE, collect
from .strategy_evaluator import EvaluationMachine, RuleResult, TERMINAL
from .strategy_evidence import fingerprint

STATE_FIELDS = ("context_at","setup_at","trigger_at","attempt","state","reason","timeline","phase_results",
                "phase_times","binding_times","binding_status","scans","lookups")
PROOF_FIELDS = ("results","latches","bindings","activation_proofs")


def normalize_prefix(value):
    """Drop proof coordinates that encode the chosen calculation snapshot."""
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


def uncertainty_prefix(evidence, cutoff):
    return {key:{family:[(edge.start,edge.reason,
                         edge.recovered_at if edge.recovered_at is not None and edge.recovered_at<=cutoff else None)
                        for edge in entries if edge.start<=cutoff]
                 for family,entries in view.smt_uncertainty.items() if entries[0].start<=cutoff}
            for key,view in evidence.views.items()}


def verify_prefix(definition, run, evidence, cutoff, check):
    earlier = collect(definition,run["selection"],cutoff,run["identity"]["evaluation_start"],check_budget=check)
    if (publication_prefix(earlier,cutoff)!=publication_prefix(evidence,cutoff)
            or uncertainty_prefix(earlier,cutoff)!=uncertainty_prefix(evidence,cutoff)
            or any(lane.at(cutoff)!=earlier.coverage[key].at(cutoff) for key,lane in evidence.coverage.items())):
        raise ValueError("Canonical producer prefix diverged from an independent calculation")
    return earlier.metrics


def checkpoint(run, evidence, machine, frontier, attempts, spent, terminal_time, processed):
    fields = {key:getattr(machine,key) for key in STATE_FIELDS}

    def encode(result):
        return [result.outcome.value,result.reason,
                result.proof.model_dump(mode="json") if result.proof else None,
                result.selected_at,result.verified_at]

    payload = {"version":CHECKPOINT_VERSION,"run_id":run["run_id"],
               "selection":run["identity"]["frozen_selection"],"frontier":frontier,"attempts":attempts,
               "spent":spent,"terminal_time":terminal_time,"processed":processed,
               "snapshots":{key:view.snapshot for key,view in evidence.views.items()},
               "fields":fields,"outcomes":[machine.invalidation.value,machine.expiry.value],
               "crt":machine.crt_binding.model_dump(mode="json") if machine.crt_binding else None,
               "latest_crt":machine.latest_crt["evidence_reference"] if machine.latest_crt else None,
               "proofs":{name:{key:encode(item) for key,item in getattr(machine,name).items()} for name in PROOF_FIELDS},
               "temporal":[[list(key),value] for key,value in sorted(machine.temporal.items())],
               "temporal_proofs":[[list(key),encode(item)] for key,item in sorted(machine.temporal_proofs.items())]}
    encoded = canonical(payload)
    if len(encoded.encode())>MAX_CHECKPOINT_BYTES:
        raise ResourceLimit("Historical checkpoint exceeds the byte budget")
    return encoded,fingerprint(payload)


def restore(run, evidence, definition, saved):
    data = json.loads(saved["payload"])
    if (fingerprint(data)!=saved["digest"] or data["version"]!=CHECKPOINT_VERSION
            or data["run_id"]!=run["run_id"] or data["selection"]!=run["identity"]["frozen_selection"]
            or data["frontier"]!=saved["frontier"]
            or not run["identity"]["evaluation_start"]<=data["frontier"]<=run["identity"]["cutoff"]
            or data["snapshots"]!={key:view.snapshot for key,view in evidence.views.items()}):
        raise ValueError("Historical checkpoint is corrupt or incompatible")
    machine = make_machine(run,evidence,definition,data["terminal_time"],data["spent"])

    def event_for(ref):
        if evidence.registry.resolve(ref) is None or ref["available_at"]>data["frontier"]:
            raise ValueError("Checkpoint references unavailable or future evidence")
        return machine.events_by_id[ref["calculation_snapshot"]][ref["event_id"]]

    def decode(values):
        outcome,reason,proof,selected,verified = values
        proof = BoundEvidence.model_validate(proof) if proof else None
        event = event_for(proof.reference.model_dump(mode="json",exclude_none=True)) if proof else None
        if any(stamp is not None and stamp>data["frontier"] for stamp in (selected,verified)):
            raise ValueError("Checkpoint contains a future verification")
        return RuleResult(Outcome(outcome),reason,proof,event,selected,verified)

    for key in STATE_FIELDS:
        setattr(machine,key,data["fields"][key])
    for key in PROOF_FIELDS:
        setattr(machine,key,{rule:decode(value) for rule,value in data["proofs"][key].items()})
    machine.invalidation,machine.expiry = map(Outcome,data["outcomes"])
    machine.crt_binding = AttemptBinding.model_validate(data["crt"]) if data["crt"] else None
    machine.latest_crt = event_for(data["latest_crt"]) if data["latest_crt"] else None
    machine.temporal = {tuple(key):value for key,value in data["temporal"]}
    machine.temporal_proofs = {tuple(key):decode(value) for key,value in data["temporal_proofs"]}
    return machine,data


def make_machine(run, evidence, definition, after=None, spent=()):
    identity = run["identity"]
    return EvaluationMachine(definition,evidence,identity,identity["evaluation_start"],identity["cutoff"],
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


def run_chunks(run, evidence, definition, publish, *, saved=None, chunk_size=32, check=lambda:None):
    identity = run["identity"]
    start,end = identity["evaluation_start"],identity["cutoff"]
    method = identity["method"]
    if method=="REFERENCE_SINGLE":
        if saved:
            raise ValueError("Single reference evaluation has no partial checkpoint")
        machine = make_machine(run,evidence,definition)
        machine.check_budget = check
        machine.run(end)
        entry = ledger_entry(run,machine,end)
        publish(checkpoint(run,evidence,machine,end,int(bool(machine.attempt)),[],None,1),
                end,[entry],True,evidence.metrics)
        return
    machine,data = restore(run,evidence,definition,saved) if saved else (make_machine(run,evidence,definition),None)
    frontier = data["frontier"] if data else start-1
    attempts,spent,terminal_time,processed = ((data["attempts"],data["spent"],data["terminal_time"],data["processed"])
                                                if data else (0,[],None,0))
    candidates = machine.boundaries(end)
    candidates.update(machine.dynamic_boundaries(frontier,end))
    if method=="INDEPENDENT_PREFIX_CHECKED" and len(candidates)>MAX_ORACLE_CUTOFFS:
        raise ResourceLimit("This producer combination requires a shorter independently checked window")
    pending = [point for point in candidates if frontier<point<=end]
    heapify(pending)
    known = set(pending)
    batch = []
    batch_count = 0
    while pending:
        check()
        frontier = heappop(pending)
        if method=="INDEPENDENT_PREFIX_CHECKED":
            if processed>=MAX_ORACLE_CUTOFFS:
                raise ResourceLimit("Historical oracle cutoff budget exceeded")
            verify_prefix(definition,run,evidence,frontier,check)
        if machine.state in TERMINAL:
            machine = make_machine(run,evidence,definition,terminal_time,spent)
        machine.step(frontier)
        if machine.state in TERMINAL and machine.attempt:
            batch.append(ledger_entry(run,machine,frontier))
            binding = machine.bindings[run["rearm_rule"]]
            spent.append(binding.proof.family)
            terminal_time = frontier
            attempts += 1
        processed += 1
        batch_count += 1
        for stamp in machine.dynamic_boundaries(frontier,end):
            if stamp not in known:
                if len(known)>=MAX_TIMELINE:
                    raise ResourceLimit("Historical timeline exceeds the evaluator budget")
                known.add(stamp)
                heappush(pending,stamp)
        done = not pending
        if attempts>=identity["max_attempts"] and not done:
            # Preserve the last trustworthy frontier instead of labeling a
            # resource-bounded prefix as a complete window.
            publish(checkpoint(run,evidence,machine,frontier,attempts,spent,terminal_time,processed),
                    frontier,batch,False,evidence.metrics)
            raise ResourceLimit("Historical attempt budget reached before the final cutoff")
        if done and (not batch or batch[-1]["trace"]["cutoff"]!=frontier):
            batch.append(ledger_entry(run,machine,frontier))
        if done or batch_count>=chunk_size:
            publish(checkpoint(run,evidence,machine,frontier,attempts,spent,terminal_time,processed),
                    frontier,batch,done,{**evidence.metrics,"processed_cutoffs":processed,
                                          "oracle_prefixes":processed if method=="INDEPENDENT_PREFIX_CHECKED" else 0,
                                          "spent_context_families":len(spent)})
            batch,batch_count = [],0
