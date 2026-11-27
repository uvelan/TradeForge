"""Import EURUSD BI5 sources and retain successful public downloads for offline replay."""
import argparse
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
from time import sleep

from research_backend.app import config
from research_backend.app.db import initialize, transaction
from research_backend.app.historical_import import import_bi5
from research_backend.app.month_replay import inventory
from research_backend.app.storage import decode_bi5
from scripts.benchmark_t06 import PINS


def download(day, name):
    url = f"https://datafeed.dukascopy.com/datafeed/EURUSD/{day.year}/{day.month-1:02}/{day.day:02}/{name}"
    with TemporaryDirectory() as temp:
        headers = Path(temp) / "headers"
        result = subprocess.run(["curl", "--proto", "=https", "--fail", "--silent", "--show-error",
                                 "--connect-timeout", "5", "--max-time", "15", "--max-filesize", "8000000",
                                 "--dump-header", str(headers), url], capture_output=True)
        status, retry = None, 0
        if headers.exists():
            for line in headers.read_text(encoding="latin-1").splitlines():
                if line.startswith("HTTP/"):
                    status = int(line.split()[1])
                if line.lower().startswith("retry-after:"):
                    value = line.partition(":")[2].strip()
                    try:
                        retry = int(value) if value.isdigit() else max(0, int(
                            (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())+1)
                    except (TypeError, ValueError, OverflowError):
                        pass
        return result.stdout, {"http": status, "curl_exit": result.returncode, "retry_after_seconds": retry}


def verify_digest(raw, expected, day):
    digest = sha256(raw).hexdigest()
    if expected and digest != expected:
        raise ValueError(f"Source checksum mismatch at {day.isoformat()}")
    return digest


def publish(target, raw, digest):
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(raw)
        except BaseException:
            stream.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.link(tmp, target)  # Atomic create; a concurrent verified file is never replaced.
    except FileExistsError:
        if sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Existing source changed at {target.name}")
    finally:
        tmp.unlink(missing_ok=True)


def _try_lock_receipt(stream):
    try:
        if os.name == "nt":
            import msvcrt

            # Lock beyond the receipt data so other processes can still read it.
            stream.seek(1 << 30)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EAGAIN):
            raise
        return False
    return True


def _recover_receipt(stream):
    resume_after = 0
    stream.seek(0)
    for line_number, line in enumerate(stream, 1):
        complete = line.endswith(b"\n")
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            if complete:
                raise ValueError(f"Invalid acquisition receipt at line {line_number}") from exc
            # A killed process can leave a fragment after the last committed newline.
            stream.truncate(stream.tell() - len(line))
            break
        if not isinstance(event, dict):
            raise ValueError(f"Invalid acquisition receipt at line {line_number}")
        resume_after = max(resume_after, event.get("resume_after_epoch", 0))
        if not complete:
            # Keep a complete object even if its terminating newline was interrupted.
            stream.write(b"\n")
            stream.flush()
    return resume_after


def acquire(pending, receipt, *, observations=None):
    # One local provider lock covers the check, network request and durable
    # cooldown. Month-specific receipts still contain full diagnostic history.
    directory = config.STORAGE_ROOT / "t06-input"
    directory.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    guard = directory / "dukascopy-provider.jsonl"
    if guard.resolve() == receipt.resolve():
        raise ValueError("Use a separate diagnostic receipt for acquisition")
    with guard.open("a+b") as provider:
        if not _try_lock_receipt(provider):
            return "ACQUISITION_IN_PROGRESS"
        deadline = _recover_receipt(provider)
        # Honor an outstanding pre-upgrade cooldown even on the first request
        # for a different month. Only the original month writers use these names.
        if provider.tell() == 0:
            for folder in {directory, receipt.parent}:
                for path in folder.glob("????-??-downloads.jsonl"):
                    with path.open("a+b") as previous:
                        if not _try_lock_receipt(previous):
                            return "ACQUISITION_IN_PROGRESS"
                        deadline = max(deadline, _recover_receipt(previous))
            store_cooldown(provider, deadline)
        with receipt.open("a+b") as stream:
            if not _try_lock_receipt(stream):
                return "ACQUISITION_IN_PROGRESS"
            local = _recover_receipt(stream)
            if local > deadline:
                store_cooldown(provider, local)
            if max(deadline,local) > datetime.now(UTC).timestamp():
                return "PROVIDER_COOLDOWN_ACTIVE"
            return _acquire(pending, stream, observations, provider)


def store_cooldown(stream, deadline):
    stream.write((json.dumps({"resume_after_epoch":deadline}) + "\n").encode("ascii"))
    stream.flush()
    os.fsync(stream.fileno())


def _acquire(pending, stream, observations, provider):
    failures = 0
    for attempt in range(1, 3):
        if attempt == 2 and pending:
            sleep(5)
        retry = []
        for day, target, expected in pending:
            raw, result = download(day, target.name)
            if not result["curl_exit"] and result["http"] == 200:
                digest = verify_digest(raw, expected, day)
                try:
                    records = decode_bi5(raw, "EURUSD", day, day.hour)
                    if not records:
                        raise ValueError("No tick records in provider response")
                except ValueError as exc:
                    result["validation_error"] = str(exc)
                else:
                    imported = import_bi5(raw, "EURUSD", day, day.hour)
                    publish(target, raw, digest)
                    result.update(sha256=digest, rows=imported["quality"]["valid_ticks"])
            observed = datetime.now(UTC)
            event = dict(start=int(day.timestamp()), at=observed.isoformat(), attempt=attempt, **result)
            failures = 0 if "sha256" in result else failures + 1
            pause = result["retry_after_seconds"] or (60 if result["http"] == 429 else 0)
            if pause:
                event["resume_after_epoch"] = int(observed.timestamp()) + pause + 1
                store_cooldown(provider, event["resume_after_epoch"])
                event["stop"] = "PROVIDER_COOLDOWN"
            elif failures >= 4:
                event["stop"] = "FOUR_CONSECUTIVE_FAILURES"
            stream.write((json.dumps(event) + "\n").encode("utf-8"))
            stream.flush()
            if observations is not None:
                observations.append(event)
            if "stop" in event:
                return event["stop"]
            if "sha256" not in result:
                retry.append((day, target, expected))
        pending = retry
    return "RETRY_LIMIT" if pending else "COMPLETE"


def run(month, directory, fetch, receipt=None):
    initialize()
    with transaction() as db:
        plan = inventory(db, month)
    pending = []
    for hour in plan["hours"]:
        if hour["status"] == "SCHEDULED_CLOSURE":
            continue
        day = datetime.fromtimestamp(hour["start"], UTC)
        name = f"{day.hour:02}h_ticks.bi5"
        target = directory / day.strftime("%Y-%m-%d") / name
        approved = PINS.get(day.hour) if day.date().isoformat() == "2025-11-13" else None
        registered = hour["pin"]["source_sha256"] if hour["pin"] else None
        if approved and registered and approved != registered:
            raise ValueError(f"Registered source differs from approved control at {day.isoformat()}")
        expected = approved or registered
        source = directory / name if approved and not target.exists() and (directory / name).is_file() else target
        local = source.is_file()
        raw = source.read_bytes() if local else None
        if raw is None and registered:
            with transaction() as db:
                stored = db.execute("SELECT payload FROM historical_bi5_sources WHERE sha256=?", (registered,)).fetchone()
            raw = stored[0] if stored else None
        if raw is not None:
            digest = verify_digest(raw, expected, day)
            import_bi5(raw, "EURUSD", day, day.hour)
            if not local:
                publish(target, raw, digest)
        elif fetch:
            pending.append((day, target, expected))
    if pending:
        reason = acquire(pending, receipt or directory / (month + "-downloads.jsonl"))
        print(f"Archive acquisition: {reason}", file=sys.stderr)
    with transaction() as db:
        print(json.dumps(inventory(db, month), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("month")
    parser.add_argument("--source", type=Path, default=Path("storage/t06-input"))
    parser.add_argument("--fetch", action="store_true", help="Fetch missing hours with one bounded retry pass.")
    parser.add_argument("--receipt", type=Path, help="Append acquisition receipts (default: beside source dates).")
    args = parser.parse_args()
    run(args.month, args.source, args.fetch, args.receipt)
