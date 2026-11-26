"""Inspect review JSON before Pydantic and classify immutable pre-upgrade uploads."""
from hashlib import sha256
import json

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute

MAX_BYTES = 1_000_000
CLEAN = "UNAMBIGUOUS_JSON"
AMBIGUOUS = "DUPLICATE_JSON_KEY"


class DuplicateKey(ValueError):
    pass


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise DuplicateKey(ascii(key)[:120])
        obj[key] = value
    return obj


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique_object)


class ParsedReviewRequest(Request):
    """Supply the exact validated bytes and their parsed value to the normal typed route."""
    def __init__(self, request, raw, parsed):
        super().__init__(request.scope, request.receive)
        self.raw = raw
        self.parsed = parsed

    async def body(self):
        return self.raw

    async def json(self):
        return self.parsed


class ReviewUploadRoute(APIRoute):
    def get_route_handler(self):
        validate_schema_and_import = super().get_route_handler()

        async def checked(request: Request):
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_BYTES:
                    raise HTTPException(413, "AI review exceeds 1 MB")
                raw.extend(chunk)
            original = bytes(raw)
            try:
                parsed = decode(original)
            except DuplicateKey as exc:
                raise HTTPException(422, f"{AMBIGUOUS}: {exc}") from exc
            except (ValueError, RecursionError) as exc:
                raise HTTPException(422, "Invalid review JSON") from exc
            return await validate_schema_and_import(ParsedReviewRequest(request, original, parsed))

        return checked


def backfill(db):
    # Previous writers might still have run since the last startup. Only inspect
    # unclassified originals and commit each bounded batch under writer ownership.
    while True:
        db.execute("BEGIN IMMEDIATE")
        originals = db.execute("""
            SELECT o.review_id,o.body,o.sha256 FROM ai_review_originals o
            WHERE NOT EXISTS (SELECT 1 FROM ai_review_integrity i WHERE i.review_id=o.review_id)
            LIMIT 16""").fetchall()
        for row in originals:
            status, key = CLEAN, None
            if sha256(row["body"]).hexdigest() != row["sha256"]:
                status = "ORIGINAL_CHECKSUM_MISMATCH"
            else:
                try:
                    decode(row["body"])
                except DuplicateKey as exc:
                    status, key = AMBIGUOUS, str(exc)
                except (ValueError, RecursionError):
                    status = "INVALID_ORIGINAL_JSON"
            db.execute("INSERT INTO ai_review_integrity VALUES(?,?,?,NULL)", (row["review_id"],status,key))
        db.commit()
        if len(originals)<16:
            return
