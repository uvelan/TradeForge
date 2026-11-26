"""Independent provider-row verification for the saved H1 to M5 CRT replay."""

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from operator import itemgetter
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.verify_t10_structure_liquidity import (
    END, PRICE_FIELDS, START, STEP, SYMBOLS, load_provider, utc,
)


ROOT = Path(__file__).resolve().parents[1]
HOUR = 3600
PANES = (("primary", "EURUSD"), ("2", "GBPUSD"))
SCHEDULE = {"kind": "forex_weekly", "timezone": "America/New_York",
            "sunday_open": "17:00", "friday_close": "17:00"}
CONTROL_FEED = "controlled-t10-crt"
QUIET = (103, 104, 102, 103)
REFERENCE = (103, 105, 100, 103)
TERMINAL = {"AMBIGUOUS", "EXPIRED", "INVALIDATED", "OPPOSITE_BOUNDARY_REACHED", "UNCERTAIN"}
STATES = (
    "ACTIVE", "HIGH_SWEEP_CANDIDATE", "LOW_SWEEP_CANDIDATE", "HIGH_SWEPT_RECLAIMED",
    "LOW_SWEPT_RECLAIMED", "EXPANSION_OBSERVED", *sorted(TERMINAL),
)
REASONS = (
    "CLOSED_REFERENCE", "NO_SAME_CANDLE_RECLAIM", "SAME_CANDLE_RECLAIM", "BOTH_BOUNDARIES_SWEPT",
    "POST_RECLAIM_MIDPOINT", "POST_RECLAIM_BOUNDARY", "OPPOSITE_SWEEP_BEFORE_RECLAIM",
    "UNOBSERVED_OPPOSITE_GAP", "CLOSE_BEYOND_INVALIDATION", "NEW_SWEEP_EXTREME", "OUTSIDE_CLOSE",
    "CANDIDATE_AGE", "REFERENCE_AGE", "REFERENCE_CAP_REPLACEMENT",
    "STRADDLING_OBSERVATION", "MISSING_OPEN_CANDLES", "UNVERIFIABLE_CLOSURE",
)


def bar(stamp, prices=QUIET, delay=0):
    return {"time": stamp, "available_at": stamp + STEP + delay,
            **dict(zip(PRICE_FIELDS, (float(value) for value in prices)))}


def browser_controls():
    """Synthetic paired journey with a delayed formation and distinct terminal causes."""
    origin = START + 13 * HOUR
    close = origin + HOUR
    symbols = {}
    for symbol in SYMBOLS:
        high_side = symbol == "EURUSD"
        rally = (103, 106, 103, 104) if high_side else (103, 103, 99, 101)
        midpoint = (104, 104.5, 102, 103) if high_side else (101, 103, 101, 102)
        rows = [bar(stamp, REFERENCE, 17 * int(stamp == close - STEP))
                for stamp in range(origin, close, STEP)]
        rows.extend([bar(close), bar(close + STEP, rally), bar(close + 2 * STEP, midpoint)])
        if not high_side:
            rows.append(bar(close + 3 * STEP, (102, 105, 102, 104)))
        rows.append(bar(close + 4 * STEP, delay=17 * int(high_side)))
        rows.extend(bar(stamp) for stamp in range(close + 5 * STEP, close + 9 * STEP, STEP))
        symbols[symbol] = {
            "bars": rows, "checkpoints": (close - 1, close),
            "reference_start": origin, "reference_end": close, "activation": close + 17,
            "sweep": close + 2 * STEP, "midpoint": close + 3 * STEP,
            "terminal": close + (5 if high_side else 4) * STEP + 17 * int(high_side),
            "terminal_state": "INVALIDATED" if high_side else "OPPOSITE_BOUNDARY_REACHED",
            "terminal_reason": "MISSING_OPEN_CANDLES" if high_side else "POST_RECLAIM_BOUNDARY",
            "previous": close + 2 * STEP if high_side else None,
            "successor": close + 4 * STEP if high_side else None,
            "missing_open_intervals": [{"start": close + 3 * STEP, "end": close + 4 * STEP}] if high_side else [],
        }
    return {"start": origin, "end": close + 9 * STEP, "feed": CONTROL_FEED, "symbols": symbols}


def seed_controls(manifest):
    import pyarrow.parquet as parquet

    from research_backend.app import db, storage

    db.initialize()
    for symbol, lane in manifest["symbols"].items():
        rows = lane["bars"]
        partitions = lane.get("shards", [rows])
        origins, pins = {}, []
        for shard in partitions:
            dataset = storage.write_partition("DUKASCOPY", manifest["feed"], symbol, "candles", "M5", "BID", shard)
            path = storage.MARKET_DIR / dataset["relative_path"]
            assert sha256(path.read_bytes()).hexdigest() == dataset["sha256"], path
            decoded = parquet.ParquetFile(path).read().to_pylist()
            assert decoded == shard, path
            pin = {name: dataset[name] for name in ("id", "sha256")}
            origins.update((row["time"], pin) for row in decoded)
            pins.append(pin)
        lane.update(origins=origins, partitions=pins)
    return manifest


def stamp(day, hour):
    return int(datetime.fromisoformat(f"{day}T{hour}:00+00:00").timestamp())


FRIDAY_CLOSE = stamp("2026-11-13", "22:00")
SUNDAY_OPEN = stamp("2026-11-15", "22:00")


def forex_open(time):
    # All fixtures span the pinned November 13–15 New York standard-time weekend.
    return time < FRIDAY_CLOSE or time >= SUNDAY_OPEN


def gap_proof(left, right):
    left_open, right_open = forex_open(left), forex_open(right)
    spans = [
        {"start": start, "end": end}
        for start, end in ((left + STEP, min(right, FRIDAY_CLOSE)), (max(left + STEP, SUNDAY_OPEN), right))
        if start < end
    ]
    if not (left_open and right_open):
        spans = None
    return {"previous_observed_open": left, "next_observed_open": right, "step_seconds": STEP,
            "schedule": SCHEDULE, "missing_open_intervals": spans}


def reference_grid(rows):
    """Find complete UTC hours and calculate each aggregate directly from its twelve M5 observations."""
    by_hour = defaultdict(dict)
    for row in rows:
        by_hour[row["time"] // HOUR * HOUR][row["time"]] = row
    references = []
    for hour, children in sorted(by_hour.items()):
        opens = tuple(range(hour, hour + HOUR, STEP))
        if tuple(sorted(children)) != opens or not all(map(forex_open, opens)):
            continue
        h = max(children[time]["high"] for time in opens)
        l = min(children[time]["low"] for time in opens)
        if h > l:
            references.append({
                "reference_start": hour, "reference_end": hour + HOUR, "constituents": len(opens),
                "reference_ohlc": [children[opens[0]]["open"], h, l, children[opens[-1]]["close"]],
                "reference_available_at": max(children[time]["available_at"] for time in opens),
                "high": h, "low": l, "width": h - l, "midpoint": (h + l) / 2,
                "children": opens, "reference_frame": "H1", "reference_mode": "aggregated",
            })
    return sorted(references, key=lambda value: (value["reference_available_at"], value["reference_start"]))


def lifecycle(bars, cutoff):
    """Apply the TF-I07 research contract to the captured M5 prefix without producer imports."""
    rows = sorted(
        ({**row, **{key: Decimal(str(row[key])) for key in PRICE_FIELDS}}
         for row in bars if row["available_at"] <= cutoff),
        key=lambda row: (row["available_at"], row["time"]),
    )
    hours = iter(reference_grid(rows))
    upcoming = next(hours, None)
    live, ledger = {}, {}
    last_open = None

    def revise(family, state, reason, observation, confirmation, availability, **witnesses):
        chain = ledger[family]
        revision = {
            "state": state, "reason": reason, "observation_time": observation, "confirmation_time": confirmation,
            "available_at": availability, "revision": len(chain) + 1, **witnesses,
        }
        if state != "AMBIGUOUS":
            revision.update(live[family].get("sweep", {}))
        chain.append(revision)
        if state in TERMINAL:
            del live[family]

    def activate(through):
        nonlocal upcoming
        while upcoming and upcoming["reference_available_at"] <= through:
            family = upcoming["reference_start"]
            close = upcoming["reference_end"]
            arrival = upcoming["reference_available_at"]
            if len(live) == 2:
                revise(next(iter(live)), "EXPIRED", "REFERENCE_CAP_REPLACEMENT", family, close, arrival,
                       replacement_children=upcoming["children"])
            live[family] = {"reference": upcoming, "age": 0}
            ledger[family] = []
            revise(family, "ACTIVE", "CLOSED_REFERENCE", family, close, arrival, reference=upcoming)
            upcoming = next(hours, None)

    for sample in rows:
        at = sample["time"]
        if last_open is not None and at <= last_open:
            continue
        available = sample["available_at"]
        nominal_close = at + STEP

        def observe(family, state, reason, **witnesses):
            prices = [sample[key] for key in PRICE_FIELDS]
            revise(family, state, reason, at, nominal_close, available, observation_ohlc=prices, **witnesses)

        activate(at)
        if last_open is not None and last_open + STEP != at:
            gap = gap_proof(last_open, at)
            absent = gap["missing_open_intervals"]
            if absent or absent is None:
                reason = "MISSING_OPEN_CANDLES" if absent else "UNVERIFIABLE_CLOSURE"
                for family in tuple(live):
                    observe(family, "INVALIDATED", reason, continuity=gap)
        activate(available)
        last_open = at

        for family, tracking in tuple(live.items()):
            ref = tracking["reference"]
            high, low = ref["high"], ref["low"]
            close = sample["close"]
            top, bottom = sample["high"], sample["low"]
            upper_breach, lower_breach = top > high, bottom < low
            midpoint_touch = bottom <= ref["midpoint"] <= top
            breached = upper_breach or lower_breach
            if at < ref["reference_available_at"]:
                if nominal_close > ref["reference_available_at"] and breached:
                    observe(family, "UNCERTAIN", "STRADDLING_OBSERVATION")
                continue

            tracking["age"] += 1
            age = tracking["age"]
            candidate = tracking.get("candidate_at")
            reclaimed = tracking.get("reclaimed_at")
            if candidate is not None and age - candidate >= 3:
                observe(family, "EXPIRED", "CANDIDATE_AGE")
                continue
            if age >= 96:
                observe(family, "EXPIRED", "REFERENCE_AGE")
                continue
            if reclaimed and at < reclaimed:
                if nominal_close > reclaimed and (midpoint_touch or breached):
                    observe(family, "UNCERTAIN", "STRADDLING_OBSERVATION")
                continue
            if upper_breach and lower_breach:
                observe(family, "AMBIGUOUS", "BOTH_BOUNDARIES_SWEPT")
                continue

            sweep = tracking.get("sweep")
            if candidate is not None:
                side = sweep["sweep_side"]
                if (side == "HIGH" and lower_breach) or (side == "LOW" and upper_breach):
                    observe(family, "INVALIDATED", "OPPOSITE_SWEEP_BEFORE_RECLAIM")
                continue
            if reclaimed:
                high_sweep = sweep["sweep_side"] == "HIGH"
                extreme = sweep["sweep_extreme"]
                opposite = low if high_sweep else high
                invalid_close = close > extreme if high_sweep else close < extreme
                gap_through = top < opposite if high_sweep else bottom > opposite
                if invalid_close:
                    observe(family, "INVALIDATED", "CLOSE_BEYOND_INVALIDATION")
                elif gap_through:
                    observe(family, "INVALIDATED", "UNOBSERVED_OPPOSITE_GAP")
                elif bottom <= opposite <= top:
                    observe(family, "OPPOSITE_BOUNDARY_REACHED", "POST_RECLAIM_BOUNDARY")
                elif midpoint_touch and ledger[family][-1]["state"] != "EXPANSION_OBSERVED":
                    observe(family, "EXPANSION_OBSERVED", "POST_RECLAIM_MIDPOINT")
                else:
                    extended = top > extreme if high_sweep else bottom < extreme
                    note = "NEW_SWEEP_EXTREME" if extended else "OUTSIDE_CLOSE" if not low <= close <= high else None
                    if note and tracking.get("extension") != note:
                        tracking["extension"] = note
                        observe(family, ledger[family][-1]["state"], note)
            elif breached:
                side = "HIGH" if upper_breach else "LOW"
                edge, extreme = (high, top) if upper_breach else (low, bottom)
                same_candle = low < close < high
                tracking["sweep"] = {
                    "sweep_side": side, "swept_boundary": edge, "sweep_extreme": extreme,
                    "penetration": abs(extreme - edge), "reclaim_close": close if same_candle else None,
                }
                if same_candle:
                    tracking["reclaimed_at"] = available
                    observe(family, f"{side}_SWEPT_RECLAIMED", "SAME_CANDLE_RECLAIM")
                else:
                    tracking["candidate_at"] = age
                    observe(family, f"{side}_SWEEP_CANDIDATE", "NO_SAME_CANDLE_RECLAIM")
    activate(cutoff)
    return ledger


def counts(known, values):
    observed = Counter(values)
    return {value: observed[value] for value in dict.fromkeys((*known, *observed))}


def genuine_references(source):
    lanes = {}
    for symbol in SYMBOLS:
        bars, digest, path, pin = load_provider(source, symbol)
        lanes[symbol] = {
            "bars": bars, "csv_sha256": digest, "archive_name": path.name,
            "partitions": [pin], "origins": {row["time"]: pin for row in bars}, "archive_path": path,
            "basis": "MODELED_NOMINAL_CLOSE",
        }
    return lanes


def control_cases():
    origin = START + 13 * HOUR
    close = origin + HOUR
    initial = [bar(open_time, REFERENCE) for open_time in range(origin, close, STEP)]
    high = (103, 106, 103, 106)
    low = (102, 102, 99, 99)
    both = (103, 106, 99, 103)
    cases = {"browser-pair": browser_controls()}

    def add(name, rows, expected, *, checkpoints=(), shards=None):
        lane = {"bars": rows, "expected": expected, "checkpoints": checkpoints}
        if shards:
            lane["shards"] = shards
        cases[name] = {"start": rows[0]["time"], "end": max(row["available_at"] for row in rows),
                       "feed": f"{CONTROL_FEED}-{name}", "symbols": {"EURUSD": lane}}

    for side, candidate, other in (("HIGH", high, low), ("LOW", low, high)):
        branch = [*initial, bar(close, candidate), bar(close + STEP)]
        expectation = ["CLOSED_REFERENCE", "NO_SAME_CANDLE_RECLAIM"]
        add(f"{side.lower()}-later-reclaim-and-expiry",
            [*branch, bar(close + 2 * STEP), bar(close + 3 * STEP)],
            [*expectation, "CANDIDATE_AGE"], checkpoints=(close + 2 * STEP, close + 3 * STEP))
        add(f"{side.lower()}-opposite-before-reclaim", [*branch, bar(close + 2 * STEP, other)],
            [*expectation, "OPPOSITE_SWEEP_BEFORE_RECLAIM"], checkpoints=(close + 2 * STEP,))
    add("both-boundaries", [*initial, bar(close, both)], ["CLOSED_REFERENCE", "BOTH_BOUNDARIES_SWEPT"])
    reclaim = (103, 106, 100, 104)
    add("opposite-price-gap", [*initial, bar(close, reclaim), bar(close + STEP, (98, 99, 97, 98))],
        ["CLOSED_REFERENCE", "SAME_CANDLE_RECLAIM", "UNOBSERVED_OPPOSITE_GAP"])
    add("straddling-delayed-reclaim", [*initial, bar(close, reclaim, 17), bar(close + STEP, (103, 104, 100, 102))],
        ["CLOSED_REFERENCE", "SAME_CANDLE_RECLAIM", "STRADDLING_OBSERVATION"], checkpoints=(close + STEP,))
    add("incomplete-reference", [*initial[:-1], bar(close)], [])
    add("straddling-delayed-reference", [*initial[:-1], bar(close - STEP, REFERENCE, 17), bar(close, both)],
        ["CLOSED_REFERENCE", "STRADDLING_OBSERVATION"], checkpoints=(close,))
    for edge, name in ((FRIDAY_CLOSE, "clean-closure"), (FRIDAY_CLOSE - STEP, "mixed-closure")):
        start = FRIDAY_CLOSE - 2 * HOUR
        before = [bar(time, REFERENCE) for time in range(start, edge, STEP)]
        resumed_open = SUNDAY_OPEN + (FRIDAY_CLOSE - edge)
        after = [bar(resumed_open, delay=17)]
        reasons = ["CLOSED_REFERENCE"] + (["MISSING_OPEN_CANDLES"] if edge != FRIDAY_CLOSE else [])
        add(name, before + after, reasons, checkpoints=(resumed_open + STEP,), shards=[before, after])
    saturday = stamp("2026-11-14", "17:00")
    friday = [bar(time, REFERENCE) for time in range(FRIDAY_CLOSE - HOUR, FRIDAY_CLOSE, STEP)]
    add("unverifiable-closure", [*friday, bar(saturday)], ["CLOSED_REFERENCE", "UNVERIFIABLE_CLOSURE"])
    flat_hours = [bar(time, (103, 103, 103, 103)) for time in range(close, close + 96 * STEP, STEP)]
    add("reference-age", [*initial, *flat_hours], ["CLOSED_REFERENCE", "REFERENCE_AGE"])
    replacement = [bar(time, REFERENCE) for time in range(close, close + 2 * HOUR, STEP)]
    add("reference-cap", [*initial, *replacement], ["CLOSED_REFERENCE", "REFERENCE_CAP_REPLACEMENT"])
    for case in cases.values():
        for lane in case["symbols"].values():
            lane["basis"] = "ARCHIVE_SUPPLIED"
            computed = lifecycle(lane["bars"], case["end"])
            if "expected" in lane:
                first = case["start"] // HOUR * HOUR
                assert [e["reason"] for e in computed.get(first, [])] == lane["expected"], (case["feed"], computed)
            else:
                assert [e["reason"] for e in computed[case["start"]]] == [
                    "CLOSED_REFERENCE", "SAME_CANDLE_RECLAIM", "POST_RECLAIM_MIDPOINT", lane["terminal_reason"],
                ]
    return cases


def digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(payload).hexdigest()


def successful(response, status=200):
    assert response.status_code == status, (str(response.request.url), response.status_code, response.text[:1800])
    return response.json()


def import_genuine(client, source):
    lanes = genuine_references(source)
    for lane in lanes.values():
        path = lane.pop("archive_path")
        pin, = lane["partitions"]
        result = successful(client.post("/api/market/import/reviewed", files={"file": (path.name, path.read_bytes())}))
        assert [{key: item[key] for key in ("id", "sha256")} for item in result["partitions"]] == [pin]
        assert result["source_sha256"] == pin["sha256"]
    return {"start": START, "end": END, "symbols": lanes}


def browser_manifest(manifest):
    for lane in manifest["symbols"].values():
        origin = START + 13 * HOUR
        chain = lifecycle(lane["bars"], manifest["end"])[origin]
        activation, *progress, terminal = chain
        sweep = next(event for event in progress if event["reason"] == "SAME_CANDLE_RECLAIM")
        midpoint = next(event for event in progress if event["reason"] == "POST_RECLAIM_MIDPOINT")
        lane.update(reference_start=origin, reference_end=origin + HOUR, activation=activation["available_at"],
                    sweep=sweep["available_at"], midpoint=midpoint["available_at"], terminal=terminal["available_at"],
                    terminal_state=terminal["state"], terminal_reason=terminal["reason"], sweep_side=sweep["sweep_side"])
        lane["checkpoints"] = [
            {key: revision[key] for key in ("available_at", "state", "reason", "observation_time", "confirmation_time")}
            for revision in chain
        ]
    return manifest


def replay(client, manifest, end=None, swap=False):
    available = tuple(manifest["symbols"])
    symbols = tuple(reversed(available)) if swap else available
    primary, *peers = symbols
    result = successful(client.post("/api/replay/sessions", json={
        "source": "DUKASCOPY_SAVED", "symbol": primary, "timeframe": "M5",
        "start": manifest["start"], "end": end or manifest["end"],
        "views": [{"view": "2", "symbol": peer, "timeframe": "M5"} for peer in peers],
    }), 201)
    for capture, symbol in zip((result["market"], result["views"].get("2")), symbols):
        lane = manifest["symbols"][symbol]
        by_id = itemgetter("id")
        assert sorted(capture["partitions"], key=by_id) == sorted(lane["partitions"], key=by_id), capture["partitions"]
        expected = [bar for bar in lane["bars"] if end is None or bar["time"] < end]
        assert [(row["time"], row["available_at"]) for row in capture["candles"]] == [
            (row["time"], row["available_at"]) for row in expected
        ], (symbol, capture.get("coverage"))
    return result["id"]


def inspect(client, session, view, cutoff, **parameters):
    result = successful(client.get(f"/api/replay/sessions/{session}/crt",
                                   params={"view": view, "cutoff": cutoff, **parameters}))
    assert (result["session_id"], result["view"], result["cutoff"]) == (session, view, cutoff)
    assert (result["authority"], result["source"], result["timeframe"], result["price_basis"], result["rule_version"]) == (
        "RESEARCH_ONLY", "DUKASCOPY_SAVED", "M5", "BID", 1,
    ), result
    assert result["availability_policy"] == "PINNED_M5_AVAILABLE_AT_OR_MODELED_NOMINAL_CLOSE"
    assert result["rules"]["crt"] == {
        "reference_frame": "H1", "reference_mode": "aggregated", "daily_timezone": "UTC",
        "daily_boundary": "00:00", "market_schedule": SCHEDULE, "reference_start": None,
        "history_bars": 2000, "history_start": None, "max_ranges": 2, "max_age": 96, "candidate_age": 3,
        "min_penetration": 0, "min_reclaim": 0, "invalidate": "sweep_extreme", "include_context": False,
    }
    assert all(detail not in row for row in result["ranges"]
               for detail in ("evidence", "reference_children", "revisions", "continuity"))
    if "range_id" not in parameters:
        assert result["selected"] is None
    return result


def all_ranges(client, session, view, cutoff):
    first = inspect(client, session, view, cutoff)
    page = first
    groups = [page["ranges"]]
    while page["previous_before"] is not None:
        before = page["previous_before"]
        page = inspect(client, session, view, cutoff, before=before)
        assert (page["end"], page["total"]) == (before, first["total"])
        groups.append(page["ranges"])
    combined = [summary for group in reversed(groups) for summary in group]
    assert len(combined) == len({row["range_id"] for row in combined}) == first["total"]
    assert combined == sorted(combined, key=lambda row: (row["available_at"], row["observation_time"], row["range_id"]))
    return {row["reference_start"]: row for row in combined}


def assert_value(actual, expected, field):
    if isinstance(expected, list):
        assert len(actual) == len(expected), (field, actual, expected)
        for got, want in zip(actual, expected):
            assert_value(got, want, field)
    elif isinstance(expected, Decimal):
        assert Decimal(str(actual)) == expected, (field, actual, expected)
    else:
        assert actual == expected, (field, actual, expected)


def check_identity(value):
    assert len(value) == 64 and set(value) <= set("0123456789abcdef"), value


def check_summary(actual, expected, ref):
    fields = {**ref, **expected}
    fields.pop("children")
    fields.pop("reference", None)
    fields.pop("replacement_children", None)
    fields.pop("continuity", None)
    for name in ("observation_ohlc", "sweep_side", "swept_boundary", "sweep_extreme", "penetration", "reclaim_close"):
        fields.setdefault(name, None)
    for field, want in fields.items():
        assert_value(actual.get(field), want, field)
    for field in ("id", "range_id", "reference_id", "evidence_fingerprint", "provenance_id", "configuration_fingerprint"):
        check_identity(actual[field])
    assert (actual["algorithm_version"], actual["identity_policy_version"]) == (1, 1)
    if expected.get("sweep_side"):
        check_identity(actual["sweep_id"])
        if expected["reclaim_close"] is not None:
            check_identity(actual["reclaim_id"])
        else:
            assert "reclaim_id" not in actual
    else:
        assert "sweep_id" not in actual and "reclaim_id" not in actual
    if expected.get("replacement_children"):
        check_identity(actual["replacement_reference"])
    else:
        assert "replacement_reference" not in actual


def check_source(row, lane, cutoff):
    time = row["time"]
    partition = lane["origins"][time]
    bar = lane["by_open"][time]
    assert row["source_id"] == f"{partition['id']}:{time}" and row["partitions"] == [partition], row
    assert row["availability_basis"] == lane["basis"], row
    assert row["available_at"] == bar["available_at"] <= cutoff, (row, utc(cutoff))
    for field in PRICE_FIELDS:
        assert_value(row[field], Decimal(str(bar[field])), field)


def expected_roles(chain, public, lane):
    activation = chain[0]["reference"]
    roles = {(time, "REFERENCE_CHILD", public[0]["id"]) for time in activation["children"]}
    candidate_open = None
    for revision, actual in zip(chain[1:], public[1:]):
        if children := revision.get("replacement_children"):
            roles.update((time, "REPLACEMENT_REFERENCE_CHILD", actual["id"]) for time in children)
            continue
        at, key = revision["observation_time"], actual["id"]
        roles.add((at, "REVISION_OBSERVATION", key))
        reason = revision["reason"]
        if reason == "NO_SAME_CANDLE_RECLAIM":
            candidate_open = at
        if reason in {"CANDIDATE_AGE", "REFERENCE_AGE"}:
            minimum = candidate_open + STEP if reason == "CANDIDATE_AGE" else activation["reference_available_at"]
            ages = [time for time, row in lane["by_open"].items() if minimum <= time <= at
                    and row["available_at"] <= revision["available_at"]]
            assert len(ages) == (3 if reason == "CANDIDATE_AGE" else 96), ages
            roles.update((time, "AGE_OBSERVATION", key) for time in ages)
        if gap := revision.get("continuity"):
            roles.update({(gap["previous_observed_open"], "GAP_PREVIOUS_OBSERVED", key),
                          (gap["next_observed_open"], "GAP_NEXT_OBSERVED", key)})
    return roles


def check_selection(selected, chain, lane, cutoff):
    public = selected["revisions"]
    assert len(public) == len(chain) and public[-1]["id"] == selected["id"], (public, chain)
    assert len({item["id"] for item in public}) == len(public)
    assert {item["reference_id"] for item in public} == {selected["reference_id"]}
    assert {item["range_id"] for item in public} == {selected["range_id"]}
    reference = chain[0]["reference"]
    for actual, wanted in zip(public, chain):
        check_summary(actual, wanted, reference)
        assert actual.get("continuity") == wanted.get("continuity"), (actual, wanted)
    check_summary(selected, chain[-1], reference)
    children = selected["reference_children"]
    assert [row["time"] for row in children] == list(reference["children"])
    for row in children:
        check_source(row, lane, cutoff)
        assert row["available_at"] <= reference["reference_available_at"]
        assert "roles" not in row
    evidence = selected["evidence"]
    assert [row["time"] for row in evidence] == sorted({row["time"] for row in evidence})
    actual_roles = []
    for row in evidence:
        check_source(row, lane, cutoff)
        assert row["roles"]
        actual_roles.extend((row["time"], role["role"], role["event_id"]) for role in row["roles"])
    assert len(set(actual_roles)) == len(actual_roles)
    assert set(actual_roles) == expected_roles(chain, public, lane), (actual_roles, expected_roles(chain, public, lane))


def verify_lane(client, session, view, lane, start, end):
    lane["by_open"] = {bar["time"]: bar for bar in lane["bars"]}
    expected = lifecycle(lane["bars"], end)
    final = all_ranges(client, session, view, end)
    assert final.keys() == expected.keys(), (view, final.keys(), expected.keys())
    assert not all_ranges(client, session, view, start)
    details = {}
    transitions_checked = 0
    receipts_checked = 0
    for hour, chain in expected.items():
        ref = chain[0]["reference"]
        check_summary(final[hour], chain[-1], ref)
        family = final[hour]["range_id"]
        detail = inspect(client, session, view, end, range_id=family)["selected"]
        check_selection(detail, chain, lane, end)
        details[hour] = detail
        receipts_checked += 1
        boundaries = {item["available_at"] for item in chain}
        for boundary in sorted(boundaries):
            for cutoff in (boundary - 1, boundary):
                result = inspect(client, session, view, cutoff, range_id=family)
                expected_prefix = [item for item in chain if item["available_at"] <= cutoff]
                assert result["total"] == sum(rows[0]["available_at"] <= cutoff for rows in expected.values())
                visible = [row for row in result["ranges"] if row["range_id"] == family]
                if expected_prefix:
                    selected = result["selected"]
                    assert len(visible) == 1 and visible[0]["id"] == selected["id"]
                    assert selected["revisions"] == detail["revisions"][:len(expected_prefix)]
                    check_selection(selected, expected_prefix, lane, cutoff)
                    receipts_checked += 1
                else:
                    assert result["selected"] is None and not visible, (utc(cutoff), result)
        transitions_checked += len(chain)

    checkpoints = []
    for cutoff in lane.get("checkpoints", ()):
        symbols = all_ranges(client, session, view, cutoff)
        at = lifecycle(lane["bars"], cutoff)
        assert symbols.keys() == at.keys()
        for hour, summary in symbols.items():
            prefix = at[hour]
            check_summary(summary, prefix[-1], prefix[0]["reference"])
            selected = inspect(client, session, view, cutoff, range_id=summary["range_id"])["selected"]
            check_selection(selected, prefix, lane, cutoff)
            assert selected["revisions"] == details[hour]["revisions"][:len(prefix)]
        checkpoints.append({"cutoff_utc": utc(cutoff),
                            "ranges": {utc(hour): summary["state"] for hour, summary in symbols.items()}})
    return details, {
        "input_rows": len(lane["bars"]), "reference_families": len(expected),
        "revisions_by_state": counts(STATES, (row["state"] for chain in expected.values() for row in chain)),
        "revisions_by_reason": counts(REASONS, (row["reason"] for chain in expected.values() for row in chain)),
        "transitions_compared_at_prior_second_and_arrival": transitions_checked,
        "validated_selected_receipts": receipts_checked,
        "exact_source_rows_in_final_receipts": sum(len(detail["evidence"]) for detail in details.values()),
        "independent_ledger_sha256": digest(expected), "public_receipt_ledger_sha256": digest(details),
        "partitions": lane["partitions"], "checkpoints_without_new_revision": checkpoints,
    }


def compact_receipt(selected):
    fields = ("id", "state", "reason", "sweep_side", "observation_time", "confirmation_time", "available_at", "continuity")
    return {
        "range_id": selected["range_id"], "reference_id": selected["reference_id"],
        "reference_utc": utc(selected["reference_start"]), "reference_ohlc": selected["reference_ohlc"],
        "midpoint": selected["midpoint"],
        "reference_children": [row["source_id"] for row in selected["reference_children"]],
        "transitions": [{field: utc(row[field]) if field.endswith("_time") or field == "available_at" else row[field]
                         for field in fields if field in row} for row in selected["revisions"]],
        "gap_source_rows": [row for row in selected["evidence"]
                            if any(role["role"].startswith("GAP_") for role in row["roles"])],
    }


def check_stability(client, full, genuine, details):
    midpoint = START + 12 * HOUR
    truncated = replay(client, genuine, end=midpoint)
    swapped = replay(client, genuine, swap=True)
    counts_by_lane = {}
    for view, symbol in PANES:
        alternate = "2" if view == "primary" else "primary"
        full_prefix = all_ranges(client, full, view, midpoint)
        assert full_prefix == all_ranges(client, truncated, view, midpoint) == all_ranges(client, swapped, alternate, midpoint)
        for hour, summary in full_prefix.items():
            family = summary["range_id"]
            inspect(client, full, view, END, range_id=family)
            prefix = inspect(client, full, view, midpoint, range_id=family)["selected"]
            assert prefix == inspect(client, truncated, view, midpoint, range_id=family)["selected"]
            assert prefix == inspect(client, swapped, alternate, midpoint, range_id=family)["selected"]
            assert prefix["revisions"] == details[symbol][hour]["revisions"][:len(prefix["revisions"])]
        counts_by_lane[symbol] = len(full_prefix)
    return counts_by_lane


@contextmanager
def private_case(directory, db, storage):
    original = db.DATABASE, storage.MARKET_DIR
    directory.mkdir()
    db.DATABASE = directory / "tradeforge.sqlite3"
    storage.MARKET_DIR = directory / "market"
    try:
        yield
    finally:
        db.DATABASE, storage.MARKET_DIR = original


def run_api(source, repo, *, only=None):
    import subprocess
    import sys

    started = datetime.now(UTC).isoformat(timespec="seconds")
    scratch = ROOT / "storage"
    scratch.mkdir(exist_ok=True)
    sys.path.insert(0, str(repo))
    with TemporaryDirectory(prefix="t10-crt-oracle-", dir=scratch) as name:
        workspace = Path(name)
        os.environ["TRADEFORGE_STORAGE"] = str(workspace)
        os.environ["TRADEFORGE_DATA_DIR"] = str(workspace / "research")
        from fastapi.testclient import TestClient
        from research_backend.app import db, storage
        from research_backend.app.main import app

        genuine_results = {}
        if only in (None, "genuine"):
            with TestClient(app) as client:
                manifest = import_genuine(client, source)
                session = replay(client, manifest)
                details = {}
                for view, symbol in PANES:
                    print(f"Verifying pinned {symbol}", flush=True)
                    lane = manifest["symbols"][symbol]
                    all_details, stats = verify_lane(client, session, view, lane, START, END)
                    details[symbol] = all_details
                    genuine_results[symbol] = {
                        **stats, "csv_sha256": lane["csv_sha256"], "archive_file": lane["archive_name"],
                        "samples": [compact_receipt(all_details[START + hour * HOUR]) for hour in (3, 13)],
                    }
                for symbol, number in check_stability(client, session, manifest, details).items():
                    genuine_results[symbol]["stable_families_after_suffix_rewind_and_pane_swap"] = number
        controlled = {}
        for name, manifest in control_cases().items():
            if only not in (None, "controls", name):
                continue
            with private_case(workspace / name, db, storage), TestClient(app) as client:
                print(f"Verifying labeled {name}", flush=True)
                session = replay(client, seed_controls(manifest))
                controlled[name] = {}
                for view, symbol in PANES:
                    if symbol not in manifest["symbols"]:
                        continue
                    lane = manifest["symbols"][symbol]
                    details, stats = verify_lane(client, session, view, lane, manifest["start"], manifest["end"])
                    controlled[name][symbol] = {**stats, "receipts": [compact_receipt(row) for row in details.values()]}
    return {
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "tracked_worktree_diff": bool(subprocess.check_output(["git", "diff", "HEAD", "--name-only"], cwd=repo)),
        "started_at": started, "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "method": "Independent Decimal H1 aggregation and CRT lifecycle derived from the source CSV and verified against "
                  "pinned Parquet. Public list and selection verify all material changes before and at availability, "
                  "with separate checks of evidence, rewind, pane swap and suffix invariance. Controlled archive rows are synthetic.",
        "coverage_utc": [utc(START), utc(END)], "genuine": genuine_results, "controlled": controlled,
    }


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage", default=ROOT / "storage", type=Path)
    parser.add_argument("--repo", default=ROOT, type=Path)
    parser.add_argument("--seed-browser", action="store_true")
    parser.add_argument("--seed-genuine", action="store_true")
    parser.add_argument("--only")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seed_browser:
        write_json(args.output, browser_manifest(seed_controls(browser_controls())))
    elif args.seed_genuine:
        from fastapi.testclient import TestClient
        from research_backend.app.main import app

        with TestClient(app) as client:
            write_json(args.output, browser_manifest(import_genuine(client, args.source_storage)))
    else:
        write_json(args.output, run_api(args.source_storage, args.repo.resolve(), only=args.only))


if __name__ == "__main__":
    main()
