"""A versioned, research-only join of the canonical CRT and SMT producers at one replay clock."""
from collections import defaultdict
import json

from fastapi import HTTPException

from .analysis_evidence import ResourceLimit
from .arrival_policy import SIMULATION_AUTHORITY, SIMULATION_MODE
from .crt import VERSION as CRT_VERSION, evaluate_crt
from .db import one, transaction
from .indicator_coverage import open_intervals, replay_availability
from .market import FRAME_MIN
from .replay_crt import DELIVERY, RULES, continuity, range_detail, summarize
from .replay_hour import STEP, capture_start
from .replay_structure import SCHEDULE, replay_provenance, verified_replay_rows
from .smt_causal import ALGORITHM_VERSION, LIFECYCLE_VERSION, FrozenFeed, StrictSmtRequest, evaluate_strict_smt
from .strategy_evidence import EvidenceRegistry, fingerprint

PROFILE = "CRT_SMT_M5_V1"
POLICY = "PINNED_M5_AVAILABLE_AT_OR_MODELED_NOMINAL_CLOSE"
SYMBOLS = ("EURUSD", "GBPUSD")
SOURCE_FIELDS = ("symbol", "source", "feed_id", "price_basis", "timeframe")
SMT_RULES = dict(relationship="POSITIVE", period=2, alignment_bars=0, tolerance_bps=0.0,
                 confirmation_bars=1, expiry_bars=64)


def source(lane):
    return {key: lane[key] for key in SOURCE_FIELDS}


def component(sources, reason):
    return dict(family_id=None, revision_id=None, revision=None, state="UNAVAILABLE", condition="INCOMPLETE",
                direction=None, reason=reason, observation_time=None, confirmation_time=None, available_at=None,
                sources=sources, evidence=[])


def coverage_issue(symbol, reason, missing=()):
    intervals = []
    for stamp in sorted(missing):
        if intervals and stamp == intervals[-1]["end"]:
            intervals[-1]["end"] += STEP
        else:
            intervals.append(dict(start=stamp, end=stamp+STEP))
    return dict(symbol=symbol, reason=reason, missing_intervals=intervals)


def read_panes(session, cutoff):
    primary = session["market"]
    if (primary.get("source") != "DUKASCOPY_SAVED" or not primary.get("coverage") or
            not primary.get("candles") or primary.get("timeframe") not in FRAME_MIN):
        raise HTTPException(422, "Confluence requires a saved Dukascopy replay clock")
    horizon = max(primary["coverage"]["end"], replay_availability(primary["candles"][-1], FRAME_MIN[primary["timeframe"]]*60))
    if not capture_start(primary) <= cutoff <= horizon:
        raise HTTPException(422, "Requested cutoff outside the captured replay session clock")

    panes, issues = {}, {}
    views = {"primary": primary, **session["views"]}
    for symbol in SYMBOLS:
        candidates = [(view, lane) for view, lane in views.items() if lane.get("symbol") == symbol
                      and lane.get("source") == "DUKASCOPY_SAVED" and lane.get("timeframe") == "M5"
                      and lane.get("price_basis") == "BID" and lane.get("coverage") and not lane.get("error")]
        if not candidates:
            issues[symbol] = coverage_issue(symbol, "MISSING_REQUIRED_PANE")
            continue
        view, lane = candidates[0]
        if any(other != lane for _, other in candidates[1:]):
            issues[symbol] = coverage_issue(symbol, "AMBIGUOUS_REQUIRED_PANE")
            continue
        _, rows, children = verified_replay_rows(session, view, cutoff)
        start, end = capture_start(lane), lane["coverage"]["end"]
        meta = dict(**source(lane), partitions=lane["partitions"], start=start, end=end)
        panes[symbol] = dict(lane=lane, rows=rows, children=children, source=meta)
        # Checking the closed prefix also catches a missing trailing child that has
        # no successor yet. Never use the capture's eventual gap list at an earlier clock.
        if end-start > 10_000*STEP:
            issues[symbol] = coverage_issue(symbol, "REPLAY_WINDOW_TOO_LARGE")
            continue
        # A delayed final captured candle extends only its own knowledge clock.
        # No later nominal close beyond the pane can be treated as covered.
        final_arrival = replay_availability(lane["candles"][-1], STEP) if lane["candles"] else end
        if cutoff < start or cutoff > max(end, final_arrival) or cutoff//STEP*STEP > end:
            issues[symbol] = coverage_issue(symbol, "OUTSIDE_CAPTURED_PANE_WINDOW")
            continue
        closed_end = min(end, cutoff//STEP*STEP)
        bounds = open_intervals(start, closed_end, SCHEDULE)
        if bounds is None:
            issues[symbol] = coverage_issue(symbol, "UNKNOWN_SCHEDULE")
            continue
        expected = {stamp for left, right in bounds for stamp in range(left, right, STEP)}
        observed = {bar.time for bar, _ in rows}
        if observed - expected:
            issues[symbol] = coverage_issue(symbol, "SCHEDULE_MISMATCH")
        elif expected - observed:
            issues[symbol] = coverage_issue(symbol, "MISSING_OPEN_CANDLES", expected - observed)
    return panes, issues


def crt_component(pane, cutoff):
    result = component([pane["source"]], "NO_VISIBLE_CRT_FAMILY")
    histories = defaultdict(list)

    def collect(_, event):
        row = summarize(event)
        if "preceding_time" in event:
            row["continuity"] = continuity(event)
        histories[row["range_id"]].append(row)

    evaluate_crt(pane["rows"], STEP, cutoff, "H1", RULES, DELIVERY, replay_provenance(pane["lane"]),
                 registry=EvidenceRegistry(event_sink=collect))
    if not histories:
        return result
    family = min(histories, key=lambda key: (-histories[key][0]["reference_start"], key))
    chain = histories[family]
    last = chain[-1]
    state = last["state"]
    condition = "NOT_SATISFIED"
    if state in {"LOW_SWEPT_RECLAIMED", "HIGH_SWEPT_RECLAIMED", "EXPANSION_OBSERVED"}:
        condition = "SATISFIED"
    elif state in {"AMBIGUOUS", "UNCERTAIN"} or state.endswith("_CANDIDATE"):
        condition = "INCOMPLETE"
    details = range_detail(chain, pane["children"], pane["rows"])
    return {**result, **{key: last[key] for key in ("state", "reason", "observation_time",
                                                      "confirmation_time", "available_at")},
            "family_id":family, "revision_id":last["id"], "revision":last["revision"], "condition":condition,
            "direction":{"LOW":"BULLISH", "HIGH":"BEARISH"}.get(last["sweep_side"]),
            "reference_start":last["reference_start"], "reference_end":last["reference_end"],
            "reference_id":last["reference_id"], "reference_ohlc":last["reference_ohlc"],
            "sweep_side":last["sweep_side"], "revisions":chain,
            "evidence":[{**source(pane["lane"]), **c} for c in details["evidence"]]}


def smt_evidence(family, panes, cutoff):
    """Pin the matching prefix, four pivot windows, activation, confirmation and laggard surveillance."""
    matching = family["matching_window"]
    chain = family["revisions"]
    first, last = chain[0], chain[-1]
    laggard = "comparison" if family["leader"] == "primary" else "primary"
    terminal = last["state"] in {"EXPIRED", "SMT_INVALIDATED"}
    monitor_end = last["confirmation_time"] if terminal else cutoff
    evidence = []
    for side, symbol in zip(("primary", "comparison"), SYMBOLS):
        pivots = family["pivots"][side]
        for child in panes[symbol]["children"]:
            stamp = child["time"]
            roles = []

            def mark(role, event_id):
                roles.append(dict(role=role, event_id=event_id))

            if matching["coverage_prefix"]["start"] <= stamp < matching["end"]:
                mark("MATCHING_PREFIX", matching["attestation"]["event_id"])
            for name, pivot in pivots.items():
                if pivot["observation_start"] <= stamp < pivot["nominal_confirmation"]:
                    role = "PIVOT_ANCHOR" if stamp == pivot["time"] else "PIVOT_CHILD"
                    mark(name.upper()+"_"+role, pivot["id"])
            if side == laggard:
                if pivots["current"]["time"] < stamp < first["confirmation_time"]:
                    mark("ACTIVATION_OBSERVATION", first["id"])
                if stamp < monitor_end and stamp+STEP > first["available_at"]:
                    roles.append(dict(role="AS_OF_LAGGARD_SURVEILLANCE", event_id=family["id"], cutoff=cutoff))
            for rev in chain[1:]:
                if (rev["state"] == "SMT_CONFIRMED" and side == laggard and
                        stamp < first["available_at"] < stamp+STEP):
                    mark("CONFIRMATION_STRADDLING_CHILD", rev["id"])
                if (rev["state"] == "SMT_CONFIRMED" or side == laggard) and (
                        rev["timing"]["observation_start"] <= stamp < rev["confirmation_time"]):
                    mark(rev["state"], rev["id"])
            if roles:
                evidence.append({**source(panes[symbol]["lane"]), **child, "roles":roles})
    return evidence


def smt_component(panes, cutoff):
    result = component([p["source"] for p in panes.values()], "NO_VISIBLE_SMT_FAMILY")
    if len(panes) != 2:
        return result
    if any(p["source"]["end"]-p["source"]["start"] > 10_000*STEP for p in panes.values()):
        return {**result, "reason":"REPLAY_WINDOW_TOO_LARGE"}
    feeds = [FrozenFeed(**p["source"], calendar=SCHEDULE) for p in panes.values()]
    request = StrictSmtRequest(primary=feeds[0], comparison=feeds[1], cutoff=cutoff,
                               availability_mode=SIMULATION_MODE, **SMT_RULES, limit=0)

    def history(feed, start, end, step):
        # The caller just verified these exact child bytes against the frozen
        # catalog. Preserve each child's own arrival, including a delayed peer.
        pane = panes[feed.symbol]
        # Historical payloads may omit a materialized nominal close. Only the
        # SMT simulator needs it populated on the bar as well as on the tuple.
        rows = [(bar if bar.available_at == available else bar.model_copy(update={"available_at":available}), available)
                for bar, available in pane["rows"]]
        return dict(rows=rows, evidence=dict(context_authority=SIMULATION_AUTHORITY,
                    context_partitions=pane["source"]["partitions"], availability_policy=POLICY))

    selected = None

    def collect(family):
        nonlocal selected
        if selected is None or (-family["candidate_at"], family["id"]) < (-selected["candidate_at"], selected["id"]):
            selected = family

    try:
        analysis = evaluate_strict_smt(request, loader=history, family_sink=collect)
    except ResourceLimit as error:
        raise HTTPException(422, str(error)) from error
    if selected is None:
        return {**result, "reason":analysis["reason"] or result["reason"]}
    chain = selected["revisions"]
    last = chain[-1]
    state = selected["current_state"]
    condition = "NOT_SATISFIED"
    if state == "SMT_CONFIRMED" and selected["current_simulation_eligible"]:
        condition = "SATISFIED"
    elif state in {"UNCERTAIN", "SMT_CANDIDATE", "SMT_CONFIRMED"}:
        condition = "INCOMPLETE"
    return {**result, "state":state, "published_state":last["state"], "reason":selected["current_reason"] or state,
            "condition":condition, "family_id":selected["id"], "revision_id":last["id"], "revision":len(chain),
            "direction":selected["direction"].upper(), "available_at":last["available_at"],
            "observation_time":last["timing"]["observation_start"], "confirmation_time":last["confirmation_time"],
            "candidate_at":selected["candidate_at"], "leader":selected["leader"], "pivots":selected["pivots"],
            "revisions":[dict(id=r["id"], state=r["state"], available_at=r["available_at"],
                              observation_time=r["timing"]["observation_start"], confirmation_time=r["confirmation_time"],
                              witness=r["witness"]) for r in chain], "evidence":smt_evidence(selected, panes, cutoff)}


def joined_state(crt, smt):
    for entry in (crt, smt):
        if entry.get("coverage_reason"):
            return "INCOMPLETE", entry["coverage_reason"], None
    for name, entry in (("CRT", crt), ("SMT", smt)):
        if entry["condition"] == "INCOMPLETE":
            reason = entry["reason"]
            if entry["family_id"]:
                reason = name+("_CANDIDATE" if entry["state"].endswith("_CANDIDATE") else "_UNCERTAIN")
            return "INCOMPLETE", reason, None
    if crt["condition"] == "NOT_SATISFIED":
        return "NOT_ALIGNED", "CRT_NOT_RECLAIMED", None
    if smt["condition"] == "NOT_SATISFIED":
        return "NOT_ALIGNED", "SMT_NOT_CONFIRMED", None
    if crt["direction"] != smt["direction"]:
        return "CONTRADICTED", "OPPOSITE_DIRECTIONS", None
    return "ALIGNED", "MATCHING_DIRECTIONS", crt["direction"]


def inspect_confluence(session, cutoff, profile=PROFILE, receipt_id=None):
    saved = None
    if receipt_id:
        record = one("SELECT payload FROM replay_confluence_receipts WHERE session_id=? AND id=?", (session["id"], receipt_id))
        if record is None:
            raise HTTPException(409, "Unknown receipt for this replay session")
        saved = json.loads(record["payload"])
        if (fingerprint(saved) != receipt_id or saved["session_id"] != session["id"] or
                (saved["cutoff"], saved["profile"]) != (cutoff, profile)):
            raise HTTPException(409, "Pinned confluence receipt differs from the requested clock or profile")
    panes, issues = read_panes(session, cutoff)
    if saved is not None:
        return {**saved, "receipt_id":receipt_id}

    crt = crt_component(panes["EURUSD"], cutoff) if "EURUSD" in panes else component([], issues["EURUSD"]["reason"])
    smt = smt_component(panes, cutoff)
    for entry, symbols in ((crt, SYMBOLS[:1]), (smt, SYMBOLS)):
        entry["coverage_issues"] = [issues[symbol] for symbol in symbols if symbol in issues]
        if entry["coverage_issues"]:
            entry.update(condition="INCOMPLETE", coverage_reason=entry["coverage_issues"][0]["reason"])
    status, reason, direction = joined_state(crt, smt)
    first_available = None
    if status != "INCOMPLETE":
        # A closed slot with an outstanding delivery made the join incomplete
        # until recovery, even if neither producer published a newer revision.
        recoveries = (stamp for p in panes.values() for bar, stamp in p["rows"] if stamp > bar.time+STEP)
        first_available = max(crt["available_at"], smt["available_at"], max(recoveries, default=0))
    receipt = dict(session_id=session["id"], cutoff=cutoff, profile=profile, rule_version=1, authority="RESEARCH_ONLY",
                   availability_policy=POLICY, rules=dict(crt=RULES.model_dump(), smt=SMT_RULES, crt_version=CRT_VERSION,
                   smt_algorithm_version=ALGORITHM_VERSION, smt_lifecycle_version=LIFECYCLE_VERSION,
                   availability_mode=SIMULATION_MODE), status=status, reason=reason, direction=direction,
                   first_available_at=first_available, components=dict(crt=crt,smt=smt))
    return {**receipt, "receipt_id":fingerprint(receipt)}


def pin_confluence(session, cutoff, profile, expected_id):
    receipt = inspect_confluence(session, cutoff, profile)
    key = receipt.pop("receipt_id")
    if key != expected_id:
        raise HTTPException(409, "Current receipt differs from the expected version")
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
    with transaction() as db:
        db.execute("INSERT INTO replay_confluence_receipts(id,session_id,payload) VALUES(?,?,?) ON CONFLICT(id) DO NOTHING",
                   (key, session["id"], encoded))
        stored = db.execute("SELECT payload FROM replay_confluence_receipts WHERE id=?", (key,)).fetchone()
        if stored["payload"] != encoded:
            raise HTTPException(409, "Pinned receipt payload conflicts with its checksum")
    return {**receipt, "receipt_id":key}
