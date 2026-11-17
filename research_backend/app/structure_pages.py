"""Deterministic backward pages over a fully evaluated structure stream."""
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections import deque
import json
import re

from pydantic import BaseModel, Field


class StaleStructureCursor(ValueError):
    pass


class StructureDelivery(BaseModel):
    limit: int = Field(default=80, ge=1, le=500)
    pivot_limit: int | None = Field(default=None, ge=0, le=500)
    break_limit: int | None = Field(default=None, ge=0, le=500)
    pivot_cursor: str | None = Field(default=None, max_length=180)
    break_cursor: str | None = Field(default=None, max_length=180)


class EventPage:
    def __init__(self, kind: str, limit: int, cursor: str | None, *, max_before: int = 40_000):
        self.kind = kind
        self.limit = limit
        self.total = 0
        self.before = None
        self.expected_snapshot = None
        self.events = deque(maxlen=limit)
        if cursor:
            try:
                if not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
                    raise ValueError
                version, expected_kind, snapshot, before = json.loads(
                    urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if (version != 1 or expected_kind != kind or
                    not isinstance(snapshot, str) or not re.fullmatch(r"[a-f0-9]{64}", snapshot) or
                    type(before) is not int or not 0 < before <= max_before):
                    raise ValueError
                self.expected_snapshot, self.before = snapshot, before
            except (TypeError, ValueError, UnicodeError) as error:
                raise ValueError(f"Invalid {kind} cursor") from error

    def append(self, event):
        if self.before is None or self.total < self.before:
            self.events.append(event)
        self.total += 1

    def finish(self, snapshot):
        if self.expected_snapshot and (self.expected_snapshot != snapshot or self.before > self.total):
            raise StaleStructureCursor("Structure evidence changed. Restart pagination from the latest page.")
        end = self.total if self.before is None else self.before
        start = end - len(self.events)
        next_cursor = None
        if self.limit and start:
            value = json.dumps([1, self.kind, snapshot, start], separators=(",", ":"))
            next_cursor = urlsafe_b64encode(value.encode()).decode().rstrip("=")
        return {"total": self.total, "returned": len(self.events), "start": start, "end": end,
                "truncated": len(self.events) < self.total, "next_cursor": next_cursor}
