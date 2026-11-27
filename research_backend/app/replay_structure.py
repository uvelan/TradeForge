"""As-of structure and liquidity event receipts for immutable M5 replay sessions."""
from decimal import Decimal

from fastapi import HTTPException

from .arrival_policy import MODE_VERSIONS, RECORDED_MODE
from .causal_timing import STRICT
from .indicator_coverage import MarketSchedule, replay_availability
from .indicator_engine import EvidenceBar, structure_scope
from .indicators import Bar
from .liquidity import LiquidityDelivery, LiquidityRules, LiquidityStream, digest
from .market_structure import StructureRules, detect_structure
from .replay_hour import STEP, capture_horizon, capture_start, verified_children
from .structure_pages import StructureDelivery

RULE_VERSION = 1
PAGE_SIZE = 50
SCHEDULE = MarketSchedule(kind="forex_weekly")
STRUCTURE = StructureRules(algorithm_version=2, left=2, right=2, wick_candidates=True, market_schedule=SCHEDULE)
LIQUIDITY = LiquidityRules()
MIN_DISPLACEMENT = Decimal("0.00100")
MSS_RULE = {"parent": "CHOCH", "measurement": "DIRECTIONAL_CLOSE_MINUS_OPEN",
            "min_displacement": float(MIN_DISPLACEMENT), "unit": "PRICE_UNITS"}


def replay_provenance(lane):
    # Use the same producer identity as the strict chart indicator on this frozen
    # lane. Its scope excludes retrieval, partition list and moving replay cutoff.
    source = {key: lane.get(key) for key in ("source", "symbol", "timeframe", "dataset_id", "feed_id", "price_basis")}
    return {**source, "partitions": lane["partitions"], "context_authority": "CLIENT_SUPPLIED_UNVERIFIED",
            "timing_policy": STRICT, "timing_policy_version": 3, "availability_mode": RECORDED_MODE,
            "availability_mode_version": MODE_VERSIONS[RECORDED_MODE], "schedule_completeness": "NOT_ESTABLISHED"}


def processed_rows(rows):
    # Both producers consume closed candles in arrival order and ignore older bars
    # arriving behind the chronological frontier.
    frontier = -1
    for bar, available in sorted(rows, key=lambda row: (row[1], row[0].time)):
        if bar.time > frontier:
            yield bar, available
            frontier = bar.time


def mark_pivot(pivot, processed, positions, mark):
    anchor = positions[pivot["pivot_time"]]
    for index in range(anchor - STRUCTURE.left, anchor + STRUCTURE.right + 1):
        role = "PIVOT_LEFT" if index < anchor else "PIVOT_RIGHT" if index > anchor else "PIVOT_ANCHOR"
        mark(processed[index][0].time, role, pivot["id"])
    mark(pivot["confirmation_bar_time"], "PIVOT_CONFIRMATION", pivot["id"])


def proof_prefix(children, observation_time, available_at, witnesses, positions):
    return [{**row, "roles": witnesses.get(row["time"], [])} for row in children
            if row["time"] in positions and row["time"] <= observation_time and row["available_at"] <= available_at]


def replay_events(lane, rows, cutoff, event_id):
    provenance = replay_provenance(lane)
    pivots = {}
    candles = {bar.time: bar for bar, _ in rows}
    events = []
    processed = list(processed_rows(rows))
    positions = {bar.time: index for index, (bar, _) in enumerate(processed)}
    witnesses = {}
    candidates = {}
    activation = {}
    latest_revision = {}
    prior_revision = {}

    def mark(stamp, role, producer):
        witnesses.setdefault(stamp, []).append({"role": role, "event_id": producer})

    def publish(summary, pivot_ids, candidate=None, pool_id=None, prior=None):
        events.append(summary)
        if summary["status"] == "provisional":
            candidates[summary["id"]] = summary["observation_time"]
        if summary["id"] != event_id:
            return
        mark(summary["observation_time"], "EVENT_OBSERVATION", event_id)
        if candidate in candidates:
            mark(candidates[candidate], "CANDIDATE_OBSERVATION", candidate)
        if pool_id in activation:
            stamp, producer = activation[pool_id]
            mark(stamp, "LEVEL_ACTIVATION", producer)
        if prior:
            mark(prior[0], "LEVEL_REVISION", prior[1])
        for producer in pivot_ids:
            mark_pivot(pivots[producer], processed, positions, mark)

    def publish_structure(kind, event):
        if kind == "pivots":
            pivots[event["id"]] = event
            return
        if event["type"] == "TIMING_REJECTED":
            return
        pivot = pivots[event["broken_pivot_id"]]
        stamp = event["break_time"]
        summary = {key: event[key] for key in ("id", "type", "side", "direction", "status", "level_price",
                                               "confirmation_time", "available_at", "state_before", "state_after")}
        summary.update(anchor_time=pivot["pivot_time"], observation_time=stamp, rule_version=RULE_VERSION,
                       supporting_ids=[pivot["id"]] + ([event["candidate_id"]] if event.get("candidate_id") else []),
                       level_available_at=pivot["available_at"], break_price=event["break_price"])
        publish(summary, [pivot["id"]], event.get("candidate_id"))
        if event["type"] == "CHOCH" and event["status"] == "confirmed" and pivot["available_at"] <= stamp:
            bar = candles[stamp]
            sign = 1 if event["direction"] == "BULLISH" else -1
            body = sign * (Decimal(str(bar.close)) - Decimal(str(bar.open)))
            if body >= MIN_DISPLACEMENT:
                publish({**summary, "id": digest("MSS", RULE_VERSION, MSS_RULE, event["id"]),
                               "type": "MSS", "displacement": float(body),
                               "supporting_ids": [event["id"], *summary["supporting_ids"]]}, [pivot["id"]], event.get("candidate_id"))

    def publish_liquidity(kind, event):
        pool_revision = kind == "pools"
        pool_id = event["pool_id"]
        if pool_revision:
            prior_revision[event["id"]] = latest_revision.get(pool_id)
            latest_revision[pool_id] = (event["observation_time"], event["id"])
            if event["state"] == "ACTIVE":
                activation.setdefault(pool_id, latest_revision[pool_id])
        if pool_revision and (event["state"] != "ACTIVE" or event["reason"] != "CONFIRMED_PIVOT"):
            return
        side = "HIGH" if event["liquidity_side"] == "BSL" else "LOW"
        members = event["constituents"]
        dependencies = [p["id"] for p in members]
        if not pool_revision:
            dependencies.append(event["pool_revision_id"])
            if event.get("candidate_id"):
                dependencies.append(event["candidate_id"])
        prior = prior_revision.get(event["id"] if pool_revision else event["pool_revision_id"])
        publish({**{key: event[key] for key in ("id", "status", "confirmation_time", "available_at", "lower",
                                                      "upper", "liquidity_side", "pool_id", "observation_time")},
                       "type": event["classification"] if pool_revision else event["state"], "side": side,
                       "direction": None, "anchor_time": members[0]["time"],
                       "level_price": event["upper"] if side == "HIGH" else event["lower"],
                       "supporting_ids": dependencies, "rule_version": RULE_VERSION},
                [member["id"] for member in members], event.get("candidate_id"), pool_id, prior)

    stream = LiquidityStream(LIQUIDITY, LiquidityDelivery(pool_limit=0, sweep_limit=0, current_limit=0),
                             STEP, timing=(cutoff, provenance, SCHEDULE), public_event=publish_liquidity)
    detect_structure(rows, STEP, STRUCTURE, structure_scope(provenance), StructureDelivery(pivot_limit=0, break_limit=0),
                     {"cutoff": cutoff, "frozen": True, "provenance": provenance},
                     consumer=stream.observe, public_event=publish_structure, registry=stream.registry)
    return sorted(events, key=lambda e: (e["available_at"], e["observation_time"], e["type"], e["id"])), witnesses, positions


def verified_replay_rows(session, view, cutoff):
    primary = session["market"]
    lane = primary if view == "primary" else session["views"].get(view)
    if (not lane or lane.get("error") or lane.get("source") != "DUKASCOPY_SAVED" or lane.get("timeframe") != "M5"
            or lane.get("symbol") not in {"EURUSD", "GBPUSD"} or lane.get("price_basis") != "BID"
            or not lane.get("coverage") or not primary.get("coverage") or not primary.get("candles")):
        raise HTTPException(422, "Inspection requires a saved Dukascopy EURUSD or GBPUSD M5 BID archive range")
    horizon = capture_horizon(primary)
    if not capture_start(primary) <= cutoff <= horizon:
        raise HTTPException(422, "Requested cutoff outside the captured replay session clock")
    rows = []
    for row in lane["candles"]:
        available = replay_availability(row, STEP)
        if type(available) is not int or available < row["time"] + STEP:
            raise HTTPException(409, "Invalid M5 candle availability in replay")
        if available <= cutoff and row.get("closed", True):
            try:
                rows.append((EvidenceBar.model_validate(row), available))
            except ValueError as exc:
                raise HTTPException(409, f"Invalid replay M5 candle at {row['time']}") from exc

    # Verify the captured bytes before publishing any events. The selected
    # receipt retains the exact input prefix of the stateful canonical producer.
    children = verified_children(lane, [(Bar.model_validate(bar.model_dump()), available) for bar, available in rows],
                                 include_availability_basis=True)
    return lane, rows, children


def inspect_structure_liquidity(session, view, cutoff, event_id=None, before=None):
    lane, rows, children = verified_replay_rows(session, view, cutoff)
    events, witnesses, positions = replay_events(lane, rows, cutoff, event_id)
    end = len(events) if before is None else min(before, len(events))
    start = max(end - PAGE_SIZE, 0)
    selected = next((e for e in events if e["id"] == event_id), None)
    if selected is not None:
        evidence = proof_prefix(children, selected["observation_time"], selected["available_at"], witnesses, positions)
        selected = {**selected, "evidence": evidence}
    return {**{key: lane[key] for key in ("symbol", "source", "feed_id", "price_basis", "timeframe")},
            "session_id": session["id"], "view": view, "cutoff": cutoff, "rule_version": RULE_VERSION,
            "rules": {"structure": STRUCTURE.identity_parameters(), "liquidity": LIQUIDITY.identity(), "mss": MSS_RULE},
            "availability_policy": "PINNED_M5_AVAILABLE_AT_OR_MODELED_NOMINAL_CLOSE", "authority": "RESEARCH_ONLY",
            "events": events[start:end], "total": len(events), "start": start, "end": end,
            "previous_before": start or None, "selected": selected}
