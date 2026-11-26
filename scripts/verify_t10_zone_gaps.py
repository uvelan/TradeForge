"""Check public replay-zone receipt continuity against independent sparse M5 archives."""

import argparse
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


STEP = 300
DAY = "2026-11-13"
ROOT = Path(__file__).resolve().parents[1]
FVG = ((99, 100, 98, 99), (102, 105, 99, 104), (106, 108, 105, 107))
OB = ((9, 10, 8, 9), (10, 11, 9, 10), (11, 14, 10, 12),
      (10, 11, 9, 10), (10.5, 11, 9, 10), (12, 16, 12, 15))
FVG_QUIET = (106, 108, 105, 107)
OB_QUIET = (13, 14, 12, 13)
SCHEDULE = {"kind": "forex_weekly", "timezone": "America/New_York",
            "sunday_open": "17:00", "friday_close": "17:00"}
PANES = (("primary", "EURUSD"), ("2", "GBPUSD"))
MODES = ("wick", "close")
GAP_ROLES = ("GAP_PREVIOUS_OBSERVED", "GAP_NEXT_OBSERVED")
CASES = ("missing", "contiguous", "closure", "closure-missing", "delayed", "unverifiable")


def stamp(clock, day=DAY):
    return int(datetime.fromisoformat(f"{day}T{clock}:00+00:00").timestamp())


def iso(value):
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")


def bar(time, ohlc, delay=0):
    return {"time": time, **dict(zip(("open", "high", "low", "close"), map(float, ohlc))),
            "available_at": time + STEP + delay}


def lane(kind, formation, previous, successor, *, delay=0, missing=(), unverifiable=False):
    pattern, quiet = (FVG, FVG_QUIET) if kind == "FVG" else (OB, OB_QUIET)
    anchor = formation - (len(pattern) - 1) * STEP
    bars = [bar(anchor + i * STEP, ohlc) for i, ohlc in enumerate(pattern)]
    bars.extend(bar(time, quiet) for time in range(formation + STEP, previous + STEP, STEP))
    bars.append(bar(successor, quiet, delay))
    reason = "MISSING_OPEN_CANDLES" if missing else None
    if unverifiable:
        reason = "UNVERIFIABLE_CLOSURE"
    intervals = None if unverifiable else [{"start": start, "end": end} for start, end in missing]
    return {"type": kind, "anchor": anchor if kind == "FVG" else formation - STEP,
            "formation": formation, "previous": previous, "successor": successor,
            "T": successor + STEP + delay, "reason": reason, "missing": intervals,
            "bars": bars}


def fixtures(case):
    if case in {"missing", "contiguous", "delayed"}:
        delay = 17 if case == "delayed" else 0
        start = stamp("12:55")
        end = stamp("13:55") if delay else stamp("13:50")
        fvg_prev, ob_prev = stamp("13:15"), stamp("13:35")
        fvg_next, ob_next = stamp("13:25"), stamp("13:45")
        if case == "contiguous":
            fvg = lane("FVG", stamp("13:05"), fvg_next - STEP, fvg_next)
            ob = lane("OB", stamp("13:30"), ob_next - STEP, ob_next)
        else:
            fvg = lane("FVG", stamp("13:05"), fvg_prev, fvg_next, delay=delay, missing=((fvg_prev + STEP, fvg_next),))
            ob = lane("OB", stamp("13:30"), ob_prev, ob_next, delay=delay, missing=((ob_prev + STEP, ob_next),))
        fvg["bars"].extend(bar(time, FVG_QUIET) for time in range(fvg_next + STEP, end, STEP))
    else:
        # On these dates 17:00 New York is 22:00 UTC. Skip the entire weekend,
        # with an additional omitted market-open bar on each side in one case.
        close, reopened = stamp("22:00"), stamp("22:00", "2026-11-15")
        missing = case == "closure-missing"
        previous = close - STEP * (2 if missing else 1)
        # An archived Saturday observation falls outside the declared session.
        unverifiable = case == "unverifiable"
        successor = stamp("17:00", "2026-11-14") if unverifiable else reopened + STEP * int(missing)
        spans = ((close - STEP, close), (reopened, successor)) if missing else ()
        start, end = previous - 6 * STEP, successor + STEP
        fvg = lane("FVG", previous - STEP, previous, successor, missing=spans, unverifiable=unverifiable)
        ob = lane("OB", previous - STEP, previous, successor, missing=spans, unverifiable=unverifiable)
    return {"date_utc": DAY, "start": start, "end": end, "symbols": {"EURUSD": fvg, "GBPUSD": ob}}


def configure(directory, repo):
    os.environ["TRADEFORGE_STORAGE"] = str(directory)
    os.environ["TRADEFORGE_DATA_DIR"] = str(directory / "research")
    sys.path.insert(0, str(repo))


def write_fixtures(case):
    import pyarrow.parquet as parquet

    from research_backend.app import db, storage

    db.initialize()
    manifest = fixtures(case)
    for symbol, data in manifest["symbols"].items():
        bars = data["bars"]
        split = next(i for i, row in enumerate(bars) if row["time"] == data["successor"])
        shards = (bars[:split], bars[split:]) if case == "closure-missing" else (bars,)
        pins, origins = [], {}
        for shard in shards:
            part = storage.write_partition("DUKASCOPY", "controlled-t10-zone-gaps", symbol, "candles", "M5", "BID", shard)
            path = storage.MARKET_DIR / part["relative_path"]
            assert sha256(path.read_bytes()).hexdigest() == part["sha256"], path
            decoded = parquet.ParquetFile(path).read().to_pylist()
            assert decoded == shard, path
            pin = {key: part[key] for key in ("id", "sha256")}
            pins.append(pin)
            origins.update((row["time"], pin) for row in decoded)
        data.update(partitions=pins, origins=origins)
        if len(pins) == 1:
            data["partition"] = pins[0]
        else:
            assert len({pin["sha256"] for pin in pins}) == len(pins)
    return manifest


def get_json(response, expected=200):
    assert response.status_code == expected, (response.status_code, response.text)
    return response.json()


def replay(client, manifest):
    request = {"source": "DUKASCOPY_SAVED", "symbol": "EURUSD", "timeframe": "M5",
               "start": manifest["start"], "end": manifest["end"],
               "views": [{"view": "2", "symbol": "GBPUSD", "timeframe": "M5"}]}
    session = get_json(client.post("/api/replay/sessions", json=request), 201)
    for pane, symbol in PANES:
        capture = session["market"] if pane == "primary" else session["views"][pane]
        assert capture["partitions"] == manifest["symbols"][symbol]["partitions"], capture
    return session["id"]


def selective_proof(detail, data, expect_witness):
    revisions = detail["revisions"]
    terminal, *earlier = reversed(revisions)
    assert all("continuity" not in revision for revision in earlier), earlier
    assert "continuity" not in detail
    if expect_witness:
        expected = {"previous_observed_open": data["previous"], "next_observed_open": data["successor"],
                    "step_seconds": STEP, "schedule": SCHEDULE, "missing_open_intervals": data["missing"]}
        assert terminal["continuity"] == expected, terminal
    else:
        assert "continuity" not in terminal, terminal

    rows = detail["evidence"]
    assert all(row["roles"] for row in rows), rows
    assert [row["time"] for row in rows] == sorted({row["time"] for row in rows})
    observed = {row["time"]: row for row in data["bars"]}
    actual_roles = [(row["time"], role["role"], role["event_id"]) for row in rows for role in row["roles"]
                    if role["role"] in GAP_ROLES]
    expected_roles = [(data[key], role, terminal["id"]) for key, role in zip(("previous", "successor"), GAP_ROLES)]
    assert actual_roles == (expected_roles if expect_witness else []), (actual_roles, expected_roles)
    witnesses = []
    for key in ("previous", "successor"):
        stamp = data[key]
        match = [row for row in rows if row["time"] == stamp]
        if not match:
            assert not expect_witness
            continue
        row, = match
        pin = data["origins"][stamp]
        assert row["source_id"] == f"{pin['id']}:{stamp}" and row["partitions"] == [pin], row
        assert row["availability_basis"] == "ARCHIVE_SUPPLIED", row
        assert all(row[field] == value for field, value in observed[stamp].items()), row
        witnesses.append({"time_utc": iso(stamp), "available_at_utc": iso(row["available_at"]),
                          **{field: row[field] for field in ("source_id", "partitions", "availability_basis", "roles")}})
    return witnesses


def digest(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def legacy_detail(detail):
    """Project off the additive receipt fields to compare every previous field and role."""
    compatible = deepcopy(detail)
    for revision in compatible["revisions"]:
        revision.pop("continuity", None)
    for row in compatible["evidence"]:
        row["roles"] = [role for role in row["roles"] if role["role"] not in GAP_ROLES]
    compatible["evidence"] = [row for row in compatible["evidence"] if row["roles"]]
    return compatible


def view_receipts(client, session, view, mode, data, end, phase):
    def inspect(cutoff, **fields):
        result = get_json(client.get(f"/api/replay/sessions/{session}/zones",
                                     params=dict(view=view, mitigation=mode, cutoff=cutoff, **fields)))
        assert (result["session_id"], result["view"], result["cutoff"], result["authority"]) == (
            session, view, cutoff, "RESEARCH_ONLY")
        return result

    cutoff = data["T"]
    selectors = ("type", "anchor_time", "formation_time")
    key = data["type"], data["anchor"], data["formation"]
    zone, = (row for row in inspect(cutoff - 1)["zones"] if tuple(row[field] for field in selectors) == key)
    family = zone["zone_id"]
    formation = data["formation"] + STEP
    assert inspect(formation - 1, zone_id=family)["selected"] is None
    formed = inspect(formation, zone_id=family)["selected"]
    early = inspect(cutoff - 1, zone_id=family)["selected"]
    exact_response = inspect(cutoff, zone_id=family)
    exact = exact_response["selected"]
    assert (early["state"], early["revision"], early["formation_available_at"]) == ("ACTIVE", 1, formation), early
    assert early == formed and exact["zone_id"] == family
    assert len(early["revisions"]) == 1 and exact["revisions"][0] == early["revisions"][0]
    assert all(row["time"] < data["successor"] and row["available_at"] < cutoff for row in early["evidence"])
    assert selective_proof(early, data, False) == []
    if data["reason"]:
        assert (exact["state"], exact["reason"], exact["revision"]) == ("INVALIDATED", data["reason"], 2), exact
        assert len(exact["revisions"]) == 2
        assert (exact["observation_time"], exact["confirmation_time"], exact["available_at"]) == (
            data["successor"], data["successor"] + STEP, cutoff), exact
        assert exact["observed_price"] is None
        for field in ("lower", "upper", "remaining_lower", "remaining_upper", "mitigation_pct"):
            assert exact[field] == early[field], (field, early, exact)
        if data["type"] == "OB":
            assert exact["structure"] == early["structure"]
            assert exact["structure"]["type"] == "BOS" and exact["structure"]["status"] == "confirmed"
        witnesses = selective_proof(exact, data, phase == "fixed")
        expected_times = [data["previous"], data["successor"]] if phase == "fixed" else [data["successor"]]
        assert [row["time_utc"] for row in witnesses] == list(map(iso, expected_times)), witnesses
    else:
        assert early == exact, (early, exact)
        witnesses = selective_proof(exact, data, False)
        assert not witnesses
    terminal_summary, = (row for row in exact_response["zones"] if row["zone_id"] == family)
    assert terminal_summary == {key: value for key, value in exact.items()
                                if key not in {"evidence", "structure", "revisions"}}
    assert "continuity" not in terminal_summary
    assert inspect(end, zone_id=family)["selected"] == exact
    rewound = inspect(cutoff - 1, zone_id=family)["selected"]
    assert rewound == early

    fields = ("zone_id", "id", "state", "reason", "revision", "observation_time", "confirmation_time", "available_at")
    return {"T": iso(cutoff), "T-1": {key: early[key] for key in fields},
            "at_T": {key: exact[key] for key in fields}, "witness_rows": witnesses,
            "continuity": exact["revisions"][-1].get("continuity"),
            "prior_contract_sha256": digest([early, legacy_detail(exact), exact_response["zones"]]),
            "rewind_matches_T_minus_1": True}


def run(case, repo, phase):
    from fastapi.testclient import TestClient

    from research_backend.app.main import app

    manifest = write_fixtures(case)
    with TestClient(app) as client:
        session = replay(client, manifest)
        observed = {
            symbol: {mode: view_receipts(client, session, view, mode, manifest["symbols"][symbol], manifest["end"], phase)
                     for mode in MODES}
            for view, symbol in PANES
        }
    # Full fixtures are available through --seed-only. The receipt retains the
    # exact market clock and partition pins used in the comparison.
    for data in manifest["symbols"].values():
        data.pop("origins")
        data.pop("bars")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return {"case": case, "phase": phase, "git_revision": revision, "manifest": manifest,
            "session_id": session, "symbols": observed}


def compare(baseline, fixed):
    assert baseline["phase"] == "baseline" and fixed["phase"] == "fixed"
    for field in ("case", "manifest", "session_id"):
        assert baseline[field] == fixed[field], field
    observed = {}
    for _, symbol in PANES:
        observed[symbol] = {}
        for mode in MODES:
            before, after = (report["symbols"][symbol][mode] for report in (baseline, fixed))
            for field in ("T-1", "at_T", "prior_contract_sha256"):
                assert before[field] == after[field], (symbol, mode, field)
            observed[symbol][mode] = {
                "T": after["T"], "T-1": after["T-1"], "at_T": after["at_T"],
                "baseline_witness_rows": before["witness_rows"],
                "fixed_witness_rows": after["witness_rows"],
                "continuity": after["continuity"],
                "shared_prior_contract_sha256": after["prior_contract_sha256"],
                "rewind_matches_T_minus_1": before["rewind_matches_T_minus_1"] and after["rewind_matches_T_minus_1"],
            }
    return {"case": fixed["case"], "baseline_git_revision": baseline["git_revision"],
            "fixed_git_revision": fixed["git_revision"], "session_id": fixed["session_id"],
            "manifest": fixed["manifest"], "symbols": observed, "existing_receipts_match": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--storage", type=Path)
    parser.add_argument("--case", choices=CASES, default="missing")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--phase", choices=("baseline", "fixed"), default="baseline")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    if args.seed_only:
        if args.storage is None:
            parser.error("--seed-only requires an empty private --storage directory")
        directory = args.storage.resolve()
        if directory.exists() and any(directory.iterdir()):
            parser.error("--storage directory must be empty")
        configure(directory, repo)
        result = write_fixtures(args.case)
    else:
        with TemporaryDirectory(prefix="t10-zone-gaps-") as temp:
            configure(Path(temp), repo)
            result = run(args.case, repo, args.phase)
        if args.baseline:
            result = compare(json.loads(args.baseline.read_text(encoding="utf-8")), result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
