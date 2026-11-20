"""Bounded historical evaluation with fresh, exact as-of successor proofs."""
import json
from bisect import bisect_right
from datetime import UTC, datetime

from .analysis_evidence import ResourceLimit, canonical
from .db import one
from .historical_strategy_inputs import CHECKPOINT_VERSION, MAX_CHECKPOINT_BYTES, MAX_ORACLE_CUTOFFS
from .market import FRAME_MIN
from .strategy_evaluation_sources import collect
from .strategy_evaluator import EvaluationMachine, TERMINAL
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


def checkpoint(run, evidence, frontier, attempts, spent, terminal_time, processed, dynamic):
    payload = {"version":CHECKPOINT_VERSION,"run_id":run["run_id"],
               "selection":run["identity"]["frozen_selection"],"frontier":frontier,"attempts":attempts,
               "spent":spent,"terminal_time":terminal_time,"processed":processed,"dynamic":sorted(dynamic),
               "snapshots":{key:view.snapshot for key,view in evidence.views.items()}}
    encoded = canonical(payload)
    if len(encoded.encode())>MAX_CHECKPOINT_BYTES:
        raise ResourceLimit("Historical checkpoint exceeds the byte budget")
    return encoded,fingerprint(payload)


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
    if identity["method"]!="FVG_ASOF_REFERENCE":
        raise ValueError("This run uses an unsupported historical calculation")

    # Full-window evidence validates coverage and identifies reachable cutoffs.
    # Decisions and persisted proofs always come from a fresh as-of archive.
    schedule = make_machine(run,evidence,definition).boundaries(end)
    if len(schedule)>MAX_ORACLE_CUTOFFS:
        raise ResourceLimit("FVG successor evaluation requires at most 160 as-of cutoffs")
    data = restore(run,evidence,saved) if saved else None
    frontier = data["frontier"] if data else start-1
    attempts,spent,terminal_time,processed,dynamic = (
        (data["attempts"],data["spent"],data["terminal_time"],data["processed"],set(data["dynamic"]))
        if data else (0,[],None,0,set()))
    if attempts>=identity["max_attempts"] and frontier<end:
        raise ResourceLimit("Historical attempt budget reached before the final cutoff")
    pending = set(point for point in schedule if point>frontier)
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
            publish(checkpoint(run,evidence,frontier,attempts,spent,terminal_time,processed,dynamic),
                    frontier,batch,done,{**evidence.metrics,"processed_cutoffs":processed,
                                         "oracle_prefixes":processed,"spent_context_families":len(spent)})
            batch = []
        if limit_hit:
            raise ResourceLimit("Historical attempt budget reached before the final cutoff")
