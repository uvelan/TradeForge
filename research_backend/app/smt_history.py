"""Append-only SMT evidence and confirmation notifications."""

from hashlib import sha256
import json
from contextlib import nullcontext
from time import time as wall_clock

from .db import transaction
from .smt_monitor_lease import MonitorLease, owns


def scope_key(provenance: dict, rules: dict) -> str:
    identity = json.dumps({"version": 2, "feeds": provenance, "rules": rules}, sort_keys=True)
    return sha256(identity.encode()).hexdigest()


def record_source_corrections(db, scope: str, capture: dict, observed_at: int) -> set[str]:
    # A sparse capture does not prove that the source deleted a prior observation.
    indexed = {side: {bar["time"]: bar for bar in bars} for side, bars in capture.items()}
    if any(not bars for bars in indexed.values()):
        return set()
    boundaries = {side: (min(bars), max(bars)) for side, bars in indexed.items()}
    for row in db.execute(
        """SELECT event_key, status, payload FROM smt_event_history AS history
           WHERE scope_key=? AND status IN ('SMT_CANDIDATE', 'SMT_CONFIRMED')
             AND event_time >= ?
             AND NOT EXISTS (SELECT 1 FROM smt_source_corrections AS correction
                             WHERE correction.event_key=history.event_key)
           ORDER BY CASE status WHEN 'SMT_CONFIRMED' THEN 0 ELSE 1 END""",
        (scope, min(start for start, _ in boundaries.values())),
    ).fetchall():
        prior = json.loads(row["payload"])
        changes = []
        for side, saved in prior["capture"].items():
            start, end = boundaries[side]
            for old in saved["bars"]:
                timestamp = old["time"]
                observed = indexed[side].get(timestamp)
                if start <= timestamp <= end and observed is not None and observed != old:
                    changes.append({"side": side, "time": timestamp, "previous": old,
                                    "observed": observed})
        if not changes:
            continue
        correction = {**prior, **({"watch_published_at":int(wall_clock())} if "watch_published_at" in prior else {}), "status": "SMT_SOURCE_CORRECTED",
                      "transitions": [*prior["transitions"], {"status": "SMT_SOURCE_CORRECTED", "time": observed_at}],
                      "source_correction": {"observed_at": observed_at, "changes": changes,
                                            "previous_status": row["status"]}}
        db.execute("INSERT INTO smt_source_corrections VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                   (row["event_key"], scope, observed_at, json.dumps(correction, sort_keys=True)))
    return {json.loads(row["payload"])["id"] for row in db.execute(
        "SELECT payload FROM smt_source_corrections WHERE scope_key=?", (scope,)
    )}


def event_identity(scope: str, event: dict) -> str:
    pivots = {side: event[side] for side in ("primary", "comparison")}
    return sha256(f"{scope}:{event['id']}:{json.dumps(pivots, sort_keys=True)}".encode()).hexdigest()


def record_smt(events: list[dict], scope: str, provenance: dict, rules: dict, *,
               alert_after: int | None = None, capture_step: int | None = None,
               capture: dict | None = None, observed_at: int | None = None,
               monitor_lease: MonitorLease | None = None, connection=None, progress=None) -> tuple[list[dict], set[str]]:
    alerts = []
    with (transaction() if connection is None else nullcontext(connection)) as db:
        if connection is None:
            db.execute("BEGIN IMMEDIATE")
        if monitor_lease is not None and not owns(db, monitor_lease):
            return alerts, set()
        corrected = record_source_corrections(db, scope, capture, observed_at) if capture is not None else set()
        for event in events:
            if event["id"] in corrected:
                continue
            key = event_identity(scope,event)
            existing = {row["status"]: json.loads(row["payload"]) for row in db.execute(
                "SELECT status,payload FROM smt_event_history WHERE event_key=?", (key,)
            )}
            snapshot = {k: v for k, v in event.items() if k != "capture"}
            snapshot.update(event_key=key, provenance=provenance, rules=rules, version=2)
            for index, transition in enumerate(event["transitions"]):
                status, time = transition["status"], transition["time"]
                if status in existing:
                    continue
                evidence = {**snapshot, "status": status, "transitions": event["transitions"][:index + 1],
                            "confirmed_at": event["confirmed_at"] if status != "SMT_CANDIDATE" else None}
                if monitor_lease is not None:
                    evidence["watch_published_at"] = int(wall_clock())
                if capture_step is not None:
                    evidence["capture"] = {}
                    for side, bars in event["capture"].items():
                        closed = [bar for bar in bars if bar["time"] + capture_step <= time]
                        evidence["capture"][side] = {
                            "bars": closed,
                            "sha256": sha256(json.dumps(closed, sort_keys=True).encode()).hexdigest(),
                        }
                    prior = existing.get("SMT_CONFIRMED") or existing.get("SMT_CANDIDATE")
                    if prior and any(
                        evidence["capture"][side]["bars"][:len(saved["bars"])] != saved["bars"]
                        for side, saved in prior["capture"].items()
                    ):
                        # A revised capture cannot extend the earlier event's verified record.
                        break
                db.execute(
                    "INSERT INTO smt_event_history VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (key, scope, status, time, json.dumps(evidence, sort_keys=True)),
                )
                existing[status] = evidence
            confirmed = existing.get("SMT_CONFIRMED")
            if (confirmed and (monitor_lease or ("SMT_INVALIDATED" not in existing and event["status"] == "SMT_CONFIRMED"))
                    and alert_after is not None
                    and confirmed["confirmed_at"] > alert_after):
                alert = {**confirmed, "alert_recorded_at": observed_at}
                inserted = db.execute(
                    "INSERT INTO smt_alerts VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (key, scope, confirmed["confirmed_at"], json.dumps(alert, sort_keys=True)),
                ).rowcount
                if inserted:
                    alerts.append(alert)
        if progress is not None:
            save_progress(db,monitor_lease,progress)
        if monitor_lease and not owns(db,monitor_lease):
            raise WatchLeaseLost("SMT monitor lease expired before commit")
    return alerts, corrected


class WatchLeaseLost(RuntimeError):
    pass


def save_progress(db,lease,progress):
    if not owns(db,lease):
        raise WatchLeaseLost("SMT monitor no longer owns the watch")
    db.execute("""INSERT INTO smt_watch_checkpoint VALUES (?,?) ON CONFLICT(generation)
        DO UPDATE SET payload=excluded.payload""",(lease.generation,json.dumps(progress,sort_keys=True)))
