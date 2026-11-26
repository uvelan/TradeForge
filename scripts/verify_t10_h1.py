"""Compare the H1 replay API with exact arithmetic on pinned local provider candles."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory, gettempdir

from scripts.t10_h1_reference import BOUNDARIES, HOUR, build_reference
from scripts.t10_numerical_acceptance import (
    END, PRICE_FIELDS, ROOT, START, STEP, checked_json, load_chart, read_source, utc,
)

SESSION_ENDPOINT = "/api/replay/sessions"
SOURCE = "DUKASCOPY_SAVED"
CASES = ("genuine", "missing", "delayed", "suffix")


def create_session(client, symbol, end=END):
    response = client.post(SESSION_ENDPOINT, json={
        "symbol": symbol, "timeframe": "M5", "source": SOURCE,
        "start": START, "end": end,
    })
    assert response.status_code == 201, (response.status_code, response.text)
    return response.json()["id"]


def hour_at(client, session, expected, cutoff):
    payload = checked_json(client.get(f"{SESSION_ENDPOINT}/{session}/higher-timeframe", params={
        "view": "primary", "cutoff": cutoff, "hour_start": expected["start"],
    }))
    fields = {
        "session_id": session, "view": "primary", "timeframe": "H1", "source_timeframe": "M5",
        "start": expected["start"], "end": expected["end"], "cutoff": cutoff,
    }
    assert {field: payload[field] for field in fields} == fields, payload
    return payload


def verify_hour(payload, expected, status, children, missing=()):
    assert payload["status"] == status, payload
    if status == "UNRESOLVED":
        assert payload["reason"] == "MISSING_OPEN_CANDLES", payload
    assert payload["ohlc"] == (expected["ohlc"] if status == "COMPLETE" else None), payload
    assert payload["available_at"] == (expected["available_at"] if status == "COMPLETE" else None), payload
    assert payload["missing_slots"] == list(missing), payload
    assert len(payload["children"]) == len(children), payload
    for observed, source in zip(payload["children"], children):
        assert observed["time"] == source["open_time"], observed
        assert observed["available_at"] == source["available_at"], observed
        assert {field: observed[field] for field in PRICE_FIELDS} == source["ohlc"], observed
        assert observed["partitions"] == [source["partition"]], observed
        assert observed["source_id"] == f"{source['partition']['id']}:{source['open_time']}", observed
    assert len({row["source_id"] for row in payload["children"]}) == len(children), payload


def observation(payload, expected):
    return {
        "hour": [utc(expected["start"]), utc(expected["end"])],
        "cutoff": utc(payload["cutoff"]),
        "status": payload["status"],
        "reason": payload["reason"],
        "available_at": utc(payload["available_at"]) if payload["available_at"] is not None else None,
        "ohlc": payload["ohlc"],
        "child_source_ids": [row["source_id"] for row in payload["children"]],
        "missing_slots": [utc(slot) for slot in payload["missing_slots"]],
    }


def stable_hour(left, right):
    return all(left[key] == right[key] for key in ("status", "available_at", "ohlc", "children"))


def genuine(client, storage, reference):
    results = {}
    for symbol, lane in reference["symbols"].items():
        source, _ = read_source(storage, symbol)
        load_chart(client, storage, symbol, source)
        short_session = create_session(client, symbol, START + HOUR)
        full_session = create_session(client, symbol)
        first, second = lane["hours"]
        timeline = []
        for expected, cutoff, status in [
            (first, BOUNDARIES[0], "FORMING"),
            (first, BOUNDARIES[1], "COMPLETE"),
            (second, BOUNDARIES[2], "FORMING"),
            (second, BOUNDARIES[3], "COMPLETE"),
        ]:
            payload = hour_at(client, full_session, expected, cutoff)
            verify_hour(payload, expected, status, expected["children"] if status == "COMPLETE" else [])
            assert (payload["symbol"], payload["feed_id"], payload["price_basis"]) == (
                symbol, lane["market"]["feed_id"], lane["market"]["price_basis"],
            ), payload
            timeline.append(observation(payload, expected))
        old = hour_at(client, short_session, first, BOUNDARIES[1])
        extended = hour_at(client, full_session, first, BOUNDARIES[3])
        rewind = hour_at(client, full_session, first, BOUNDARIES[1])
        verify_hour(old, first, "COMPLETE", first["children"])
        assert stable_hour(old, extended) and stable_hour(old, rewind), (old, extended, rewind)
        results[symbol] = {
            "timeline": timeline,
            "earlier_hour_identical_after_later_candles_and_rewind": True,
            "source": lane["market"],
            "provider_csv_sha256": lane["provider_csv_sha256"],
        }
    return results


def publish_control(symbol, children):
    from research_backend.app.storage import write_partition

    bars = [{"time": bar["open_time"], "available_at": bar["available_at"], **bar["ohlc"]} for bar in children]
    saved = write_partition("DUKASCOPY", "t10-h1-controlled-v1", symbol, "candles", "M5", "BID", bars)
    pin = {field: saved[field] for field in ("id", "sha256")}
    for child in children:
        child["partition"] = pin
    return pin


def controlled(client, reference, case):
    symbol = "EURUSD" if case != "delayed" else "GBPUSD"
    first, second = reference["symbols"][symbol]["hours"]
    first_children, second_children = first["children"], second["children"]
    changed = None
    if case == "missing":
        changed = first_children.pop(5)["open_time"]
    if case == "delayed":
        changed = first_children[-1]["open_time"]
        first_children[-1]["available_at"] += 20
        first["available_at"] = first_children[-1]["available_at"]
    first_pin = publish_control(symbol, first_children)
    short_session = create_session(client, symbol, START + HOUR)
    second_pin = publish_control(symbol, second_children)
    full_session = create_session(client, symbol)
    timeline = []
    if case == "suffix":
        old = hour_at(client, short_session, first, BOUNDARIES[1])
        appended = hour_at(client, full_session, first, END)
        rewind = hour_at(client, full_session, first, BOUNDARIES[1])
        for payload in (old, appended, rewind):
            verify_hour(payload, first, "COMPLETE", first_children)
            assert stable_hour(old, payload)
            timeline.append(observation(payload, first))
    else:
        cutoffs = BOUNDARIES[:2] + ((first["available_at"] - 1, first["available_at"]) if case == "delayed" else (END,))
        for cutoff in cutoffs:
            visible = [child for child in first_children if child["available_at"] <= cutoff]
            status = "FORMING" if cutoff < first["end"] else (
                "COMPLETE" if len(visible) == 12 else "UNRESOLVED"
            )
            missing = [source_time for source_time in range(first["start"], first["end"], STEP)
                       if source_time not in {child["open_time"] for child in visible}]
            payload = hour_at(client, full_session, first, cutoff)
            verify_hour(payload, first, status, visible if status == "COMPLETE" else [],
                        missing if status == "UNRESOLVED" else [])
            timeline.append(observation(payload, first))
        later = hour_at(client, full_session, second, END)
        verify_hour(later, second, "COMPLETE", second_children)
        timeline.append(observation(later, second))
    return {
        "controlled": True,
        "fixture": case,
        "symbol": symbol,
        "changed_child_open": utc(changed) if changed is not None else None,
        "partitions": [first_pin, second_pin],
        "timeline": timeline,
    }


def run_case(storage, case):
    from fastapi.testclient import TestClient
    from research_backend.app.main import app

    reference = build_reference(storage)
    with TestClient(app) as client:
        if case == "genuine":
            return genuine(client, storage, reference)
        return controlled(client, reference, case)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage", type=Path, default=ROOT / "storage")
    parser.add_argument("--case", choices=CASES, help=argparse.SUPPRESS)
    options = parser.parse_args()
    storage = options.source_storage.resolve()
    if options.case:
        private = Path(os.environ["TRADEFORGE_STORAGE"]).resolve()
        data = Path(os.environ["TRADEFORGE_DATA_DIR"]).resolve()
        assert private.parent == Path(gettempdir()).resolve() and private.name.startswith("tradeforge-t10-h1-")
        assert private != storage and data == private / "research" and private.is_dir(), private
        print(json.dumps(run_case(storage, options.case)))
        return
    results = {}
    for case in CASES:
        with TemporaryDirectory(prefix="tradeforge-t10-h1-") as private:
            env = {**os.environ, "TRADEFORGE_STORAGE": private, "TRADEFORGE_DATA_DIR": str(Path(private) / "research")}
            run = subprocess.run([sys.executable, "-m", "scripts.verify_t10_h1", "--source-storage", str(storage),
                                  "--case", case], capture_output=True, text=True, env=env)
            if run.returncode:
                sys.stderr.write(run.stderr)
                sys.stderr.write(run.stdout)
                raise SystemExit(run.returncode)
            results[case] = json.loads(run.stdout)
    print(json.dumps({"status": "PASS", "results": results}, indent=2))


if __name__ == "__main__":
    main()
