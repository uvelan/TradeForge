"""Append-only SMT evidence and confirmation notifications."""

from hashlib import sha256
import json

from .db import transaction


def scope_key(provenance: dict, rules: dict) -> str:
    identity = json.dumps({"version": 2, "feeds": provenance, "rules": rules}, sort_keys=True)
    return sha256(identity.encode()).hexdigest()


def record_smt(events: list[dict], scope: str, provenance: dict, rules: dict) -> None:
    with transaction() as db:
        for event in events:
            key = sha256(f"{scope}:{event['id']}".encode()).hexdigest()
            snapshot = {**event, "event_key": key, "provenance": provenance, "rules": rules, "version": 2}
            for index, transition in enumerate(event["transitions"]):
                status, time = transition["status"], transition["time"]
                evidence = {**snapshot, "status": status, "transitions": event["transitions"][:index + 1],
                            "confirmed_at": event["confirmed_at"] if status != "SMT_CANDIDATE" else None}
                db.execute(
                    "INSERT INTO smt_event_history VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (key, scope, status, time, json.dumps(evidence, sort_keys=True)),
                )
