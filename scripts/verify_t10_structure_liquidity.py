"""Independent November 13 structure/liquidity reference and saved-replay API verification."""

import csv
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/implementation/evidence"
ARCHIVE_PIN = EVIDENCE / "TF-I10A1-dukascopy-nov13/pin.json"
CSV_PIN = EVIDENCE / "TF-I08E/numerical-checks.json"
DAY = "2026-11-13"
START = int(datetime.fromisoformat(f"{DAY}T00:00:00+00:00").timestamp())
END = int(datetime.fromisoformat(f"{DAY}T22:00:00+00:00").timestamp())
STEP = 300
SYMBOLS = ("EURUSD", "GBPUSD")
PRICE_FIELDS = ("open", "high", "low", "close")
PROFILE = {
    "algorithm_version": 2,
    "left": 2,
    "right": 2,
    "price_source": "wick",
    "ties": "strict",
    "tolerance_bps": 0,
    "break_bps": 0,
    "wick_candidates": True,
    "market_schedule": {"kind": "forex_weekly", "timezone": "America/New_York"},
}


def load_provider(storage, symbol):
    """Verify both independent CSV OHLC and the exact archive used by the application."""
    import pyarrow.parquet as parquet

    path = storage / "tf-i08e-source" / f"{symbol}_{DAY}_M5_BID.csv"
    original = path.read_bytes()
    digest = sha256(original).hexdigest()
    assert digest == json.loads(CSV_PIN.read_text(encoding="utf-8"))[symbol]["provider_m5_sha256"], path
    bars = []
    for raw in csv.DictReader(StringIO(original.decode("utf-8-sig"))):
        stamp = int(datetime.fromisoformat(raw["Etc/UTC"]).timestamp())
        if START <= stamp < END:
            bars.append({"time": stamp, "available_at": stamp + STEP,
                         **{key: Decimal(raw[key.title()]) for key in PRICE_FIELDS}})
    assert [bar["time"] for bar in bars] == list(range(START, END, STEP)), path
    selections = json.loads(ARCHIVE_PIN.read_text(encoding="utf-8"))["run"]["selection"]["series"]
    lane, = (series for series in selections.values()
             if (series["symbol"], series["timeframe"]) == (symbol, "M5"))
    pin, = lane["partitions"]
    archive = (storage / "research").resolve()
    with closing(sqlite3.connect((archive / "tradeforge.sqlite3").as_uri() + "?mode=ro", uri=True)) as database:
        relative, = database.execute("SELECT relative_path FROM datasets WHERE id=? AND sha256=?",
                                     (pin["id"], pin["sha256"])).fetchone()
    part = (archive / "market" / relative).resolve()
    part.relative_to(archive / "market")
    assert sha256(part.read_bytes()).hexdigest() == pin["sha256"], part
    decoded = [{"time": row["time"], "available_at": row.get("available_at") or row["time"] + STEP,
                **{key: Decimal(str(row[key])) for key in PRICE_FIELDS}}
               for row in parquet.ParquetFile(part).read().to_pylist() if START <= row["time"] < END]
    assert decoded == bars, (symbol, len(decoded), len(bars))
    return bars, digest, part, pin


def utc(stamp):
    return datetime.fromtimestamp(stamp, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def observation(kind, side, bar, anchor, price, *, status="confirmed", references=(), **fields):
    return {
        "type": kind, "side": side, "anchor_time": anchor,
        "observation_time": bar["time"], "available_at": bar["available_at"],
        "price": price, "status": status, "reference_opens": sorted(set(references)), **fields,
    }


def pivot_class(side, current, previous):
    if previous is None:
        return "first"
    if current == previous:
        return "equal"
    if side == "HIGH":
        return "HH" if current > previous else "LH"
    return "HL" if current > previous else "LL"


def structure_reference(bars):
    """Apply the documented chronological pivot and protected-break rules to Decimal OHLC."""
    assert all(a["time"] + STEP == b["time"] and a["available_at"] < b["available_at"]
               for a, b in zip(bars, bars[1:])), "This reference is for contiguous, monotonically available M5."
    active = {}
    spent = set()
    candidate_open = {}
    start = 0
    trend = None
    events = []
    confirmed_pivots = {}

    for index, bar in enumerate(bars):
        at = bar["time"]
        reverse = False
        for side, pivot in list(active.items()):
            anchor = pivot["anchor_time"]
            key = (side, anchor)
            price = pivot["price"]
            if key in spent:
                continue
            direction = "BULLISH" if side == "HIGH" else "BEARISH"
            sign = 1 if side == "HIGH" else -1
            outside_close = sign * (bar["close"] - price) > 0
            outside_wick = sign * (bar[side.lower()] - price) > 0
            if ((outside_close and pivot["available_at"] >= at + STEP)
                    or (not outside_close and pivot["available_at"] > at)):
                continue
            opposing = trend is not None and trend != direction
            refs = [*pivot["reference_opens"], at]
            candidate = candidate_open.get(key)
            if candidate is not None:
                refs.append(candidate)
            if outside_close:
                protected = pivot["classification"] == ("LH" if side == "HIGH" else "HL")
                kind = ("CHOCH" if protected else "COUNTERTREND_BREAK") if opposing else "BOS"
                moved = sign * (bar["close"] - bar["open"])
                event = observation(kind, side, bar, anchor, price, references=refs,
                                    direction=direction, displacement=moved, crossing_price=bar["close"],
                                    level_available_at=pivot["available_at"], candidate_time=candidate)
                events.append(event)
                if kind == "CHOCH" and pivot["available_at"] <= at and moved >= Decimal("0.00100"):
                    events.append({**event, "type": "MSS"})
                spent.add(key)
                reverse |= opposing
            elif outside_wick and key not in candidate_open:
                kind = "COUNTERTREND_CANDIDATE" if opposing else "WICK_CANDIDATE"
                events.append(observation(kind, side, bar, anchor, price, status="provisional",
                                         references=refs, direction=direction, crossing_price=bar[side.lower()],
                                         level_available_at=pivot["available_at"]))
                candidate_open[key] = at
            elif candidate is not None and not outside_wick:
                events.append(observation("WICK_INVALIDATED", side, bar, anchor, price, status="invalidated",
                                         references=refs, direction=direction, crossing_price=bar["close"],
                                         level_available_at=pivot["available_at"], candidate_time=candidate))
                candidate_open[key] = None

        if reverse:
            start = index
            trend = None
            active.clear()
            spent.clear()
            candidate_open.clear()

        if index - start < 4:
            continue
        first, second, middle, fourth, fifth = bars[index - 4:index + 1]
        refs = [item["time"] for item in (first, second, middle, fourth, fifth)]
        batch = []
        for side, extreme in (("HIGH", max), ("LOW", min)):
            field = side.lower()
            others = [item[field] for item in (first, second, fourth, fifth)]
            price = middle[field]
            if price == extreme([price, *others]) and price not in others:
                previous = active.get(side)
                pivot = observation("PIVOT", side, bar, middle["time"], price, references=refs,
                                    classification=pivot_class(side, price, previous and previous["price"]),
                                    anchor_index=index - 2)
                active[side] = pivot
                batch.append(pivot)
        if batch:
            confirmed_pivots[at] = batch
        if batch and trend is None and len(active) == 2:
            high, low = active["HIGH"], active["LOW"]
            codes = high["classification"], low["classification"]
            if high["anchor_time"] != low["anchor_time"] and len(batch) == 1:
                trend = {("HH", "HL"): "BULLISH", ("LH", "LL"): "BEARISH"}.get(codes)
    return events, confirmed_pivots


def liquidity_reference(bars, pivots):
    """Evaluate small independent price bands using a direct scan of their constituents."""
    groups = []
    result = []
    equality = Decimal("0.0005")

    def pool_event(kind, bar, group, **additional):
        levels = [member["price"] for member in group["members"]]
        lo, hi = min(levels), max(levels)
        references = [bar["time"]]
        for member in group["members"]:
            references.extend(member["reference_opens"])
        direct = {"level_activation_time": group["activation"], "level_revision_time": group["latest_open"],
                  "candidate_time": group.get("candidate_time")}
        references.extend(stamp for stamp in direct.values() if stamp is not None)
        first = group["members"][0]
        outer = hi if group["side"] == "HIGH" else lo
        return observation(kind, group["side"], bar, first["anchor_time"], outer,
                           references=references, lower=lo, upper=hi,
                           constituents=[member["anchor_time"] for member in group["members"]],
                           **direct, **additional)

    for index, bar in enumerate(bars):
        groups = [group for group in groups if index - group["created"] < 250]
        remaining = []
        for group in groups:
            members = group["members"]
            if len(members) < 2:
                remaining.append(group)
                continue
            high_side = group["side"] == "HIGH"
            sign = 1 if high_side else -1
            bounds = sorted(sign * member["price"] for member in members)
            outer = bounds[-1]
            inner = bounds[0]
            wick = sign * bar[group["side"].lower()]
            close = sign * bar["close"]
            if ((close > outer and group["latest"] >= bar["time"] + STEP)
                    or (close <= outer and wick > sign * group["anchor"] and group["latest"] > bar["time"])):
                continue
            if close > outer:
                result.append(pool_event("BROKEN", bar, group, crossing_price=bar["close"]))
            elif wick > outer and close < inner:
                result.append(pool_event("SWEPT", bar, group, crossing_price=bar["close"]))
            else:
                if wick > outer and not group["candidate"]:
                    result.append(pool_event("SWEEP_CANDIDATE", bar, group, status="provisional",
                                             crossing_price=bar[group["side"].lower()]))
                    group["candidate"] = True
                    group["candidate_time"] = bar["time"]
                    group["latest"] = bar["available_at"]
                    group["latest_open"] = bar["time"]
                remaining.append(group)
        groups = remaining

        for pivot in pivots.get(bar["time"], ()):
            price = pivot["price"]
            side = pivot["side"]
            eligible = sorted((group for group in groups if group["side"] == side
                               and abs(group["anchor"] - price) <= equality * group["anchor"]),
                              key=lambda group: abs(group["anchor"] - price))
            if eligible:
                group = eligible[0]
                if len(eligible) > 1:
                    assert abs(group["anchor"] - price) != abs(eligible[1]["anchor"] - price), (
                        "This sample needs the documented hash-ID tie-breaker", utc(bar["time"]), eligible[:2])
                if (pivot["anchor_index"] - group["members"][-1]["anchor_index"] < 2
                        or len(group["members"]) == 16):
                    continue
            else:
                group = {"anchor": price, "created": index, "side": side, "members": [], "candidate": False}
                groups.append(group)
            group["members"].append(pivot)
            if len(group["members"]) == 2:
                group["activation"] = bar["time"]
            if len(group["members"]) >= 2 and not group["candidate"]:
                result.append(pool_event("EQH" if side == "HIGH" else "EQL", bar, group))
            group["latest"] = bar["available_at"]
            group["latest_open"] = bar["time"]
    return result


def exact_events(bars):
    breaks, pivots = structure_reference(bars)
    events = [*breaks, *liquidity_reference(bars, pivots)]
    return sorted(events, key=lambda event: (event["available_at"], event["observation_time"],
                                            event["type"], event["anchor_time"], event["side"]))


def serialize(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value))


def output_json(target, value):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, default=serialize, indent=2) + "\n", encoding="utf-8")


def normalize(events):
    for event in events:
        event["level_price"] = event.pop("price")
        event.setdefault("direction", None)
        event["confirmation_time"] = (
            None if event["type"] in {"WICK_CANDIDATE", "COUNTERTREND_CANDIDATE", "WICK_INVALIDATED"}
            else event["observation_time"] + STEP)
        for field in ("anchor_time", "observation_time", "confirmation_time", "available_at"):
            event[field.removesuffix("_time") + "_utc"] = utc(event[field]) if event[field] is not None else None
    return events


def controlled_fixtures(lanes):
    """Disposable, labeled changes to genuine prices/availability, never provider observations."""
    eur, gbp = (lanes[symbol]["bars"] for symbol in SYMBOLS)
    wick, *_ = (event for event in lanes["EURUSD"]["events"] if event["type"] == "WICK_CANDIDATE")
    activation, *_ = (event for event in lanes["GBPUSD"]["events"] if event["type"] == "EQH")
    wick_bars = [dict(bar) for bar in eur if bar["available_at"] <= wick["available_at"]]
    crossed = [dict(bar) for bar in wick_bars]
    crossed[-1]["close"] = crossed[-1][wick["side"].lower()]
    delayed_bars = [dict(bar) for bar in gbp if bar["available_at"] <= activation["available_at"] + 3 * STEP]
    for bar in delayed_bars:
        if bar["time"] == activation["observation_time"]:
            bar["available_at"] += 20
    cases = {
        "wick_close_witness": {"symbol": "EURUSD", "bars": wick_bars,
                               "description": "Provider price prefix as the wick-cross comparison control."},
        "close_through_same_level": {"symbol": "EURUSD", "bars": crossed,
                                    "description": "Set the final close to the same candle's crossing wick."},
        "late_pivot_confirmation": {"symbol": "GBPUSD", "bars": delayed_bars,
                                    "description": "Delay availability of the final EQH confirmation child by 20 seconds, preserving the OHLC."},
    }
    for case in cases.values():
        case["events"] = normalize(exact_events(case["bars"]))
    close = cases["close_through_same_level"]["events"]
    assert any(event["type"] == "BOS" and event["observation_time"] == wick["observation_time"]
               and event["level_price"] == wick["level_price"] for event in close)
    late = cases["late_pivot_confirmation"]["events"]
    assert any(event["type"] == "EQH" and event["anchor_time"] == activation["anchor_time"]
               and event["available_at"] == activation["available_at"] + 20 for event in late)
    return cases


def reference_document(storage):
    symbols = {}
    for symbol in SYMBOLS:
        bars, digest, _, pin = load_provider(storage, symbol)
        symbols[symbol] = {"csv_sha256": digest, "partition": pin, "bars": bars,
                           "events": normalize(exact_events(bars))}
    return {"date_utc": DAY, "range": [START, END], "profile": PROFILE, "symbols": symbols,
            "controlled": controlled_fixtures(symbols)}



SUMMARY_FIELDS = ("type", "side", "anchor_time", "observation_time", "confirmation_time", "available_at",
                  "level_price", "direction", "status")


def signature(event):
    return tuple(Decimal(str(event[field])) if field == "level_price" else event[field]
                 for field in SUMMARY_FIELDS)


def check_summary(actual, expected):
    assert signature(actual) == signature(expected), (actual, expected)
    assert actual["rule_version"] == 1
    assert len(actual["id"]) == 64 and all(char in "0123456789abcdef" for char in actual["id"])
    dependencies = actual["supporting_ids"]
    assert len(dependencies) >= (2 if expected.get("constituents") else 1)
    assert len(set(dependencies)) == len(dependencies) and all(len(item) == 64 for item in dependencies)
    if "constituents" in expected:
        for field in ("lower", "upper"):
            assert Decimal(str(actual[field])) == expected[field], (actual, expected)
    else:
        assert Decimal(str(actual["break_price"])) == expected["crossing_price"], (actual, expected)
        assert actual["level_available_at"] == expected["level_available_at"], (actual, expected)
        if actual["type"] == "MSS":
            assert Decimal(str(actual["displacement"])) == expected["displacement"], (actual, expected)


def check_direct_roles(selected, expected, rows):
    from collections import Counter

    roles = {(row["time"], role["role"], role["event_id"]) for row in rows for role in row["roles"]}
    assert sum(len(row["roles"]) for row in rows) == len(roles), selected["id"]
    wanted = Counter({(expected["observation_time"], "EVENT_OBSERVATION"): 1})
    assert (expected["observation_time"], "EVENT_OBSERVATION", selected["id"]) in roles
    for field, role in (("level_activation_time", "LEVEL_ACTIVATION"),
                        ("level_revision_time", "LEVEL_REVISION"), ("candidate_time", "CANDIDATE_OBSERVATION")):
        if expected.get(field) is not None:
            wanted[expected[field], role] += 1
    anchors = expected.get("constituents", [expected["anchor_time"]])
    pivot_ids = []
    for anchor in anchors:
        pivot, = (item[2] for item in roles if item[:2] == (anchor, "PIVOT_ANCHOR"))
        pivot_ids.append(pivot)
        for offset, role in ((-2, "PIVOT_LEFT"), (-1, "PIVOT_LEFT"), (0, "PIVOT_ANCHOR"),
                             (1, "PIVOT_RIGHT"), (2, "PIVOT_RIGHT"), (2, "PIVOT_CONFIRMATION")):
            stamp = anchor + STEP * offset
            assert (stamp, role, pivot) in roles, (selected["id"], utc(stamp), role)
            wanted[stamp, role] += 1
    ids = selected["supporting_ids"]
    if selected["type"] == "MSS":
        ids = ids[1:]  # Its parent CHOCH has the same pivot and witness.
    assert pivot_ids == ids[:len(pivot_ids)], (selected["id"], pivot_ids, ids)
    if expected.get("candidate_time") is not None:
        assert (expected["candidate_time"], "CANDIDATE_OBSERVATION", ids[-1]) in roles, selected["id"]
    assert Counter((stamp, role) for stamp, role, _ in roles) == wanted, (selected["id"], roles, wanted)
    assert {stamp for stamp, _, _ in roles} == set(expected["reference_opens"]), selected["id"]
    return sorted(roles)


def check_receipt(selected, expected, lane, *, basis):
    check_summary(selected, expected)
    rows = selected["evidence"]
    expected_rows = [bar for bar in lane["bars"]
                     if bar["time"] <= expected["observation_time"] and bar["available_at"] <= expected["available_at"]]
    assert len(rows) == len(expected_rows), (selected["id"], len(rows), len(expected_rows))
    assert set(expected["reference_opens"]).issubset(row["time"] for row in rows)
    part = lane["partition"]
    for reference, bar in zip(rows, expected_rows):
        for field in ("time", "available_at"):
            assert reference[field] == bar[field], (field, reference, bar)
        assert reference["partitions"] == [part]
        assert reference["source_id"] == f"{part['id']}:{bar['time']}"
        assert reference["availability_basis"] == basis, reference
        assert all(Decimal(str(reference[key])) == bar[key] for key in PRICE_FIELDS), (reference, bar)
    roles = check_direct_roles(selected, expected, rows)
    return {
        "id": selected["id"],
        "supporting_ids": selected["supporting_ids"],
        "receipt_rows": len(rows),
        "receipt_sha256": sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "source_roles": [{"time_utc": utc(stamp), "source_id": f"{part['id']}:{stamp}", "role": role, "event_id": event}
                         for stamp, role, event in roles],
    }


def make_session(client, symbol, end, compare=None):
    request = {"source": "DUKASCOPY_SAVED", "symbol": symbol, "timeframe": "M5", "start": START, "end": end,
               "views": [{"view": "comparison", "symbol": compare, "timeframe": "M5"}] if compare else []}
    response = client.post("/api/replay/sessions", json=request)
    assert response.status_code == 201, (response.status_code, response.text)
    return response.json()["id"]


def inspect(client, session_id, view, cutoff, **fields):
    response = client.get(f"/api/replay/sessions/{session_id}/structure-liquidity",
                          params={"view": view, "cutoff": cutoff, **fields})
    assert response.status_code == 200, (view, utc(cutoff), fields, response.status_code, response.text[:1200])
    result = response.json()
    assert (result["session_id"], result["view"], result["cutoff"]) == (session_id, view, cutoff), result
    assert result["authority"] == "RESEARCH_ONLY"
    assert (result["source"], result["timeframe"], result["price_basis"]) == ("DUKASCOPY_SAVED", "M5", "BID")
    return result


def all_summaries(client, session_id, view, cutoff):
    latest = inspect(client, session_id, view, cutoff)
    pages = [latest["events"]]
    before = latest["previous_before"]
    while before is not None:
        page = inspect(client, session_id, view, cutoff, before=before)
        assert page["end"] == before and page["total"] == latest["total"]
        pages.append(page["events"])
        before = page["previous_before"]
    ledger = [event for group in reversed(pages) for event in group]
    assert len(ledger) == latest["total"]
    assert len({event["id"] for event in ledger}) == len(ledger)
    assert ledger == sorted(ledger, key=lambda item: (item["available_at"], item["observation_time"],
                                                     item["type"], item["id"]))
    return {signature(event): event for event in ledger}


def verify_lane(client, session_id, view, symbol, lane, end, *, basis):
    from collections import Counter

    observed = all_summaries(client, session_id, view, end)
    expected = {signature(event): event for event in lane["events"]}
    assert len(expected) == len(lane["events"])
    missing, extra = expected.keys() - observed.keys(), observed.keys() - expected.keys()
    assert not missing and not extra, (symbol, sorted(missing, key=str), sorted(extra, key=str))

    comparisons = []
    boundaries = set()
    for key, event in expected.items():
        summary = observed[key]
        at = event["available_at"]
        before = inspect(client, session_id, view, at - 1, event_id=summary["id"])
        boundaries.add(at)
        assert before["selected"] is None
        assert before["total"] == sum(item["available_at"] < at for item in expected.values()), (
            symbol, utc(at - 1), before["total"])
        assert all(item["id"] != summary["id"] for item in before["events"]), summary
        readout = inspect(client, session_id, view, at, event_id=summary["id"])
        assert readout["symbol"] == symbol
        assert readout["total"] == sum(item["available_at"] <= at for item in expected.values())
        assert summary in readout["events"], (symbol, at, summary)
        receipt = check_receipt(readout["selected"], event, lane, basis=basis)
        if event["type"] == "MSS":
            parent = observed[signature({**event, "type": "CHOCH"})]
            assert receipt["supporting_ids"][0] == parent["id"], readout["selected"]
        comparisons.append({**event, **receipt, "before_utc": utc(at - 1), "before_total": before["total"],
                            "published_total": readout["total"]})
    return {"type_counts": dict(Counter(event["type"] for event in comparisons)),
            "distinct_availability_boundaries_checked": len(boundaries),
            "exact_receipts_checked": len(comparisons), "events": comparisons}


def run_api(source, target, reference):
    from contextlib import contextmanager
    import os
    import subprocess
    from tempfile import TemporaryDirectory

    from fastapi.testclient import TestClient

    started = datetime.now(UTC).isoformat(timespec="seconds")
    scratch = ROOT / "storage"
    scratch.mkdir(exist_ok=True)

    @contextmanager
    def private_storage(directory):
        original = db.DATABASE, storage.MARKET_DIR
        db.DATABASE = directory / "tradeforge.sqlite3"
        storage.MARKET_DIR = directory / "market"
        directory.mkdir()
        db.initialize()
        try:
            yield
        finally:
            db.DATABASE, storage.MARKET_DIR = original

    with TemporaryDirectory(prefix="t10-structure-oracle-", dir=scratch) as workspace:
        workspace = Path(workspace)
        os.environ["TRADEFORGE_STORAGE"] = str(workspace)
        os.environ["TRADEFORGE_DATA_DIR"] = str(workspace / "research")
        from research_backend.app import db, storage
        from research_backend.app.main import app

        symbols = reference["symbols"]
        with TestClient(app) as client:
            for symbol in SYMBOLS:
                _, _, path, pin = load_provider(source, symbol)
                response = client.post("/api/market/import/reviewed", files={"file": (path.name, path.read_bytes())})
                assert response.status_code == 200, response.text
                imported, = response.json()["partitions"]
                assert {key: imported[key] for key in ("id", "sha256")} == pin

            short_end = START + 12 * 3600
            short = make_session(client, "EURUSD", short_end, "GBPUSD")
            full = make_session(client, "EURUSD", END, "GBPUSD")
            swapped = make_session(client, "GBPUSD", END, "EURUSD")
            assert len({short, full, swapped}) == 3
            genuine = {}
            for view, symbol in zip(("primary", "comparison"), SYMBOLS):
                lane = symbols[symbol]
                verified = verify_lane(client, full, view, symbol, lane, END, basis="MODELED_NOMINAL_CLOSE")
                verified.update(csv_sha256=lane["csv_sha256"], partition=lane["partition"], candles=len(lane["bars"]))
                close_before = all_summaries(client, short, view, short_end)
                close_after = all_summaries(client, full, view, short_end)
                swapped_view = "comparison" if view == "primary" else "primary"
                close_swapped = all_summaries(client, swapped, swapped_view, short_end)
                assert close_before == close_after == close_swapped, (symbol, "append/rewind/pane swap")
                checked = close_before[next(iter(close_before))]
                replayed = [
                    inspect(client, session, pane, short_end, event_id=checked["id"])["selected"]
                    for session, pane in ((short, view), (full, view), (swapped, swapped_view))
                ]
                assert replayed[0] == replayed[1] == replayed[2]
                verified.update(fixed_cutoff_append_and_pane_swap={
                    "cutoff_utc": utc(short_end), "stable_events": len(close_before), "stable_receipt_id": checked["id"],
                    "sessions": [short, full, swapped],
                })
                genuine[symbol] = verified

        controlled = {}
        for label, lane in reference["controlled"].items():
            symbol = lane["symbol"]
            with private_storage(workspace / label), TestClient(app) as client:
                rows = [{key: float(item) if isinstance(item, Decimal) else item for key, item in bar.items()}
                        for bar in lane["bars"]]
                part = storage.write_partition("DUKASCOPY", "controlled-t10-structure", symbol,
                                               "candles", "M5", "BID", rows)
                lane["partition"] = {key: part[key] for key in ("id", "sha256")}
                end = rows[-1]["time"] + STEP
                session = make_session(client, symbol, end)
                controlled[label] = verify_lane(client, session, "primary", symbol, lane, end,
                                                basis="ARCHIVE_SUPPLIED")
                controlled[label].update(description=lane["description"], symbol=symbol,
                                         partition=lane["partition"], session_id=session)
                if label == "late_pivot_confirmation":
                    delayed, = (row for row in rows if row["available_at"] != row["time"] + STEP)
                    late, = (event for event in controlled[label]["events"] if event["type"] == "EQH")
                    nominal = delayed["time"] + STEP
                    unavailable = inspect(client, session, "primary", nominal, event_id=late["id"])
                    assert unavailable["selected"] is None, unavailable
                    controlled[label]["nominal_close_unavailable"] = utc(nominal)

    output_json(target, {
        "date_utc": DAY, "range_utc": [utc(START), utc(END)], "profile": PROFILE,
        "started_at": started, "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "method": "Decimal OHLC from checksummed provider CSV; independent comparisons to pinned Parquet and "
                  "public saved-replay summaries and selected receipts. The provider CSV and Parquet stayed local.",
        "symbols": genuine, "controlled": controlled,
    })


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-only", action="store_true")
    args = parser.parse_args()
    reference = reference_document(args.source_storage)
    if args.reference_only:
        output_json(args.output, reference)
    else:
        run_api(args.source_storage, args.output, reference)


if __name__ == "__main__":
    main()

