"""Offline redacted statement import; all writes are local reconciliation receipts."""
import hashlib
import json

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from .db import transaction
from .statement_contract import Statement
from .statement_ledger import VALUATION_POLICY, apply, finalize, initial

router = APIRouter(prefix="/api/statements")
MAX_BYTES = 2 * 1024 * 1024


def unique_fields(pairs):
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("Duplicate JSON field")
    return fields


def source(db, identity):
    record = db.execute("SELECT * FROM statement_versions WHERE id=?", (identity,)).fetchone()
    if record is None:
        raise HTTPException(404, "Unknown statement version")
    return record, Statement.model_validate(json.loads(record["raw"].decode("utf-8-sig")), context={"read_v1": True})


def receipt(db, record, statement):
    identity = record["id"]
    report = db.execute("SELECT payload FROM statement_reports WHERE id=?", (identity,)).fetchone()
    cursor = db.execute("SELECT cursor FROM statement_progress WHERE id=?", (identity,)).fetchone()[0]
    stored = json.loads(report[0]) if report else None
    current = stored
    original = None
    limitation = None
    if stored is None or "valuation_policy" not in stored:
        original = stored
        # The original bytes are authoritative. A normalized V1 timestamp
        # may already have lost the very precision being checked here.
        try:
            exact = Statement.model_validate(json.loads(record["raw"].decode("utf-8-sig")))
        except ValidationError as exc:
            current = None
            item = exc.errors(include_url=False)[0]
            limitation = dict(field=list(item["loc"]), reason=item["msg"], value=item.get("input"))
        else:
            if original is not None:
                # Rebuild from immutable rows. The V1 checkpoint may include
                # obsolete closing issues appended by its finalization.
                state = initial(exact)
                for row in sorted(exact.rows,key=lambda r:(r.at,r.sequence)):
                    apply(exact,state,row)
                current = finalize(exact,state)
    return dict(id=identity, previous=record["previous"], version=record["version"], book=statement.book,
                start=statement.start, end=statement.end, currency=statement.currency, mode=statement.mode,
                cursor=cursor, total=len(statement.rows), report=current, original_v1_report=original,
                assessment_policy=VALUATION_POLICY, assessment_error=limitation,
                assessment_id=hashlib.sha256(f"{identity}:{VALUATION_POLICY}".encode()).hexdigest())




@router.post("", status_code=201)
async def register(request: Request):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, "Statement exceeds 2 MiB")
        raw.extend(chunk)
    try:
        statement = Statement.model_validate(json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_fields))
    except ValidationError as exc:
        item = exc.errors(include_input=False, include_url=False)[0]
        raise HTTPException(422, f"{'.'.join(map(str,item['loc']))}: {item['msg']}") from exc
    except (ValueError, RecursionError) as exc:
        raise HTTPException(422, "Expected unique-key redacted UTF-8 statement JSON") from exc
    identity = hashlib.sha256(raw).hexdigest()
    series = json.dumps([statement.book, statement.start, statement.end])
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM statement_versions WHERE id=?", (identity,)).fetchone():
            previous = db.execute("SELECT id,version FROM statement_versions WHERE series=? ORDER BY version DESC LIMIT 1", (series,)).fetchone()
            db.execute("INSERT INTO statement_versions VALUES(?,?,?,?,?)",
                       (identity, series, previous["version"]+1 if previous else 1, previous["id"] if previous else None, raw))
            db.execute("INSERT INTO statement_progress VALUES(?,0,?)", (identity,json.dumps(initial(statement))))
        return receipt(db, *source(db, identity))


@router.get("")
def versions():
    with transaction() as db:
        return [dict(id=r["id"],book=json.loads(r["series"])[0],start=json.loads(r["series"])[1],version=r["version"])
                for r in db.execute("SELECT id,series,version FROM statement_versions ORDER BY rowid DESC")]


@router.get("/{identity}")
def detail(identity: str):
    with transaction() as db:
        db.execute("BEGIN")
        return receipt(db, *source(db, identity))


@router.post("/{identity}/advance")
def advance(identity: str, cursor: int = Query(ge=0), limit: int = Query(100, ge=1, le=250)):
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        record, s = source(db, identity)
        progress = db.execute("SELECT * FROM statement_progress WHERE id=?", (identity,)).fetchone()
        if cursor > progress["cursor"]:
            raise HTTPException(409, "Requested frontier has not committed")
        if cursor == progress["cursor"] and not db.execute("SELECT 1 FROM statement_reports WHERE id=?", (identity,)).fetchone():
            try:
                s = Statement.model_validate(json.loads(record["raw"].decode("utf-8-sig")))
            except ValidationError as exc:
                raise HTTPException(422, "Historical source requires corrected timestamps before processing") from exc
            state = json.loads(progress["state"])
            ordered = sorted(s.rows,key=lambda r:(r.at,r.sequence))
            end = min(cursor+limit,len(ordered))
            for ordinal in range(cursor,end):
                row = ordered[ordinal]
                trace = apply(s,state,row)
                db.execute("INSERT INTO statement_traces VALUES(?,?,?,?)", (identity,ordinal,row.id,json.dumps(trace)))
            if end == len(ordered):
                db.execute("INSERT INTO statement_reports VALUES(?,?)", (identity,json.dumps(finalize(s,state))))
            db.execute("UPDATE statement_progress SET cursor=?,state=? WHERE id=?", (end,json.dumps(state),identity))
        return receipt(db, record, s)


@router.get("/{identity}/rows")
def traces(identity: str, offset: int = Query(0,ge=0), limit: int = Query(100,ge=1,le=250)):
    with transaction() as db:
        source(db,identity)
        return [json.loads(r[0]) for r in db.execute("SELECT payload FROM statement_traces WHERE id=? ORDER BY ordinal LIMIT ? OFFSET ?", (identity,limit,offset))]


@router.get("/{identity}/evidence")
def evidence(identity: str):
    with transaction() as db:
        record,s = source(db,identity)
        changes = []
        if record["previous"]:
            _,old = source(db,record["previous"])
            for field in type(s).model_fields:
                if field == "rows":
                    continue
                a,b = getattr(old,field),getattr(s,field)
                if a != b:
                    changes.append(dict(field=field,before=a,after=b))
            a,b = ({r.id:r for r in version.rows} for version in (old,s))
            for key in sorted(a.keys()|b.keys()):
                if a.get(key)!=b.get(key):
                    changes.append(dict(row_id=key,before=a.get(key),after=b.get(key)))
        return dict(sha256=identity,header=s.model_dump(exclude={"rows"}),changes=changes)
