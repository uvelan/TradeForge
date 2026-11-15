"""Backtest trust classification and safe, non-destructive projections."""
import json
from decimal import Decimal
from hashlib import sha256
from typing import Literal, TypedDict

from fastapi import HTTPException

from .db import one
from .market import FRAME_MIN
from .saved_history import read_tick_partitions
from .storage import read_partition, verify_partition
from .tick_replay import replay_breakout, validate_bid_extremes

TrustStatus = Literal["VERIFIED_PROVENANCE", "LEGACY_UNVERIFIED_ORDER", "SOURCE_INTEGRITY_FAILURE",
                      "INCOMPLETE_EXECUTION_DATA", "SYNTHETIC_RESEARCH", "OHLC_RESEARCH"]


class ReplayTrust(TypedDict):
    version: int
    status: TrustStatus
    reason: str
    performance_eligible: bool
    broker_execution_verified: bool
    data_provenance: str
    source_integrity: str
    execution_coverage: str
    simulation: str


def trust(status: TrustStatus, reason: str) -> ReplayTrust:
    dimensions = {
        "VERIFIED_PROVENANCE": ("PINNED_ARCHIVE", "MATCHED", "SUFFICIENT_FOR_MODEL", "VALIDATED_TICK_MODEL"),
        "LEGACY_UNVERIFIED_ORDER": ("LEGACY_ORDER_UNKNOWN", "NOT_ASSESSED", "NOT_ASSESSED", "RESTRICTED"),
        "SOURCE_INTEGRITY_FAILURE": ("UNVERIFIED", "FAILED_OR_UNAVAILABLE", "NOT_ASSESSED", "RESTRICTED"),
        "INCOMPLETE_EXECUTION_DATA": ("PINNED_ARCHIVE", "MATCHED", "INCOMPLETE", "RESTRICTED"),
        "SYNTHETIC_RESEARCH": ("SYNTHETIC", "NOT_ASSESSED", "NOT_APPLICABLE", "ILLUSTRATIVE"),
        "OHLC_RESEARCH": ("CANDLE_ONLY", "NOT_ASSESSED", "NOT_APPLICABLE", "ILLUSTRATIVE"),
    }
    provenance, integrity, coverage, simulation = dimensions[status]
    return {"version": 1, "status": status, "reason": reason, "data_provenance": provenance,
            "source_integrity": integrity, "execution_coverage": coverage, "simulation": simulation,
            "performance_eligible": status == "VERIFIED_PROVENANCE", "broker_execution_verified": False}


def tick_pins(lineage: dict) -> list[dict]:
    return lineage.get("tick_partitions") or ([lineage["tick_partition"]] if "tick_partition" in lineage else [])


def pinned_partitions(pins, *, kind, source, symbol, feed, basis, timeframe=None) -> list[dict]:
    if not isinstance(pins, list) or not 1 <= len(pins) <= 50:
        raise ValueError("Missing or invalid pinned archive lineage")
    datasets = []
    seen = set()
    for pin in pins:
        ident = pin["id"]
        row = one("SELECT * FROM datasets WHERE id=?", (ident,))
        if (row is None or ident in seen or row["sha256"] != pin["sha256"]
                or row["provider"] + "_SAVED" != source or row["symbol"] != symbol
                or row["feed_id"] != feed or row["kind"] != kind or row["price_basis"] != basis
                or row["timeframe"] != timeframe):
            raise ValueError("Pinned archive does not match the recorded feed, instrument, frame or price basis")
        seen.add(ident)
        datasets.append(row)
    return datasets


def validated_tick_sources(snapshot: dict) -> tuple[list[dict], list[dict]]:
    result, market = snapshot["result"], snapshot["market"]
    lineage = result["lineage"]
    common = {"source": result["source"], "symbol": result["symbol"], "feed": market["feed_id"]}
    if not common["feed"] or market["price_basis"] != "BID" or market["partitions"] != lineage["candle_partitions"]:
        raise ValueError("Candle input and recorded lineage disagree")
    ticks = pinned_partitions(tick_pins(lineage), kind="ticks", basis="BID_ASK_UNITS", **common)
    candles = pinned_partitions(lineage["candle_partitions"], kind="candles", basis="BID",
                                timeframe=result["timeframe"], **common)
    return ticks, candles


def verify_historical_execution(snapshot: dict, ticks: list[dict], candle_files: list[dict]) -> None:
    result, market, params = snapshot["result"], snapshot["market"], snapshot["parameters"]
    candles = market["candles"]
    if not candles:
        raise ValueError("Missing recorded candles")
    recorded = {bar["time"]: bar for bar in candles}
    matched = set()
    for dataset in candle_files:
        if not 1 <= dataset["rows_count"] <= 2000:
            raise ValueError("Cannot verify candle partition row count")
        for row in read_partition(dataset, dataset["rows_count"]):
            if row["time"] in recorded:
                if row != recorded[row["time"]]:
                    raise ValueError("Recorded candle differs from its pinned source")
                matched.add(row["time"])
    if matched != recorded.keys():
        raise ValueError("Recorded candles are absent from their pinned sources")
    step = FRAME_MIN[result["timeframe"]] * 60
    quotes = read_tick_partitions(ticks)
    validate_bid_extremes(candles, quotes, step)
    computed = replay_breakout(candles, quotes, step, params["target_r"],
                               slip_bps=Decimal(params["slippage_bps"]),
                               commission_bps=Decimal(params["commission_bps"]),
                               max_quote_gap_seconds=params["max_quote_gap_seconds"])
    for key in ("trades", "unresolved_outcomes", "execution_model", "count"):
        if result.get(key) != computed[key]:
            raise ValueError("Recorded execution does not reproduce from pinned inputs")
    if not result.get("candle_gaps"):
        for key in ("expectancy_r", "expectancy_bounds_r", "total_r", "win_rate"):
            if result.get(key) != computed[key]:
                raise ValueError("Recorded performance does not reproduce from pinned inputs")


def assess_replay(snapshot: dict, *, verified_now: bool = False, defer_tick_verification: bool = False) -> ReplayTrust:
    result, market = snapshot["result"], snapshot["market"]
    if result.get("synthetic") or market.get("synthetic"):
        return trust("SYNTHETIC_RESEARCH", "Synthetic demonstration. Results are illustrative.")
    if "execution_model" not in result:
        return trust("OHLC_RESEARCH", "Candle-only model. Bid/ask execution was not validated.")
    lineage = result.get("lineage") or {}
    try:
        pins = tick_pins(lineage)
        if len(pins) > 1 and lineage.get("tick_ordering") != "PROVENANCE_V1":
            return trust("LEGACY_UNVERIFIED_ORDER", "Historical cross-file ordering is unverified. Rerun with validated archives.")
        model, params = result["execution_model"], snapshot["parameters"]
        gap = params["max_quote_gap_seconds"]
        if (model["version"] != 2 or snapshot.get("engine_version") not in (1, 2)
                or not 1 <= gap <= 60
                or model["max_quote_gap_ms"] != min(FRAME_MIN[result["timeframe"]] * 60, gap) * 1000
                or Decimal(model["slippage_bps"]) != Decimal(params["slippage_bps"])
                or Decimal(model["commission_bps_per_side"]) != Decimal(params["commission_bps"])
                or any(result[key] != params[key] for key in ("source", "symbol", "timeframe"))
                or result["count"] != len(result["trades"])
                or snapshot["engine_version"] == 2 and lineage.get("tick_ordering") != "PROVENANCE_V1"):
            raise ValueError("Unsupported or inconsistent saved execution model")
        ticks, candles = validated_tick_sources(snapshot)
        if not verified_now:
            if snapshot["engine_version"] == 2:
                for dataset in candles if defer_tick_verification else [*ticks, *candles]:
                    verify_partition(dataset)
            else:
                verify_historical_execution(snapshot, ticks, candles)
        if result.get("unresolved_outcomes") or result.get("candle_gaps") or (market.get("coverage") or {}).get("gaps"):
            return trust("INCOMPLETE_EXECUTION_DATA", "Quote or candle coverage is insufficient for validated performance.")
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, ArithmeticError):
        return trust("SOURCE_INTEGRITY_FAILURE", "Pinned archive or recorded execution could not be verified.")
    return trust("VERIFIED_PROVENANCE", "Pinned sources and modelled bid/ask execution verified. Broker execution is unverified.")


def project_replay(snapshot: dict, *, verified_now: bool = False) -> dict:
    classification = assess_replay(snapshot, verified_now=verified_now)
    result = {**snapshot["result"], "trust": classification}
    if classification["status"] not in ("VERIFIED_PROVENANCE", "SYNTHETIC_RESEARCH", "OHLC_RESEARCH"):
        result.update(trades=[], count=0, ambiguous_rate=None,
                      expectancy_r=None, expectancy_bounds_r=None, total_r=None, win_rate=None,
                      data_note=classification["reason"])
    return {**snapshot, "result": result}


def stored_replay(run_id: str) -> tuple[dict, dict]:
    row = one("SELECT * FROM backtest_runs WHERE id=?", (run_id,))
    if row is None:
        raise HTTPException(404, "Backtest run unavailable")
    if sha256(row["payload"].encode()).hexdigest() != run_id:
        raise HTTPException(409, "Saved run checksum mismatch")
    return row, json.loads(row["payload"])
