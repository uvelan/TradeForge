"""Append-only SMT evidence and confirmation notifications."""

from hashlib import sha256
import json

from .db import transaction


def scope_key(provenance: dict, rules: dict) -> str:
    identity = json.dumps({"version": 2, "feeds": provenance, "rules": rules}, sort_keys=True)
    return sha256(identity.encode()).hexdigest()


def record_smt(events: list[dict], scope: str, provenance: dict, rules: dict, *,
               alert_after: int | None = None, capture_step: int | None = None) -> list[dict]:
    alerts = []
    with transaction() as db:
        for event in events:
            pivots = {side: event[side] for side in ("primary", "comparison")}
            key = sha256(f"{scope}:{event['id']}:{json.dumps(pivots, sort_keys=True)}".encode()).hexdigest()
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
                inserted = db.execute(
                    "INSERT INTO smt_event_history VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (key, scope, status, time, json.dumps(evidence, sort_keys=True)),
                ).rowcount
                if inserted and status == "SMT_CONFIRMED" and alert_after is not None and time > alert_after:
                    alerts.append(evidence)
    return alerts
