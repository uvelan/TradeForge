"""As-of FVG and order-block receipts from a verified saved M5 replay lane."""
from collections import defaultdict

from .fvg import FVGDelivery, FVGRules, evaluate_fvg
from .indicator_engine import structure_scope
from .market_structure import detect_structure
from .order_blocks import OrderBlockDelivery, OrderBlockRules, OrderBlockStream
from .replay_hour import STEP
from .replay_structure import (PAGE_SIZE, SCHEDULE, STRUCTURE, mark_pivot, processed_rows, proof_prefix,
                               replay_provenance, verified_replay_rows)
from .strategy_evidence import EvidenceRegistry
from .structure_pages import StructureDelivery


class ReplayOrderBlocks(OrderBlockStream):
    def qualifies_structure(self, event):
        # A verified saved chart can describe a break under its pinned replay
        # clock. Its rows do not establish a strategy-eligible arrival attestation.
        # The canonical strict structure producer already rejected late/straddling
        # breaks. Independently retain the close-only ordering at this boundary.
        return (event["level_available_at"] < event["confirmation_time"]
                <= event["available_at"] <= self.timing_cutoff
                and event["timing"]["eligibility_reason"] in {"UNVERIFIED_SOURCE", "UNVERIFIED_AVAILABILITY"})


def summarize(event):
    summary = {key: event[key] for key in (
        "id", "algorithm_version", "identity_policy_version", "configuration_fingerprint", "direction",
        "lower", "upper", "remaining_lower", "remaining_upper", "mitigation_pct", "observed_price",
        "state", "reason", "revision", "observation_time", "confirmation_time", "available_at")}
    if "fvg_id" in event:
        summary.update(type="FVG", zone_id=event["fvg_id"], anchor_time=event["a_time"],
                       formation_time=event["c_time"], formation_confirmation_time=event["formation_confirmation_time"],
                       formation_available_at=event["formation_available_at"])
    else:
        summary.update(type="OB", zone_id=event["ob_id"], anchor_time=event["candidate_time"],
                       formation_time=event["break_time"], formation_confirmation_time=event["bos_confirmation_time"],
                       formation_available_at=event["first_visibility_time"])
    return summary


def zone_detail(latest, formation, revisions, breaks, pivots, rows, children):
    roles = defaultdict(list)

    def mark(stamp, role, producer):
        roles[stamp].append({"role": role, "event_id": producer})

    structure = None
    if latest["type"] == "FVG":
        for key, role in (("a_time", "FVG_A"), ("b_time", "FVG_B"), ("c_time", "FVG_C")):
            mark(formation[key], role, formation["id"])
    else:
        event = breaks[formation["bos_id"]]
        pivot = pivots[event["broken_pivot_id"]]
        processed = list(processed_rows(rows))
        positions = {bar.time: index for index, (bar, _) in enumerate(processed)}
        mark_pivot(pivot, processed, positions, mark)
        if event.get("candidate_id"):
            candidate = breaks[event["candidate_id"]]
            mark(candidate["break_time"], "CANDIDATE_OBSERVATION", candidate["id"])
        structure = {key: event[key] for key in (
            "id", "type", "algorithm_version", "side", "direction", "status", "level_price", "level_time",
            "level_available_at", "break_time", "break_price", "confirmation_time", "available_at",
            "state_before", "state_after", "broken_pivot_id", "candidate_id")}
        structure["pivot"] = {key: pivot[key] for key in (
            "id", "side", "price", "pivot_time", "confirmation_bar_time", "confirmation_time",
            "available_at", "classification")}
        # Keep the complete input prefix for the stateful classifier, while
        # distinguishing its direct pivot/break witnesses from contextual rows.
        structure_roles = {stamp: list(items) for stamp, items in roles.items()}
        structure_roles.setdefault(event["break_time"], []).append({"role": "EVENT_OBSERVATION", "event_id": event["id"]})
        structure["evidence"] = proof_prefix(children, event["break_time"], event["available_at"], structure_roles, positions)
        mark(formation["candidate_time"], "OB_CANDIDATE", formation["candidate_id"])
        mark(event["break_time"], "STRUCTURE_BREAK", event["id"])

    for revision in revisions:
        mark(revision["observation_time"], "REVISION_OBSERVATION", revision["id"])
    evidence = [{**row, "roles": roles[row["time"]]} for row in children if row["time"] in roles]
    return {**latest, "revisions": revisions, "evidence": evidence, "structure": structure}


def inspect_zones(session, view, cutoff, mitigation, zone_id, before):
    lane, rows, children = verified_replay_rows(session, view, cutoff)
    provenance = replay_provenance(lane)
    fvg = FVGRules(market_schedule=SCHEDULE, mitigation=mitigation)
    ob = OrderBlockRules(mitigation=mitigation)
    latest = {}
    revisions = []
    formation = None
    breaks = {}
    pivots = {}

    def collect(_producer, event):
        nonlocal formation
        if "fvg_id" not in event and "ob_id" not in event:
            return
        summary = summarize(event)
        family = summary["zone_id"]
        # Public producers deliver each family's revisions in causal order,
        # including distinct observations sharing one arrival timestamp.
        latest[family] = summary
        if family == zone_id:
            revisions.append(summary)
            if summary["revision"] == 1:
                formation = event

    def structure_event(kind, event):
        if zone_id:
            (pivots if kind == "pivots" else breaks)[event["id"]] = event

    registry = EvidenceRegistry(event_sink=collect)
    evaluate_fvg(rows, STEP, cutoff, fvg, FVGDelivery(formation_limit=0, revision_limit=0, current_limit=0),
                 provenance, True, registry=registry)
    stream = ReplayOrderBlocks(ob, STRUCTURE, provenance, STEP,
                               OrderBlockDelivery(formation_limit=0, revision_limit=0, current_limit=0),
                               timing_cutoff=cutoff, registry=registry)
    detect_structure(rows, STEP, STRUCTURE, structure_scope(provenance), StructureDelivery(pivot_limit=0, break_limit=0),
                     {"cutoff": cutoff, "frozen": True, "provenance": provenance},
                     consumer=stream.observe, public_event=structure_event, registry=registry)

    zones = sorted(latest.values(), key=lambda z: (z["available_at"], z["observation_time"], z["type"], z["zone_id"]))
    end = len(zones) if before is None else min(len(zones), before)
    start = max(0, end - PAGE_SIZE)
    selected = (zone_detail(latest[zone_id], formation, revisions, breaks, pivots, rows, children)
                if formation is not None else None)
    return {**{key: lane[key] for key in ("symbol", "source", "feed_id", "price_basis", "timeframe")},
            "session_id": session["id"], "view": view, "cutoff": cutoff, "authority": "RESEARCH_ONLY",
            "availability_policy": "PINNED_M5_AVAILABLE_AT_OR_MODELED_NOMINAL_CLOSE",
            "rule_version": 1, "rules": {"fvg": fvg.canonical(), "ob": ob.model_dump(),
                                         "structure": STRUCTURE.identity_parameters()},
            "zones": zones[start:end], "total": len(zones), "start": start, "end": end,
            "previous_before": start or None, "selected": selected}
