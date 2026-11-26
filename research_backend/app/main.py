"""TradeForge — localhost-only API, no execution/trading authority."""
from __future__ import annotations
from contextlib import asynccontextmanager
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from typing import Literal
from hashlib import sha256
import asyncio
import json
import uuid
from fastapi import APIRouter, FastAPI, HTTPException, Query, UploadFile, File, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel,Field, model_validator
import httpx
from . import binance
from .db import initialize, rows, one, transaction, utc_now
from .market import mt5_connection, mt5_rates, mt5_recent_deals,mt5_quote,mt5_symbols,demo_candles,BASE,FRAME_MIN
from .storage import write_partition, read_partition
from .historical_import import import_bi5
from .reviewed_archives import import_reviewed, candle_origin
import errno
from .legacy_quote_evidence import read_v2_quote_evidence
from .domain import create_trade,get_trade,revise,decline_trade,close_trade,list_trades,analytics,export_ai,import_ai,journal_add,journal_list,journal_materialize
from .risk import evaluate
from .backtest import breakout_backtest
from .tick_replay import effective_tick_period, replay_breakout, replay_coverage, validate_bid_extremes
from .schemas import TradePlan,Outcome,ReviewInput,ReviewV1,JournalInput,RiskInput,SignalReference
from . import domain
from .review_integrity import ReviewUploadRoute
from .drawings import router as drawing_router
from .arrival_capture import CaptureManager
from .arrival_routes import router as arrival_router
from .ai_review_decisions import router as review_decision_router
from .workspace_routes import router as workspace_router
from .learning import router as learning_router
from .historical_packages import HistoricalReview, import_historical, router as historical_package_router
from .month_replay import router as month_router
from .decision_reviews import router as decision_review_router
from .strategy_routes import router as strategy_router
from .strategy_evaluation_routes import router as strategy_evaluation_router
from .historical_strategy_routes import router as historical_strategy_router
from .historical_strategy_worker import supervise as supervise_strategy_history
from .indicator_routes import router as indicator_router, run_smt_monitor, signal_candles, signal_revision
from .indicator_coverage import replay_availability
from .replay_confluence import inspect_confluence, pin_confluence
from .replay_crt import inspect_crt
from .replay_hour import inspect_hour
from .replay_structure import inspect_structure_liquidity
from .replay_zones import inspect_zones
from .saved_history import chart_range, tick_partitions, read_tick_partitions, tick_source_manifests


@asynccontextmanager
async def lifespan(app:FastAPI):
    initialize()
    journal_materialize()
    app.state.smt_monitor = {}
    app.state.arrival_capture = CaptureManager()
    stop = asyncio.Event()
    monitor = asyncio.create_task(run_smt_monitor(stop, app.state.smt_monitor))
    history = asyncio.create_task(supervise_strategy_history(stop))
    try:
        yield
    finally:
        stop.set()
        await app.state.arrival_capture.stop()
        await monitor
        await history
app=FastAPI(title='TradeForge Local API',version='0.1.0',lifespan=lifespan)
from .plan_paper import router as paper_router
app.include_router(paper_router)
app.include_router(indicator_router)
app.include_router(arrival_router)
app.include_router(review_decision_router)
app.include_router(drawing_router)
app.include_router(workspace_router)
app.include_router(learning_router)
app.include_router(decision_review_router)
from .statement_routes import router as statement_router
app.include_router(statement_router)
app.include_router(month_router)
app.include_router(historical_package_router)
app.include_router(strategy_router)
app.include_router(strategy_evaluation_router)
app.include_router(historical_strategy_router)
app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:5173','http://localhost:5173','http://127.0.0.1:4180','http://localhost:4180'],allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['Content-Type'])


@app.get('/api/health')
def health():return {'status':'ok','mode':'LOCAL_SINGLE_USER','execution_enabled':False,'time_utc':utc_now()}


@app.get('/api/overview')
def overview():
    return {'analytics':analytics(), 'mt5':mt5_connection(),
      'datasets':rows('SELECT * FROM datasets ORDER BY created_at DESC LIMIT 5'),
      'recent_trades':list_trades()[:5], 'recent_reviews':rows('SELECT * FROM ai_reviews ORDER BY created_at DESC LIMIT 5'),
      'journal_count':one('SELECT count(*) AS n FROM journal_entries')['n']}


@app.get('/api/market/status')
def market_status():return {'mt5':mt5_connection(),'dukascopy':{'available':'MANUAL_IMPORT','notice':'Official S3 data can incur requester-pays charges.'},'demo':True}


@app.get('/api/market/candles')
def market_candles(symbol:str='EURUSD',timeframe:str='M5',source:str='DEMO',limit:int=Query(180,ge=10,le=1000),
                   include_latest:bool=False,start:int|None=None,end:int|None=None):
    if (start is None) != (end is None) or (start is not None and source not in ("MT5_SAVED", "DUKASCOPY_SAVED")):
        raise HTTPException(422, "Provide both start and end UTC epoch seconds for a saved source")
    if source == "BINANCE_SPOT":
        if symbol != "BTCUSDT" or timeframe not in binance.INTERVALS:
            raise HTTPException(422, "Binance Spot supports BTCUSDT and the standard chart timeframes")
        try:
            candles = binance.historical_candles(timeframe, limit)
        except (httpx.HTTPError, ValueError) as error:
            raise HTTPException(503, "Binance Spot candle history unavailable") from error
        return {"source": source, "synthetic": False, "price_basis": "SPOT_TRADE_OHLC", "candles": candles}
    if timeframe not in FRAME_MIN or (source != 'MT5' and symbol not in BASE):
        raise HTTPException(422,'Unsupported instrument or timeframe')
    if source=='DEMO': return {'source':'DEMO','synthetic':True,'price_basis':'SIMULATED','feed_id':'demo-v1','candles':demo_candles(symbol,timeframe,limit)}
    if source=='MT5':
        provenance = {}
        try: candles=mt5_rates(symbol,timeframe,limit,include_latest=include_latest,provenance=provenance)
        except (RuntimeError,ValueError) as ex:raise HTTPException(503,str(ex)) from ex
        return {'source':'MT5','synthetic':False,'price_basis':'BROKER_BARS_UNSPECIFIED','candles':candles,
                'latest_unconfirmed':include_latest and bool(candles), **provenance}
    if source in ('MT5_SAVED','DUKASCOPY_SAVED'):
        provider=source.split('_')[0]
        try:
            step = FRAME_MIN[timeframe] * 60
            result = chart_range(provider, symbol, timeframe, start, end, step, limit)
        except FileNotFoundError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, RuntimeError, OSError) as error:
            raise HTTPException(409, str(error)) from error
        return {"source":source, "synthetic":False, "source_completeness":"UNVERIFIED", **result}
    raise HTTPException(422,'Unsupported source')


@app.get('/api/market/quote')
def quote(symbol:str='EURUSD',source:str='DEMO'):
    if source != 'MT5' and symbol not in BASE:raise HTTPException(422,'Unsupported symbol')
    if source=='DEMO':
        b=demo_candles(symbol,'M5',30)[-1]
        return {'source':'DEMO','synthetic':True,'symbol':symbol,'bid':b['close'],
          'ask':round(b['close']+(0.0001 if symbol in ('EURUSD','GBPUSD') else 0.01 if symbol=='USDJPY' else .2),5),
          'as_of':datetime.fromtimestamp(b['time'],timezone.utc).isoformat(), 'stale':True}
    if source!='MT5':raise HTTPException(422,'Unsupported source')
    try:return mt5_quote(symbol)
    except (RuntimeError,ValueError) as ex:raise HTTPException(503,str(ex)) from ex


@app.get('/api/market/mt5/symbols')
def broker_symbols():
    try:
        return mt5_symbols()
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error


@app.get('/api/trades')
def trades():return list_trades()


@app.post('/api/trades',status_code=201)
def create(plan:TradePlan):return create_trade(plan)


@app.get('/api/trades/{uid}')
def detail(uid:str):return get_trade(uid)


@app.post('/api/trades/{uid}/revisions')
def update_plan(uid:str,plan:TradePlan,expected_version:int|None=Query(None,ge=1)):
    return revise(uid,plan,expected_version)


class NoTrade(BaseModel):
    reason:str = Field(min_length=5,max_length=5000)


@app.post('/api/trades/{uid}/decline')
def no_trade(uid:str,decision:NoTrade):
    if len(decision.reason.strip())<5:
        raise HTTPException(422,"Provide the reason for passing on this trade")
    return decline_trade(uid,decision.reason.strip())


@app.post('/api/trades/{uid}/close')
def record_outcome(uid:str,outcome:Outcome):return close_trade(uid,outcome)


@app.post('/api/risk/evaluate')
def risk(inp:RiskInput):return evaluate(inp.plan)


@app.get('/api/analytics')
def stats():return analytics()


@app.post('/api/ai/exports/{uid}',status_code=201)
def create_export(uid:str, plan_version:int|None=Query(None,ge=1)):return export_ai(uid,plan_version)


@app.get('/api/ai/packages')
def list_packages():return rows('SELECT id,trade_id,plan_version,knowledge_cutoff,sha256,created_at FROM ai_packages WHERE trade_id IS NOT NULL ORDER BY created_at DESC')


@app.get('/api/ai/packages/{pkg}/download')
def download_ai(pkg:str):
    p=one('SELECT * FROM ai_packages WHERE id=?',(pkg,))
    if not p:raise HTTPException(404,'Package not found')
    payload=domain.read_package_bytes(p,missing_status=404,missing_detail="Package file missing",
                                      checksum_detail="Export checksum mismatch")
    return Response(payload,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{pkg}.zip"'})


review_upload = APIRouter(route_class=ReviewUploadRoute)


@review_upload.post('/api/ai/reviews',status_code=201)
async def import_review(review: HistoricalReview | ReviewV1 | ReviewInput, request: Request):
    original = await request.body()
    importer = import_historical if isinstance(review, HistoricalReview) else import_ai
    return await asyncio.to_thread(importer, review, original)


app.include_router(review_upload)


@app.get('/api/ai/reviews/{review_id}/original')
def download_review_original(review_id: str):
    row = one("SELECT body,sha256 FROM ai_review_originals WHERE review_id=?", (review_id,))
    if row is None:
        raise HTTPException(404, "Original AI review response unavailable")
    if sha256(row["body"]).hexdigest() != row["sha256"]:
        raise HTTPException(409, "Original AI review checksum mismatch")
    return Response(row["body"], media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="ai-review.json"'})


@app.get('/api/ai/reviews')
def reviews():
    return rows("""SELECT id,package_id,assessment,verification,created_at,
        COALESCE(i.status,'ORIGINAL_UNAVAILABLE') AS original_integrity,i.duplicate_key,i.corrects_review_id,
        EXISTS(SELECT 1 FROM recorded_package_sources src WHERE src.package_id=ai_reviews.package_id) AS recorded_package,
        EXISTS(SELECT 1 FROM ai_review_originals WHERE review_id=ai_reviews.id) AS has_original
        FROM ai_reviews LEFT JOIN ai_review_integrity i ON i.review_id=ai_reviews.id ORDER BY ai_reviews.rowid DESC""")


@app.get('/api/journal')
def journal():return journal_list()


@app.post('/api/journal',status_code=201)
def new_journal(entry:JournalInput):return journal_add(entry)


@app.get('/api/journal/download')
def journal_download():
    return FileResponse(domain.JOURNAL_FILE,media_type='text/markdown',filename='Forex Intraday Trading - Master Learning Journal.md')


@app.get('/api/datasets')
def datasets():return rows('SELECT * FROM datasets ORDER BY created_at DESC LIMIT 500')


@app.get('/api/datasets/{dataset_id}/coverage')
def dataset_coverage(dataset_id:str):
    dataset = one("SELECT * FROM datasets WHERE id=? AND kind='candles'", (dataset_id,))
    if not dataset:
        raise HTTPException(404, "Candle partition unavailable")
    try:
        step = FRAME_MIN[dataset["timeframe"]] * 60
        values = read_partition(dataset)
        first = int(datetime.fromisoformat(dataset["first_time"]).timestamp())
        end = int(datetime.fromisoformat(dataset["last_time"]).timestamp()) + step
        if not values:
            raise ValueError("Candle partition is empty")
        gaps, duplicates = [], 0
        truncated = dataset["rows_count"] > len(values)
        cursor = values[0]["time"] if truncated else first
        previous = None
        for candle in values:
            timestamp = candle["time"]
            if timestamp % step or not first <= timestamp < end or timestamp < cursor - step:
                raise ValueError("Candle partition timestamps do not match its registered interval")
            if timestamp == cursor - step:
                if previous != candle:
                    raise ValueError("Conflicting duplicate candles")
                duplicates += 1
                continue
            if timestamp > cursor:
                gaps.append({"start": cursor, "end": timestamp})
            cursor = timestamp + step
            previous = candle
        if cursor < end:
            gaps.append({"start": cursor, "end": end})
        return {"source":dataset["provider"]+"_SAVED","synthetic":False,"symbol":dataset["symbol"],
                "timeframe":dataset["timeframe"],"feed_id":dataset["feed_id"],"price_basis":dataset["price_basis"],
                "candles":values[-1:], "origin":candle_origin(dataset, values, step), "partitions":[{"id":dataset["id"],"sha256":dataset["sha256"]}],
                "coverage":{"start":first,"end":end,"gaps":gaps,"duplicate_bars":duplicates,
                            "truncated":truncated}}
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        raise HTTPException(409, f"UNTRUSTED: {error}") from error


class MT5Import(BaseModel):
    symbol:str='EURUSD'
    timeframe:str='M5'
    limit:int=Field(300,ge=10,le=2000)


@app.post('/api/market/import/mt5')
def import_mt5(inp:MT5Import):
    try:
        provenance = {}
        rates=mt5_rates(inp.symbol,inp.timeframe,inp.limit,provenance=provenance)
        if not rates: raise RuntimeError('No completed MT5 bars were returned')
        if not provenance.get("feed_id"): raise RuntimeError("MT5 feed and price basis are unavailable")
        feed='mt5-'+sha256(provenance["feed_id"].encode()).hexdigest()[:12]
        return write_partition('MT5',feed,inp.symbol,'candles',inp.timeframe,provenance["price_basis"],rates)
    except (RuntimeError,ValueError,ImportError) as ex:raise HTTPException(503,str(ex)) from ex


@app.post('/api/market/import/bi5')
async def import_dukascopy_bi5(symbol:str,day:str,hour:int|None=Query(None,ge=0,le=23),file:UploadFile=File(...)):
    try:
        if symbol not in BASE:raise ValueError('Unsupported symbol')
        date=datetime.strptime(day,'%Y-%m-%d').replace(tzinfo=timezone.utc)
        start = date + timedelta(hours=hour or 0)
        end = start + (timedelta(hours=1) if hour is not None else timedelta(days=1))
        if end > datetime.now(timezone.utc):
            raise ValueError("Select a completed UTC hour or day")
        payload=await file.read(40_000_001)
        return await asyncio.to_thread(import_bi5,payload,symbol,date,hour)
    except (RuntimeError,ValueError,ImportError) as ex:raise HTTPException(422,str(ex)) from ex


@app.post('/api/market/import/reviewed')
async def import_reviewed_archive(file: UploadFile = File(...)):
    payload = await file.read(1_048_577)
    if len(payload) > 1_048_576:
        raise HTTPException(413, "Reviewed archive files must fit within 1 MiB")
    try:
        return await asyncio.to_thread(import_reviewed, payload)
    except OSError as error:
        if error.errno == errno.ENOSPC:
            raise HTTPException(507, "Insufficient storage to publish the archive") from error
        raise
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.post('/api/market/import/mt5-deals')
def import_deals(days:int=Query(30,ge=1,le=365)):
    try:deals=mt5_recent_deals(days)
    except RuntimeError as ex:raise HTTPException(503,str(ex)) from ex
    imported=0
    with transaction() as db:
        for deal in deals:
            cur=db.execute('INSERT OR IGNORE INTO imported_fills(id,broker_key,deal_id,symbol,payload,imported_at) VALUES(?,?,?,?,?,?)',
              (str(uuid.uuid4()),deal['broker_key'],deal['deal_id'],deal['symbol'],json.dumps(deal['payload']),utc_now()))
            imported+=cur.rowcount
    return {'imported':imported,'seen':len(deals),'note':'Raw broker deals imported, not yet grouped or financially reconciled'}


@app.get('/api/market/imported-deals')
def imported_deals():return rows('SELECT id,symbol,imported_at FROM imported_fills ORDER BY imported_at DESC LIMIT 200')


@app.get('/api/backtest')
def backtest(symbol:str='EURUSD',timeframe:str='M5',source:str='DEMO',target_r:float=Query(2.0,ge=1,le=5),
             start:int|None=None,end:int|None=None,tick_dataset_id:str|None=None, tick_archive_range:bool=False,
             slippage_bps:Decimal=Query(Decimal(0),ge=0,le=100),
             commission_bps:Decimal=Query(Decimal(0),ge=0,le=100),
             max_quote_gap_seconds:int=Query(60,ge=1,le=60)):
    return calculate_backtest(BacktestRequest(
        symbol=symbol,timeframe=timeframe,source=source,target_r=target_r,start=start,end=end,
        tick_dataset_id=tick_dataset_id,tick_archive_range=tick_archive_range,slippage_bps=slippage_bps,commission_bps=commission_bps,
        max_quote_gap_seconds=max_quote_gap_seconds,
    ))["result"]


class BacktestRequest(BaseModel):
    symbol: str = Field(default="EURUSD", max_length=64)
    timeframe: str = Field(default="M5", max_length=4)
    source: str = Field(default="DEMO", max_length=30)
    target_r: float = Field(default=2, ge=1, le=5)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    tick_dataset_id: str | None = Field(default=None,max_length=100)
    tick_archive_range: bool = False
    slippage_bps: Decimal = Field(default=Decimal(0),ge=0,le=100)
    commission_bps: Decimal = Field(default=Decimal(0),ge=0,le=100)
    max_quote_gap_seconds: int = Field(default=60,ge=1,le=60)


def calculate_backtest(request: BacktestRequest):
    symbol,timeframe,source = request.symbol,request.timeframe,request.source
    start,end,target_r = request.start,request.end,request.target_r
    tick_dataset_id = request.tick_dataset_id
    if request.tick_archive_range and (tick_dataset_id is not None or not source.endswith("_SAVED")):
        raise HTTPException(422, "Choose either a single tick partition or matching saved range ticks")
    tick_enabled = tick_dataset_id is not None or request.tick_archive_range
    m=market_candles(symbol,timeframe,source,limit=700,start=start,end=end)
    coverage = m.get("coverage")
    if coverage and ((not tick_enabled and coverage["gaps"])
                     or (start is not None and coverage["truncated"])):
        raise HTTPException(422, "Select a complete archive range of at most 700 candles for this backtest")
    try:
        if not tick_enabled:
            result=breakout_backtest(m['candles'],target_r)
        else:
            if m["price_basis"] != "BID":
                raise ValueError("Tick replay requires saved bid candles")
            step = FRAME_MIN[timeframe] * 60
            if not m["candles"]:
                raise ValueError("No saved bid candles in the selected range")
            period = effective_tick_period(m)
            if request.tick_archive_range:
                datasets = tick_partitions(source.removesuffix("_SAVED"), symbol, m["feed_id"],
                                           *period)
            else:
                dataset = one("SELECT * FROM datasets WHERE id=?", (tick_dataset_id,))
                if (not dataset or dataset["kind"] != "ticks" or dataset["symbol"] != symbol
                        or dataset["provider"] + "_SAVED" != source or dataset["feed_id"] != m.get("feed_id")
                        or dataset["price_basis"] != "BID_ASK_UNITS"):
                    raise ValueError("Choose saved bid candles and a bid/ask tick partition from the same feed and symbol")
                datasets = [dataset]
            manifests = tick_source_manifests(datasets)
            ticks = read_tick_partitions(datasets, source_manifests=manifests, execution_only=True, period=period)
            validate_bid_extremes(m["candles"], ticks, step)
            audit = replay_coverage(m, ticks, source.removesuffix("_SAVED"), symbol, step, request.max_quote_gap_seconds)
            result = replay_breakout(m["candles"], ticks, step, target_r,
                                     slip_bps=request.slippage_bps, commission_bps=request.commission_bps,
                                     max_quote_gap_seconds=request.max_quote_gap_seconds, coverage=audit)
            result["replay_coverage"] = audit
            result["execution_model"].update(version=3, ordering="UTC_MS_SOURCE_ORDINAL_V2")
            if coverage and coverage["gaps"]:
                result["candle_gaps"] = coverage["gaps"]
            tick_lineage = [{"id": part["id"], "sha256": part["sha256"]} for part in datasets]
            result["lineage"] = {"candle_partitions": m["partitions"], "tick_partitions": tick_lineage,
                                 "source_manifests": list(manifests.values())}
            if tick_dataset_id is not None:
                result["lineage"]["tick_partition"] = tick_lineage[0]
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        raise HTTPException(422, str(error)) from error
    result.update({'source':source,'synthetic':m['synthetic'],'symbol':symbol,'timeframe':timeframe})
    return {"schema_version":1,"engine_version":1,"parameters":request.model_dump(mode="json"),
            "market":m,"result":result}


def run_summary(row: dict) -> dict:
    return {"id": row["id"],"created_at":row["created_at"],**json.loads(row["summary"])}


@app.post('/api/backtest/runs',status_code=201)
def save_backtest(request:BacktestRequest):
    snapshot = calculate_backtest(request)
    encoded = json.dumps(snapshot,sort_keys=True,separators=(",",":"),allow_nan=False)
    run_id = sha256(encoded.encode()).hexdigest()
    result = snapshot["result"]
    summary = {key:result[key] for key in ("count","expectancy_r","source","timeframe","symbol","synthetic")}
    summary["model"] = "TICK" if "execution_model" in result else "OHLC"
    with transaction() as db:
        db.execute("INSERT INTO backtest_runs VALUES (?,?,?,?) ON CONFLICT(id) DO NOTHING",
                   (run_id,json.dumps(summary),encoded,utc_now()))
    return load_backtest(run_id)


@app.get('/api/backtest/runs')
def backtest_runs(limit:int=Query(40,ge=1,le=100)):
    return [run_summary(row) for row in rows("SELECT id,summary,created_at FROM backtest_runs ORDER BY created_at DESC,id LIMIT ?",(limit,))]


@app.get('/api/backtest/runs/{run_id}')
def load_backtest(run_id:str):
    row = one("SELECT * FROM backtest_runs WHERE id=?", (run_id,))
    if row is None:
        raise HTTPException(404,"Backtest run unavailable")
    if sha256(row["payload"].encode()).hexdigest() != run_id:
        raise HTTPException(409,"Saved run checksum mismatch")
    return {"id":run_id,"created_at":row["created_at"],**json.loads(row["payload"])}


@app.get('/api/backtest/runs/{run_id}/trades/{trade_index}/quotes')
def run_quotes(run_id:str,trade_index:int,response:Response,offset:int=Query(0,ge=0,le=500_000),limit:int=Query(250,ge=1,le=2000)):
    snapshot = load_backtest(run_id)
    result = snapshot["result"]
    if not 0<=trade_index<len(result["trades"]):
        raise HTTPException(404,"Trade not found in run")
    if "execution_model" not in result:
        raise HTTPException(409,"This OHLC run has no recorded quote path")
    trade = result["trades"][trade_index]
    lineage = result["lineage"]
    partitions = lineage.get("tick_partitions") or [lineage["tick_partition"]]
    datasets = []
    for partition in partitions:
        row = one("SELECT * FROM datasets WHERE id=? AND kind='ticks'", (partition["id"],))
        if row is None or row["sha256"] != partition["sha256"]:
            raise HTTPException(409,"Original quote partition unavailable")
        datasets.append(row)
    try:
        if result["execution_model"].get("version") == 2:
            quotes = read_v2_quote_evidence(datasets)
            response.headers["X-TradeForge-Quote-Order"] = "historical-v2"
        else:
            period = result.get("replay_coverage", {}).get("required_period")
            quotes = read_tick_partitions(datasets, period=period)
    except (RuntimeError,OSError,ValueError,KeyError) as error:
        raise HTTPException(409,f"Original quote partitions failed verification: {error}") from error
    start = (trade["entry_time_ms"],trade["entry_source_order"])
    end = (trade["exit_time_ms"],trade["exit_source_order"])
    path = sorted((q for q in quotes if start <= (q["time_ms"],q["source_order"]) <= end),
                  key=lambda quote:(quote["time_ms"],quote["source_order"]))
    return {"items":path[offset:offset+limit],"total":len(path),
            "next_offset":offset+limit if offset+limit < len(path) else None}


class ReplayView(BaseModel):
    view: str = Field(pattern=r"^(comparison|[234])$")
    symbol: str = Field(min_length=1,max_length=64)
    timeframe: str = Field(min_length=2,max_length=4)


class ReplayRequest(BaseModel):
    symbol: str = Field(min_length=1,max_length=64)
    timeframe: str = Field(min_length=2,max_length=4)
    source: str = Field(max_length=30)
    start: int | None = Field(None,ge=0)
    end: int | None = Field(None,ge=0)
    views: list[ReplayView] = Field(default_factory=list,max_length=4)
    signal_reference: SignalReference | None = None
    @model_validator(mode="after")
    def paired_range(self):
        if (self.start is None) != (self.end is None) or self.start is not None and self.start>=self.end:
            raise ValueError("Replay range requires an ordered start and end")
        if len({view.view for view in self.views}) != len(self.views):
            raise ValueError("Duplicate replay view")
        return self


class ReplayCheckpoint(BaseModel):
    position:int = Field(ge=0,lt=2000)
    bookmarks:list[int] = Field(default_factory=list,max_length=50)


def captured_replay(request:ReplayRequest,symbol:str,timeframe:str,cutoff:int):
    if timeframe not in FRAME_MIN:
        raise HTTPException(422,"Unsupported replay timeframe")
    step = FRAME_MIN[timeframe]*60
    start,end=request.start,request.end
    if start is not None:
        start = start//step*step
        end = (end+step-1)//step*step
        end = min(end, cutoff//step*step)
        if end <= start:
            raise HTTPException(422,"No completed window for this replay timeframe")
    market=market_candles(symbol,timeframe,request.source,limit=600,include_latest=(request.source=="MT5"),
                          start=start,end=end)
    bars = []
    for bar in market["candles"]:
        available = replay_availability(bar, step)
        if available <= cutoff:
            bars.append({**bar, "available_at": available})
    if any(right["time"]<=left["time"] or right["available_at"]<left["available_at"]
           for left,right in zip(bars,bars[1:])):
        raise HTTPException(409,"Replay candle timestamps are inconsistent")
    return {**market,"candles":bars,"latest_unconfirmed":False,"symbol":symbol,"timeframe":timeframe}


@app.post('/api/replay/sessions',status_code=201)
def create_replay(request:ReplayRequest):
    clock = int(datetime.now(timezone.utc).timestamp())
    if request.source not in {"DEMO","MT5","MT5_SAVED","DUKASCOPY_SAVED"}:
        raise HTTPException(422,"Replay requires a supported closed-candle source")
    panes = {}
    if request.signal_reference:
        reference=request.signal_reference
        event=signal_revision(reference.event_key,reference.status)
        provenance=event["provenance"]
        primary,comparison=provenance["primary"],provenance["comparison"]
        if (request.symbol,request.timeframe,request.source)!=(primary["symbol"],provenance["timeframe"],primary["source"]):
            raise HTTPException(422,"Replay context differs from linked signal")
        if request.views or request.start is not None:
            raise HTTPException(422,"Recorded signal replay uses its original capture boundaries")
        captures=signal_candles(reference.event_key,reference.status)
        def pane(side:dict, bars:list[dict]):
            return {**side,"candles":bars,"timeframe":request.timeframe,"synthetic":False,"latest_unconfirmed":False}
        market=pane(primary,captures["primary"])
        panes["comparison"]=pane(comparison,captures["comparison"])
    else:
        market = captured_replay(request,request.symbol,request.timeframe,clock)
        if market.get("coverage",{}).get("truncated") and request.start is not None:
            raise HTTPException(422,"Choose a replay range of at most 600 primary candles")
        for view in request.views:
            try:
                panes[view.view] = captured_replay(request,view.symbol,view.timeframe,clock)
            except HTTPException as error:
                if error.status_code not in (404,409,422,503):
                    raise
                panes[view.view] = {"symbol":view.symbol,"timeframe":view.timeframe,"error":str(error.detail)}
    if not market["candles"]:
        raise HTTPException(422,"No completed replay candles")
    snapshot = {"schema_version":1,"request":request.model_dump(),"market":market,"views":panes}
    encoded=json.dumps(snapshot,sort_keys=True,separators=(",",":"),allow_nan=False)
    session_id=sha256(encoded.encode()).hexdigest()
    with transaction() as db:
        db.execute("INSERT INTO replay_sessions VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                   (session_id,request.symbol,request.timeframe,request.source,encoded,utc_now()))
    return replay_session(session_id)


@app.get('/api/replay/sessions')
def list_replays(symbol:str=Query(max_length=64),timeframe:str=Query(max_length=4),source:str=Query(max_length=30)):
    return rows("""SELECT session.id,session.created_at,checkpoint.position,checkpoint.updated_at FROM replay_sessions session
                 JOIN replay_checkpoints checkpoint ON checkpoint.session_id=session.id
                 WHERE session.symbol=? AND session.timeframe=? AND session.source=?
                 ORDER BY checkpoint.updated_at DESC,session.id LIMIT 20""",(symbol,timeframe,source))


@app.get('/api/replay/sessions/{session_id}')
def replay_session(session_id:str):
    row=one("SELECT * FROM replay_sessions WHERE id=?",(session_id,))
    if not row:
        raise HTTPException(404,"Replay session unavailable")
    if sha256(row["payload"].encode()).hexdigest()!=session_id:
        raise HTTPException(409,"Replay session checksum mismatch")
    checkpoint=one("SELECT position,bookmarks FROM replay_checkpoints WHERE session_id=?",(session_id,))
    return {"id":session_id,**json.loads(row["payload"]),
            "checkpoint":{"position":checkpoint["position"],"bookmarks":json.loads(checkpoint["bookmarks"])}
            if checkpoint else None}


@app.get('/api/replay/sessions/{session_id}/higher-timeframe')
def replay_higher_timeframe(session_id: str, hour_start: int = Query(ge=0), cutoff: int = Query(ge=0),
                            view: str = Query(default="primary", pattern=r"^(primary|comparison|[234])$")):
    return inspect_hour(replay_session(session_id), view, hour_start, cutoff)


@app.get('/api/replay/sessions/{session_id}/structure-liquidity')
def replay_structure_liquidity(session_id: str, cutoff: int = Query(ge=0),
                               view: str = Query(default="primary", pattern=r"^(primary|comparison|[234])$"),
                               event_id: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$"),
                               before: int | None = Query(default=None, ge=0)):
    return inspect_structure_liquidity(replay_session(session_id), view, cutoff, event_id, before)


@app.get('/api/replay/sessions/{session_id}/crt')
def replay_crt(session_id: str, cutoff: int = Query(ge=0),
               view: str = Query(default="primary", pattern=r"^(primary|comparison|[234])$"),
               range_id: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$"),
               before: int | None = Query(default=None, ge=0)):
    return inspect_crt(replay_session(session_id), view, cutoff, range_id, before)


@app.get('/api/replay/sessions/{session_id}/confluence')
def replay_confluence(session_id: str, cutoff: int = Query(ge=0),
                      profile: Literal["CRT_SMT_M5_V1"] = "CRT_SMT_M5_V1",
                      receipt_id: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$")):
    return inspect_confluence(replay_session(session_id), cutoff, profile, receipt_id)


@app.post('/api/replay/sessions/{session_id}/confluence')
def pin_replay_confluence(session_id: str, cutoff: int = Query(ge=0),
                          receipt_id: str = Query(pattern=r"^[a-f0-9]{64}$"),
                          profile: Literal["CRT_SMT_M5_V1"] = "CRT_SMT_M5_V1"):
    return pin_confluence(replay_session(session_id), cutoff, profile, receipt_id)


@app.get('/api/replay/sessions/{session_id}/zones')
def replay_zones(session_id: str, cutoff: int = Query(ge=0),
                 view: str = Query(default="primary", pattern=r"^(primary|comparison|[234])$"),
                 mitigation: Literal["wick", "close"] = "wick",
                 zone_id: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$"),
                 before: int | None = Query(default=None, ge=0)):
    return inspect_zones(replay_session(session_id), view, cutoff, mitigation, zone_id, before)


@app.post('/api/replay/sessions/{session_id}/checkpoint')
def checkpoint_replay(session_id:str,checkpoint:ReplayCheckpoint):
    session=replay_session(session_id)
    size=len(session["market"]["candles"])
    if checkpoint.position>=size or any(not 0<=index<size for index in checkpoint.bookmarks):
        raise HTTPException(422,"Replay position or bookmark is outside the frozen snapshot")
    with transaction() as db:
        db.execute("""INSERT INTO replay_checkpoints VALUES(?,?,?,?) ON CONFLICT(session_id) DO UPDATE
                      SET position=excluded.position,bookmarks=excluded.bookmarks,updated_at=excluded.updated_at""",
                   (session_id,checkpoint.position,json.dumps(sorted(set(checkpoint.bookmarks))),utc_now()))
    return {"position":checkpoint.position,"bookmarks":sorted(set(checkpoint.bookmarks))}
