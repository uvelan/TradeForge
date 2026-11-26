"""Compare public confluence coverage, saved receipts and inspection cost to the PR writer."""

import argparse
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory

from scripts.benchmark_t10_confluence import count_direct_verification, measure_sample
from scripts.benchmark_t10_structure_liquidity import install_counters, summary
from scripts.benchmark_t10_zones import CountedClient
from scripts.verify_t10_confluence import DAY, PROFILE, genuine, read_confluence, save_confluence
from scripts.verify_t10_crt import bar, replay, seed_controls, successful
from scripts.verify_t10_structure_liquidity import PRICE_FIELDS, STEP, utc

BASELINE = "7cdacaf4c10bdd1073128c0a4d10b2e34be7c7f6"
LIMITS = ("sql_statements", "sql_selects", "parquet_opens", "decoded_rows", "verified_archive_bytes")
FIRST = DAY + 14 * 3600 + 5 * 60
NOMINAL = FIRST + STEP
DELIVERY = NOMINAL + 17
ALIGNED = DAY + 13 * 3600 + 25 * 60
GENUINE = (ALIGNED, ALIGNED + STEP, NOMINAL, DELIVERY)
CONTROL = (FIRST, NOMINAL - 1, NOMINAL, DELIVERY - 1, DELIVERY, DELIVERY + 1)
SUMMARY_KEYS = ("session_id", "cutoff", "profile", "rule_version", "receipt_id", "status", "reason", "direction",
                "first_available_at")
COMPONENT_KEYS = ("family_id", "revision_id", "state", "available_at", "coverage_issues")


def controlled(delayed):
    """A confirmed bearish SMT and a reclaimed CRT high precede a quiet 14:05 child."""
    start = DAY + 13 * 3600
    left = [10, 11, 14, 11, 10, 11, 16, 11, 10, 11, 11, 11]
    right = [22, 24, 29, 24, 22, 24, 27, 24, 22, 24, 24, 24]
    start_sweep = DAY + 14 * 3600
    eur = [bar(start + i * STEP, (p, p + 1, p - 1, p)) for i, p in enumerate(left)]
    gbp = [bar(start + i * STEP, (p, p + 1, p - 1, p)) for i, p in enumerate(right)]
    eur.append(bar(start_sweep, (16, 18, 15, 16)))
    gbp.append(bar(start_sweep, (24, 25, 23, 24)))
    for symbol, rows, prices in (("EURUSD", eur, (16, 16.5, 15.5, 16)), ("GBPUSD", gbp, (24, 25, 23, 24))):
        rows.extend(bar(stamp, prices, 17 if stamp == FIRST and symbol in delayed else 0)
                    for stamp in range(FIRST, FIRST + 4 * STEP, STEP))
    return {"start": start, "end": FIRST + 4 * STEP, "feed": "controlled-t10-quiet-recovery",
            "symbols": {"EURUSD": {"bars": eur}, "GBPUSD": {"bars": gbp}}}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def select_receipt(receipt):
    result = {key: receipt[key] for key in SUMMARY_KEYS}
    result["components"] = {name: {key: part[key] for key in COMPONENT_KEYS}
                            for name, part in receipt["components"].items()}
    if "coverage_recoveries" in receipt:
        result["coverage_recoveries"] = receipt["coverage_recoveries"]
    return result


def saved_bytes(session, receipt_id):
    from research_backend.app.config import DATABASE

    with closing(sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True)) as connection:
        row = connection.execute("SELECT CAST(payload AS BLOB) FROM replay_confluence_receipts "
                                 "WHERE session_id=? AND id=?", (session, receipt_id)).fetchone()
    assert row is not None, receipt_id
    return row[0]


def assert_payload(session, receipt, encoded):
    rid = receipt["receipt_id"]
    assert encoded == saved_bytes(session, rid)
    assert sha256(encoded).hexdigest() == rid
    assert canonical({key: value for key, value in receipt.items() if key != "receipt_id"}) == encoded


def list_receipts(client, session, cutoff, **options):
    return client.get(f"/api/replay/sessions/{session}/confluence/receipts",
                      params={"cutoff": cutoff, "profile": PROFILE, **options})


def expected_witnesses(manifest, delayed):
    witnesses = []
    for symbol in sorted(delayed):
        origin = manifest["symbols"][symbol]["origins"][FIRST]
        witnesses.append({"symbol": symbol, "time": FIRST, "nominal_close": NOMINAL, "available_at": DELIVERY,
                          "availability_basis": "ARCHIVE_SUPPLIED", "source_id": f"{origin['id']}:{FIRST}",
                          "partitions": [origin]})
    return witnesses


def verify_control(results, witnesses, rule_version):
    affected = [row["symbol"] for row in witnesses]
    gap = [{"symbol": symbol, "reason": "MISSING_OPEN_CANDLES", "missing_intervals": [{"start": FIRST, "end": NOMINAL}]}
           for symbol in affected]
    anchor = results[FIRST]
    assert anchor["status"] == "ALIGNED" and anchor["direction"] == "BEARISH", select_receipt(anchor)
    for cutoff, receipt in results.items():
        blocked = NOMINAL <= cutoff < DELIVERY
        assert receipt["status"] == ("INCOMPLETE" if blocked else "ALIGNED"), select_receipt(receipt)
        if blocked:
            assert receipt["reason"] == "MISSING_OPEN_CANDLES"
        assert receipt["first_available_at"] == (None if blocked else FIRST if cutoff < NOMINAL else DELIVERY)
        for side, part in receipt["components"].items():
            component_gaps = [item for item in gap if side == "smt" or item["symbol"] == "EURUSD"]
            assert part["coverage_issues"] == (component_gaps if blocked else [])
            for name in ("family_id", "revision_id", "available_at"):
                assert part[name] == anchor["components"][side][name], (utc(cutoff), side, name)
            assert all(row["available_at"] <= cutoff for row in part["evidence"])
        assert receipt["rule_version"] == rule_version
        if rule_version == 1:
            assert "coverage_recoveries" not in receipt
            assert all((row["symbol"], row["time"]) != ("EURUSD", FIRST)
                       for part in receipt["components"].values() for row in part["evidence"]), select_receipt(receipt)
        else:
            assert receipt["coverage_recoveries"] == (witnesses if cutoff >= DELIVERY else []), select_receipt(receipt)


def verify_genuine(results, reference, rule_version):
    oracle = {checkpoint["cutoff"]: checkpoint for checkpoint in reference}
    for cutoff, result in results.items():
        # 14:10:17 falls between nominal arrivals and uses the same prefix as 14:10:00.
        expected = oracle[cutoff if cutoff != DELIVERY else NOMINAL]
        assert (result["status"], result["reason"], result["direction"]) == (
            expected["verdict"], expected["reason"], expected["direction"]), select_receipt(result)
        assert result["rule_version"] == rule_version
        if rule_version == 2:
            assert result["coverage_recoveries"] == [], result["coverage_recoveries"]
    assert results[ALIGNED]["status"] == "ALIGNED"


def writer(client, case, source):
    if case == "genuine":
        manifest = genuine(client, source)
        clocks, cutoffs, witnesses = GENUINE, (ALIGNED, NOMINAL), []
        assert all(row["available_at"] == row["time"] + STEP
                   for lane in manifest["symbols"].values() for row in lane["bars"])
    else:
        delayed = ("EURUSD", "GBPUSD") if case == "both" else ("EURUSD",)
        manifest = seed_controls(controlled(delayed))
        clocks, cutoffs, witnesses = CONTROL, (FIRST, DELIVERY), expected_witnesses(manifest, delayed)
    session = replay(client, manifest)
    route = f"/api/replay/sessions/{session}"
    position = max(i for i, row in enumerate(manifest["symbols"]["EURUSD"]["bars"])
                   if row["available_at"] <= cutoffs[-1])
    assert successful(client.post(f"{route}/checkpoint", json={"position": position}))["position"] == position
    results = {cutoff: successful(read_confluence(client, session, cutoff)) for cutoff in clocks}
    if case == "genuine":
        verify_genuine(results, manifest["checkpoints"], 1)
    else:
        verify_control(results, witnesses, 1)
    saved = []
    for cutoff in cutoffs:
        receipt = results[cutoff]
        rid = receipt["receipt_id"]
        assert successful(save_confluence(client, session, cutoff, rid)) == receipt
        encoded = saved_bytes(session, rid)
        assert_payload(session, receipt, encoded)
        saved.append({"summary": select_receipt(receipt), "original_payload_utf8": encoded.decode()})
    missing_route = list_receipts(client, session, cutoffs[-1]).status_code
    assert missing_route == 404, missing_route
    return {"session": session, "position": position, "saved": saved, "witnesses": witnesses,
            "provider_pins": {symbol: lane["partitions"] for symbol, lane in manifest["symbols"].items()},
            "samples": {str(clock): select_receipt(result) for clock, result in results.items()}}


def verify_saved_payloads(client, plan):
    session = plan["session"]
    state = successful(client.get(f"/api/replay/sessions/{session}"))
    assert state["checkpoint"]["position"] == plan["position"]
    for item in plan["saved"]:
        expected = item["summary"]
        receipt = successful(read_confluence(client, session, expected["cutoff"], expected["receipt_id"]))
        assert select_receipt(receipt) == expected
        assert_payload(session, receipt, item["original_payload_utf8"].encode())


def verify_reopens(client, plan, candidate):
    verify_saved_payloads(client, plan)
    session = plan["session"]
    if candidate:
        earlier, latest = [item["summary"] for item in plan["saved"]]
        expected_summaries = [{key: item[key] for key in SUMMARY_KEYS} for item in (latest, earlier)]
        assert successful(list_receipts(client, session, latest["cutoff"])) == {
            "receipts": expected_summaries, "next_cursor": None}
        assert successful(list_receipts(client, session, earlier["cutoff"])) == {
            "receipts": expected_summaries[1:], "next_cursor": None}
        page = successful(list_receipts(client, session, latest["cutoff"], limit=1))
        assert page["receipts"] == expected_summaries[:1]
        assert page["next_cursor"] == {"cutoff": latest["cutoff"], "receipt_id": latest["receipt_id"]}
        following = successful(list_receipts(client, session, latest["cutoff"], limit=1,
                                             before_cutoff=latest["cutoff"], before_id=latest["receipt_id"]))
        assert following == {"receipts": expected_summaries[1:], "next_cursor": None}
        return page
    assert list_receipts(client, session, plan["saved"][-1]["summary"]["cutoff"]).status_code == 404
    return None


def candidate_probe(client, case, plan):
    first_page = verify_reopens(client, plan, True)
    old = plan["samples"]
    results = {int(clock): successful(read_confluence(client, plan["session"], int(clock))) for clock in old}
    if case != "genuine":
        verify_control(results, plan["witnesses"], 2)
    for cutoff, receipt in results.items():
        actual = select_receipt(receipt)
        assert actual["rule_version"] == 2
        if case == "genuine":
            assert actual["coverage_recoveries"] == []
        assert actual["receipt_id"] != old[str(cutoff)]["receipt_id"]
        changed_fields = {key for key in actual if actual[key] != old[str(cutoff)].get(key)}
        assert changed_fields == {"receipt_id", "rule_version", "coverage_recoveries"}, (utc(cutoff), changed_fields)
    if case == "genuine":
        return {"first_page": first_page, "samples": {str(k): select_receipt(v) for k, v in results.items()}}
    session = plan["session"]
    receipt = results[DELIVERY]
    rid = receipt["receipt_id"]
    assert successful(save_confluence(client, session, DELIVERY, rid)) == receipt
    encoded = saved_bytes(session, rid)
    assert_payload(session, receipt, encoded)
    return {"first_page": first_page, "samples": {str(k): select_receipt(v) for k, v in results.items()},
            "new_saved": {"summary": select_receipt(receipt), "original_payload_utf8": encoded.decode()}}


def same_clock_pages(client, plan):
    verify_saved_payloads(client, plan)
    session = plan["session"]
    current = plan["new_saved"]
    new = current["summary"]
    cutoff, rid = new["cutoff"], new["receipt_id"]
    result = successful(read_confluence(client, session, cutoff, rid))
    assert select_receipt(result) == new
    assert_payload(session, result, current["original_payload_utf8"].encode())
    ordered = sorted([items["summary"] for items in [*plan["saved"], current]],
                     key=lambda item: (item["cutoff"], item["receipt_id"]), reverse=True)
    options = {"limit": 1}
    for i, item in enumerate(ordered):
        page = successful(list_receipts(client, session, cutoff, **options))
        assert page["receipts"] == [{key: item[key] for key in SUMMARY_KEYS}], page
        expected_cursor = {"cutoff": item["cutoff"], "receipt_id": item["receipt_id"]}
        assert page["next_cursor"] == (expected_cursor if i < len(ordered)-1 else None), page
        options = {"limit": 1, "before_cutoff": item["cutoff"], "before_id": item["receipt_id"]}
    return [item["receipt_id"] for item in ordered]


def benchmark(raw, plan, action, count):
    counters = install_counters()
    count_direct_verification(counters)
    client = CountedClient(raw, counters)
    session = plan["session"]
    saved = plan["saved"][0]["summary"]
    cutoff, rid = saved["cutoff"], saved["receipt_id"]
    route = f"/api/replay/sessions/{session}"

    def perform(mode):
        if mode == "receipt_list":
            result = list_receipts(client, session, plan["saved"][-1]["summary"]["cutoff"])
            assert result.status_code == 404 or len(successful(result)["receipts"]) == 2
        elif mode == "session_list":
            result = client.get("/api/replay/sessions", params={"symbol": "EURUSD", "timeframe": "M5",
                                                               "source": "DUKASCOPY_SAVED"})
            assert successful(result)[0]["id"] == session
        else:
            if mode == "session_and_reopen":
                assert successful(client.get(route))["checkpoint"]["position"] == plan["position"]
            result = read_confluence(client, session, cutoff, rid)
            assert successful(result)["receipt_id"] == rid
        return result.status_code

    def series(mode):
        return {"status_code": perform(mode), "samples": [measure_sample(lambda: perform(mode), counters)
                                                           for _ in range(count)]}

    if action == "bench_warm":
        return {mode: series(mode) for mode in ("receipt_list", "session_list", "reopen", "session_and_reopen")}
    # The first request in a fresh process includes lazy route imports and excludes startup.
    action = action.removeprefix("bench_cold_")
    status = []
    timing = measure_sample(lambda: status.append(perform(action)), counters)
    return {"status_code": status[0], "samples": [timing]}


def run_worker(args):
    from fastapi.testclient import TestClient
    from research_backend.app.main import app

    plan = json.loads(args.plan.read_text(encoding="utf-8")) if args.plan else None
    with TestClient(app) as client:
        if args.worker == "writer":
            return writer(client, args.case, args.source_storage)
        if args.worker == "baseline_reopen":
            return verify_reopens(client, plan, False)
        if args.worker == "candidate_probe":
            return candidate_probe(client, args.case, plan)
        if args.worker == "candidate_restart":
            return same_clock_pages(client, plan)
        return benchmark(client, plan, args.worker, args.warm_samples)


def revision(repo):
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def write(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def public_evidence(plan):
    """The private phase plan keeps exact bytes. The committed summary only needs their hash and length."""
    result = dict(plan)
    for item in [*result["saved"], *([result["new_saved"]] if "new_saved" in result else [])]:
        payload = item.pop("original_payload_utf8").encode()
        item.update(payload_bytes=len(payload), payload_sha256=sha256(payload).hexdigest())
    return result


def summarize_bench(data):
    statuses = {sampling["status_code"] for sampling in data}
    assert len(statuses) == 1
    results = summary([sample for sampling in data for sample in sampling["samples"]])
    counts = results["counts_per_sample_min_max"]
    return {"status_code": statuses.pop(), "samples": results["samples"], "median_ms": results["p50_ms"],
            "p95_ms": results["p95_ms"], "per_request_min_max": {metric: counts.get(metric, [0, 0]) for metric in LIMITS}}


def compare(args):
    baseline = args.baseline.resolve()
    assert revision(baseline) == BASELINE, "The saved records must come from the original writer"
    candidates = [("baseline", baseline)]
    if args.candidate:
        candidate = args.candidate.resolve()
        subprocess.run(["git", "merge-base", "--is-ancestor", BASELINE, "HEAD"], cwd=candidate, check=True)
        assert revision(candidate) != BASELINE
        candidates.append(("candidate", candidate))
    payload = {"revisions": {label: revision(repo) for label, repo in candidates},
               "method": "Fresh FastAPI TestClient process per phase, public HTTP routes on the same SQLite and pinned Parquet files. "
                         "Trial timings exclude startup; OS file cache was uncontrolled. HTTP 404 is baseline receipt-list availability, "
                         "with no baseline API listing latency. SQL uses connection trace callbacks. Archive opens, rows and hashes "
                         "count application work even if the host returns cached bytes.",
               "clock_utc": {"controlled_open": utc(FIRST), "nominal_close": utc(NOMINAL), "arrival": utc(DELIVERY)},
               "cases": {}, "benchmarks": {}}
    script = Path(__file__).resolve()
    with TemporaryDirectory(prefix="tradeforge-confluence-review-", dir=args.output.resolve().parent) as tmp:
        scratch = Path(tmp)

        def run(repo, operation, case, plan=None):
            work = scratch / case
            env = {**os.environ, "PYTHONPATH": str(repo), "TRADEFORGE_STORAGE": str(work),
                   "TRADEFORGE_DATA_DIR": str(work / "research")}
            command = [sys.executable, str(script), "--worker", operation, "--case", case,
                       "--source-storage", str(args.source_storage.resolve()), "--warm-samples", str(args.warm_samples)]
            if plan is not None:
                plan_path = work / "plan.json"
                write(plan_path, plan)
                command += ["--plan", str(plan_path)]
            return json.loads(subprocess.check_output(command, cwd=repo, env=env, text=True, encoding="utf-8"))

        for case in ("genuine", "quiet-eurusd", "both"):
            print(f"Checking {case} public API and saved receipt bytes", flush=True)
            plan = run(baseline, "writer", case)
            run(baseline, "baseline_reopen", case, plan)
            if args.candidate:
                fresh = run(candidate, "candidate_probe", case, plan)
                plan.update(candidate=fresh)
                if "new_saved" in fresh:
                    plan["new_saved"] = fresh.pop("new_saved")
                    plan["candidate_restart_order"] = run(candidate, "candidate_restart", case, plan)
            if case == "genuine":
                bench_plan = json.loads(json.dumps(plan))
            payload["cases"][case] = public_evidence(plan)
        if args.cold_samples:
            print("Measuring public list and pinned reopen on the genuine session", flush=True)
            cold_modes = ("receipt_list", "reopen", "session_and_reopen")
            cold = {name: {mode: [] for mode in cold_modes} for name, _ in candidates}
            for _ in range(args.cold_samples):
                for label, repo in candidates:
                    for mode in cold_modes:
                        cold[label][mode].append(run(repo, f"bench_cold_{mode}", "genuine", bench_plan))
            for label, repo in candidates:
                warm = run(repo, "bench_warm", "genuine", bench_plan)
                payload["benchmarks"][label] = {"cold": {mode: summarize_bench(data) for mode, data in cold[label].items()},
                                                 "warm": {mode: summarize_bench([data]) for mode, data in warm.items()}}
    write(args.output, payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--source-storage", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--warm-samples", type=int, default=30)
    parser.add_argument("--cold-samples", type=int, default=4)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--case", help=argparse.SUPPRESS)
    parser.add_argument("--plan", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(run_worker(args)))
    elif not args.baseline or not args.output or args.warm_samples < 2 or (args.cold_samples != 0 and args.cold_samples < 2):
        parser.error("Provide baseline and output paths, at least 2 warm samples, and 0 or at least 2 cold samples")
    else:
        compare(args)


if __name__ == "__main__":
    main()
