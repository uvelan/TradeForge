"""Independent price-row oracle and seed utility for CRT_SMT_M5_V1."""

import argparse
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.verify_t10_crt import bar, digest, import_genuine, lifecycle, replay, seed_controls, successful
from scripts.verify_t10_structure_liquidity import PRICE_FIELDS, START as DAY, STEP, utc

ROOT = Path(__file__).resolve().parents[1]
START, END = DAY + 12 * 3600, DAY + 16 * 3600
CLOSE = DAY + 14 * 3600
PROFILE = "CRT_SMT_M5_V1"
SWEEP_CANDIDATES = {"HIGH_SWEEP_CANDIDATE", "LOW_SWEEP_CANDIDATE"}
RECLAIMED = {"HIGH_SWEPT_RECLAIMED", "LOW_SWEPT_RECLAIMED", "EXPANSION_OBSERVED"}
CONTROLS = ("disjoint", "overlap", "delayed-overlap", "contradiction", "terminal-tie",
            "smt-terminal", "newest-crt", "newest-smt", "peer-gap")


def price_rows(name):
    """Construct a complete 13:00 H1 and two exact-aligned divergence pivot pairs."""
    start = CLOSE - 3600
    eur = [(103, 105, 102, 103)] * 12
    gbp = [(103, 104, 102, 103)] * 12
    eur[8] = (103, 105, 101, 102)
    gbp[8] = (103, 104, 99, 102)
    eur += [(102, 102.5, 102, 102.25)] * 10
    gbp += [(102, 104, 102, 103)] * 10
    eur[13] = (102, 102.5, 99, 102)
    gbp[13] = (102, 104, 100, 102)
    eur[14] = (102, 104, 102, 103)
    if name == "disjoint":
        eur[15] = (103, 105, 102, 104)
    else:
        eur[20] = (103, 105, 102, 104)
    if name == "contradiction":
        # Two bearish pivots use the same source rows as a later low reclaim.
        eur = [(103, 104, 102, 103)] * 22
        gbp = [(103, 104, 102, 103)] * 22
        eur[8] = (103, 105, 101, 103)
        gbp[8] = (103, 107, 102, 103)
        eur[13] = (103, 106, 102, 106)
        gbp[13] = (103, 106, 102, 103)
        # Give EURUSD a higher earlier reference wick outside the pivot stencil.
        eur[2] = (103, 108, 102, 103)
        eur[14] = (103, 104, 99, 102)
    gbp[21] = (103, 104, 98, 102)
    if name in {"newest-crt", "newest-smt"}:
        eur[20] = (102, 102.5, 102, 102.25)
        gbp[21] = (102, 104, 102, 103)
        if name == "newest-crt":
            eur += [eur[19]] * 4
            gbp += [gbp[19]] * 4
        else:
            eur[20] = (102, 102.5, 98, 102.25)
            gbp[20] = (102, 104, 101, 103)
            eur.append(eur[19])
            gbp.append(gbp[19])
    if name == "terminal-tie":
        # Same confirmation bar breaches the laggard's old low. Terminal wins.
        gbp[16] = (102, 104, 98, 102)
    if name == "smt-terminal":
        gbp[18] = (102, 104, 98, 102)

    delayed = {
        ("GBPUSD", 15): 17,
        ("EURUSD", 17): 23,
    } if name == "delayed-overlap" else {}
    return {"start": start, "end": start + len(eur) * STEP, "feed": f"controlled-t10-confluence-{name}",
            "symbols": {
                symbol: {"bars": [bar(start + i * STEP, ohlc, delayed.get((symbol, i), 0))
                                  for i, ohlc in enumerate(prices)
                                  if not (name == "peer-gap" and symbol == "GBPUSD" and i == 18)],
                         "basis": "ARCHIVE_SUPPLIED"}
                for symbol, prices in (("EURUSD", eur), ("GBPUSD", gbp))
            }}


def smt_families(manifest, cutoff):
    """Reconstruct strict aligned SMT on the captured prefix of one open session.

    This worksheet covers one Friday window and does not derive producer IDs.
    Same-arrival family identity ties are checked by a dedicated public API control.
    """
    slots = range(manifest["start"], manifest["end"], STEP)
    a, b = [{row["time"]: row for row in lane["bars"] if row["available_at"] <= cutoff}
            for lane in manifest["symbols"].values()]
    shared = []
    for stamp in slots:
        if stamp not in a or stamp not in b:
            break
        shared.append(stamp)

    families = []
    for direction, field, sign in (("BULLISH", "low", -1), ("BEARISH", "high", 1)):
        previous = None
        for index in range(2, len(shared) - 2):
            center = shared[index]
            stencil = shared[index - 2:index + 3]
            prices = [sign * Decimal(str(lane[center][field])) for lane in (a, b)]
            if not all(all(price > sign * Decimal(str(lane[other][field])) for other in stencil
                           if other != center) for lane, price in zip((a, b), prices)):
                continue
            former = previous
            previous = center, prices
            if former is None:
                continue
            prior, bases = former
            changes = [price - base for price, base in zip(prices, bases)]
            if changes[0] * changes[1] >= 0:
                continue
            laggard_index = int(changes[0] > 0)
            laggard = (a, b)[laggard_index]
            base = bases[laggard_index]

            def breaks(row):
                return sign * Decimal(str(row[field])) > base

            matching = shared[:index + 3]
            candidate_at = max(lane[stamp]["available_at"] for lane in (a, b) for stamp in matching)
            observed = [stamp for stamp in slots if center < stamp and stamp + STEP <= candidate_at]
            for stamp in observed:
                if stamp not in laggard or breaks(laggard[stamp]):
                    break
                candidate_at = max(candidate_at, laggard[stamp]["available_at"])
            else:
                eligible = [stamp for stamp in slots if stamp >= candidate_at][:64]
                straddle = candidate_at // STEP * STEP if candidate_at % STEP else None
                straddle_closed = straddle is not None and straddle + STEP <= cutoff
                uncertain_straddle = straddle_closed and (straddle not in laggard or breaks(laggard[straddle]))
                revisions = [{"state": "SMT_CANDIDATE", "available_at": candidate_at,
                              "observation_time": stencil[0],
                              "confirmation_time": max(stencil[-1], *observed) + STEP}]
                catches = sorted((laggard[stamp]["available_at"], stamp) for stamp in eligible
                                 if stamp in laggard and breaks(laggard[stamp]))
                catch = catches[0] if catches else None
                expiry = max(laggard[stamp]["available_at"] for stamp in eligible) if (
                    len(eligible) == 64 and all(stamp in laggard for stamp in eligible)
                ) else None
                if eligible and not uncertain_straddle:
                    first = eligible[0]
                    if first in a and first in b:
                        confirms = max(a[first]["available_at"], b[first]["available_at"],
                                       laggard[straddle]["available_at"] if straddle_closed else candidate_at)
                        if (catch is None or confirms < catch[0]) and (expiry is None or confirms <= expiry):
                            revisions.append({"state": "SMT_CONFIRMED", "available_at": confirms,
                                              "observation_time": first, "confirmation_time": first + STEP})
                if catch:
                    at, stamp = catch
                    revisions.append({"state": "SMT_INVALIDATED", "available_at": at,
                                      "observation_time": stamp, "confirmation_time": stamp + STEP})
                elif expiry is not None:
                    revisions.append({"state": "EXPIRED", "available_at": expiry,
                                      "observation_time": eligible[0], "confirmation_time": eligible[-1] + STEP})
                uncertain = uncertain_straddle or any(stamp not in laggard
                                                       for stamp in eligible if stamp + STEP <= cutoff)
                state = revisions[-1]["state"]
                if uncertain and state in {"SMT_CANDIDATE", "SMT_CONFIRMED"}:
                    state = "UNCERTAIN"
                families.append({"key": (direction, prior, center), "direction": direction,
                                 "leader": ("comparison", "primary")[laggard_index],
                                 "candidate_at": candidate_at, "revisions": revisions, "state": state})
    return families


def expected_coverage(manifest, cutoff):
    expected = set(range(manifest["start"], cutoff // STEP * STEP, STEP))
    issues = {}
    for symbol, lane in manifest["symbols"].items():
        visible = {row["time"] for row in lane["bars"] if row["available_at"] <= cutoff}
        gaps = []
        for stamp in sorted(expected - visible):
            if gaps and gaps[-1]["end"] == stamp:
                gaps[-1]["end"] += STEP
            else:
                gaps.append({"start": stamp, "end": stamp + STEP})
        if gaps:
            issues[symbol] = {"symbol": symbol, "reason": "MISSING_OPEN_CANDLES", "missing_intervals": gaps}
    return issues


def expected_reason(crt_state, smt_state, coverage, verdict):
    if coverage:
        return "MISSING_OPEN_CANDLES"
    if crt_state is None:
        return "NO_VISIBLE_CRT_FAMILY"
    if crt_state in SWEEP_CANDIDATES:
        return "CRT_CANDIDATE"
    if crt_state in {"AMBIGUOUS", "UNCERTAIN"}:
        return "CRT_UNCERTAIN"
    if smt_state is None:
        return "NO_VISIBLE_SMT_FAMILY"
    if smt_state == "SMT_CANDIDATE":
        return "SMT_CANDIDATE"
    if smt_state == "UNCERTAIN":
        return "SMT_UNCERTAIN"
    if crt_state not in RECLAIMED:
        return "CRT_NOT_RECLAIMED"
    if smt_state != "SMT_CONFIRMED":
        return "SMT_NOT_CONFIRMED"
    return "MATCHING_DIRECTIONS" if verdict == "ALIGNED" else "OPPOSITE_DIRECTIONS"


def expected_at(manifest, cutoff):
    ledger = lifecycle(manifest["symbols"]["EURUSD"]["bars"], cutoff)
    crt = ledger[max(ledger)][-1] if ledger else None
    families = smt_families(manifest, cutoff)
    if families:
        candidate = max(family["candidate_at"] for family in families)
        newest = [family for family in families if family["candidate_at"] == candidate]
        assert len(newest) == 1, ("Add explicit canonical family identity checking for tied fixtures", newest)
        smt = newest[0]
    else:
        smt = None
    crt_state, smt_state = (crt["state"] if crt else None), (smt["state"] if smt else None)
    direction = None
    coverage = expected_coverage(manifest, cutoff)
    if coverage or not crt or not smt or crt_state in SWEEP_CANDIDATES | {"UNCERTAIN", "AMBIGUOUS"} or smt_state in {
        "SMT_CANDIDATE", "UNCERTAIN",
    }:
        verdict = "INCOMPLETE"
    elif crt_state not in RECLAIMED or smt_state != "SMT_CONFIRMED":
        verdict = "NOT_ALIGNED"
    else:
        direction = "BULLISH" if crt["sweep_side"] == "LOW" else "BEARISH"
        verdict = "ALIGNED" if direction == smt["direction"] else "CONTRADICTED"
        if verdict == "CONTRADICTED":
            direction = None
    reason = expected_reason(crt_state, smt_state, coverage, verdict)
    return {"cutoff": cutoff, "verdict": verdict, "reason": reason, "coverage_issues": coverage,
            "crt_state": crt_state, "smt_state": smt_state,
            "direction": direction, "crt": crt, "smt": smt,
            "crt_reference": max(ledger) if ledger else None,
            "crt_family_count": len(ledger), "smt_family_count": len(families)}


def with_checkpoints(manifest):
    # Include nominal and captured boundaries even when an expected current verdict
    # does not change. This catches a false positive on an unrelated publication.
    boundaries = {row["time"] + STEP for lane in manifest["symbols"].values() for row in lane["bars"]}
    boundaries.update(row["available_at"] for lane in manifest["symbols"].values() for row in lane["bars"])
    boundaries.add(manifest["end"])
    cutoffs = sorted({manifest["start"]} | {cutoff for t in boundaries for cutoff in (t - 1, t)})
    manifest["checkpoints"] = [expected_at(manifest, cutoff) for cutoff in cutoffs]
    return manifest


def genuine(client, source):
    manifest = import_genuine(client, source)
    manifest.update(start=START, end=END)
    for lane in manifest["symbols"].values():
        lane["bars"] = [{**row, **{key: float(row[key]) for key in PRICE_FIELDS}}
                        for row in lane["bars"] if START <= row["time"] < END]
    return with_checkpoints(manifest)


def assert_identity(value):
    assert len(value) == 64 and set(value) <= set("0123456789abcdef"), value


def verify_component(actual, expected, kind, manifest, cutoff):
    expected_state = expected[f"{kind}_state"]
    assert actual["state"] == (expected_state or "UNAVAILABLE"), (kind, actual["state"], expected_state)
    if expected_state is None:
        assert actual["family_id"] is None and actual["revision_id"] is None and not actual["evidence"]
        return
    assert_identity(actual["family_id"])
    assert_identity(actual["revision_id"])
    rows = actual["revisions"]
    assert len(rows) == actual["revision"]
    assert rows[-1]["id"] == actual["revision_id"]
    assert len({row["id"] for row in rows}) == len(rows)
    assert all(row["available_at"] <= cutoff for row in rows)
    expected_revision = expected[kind] if kind == "crt" else expected[kind]["revisions"][-1]
    for field in ("observation_time", "confirmation_time", "available_at"):
        assert actual[field] == expected_revision[field], (kind, field, actual[field], expected_revision[field])

    by_symbol = {symbol: {row["time"]: row for row in lane["bars"]}
                 for symbol, lane in manifest["symbols"].items()}
    evidence = {(row["symbol"], row["time"]): row for row in actual["evidence"]}
    assert len(evidence) == len(actual["evidence"])
    for (symbol, stamp), row in evidence.items():
        assert (row["source"], row["price_basis"], row["timeframe"]) == ("DUKASCOPY_SAVED", "BID", "M5")
        lane = manifest["symbols"][symbol]
        origin = lane["origins"][stamp]
        assert row["partitions"] == [origin]
        assert row["source_id"] == f"{origin['id']}:{stamp}"
        assert row["availability_basis"] == lane["basis"]
        assert row["available_at"] == by_symbol[symbol][stamp]["available_at"] <= cutoff
        assert all(row[key] == by_symbol[symbol][stamp][key] for key in PRICE_FIELDS)
        assert len({(role["role"], role["event_id"]) for role in row["roles"]}) == len(row["roles"]) > 0
        for role in row["roles"]:
            assert_identity(role["event_id"])

    if kind == "crt":
        assert actual["reference_start"] == expected["crt_reference"]
        assert actual["reason"] == expected_revision["reason"]
        assert actual["revision"] == expected_revision["revision"]
        assert actual["sweep_side"] == expected_revision.get("sweep_side")
        start = actual["reference_start"]
        required = {("EURUSD", stamp) for stamp in range(start, start + 3600, STEP)}
        assert {key for key, row in evidence.items()
                if any(role["role"] == "REFERENCE_CHILD" for role in row["roles"])} == required
    else:
        smt = expected["smt"]
        direction, prior, current = smt["key"]
        assert (actual["direction"], actual["leader"], actual["candidate_at"]) == (
            direction, smt["leader"], smt["candidate_at"])
        timing_fields = ("state", "available_at", "observation_time", "confirmation_time")
        assert [{key: row[key] for key in timing_fields} for row in rows] == smt["revisions"]
        for side, symbol in zip(("primary", "comparison"), ("EURUSD", "GBPUSD")):
            for label, anchor in (("previous", prior), ("current", current)):
                pivot = actual["pivots"][side][label]
                assert pivot["time"] == anchor
                assert pivot["price"] == by_symbol[symbol][anchor]["high" if direction == "BEARISH" else "low"]
                anchor_role = {"role": f"{label.upper()}_PIVOT_ANCHOR", "event_id": pivot["id"]}
                assert anchor_role in evidence[symbol, anchor]["roles"]
                for stamp in (anchor - 2*STEP, anchor - STEP, anchor + STEP, anchor + 2*STEP):
                    child_role = {"role": f"{label.upper()}_PIVOT_CHILD", "event_id": pivot["id"]}
                    assert child_role in evidence[symbol, stamp]["roles"]
            assert all(any(role["role"] == "MATCHING_PREFIX" for role in evidence[symbol, stamp]["roles"])
                       for stamp in range(manifest["start"], current + 3*STEP, STEP))


def read_confluence(client, session, cutoff, receipt_id=None):
    parameters = {"cutoff": cutoff, "profile": PROFILE}
    if receipt_id is not None:
        parameters["receipt_id"] = receipt_id
    return client.get(f"/api/replay/sessions/{session}/confluence", params=parameters)


def save_confluence(client, session, cutoff, receipt_id):
    return client.post(f"/api/replay/sessions/{session}/confluence",
                       params={"cutoff": cutoff, "profile": PROFILE, "receipt_id": receipt_id})


def assert_labeled_results(name, checkpoints):
    at = {item["cutoff"]: item for item in checkpoints}
    counts = Counter(item["verdict"] for item in checkpoints)
    if name == "genuine":
        assert counts == {"ALIGNED": 2, "NOT_ALIGNED": 55, "INCOMPLETE": 40}
        alignment = DAY + 13*3600 + 25*60
        assert [item["cutoff"] for item in checkpoints if item["verdict"] == "ALIGNED"] == [
            alignment, alignment + STEP - 1,
        ]
        assert at[alignment]["direction"] == "BEARISH"
        assert at[alignment + STEP]["crt_state"] == "INVALIDATED"
        assert at[alignment + STEP]["smt_state"] == "SMT_INVALIDATED"
    elif name == "disjoint":
        assert counts["ALIGNED"] == counts["CONTRADICTED"] == 0
        assert at[CLOSE + 4*STEP]["crt_state"] == "OPPOSITE_BOUNDARY_REACHED"
        assert at[CLOSE + 5*STEP]["smt_state"] == "SMT_CONFIRMED"
    elif name == "contradiction":
        assert counts["CONTRADICTED"] > 0 and counts["ALIGNED"] == 0
        assert at[CLOSE + 5*STEP - 1]["verdict"] == "INCOMPLETE"
        assert at[CLOSE + 5*STEP]["verdict"] == "CONTRADICTED"
    elif name == "terminal-tie":
        assert counts["ALIGNED"] == 0 and at[CLOSE + 5*STEP]["smt_state"] == "SMT_INVALIDATED"
    elif name == "peer-gap":
        gap = CLOSE + 7*STEP
        assert at[gap - 1]["verdict"] == "ALIGNED"
        assert at[gap]["smt_state"] == "UNCERTAIN"
        assert all(item["verdict"] == "INCOMPLETE" for item in checkpoints if item["cutoff"] >= gap)
    elif name == "newest-crt":
        assert at[CLOSE + 12*STEP - 1]["verdict"] == "ALIGNED"
        assert at[CLOSE + 12*STEP]["crt_state"] == "ACTIVE"
        assert at[CLOSE + 12*STEP]["verdict"] == "NOT_ALIGNED"
    elif name == "newest-smt":
        assert at[CLOSE + 11*STEP - 1]["verdict"] == "ALIGNED"
        assert at[CLOSE + 11*STEP]["smt_state"] == "SMT_CANDIDATE"
        assert at[CLOSE + 11*STEP]["verdict"] == "INCOMPLETE"
        assert at[CLOSE + 11*STEP]["smt_family_count"] == 2
    else:
        aligned_at = CLOSE + 6*STEP + 23 if name == "delayed-overlap" else CLOSE + 5*STEP
        assert at[aligned_at - 1]["verdict"] == "INCOMPLETE"
        assert at[aligned_at]["verdict"] == "ALIGNED"
        assert at[aligned_at]["direction"] == "BULLISH"
        end = CLOSE + (7 if name == "smt-terminal" else 9)*STEP
        assert at[end - 1]["verdict"] == "ALIGNED"
        assert at[end]["verdict"] == "NOT_ALIGNED"


def verify_case(client, name, manifest):
    session = replay(client, manifest)
    checkpoints = manifest["checkpoints"]
    assert_labeled_results(name, checkpoints)
    first, last = checkpoints[0]["cutoff"], checkpoints[-1]["cutoff"]
    seen, transitions = {}, []
    families = {"crt": {}, "smt": {}}
    previous = None
    for expected in checkpoints:
        cutoff = expected["cutoff"]
        actual = successful(read_confluence(client, session, cutoff))
        assert (actual["session_id"], actual["cutoff"], actual["profile"], actual["rule_version"]) == (
            session, cutoff, PROFILE, 2,
        )
        assert actual["authority"] == "RESEARCH_ONLY"
        assert actual["availability_policy"] == "PINNED_M5_AVAILABLE_AT_OR_MODELED_NOMINAL_CLOSE"
        assert actual["rules"]["smt"] == {
            "relationship": "POSITIVE", "period": 2, "alignment_bars": 0, "tolerance_bps": 0.0,
            "confirmation_bars": 1, "expiry_bars": 64,
        }
        assert (actual["status"], actual["direction"]) == (expected["verdict"], expected["direction"]), (
            name, utc(cutoff), expected["verdict"], actual["status"], actual["reason"],
        )
        assert actual["reason"] == expected["reason"], (name, utc(cutoff), actual["reason"], expected["reason"])
        for kind, symbols in (("crt", ("EURUSD",)), ("smt", ("EURUSD", "GBPUSD"))):
            assert actual["components"][kind]["coverage_issues"] == [
                expected["coverage_issues"][symbol] for symbol in symbols if symbol in expected["coverage_issues"]
            ], (name, cutoff, kind)
        if actual["status"] == "INCOMPLETE":
            assert actual["first_available_at"] is None
        else:
            recoveries = [row["available_at"] for lane in manifest["symbols"].values() for row in lane["bars"]
                          if row["time"] + STEP < row["available_at"] <= cutoff]
            assert actual["first_available_at"] == max(
                [entry["available_at"] for entry in actual["components"].values()] + recoveries
            ) <= cutoff
        # The chart must explain the join clock from the archive, including
        # every tied arrival and even when no producer published a revision.
        expected_witnesses = []
        for symbol, lane in sorted(manifest["symbols"].items()):
            for row in lane["bars"]:
                stamp = row["time"]
                arrived = row["available_at"]
                if stamp + STEP < arrived == actual["first_available_at"]:
                    partition = lane["origins"][stamp]
                    expected_witnesses.append((symbol, stamp, stamp + STEP, arrived,
                                               f"{partition['id']}:{stamp}", [partition]))
        actual_witnesses = [(w["symbol"], w["time"], w["nominal_close"], w["available_at"],
                             w["source_id"], w["partitions"]) for w in actual["coverage_recoveries"]]
        assert actual_witnesses == expected_witnesses, (name, utc(cutoff), actual_witnesses, expected_witnesses)
        receipt = actual["receipt_id"]
        assert_identity(receipt)
        assert digest({key: value for key, value in actual.items() if key != "receipt_id"}) == receipt
        if cutoff == first:
            successful(read_confluence(client, session, cutoff, receipt), 409)
            successful(save_confluence(client, session, cutoff, "0"*64), 409)
        assert successful(save_confluence(client, session, cutoff, receipt)) == actual
        seen[cutoff] = (receipt, digest(actual))
        for kind, component in actual["components"].items():
            verify_component(component, expected, kind, manifest, cutoff)
            if component["family_id"] is None:
                continue
            key = expected["crt_reference"] if kind == "crt" else expected["smt"]["key"]
            family, revisions = families[kind].setdefault(key, (component["family_id"], {}))
            assert family == component["family_id"]
            for row_index, row in enumerate(component["revisions"]):
                assert row == revisions.setdefault(row_index, row), (kind, key, row_index)
        verdict = {key: actual[key] for key in ("status", "reason", "direction", "first_available_at")}
        components = {
            name: {key: entry[key] for key in ("family_id", "revision_id", "revision", "state", "reason",
                                               "available_at", "observation_time", "confirmation_time")}
            for name, entry in actual["components"].items()
        }
        if (verdict, components) != previous:
            transitions.append({
                "utc": utc(cutoff), **verdict,
                "verdict_changed": previous is None or any(
                    verdict[key] != previous[0][key] for key in ("status", "reason", "direction")
                ),
                "component_changed": previous is None or components != previous[1],
                "crt_state": expected["crt_state"], "smt_state": expected["smt_state"],
                "first_available_at": utc(actual["first_available_at"]) if actual["first_available_at"] else None,
                "components": {
                    name: {key: utc(value) if value is not None and key in (
                        "available_at", "observation_time", "confirmation_time"
                    ) else value for key, value in entry.items()}
                    for name, entry in components.items()
                },
                "receipt_id": receipt,
            })
            previous = verdict, components

    # Rewind the whole series by identity after every later boundary has been published.
    for cutoff, (receipt, expected_digest) in reversed(tuple(seen.items())):
        assert digest(successful(read_confluence(client, session, cutoff))) == expected_digest
        assert digest(successful(read_confluence(client, session, cutoff, receipt))) == expected_digest

    successful(read_confluence(client, session, last, seen[first][0]), 409)
    successful(save_confluence(client, session, last, seen[first][0]), 409)
    successful(read_confluence(client, session, last, "0"*64), 409)
    successful(read_confluence(client, session, first - 1), 422)
    successful(client.get(f"/api/replay/sessions/{session}/confluence",
                          params={"cutoff": last, "profile": "OTHER"}), 422)
    other = replay(client, manifest, swap=True)
    if other != session:
        successful(read_confluence(client, other, last, seen[last][0]), 409)
        swapped = successful(read_confluence(client, other, last))
        direct = successful(read_confluence(client, session, last))
        scopes = ("status", "reason", "direction", "first_available_at", "components")
        assert all(swapped[key] == direct[key] for key in scopes), name
    return {
        "session": session, "case": name, "coverage_utc": [utc(manifest["start"]), utc(manifest["end"])],
        "sources": {name: {key: lane[key] for key in ("partitions", "basis", "csv_sha256") if key in lane}
                    for name, lane in manifest["symbols"].items()},
        "boundaries_checked": len(seen), "rewind_reads": 2 * len(seen), "transitions": transitions,
        "verdicts": {name: sum(c["verdict"] == name for c in checkpoints)
                     for name in ("ALIGNED", "CONTRADICTED", "NOT_ALIGNED", "INCOMPLETE")},
        "families": {key: len(value) for key, value in families.items()},
        "restart_probes": [{"cutoff": cutoff, "receipt_id": receipt, "payload_sha256": sha}
                           for cutoff, (receipt, sha) in seen.items()
                           if cutoff in {first, last} or any(t["utc"] == utc(cutoff) for t in transitions)],
    }


def local_environment(workspace):
    return {**os.environ, "TRADEFORGE_STORAGE": str(workspace),
            "TRADEFORGE_DATA_DIR": str(workspace / "research")}


def run_api(source, cases):
    scratch = ROOT / "storage"
    scratch.mkdir(exist_ok=True)
    results = {}
    with TemporaryDirectory(prefix="t10-confluence-oracle-", dir=scratch) as temporary:
        for name in cases:
            workspace = Path(temporary) / name
            workspace.mkdir()
            output = workspace / "result.json"
            command = (sys.executable, "-m", "scripts.verify_t10_confluence", "--output", str(output))
            env = local_environment(workspace)
            subprocess.run((*command, "--verify-case", name, "--source-storage", str(source)),
                           cwd=ROOT, env=env, check=True)
            if name in {"genuine", "delayed-overlap"}:
                subprocess.run((*command, "--restart"), cwd=ROOT, env=env, check=True)
            results[name] = json.loads(output.read_text(encoding="utf-8"))
    return {
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "verified_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "method": "Independent Decimal wick pivot and H1-to-M5 CRT lifecycles from verified source OHLC. "
                  "GET requests cover the entire pinned acceptance window at T-1 and T for each close "
                  "and delayed publication. Controlled OHLC rows are synthetic.",
        "cases": results,
    }


def restart_verify(client, output):
    case = json.loads(output.read_text(encoding="utf-8"))
    for probe in case["restart_probes"]:
        for receipt in (None, probe["receipt_id"]):
            response = successful(read_confluence(client, case["session"], probe["cutoff"], receipt))
            assert digest(response) == probe["payload_sha256"], (case["case"], probe)
    case["fresh_backend_restart_reads"] = 2 * len(case["restart_probes"])
    return case


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage", default=ROOT / "storage", type=Path)
    seed = parser.add_mutually_exclusive_group()
    seed.add_argument("--seed-browser", action="store_true")
    seed.add_argument("--seed-genuine", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", choices=("genuine", "controls", *CONTROLS))
    parser.add_argument("--verify-case", choices=("genuine", *CONTROLS), help=argparse.SUPPRESS)
    parser.add_argument("--restart", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not (args.seed_browser or args.seed_genuine or args.verify_case or args.restart):
        cases = ("genuine", *CONTROLS) if args.only is None else CONTROLS if args.only == "controls" else (args.only,)
        write_json(args.output, run_api(args.source_storage.resolve(), cases))
        return
    from fastapi.testclient import TestClient
    from research_backend.app.main import app

    if args.seed_browser:
        write_json(args.output, with_checkpoints(seed_controls(price_rows("delayed-overlap"))))
    elif args.seed_genuine:
        with TestClient(app) as client:
            write_json(args.output, genuine(client, args.source_storage))
    elif args.verify_case:
        with TestClient(app) as client:
            manifest = (genuine(client, args.source_storage) if args.verify_case == "genuine" else
                        with_checkpoints(seed_controls(price_rows(args.verify_case))))
            write_json(args.output, verify_case(client, args.verify_case, manifest))
    else:
        with TestClient(app) as client:
            write_json(args.output, restart_verify(client, args.output))


if __name__ == "__main__":
    main()
