"""Append-only SMT evidence and confirmation notifications."""

from hashlib import sha256
import json

from .db import transaction


def scope_key(provenance: dict, rules: dict) -> str:
    identity = json.dumps({"version": 2, "feeds": provenance, "rules": rules}, sort_keys=True)
    return sha256(identity.encode()).hexdigest()


def record_smt(events: list[dict], scope: str, provenance: dict, rules: dict, alert_after: int | None) -> list[dict]:
    alerts = []
    with transaction() as db:
        for event in events:
            key = sha256(f"{scope}:{event['id']}".encode()).hexdigest()
            snapshot = {**event, "event_key": key, "provenance": provenance, "rules": rules, "version": 2}
            for index, transition in enumerate(event["transitions"]):
                status, time = transition["status"], transition["time"]
                evidence = {**snapshot, "status": status, "transitions": event["transitions"][:index + 1],
                            "confirmed_at": event["confirmed_at"] if status != "SMT_CANDIDATE" else None}
                inserted = db.execute(
                    "INSERT INTO smt_event_history VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (key, scope, status, time, json.dumps(evidence, sort_keys=True)),
                ).rowcount
                if inserted and status == "SMT_CONFIRMED" and alert_after is not None and time > alert_after:
                    alerts.append(evidence)
    return alerts


def frozen_confirmations(events: list[dict], scope: str, primary: list, comparison: list, step: int) -> list[dict]:
    if not primary or not comparison:
        return events
    cutoff = min(primary[-1].time, comparison[-1].time) + step
    earliest = max(primary[0].time, comparison[0].time)
    primary_times = {bar.time for bar in primary}
    comparison_times = {bar.time for bar in comparison}
    by_id = {event["id"]: event for event in events}
    with transaction() as db:
        saved = db.execute(
            """SELECT event_key, status, payload FROM smt_event_history
                WHERE scope_key = ? AND event_time BETWEEN ? AND ? AND status <> 'SMT_CANDIDATE'
                ORDER BY event_time, status""", (scope, earliest, cutoff),
        ).fetchall()
    confirmed = {}
    for row in saved:
        event = json.loads(row["payload"])
        if row["status"] == "SMT_CONFIRMED":
            if all(event["primary"][point]["time"] in primary_times and
                   event["comparison"][point]["time"] in comparison_times for point in ("previous", "current")):
                confirmed[row["event_key"]] = event
        elif row["event_key"] in confirmed:
            original = confirmed[row["event_key"]]
            original["status"] = "SMT_INVALIDATED"
            original["transitions"].append(event["transitions"][-1])
    for event in confirmed.values():
        by_id[event["id"]] = event
    return sorted(by_id.values(), key=lambda event: (event["transitions"][0]["time"], event["id"]))
