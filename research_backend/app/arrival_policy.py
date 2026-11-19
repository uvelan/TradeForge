"""Canonical market evidence admission and public availability modes."""
from typing import Literal

LEGACY_MODE = "LEGACY_RESEARCH"
RECORDED_MODE = "STRICT_RECORDED"
SIMULATION_MODE = "HISTORICAL_SIMULATION"
LEGACY_RECORDED_ARRIVAL = "SERVER_RECORDED_ARRIVAL_V1"
RECORDED_ARRIVAL = "SERVER_RECORDED_ARRIVAL_V2"
PUBLICATION_POLICY = "POST_COMMIT_RECEIPT_WITNESS_V1"
VERIFIED_UTC = "UTC_EPOCH_VERIFIED_V1"
UNKNOWN_TIMESTAMP_DOMAIN = "UNVERIFIED_TIMESTAMP_DOMAIN"
AGGREGATION_POLICY = "RECORDED_M1_TO_M5_V2"
SIMULATED_ARRIVAL = "SIMULATED_NOMINAL_CLOSE_PLUS_DELAY_V1"
CLOCK_POLICY = "LOCAL_UTC_MONOTONIC_GUARD_V1"
RECORDED_AUTHORITY = "CHECKSUM_VERIFIED_ARCHIVE"
SIMULATION_AUTHORITY = "VERIFIED_ARCHIVE_SIMULATED_AVAILABILITY"
SIMULATION_ELIGIBLE = "SIMULATION_ONLY_ELIGIBLE"
MODE_VERSIONS = {"LEGACY_RESEARCH":1,"STRICT_RECORDED":2,"HISTORICAL_SIMULATION":1}
AvailabilityMode = Literal["LEGACY_RESEARCH", "STRICT_RECORDED", "HISTORICAL_SIMULATION"]


def availability_mode(request):
    return getattr(request, "availability_mode", None) or (
        RECORDED_MODE if getattr(request, "timing_policy", None) == "STRICT_CAUSAL" else LEGACY_MODE
    )


def to_seconds(milliseconds):
    return (milliseconds + 999) // 1000


def admitted_arrival(row, step, *, mode, attestation=None, delay=0):
    """None grants descriptive OHLC only. Simulation never reads a stored arrival claim."""
    nominal = row["time"] + step
    supplied = row.get("available_at")
    if supplied is not None and (type(supplied) is not int or supplied < nominal):
        raise ValueError("INVALID_RECORDED_ARRIVAL")
    if mode == SIMULATION_MODE:
        return nominal + delay if row.get("closed", True) is True else None
    if mode != RECORDED_MODE or attestation is None:
        return None
    observed, persisted = row.get("observed_ms"), row.get("persisted_ms")
    witnessed = row.get("receipt_committed_by_ms")
    domain = row.get("timestamp_policy")
    if (row.get("availability_policy") != RECORDED_ARRIVAL or row.get("clock_quality") != CLOCK_POLICY
            or row.get("session_id") not in attestation["sessions"]
            or row.get("witness_session_id") not in attestation["sessions"] or row.get("closed") is not True
            or row.get("nominal_close") != nominal or type(observed) is not int or type(persisted) is not int
            or row.get("publication_policy") != PUBLICATION_POLICY
            or type(witnessed) is not int or not nominal*1000 <= observed <= persisted <= witnessed
            or to_seconds(witnessed) != supplied
            or (domain != VERIFIED_UTC and row.get("aggregation_policy") != AGGREGATION_POLICY)
            or (domain == VERIFIED_UTC and row.get("raw_provider_time") != row["time"])
            or type(row.get("sequence")) is not int):
        raise ValueError("INVALID_CAPTURE_ATTESTATION")
    return supplied
