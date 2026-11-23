"""Bounded and independently resolved historical context for previous-period levels."""
from datetime import UTC, datetime
import json

from pydantic import ValidationError

from .analysis_evidence import ResourceLimit
from .archive_provenance import SYNTHETIC_MANIFESTS, SYNTHETIC_SESSIONS
from .arrival_policy import (CLOCK_POLICY, LEGACY_MODE, LEGACY_RECORDED_ARRIVAL, MODE_VERSIONS, PUBLICATION_POLICY,
                             RECORDED_ARRIVAL, RECORDED_MODE, SIMULATED_ARRIVAL,
                             SIMULATION_AUTHORITY, SIMULATION_MODE, admitted_arrival, availability_mode)
from .db import one, rows
from .indicator_coverage import open_intervals
from .indicator_engine import EvidenceBar
from .market import demo_reference_candles, mt5_reference_candles
from .storage import read_partition

MAX_BARS = 10_080
MAX_PARTITIONS = 50
MAX_DECODED = 30_000


def normalized_bar(raw, step, mode, attestation, delay):
    bar = EvidenceBar.model_validate(dict(raw))
    if mode == LEGACY_MODE:
        return bar
    arrival = admitted_arrival(raw,step,mode=mode,attestation=attestation,delay=delay)
    return bar.model_copy(update={"available_at":arrival})


def load_reference(request, start, end, step, *, archive_limits=None, archive_session=None, include_observations=False):
    measurements = {"requested_candles": (end-start)//step, "decoded_candles": 0,
                    "partitions_examined": 0}
    evidence = {"context_authority": "UNAVAILABLE", "context_partitions": [], "retrieval": measurements}
    mode = availability_mode(request)
    delay = getattr(request, "delay_seconds", 0)
    manifest_id = getattr(request, "arrival_manifest", None)
    native = getattr(request, "native_arrival_manifests", {})
    manifest_id = native.get(request.timeframe, manifest_id)

    def refused(status, reason):
        return {"rows": [], "evidence": {**evidence, "status": status, "reason": reason}}

    if measurements["requested_candles"] > MAX_BARS:
        return refused("HISTORY_LIMIT", "REFERENCE_WINDOW_TOO_LARGE")
    try:
        if mode != LEGACY_MODE and request.source not in {"MT5_SAVED", "DUKASCOPY_SAVED"}:
            return refused("UNVERIFIED_AVAILABILITY", "FROZEN_VERIFIED_ARCHIVE_REQUIRED")
        if request.source == "DEMO" and request.feed_id == "demo-v1" and request.price_basis == "SIMULATED":
            bars = demo_reference_candles(request.symbol, request.timeframe, start, end)
            measurements["decoded_candles"] = len(bars)
            evidence["context_authority"] = "SERVER_SYNTHETIC"
        elif request.source == "MT5" and not request.frozen and request.feed_id and request.price_basis:
            if end > datetime.now(UTC).timestamp():
                return refused("HISTORY_UNAVAILABLE", "BROKER_REFERENCE_PERIOD_STILL_OPEN")
            provenance = {}
            bars = mt5_reference_candles(request.symbol, request.timeframe, start, end, provenance)
            measurements["decoded_candles"] = len(bars)
            if len(bars) > MAX_BARS:
                return refused("HISTORY_LIMIT", "BROKER_READ_BUDGET_EXCEEDED")
            if (provenance.get("feed_id"), provenance.get("price_basis")) != (request.feed_id, request.price_basis):
                return refused("SOURCE_MISMATCH", "BROKER_FEED_OR_PRICE_BASIS_CHANGED")
            evidence["context_authority"] = "SERVER_MT5_READ"
        elif request.source in {"MT5_SAVED", "DUKASCOPY_SAVED"} and request.feed_id and request.price_basis:
            provider = request.source.removesuffix("_SAVED")
            manifest = {item.id: item.sha256.lower() for item in request.partitions}
            if mode != LEGACY_MODE and len(manifest) != len(request.partitions):
                return refused("SOURCE_INTEGRITY_FAILURE","DUPLICATE_PARTITION_MANIFEST_ENTRY")
            calendar = getattr(request, "calendar", None)
            registered = {}
            kind = "candles"
            if mode == RECORDED_MODE and manifest_id and request.frozen:
                sealed = one("SELECT payload FROM arrival_manifests WHERE id=?", (manifest_id,))
                if not sealed:
                    return refused("UNVERIFIED_AVAILABILITY", "UNKNOWN_CAPTURE_MANIFEST")
                sealed = json.loads(sealed["payload"])
                if manifest_id in SYNTHETIC_MANIFESTS or SYNTHETIC_SESSIONS.intersection(sealed.get("sessions",[])):
                    return refused("UNVERIFIED_AVAILABILITY", "QUARANTINED_SYNTHETIC_CAPTURE")
                if sealed.get("availability_policy") == LEGACY_RECORDED_ARRIVAL:
                    return refused("UNVERIFIED_AVAILABILITY", "LEGACY_RECEIPT_HAS_NO_DURABLE_PUBLICATION_PROOF")
                scope = {"provider":provider,"symbol":request.symbol,"feed_id":request.feed_id,
                         "timeframe":request.timeframe,"price_basis":request.price_basis}
                if (any(sealed.get(key) != value for key,value in scope.items())
                    or not sealed["start"] <= start < end <= sealed["end"] or request.cutoff > sealed["cutoff"]
                    or sealed.get("availability_policy") != RECORDED_ARRIVAL or sealed["clock_policy"] != CLOCK_POLICY
                    or sealed.get("publication_policy") != PUBLICATION_POLICY or sealed.get("manifest_version") != 2
                    or calendar is not None and sealed["calendar"] != calendar.model_dump()):
                    return refused("UNVERIFIED_AVAILABILITY", "CAPTURE_MANIFEST_SCOPE_MISMATCH")
                pinned = {part["id"]:part["sha256"] for part in sealed["partitions"]}
                if manifest and manifest != pinned and request.timeframe not in native:
                    return refused("SOURCE_INTEGRITY_FAILURE", "PARTITION_MANIFEST_MISMATCH")
                manifest = pinned
                ids = ",".join("?" for _ in manifest)
                for receipt in rows(f"SELECT * FROM arrival_partitions WHERE dataset_id IN ({ids})", tuple(manifest)):
                    if (receipt["sha256"] != manifest[receipt["dataset_id"]] or receipt["policy"] != RECORDED_ARRIVAL
                            or json.loads(receipt["scope"]) != scope):
                        return refused("SOURCE_INTEGRITY_FAILURE", "CAPTURE_PARTITION_ATTESTATION_MISMATCH")
                    session_ids = set(json.loads(receipt["sessions"]))
                    if not session_ids or not session_ids <= set(sealed["sessions"]):
                        return refused("UNVERIFIED_AVAILABILITY", "UNATTESTED_CAPTURE_SESSION")
                    registered[receipt["dataset_id"]] = {"sessions":session_ids}
                if set(registered) != set(manifest):
                    return refused("UNVERIFIED_AVAILABILITY", "UNATTESTED_CAPTURE_PARTITIONS")
                session_ids = set().union(*(receipt["sessions"] for receipt in registered.values()))
                if len(session_ids) > MAX_DECODED:
                    return refused("HISTORY_LIMIT","CAPTURE_SESSION_BUDGET_EXCEEDED")
                ids_sql = ",".join("?" for _ in session_ids)
                sessions = rows(f"SELECT * FROM arrival_sessions WHERE id IN ({ids_sql})",tuple(sorted(session_ids)))
                if (len(sessions) != len(session_ids) or any(
                    any(row[key] != scope[key] for key in ("provider","symbol","timeframe"))
                    or row["policy"] != RECORDED_ARRIVAL or json.loads(row["calendar"]) != sealed["calendar"]
                    for row in sessions)):
                    return refused("UNVERIFIED_AVAILABILITY","UNATTESTED_CAPTURE_SESSION")
                kind = "arrival_candles"
                evidence["arrival_manifest"] = manifest_id
                evidence["capture_calendar"] = sealed["calendar"]
                evidence["availability_policy"] = RECORDED_ARRIVAL
            if mode == SIMULATION_MODE:
                if not request.frozen or not manifest or calendar is None:
                    return refused("UNVERIFIED_AVAILABILITY", "SIMULATION_REQUIRES_FROZEN_ARCHIVE_AND_CALENDAR")
                intervals = open_intervals(start,end,calendar)
                if intervals is None or any(left % step or right % step for left,right in intervals):
                    return refused("UNKNOWN_COVERAGE", "SIMULATION_CALENDAR_UNKNOWN_OR_UNALIGNED")
                allowed = {stamp for left,right in intervals for stamp in range(left,right,step)}
                evidence.update(availability_policy=SIMULATED_ARRIVAL,delay_seconds=delay,
                                simulation_calendar=calendar.model_dump())
            if mode != LEGACY_MODE:
                evidence["availability_mode"] = mode
                evidence["availability_mode_version"] = MODE_VERSIONS[mode]
            if request.frozen and not manifest:
                return refused("HISTORY_UNAVAILABLE", "FROZEN_PARTITION_MANIFEST_REQUIRED")
            if mode != LEGACY_MODE and request.frozen:
                if len(manifest) > MAX_PARTITIONS:
                    return refused("HISTORY_LIMIT","ARCHIVE_READ_BUDGET_EXCEEDED")
                ids_sql = ",".join("?" for _ in manifest)
                catalogued = rows(f"""SELECT id,sha256 FROM datasets WHERE id IN ({ids_sql})
                    AND provider=? AND symbol=? AND timeframe=? AND kind=? AND feed_id=? AND price_basis=?""",
                    (*manifest,provider,request.symbol,request.timeframe,kind,request.feed_id,request.price_basis))
                if {part["id"]:part["sha256"] for part in catalogued} != manifest:
                    return refused("SOURCE_INTEGRITY_FAILURE","PARTITION_MANIFEST_MISMATCH")
            start_iso = datetime.fromtimestamp(start, UTC).isoformat()
            end_iso = datetime.fromtimestamp(end, UTC).isoformat()
            selection = " AND id IN (" + ",".join("?" for _ in manifest) + ")" if request.frozen else ""
            max_parts,max_decoded=archive_limits or (MAX_PARTITIONS,MAX_DECODED)
            parts = rows("""SELECT * FROM datasets
                WHERE provider=? AND symbol=? AND timeframe=? AND kind=?
                AND feed_id=? AND price_basis=? AND last_time>=? AND first_time<?""" + selection +
                " ORDER BY first_time, id LIMIT ?", (provider, request.symbol, request.timeframe,kind,
                request.feed_id, request.price_basis, start_iso, end_iso,
                *(manifest if request.frozen else ()), max_parts+1))
            measurements["partitions_examined"] = len(parts)
            if not parts:
                return refused("REFERENCE_ABSENT", "NO_MATCHING_ARCHIVE_PARTITIONS")
            if (len(parts) > max_parts or any(part["rows_count"] > 2000 for part in parts)
                    or sum(part["rows_count"] for part in parts) > max_decoded):
                return refused("HISTORY_LIMIT", "ARCHIVE_READ_BUDGET_EXCEEDED")
            bars = []
            for part in parts:
                if part["id"] in manifest and manifest[part["id"]] != part["sha256"]:
                    return refused("SOURCE_INTEGRITY_FAILURE", "PARTITION_MANIFEST_MISMATCH")
                decoded = read_partition(part) if archive_session is None else archive_session.read(part,read_partition)
                measurements["decoded_candles"] += len(decoded)
                if len(decoded) != part["rows_count"]:
                    return refused("SOURCE_INTEGRITY_FAILURE", "PARTITION_ROW_COUNT_MISMATCH")
                if mode == LEGACY_MODE:
                    bars.extend(decoded)
                else:
                    bars.extend((raw,registered.get(part["id"])) for raw in decoded)
            evidence["context_partitions"] = [{"id": part["id"], "sha256": part["sha256"]} for part in parts]
            evidence["context_authority"] = (SIMULATION_AUTHORITY if mode == SIMULATION_MODE else
                "CHECKSUM_VERIFIED_ARCHIVE" if mode == LEGACY_MODE or registered else
                "CHECKSUM_VERIFIED_UNRECORDED_ARCHIVE")
        else:
            return refused("HISTORY_UNAVAILABLE", "NO_COMPATIBLE_HISTORICAL_SOURCE")
        observed = {}
        replay = getattr(archive_session,"replay",None) if mode == SIMULATION_MODE else None
        for member in bars:
            raw, attestation = (member,None) if mode == LEGACY_MODE else member
            if not start <= raw["time"] < end:
                continue
            if mode == SIMULATION_MODE and raw.get("complete_m1") is False:
                # A tick-derived M5 candle can contain observations without having all five minutes.
                continue
            # Keep archive validation and cutoff filtering per request. Only
            # immutable candle normalization can be shared by offline prefixes.
            try:
                bar = (normalized_bar(raw,step,mode,attestation,delay) if replay is None else
                       replay.simulated_bar(raw,step,delay,normalized_bar))
            except ValidationError:
                raise
            except ValueError as error:
                return refused("SOURCE_INTEGRITY_FAILURE",str(error))
            if mode == SIMULATION_MODE and (bar.time not in allowed or
                                            bar.time+step > int(datetime.now(UTC).timestamp())):
                return refused("UNKNOWN_COVERAGE", "SIMULATION_CANDLE_OUTSIDE_COMPLETED_MARKET_SLOT")
            available = max(bar.time + step, bar.available_at or 0)
            if attestation and available > request.cutoff:
                continue
            if attestation:
                evidence["source_corrections"] = evidence.get("source_corrections",0) + int(raw["previous_sequence"] is not None)
                evidence["recovery_observations"] = evidence.get("recovery_observations",0) + int(raw["recovery"])
            if bar.time in observed:
                prior = observed[bar.time]
                if not attestation or prior[2] == raw["sequence"]:
                    return refused("SOURCE_INTEGRITY_FAILURE", "DUPLICATE_ARCHIVE_CANDLE")
                if (available,raw["sequence"]) < (prior[1],prior[2]):
                    continue
            observed[bar.time] = (bar, available, raw.get("sequence",0), raw if include_observations else None)
        ordered = [observed[stamp] for stamp in sorted(observed)]
        if any(right[0].time < left[0].time + step for left, right in zip(ordered, ordered[1:])):
            return refused("SOURCE_INTEGRITY_FAILURE", "OVERLAPPING_ARCHIVE_CANDLE")
        visible = [entry for entry in ordered if entry[0].closed and entry[1] <= request.cutoff]
        result = {"rows": [(bar, available) for bar, available, _, _ in visible], "evidence": evidence}
        if include_observations:
            result["observations"] = {bar.time: raw for bar, _, _, raw in visible}
        return result
    except ResourceLimit:
        raise
    except (OSError, RuntimeError, ValueError):
        if request.source.endswith("_SAVED"):
            return refused("SOURCE_INTEGRITY_FAILURE", "ARCHIVE_READ_FAILED")
        return refused("HISTORY_UNAVAILABLE", "HISTORICAL_SOURCE_READ_FAILED")
