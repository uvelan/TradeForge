import json
from base64 import b64decode
from binascii import Error as Base64Error
from hashlib import sha256
from struct import unpack_from
from zlib import crc32

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .db import one, rows, transaction, utc_now
from .chart_snapshot_lanes import ChartLane, verified_lanes
from .domain import journal_detail, resolve_signal, resolve_snapshot
from .market import FRAME_MIN
from .schemas import SignalReference

router = APIRouter()


@router.get('/api/workspace/search')

def search_workspace(q:str=Query(min_length=2,max_length=80),offset:int=Query(0,ge=0,le=2000)):
    keyword=q.strip().lower()
    if len(keyword)<2:
        return {"items":[],"next_offset":None}
    matches=rows("""
        WITH records AS (
          SELECT 'plan' AS type,id,symbol||' / '||direction||' / '||id AS label,NULL AS status,updated_at AS sort
            FROM trades
          UNION ALL
          SELECT 'journal',id,id||' / '||title,NULL,created_at FROM journal_entries
          UNION ALL
          SELECT 'dataset',id,symbol||' / '||provider||' / '||coalesce(timeframe,kind)||' / '||substr(id,1,12),
                 NULL,created_at FROM datasets WHERE kind='candles'
          UNION ALL
          SELECT 'signal',history.event_key,
                 'SMT / '||json_extract(history.payload,'$.direction')||' / '||
                  json_extract(history.payload,'$.provenance.primary.symbol')||' / '||
                  json_extract(history.payload,'$.provenance.comparison.symbol'),
                 history.status,printf('%020d',history.event_time)
          FROM smt_event_history history
          WHERE history.status='SMT_CONFIRMED' AND NOT EXISTS (
            SELECT 1 FROM smt_source_corrections corrected WHERE corrected.event_key=history.event_key)
            AND NOT EXISTS (SELECT 1 FROM smt_event_history invalid
                 WHERE invalid.event_key=history.event_key AND invalid.status='SMT_INVALIDATED')
        )
        SELECT type,id,label,status FROM records
        WHERE label IS NOT NULL AND (instr(lower(label),?)>0 OR instr(lower(id),?)>0)
        ORDER BY CASE WHEN lower(id)=? THEN 0 WHEN instr(lower(label),?)=1 THEN 1 ELSE 2 END,
                 type,sort DESC,id LIMIT 13 OFFSET ?
    """,(keyword,keyword,keyword,keyword,offset))
    return {"items":matches[:12],"next_offset":offset+12 if len(matches)>12 else None}


@router.get('/api/journal/entries/{entry_id}')

def find_journal_entry(entry_id:str):
    return journal_detail(entry_id)


@router.get('/api/datasets/{dataset_id}')

def dataset_detail(dataset_id:str):
    dataset=one("SELECT * FROM datasets WHERE id=? AND kind='candles'",(dataset_id,))
    if not dataset:
        raise HTTPException(404,"Candle dataset unavailable")
    return dataset


class ChartSnapshotRequest(BaseModel):
    lanes: tuple[ChartLane, ChartLane] | None = None
    png: str = Field(min_length=40,max_length=2_000_000)
    symbol: str = Field(min_length=1,max_length=64)
    timeframe: str = Field(min_length=2,max_length=4)
    source: str = Field(min_length=1,max_length=30)
    signal_reference: SignalReference | None = None
    cutoff: int | None = Field(default=None,ge=0)


def validate_chart_png(png:bytes) -> tuple[int,int]:
    if (len(png)<45 or png[:16]!=bytes.fromhex("89504e470d0a1a0a0000000d49484452")
        or png[-12:]!=bytes.fromhex("0000000049454e44ae426082")):
        raise HTTPException(422,"Expected a bounded PNG chart image")
    width,height = unpack_from(">II",png,16)
    if not (1<=width<=4096 and 1<=height<=4096 and width*height<=8_000_000):
        raise HTTPException(422,"Snapshot dimensions exceed the supported limit")
    position=8
    data_seen=False
    while position+12<=len(png):
        size,kind=unpack_from(">I4s",png,position)
        end=position+12+size
        if end>len(png) or crc32(png[position+4:end-4])!=unpack_from(">I",png,end-4)[0]:
            break
        if kind==b"IEND":
            if end==len(png) and size==0 and data_seen:
                return width,height
            break
        data_seen |= kind==b"IDAT"
        position=end
    raise HTTPException(422,"PNG data is truncated or has an invalid checksum")


@router.post('/api/chart-snapshots',status_code=201)

def create_snapshot(request:ChartSnapshotRequest):
    if request.timeframe not in FRAME_MIN:
        raise HTTPException(422,"Unknown chart timeframe")
    try:
        png=b64decode(request.png,validate=True)
    except (Base64Error,ValueError) as error:
        raise HTTPException(422,"Malformed PNG encoding") from error
    width,height=validate_chart_png(png)
    metadata=request.model_dump(mode="json",exclude={"png","lanes"})
    metadata.update(width=width,height=height,authority="USER_CHART_CAPTURE")
    with transaction() as db:
        signal=resolve_signal(db,request.signal_reference)
        if signal is not None and (signal["provenance"]["timeframe"]!=request.timeframe
                                   or signal["provenance"]["primary"]["symbol"]!=request.symbol
                                   or signal["provenance"]["primary"]["source"]!=request.source):
            raise HTTPException(422,"Snapshot context differs from linked signal")
        if request.lanes is not None:
            metadata.update(lane_schema_version=1, lanes=verified_lanes(db, request, signal))
        if signal is not None:
            transition = next((t for t in reversed(signal["transitions"])
                               if t["status"] == request.signal_reference.status), None)
            if transition is None:
                raise HTTPException(409, "Signal revision has no matching transition")
            metadata["signal_transition_at"] = transition["time"]
            metadata["chart_context"] = "FROZEN" if request.cutoff is not None else "RETROSPECTIVE"
            if request.lanes is not None:
                metadata["chart_evidence_through"] = max(lane["latest_bar_available_at"] for lane in metadata["lanes"])
        data=json.dumps(metadata,sort_keys=True,separators=(",",":"))
        snapshot_id=sha256(data.encode()+png).hexdigest()
        db.execute("INSERT INTO chart_snapshots VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING",
                   (snapshot_id,data,png,utc_now()))
    return {"id":snapshot_id,**metadata}


@router.get('/api/chart-snapshots')
def list_snapshots(event_key:str=Query(pattern=r"^[0-9a-f]{64}$"),
                   status:str=Query(min_length=1,max_length=40)):
    return [{"id":row["id"],"created_at":row["created_at"]} for row in rows(
        """SELECT id,created_at FROM chart_snapshots
          WHERE json_extract(metadata,'$.signal_reference.event_key')=?
          AND json_extract(metadata,'$.signal_reference.status')=?
          ORDER BY created_at DESC,id LIMIT 10""",(event_key,status))]


@router.get('/api/chart-snapshots/{snapshot_id}')
def snapshot_metadata(snapshot_id:str):
    with transaction() as db:
        metadata, _, _ = resolve_snapshot(db, snapshot_id)
    return {"id":snapshot_id, **metadata}


@router.get('/api/chart-snapshots/{snapshot_id}/image')

def snapshot_image(snapshot_id:str):
    row=one("SELECT png,metadata FROM chart_snapshots WHERE id=?",(snapshot_id,))
    if row is None:
        raise HTTPException(404,"Chart snapshot unavailable")
    if sha256(row["metadata"].encode()+row["png"]).hexdigest()!=snapshot_id:
        raise HTTPException(409,"Chart snapshot checksum mismatch")
    return Response(row["png"],media_type="image/png",headers={"X-Content-Type-Options":"nosniff"})
