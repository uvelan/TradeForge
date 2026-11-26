"""Independent Decimal FVG and order-block replay reference for November 13, 2026."""

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from itertools import groupby
import json
from operator import itemgetter
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


from scripts.verify_t10_structure_liquidity import (
    DAY, END, PRICE_FIELDS, PROFILE, START, STEP, SYMBOLS, load_provider,
    make_session, output_json, structure_reference, utc,
)


ROOT = Path(__file__).resolve().parents[1]
MODES = ("wick", "close")
TERMINAL = {"FULLY_MITIGATED", "INVALIDATED", "EXPIRED", "UNCERTAIN"}
ALL_STATES = ("ACTIVE", "TOUCHED", "PARTIALLY_MITIGATED", "FULLY_MITIGATED", "INVALIDATED", "EXPIRED", "UNCERTAIN")
MAX_AGE = 250
CANDIDATE_WINDOW = 20
FIELDS = (
    "type", "direction", "state", "reason", "revision", "lower", "upper",
    "remaining_lower", "remaining_upper", "mitigation_pct", "anchor_time",
    "formation_time", "formation_confirmation_time", "formation_available_at",
    "observation_time", "confirmation_time", "available_at",
)


def formation(kind, sign, candles, lower, upper, *, proof=None, proof_roles=()):
    first, last = candles[0], candles[-1]
    roles = (
        [(bar["time"], f"FVG_{letter}") for bar, letter in zip(candles, "ABC")]
        if kind == "FVG" else
        [(first["time"], "OB_CANDIDATE"), (last["time"], "STRUCTURE_BREAK"), *proof_roles]
    )
    return {
        "type": kind, "direction": "BULLISH" if sign == 1 else "BEARISH",
        "lower": lower, "upper": upper, "anchor_time": first["time"],
        "formation_time": last["time"], "formation_confirmation_time": last["time"] + STEP,
        "formation_available_at": last["available_at"], "formation_refs": roles, "structure": proof,
    }


def observe(zone, bar, state, reason, depth, observed=None):
    return {
        **zone, "revision": zone.get("revision", 0) + 1, "state": state, "reason": reason,
        "observation_time": bar["time"], "confirmation_time": bar["time"] + STEP,
        "available_at": bar["available_at"], "observed_price": observed, "depth": depth,
        "remaining_lower": zone["lower"] + (depth if zone["direction"] == "BEARISH" else 0),
        "remaining_upper": zone["upper"] - (depth if zone["direction"] == "BULLISH" else 0),
        "mitigation_pct": 100 * depth / (zone["upper"] - zone["lower"]),
    }


def interaction(zone, bar, mode):
    """Check a single active family against one subsequent accepted observation."""
    sign = 1 if zone["direction"] == "BULLISH" else -1
    lower, upper = zone["lower"], zone["upper"]
    width, prior = upper - lower, zone["depth"]
    observed = bar["close"] if mode == "close" else bar["low" if sign == 1 else "high"]
    entry, far = (upper, lower) if sign == 1 else (lower, upper)
    distance = sign * (entry - observed)
    if distance < prior or (distance == prior and not (zone["type"] == "OB" and zone["revision"] == 1)):
        return None
    activation, at = zone["formation_available_at"], bar["time"]
    if bar["available_at"] <= activation or at + STEP <= activation:
        return None
    if at < activation:
        return observe(zone, bar, "UNCERTAIN", "STRADDLING_OBSERVATION", prior,
                       observed if zone["type"] == "FVG" else None)
    if bar["high"] < lower or bar["low"] > upper:
        return observe(zone, bar, "INVALIDATED", "GAP_THROUGH_UNOBSERVED", prior, observed)
    depth = min(distance, width)
    if sign * (bar["close"] - far) < 0:
        reason = "CLOSE_BEYOND_OPPOSITE_BOUNDARY" if zone["type"] == "FVG" else "CLOSE_BEYOND_FAR_EDGE"
        return observe(zone, bar, "INVALIDATED", reason, depth, observed)
    if depth == width:
        return observe(zone, bar, "FULLY_MITIGATED", "OBSERVED_FULL_DEPTH", depth, observed)
    if depth == 0:
        return observe(zone, bar, "TOUCHED", "OBSERVED_TOUCH", depth, observed)
    reason = "OBSERVED_DEPTH" if zone["type"] == "FVG" else "OBSERVED_PENETRATION"
    return observe(zone, bar, "PARTIALLY_MITIGATED", reason, depth, observed)


def order_block_formations(bars):
    """Find qualifying BOS and the latest opposing candle known by the break open."""
    if len(bars) < 6:
        return {}
    breaks, pivots = structure_reference(bars)
    positions = {bar["time"]: index for index, bar in enumerate(bars)}
    known_pivots = {
        (pivot["side"], pivot["anchor_time"], pivot["available_at"]): pivot
        for batch in pivots.values() for pivot in batch
    }
    result = {}
    for event in breaks:
        if event["type"] != "BOS":
            continue
        at = event["observation_time"]
        index = positions[at]
        sign = 1 if event["side"] == "HIGH" else -1
        candidates = bars[max(0, index - CANDIDATE_WINDOW):index]
        candidate = next((row for row in reversed(candidates)
                          if sign * (row["open"] - row["close"]) > 0 and row["available_at"] <= at), None)
        if candidate is None:
            continue
        pivot = known_pivots[event["side"], event["anchor_time"], event["level_available_at"]]
        proof = {
            "type": "BOS", "side": event["side"], "level_price": event["price"],
            "break_price": event["crossing_price"], "break_time": at, "confirmation_time": at + STEP,
            "available_at": event["available_at"], "pivot_time": pivot["anchor_time"],
            "pivot_available_at": pivot["available_at"], "pivot_confirmation_bar_time": pivot["observation_time"],
            "wick_candidate_time": event["candidate_time"],
        }
        roles = list(zip(pivot["reference_opens"], ("PIVOT_LEFT", "PIVOT_LEFT", "PIVOT_ANCHOR",
                                                    "PIVOT_RIGHT", "PIVOT_RIGHT")))
        roles.append((pivot["observation_time"], "PIVOT_CONFIRMATION"))
        if event["candidate_time"] is not None:
            roles.append((event["candidate_time"], "CANDIDATE_OBSERVATION"))
        assert at not in result
        result[at] = formation("OB", sign, (candidate, bars[index]), candidate["low"], candidate["high"],
                               proof=proof, proof_roles=roles)
    return result


def zone_reference(bars, mode):
    """Scan the active families directly, using chronological provider observations."""
    assert mode in MODES
    assert all(a["time"] + STEP == b["time"] and a["available_at"] <= b["available_at"]
               for a, b in zip(bars, bars[1:]))
    order_blocks = order_block_formations(bars)
    live = {}
    ledger = {}
    for index, bar in enumerate(bars):
        for key, (formed, previous) in tuple(live.items()):
            revised = (observe(previous, bar, "EXPIRED", "MAX_AGE", previous["depth"])
                       if index - formed >= MAX_AGE else interaction(previous, bar, mode))
            if revised is None:
                continue
            ledger[key].append(revised)
            if revised["state"] in TERMINAL:
                del live[key]
            else:
                live[key] = formed, revised

        new = [order_blocks[bar["time"]]] if bar["time"] in order_blocks else []
        if index >= 2:
            first, middle = bars[index - 2:index]
            if first["high"] < bar["low"]:
                new.append(formation("FVG", 1, (first, middle, bar), first["high"], bar["low"]))
            elif bar["high"] < first["low"]:
                new.append(formation("FVG", -1, (first, middle, bar), bar["high"], first["low"]))
        for item in new:
            key = family_key(item)
            assert key not in ledger
            reason = "THREE_CLOSED_CANDLES" if item["type"] == "FVG" else "CONFIRMED_BOS"
            event = observe(item, bar, "ACTIVE", reason, Decimal(0))
            ledger[key] = [event]
            live[key] = index, event
    return list(ledger.values())


def family_key(event):
    return event["type"], event["direction"], event["anchor_time"], event["formation_time"]


def lane_reference(bars):
    return {mode: zone_reference(bars, mode) for mode in MODES}


def controlled_reference(gbp):
    """Small independently specified bars isolate equality, delays and price boundaries."""
    def build(values, arrivals=None):
        return [{"time": START + i * STEP, "available_at": START + (arrivals or {}).get(i, (i + 1) * STEP),
                 **dict(zip(PRICE_FIELDS, map(Decimal, row)))} for i, row in enumerate(values)]

    gap = [("99", "100", "98", "99"), ("102", "105", "99", "104"), ("106", "108", "105", "107")]
    fills = [("107", "108", "105", "106"), ("106", "107", "102", "106"), ("106", "107", "100", "100")]
    equality = [*gap[:2], ("102", "104", "100", "102")]
    equal_arrival = [*gap, ("107", "108", "101", "106")]
    delayed = [*gap, ("107", "108", "102", "106")]
    close_beyond = [*gap, *fills[:-1], ("106", "107", "99", "99")]
    block_bars = [dict(bar) for bar in gbp[:7]]
    block_bars[5]["available_at"] += 20
    block_exact = [dict(bar) for bar in gbp[:7]]
    block_exact[6].update(low=Decimal("1.32313"), close=Decimal("1.32315"))
    block_terminal = [dict(bar) for bar in gbp[:7]]
    block_terminal[6]["close"] = block_terminal[6]["low"]
    block_equal_prices = [(9, 10, 8, 9), (10, 11, 9, 10), (11, 14, 10, 12), (10, 11, 9, 10),
                          ("10.5", 11, 9, 10), (12, 16, 12, 15), (13, 14, 11, 13), (13, 14, 10, 13),
                          (13, 14, 9, 10)]

    cases = {
        "ob_delayed_bos_straddle": block_bars,
        "ob_equal_arrival_lifecycle": build(block_equal_prices, {6: 8 * STEP, 7: 8 * STEP}),
        "ob_equal_pivot_break_close": build(block_equal_prices, {4: 6 * STEP}),
        "ob_exact_entry_touch": block_exact,
        "ob_strict_terminal_close": block_terminal,
        "equal_gap_boundary": build(equality),
        "equal_arrival": build(equal_arrival, {2: 4 * STEP}),
        "delayed_formation_straddle": build(delayed, {2: 3 * STEP + 20}),
        "exact_entry_and_far_boundary": build([*gap, *fills]),
        "strictly_past_far_boundary": build(close_beyond),
    }
    for bars in cases.values():
        for bar in bars:
            assert bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"], bar
    result = {name: {"bars": bars, "modes": lane_reference(bars)} for name, bars in cases.items()}
    def states(case, mode, form_time=START + 2 * STEP):
        return [event["state"] for chain in result[case]["modes"][mode] for event in chain
                if event["type"] == "FVG" and event["formation_time"] == form_time]
    assert states("equal_gap_boundary", "wick") == []
    assert states("equal_arrival", "wick") == ["ACTIVE"]
    assert states("delayed_formation_straddle", "wick") == ["ACTIVE", "UNCERTAIN"]
    assert states("delayed_formation_straddle", "close") == ["ACTIVE"]
    assert states("exact_entry_and_far_boundary", "wick") == ["ACTIVE", "PARTIALLY_MITIGATED", "FULLY_MITIGATED"]
    assert states("exact_entry_and_far_boundary", "close") == ["ACTIVE", "FULLY_MITIGATED"]
    assert states("strictly_past_far_boundary", "wick")[-1] == "INVALIDATED"
    assert states("strictly_past_far_boundary", "close")[-1] == "INVALIDATED"

    def block_states(case, mode):
        chain, = (chain for chain in result[case]["modes"][mode]
                  if chain[0]["type"] == "OB" and chain[0]["formation_time"] == START + 5 * STEP)
        return [item["state"] for item in chain]

    assert block_states("ob_delayed_bos_straddle", "wick") == ["ACTIVE", "UNCERTAIN"]
    assert block_states("ob_exact_entry_touch", "wick") == ["ACTIVE", "TOUCHED"]
    assert block_states("ob_exact_entry_touch", "close") == ["ACTIVE"]
    assert block_states("ob_strict_terminal_close", "close") == ["ACTIVE", "INVALIDATED"]
    assert block_states("ob_equal_arrival_lifecycle", "wick") == [
        "ACTIVE", "TOUCHED", "PARTIALLY_MITIGATED", "FULLY_MITIGATED",
    ]
    assert not any(chain[0]["type"] == "OB" and chain[0]["formation_time"] == START + 5 * STEP
                   for chain in result["ob_equal_pivot_break_close"]["modes"]["wick"])
    return result


def reference_document(source):
    symbols = {}
    for symbol in SYMBOLS:
        bars, digest, _, pin = load_provider(source, symbol)
        symbols[symbol] = {"csv_sha256": digest, "partition": pin, "bars": bars, "modes": lane_reference(bars)}
    return {"date_utc": DAY, "range_utc": [utc(START), utc(END)], "structure_profile": PROFILE,
            "symbols": symbols, "controlled": controlled_reference(symbols["GBPUSD"]["bars"])}



def check_summary(actual, expected):
    for field in FIELDS + ("observed_price",):
        want = expected[field]
        got = actual[field]
        if field == "mitigation_pct":
            assert abs(Decimal(str(got)) - want) < Decimal("0.000000000001"), (actual, field, want)
        else:
            assert (Decimal(str(got)) if isinstance(want, Decimal) else got) == want, (actual, field, want)
    for field in ("id", "zone_id"):
        key = actual[field]
        assert len(key) == 64 and set(key) <= set("0123456789abcdef"), (field, actual)
    assert actual["algorithm_version"] == 1
    assert actual["identity_policy_version"] == (2 if expected["type"] == "FVG" else 1)


def check_evidence(rows, bars, partition, basis, *, cutoff):
    by_open = {bar["time"]: bar for bar in bars}
    seen = set()
    roles = set()
    assert [row["time"] for row in rows] == sorted({row["time"] for row in rows}), rows
    for row in rows:
        stamp = row["time"]
        bar = by_open[stamp]
        assert row["partitions"] == [partition] and row["source_id"] == f"{partition['id']}:{stamp}", row
        assert row["availability_basis"] == basis, row
        assert row["available_at"] == bar["available_at"] <= cutoff, row
        for field in PRICE_FIELDS:
            assert Decimal(str(row[field])) == bar[field], (row, field, bar[field])
        for role in row["roles"]:
            triple = stamp, role["role"], role["event_id"]
            assert triple not in seen, triple
            seen.add(triple)
        roles.update((stamp, role["role"]) for role in row["roles"])
    return roles, seen


def check_selection(selected, chain, bars, partition, basis):
    check_summary(selected, chain[-1])
    ids = set()
    summaries = selected["revisions"]
    assert len(summaries) == len(chain), (selected, chain)
    for actual, expected in zip(summaries, chain):
        check_summary(actual, expected)
        assert actual["zone_id"] == selected["zone_id"] and actual["id"] not in ids, actual
        ids.add(actual["id"])
    assert summaries[-1]["id"] == selected["id"], selected

    formation, latest = chain[0], chain[-1]
    cutoff = latest["available_at"]
    structure = selected["structure"]
    expected_roles = set(formation["formation_refs"])
    for revision in chain:
        expected_roles.add((revision["observation_time"], "REVISION_OBSERVATION"))
    roles, bound = check_evidence(selected["evidence"], bars, partition, basis, cutoff=cutoff)
    assert roles == expected_roles and len(bound) == len(roles), (selected["zone_id"], roles, expected_roles)
    assert {row["time"] for row in selected["evidence"]} == {stamp for stamp, _ in expected_roles}
    for revision, summary in zip(chain, summaries):
        assert (revision["observation_time"], "REVISION_OBSERVATION", summary["id"]) in bound
    if formation["type"] == "FVG":
        assert structure is None
        assert all((stamp, role, summaries[0]["id"]) in bound for stamp, role in formation["formation_refs"])
    else:
        proof = formation["structure"]
        for field in ("type", "side", "break_time", "confirmation_time", "available_at"):
            assert structure[field] == proof[field], (structure, proof)
        for field in ("break_price", "level_price"):
            assert Decimal(str(structure[field])) == proof[field], (structure, proof)
        assert structure["status"] == "confirmed" and structure["direction"] == formation["direction"]
        pivot = structure["pivot"]
        assert structure["broken_pivot_id"] == pivot["id"] and structure["level_available_at"] == proof["pivot_available_at"]
        assert pivot["pivot_time"] == proof["pivot_time"] and pivot["available_at"] == proof["pivot_available_at"]
        assert pivot["side"] == proof["side"] and Decimal(str(pivot["price"])) == proof["level_price"]
        assert pivot["confirmation_bar_time"] == proof["pivot_confirmation_bar_time"]
        assert pivot["confirmation_time"] == proof["pivot_confirmation_bar_time"] + STEP
        assert bool(structure["candidate_id"]) == (proof["wick_candidate_time"] is not None)
        candidate_id, = (key for stamp, role, key in bound
                         if (stamp, role) == (formation["anchor_time"], "OB_CANDIDATE"))
        assert len(candidate_id) == 64 and set(candidate_id) <= set("0123456789abcdef")
        assert (proof["break_time"], "STRUCTURE_BREAK", structure["id"]) in bound
        for stamp, role in formation["formation_refs"]:
            if role.startswith("PIVOT_"):
                assert (stamp, role, pivot["id"]) in bound
            elif role == "CANDIDATE_OBSERVATION":
                assert (stamp, role, structure["candidate_id"]) in bound
        prefix = structure["evidence"]
        _, producer_refs = check_evidence(prefix, bars, partition, basis, cutoff=formation["available_at"])
        assert [row["time"] for row in prefix] == [
            row["time"] for row in bars if row["time"] <= proof["break_time"] and row["available_at"] <= proof["available_at"]
        ]
        assert producer_refs == {
            (stamp, "EVENT_OBSERVATION" if role == "STRUCTURE_BREAK" else role, event_id)
            for stamp, role, event_id in bound if role not in {"OB_CANDIDATE", "REVISION_OBSERVATION"}
        }, producer_refs
    return len(selected["evidence"])


def inspect(client, session, view, mode, cutoff, **params):
    response = client.get(f"/api/replay/sessions/{session}/zones",
                          params={"view": view, "mitigation": mode, "cutoff": cutoff, **params})
    assert response.status_code == 200, (view, mode, utc(cutoff), params, response.status_code, response.text[:900])
    result = response.json()
    assert (result["session_id"], result["view"], result["cutoff"]) == (session, view, cutoff), result
    assert result["authority"] == "RESEARCH_ONLY" and result["rule_version"] == 1, result
    assert result["rules"]["fvg"]["mitigation"] == result["rules"]["ob"]["mitigation"] == mode, result
    assert (result["source"], result["timeframe"], result["price_basis"]) == ("DUKASCOPY_SAVED", "M5", "BID")
    if params.get("zone_id") is None:
        assert result["selected"] is None
        assert all("evidence" not in row and "structure" not in row and "revisions" not in row for row in result["zones"])
    return result


def all_zones(client, session, view, mode, cutoff):
    newest = inspect(client, session, view, mode, cutoff)
    page = newest
    pages = [page["zones"]]
    while page["previous_before"] is not None:
        before = page["previous_before"]
        page = inspect(client, session, view, mode, cutoff, before=before)
        assert page["end"] == before and page["total"] == newest["total"], page
        pages.append(page["zones"])
    records = [row for group in reversed(pages) for row in group]
    assert len(records) == newest["total"] == len({row["zone_id"] for row in records}), newest
    assert records == sorted(records, key=lambda e: (e["available_at"], e["observation_time"], e["type"], e["zone_id"]))
    return {family_key(row): row for row in records}


def counts(keys, values):
    frequency = Counter(values)
    return {key: frequency[key] for key in keys}


def digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


def boundary_witness(chain):
    return {
        "direction": chain[0]["direction"], "bounds": [chain[0]["lower"], chain[0]["upper"]],
        "anchor_utc": utc(chain[0]["anchor_time"]), "formation_observation_utc": utc(chain[0]["formation_time"]),
        "formation_availability_utc": utc(chain[0]["formation_available_at"]),
        "transitions": [{"state": row["state"], "observation_utc": utc(row["observation_time"]),
                         "arrival_utc": utc(row["available_at"]), "mitigation_pct": row["mitigation_pct"]}
                        for row in chain],
    }


def mode_differences(lane):
    result = {}
    for kind in ("FVG", "OB"):
        wick = [chain for chain in lane["modes"]["wick"] if chain[0]["type"] == kind]
        close = [chain for chain in lane["modes"]["close"] if chain[0]["type"] == kind]
        assert [family_key(chain[0]) for chain in wick] == [family_key(chain[0]) for chain in close]
        paired = list(zip(wick, close))
        fields = ("observation_time", "available_at", "state", "remaining_lower", "remaining_upper")
        def lifecycle(chain):
            return [tuple(row[field] for field in fields) for row in chain]
        result[kind] = {
            "families_with_different_lifecycle": sum(lifecycle(a) != lifecycle(b) for a, b in paired),
            "families_with_different_final_state": sum(a[-1]["state"] != b[-1]["state"] for a, b in paired),
            "families_with_different_latest_availability": sum(
                a[-1]["available_at"] != b[-1]["available_at"] for a, b in paired),
        }
    return result


def verify_lane(client, session, view, mode, end, lane, *, basis, canonical_ids=False):
    bars, partition = lane["bars"], lane["partition"]
    chains = lane["modes"][mode]
    expected = {family_key(chain[0]): chain for chain in chains}
    latest = all_zones(client, session, view, mode, end)
    assert latest.keys() == expected.keys(), (view, mode, expected.keys() - latest.keys(), latest.keys() - expected.keys())
    empty = inspect(client, session, view, mode, bars[0]["time"], zone_id="f" * 64)
    assert empty["total"] == 0 and empty["zones"] == [] and empty["selected"] is None, empty

    receipt_rows = 0
    structure_rows = 0
    boundaries = set()
    public_sequences = []
    tied_revision_boundaries = 0

    for key, chain in expected.items():
        summary = latest[key]
        family = summary["zone_id"]
        check_summary(summary, chain[-1])
        final = inspect(client, session, view, mode, end, zone_id=family)["selected"]
        receipt_rows += check_selection(final, chain, bars, partition, basis)
        if final["structure"]:
            structure_rows += len(final["structure"]["evidence"])
        if canonical_ids and final["structure"]:
            structure_id = final["structure"]["id"]
            response = client.get(f"/api/replay/sessions/{session}/structure-liquidity", params={
                "view": view, "cutoff": chain[0]["available_at"], "event_id": structure_id,
            })
            assert response.status_code == 200, response.text
            bos = response.json()["selected"]
            assert bos is not None and bos["type"] == "BOS" and bos["supporting_ids"][0] == final["structure"]["pivot"]["id"]
            assert bos["evidence"] == final["structure"]["evidence"], (bos, final["structure"])

        published = 0
        for available, group in groupby(chain, itemgetter("available_at")):
            event = chain[published]
            next_published = published + sum(1 for _ in group)
            tied_revision_boundaries += next_published - published > 1
            boundaries.add(available)
            lead = inspect(client, session, view, mode, available - 1, zone_id=family)
            before = lead["selected"]
            assert lead["total"] == sum(value[0]["available_at"] < available for value in chains), (event, lead)
            if published == 0:
                assert before is None and all(row["zone_id"] != family for row in lead["zones"]), (event, lead)
            else:
                check_summary(before, chain[published - 1])
                assert before["revisions"] == final["revisions"][:published], (event, before)
                assert all(row["available_at"] < available for row in before["evidence"]), (event, before)

            done = inspect(client, session, view, mode, available, zone_id=family)
            selected = done["selected"]
            assert done["total"] == sum(value[0]["available_at"] <= available for value in chains), (event, done)
            assert any(row["zone_id"] == family and row["id"] == selected["id"] for row in done["zones"]), (event, done)
            check_selection(selected, chain[:next_published], bars, partition, basis)
            assert selected["revisions"] == final["revisions"][:next_published], (event, selected)
            assert selected["structure"] == final["structure"], (event, selected)
            published = next_published
        public_sequences.append(final)

    return {
        "families": counts(("FVG", "OB"), (key[0] for key in expected)),
        "states": counts(ALL_STATES, (event["state"] for chain in chains for event in chain)),
        "latest_states": counts(ALL_STATES, (chain[-1]["state"] for chain in chains)),
        "distinct_availability_boundaries": len(boundaries),
        "checked_t_minus_one_and_t": sum(map(len, chains)),
        "direct_rows_checked_at_final_cutoff": receipt_rows,
        "structure_rows_checked_at_final_cutoff": structure_rows,
        "empty_before_any_formation": True,
        "canonical_bos_receipts_checked": sum(key[0] == "OB" for key in expected) if canonical_ids else 0,
        "tied_revision_boundaries": tied_revision_boundaries,
        "independent_sequence_sha256": digest(chains),
        "verified_receipt_sequence_sha256": digest(public_sequences),
        "first_formations": {
            kind: next((boundary_witness(chain) for chain in chains if chain[0]["type"] == kind), None)
            for kind in ("FVG", "OB")
        },
    }


@contextmanager
def private_case(directory, db, storage):
    original = db.DATABASE, storage.MARKET_DIR
    db.DATABASE = directory / "tradeforge.sqlite3"
    storage.MARKET_DIR = directory / "market"
    directory.mkdir()
    try:
        db.initialize()
        yield
    finally:
        db.DATABASE, storage.MARKET_DIR = original


def run_api(source, target, reference, repo):
    started = datetime.now(UTC).isoformat(timespec="seconds")
    scratch = ROOT / "storage"
    scratch.mkdir(exist_ok=True)
    sys.path.insert(0, str(repo))
    with TemporaryDirectory(prefix="t10-zones-oracle-", dir=scratch) as name:
        workspace = Path(name)
        os.environ["TRADEFORGE_STORAGE"] = str(workspace)
        os.environ["TRADEFORGE_DATA_DIR"] = str(workspace / "research")
        from fastapi.testclient import TestClient
        from research_backend.app import db, storage
        from research_backend.app.main import app

        with TestClient(app) as client:
            for symbol in SYMBOLS:
                _, _, path, pin = load_provider(source, symbol)
                response = client.post("/api/market/import/reviewed", files={"file": (path.name, path.read_bytes())})
                assert response.status_code == 200, response.text
                imported, = response.json()["partitions"]
                assert {key: imported[key] for key in ("id", "sha256")} == pin

            truncated_end = START + 12 * 3600
            short = make_session(client, "EURUSD", truncated_end, "GBPUSD")
            full = make_session(client, "EURUSD", END, "GBPUSD")
            swapped = make_session(client, "GBPUSD", END, "EURUSD")
            assert len({short, full, swapped}) == 3
            genuine = {}
            for view, symbol in zip(("primary", "comparison"), SYMBOLS):
                lane = reference["symbols"][symbol]
                swapped_view = "comparison" if view == "primary" else "primary"
                genuine[symbol] = {"csv_sha256": lane["csv_sha256"], "partition": lane["partition"],
                                   "candles": len(lane["bars"]), "mode_differences": mode_differences(lane), "modes": {}}
                for mode in MODES:
                    print(f"Verifying pinned {symbol} {mode}", flush=True)
                    results = verify_lane(client, full, view, mode, END, lane,
                                          basis="MODELED_NOMINAL_CLOSE", canonical_ids=mode == "wick")
                    def at_same_cutoff(session, pane):
                        return all_zones(client, session, pane, mode, truncated_end)
                    before = at_same_cutoff(short, view)
                    assert before == at_same_cutoff(full, view) == at_same_cutoff(swapped, swapped_view)
                    for kind in ("FVG", "OB"):
                        family = next(row["zone_id"] for row in before.values() if row["type"] == kind)
                        receipts = [
                            inspect(client, session, pane, mode, truncated_end, zone_id=family)["selected"]
                            for session, pane in ((short, view), (full, view), (swapped, swapped_view))
                        ]
                        assert receipts[0] == receipts[1] == receipts[2], (kind, mode, symbol)
                    results["stable_after_append_rewind_and_pane_swap"] = len(before)
                    genuine[symbol]["modes"][mode] = results

        controlled = {}
        for label, lane in reference["controlled"].items():
            with private_case(workspace / label, db, storage), TestClient(app) as client:
                print(f"Verifying disposable {label}", flush=True)
                bars = lane["bars"]
                rows = [{key: float(item) if isinstance(item, Decimal) else item for key, item in bar.items()}
                        for bar in bars]
                symbol = "GBPUSD" if label.startswith("ob_") else "EURUSD"
                part = storage.write_partition("DUKASCOPY", "controlled-t10-zones", symbol, "candles", "M5", "BID", rows)
                lane["partition"] = {key: part[key] for key in ("id", "sha256")}
                end = rows[-1]["time"] + STEP
                session = make_session(client, symbol, end)
                controlled[label] = {mode: verify_lane(client, session, "primary", mode, end, lane,
                                                        basis="ARCHIVE_SUPPLIED") for mode in MODES}
    output_json(target, {
        "date_utc": DAY, "range_utc": reference["range_utc"], "structure_profile": PROFILE,
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "started_at": started, "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "method": "Independent Decimal scan over the provider CSV, with every M5 candle checked against pinned "
                  "Parquet. A separately reconstructed structure model identifies confirmed BOS and the preceding "
                  "known opposite candle. Public saved-replay receipts check every zone revision at T-1 and T.",
        "symbols": genuine, "controlled": controlled,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--reference-only", action="store_true")
    args = parser.parse_args()
    reference = reference_document(args.source_storage)
    if args.reference_only:
        output_json(args.output, reference)
    else:
        run_api(args.source_storage, args.output, reference, args.repo.resolve())
    for symbol, lane in reference["symbols"].items():
        for mode, chains in lane["modes"].items():
            print(symbol, mode, dict(Counter(chain[0]["type"] for chain in chains)),
                  dict(Counter(event["state"] for chain in chains for event in chain)))


if __name__ == "__main__":
    main()
