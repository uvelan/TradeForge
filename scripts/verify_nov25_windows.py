"""Compare a Windows TradeForge worktree to an independent November source oracle.

Run from a fresh process. Only isolated SQLite/market copies are written. Timed
workloads use in-process HTTP so browser, acquisition and import cost are outside
the measurement.
"""

import argparse
from collections import Counter
from datetime import UTC, datetime
from functools import lru_cache, wraps
from hashlib import sha256
from itertools import groupby, zip_longest
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

from nov25_source_oracle import (
    BI5Source,
    END,
    GAP_MS,
    MT5Source,
    START,
    Quote,
    decode_bi5,
    decode_mt5_day,
    expected,
    open_hour,
    verify_control,
)


MONTH_PATH = "/api/month-replay/2025-11"


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def replace_import_aliases(original, replacement):
    for name, module in tuple(sys.modules.items()):
        if module is None or not name.startswith("research_backend.app."):
            continue
        for attribute, value in tuple(vars(module).items()):
            if value is original:
                setattr(module, attribute, replacement)


def instrument_reads(storage, counters):
    original_decode = storage.decode_bi5
    original_partition = storage.iter_complete_partition

    @wraps(original_decode)
    def decode(payload, *args, **kwargs):
        counters["bi5_decodes"] += 1
        counters["bi5_logical_bytes"] += len(payload)
        return original_decode(payload, *args, **kwargs)

    @wraps(original_partition)
    def partition(dataset, *args, **kwargs):
        if dataset["kind"] == "ticks":
            counters["parquet_verifications"] += 1
            counters["parquet_logical_bytes"] += (storage.MARKET_DIR / dataset["relative_path"]).stat().st_size
        return original_partition(dataset, *args, **kwargs)

    replace_import_aliases(original_decode, decode)
    replace_import_aliases(original_partition, partition)


def instrument_quotes(module, files, state):
    original = module.run_batch

    @lru_cache(maxsize=1)
    def source_slots(hour):
        by_slot = {}
        for row in decode_bi5(Path(files[hour]), hour):
            slot = row.time_ms // 600_000 * 600
            by_slot.setdefault(slot, []).append(row)
        return by_slot

    @wraps(original)
    def run_batch(checkpoint, hour, quotes, session):
        start = hour["start"]
        source_hour = start // 3600 * 3600
        rows = source_slots(source_hour).get(start, []) if source_hour in files else []
        observed = [
            Quote(q["time_ms"], q["source_order"], q["bid_units"], q["ask_units"], q["source_sha256"])
            for q in quotes
        ]
        state["verified_quote_rows"] += len(observed)
        if rows != observed and state["first_quote_divergence"] is None:
            for index, (want, got) in enumerate(zip_longest(rows, observed)):
                if want != got:
                    state["first_quote_divergence"] = dict(
                        stage="ordered_quotes", at_ms=(want or got).time_ms, slot=start, index=index,
                        expected=want.proof() if want else None, actual=got.proof() if got else None,
                    )
                    break
        return original(checkpoint, hour, quotes, session)

    module.run_batch = run_batch


def response_json(response):
    if not response.is_success:
        raise RuntimeError((response.status_code, response.text))
    return response.json()


def production_events(client, url, events, binding, verify_sources):
    normalized = []
    seen = set()
    decision = None
    for ev in events:
        assert ev["id"] not in seen, ev
        seen.add(ev["id"])
        record = {}
        for key, value in ev.items():
            if key in ("quote", "anchor"):
                stamp = value["time_ms"] // 3_600_000 * 3600
                pin = binding["hours"][(stamp - binding["start"]) // 3600]["pin"]
                assert pin and value["partition"] == pin["partition"], ev
                if verify_sources:
                    resolved = response_json(client.get(url + "/quote", params={
                        "time_ms": value["time_ms"], "ordinal": value["ordinal"],
                    }))
                    assert resolved == value, (value, resolved)
                value = {k: v for k, v in value.items() if k != "partition"}
            if key == "decision_id":
                assert value == decision, (decision, ev)
            elif key != "id":
                record[key] = value
        if ev["kind"] == "QUALIFIED":
            decision = ev["id"]
        normalized.append(record)
    return normalized


def worker(mode, files):
    # Import production code from the requested checkout. The oracle itself only
    # imports the adjacent independent raw source module.
    sys.path.insert(0, str(Path.cwd()))
    from research_backend.app import month_replay, storage
    from research_backend.app.db import initialize, transaction
    from research_backend.app.historical_import import import_bi5

    if mode == "seed":
        initialize()
        for stamp, path in sorted(files.items()):
            day = datetime.fromtimestamp(stamp, UTC)
            import_bi5(Path(path).read_bytes(), "EURUSD", day, day.hour)
        return {"registered_inputs": len(files)}

    from fastapi.testclient import TestClient
    from research_backend.app.main import app
    from research_backend.app.recorded_session import read_session
    from scripts.verify_real_strategy_evaluation import peak_rss_bytes

    counters = Counter(bi5_decodes=0, bi5_logical_bytes=0, parquet_verifications=0, parquet_logical_bytes=0)
    quote_comparison = {"verified_quote_rows": 0, "first_quote_divergence": None}
    instrument_reads(storage, counters)
    if mode == "parity":
        instrument_quotes(month_replay, files, quote_comparison)
    with TestClient(app, base_url="http://localhost") as client:
        if mode == "warm":
            # Prime each identical pinned hourly reader once outside the timer.
            with transaction() as db:
                for stamp in sorted(files):
                    read_session(db, "EURUSD", stamp, stamp + 3600, [])
        warmup_reads = dict(counters)
        counters.subtract(warmup_reads)
        begin = perf_counter()
        manifest = response_json(client.get(MONTH_PATH))
        url = MONTH_PATH + "/" + manifest["session"]
        assert manifest["checkpoint"] is None
        index, requests = 0, 0
        while True:
            step = response_json(client.post(url + "/advance", params={"index": index}))
            requests += 1
            assert step["next_index"] > index, step
            index = step["next_index"]
            if step["completed"]:
                break
        reopened = response_json(client.get(MONTH_PATH, params={"session": manifest["session"]}))
        assert reopened["checkpoint"] == step
        elapsed = perf_counter() - begin
        timed_rss = peak_rss_bytes()
        timed_reads = dict(counters)

        pages = []
        after = -1
        while part := response_json(client.get(url + "/steps", params={"after": after, "limit": 48})):
            pages.extend(part)
            after = part[-1]["index"]
        coverage = [receipt for page in pages for receipt in page["coverage"]]
        events = [event for page in pages for event in page["events"]]
        assert len(pages) == requests
        assert len(coverage) == index
        binding = manifest["binding"]
        events = production_events(client, url, events, binding, mode == "parity")
        inventory = [
            dict(start=h["start"], status=h["status"], source_sha256=h["pin"]["source_sha256"] if h["pin"] else None)
            for h in binding["hours"]
        ]
    return dict(mode=mode, seconds=elapsed, rss_bytes=timed_rss, requests=requests,
                warmup_reads=warmup_reads, timed_reads=timed_reads, policy=binding["policy"],
                service_hour_reads=step["hour_reads"], service_open_gap_intervals=reopened.get("open_gap_intervals"),
                session=manifest["session"],
                ledger_sha256=digest(pages), quote_count=step["quote_count"], inventory=inventory,
                coverage=coverage, events=events, **quote_comparison)


def first_difference(stage, want, got):
    for index, (a, b) in enumerate(zip_longest(want, got)):
        if a != b:
            row = a or b
            return dict(stage=stage, index=index, expected=a, actual=b,
                        at_ms=row["start"] * 1000 if "start" in row else row["quote"]["time_ms"])
    return None


def reopened_gap_difference(count, observed, candidate):
    if observed is None and not candidate:
        return None  # Historical baseline versions did not publish this field.
    if type(observed) is not int or observed != count:
        return dict(stage="reopened_gap_count", expected=count, actual=observed)
    return None


def parity_report(want, got, candidate, *, verify_quote_rows=True):
    differences = [
        first_difference(key, want[key], got[key]) for key in ("inventory", "coverage", "events")
    ]
    differences.append(got["first_quote_divergence"])
    first = min((d for d in differences if d), key=lambda d: d["at_ms"], default=None)
    if first is None and verify_quote_rows and want["quote_count"] != got["verified_quote_rows"]:
        first = dict(stage="quote_count", expected=want["quote_count"], actual=got["verified_quote_rows"])
    if first is None and want["quote_count"] != got["quote_count"]:
        first = dict(stage="checkpoint_quote_count", expected=want["quote_count"], actual=got["quote_count"])
    gap_count = sum(len(slot["gaps"]) for slot in want["coverage"])
    reopened = got.get("service_open_gap_intervals")
    if first is None:
        first = reopened_gap_difference(gap_count, reopened, candidate)
    return dict(passed=first is None, first_divergence=first,
                verified_quote_rows=got["verified_quote_rows"], policy=got["policy"],
                expected_gap_intervals=gap_count, reopened_gap_intervals=reopened,
                ledger_sha256=got["ledger_sha256"])


def summarize(want):
    counts = Counter(h["status"] for h in want["inventory"])
    gaps = [(slot["status"], pair) for slot in want["coverage"] for pair in slot["gaps"]]
    merged = []
    for _, (start, end) in gaps:
        assert end > start and (not merged or start >= merged[-1][1]), (start, end)
        if merged and merged[-1][1] == start:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    statuses = Counter(status for status, _ in gaps)
    return dict(inventory_counts=dict(counts),
                available_hours=[h for h in want["inventory"] if h["status"] == "REGISTERED"],
                missing_open_seconds=[h["start"] for h in want["inventory"] if h["status"] == "MISSING_OPEN"],
                first_open_coverage_gap_ms=merged[0] if merged else None,
                merged_open_gaps_ms=merged, coverage_gap_counts=dict(statuses),
                unobserved_open_ms=sum(b - a for a, b in merged),
                coverage_sha256=digest(want["coverage"]), events=want["events"],
                quote_count=want["quote_count"], ordered_slot_hashes_sha256=digest(want["slot_quote_hashes"]),
                full_month_coverage=not counts["MISSING_OPEN"] and not gaps)


def run_process(repo, directory, mode, files, isolated_root):
    env = os.environ | {
        "TRADEFORGE_STORAGE": str(directory), "TRADEFORGE_DATA_DIR": str(directory),
        "TRADEFORGE_PAPER_STORAGE": str(directory / "paper"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", mode, "--file-map", str(files)],
                          cwd=repo, env=env, check=False, capture_output=True, text=True)
    if proc.returncode:
        error = proc.stderr.replace(str(isolated_root), "<isolated-storage>").replace(str(repo), "<worktree>")
        raise RuntimeError(f"{mode} worker failed ({proc.returncode}): {error[-5000:]}")
    return json.loads(proc.stdout)


def clean_commit(repo):
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=repo, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def windows_replay(source, baseline, candidate, repeats, workspace):
    want = expected(source)
    if want["closed_observations"]:
        closed = want["closed_observations"][0]
        return dict(first_closed_hour=closed, reason="SOURCE_CALENDAR_CONTRADICTION",
                    first_divergence=dict(stage="source_calendar", expected="SCHEDULED_CLOSURE", actual=closed))
    report = dict(oracle=summarize(want), variants={})
    mode, label, trial = "seed", "baseline", None
    try:
        with TemporaryDirectory(prefix="nov25-", dir=workspace) as temp:
            root = Path(temp)
            file_map = root / "files.json"
            file_map.write_text(json.dumps({stamp: str(path.resolve()) for stamp, path in source.files.items()}))
            seed = root / "seed"
            run_process(baseline, seed, "seed", file_map, root)
            variants = {"baseline": baseline}
            if candidate:
                variants["candidate"] = candidate

            def run(label, mode):
                copy = (root / f"{label}-{mode}").resolve()
                if not copy.is_relative_to(root.resolve()):
                    raise ValueError("Replay copy escaped the isolated workspace")
                shutil.copytree(seed, copy)
                try:
                    return run_process(variants[label], copy, mode, file_map, root)
                finally:
                    shutil.rmtree(copy)

            mode = "parity"
            for label, repo in variants.items():
                commit = clean_commit(repo)
                parity = parity_report(want, run(label, "parity"), candidate=label == "candidate")
                report["variants"][label] = dict(commit=commit, parity=parity, measurements=[])
                if difference := parity["first_divergence"]:
                    report.setdefault("first_divergence", dict(variant=label, mode=mode, **difference))
            for trial in range(repeats):
                order = list(variants) if trial % 2 == 0 else list(reversed(variants))
                for mode in ("cold", "warm"):
                    for label in order:
                        measured = run(label, mode)
                        variant = report["variants"][label]
                        parity = variant["parity"]
                        ledger = measured["ledger_sha256"]
                        match = parity["ledger_sha256"] == ledger
                        comparison = parity_report(want, measured, candidate=label == "candidate",
                                                   verify_quote_rows=False)
                        difference = comparison["first_divergence"]
                        if difference is None and not match:
                            difference = dict(stage="timed_ledger_sha256", expected=parity["ledger_sha256"], actual=ledger)
                        if difference:
                            report.setdefault("first_divergence", dict(variant=label, mode=mode, trial=trial + 1,
                                                                       **difference))
                        for key in ("inventory", "coverage", "events", "first_quote_divergence", "verified_quote_rows"):
                            measured.pop(key)
                        variant["measurements"].append(dict(measured, parity_ledger_match=match,
                                                            trial=trial + 1, first_divergence=difference))
            mode = "verify_commit"
            for label, repo in variants.items():
                if clean_commit(repo) != report["variants"][label]["commit"]:
                    raise ValueError(f"{label} worktree changed during the run")
    except Exception as error:
        difference = dict(stage="comparison_error", variant=label, mode=mode,
                          diagnostic=error_diagnostic(error))
        if trial is not None:
            difference["trial"] = trial + 1
        report.setdefault("first_divergence", difference)
    return report


def mt5_calendar_report(receipt, expected_sha, preflight):
    raw = receipt.read_bytes()
    receipt_sha = sha256(raw).hexdigest()
    if expected_sha and receipt_sha != expected_sha:
        raise ValueError("MT5 capture receipt SHA-256 differs from requested pin")
    ledger = json.loads(raw)
    if ledger["symbol"] != "EURUSD" or ledger["digits"] != 5 or ledger["source"] != "MT5_COPY_TICKS_ALL":
        raise ValueError("Only five-decimal COPY_TICKS_ALL EURUSD MT5 captures are admitted")
    source = MT5Source(receipt.parent)
    captured_days, raw_hours = [], {}
    for record, stamp in zip(ledger["days"], range(START, END, 86400), strict=True):
        path = source.files[stamp]
        file_digest, millis, bids, asks = decode_mt5_day(path, stamp)
        expected_claim = (path.stem, stamp, stamp + 86400, file_digest, len(millis), 1)
        claim = (record["day"], record["start"], record["end"], record["sha256"], record["rows"],
                 record["attempts"][-1]["code"])
        if claim != expected_claim:
            raise ValueError(("MT5 manifest differs from original day bytes", path.stem))
        captured_days.append(dict(day=path.stem, sha256=file_digest, rows=len(millis)))
        for hour, indices in groupby(range(len(millis)), lambda i: int(millis[i]) // 3_600_000):
            selected = list(indices)
            first, last = selected[0], selected[-1]
            raw_hours[hour * 3600] = (
                [int(millis[i]) for i in selected],
                Quote(int(millis[first]), first, int(bids[first]), int(asks[first]), file_digest),
                Quote(int(millis[last]), last, int(bids[last]), int(asks[last]), file_digest)
            )
    hours, gaps, contradictions = [], [], []
    for is_open, group in groupby(range(START, END, 3600), open_hour):
        times, stamps = [], list(group)
        for stamp in stamps:
            row_times, first, last = raw_hours.get(stamp, ([], None, None))
            if is_open:
                status = "OBSERVED_OPEN" if row_times else "MISSING_OPEN"
                times.extend(row_times)
            else:
                status = "CLOSED_WITH_OBSERVATIONS" if row_times else "SCHEDULED_CLOSURE"
                if row_times:
                    contradictions.append(dict(start=stamp, count=len(row_times),
                                                first=first.proof(), last=last.proof()))
            day_sha = captured_days[(stamp - START) // 86400]["sha256"]
            hours.append(dict(start=stamp, end=stamp + 3600, rows=len(row_times), status=status,
                              first_ms=first.time_ms if first else None,
                              last_ms=last.time_ms if last else None,
                              first_ordinal=first.ordinal if first else None, day_sha256=day_sha))
        if is_open:
            start_ms, end_ms = stamps[0] * 1000, (stamps[-1] + 3600) * 1000
            if times:
                pairs = zip([start_ms - 1, *times], times)
                gaps.extend([a + 1, b] for a, b in pairs if b - a > GAP_MS)
                start_ms = times[-1] + 1
            if end_ms - start_ms > GAP_MS:
                gaps.append([start_ms, end_ms])

    count = Counter(hour["status"] for hour in hours)
    accepted = not contradictions and not count["MISSING_OPEN"] and not gaps
    report = dict(capture_sha256=receipt_sha, captured_days=captured_days,
                  source_rows=sum(day["rows"] for day in captured_days),
                  open_rows=sum(h["rows"] for h in hours if h["status"] == "OBSERVED_OPEN"),
                  counts=dict(count), closed_rows=sum(h["count"] for h in contradictions),
                  closed_hours_with_observations=contradictions,
                  missing_open_seconds=[h["start"] for h in hours if h["status"] == "MISSING_OPEN"],
                  open_gaps_ms=gaps, max_open_gap_ms=max((b - a for a, b in gaps), default=0),
                  accepted_for_replay=accepted, used_raw_timestamps=True)
    if preflight:
        try:
            report["product_preflight"] = verify_preflight(preflight, receipt, receipt_sha, hours, report)
        except Exception as error:
            report["product_preflight"] = dict(parity_passed=False, first_divergence=dict(
                stage="mt5_preflight_error", diagnostic=error_diagnostic(error)))
    return report


def verify_preflight(repo, receipt, receipt_sha, hours, oracle):
    with TemporaryDirectory(prefix="tf-nov25-mt5-") as tmp:
        result = Path(tmp) / "preflight.json"
        subprocess.run([sys.executable, "-m", "scripts.month_mt5_audit", str(receipt.resolve()),
                        "--sha256", receipt_sha, "--output", str(result)], cwd=repo,
                       check=True, capture_output=True, text=True)
        observed = json.loads(result.read_text())
    expected_status = "ELIGIBLE_FOR_INDEPENDENT_REPLAY" if oracle["accepted_for_replay"] else "REJECTED"
    reasons = []
    if oracle["closed_rows"]:
        reasons.append("RECORDED_CLOCK_CONTRADICTS_REPLAY_CALENDAR")
    if oracle["missing_open_seconds"] or oracle["open_gaps_ms"]:
        reasons.append("OPEN_SESSION_COVERAGE_INCOMPLETE")
    want = {"status": expected_status, "reasons": reasons, "capture_sha256": receipt_sha,
            "hours": hours, "counts": oracle["counts"],
            "rows": oracle["source_rows"], "closed_rows": oracle["closed_rows"],
            "open_gaps_ms": oracle["open_gaps_ms"]}
    first = next((key for key, value in want.items() if observed.get(key) != value), None)
    difference = None
    if first == "hours" and isinstance(observed.get("hours"), list):
        difference = first_difference("mt5_preflight_hours", hours, observed["hours"])
    elif first:
        difference = dict(stage=f"mt5_preflight_{first}", expected=want[first], actual=observed.get(first))
    commit = clean_commit(repo)
    return dict(commit=commit, status=observed.get("status"), reasons=observed.get("reasons"), first_divergence=difference,
                parity_passed=difference is None, hourly_receipts_sha256=digest(observed.get("hours")))


def error_diagnostic(error):
    detail = getattr(error, "stderr", None) or str(error)
    return f"{type(error).__name__}: {detail[-5000:]}"


def first_report_divergence(report):
    for source, result in report.items():
        preflight = result.get("product_preflight", {})
        if difference := result.get("first_divergence") or preflight.get("first_divergence"):
            return dict(source=source, **difference)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controls", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--mt5-capture", type=Path)
    parser.add_argument("--mt5-sha256")
    parser.add_argument("--mt5-preflight", type=Path)
    parser.add_argument("--baseline", type=Path, default=Path.cwd())
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path("storage/nov25-oracle"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--worker", choices=("seed", "parity", "cold", "warm"), help=argparse.SUPPRESS)
    parser.add_argument("--file-map", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        files = {int(stamp): path for stamp, path in json.loads(args.file_map.read_text()).items()}
        print(json.dumps(worker(args.worker, files)))
        return
    if not args.output or not (args.controls or args.mt5_capture):
        parser.error("provide --output and at least one source")
    if args.archive and not args.controls:
        parser.error("--archive requires --controls")
    if args.repeats < 0:
        parser.error("--repeats must be nonnegative")

    report = {}
    if args.mt5_capture:
        try:
            report["mt5"] = mt5_calendar_report(args.mt5_capture, args.mt5_sha256, args.mt5_preflight)
        except Exception as error:
            report["mt5"] = dict(first_divergence=dict(stage="source_validation", diagnostic=error_diagnostic(error)))
    if args.controls:
        source = "dukascopy_control"
        try:
            args.workspace.mkdir(parents=True, exist_ok=True)
            control = BI5Source(args.controls)
            verify_control(expected(control))
            report[source] = windows_replay(control, args.baseline, args.candidate,
                                            args.repeats, args.workspace.resolve())
            if args.archive:
                source = "dukascopy_archive"
                archive = BI5Source(args.controls, args.archive)
                if archive.files.keys() == control.files.keys():
                    # BI5Source already checked these overlapping inputs byte-for-byte.
                    report[source] = {"same_sha256_inputs_as_control": True, "registered": len(archive.files)}
                else:
                    report[source] = windows_replay(archive, args.baseline, args.candidate,
                                                    args.repeats, args.workspace.resolve())
        except Exception as error:
            report[source] = dict(first_divergence=dict(stage="source_validation", diagnostic=error_diagnostic(error)))
    first = first_report_divergence(report)
    report.update(passed=first is None, first_divergence=first)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return int(first is not None)


if __name__ == "__main__":
    raise SystemExit(main())
