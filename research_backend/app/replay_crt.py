"""Historical H1 CRT families from the strict producer on a pinned saved M5 replay."""
from collections import defaultdict

from .crt import CRTDelivery, CRTRules, VERSION, evaluate_crt
from .indicator_coverage import open_intervals
from .replay_hour import STEP
from .replay_structure import PAGE_SIZE, SCHEDULE, processed_rows, replay_provenance, verified_replay_rows
from .strategy_evidence import EvidenceRegistry


# Match the validated numeric representation of these rules on the public indicator API.
RULES = CRTRules(reference_frame="H1", market_schedule=SCHEDULE, min_penetration=0.0, min_reclaim=0.0)
DELIVERY = CRTDelivery(formation_limit=0, sweep_limit=0, revision_limit=0, current_limit=0)


def summarize(event):
    fields = (
        "id", "range_id", "reference_id", "reference_start", "reference_end", "reference_available_at",
        "reference_ohlc", "reference_mode", "reference_frame", "constituents", "high", "low", "width",
        "midpoint", "algorithm_version", "identity_policy_version", "provenance_id", "configuration_fingerprint",
        "revision", "evidence_fingerprint", "state", "reason", "observation_time", "confirmation_time",
        "available_at", "observation_ohlc", "swept_boundary", "sweep_extreme", "sweep_id", "reclaim_id",
        "penetration", "reclaim_close", "replacement_reference",
    )
    summary = {key: event[key] for key in fields if key in event}
    summary["sweep_side"] = event.get("sweep_side")
    return summary


def continuity(event):
    previous, successor = event["preceding_time"], event["observation_time"]
    missing = None
    if event["reason"] == "MISSING_OPEN_CANDLES":
        missing = [{"start": left, "end": right} for left, right in open_intervals(previous + STEP, successor, SCHEDULE)]
    return {"previous_observed_open": previous, "next_observed_open": successor, "step_seconds": STEP,
            "missing_open_intervals": missing, "schedule": SCHEDULE.model_dump()}


def range_detail(revisions, children, rows):
    formation = revisions[0]
    reference = [row for row in children if formation["reference_start"] <= row["time"] < formation["reference_end"]]
    roles = defaultdict(list)

    def mark(stamp, role, event):
        roles[stamp].append({"role": role, "event_id": event["id"]})

    for child in reference:
        mark(child["time"], "REFERENCE_CHILD", formation)

    candidate = None
    for revision in revisions[1:]:
        if revision["reason"] == "REFERENCE_CAP_REPLACEMENT":
            # The producer records the replacing reference's start, end and identity.
            for child in children:
                if revision["observation_time"] <= child["time"] < revision["confirmation_time"]:
                    mark(child["time"], "REPLACEMENT_REFERENCE_CHILD", revision)
            continue
        stamp = revision["observation_time"]
        mark(stamp, "REVISION_OBSERVATION", revision)
        if revision["reason"] == "NO_SAME_CANDLE_RECLAIM":
            candidate = stamp
        if revision["reason"] in {"CANDIDATE_AGE", "REFERENCE_AGE"}:
            for bar, _ in processed_rows(rows):
                if formation["reference_available_at"] <= bar.time <= stamp and (
                        revision["reason"] == "REFERENCE_AGE" or candidate < bar.time):
                    mark(bar.time, "AGE_OBSERVATION", revision)
        if gap := revision.get("continuity"):
            mark(gap["previous_observed_open"], "GAP_PREVIOUS_OBSERVED", revision)
            mark(gap["next_observed_open"], "GAP_NEXT_OBSERVED", revision)

    evidence = [{**row, "roles": roles[row["time"]]} for row in children if row["time"] in roles]
    return {**revisions[-1], "reference_children": reference, "revisions": revisions, "evidence": evidence}


def inspect_crt(session, view, cutoff, range_id=None, before=None):
    lane, rows, children = verified_replay_rows(session, view, cutoff)
    latest = {}
    revisions = []

    def collect(_registry, event):
        summary = summarize(event)
        family = event["range_id"]
        latest[family] = summary
        if family == range_id:
            if "preceding_time" in event:
                summary = {**summary, "continuity": continuity(event)}
            revisions.append(summary)

    # The sink receives all canonical publications, including terminal revisions and
    # multiple events delivered at one instant, independently of indicator paging.
    evaluate_crt(rows, STEP, cutoff, "H1", RULES, DELIVERY, replay_provenance(lane),
                 registry=EvidenceRegistry(event_sink=collect))
    ranges = sorted(latest.values(), key=lambda row: (row["available_at"], row["observation_time"], row["range_id"]))
    end = len(ranges) if before is None else min(len(ranges), before)
    start = max(0, end - PAGE_SIZE)
    return {**{key: lane[key] for key in ("symbol", "source", "feed_id", "price_basis", "timeframe")},
            "session_id": session["id"], "view": view, "cutoff": cutoff, "authority": "RESEARCH_ONLY",
            "availability_policy": "PINNED_M5_AVAILABLE_AT_OR_MODELED_NOMINAL_CLOSE",
            "rule_version": VERSION, "rules": {"crt": RULES.model_dump()},
            "ranges": ranges[start:end], "total": len(ranges), "start": start, "end": end,
            "previous_before": start or None, "selected": range_detail(revisions, children, rows) if revisions else None}
