"""Bounded and independently resolved historical context for previous-period levels."""
from datetime import UTC, datetime

from .db import rows
from .indicator_engine import EvidenceBar
from .market import demo_reference_candles, mt5_reference_candles
from .storage import read_partition

MAX_BARS = 10_080
MAX_PARTITIONS = 50
MAX_DECODED = 30_000


def load_reference(request, start, end, step, *, archive_limits=None):
    measurements = {"requested_candles": (end-start)//step, "decoded_candles": 0,
                    "partitions_examined": 0}
    evidence = {"context_authority": "UNAVAILABLE", "context_partitions": [], "retrieval": measurements}

    def refused(status, reason):
        return {"rows": [], "evidence": {**evidence, "status": status, "reason": reason}}

    if measurements["requested_candles"] > MAX_BARS:
        return refused("HISTORY_LIMIT", "REFERENCE_WINDOW_TOO_LARGE")
    try:
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
            if request.frozen and not manifest:
                return refused("HISTORY_UNAVAILABLE", "FROZEN_PARTITION_MANIFEST_REQUIRED")
            start_iso = datetime.fromtimestamp(start, UTC).isoformat()
            end_iso = datetime.fromtimestamp(end, UTC).isoformat()
            selection = " AND id IN (" + ",".join("?" for _ in manifest) + ")" if request.frozen else ""
            max_parts,max_decoded=archive_limits or (MAX_PARTITIONS,MAX_DECODED)
            parts = rows("""SELECT * FROM datasets
                WHERE provider=? AND symbol=? AND timeframe=? AND kind='candles'
                AND feed_id=? AND price_basis=? AND last_time>=? AND first_time<?""" + selection +
                " ORDER BY first_time, id LIMIT ?", (provider, request.symbol, request.timeframe,
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
                decoded = read_partition(part)
                measurements["decoded_candles"] += len(decoded)
                if len(decoded) != part["rows_count"]:
                    return refused("SOURCE_INTEGRITY_FAILURE", "PARTITION_ROW_COUNT_MISMATCH")
                bars.extend(decoded)
            evidence["context_partitions"] = [{"id": part["id"], "sha256": part["sha256"]} for part in parts]
            evidence["context_authority"] = "CHECKSUM_VERIFIED_ARCHIVE"
        else:
            return refused("HISTORY_UNAVAILABLE", "NO_COMPATIBLE_HISTORICAL_SOURCE")
        observed = {}
        for raw in bars:
            if not start <= raw["time"] < end:
                continue
            bar = EvidenceBar.model_validate(raw)
            available = max(bar.time + step, bar.available_at or 0)
            if bar.time in observed:
                return refused("SOURCE_INTEGRITY_FAILURE", "DUPLICATE_ARCHIVE_CANDLE")
            observed[bar.time] = (bar, available)
        ordered = [observed[stamp] for stamp in sorted(observed)]
        if any(right[0].time < left[0].time + step for left, right in zip(ordered, ordered[1:])):
            return refused("SOURCE_INTEGRITY_FAILURE", "OVERLAPPING_ARCHIVE_CANDLE")
        return {"rows": [(bar, available) for bar, available in ordered if bar.closed and available <= request.cutoff],
                "evidence": evidence}
    except (OSError, RuntimeError, ValueError):
        if request.source.endswith("_SAVED"):
            return refused("SOURCE_INTEGRITY_FAILURE", "ARCHIVE_READ_FAILED")
        return refused("HISTORY_UNAVAILABLE", "HISTORICAL_SOURCE_READ_FAILED")
