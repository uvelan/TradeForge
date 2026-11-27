"""Verify native saved replay through the production BI5 import and replay HTTP API."""

import argparse
from bisect import bisect_left
import csv
from collections import defaultdict
from datetime import UTC, date, datetime
from hashlib import sha256
import json
import lzma
import os
from pathlib import Path
import struct
import subprocess
import sys

import httpx


START = int(datetime(2024, 4, 1, tzinfo=UTC).timestamp())
SLOTS = 5007
END = START + 300 * SLOTS
SIZE = 5000
SOURCE = "DUKASCOPY_SAVED"
LANES = (
    ("primary", "EURUSD", "M5", 100_000, 108_000),
    ("2", "GBPUSD", "M1", 100_000, 126_000),
    ("3", "USDJPY", "M5", 1_000, 139_000),
    ("4", "XAUUSD", "M1", 1_000, 2050_000),
)
GENUINE_SHA256 = {
    13: "2f47bcb14e0a2c4dcd6d65eb819b85f1b5168b656d1f5f67c3dcd2240569f014",
    14: "9d40aa3147d93e6daa2218933877adb9471dc02ff08b19fc0d272f1eb781688f",
}
QUOTE = struct.Struct(">IIIff")
FIELDS = ("open", "high", "low", "close")


def request(client, method, endpoint, status=200, **kwargs):
    response = client.request(method, endpoint, **kwargs)
    assert response.status_code == status, (
        method, endpoint, status, response.status_code, response.text[:500]
    )
    return response.json()


def controlled_files(lane_index, frame, basis):
    """Make four unique, sparse source histories and a bar just beyond the requested end."""
    days = defaultdict(bytearray)
    omitted = {1 + lane_index, *(715 * i + 11 * lane_index for i in range(1, 7))}
    for slot in range(SLOTS + 1):
        if slot in omitted:
            continue
        stamp = START + slot * 300
        if frame == "M1":
            stamp += 60 * (1 + (slot + lane_index) % 4)
        day_start = stamp // 86400 * 86400
        offset = (stamp - day_start) * 1000
        opening = basis + slot // 19 + (slot * (7 + lane_index * 2)) % 127
        # Low and high share a timestamp. Original ordinal determines their order.
        quotes = (
            (109, opening), (703, opening - 7 - lane_index),
            (703, opening + 11 + lane_index), (850, opening + slot % 7 - 3),
        )
        for millis, bid in quotes:
            days[day_start] += QUOTE.pack(offset + millis, bid + 3, bid, 1.0, 2.0)
    for stamp, raw in days.items():
        yield stamp, None, lzma.compress(raw, format=lzma.FORMAT_ALONE, preset=1)


def independent_bars(files, scale, timeframe, start, end):
    """Fold original ordered BID observations into UTC bars, with no product imports."""
    step = int(timeframe[1:]) * 60
    observations = defaultdict(list)
    for epoch, _, payload in files:
        raw = lzma.decompress(payload)
        ordered = sorted(
            enumerate(QUOTE.iter_unpack(raw)), key=lambda record: (record[1][0], record[0])
        )
        for _, (offset, _, bid, _, _) in ordered:
            stamp = epoch + offset // 1000
            bucket = stamp - stamp % step
            if start <= bucket < end:
                observations[bucket].append(bid)
    return [
        {"time": stamp, "available_at": stamp + step, **{
            field: value / scale for field, value in zip(
                FIELDS, (bid[0], max(bid), min(bid), bid[-1]), strict=True
            )
        }}
        for stamp, bid in sorted(observations.items())
    ]


def upload(client, symbol, timeframe, files):
    pins, receipts = [], []
    for epoch, hour, payload in files:
        params = {"symbol": symbol, "day": datetime.fromtimestamp(epoch, UTC).date().isoformat()}
        if hour is not None:
            params["hour"] = hour
        result = request(client, "POST", "/api/market/import/bi5", params=params, files={
            "file": ("quotes.bi5", payload, "application/octet-stream"),
        })
        pin = sha256(payload).hexdigest()
        assert result["source"]["sha256"] == pin and result["source"]["start"] == epoch
        part, = (
            part for part in result["candle_datasets"]
            if (part["timeframe"], part["price_basis"]) == (timeframe, "BID")
        )
        pins.append({key: part[key] for key in ("id", "sha256")})
        receipts.append({"symbol": symbol, "start": epoch, "sha256": pin, "bytes": len(payload),
                         "quotes": result["quality"]["source_records"]})
    return pins, receipts


def bind_pins(pane, bounds, basis="MODELED_NOMINAL_CLOSE"):
    for bar in pane["candles"]:
        bar["availability_basis"] = basis
        bar["source_partition_indexes"] = [index for index, (start, end) in enumerate(bounds)
                                           if start <= bar["time"] < end]
        assert bar["source_partition_indexes"]


def bi5_pins(files):
    return [(epoch, epoch + (86400 if hour is None else 3600)) for epoch, hour, _ in files]


def controlled(client, lanes=LANES):
    panes, sources = {}, []
    for index, (view, symbol, frame, scale, basis) in enumerate(lanes):
        files = list(controlled_files(index, frame, basis))
        candles = independent_bars(files, scale, frame, START, END)
        assert len(candles) == SIZE
        pins, receipts = upload(client, symbol, frame, files)
        panes[view] = {"symbol": symbol, "timeframe": frame, "candles": candles, "partitions": pins}
        bind_pins(panes[view], bi5_pins(files))
        sources.extend(receipts)
        print(f"{view} {symbol} {frame}: {len(candles)} bars from {len(receipts)} imports", flush=True)
    return panes, sources


def verify_snapshot(session, panes):
    assert set(session["views"]) == set(panes) - {"primary"}
    for view, expected in panes.items():
        captured = session["market"] if view == "primary" else session["views"][view]
        for key in ("symbol", "timeframe"):
            assert captured[key] == expected[key], (view, key)
        def key(pin):
            return pin["id"], pin["sha256"]

        assert sorted(map(key, captured["partitions"])) == sorted(map(key, expected["partitions"])), (
            view, "partitions"
        )
        if view == "primary":
            assert captured["replay_cutoffs"] == sorted(bar["available_at"] for bar in expected["candles"])
        assert captured["source"] == SOURCE and captured["price_basis"] == "BID", view
        assert len(captured["candles"]) == len(expected["candles"]), view
        for actual, target in zip(captured["candles"], expected["candles"], strict=True):
            observed = {field: actual[field] for field in target if field != "source_partition_indexes"}
            desired = {field: value for field, value in target.items() if field != "source_partition_indexes"}
            assert observed == desired, (view, observed, desired)
            actual_pins = [key(captured["partitions"][index]) for index in actual["source_partition_indexes"]]
            oracle_pins = [key(expected["partitions"][index]) for index in target["source_partition_indexes"]]
            assert sorted(actual_pins) == sorted(oracle_pins), (view, observed["time"], actual_pins, oracle_pins)
        coverage = captured["coverage"]
        start, end = session["request"]["start"], session["request"]["end"]
        step = int(expected["timeframe"][1:]) * 60
        gaps = []
        cursor = start
        for bar in expected["candles"]:
            if bar["time"] > cursor:
                gaps.append({"start": cursor, "end": bar["time"]})
            cursor = bar["time"] + step
        if cursor < end:
            gaps.append({"start": cursor, "end": end})
        assert coverage == {"start": start, "end": end, "gaps": gaps,
                            "duplicate_bars": expected.get("duplicate_bars", 0),
                            "truncated": False}, view


def save(client, payload, panes, sources, checkpoint):
    session = request(client, "POST", "/api/replay/sessions", 201, json=payload)
    verify_snapshot(session, panes)
    session_id = session["id"]
    actual = request(client, "POST", f"/api/replay/sessions/{session_id}/checkpoint", json=checkpoint)
    assert actual == checkpoint, actual
    return {"request": payload, "session_id": session_id, "checkpoint": checkpoint,
            "panes": panes, "sources": sources}


def baseline(client):
    panes, sources = controlled(client, LANES[:1])
    first_601 = panes["primary"]["candles"][:601]
    payload = {"symbol": "EURUSD", "timeframe": "M5", "source": SOURCE,
               "start": START, "end": first_601[-1]["available_at"]}
    market = request(client, "GET", "/api/market/candles", params={**payload, "limit": 600})
    assert market["coverage"]["truncated"] and len(market["candles"]) == 600
    assert [bar["time"] for bar in market["candles"]] == [bar["time"] for bar in first_601[1:]]
    reply = request(client, "POST", "/api/replay/sessions", 422, json=payload)
    accepted = request(client, "POST", "/api/replay/sessions", 201, json={
        **payload, "end": first_601[-2]["available_at"],
    })
    assert len(accepted["market"]["candles"]) == 600
    return {"request": payload, "range_count": len(first_601), "market_count": len(market["candles"]),
            "market_coverage": market["coverage"], "replay_status": 422, "replay": reply,
            "accepted_count": len(accepted["market"]["candles"]), "accepted_session": accepted["id"],
            "sources": sources}


def prepare(client):
    panes, sources = controlled(client)
    payload = {"symbol": "EURUSD", "timeframe": "M5", "source": SOURCE,
               "start": START, "end": END, "window_bars": SIZE,
               "views": [{"view": view, "symbol": pane["symbol"], "timeframe": pane["timeframe"]}
                         for view, pane in panes.items() if view != "primary"]}
    checkpoint = {"position": SIZE - 1, "bookmarks": [602, SIZE - 1],
                  "selected": {"view": "3", "time": panes["3"]["candles"][-2]["time"]}}
    receipt = save(client, payload, panes, sources, checkpoint)
    rejected = request(client, "POST", "/api/replay/sessions", 422,
                       json={**payload, "end": END + 300})
    receipt["oversized_range"] = {"status": 422, "response": rejected}
    endpoint = f"/api/replay/sessions/{receipt['session_id']}/checkpoint"
    rewind = {**checkpoint, "position": 602}
    assert request(client, "POST", endpoint, json=rewind) == rewind
    invalid = request(client, "POST", endpoint, 422, json={
        **checkpoint, "selected": {**checkpoint["selected"], "time": checkpoint["selected"]["time"] + 1},
    })
    assert request(client, "POST", endpoint, json=checkpoint) == checkpoint
    receipt["checkpoint_controls"] = {"rewind_kept_pin": True, "unknown_candle_status": 422,
                                      "unknown_candle": invalid}
    receipt["source_kind"] = "CONTROLLED_BI5"
    return receipt


def genuine_bi5(client, directory):
    day = date(2025, 11, 13)
    start_day = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    files = []
    for hour, digest in GENUINE_SHA256.items():
        payload = (directory / f"{hour}h_ticks.bi5").read_bytes()
        assert sha256(payload).hexdigest() == digest, (day, hour)
        files.append((start_day + hour * 3600, hour, payload))
    start, end = files[0][0], files[-1][0] + 3600
    panes = {}
    sources = []
    for view, frame in (("primary", "M5"), ("2", "M1")):
        candles = independent_bars(files, 100_000, frame, start, end)
        pins, receipts = upload(client, "EURUSD", frame, files)
        panes[view] = {"symbol": "EURUSD", "timeframe": frame, "candles": candles, "partitions": pins}
        bind_pins(panes[view], bi5_pins(files))
        if not sources:
            sources = receipts
    payload = {"symbol": "EURUSD", "timeframe": "M5", "source": SOURCE, "start": start,
               "end": end, "window_bars": SIZE,
               "views": [{"view": "2", "symbol": "EURUSD", "timeframe": "M1"}]}
    checkpoint = {"position": len(panes["primary"]["candles"]) - 1, "bookmarks": [0],
                  "selected": {"view": "2", "time": panes["2"]["candles"][-2]["time"]}}
    return {**save(client, payload, panes, sources, checkpoint), "source_kind": "GENUINE_BI5"}


def delayed(client, storage):
    """Write a small staggered-delivery archive through the production partition writer."""
    start = START - 7 * 86400
    panes = {}
    publications = []
    for view, symbol, frame, prices, opens, arrivals in (
        ("primary", "EURUSD", "M5", [1.081, 1.082, 1.083, 1.084], [0, 300, 600, 900],
         [900, 600, 900, 1200]),
        ("2", "GBPUSD", "M1", [1.261, 1.262, 1.263], [60, 120, 180], [420, 1200, 350]),
    ):
        candles = [{"time": start + timestamp, "available_at": start + arrival, "open": price,
                    "high": price + .0003, "low": price - .0001, "close": price + .0002}
                   for timestamp, arrival, price in zip(opens, arrivals, prices, strict=True)]
        pane = {"symbol": symbol, "timeframe": frame, "candles": candles, "partitions": []}
        if view == "primary":
            groups = [candles[:2], candles[1:]]
            pane["duplicate_bars"] = 1
        else:
            groups = [candles]
        bind_pins(pane, [(rows[0]["time"], rows[-1]["time"] + 1) for rows in groups], "ARCHIVE_SUPPLIED")
        for rows in groups:
            keys = ("time", "available_at", *FIELDS)
            publications.append({"view": view, "symbol": symbol, "frame": frame,
                                 "rows": [{key: bar[key] for key in keys} for bar in rows]})
        panes[view] = pane
    publish = (
        "import json,sys; from research_backend.app.db import initialize; "
        "from research_backend.app.storage import write_partition; initialize(); "
        "print(json.dumps([write_partition('DUKASCOPY','native-availability-control', "
        "p['symbol'],'candles',p['frame'],'BID',p['rows']) for p in json.load(sys.stdin)]))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", publish], check=True, capture_output=True, text=True,
        input=json.dumps(publications),
        env=storage_env(storage),
    )
    for item, pin in zip(publications, json.loads(proc.stdout), strict=True):
        panes[item["view"]]["partitions"].append({key: pin[key] for key in ("id", "sha256")})
    payload = {"symbol": "EURUSD", "timeframe": "M5", "source": SOURCE,
               "start": start, "end": start + 1200, "window_bars": SIZE,
               "views": [{"view": "2", "symbol": "GBPUSD", "timeframe": "M1"}]}
    checkpoint = {"position": 3, "bookmarks": [0, 1],
                  "selected": {"view": "primary", "time": start}}
    result = save(client, payload, panes, [], checkpoint)
    return {**result, "source_kind": "CONTROLLED_ARCHIVE_AVAILABILITY"}


def storage_env(storage):
    return {**os.environ, "TRADEFORGE_STORAGE": str(storage.resolve()),
            "TRADEFORGE_DATA_DIR": str(storage.resolve() / "research")}


def genuine_csv(directory, storage=None):
    """Independently inventory eight retained UTC BID days per instrument."""
    evidence = Path("docs/implementation/evidence/TF-I08E/data-quality.json")
    proven = {(row["source"]["symbol"], row["source"]["day"]): row["source"]
              for row in json.loads(evidence.read_text(encoding="utf-8"))
              if row["source"]["price_basis"] == "BID"}
    panes = {view: {"symbol": symbol, "timeframe": frame, "candles": [], "partitions": []}
             for view, symbol, frame in (
                 ("primary", "EURUSD", "M5"), ("2", "GBPUSD", "M1"),
                 ("3", "GBPUSD", "M5"), ("4", "EURUSD", "M1"),
             )}
    sources = []
    for symbol in ("EURUSD", "GBPUSD"):
        files = sorted(directory.glob(f"{symbol}_*_M1_BID.csv"))
        assert len(files) == 8, (symbol, len(files))
        for file in files:
            day = file.name.split("_")[1]
            with file.open(encoding="utf-8-sig", newline="") as handle:
                minute = {}
                groups = defaultdict(list)
                for row in csv.DictReader(handle):
                    stamp = int(datetime.fromisoformat(row["Etc/UTC"]).timestamp())
                    bar = {"time": stamp, "available_at": stamp + 60,
                           **{key: float(row[key.title()]) for key in FIELDS}}
                    assert stamp not in minute or minute[stamp] == bar
                    minute[stamp] = bar
                for stamp in sorted(minute):
                    groups[stamp - stamp % 300].append(minute[stamp])
            five = []
            for stamp, children in sorted(groups.items()):
                if len(children) == 5:
                    five.append({
                        "time": stamp, "available_at": stamp + 300,
                        "open": children[0]["open"], "high": max(bar["high"] for bar in children),
                        "low": min(bar["low"] for bar in children), "close": children[-1]["close"],
                    })
            digest = sha256(file.read_bytes()).hexdigest()
            source = proven[symbol, day]
            assert source["sha256"] == digest
            sources.append({"symbol": symbol, "day": day, "sha256": digest,
                            "m1": len(minute), "complete_m5": len(five)})
            imports = {}
            if storage is not None:
                proc = subprocess.run(
                    [sys.executable, "-m", "scripts.import_dukascopy_csv", symbol, day,
                     str(file.resolve()), "--original-name", source["original_name"]],
                    env=storage_env(storage),
                    check=True, capture_output=True, text=True,
                )
                imported = json.loads(proc.stdout)
                assert imported["source"]["sha256"] == digest
                imports = {part["timeframe"]: part for part in imported["candle_datasets"]}
            for pane in panes.values():
                if pane["symbol"] != symbol:
                    continue
                frame = pane["timeframe"]
                bars = [minute[stamp] for stamp in sorted(minute)] if frame == "M1" else five
                pane["candles"].extend(bars)
                if storage is not None:
                    assert imports[frame]["rows"] == len(bars)
                    pane["partitions"].append({
                        **{key: imports[frame][key] for key in ("id", "sha256")},
                        "left": bars[0]["time"], "right": bars[-1]["time"],
                    })
    return panes, sources


def genuine_range(panes):
    """Find the most balanced aligned window that fits all four 5,000-bar bounds."""
    stamps = [[bar["time"] for bar in pane["candles"]] for pane in panes.values()]
    best = None
    for primary_candle in panes["primary"]["candles"]:
        end = primary_candle["available_at"]
        right = [bisect_left(lane, end) for lane in stamps]
        excluded_times = [lane[limit - SIZE - 1]
                          for lane, limit in zip(stamps, right, strict=True) if limit > SIZE]
        start = (max(excluded_times) // 300 + 1) * 300 if excluded_times else (
            min(lane[0] for lane in stamps) // 300 * 300
        )
        counts = [limit - bisect_left(lane, start)
                  for lane, limit in zip(stamps, right, strict=True)]
        candidate = (min(counts), sum(counts), end, start)
        if best is None or candidate > best:
            best = candidate
    assert best is not None
    _, _, end, start = best
    totals = {view: len(pane["candles"]) for view, pane in panes.items()}
    for pane in panes.values():
        pane["candles"] = [bar for bar in pane["candles"] if start <= bar["time"] < end]
        included = [pin for pin in pane["partitions"] if pin["left"] < end and pin["right"] >= start]
        if included:
            bind_pins(pane, [(pin["left"], pin["right"] + 1) for pin in included])
        pane["partitions"] = [{key: pin[key] for key in ("id", "sha256")} for pin in included]
    return start, end, totals


def verify_genuine_csv(client, directory, storage):
    panes, sources = genuine_csv(directory, storage)
    start, end, caps = genuine_range(panes)
    payload = {"symbol": "EURUSD", "timeframe": "M5", "source": SOURCE,
               "start": start, "end": end, "window_bars": SIZE,
               "views": [{"view": view, "symbol": pane["symbol"], "timeframe": pane["timeframe"]}
                         for view, pane in panes.items() if view != "primary"]}
    checkpoint = {"position": len(panes["primary"]["candles"]) - 1, "bookmarks": [0],
                  "selected": {"view": "3", "time": panes["3"]["candles"][-2]["time"]}}
    return {**save(client, payload, panes, sources, checkpoint),
            "source_kind": "GENUINE_DUKASCOPY_WEB_CSV", "source_inventory": caps}


def reopen(client, receipt):
    session = request(client, "GET", f"/api/replay/sessions/{receipt['session_id']}")
    assert session["id"] == receipt["session_id"] and session["checkpoint"] == receipt["checkpoint"]
    assert session["request"]["window_bars"] == receipt["request"]["window_bars"]
    verify_snapshot(session, receipt["panes"])
    query = {key: receipt["request"][key] for key in ("source", "symbol", "timeframe")}
    sessions = request(client, "GET", "/api/replay/sessions", params=query)
    assert any(row["id"] == session["id"] and row["position"] == receipt["checkpoint"]["position"]
               for row in sessions)
    return {"session_id": session["id"], "checkpoint": session["checkpoint"],
            "bars": {view: len(pane["candles"]) for view, pane in receipt["panes"].items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("baseline", "prepare", "genuine-bi5", "genuine-csv",
                                         "inventory", "delayed", "reopen"))
    parser.add_argument("--api", default="http://127.0.0.1:8793")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, help="Previously generated fixture, for reopen")
    parser.add_argument("--bi5", type=Path, default=Path("storage/t06-input"))
    parser.add_argument("--csv", type=Path, default=Path("storage/tf-i08e-source"))
    parser.add_argument("--storage", type=Path, help="Isolated API storage, for genuine-csv and delayed")
    args = parser.parse_args()
    if args.mode in {"genuine-csv", "delayed"} and not args.storage:
        parser.error(f"{args.mode} requires --storage matching the isolated API instance")
    if args.mode == "inventory":
        panes, sources = genuine_csv(args.csv)
        start, end, totals = genuine_range(panes)
        write_receipt(args.output, {"source_maximum": totals, "start": start, "end": end,
                                   "common_range": {key: len(value["candles"])
                                                    for key, value in panes.items()},
                                   "sources": sources})
        return
    with httpx.Client(base_url=args.api, timeout=60, trust_env=False) as client:
        assert request(client, "GET", "/api/health")["execution_enabled"] is False
        match args.mode:
            case "baseline":
                receipt = baseline(client)
            case "prepare":
                receipt = prepare(client)
            case "genuine-bi5":
                receipt = genuine_bi5(client, args.bi5)
            case "genuine-csv":
                receipt = verify_genuine_csv(client, args.csv, args.storage)
            case "delayed":
                receipt = delayed(client, args.storage)
            case "reopen":
                if not args.fixture:
                    parser.error("reopen requires --fixture")
                receipt = reopen(client, json.loads(args.fixture.read_text(encoding="utf-8")))
    write_receipt(args.output, receipt)


def write_receipt(output, receipt):
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, separators=(",", ":")) + "\n", encoding="utf-8")
    print(output.resolve())


if __name__ == "__main__":
    main()
