"""Local append-only strategy definitions. Routes expose no market evaluation."""
import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, StrictInt, ValidationError
from starlette.concurrency import run_in_threadpool

from .db import one, rows, transaction, utc_now
from .strategy_catalog import DEFAULT_POLICY, INTEGRITY_DEFAULT_POLICY, VERSIONS, capability_catalog
from .strategy_definitions import Contract, validate_definition


router = APIRouter(prefix="/api/strategies/v1")
MAX_BODY = 64 * 1024
MAX_HISTORY = 200


def fail(path, code, expected, actual=None):
    raise HTTPException(422,{"status":"INVALID_STRATEGY_DEFINITION","errors":[
        {"path":path,"condition_id":None,"code":code,"expected":expected,"actual":actual}]})


def pairs(items):
    for key, _ in items:
        key.encode("utf-8")
    result = dict(items)
    if len(result)!=len(items):
        raise ValueError("Duplicate JSON key")
    return result


def reject_constant(value):
    raise ValueError(value)


async def read_object(request):
    content = bytearray()
    async for chunk in request.stream():
        if len(content)+len(chunk)>MAX_BODY:
            raise HTTPException(413,"Strategy request exceeds 64 KiB")
        content.extend(chunk)
    try:
        result = json.loads(content,object_pairs_hook=pairs,parse_constant=reject_constant)
    except (ValueError,RecursionError):
        fail("","INVALID_JSON","Unique keys, finite values and bounded JSON")
    if not isinstance(result,dict):
        fail("","INVALID_OBJECT","A JSON object")
    nodes = [(result,1)]
    count = 0
    while nodes:
        value,depth = nodes.pop()
        count += 1
        if count>4096 or depth>32:
            fail("","PAYLOAD_COMPLEXITY","At most 4096 JSON items and depth 32")
        if isinstance(value,dict):
            nodes.extend((child,depth+1) for child in value.values())
        elif isinstance(value,list):
            nodes.extend((child,depth+1) for child in value)
        elif isinstance(value,float) and not -float("inf") < value < float("inf"):
            fail("","INVALID_NUMBER","Finite JSON numbers")
        elif isinstance(value,str):
            try:
                value.encode("utf-8")
            except UnicodeError:
                fail("","INVALID_JSON","Unicode scalar values")
    return result


class Expected(Contract):
    expected_version: StrictInt = Field(ge=1,le=MAX_HISTORY)


class Revision(Expected):
    definition: dict


async def envelope(request, model):
    payload = await read_object(request)
    try:
        return model.model_validate(payload)
    except ValidationError:
        fail("","INVALID_MUTATION","Integer expected_version and the required request fields")


def validate_submission(payload):
    result = validate_definition(payload)
    if result["valid"] and payload["schema_version"] == 1 and payload.get("status") == "READY_FOR_EVALUATION":
        result = {**result, "valid":False, "status":"INVALID_STRATEGY_DEFINITION", "errors":[{
            "path":"schema_version", "condition_id":None, "code":"LEGACY_REQUIRES_REVISION",
            "expected":"Revise this strategy as schema 2", "actual":1,
        }]}
        result.pop("definition")
        result.pop("configuration_fingerprint")
    return result


def validated(payload):
    result = validate_submission(payload)
    if not result["valid"]:
        raise HTTPException(422,result)
    return result


def unpack(row):
    value = dict(row)
    value["definition"] = json.loads(value["definition"])
    return value


def current(db, strategy_id, version=None):
    if version is None:
        item = db.execute("SELECT * FROM strategy_versions WHERE strategy_id=? ORDER BY version DESC LIMIT 1",
                          (strategy_id,)).fetchone()
    else:
        item = db.execute("SELECT * FROM strategy_versions WHERE strategy_id=? AND version=?",
                          (strategy_id,version)).fetchone()
    if item is None:
        raise HTTPException(404,"Strategy version not found")
    return unpack(item)


def store_version(db, strategy_id, version, result, *, archive=False):
    created = utc_now()
    version_id = "SV-"+uuid4().hex
    definition = result["definition"]
    status = "ARCHIVED" if archive else definition["status"]
    db.execute("INSERT INTO strategy_versions VALUES(?,?,?,?,?,?,?)",
               (strategy_id,version,version_id,created,status,result["configuration_fingerprint"],
                json.dumps(definition,sort_keys=True,separators=(",",":"))))
    return current(db,strategy_id,version)


def expect(row, expected):
    if row["version"] != expected:
        raise HTTPException(409,{"code":"VERSION_CONFLICT","current_version":row["version"]})
    if row["status"]=="ARCHIVED":
        raise HTTPException(409,{"code":"STRATEGY_ARCHIVED","current_version":row["version"]})


def append(strategy_id, expected, result=None):
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = current(db,strategy_id)
        expect(previous,expected)
        if result and result["definition"]["schema_version"] < previous["definition"]["schema_version"]:
            fail("schema_version","SCHEMA_DOWNGRADE","Preserve or upgrade the saved schema",
                 result["definition"]["schema_version"])
        if previous["version"]>=MAX_HISTORY:
            raise HTTPException(409,{"code":"HISTORY_LIMIT","current_version":previous["version"]})
        return store_version(db,strategy_id,expected+1,result or previous,archive=result is None)


def rule(id_, dependency, observation, expected, *, state="HISTORICAL"):
    return {"type":"RULE","id":id_,"dependency":dependency,"observation":observation,
            "operator":"EQ","expected":expected,
            "evidence":{"state":state,"purpose":"HISTORICAL_SIMULATION","max_age_seconds":3600}}


def template(direction):
    bearish = direction=="bearish"
    def binding(symbol):
        return {"symbol":symbol,"source":"DUKASCOPY_SAVED","price_basis":"BID","modeled_delay_seconds":0}
    def dep(id_,kind,role):
        return {"id":id_,"indicator":kind,"algorithm_version":VERSIONS[kind][0],
                "role":role,"timeframe":"M5","settings":{}}
    return {"schema_version":1,"defaults_policy":DEFAULT_POLICY,"name":direction.title()+" CRT / SMT",
            "description":"Research template. No optional filters enabled. Configure trigger, invalidation and execution policies.",
            "status":"DRAFT","availability_modes":["HISTORICAL_SIMULATION"],
            "instruments":{"primary":binding("EURUSD"),"comparison":binding("GBPUSD"),"calendar":{"kind":"forex_weekly"}},
            "timeframes":{"context":"M5","trigger":"M5"},
            "indicators":[dep("crt","CRT","PRIMARY"),dep("smt","SMT","PAIR")],
            "context":{"type":"GROUP","id":"context","operator":"ALL","children":[
                rule("reference","crt","CRT_REFERENCE","ACTIVE",state="CURRENT")]},
            "setup":{"type":"GROUP","id":"setup","operator":"SEQUENCE","children":[
                rule("reclaim","crt","CRT_RECLAIM","HIGH_SWEPT_RECLAIMED" if bearish else "LOW_SWEPT_RECLAIMED"),
                rule("divergence","smt","SMT_CONFIRMED",direction)]},
            "temporal":[{"first":"reclaim","then":"divergence","relation":"AFTER"}]}


def template_v2(direction):
    definition = template(direction)
    reference, = definition["context"]["children"]
    reclaim, smt = definition["setup"]["children"]
    reference["evidence"]["state"] = "HISTORICAL"
    reference.update(alias="crt_reference", selection="EARLIEST_ELIGIBLE")
    reclaim.update(alias="crt_reclaim", same_family_as="crt_reference", selection="BOUND_FAMILY")
    smt.update(alias="smt_confirmation", selection="EARLIEST_ELIGIBLE")
    definition.update(schema_version=2, defaults_policy=INTEGRITY_DEFAULT_POLICY,
                      trade_intent={"role":"PRIMARY", "direction":"SHORT" if direction=="bearish" else "LONG",
                                    "earliest":"AFTER_CAUSAL_AVAILABILITY"},
                      attempt={"context_mode":"LATCHED","candidate_limit":32,
                               "expiry_origin":"CONTEXT_ACTIVATION","crt_reference_alias":"crt_reference"})
    return definition


@router.get("/capabilities")
def capabilities(schema_version: int = Query(2, ge=1, le=2)):
    return capability_catalog(schema_version)


@router.get("/templates")
def templates():
    return [{"id":direction,"definition":(result:=validated(template_v2(direction)))["definition"],
             "unresolved":result["unresolved"]} for direction in ("bearish","bullish")]


@router.post("/validate")
async def validate(request:Request):
    return validate_submission(await read_object(request))


@router.post("",status_code=201)
async def create(request:Request):
    result = validated(await read_object(request))
    return await run_in_threadpool(create_version,result)


def create_version(result):
    with transaction() as db:
        return store_version(db,"ST-"+uuid4().hex,1,result)


@router.get("")
def strategy_list(limit: int=Query(40,ge=1,le=100), offset:int=Query(0,ge=0,le=10000)):
    return rows("""SELECT r.strategy_id,r.version,r.version_id,r.created_at,r.status,r.configuration_fingerprint,
                   json_extract(r.definition,'$.name') AS name,
                   json_extract(r.definition,'$.schema_version') AS schema_version,
                   json_extract(r.definition,'$.availability_modes[0]') AS purpose
                   FROM strategy_versions r WHERE r.version=
                   (SELECT MAX(v.version) FROM strategy_versions v WHERE v.strategy_id=r.strategy_id)
                   ORDER BY created_at DESC,strategy_id LIMIT ? OFFSET ?""",(limit,offset))


@router.get("/{strategy_id}")
def read_strategy(strategy_id:str, version:int | None=Query(None,ge=1,le=MAX_HISTORY)):
    with transaction() as db:
        return current(db,strategy_id,version)


@router.get("/{strategy_id}/history")
def history(strategy_id:str, limit:int=Query(30,ge=1,le=100), before:int=Query(MAX_HISTORY+1,ge=1,le=MAX_HISTORY+1)):
    if not one("SELECT 1 FROM strategy_versions WHERE strategy_id=?",(strategy_id,)):
        raise HTTPException(404,"Strategy not found")
    return rows("""SELECT version,version_id,created_at,status,configuration_fingerprint,
                   json_extract(definition,'$.name') AS name FROM strategy_versions
                   WHERE strategy_id=? AND version<? ORDER BY version DESC LIMIT ?""",(strategy_id,before,limit))


@router.post("/{strategy_id}/revisions",status_code=201)
async def revise(strategy_id:str, request:Request):
    body = await envelope(request,Revision)
    return await run_in_threadpool(append,strategy_id,body.expected_version,validated(body.definition))


@router.post("/{strategy_id}/archive",status_code=201)
async def archive(strategy_id:str, request:Request):
    body = await envelope(request,Expected)
    return await run_in_threadpool(append,strategy_id,body.expected_version)


@router.post("/{strategy_id}/duplicate",status_code=201)
async def duplicate(strategy_id:str, request:Request):
    body = await envelope(request,Expected)
    return await run_in_threadpool(copy_version,strategy_id,body.expected_version)


def copy_version(strategy_id, version):
    with transaction() as db:
        source = current(db,strategy_id,version)
    definition = {**source["definition"],"status":"DRAFT","name":source["definition"]["name"][:93]+" (copy)"}
    return create_version(validated(definition))


def differences(before, after, path=""):
    if before==after:
        return []
    if isinstance(before,dict) and isinstance(after,dict):
        return [item for key in sorted(before.keys() | after.keys()) for item in
                differences(before.get(key),after.get(key),f"{path}.{key}" if path else key)]
    if isinstance(before,list) and isinstance(after,list) and all(
            isinstance(item,dict) and "id" in item for item in (*before,*after)):
        old,new = ({item["id"]:item for item in values} for values in (before,after))
        changes = differences(old,new,path)
        if list(old)!=list(new) and old.keys()==new.keys():
            changes.append({"path":path+".order","before":list(old),"after":list(new)})
        return changes
    return [{"path":path,"before":before,"after":after}]


@router.get("/{strategy_id}/compare")
def compare(strategy_id:str, from_version:int=Query(ge=1,le=MAX_HISTORY), to_version:int=Query(ge=1,le=MAX_HISTORY)):
    with transaction() as db:
        left,right = (current(db,strategy_id,version) for version in (from_version,to_version))
    same = left["configuration_fingerprint"]==right["configuration_fingerprint"]
    changes = differences({**left["definition"],"status":left["status"]},
                          {**right["definition"],"status":right["status"]})
    return {"from_version_id":left["version_id"],"to_version_id":right["version_id"],
            "same_configuration":same,"changes":changes}
