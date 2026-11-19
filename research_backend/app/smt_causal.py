"""Frozen dual-source SMT evidence built on the legacy wick mathematics."""
from bisect import bisect_left
from dataclasses import asdict, dataclass
from hashlib import sha256
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .analysis_evidence import AnalysisCoordinator, ResourceLimit
from .arrival_policy import MODE_VERSIONS, RECORDED_AUTHORITY, RECORDED_MODE, SIMULATION_AUTHORITY, SIMULATION_MODE
from .causal_timing import STRICT, TemporalEvidence, strategy_eligible
from .indicator_coverage import MarketSchedule, open_intervals
from .indicator_engine import Partition
from .indicator_history import load_reference
from .indicators import swing_points
from .market import FRAME_MIN
from .smt import divergence_leader, nearest_peer, pivots, signed_change
from .strategy_evidence import (
    REFERENCE_VERSION, SOURCE_FIELDS, SMT_ALGORITHM_VERSION as ALGORITHM_VERSION,
    VERSION as TIMING_VERSION, SmtUncertainty, fingerprint,
)
from .structure_pages import EventPage, StaleStructureCursor

LIFECYCLE_VERSION = 2
AUTHORITY = "CHECKSUM_VERIFIED_ARCHIVE"
SIDES = ("primary", "comparison")
MAX_TIMESTAMP = 32_503_680_000


class FrozenFeed(BaseModel, extra="forbid"):
    source: Literal["MT5_SAVED", "DUKASCOPY_SAVED"]
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: str
    feed_id: str = Field(min_length=1, max_length=200)
    dataset_id: str | None = Field(default=None, max_length=200)
    price_basis: str = Field(min_length=1, max_length=40)
    calendar: MarketSchedule
    partitions: list[Partition] = Field(min_length=1, max_length=50)
    start: int = Field(ge=0, le=MAX_TIMESTAMP)
    end: int = Field(gt=0, le=MAX_TIMESTAMP)
    arrival_manifest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    delay_seconds: int = Field(default=0, ge=0, le=86_400)

    @model_validator(mode="after")
    def validate_window(self):
        minutes = FRAME_MIN.get(self.timeframe)
        if not minutes:
            raise ValueError("Unsupported SMT timeframe")
        step = minutes * 60
        if self.start % step or self.end % step or not 0 < self.end-self.start <= 10_000*step:
            raise ValueError("SMT evidence window must contain 1 to 10,000 aligned UTC slots")
        if len({part.id for part in self.partitions}) != len(self.partitions):
            raise ValueError("Duplicate frozen partition ID")
        if self.dataset_id and self.dataset_id not in {part.id for part in self.partitions}:
            raise ValueError("Dataset ID must identify one of the frozen partitions")
        return self

    def scope(self):
        return self.model_dump(include=set(SOURCE_FIELDS) | {"calendar"})

    def manifest(self):
        return sorted((part.id, part.sha256.lower()) for part in self.partitions)


class ArchiveRequest(FrozenFeed):
    cutoff: int
    frozen: Literal[True] = True
    availability_mode: Literal["STRICT_RECORDED", "HISTORICAL_SIMULATION"]


class StrictSmtRequest(BaseModel, extra="forbid"):
    primary: FrozenFeed
    comparison: FrozenFeed
    availability_mode: Literal["STRICT_RECORDED", "HISTORICAL_SIMULATION"] = RECORDED_MODE
    relationship: Literal["POSITIVE"] = "POSITIVE"
    period: int = Field(default=3, ge=2, le=20)
    alignment_bars: int = Field(default=2, ge=0, le=10)
    tolerance_bps: float = Field(default=0.5, ge=0, le=100, allow_inf_nan=False)
    confirmation_bars: int = Field(default=1, ge=0, le=20)
    expiry_bars: int = Field(default=64, ge=1, le=128)
    cutoff: int = Field(ge=0, le=MAX_TIMESTAMP)
    limit: int = Field(default=30, ge=0, le=100)
    cursor: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def independent_sources(self):
        if self.primary.symbol.casefold() == self.comparison.symbol.casefold():
            raise ValueError("SMT requires two different instruments")
        if self.expiry_bars < self.confirmation_bars:
            raise ValueError("SMT expiry cannot precede the required confirmation bars")
        return self

    # Coordinator consumes the same timing and resource policy as other strict indicators.
    @property
    def timing_policy(self):
        return STRICT


@dataclass(frozen=True, slots=True)
class ObservedPivot:
    id: str
    time: int
    price: float
    nominal_confirmation: int
    observation_start: int
    available_at: int
    evidence_digest: str


@dataclass(frozen=True, slots=True)
class Match:
    primary: ObservedPivot
    comparison: ObservedPivot
    closure: int
    start: int
    end: int
    evidence_digest: str
    prefix_digest: str
    prefix_start: int


def _usable(rows):
    return {bar.time: (bar, available) for bar, available in rows if bar.available_at == available}


def _pivots(series, period, step, candle_ids):
    bars = [bar for bar, _ in series.values()]
    timestamps = [bar.time for bar in bars]
    observed = {}
    for label, values in swing_points(bars, period).items():
        stream = []
        for pivot in pivots(bars, values, period, step):
            index = bisect_left(timestamps, pivot.time)
            members = bars[index-period:index+period+1]
            witness = fingerprint([candle_ids[member.time] for member in members])
            stream.append(ObservedPivot(
                fingerprint([ALGORITHM_VERSION, label, pivot.time, pivot.price, witness]),
                pivot.time, pivot.price, pivot.confirmed_at, members[0].time,
                max(member.available_at for member in members), witness,
            ))
        observed[label] = stream
    return observed


def _matching_frontiers(intervals, series, candle_ids, step):
    """Scan each shared session prefix once, stopping at the first unavailable slot."""
    result = {}
    for start, end in intervals:
        digest = sha256()
        availability = start
        prefix = []
        for stamp in range(start, end, step):
            left, right = series[0].get(stamp), series[1].get(stamp)
            if left is None or right is None:
                break
            availability = max(availability, left[1], right[1])
            digest.update(bytes.fromhex(candle_ids[0][stamp] + candle_ids[1][stamp]))
            prefix.append((availability, digest.hexdigest()))
        result[start] = prefix
    return result


def _matches(primary, comparison, intervals, frontiers, candle_ids, policy, step):
    """Freeze matches in primary UTC order, partitioned by declared market session."""
    window = policy.alignment_bars * step
    margin = policy.period * step
    times = [pivot.time for pivot in comparison]
    for start, _ in intervals:
        # A missing open candle leaves earlier potential pivots unresolved. An
        # independent scheduled session may still establish its own sequence.
        prefix = frontiers[start]
        complete_end = start+len(prefix)*step
        previous = None
        next_peer = bisect_left(times, start + margin)
        lo = bisect_left(primary, start + margin + window, key=lambda pivot: pivot.time)
        hi = bisect_left(primary, complete_end-margin-window, lo=lo,
                         key=lambda pivot: pivot.time)
        for pivot in primary[lo:hi]:
            first, last = pivot.time-window-margin, pivot.time+window+margin+step
            frontier, prefix_digest = prefix[(last-start)//step-1]
            match_index = nearest_peer(times, pivot.time, window, next_peer)
            if match_index is None:
                continue
            peer = comparison[match_index]
            next_peer = match_index + 1
            witness = fingerprint([[ids[stamp] for stamp in range(first, last, step)] for ids in candle_ids])
            current = Match(pivot, peer, frontier, first, last, witness, prefix_digest, start)
            if previous:
                yield previous, current
            previous = current


def _transition(state, observed_start, observed_end, available, barrier, witness):
    return {"state": state, "start": observed_start, "end": observed_end, "available": available,
            "barrier": barrier, "witness": witness}


def _lifecycle(match, laggard, reference, sign, policy, step, slots, series, candle_ids, unrecorded, charge):
    tolerance = policy.tolerance_bps
    candidate_at = match.closure
    own = series[laggard]
    laggard_pivot = (match.primary, match.comparison)[laggard]
    extreme = "high" if sign == 1 else "low"

    def breaks(bar):
        return signed_change(getattr(bar, extreme), reference, sign) > tolerance

    offset = bisect_left(slots, laggard_pivot.time + step)
    # Delayed matching may leave already-closed laggard bars between the
    # nominal matching interval and activation. Require those observations too.
    activation_members = []
    end = match.end
    while offset < len(slots) and slots[offset] + step <= candidate_at:
        charge(1)
        stamp = slots[offset]
        member = own.get(stamp)
        if member is None or breaks(member[0]):
            return [], ()
        candidate_at = max(candidate_at, member[1])
        end = max(end, stamp+step)
        activation_members.append(candle_ids[laggard][stamp])
        offset += 1
    activation = fingerprint([match.evidence_digest, activation_members])
    changes = [_transition("SMT_CANDIDATE", match.start, end, candidate_at, candidate_at, activation)]
    offset = bisect_left(slots, candidate_at)
    straddling_start = slots[offset-1] if offset and slots[offset-1] < candidate_at < slots[offset-1]+step else None
    straddling = own.get(straddling_start)
    ambiguous_wick = (straddling_start is not None and straddling_start+step <= policy.cutoff and
                      (straddling is None or breaks(straddling[0])))
    monitored = slots[offset:offset+policy.expiry_bars]
    charge(len(monitored))
    # Only the laggard must have a qualifying post-activation wick. Order
    # breaches by actual availability, then UTC observation time.
    breaches = [(own[stamp][1], stamp) for stamp in monitored
                if stamp in own and breaks(own[stamp][0])]
    breach = min(breaches, default=None)
    shared = []
    for stamp in monitored[:policy.confirmation_bars]:
        pair = [feed.get(stamp) for feed in series]
        if any(member is None for member in pair):
            break
        shared.append(pair)
    expiry = (max(own[stamp][1] for stamp in monitored)
              if len(monitored) == policy.expiry_bars and all(stamp in own for stamp in monitored) else None)
    if len(shared) == policy.confirmation_bars and (not ambiguous_wick or not shared):
        available = max([candidate_at, *(time for pair in shared for _, time in pair),
                         straddling[1] if shared and straddling else 0])
        if (breach is None or available < breach[0]) and (expiry is None or available <= expiry):
            end = monitored[len(shared)-1]+step if shared else end
            start = monitored[0] if shared else match.start
            witness = fingerprint([activation, [[ids[bar.time] for ids, (bar, _) in zip(candle_ids, pair)]
                                                for pair in shared],
                                   candle_ids[laggard][straddling_start] if shared and straddling else None])
            changes.append(_transition("SMT_CONFIRMED", start, end, available, candidate_at, witness))
    if breach:
        available, stamp = breach
        changes.append(_transition("SMT_INVALIDATED", stamp, stamp+step, available, candidate_at,
                                   candle_ids[laggard][stamp]))
    elif expiry is not None:
        witness = fingerprint([candle_ids[laggard][stamp] for stamp in monitored])
        changes.append(_transition("EXPIRED", monitored[0], monitored[-1]+step,
                                   expiry, candidate_at, witness))
    boundaries = []
    for stamp in monitored:
        deadline = stamp+step
        observed = own.get(stamp)
        recovery = observed[1] if observed else None
        if deadline <= policy.cutoff and (recovery is None or recovery > deadline):
            reason = ("UNRECORDED_LAGGARD_ARRIVAL" if stamp in unrecorded[laggard]
                      else "MISSING_LAGGARD_OBSERVATION")
            boundaries.append(SmtUncertainty(deadline, reason, recovery))
    if straddling_start is not None and straddling_start+step <= policy.cutoff:
        deadline = straddling_start+step
        recovery = straddling[1] if straddling and not breaks(straddling[0]) else None
        if recovery is None or recovery > deadline:
            boundaries.append(SmtUncertainty(deadline, "AMBIGUOUS_ACTIVATION_WICK", recovery))
    return changes, boundaries


def _timed_event(event_id, state, start, end, available, dependency, cutoff, *, refs=(), reason=None, authority=AUTHORITY):
    temporal = TemporalEvidence(start, end, available, dependency, cutoff, True, authority)
    return {"id": event_id, "state": state, "algorithm_version": ALGORITHM_VERSION,
            "available_at": available, "confirmation_time": end,
            "timing": temporal.contract(reason=reason, references=refs)}


def _producer(coordinator, history, scope, policy, step, configuration, series, candle_ids):
    view = coordinator.evidence_for("smt-pivots", history["rows"], scope, configuration)
    streams = coordinator.run("smt_pivots", view.snapshot, history["rows"],
                              lambda: _pivots(series, policy.period, step, candle_ids))
    for stream in streams.values():
        for pivot in stream:
            event = _timed_event(pivot.id, "CONFIRMED", pivot.observation_start, pivot.nominal_confirmation,
                                 pivot.available_at, pivot.available_at, policy.cutoff, reason="ELIGIBLE",
                                 authority=scope["authority"])
            event["pivot"] = asdict(pivot)
            view.publish(event, "smt_pivot", scope, configuration, predicate="FORMATION")
    return view, streams


def _coverage(feed, rows, slots, cutoff, step):
    closed = [stamp for stamp in slots if stamp+step <= cutoff]
    present = {bar.time for bar, _ in rows}
    recorded = set(_usable(rows))
    unknown = len(present - recorded)
    watermark = max(recorded, default=-1)
    missing = [stamp for stamp in closed if stamp not in present]
    interior = sum(stamp < watermark for stamp in missing)
    status = ("UNVERIFIED_AVAILABILITY" if unknown else "MISSING_OPEN_CANDLES" if interior else
              "SCHEDULED_CLOSURE" if not slots else
              "PENDING_SYNCHRONIZATION" if missing or len(closed) != len(slots) else "COMPLETE")
    return {"status": status, "expected_closed": len(closed), "missing_closed": len(missing),
            "known_open_gaps": interior, "pending_closed": len(missing)-interior,
            "unrecorded_observations": unknown, "symbol": feed.symbol}


def evaluate_strict_smt(request, loader=load_reference, *, coordinator=None):
    feeds = (request.primary, request.comparison)
    step = FRAME_MIN[request.primary.timeframe]*60
    rule_keys = {"relationship", "period", "alignment_bars", "tolerance_bps", "confirmation_bars", "expiry_bars"}
    simulated = request.availability_mode == SIMULATION_MODE
    authority = SIMULATION_AUTHORITY if simulated else RECORDED_AUTHORITY
    rules = {**request.model_dump(include=rule_keys), "algorithm_version": ALGORITHM_VERSION,
             "timing_policy_version": TIMING_VERSION, "evidence_reference_version": REFERENCE_VERSION,
             "lifecycle_version": LIFECYCLE_VERSION, "availability_mode": request.availability_mode,
             "availability_mode_version": MODE_VERSIONS[request.availability_mode],
             "delay_seconds": [feed.delay_seconds for feed in feeds] if simulated else None}
    config = fingerprint(rules)
    reply = {**rules, "cutoff": request.cutoff, "events": [], "current": [], "sources": {}, "alerts": []}

    def unavailable(reason, status="UNAVAILABLE"):
        if request.cursor:
            raise StaleStructureCursor("Strict SMT snapshot unavailable. Restart pagination.")
        return {**reply, "status": status, "reason": reason}

    a, b = feeds
    if (a.source, a.price_basis, a.timeframe, a.calendar) != (b.source, b.price_basis, b.timeframe, b.calendar):
        return unavailable("INCOMPATIBLE_PROVIDER_PRICE_BASIS_TIMEFRAME_OR_CALENDAR")
    start, end = max(a.start, b.start), min(a.end, b.end)
    if start >= end:
        return unavailable("NO_SHARED_UTC_WINDOW")
    intervals = open_intervals(start, end, a.calendar)
    feed_intervals = [open_intervals(feed.start, feed.end, feed.calendar) for feed in feeds]
    if intervals is None or any(bounds is None or any(left % step or right % step for left, right in bounds)
                                for bounds in feed_intervals):
        return unavailable("UNKNOWN_OR_UNALIGNED_CALENDAR", "UNKNOWN_COVERAGE")
    slots = [stamp for left, right in intervals for stamp in range(left, right, step)]
    shared_slots = set(slots)
    coordinator = coordinator or AnalysisCoordinator(request, loader)
    histories = []
    scopes = {}
    identities = {}
    for side, feed, bounds in zip(SIDES, feeds, feed_intervals):
        query = ArchiveRequest(**feed.model_dump(), cutoff=request.cutoff, availability_mode=request.availability_mode)
        history = coordinator.history(query, feed.start, feed.end, step)
        evidence = history["evidence"]
        scope = {**feed.scope(), "window": [feed.start, feed.end], "manifest": feed.manifest(),
                 "authority": evidence.get("context_authority"), "arrival_manifest": evidence.get("arrival_manifest"),
                 "availability_policy": evidence.get("availability_policy"),
                 "delay_seconds": evidence.get("delay_seconds")}
        scopes[side] = scope
        reply["sources"][side] = {**scope, "retrieval": evidence.get("retrieval")}
        if evidence.get("context_authority") == "CHECKSUM_VERIFIED_UNRECORDED_ARCHIVE" and not evidence.get("status"):
            # Preserve unverified coverage diagnostics without admitting any pivots.
            pass
        elif evidence.get("context_authority") != authority or evidence.get("status"):
            return unavailable(evidence.get("reason", "UNVERIFIED_SOURCE"),
                               evidence.get("status", "UNAVAILABLE"))
        if sorted((part["id"], part["sha256"].lower()) for part in evidence["context_partitions"]) != feed.manifest():
            return unavailable("INCOMPLETE_FROZEN_MANIFEST", "SOURCE_INTEGRITY_FAILURE")
        permitted = {stamp for left, right in bounds for stamp in range(left, right, step)}
        if any(bar.time not in permitted for bar, _ in history["rows"]):
            return unavailable("CANDLE_OUTSIDE_DECLARED_CALENDAR", "INCOMPATIBLE_COVERAGE")
        identities[side] = {key: value for key, value in feed.scope().items() if key != "dataset_id"}
        reply["sources"][side]["coverage"] = _coverage(feed, history["rows"], sorted(permitted), request.cutoff, step)
        histories.append({**history, "rows": tuple(entry for entry in history["rows"] if entry[0].time in shared_slots)})

    series = [_usable(history["rows"]) for history in histories]
    unrecorded = [{bar.time for bar, _ in history["rows"] if bar.available_at is None} for history in histories]
    candle_ids = [{stamp: fingerprint(bar.model_dump(mode="json")) for stamp, (bar, _) in feed.items()}
                  for feed in series]
    pair_id = fingerprint(identities)
    pair_scope = {"source": "SMT_PAIR", "symbol": pair_id, "feed_id": pair_id, "timeframe": a.timeframe,
                  "price_basis": a.price_basis, "dataset_id": None}
    pivot_config = fingerprint([ALGORITHM_VERSION, request.period, step])
    producers = [_producer(coordinator, history, scopes[side], request, step, pivot_config, feed, ids)
                 for side, history, feed, ids in zip(SIDES, histories, series, candle_ids)]
    left, right = producers
    view = coordinator.evidence_for("smt", histories[0]["rows"],
                                    {**pair_scope, "participants": scopes}, config, native=histories[1]["rows"])
    view.pin_smt_producers(left[0], right[0], scopes)
    frontier_started = perf_counter()
    frontiers = _matching_frontiers(intervals, series, candle_ids, step)
    frontier_slots = sum(map(len, frontiers.values()))
    coordinator.charge_work(frontier_slots)
    coordinator.reserve(frontier_slots * 160)
    frontier_ms = (perf_counter()-frontier_started)*1000
    page = EventPage("strict-smt-v2", request.limit, request.cursor)
    events = []
    unresolved_activations = 0
    post_window = open_intervals(end, max(end, request.cutoff), a.calendar) if request.cutoff > end else []
    exhaustion = next((left+step for left, right in post_window or () if right-left >= step), None)
    for label in left[1]:
        sign = 1 if label == "Swing high" else -1
        direction = "bearish" if sign == 1 else "bullish"
        tol = request.tolerance_bps
        for previous, match in _matches(left[1][label], right[1][label], intervals, frontiers, candle_ids, request, step):
            leader = divergence_leader(
                signed_change(match.primary.price, previous.primary.price, sign),
                signed_change(match.comparison.price, previous.comparison.price, sign), tol)
            if leader is None:
                continue
            laggard = int(leader == "primary")
            reference = (previous.primary, previous.comparison)[laggard].price
            changes, boundaries = _lifecycle(match, laggard, reference, sign, request, step, slots, series,
                                             candle_ids, unrecorded, coordinator.charge_work)
            if not changes:
                unresolved_activations += 1
                continue
            if exhaustion is not None:
                boundaries.append(SmtUncertainty(max(exhaustion, changes[0]["available"]),
                                                 "FROZEN_EVIDENCE_WINDOW_ENDED"))
            pivots_by_side = ((previous.primary, match.primary), (previous.comparison, match.comparison))
            refs = [producer[0].records[pivot.id].reference
                    for producer, side_pivots in zip(producers, pivots_by_side) for pivot in side_pivots]
            keys = [f"{side}:{pivot.id}" for side, side_pivots in zip(SIDES, pivots_by_side) for pivot in side_pivots]
            event_id = fingerprint([pair_id, config, direction, leader,
                                    previous.evidence_digest, match.evidence_digest, previous.closure, match.closure,
                                    [pivot.id for pair in pivots_by_side for pivot in pair]])
            prefix = {"start": match.prefix_start, "previous_end": previous.end, "end": match.end,
                      "previous_digest": previous.prefix_digest, "digest": match.prefix_digest}
            matching_id = fingerprint([event_id, "MATCH_CLOSED", prefix])
            matching = _timed_event(matching_id, "MATCH_CLOSED", match.prefix_start, match.end, match.closure,
                                    match.closure, request.cutoff, refs=keys, reason="RESEARCH_ONLY", authority=authority)
            matching["coverage_prefix"] = prefix
            view.publish(matching, "smt_match", pair_scope, config, family=event_id,
                         predicate="FORMATION", lineage=True, producer_references=refs)
            states = []
            for change in changes:
                state, available = change["state"], change["available"]
                revision = fingerprint([event_id, states[-1]["id"] if states else None, change])
                eligible = state == "SMT_CONFIRMED"
                predicate = "FORMATION" if state == "SMT_CANDIDATE" or eligible and not request.confirmation_bars else "WHOLE_CANDLE"
                event = _timed_event(revision, state, change["start"], change["end"], available, change["barrier"],
                                     request.cutoff, refs=keys, authority=authority,
                                     reason=("ELIGIBLE" if eligible else "RESEARCH_ONLY") if predicate == "FORMATION"
                                     else None if eligible else "RESEARCH_ONLY")
                event.update(market_event_id=event_id, pair_id=pair_id, direction=direction, leader=leader,
                             confirmation_bars=request.confirmation_bars, witness=change["witness"])
                links = {"match": matching["evidence_reference"]}
                if states:
                    links["previous"] = states[-1]["evidence_reference"]
                view.publish(event, "smt", pair_scope, config, family=event_id, active=eligible,
                             predicate=predicate, lineage=not eligible, producer_references=refs, smt_links=links)
                states.append(event)
            view.record_smt_uncertainty(event_id, boundaries)
            confirmed = next((state for state in states if state["state"] == "SMT_CONFIRMED"), None)
            historical = bool(confirmed and strategy_eligible(confirmed, request.cutoff, registry=view,
                                                               purpose=request.availability_mode))
            current = historical and view.current_at(confirmed["evidence_reference"], request.cutoff,
                                                      purpose=request.availability_mode)
            current_state, reason = view.smt_state(event_id, request.cutoff)
            events.append({"id": event_id, "direction": direction, "leader": leader,
                           "pivots": {side: {"previous": asdict(pair[0]), "current": asdict(pair[1])}
                                      for side, pair in zip(SIDES, pivots_by_side)},
                           "matching_window": {"status": "COMPLETE", "start": match.start, "end": match.end,
                                               "available_at": match.closure, "previous_available_at": previous.closure,
                                               "attestation": matching["evidence_reference"], "coverage_prefix": prefix},
                           "candidate_at": states[0]["available_at"],
                           "confirmed_at": confirmed["available_at"] if confirmed else None,
                           "historically_eligible": historical and not simulated,
                           "historically_simulation_eligible": historical and simulated,
                           "current_simulation_eligible": current and simulated,
                           "current_eligible": current and not simulated, "current_reason": reason,
                           "current_state": current_state, "revisions": states})
    for event in sorted(events, key=lambda event: (event["candidate_at"], event["id"])):
        page.append(event)
    current = [{"id": event["id"], "state": event["current_state"], "current_eligible": event["current_eligible"],
                "current_simulation_eligible": event["current_simulation_eligible"],
                "reason": event["current_reason"], "confirmed_at": event["confirmed_at"], "direction": event["direction"]}
               for event in events if event["current_state"] in {"SMT_CANDIDATE", "SMT_CONFIRMED", "UNCERTAIN"}]
    if len(current) > 500:
        raise ResourceLimit("Strict SMT current-state evidence exceeds its delivery budget")
    coverage = [source["coverage"]["status"] for source in reply["sources"].values()]
    reason = ("UNVERIFIED_AVAILABILITY" if "UNVERIFIED_AVAILABILITY" in coverage else
              "MATCHING_OR_ACTIVATION_COVERAGE_INCOMPLETE" if unresolved_activations or
              "MISSING_OPEN_CANDLES" in coverage else None)
    matching = ("INCOMPLETE" if reason else "SCHEDULED_CLOSURE" if not slots else
                "PENDING_MATCH" if "PENDING_SYNCHRONIZATION" in coverage else "EVALUATED")
    return {**reply, "status": "PARTIAL" if reason else "EVALUATED", "reason": reason,
            "pair_id": pair_id, "calculation_id": view.calculation_id, "snapshot": view.snapshot,
            "matching_status": matching,
            "delivery": page.finish(view.snapshot), "events": list(page.events), "current": current,
            "resources": {**coordinator.counts, **coordinator.archive.counts, **view.counts,
                          "matching_frontier_slots": frontier_slots, "matching_frontier_ms": round(frontier_ms, 3),
                          "estimated_retained_bytes": coordinator.reserved_bytes}}
