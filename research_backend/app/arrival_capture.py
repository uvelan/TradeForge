"""Bounded read-only MT5 capture using the research adapter and immutable SQLite receipts."""
import asyncio
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
import sqlite3
from time import monotonic_ns, time_ns
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from .arrival_policy import CLOCK_POLICY, RECORDED_ARRIVAL, RECORDED_MODE, to_seconds
from .db import rows, transaction
from .indicator_coverage import MarketSchedule, open_intervals
from .indicators import Bar
from .market import FRAME_MIN, mt5_rates
from .storage import write_partition

LEASE_MS = 90_000
LOOKBACK = 32
MAX_SCOPES = 8
AGGREGATION_POLICY = "RECORDED_M1_TO_M5_V1"
SCOPE_FIELDS = ("provider", "feed_id", "symbol", "timeframe", "price_basis")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return sha256(canonical(value).encode()).hexdigest()


class CaptureConfig(BaseModel, extra="forbid"):
    symbols: list[str] = Field(default_factory=lambda: ["EURUSD", "GBPUSD"], min_length=1, max_length=MAX_SCOPES)
    timeframe: str = Field(pattern=r"^M[15]$")
    calendar: MarketSchedule = Field(default_factory=lambda: MarketSchedule(kind="forex_weekly"))
    poll_seconds: int = Field(default=5, ge=1, le=30)

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, symbols):
        if len({symbol.casefold() for symbol in symbols}) != len(symbols) or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._#+-]{0,31}", symbol) for symbol in symbols
        ):
            raise ValueError("Use distinct supported MT5 symbols")
        return symbols


class CaptureClock:
    def __init__(self, wall=time_ns, monotonic=monotonic_ns):
        self.wall = wall
        self.monotonic = monotonic
        self.anchor = None
        self.origin = monotonic()

    def sample(self):
        wall, mono = self.wall(), self.monotonic()
        if self.anchor is not None:
            before, elapsed = self.anchor
            if wall < before or mono < elapsed or abs((wall-before) - (mono-elapsed)) > 2_000_000_000:
                raise ValueError("CLOCK_DISCONTINUITY")
        self.anchor = wall, mono
        return wall//1_000_000, (mono-self.origin)//1_000_000


@dataclass(slots=True)
class Worker:
    config: CaptureConfig
    sessions: dict[str, str]
    started_ms: int
    clock: CaptureClock
    scopes: dict = field(default_factory=dict)
    task: asyncio.Task | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    state: str = "STARTING"
    warning: str | None = None


def claim(config, started):
    sessions = {symbol: uuid4().hex for symbol in config.symbols}
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM arrival_leases WHERE expires_ms<=?", (started,))
        if db.execute("SELECT COUNT(*) FROM arrival_leases").fetchone()[0] + len(sessions) > MAX_SCOPES:
            raise ValueError("Capture scope limit reached")
        for symbol,id_ in sessions.items():
            if db.execute("SELECT 1 FROM arrival_leases WHERE symbol=? COLLATE NOCASE AND timeframe=?",
                          (symbol, config.timeframe)).fetchone():
                raise ValueError("Capture already active for this symbol and timeframe")
            db.execute("INSERT INTO arrival_sessions VALUES(?,?,?,?,?,?,?)",
                       (id_, "MT5", symbol, config.timeframe, RECORDED_ARRIVAL, started,
                        canonical(config.calendar.model_dump())))
            db.execute("INSERT INTO arrival_leases VALUES(?,?,?,?)",
                       (symbol, config.timeframe, id_, started+LEASE_MS))
    return sessions


def renew(db, session, stamp):
    lease = db.execute("UPDATE arrival_leases SET expires_ms=? WHERE session_id=? AND expires_ms>?",
                       (stamp+LEASE_MS, session, stamp))
    if lease.rowcount != 1:
        raise ValueError("CAPTURE_LEASE_LOST")


def heartbeat(sessions, wall):
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        for id_ in sessions:
            renew(db,id_,wall)


def release(sessions):
    with transaction() as db:
        db.executemany("DELETE FROM arrival_leases WHERE session_id=?",[(id_,) for id_ in sessions])


def observe(worker, provider, symbol):
    config = worker.config
    session = worker.sessions[symbol]
    step = FRAME_MIN[config.timeframe]*60
    metadata = {}
    incoming = provider(symbol, config.timeframe, LOOKBACK, include_latest=False, provenance=metadata)
    observed, elapsed = worker.clock.sample()
    raw_feed, basis = metadata.get("feed_id"), metadata.get("price_basis")
    if not isinstance(raw_feed, str) or not raw_feed or basis not in {"BID", "LAST_TRADE"}:
        raise ValueError("UNVERIFIED_FEED_IDENTITY")
    feed = "capture-" + sha256(raw_feed.encode()).hexdigest()[:40]
    scope = ("MT5", feed, symbol, config.timeframe, basis)
    prior = worker.scopes.get(symbol, {})
    if prior.get("feed_id") and (feed, basis) != (prior["feed_id"], prior["price_basis"]):
        raise ValueError("FEED_IDENTITY_CHANGED")
    if len(incoming) > LOOKBACK or len({row["time"] for row in incoming}) != len(incoming):
        raise ValueError("INVALID_PROVIDER_BATCH")
    if config.calendar.kind == "unknown":
        raise ValueError("UNKNOWN_MARKET_CALENDAR")

    valid = []
    for raw in sorted(incoming, key=lambda row: row["time"]):
        bar = Bar.model_validate(raw)
        volume = raw.get("volume")
        if (type(raw["time"]) is not int or bar.time % step or raw.get("closed", True) is not True
                or type(volume) is not int or volume < 0):
            raise ValueError("INVALID_PROVIDER_CANDLE")
        if (bar.time+step)*1000 > observed:
            raise ValueError("PROVIDER_NOMINAL_CLOSE_IN_FUTURE")
        if open_intervals(bar.time, bar.time+step, config.calendar) != [(bar.time,bar.time+step)]:
            raise ValueError("PROVIDER_OUTSIDE_CALENDAR")
        content = {**bar.model_dump(), "volume": volume,
                   "source_observed_ms": raw.get("source_observed_ms")}
        digest = fingerprint({key: content[key] for key in ("time", "open", "high", "low", "close", "volume")})
        valid.append((bar.time, content, digest))

    pending = []
    writes = duplicates = 0
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        renew(db, session, observed)
        for stamp, content, digest in valid:
            latest = db.execute("""SELECT o.sequence, o.fingerprint, o.observed_ms, r.persisted_ms
                FROM arrival_observations o LEFT JOIN arrival_receipts r USING(sequence)
                WHERE o.provider=? AND o.feed_id=? AND o.symbol=? AND o.timeframe=? AND o.price_basis=?
                AND o.candle_open=? ORDER BY o.sequence DESC LIMIT 1""", (*scope,stamp)).fetchone()
            nominal = stamp+step
            if latest is None and nominal*1000 <= worker.started_ms:
                continue
            if latest and observed < max(latest["observed_ms"], latest["persisted_ms"] or 0):
                raise ValueError("CLOCK_PRECEDES_PRIOR_OBSERVATION")
            if latest and latest["persisted_ms"] is None:
                pending.append(latest["sequence"])
            if latest and latest["fingerprint"] == digest:
                duplicates += 1
                continue
            recovery = int(observed-nominal*1000 > config.poll_seconds*2000 or prior.get("state") == "DISCONNECTED")
            pending.append(db.execute("""INSERT INTO arrival_observations(
                session_id,provider,feed_id,symbol,timeframe,price_basis,candle_open,nominal_close,
                observed_ms,elapsed_ms,content,fingerprint,previous_sequence,recovery)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (session,*scope,stamp,nominal,observed,elapsed,
                canonical(content),digest,latest["sequence"] if latest else None,recovery)).lastrowid)
            writes += 1

    persisted = None
    if pending:
        persisted, _ = worker.clock.sample()
        with transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            renew(db, session, persisted)
            db.executemany("INSERT OR IGNORE INTO arrival_receipts VALUES(?,?)",
                           [(sequence, persisted) for sequence in pending])

    # Coverage status is bounded to the polling lookback. A separate frozen
    # assessment verifies any larger research window.
    end = observed // (step*1000)*step
    start = max(end-LOOKBACK*step, worker.started_ms//(step*1000)*step)
    present = {item["candle_open"] for item in rows("""SELECT DISTINCT o.candle_open
        FROM arrival_observations o JOIN arrival_receipts r USING(sequence)
        WHERE o.provider=? AND o.feed_id=? AND o.symbol=? AND o.timeframe=? AND o.price_basis=?
          AND o.candle_open>=? AND o.candle_open<?""", (*scope,start,end))}
    intervals = open_intervals(start,end,config.calendar)
    if intervals is None or any(left % step or right % step for left,right in intervals):
        raise ValueError("UNKNOWN_OR_UNALIGNED_CALENDAR")
    missing = [stamp for left,right in intervals for stamp in range(left,right,step)
               if stamp not in present and (stamp+step)*1000+config.poll_seconds*2000 <= observed]
    return {"state": "DEGRADED" if missing else "RUNNING", "feed_id": feed, "price_basis": basis,
            "last_observed_ms": observed, "last_persisted_ms": persisted or prior.get("last_persisted_ms"),
            "last_candle": max(present, default=prior.get("last_candle")),
            "missing_slots": missing, "coverage_start": start, "coverage_end": end,
            "recorded": len(present), "duplicates": duplicates, "writes": writes}


class CaptureManager:
    def __init__(self, provider=mt5_rates, clock_factory=CaptureClock):
        self.provider = provider
        self.clock_factory = clock_factory
        self.worker = None
        self.lock = asyncio.Lock()

    def status(self):
        worker = self.worker
        if worker is None:
            return {"state":"STOPPED", "scopes": {}}
        return {"state":worker.state, "timeframe":worker.config.timeframe, "started_ms":worker.started_ms,
                "clock_policy":CLOCK_POLICY, "calendar":worker.config.calendar.model_dump(), "warning":worker.warning,
                "scopes":{symbol:{"session_id":id_, **worker.scopes.get(symbol, {})}
                          for symbol,id_ in worker.sessions.items()}, "queue_depth":0}

    async def start(self, config):
        async with self.lock:
            if self.worker and self.worker.task and not self.worker.task.done():
                if self.worker.config == config and not self.worker.stop.is_set():
                    return self.status()
                raise ValueError("Stop the existing capture session before reconfiguring")
            clock = self.clock_factory()
            started, _ = clock.sample()
            sessions = await asyncio.to_thread(claim,config,started)
            worker = self.worker = Worker(config,sessions,started,clock)
            worker.task = asyncio.create_task(self.run(worker))
            return self.status()

    async def run(self, worker):
        retries = 0
        try:
            while not worker.stop.is_set():
                for symbol in worker.sessions:
                    if worker.stop.is_set():
                        break
                    try:
                        worker.scopes[symbol] = await asyncio.to_thread(observe,worker,self.provider,symbol)
                    except RuntimeError:
                        worker.scopes[symbol] = {**worker.scopes.get(symbol,{}),"state":"DISCONNECTED",
                                                 "warning":"MT5_READ_OR_STORAGE_UNAVAILABLE"}
                    except (ValueError, OSError, sqlite3.Error) as error:
                        worker.scopes[symbol] = {**worker.scopes.get(symbol,{}),"state":"DEGRADED",
                                                 "warning":str(error) if isinstance(error,ValueError) else "STORAGE_UNAVAILABLE"}
                        if str(error) in {"CLOCK_DISCONTINUITY","CLOCK_PRECEDES_PRIOR_OBSERVATION",
                                          "CAPTURE_LEASE_LOST","FEED_IDENTITY_CHANGED"}:
                            worker.state = "FAILED"
                            return
                statuses = {state["state"] for state in worker.scopes.values()}
                worker.state = ("DISCONNECTED" if statuses == {"DISCONNECTED"} else
                                "RUNNING" if statuses == {"RUNNING"} else "DEGRADED")
                retries = min(retries+1, 3) if "DISCONNECTED" in statuses else 0
                # Heartbeat is independent of successful broker acquisition.
                wall, _ = worker.clock.sample()
                await asyncio.to_thread(heartbeat,tuple(worker.sessions.values()),wall)
                try:
                    await asyncio.wait_for(worker.stop.wait(),timeout=min(30,worker.config.poll_seconds*2**retries))
                except TimeoutError:
                    continue
        except Exception:
            worker.state = "FAILED"
            worker.warning = "CAPTURE_WORKER_FAILED"
        finally:
            await asyncio.to_thread(release,tuple(worker.sessions.values()))
            if worker.state != "FAILED":
                worker.state = "STOPPED"

    async def stop(self):
        async with self.lock:
            if self.worker and self.worker.task and not self.worker.task.done():
                self.worker.state = "STOPPING"
                self.worker.stop.set()
                try:
                    await asyncio.wait_for(asyncio.shield(self.worker.task),timeout=5)
                except TimeoutError:
                    self.worker.warning = "WAITING_FOR_PROVIDER_OR_STORAGE"
            return self.status()


def seal(scope, start, end, cutoff, calendar):
    step = FRAME_MIN[scope["timeframe"]]*60
    if start < 0 or start % step or end % step or not 0 < end-start <= 10_000*step:
        raise ValueError("Use a bounded aligned UTC range")
    selected = rows("""SELECT o.*,r.persisted_ms,s.calendar FROM arrival_observations o
        JOIN arrival_receipts r USING(sequence) JOIN arrival_sessions s ON s.id=o.session_id
        WHERE o.provider=? AND o.feed_id=? AND o.symbol=? AND o.timeframe=? AND o.price_basis=?
          AND candle_open>=? AND candle_open<? AND persisted_ms<=? ORDER BY candle_open,sequence LIMIT 30001""",
        (*(scope[key] for key in SCOPE_FIELDS),start,end,cutoff*1000))
    if not selected or len(selected)>30_000:
        raise ValueError("No bounded recorded evidence for the requested scope")
    calendar_data = calendar.model_dump()
    if any(json.loads(row["calendar"]) != calendar_data for row in selected):
        raise ValueError("Recorded sessions use a different calendar")
    partitions = []
    registrations = []
    for offset in range(0,len(selected),2000):
        chunk = selected[offset:offset+2000]
        members = [{**json.loads(row["content"]), "closed":True, "availability_policy":RECORDED_ARRIVAL,
                    "clock_quality":CLOCK_POLICY, "available_at":to_seconds(row["persisted_ms"]),
                    **{key:row[key] for key in ("session_id","sequence","previous_sequence","nominal_close",
                        "observed_ms","elapsed_ms","persisted_ms","fingerprint","recovery")}} for row in chunk]
        part = write_partition(scope["provider"],scope["feed_id"],scope["symbol"],"arrival_candles",
                               scope["timeframe"],scope["price_basis"],members)
        entry = {"id":part["id"],"sha256":part["sha256"]}
        partitions.append(entry)
        registrations.append((part["id"],part["sha256"],RECORDED_ARRIVAL,canonical(scope),
                              canonical(sorted({row["session_id"] for row in chunk}))))
    manifest = {**scope,"start":start,"end":end,"cutoff":cutoff,"partitions":partitions,
                "calendar":calendar_data,"availability_mode":RECORDED_MODE,
                "availability_policy":RECORDED_ARRIVAL,"clock_policy":CLOCK_POLICY,
                "sessions":sorted({row["session_id"] for row in selected}),
                "data_quality":{"observations":len(selected),"corrections":sum(row["previous_sequence"] is not None for row in selected)}}
    if scope["feed_id"].startswith("aggregate-"):
        manifest["aggregation_policy"] = AGGREGATION_POLICY
    manifest_id = fingerprint(manifest)
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        db.executemany("INSERT OR IGNORE INTO arrival_partitions VALUES(?,?,?,?,?)", registrations)
        db.execute("INSERT OR IGNORE INTO arrival_manifests VALUES(?,?)",(manifest_id,canonical(manifest)))
    return {"id":manifest_id,**manifest}


def aggregate_m1(manifest, clock=None):
    """Publish complete recorded M5 aggregates as a separately identified feed."""
    if manifest["timeframe"] != "M1" or manifest["provider"] != "MT5":
        raise ValueError("Recorded aggregation requires an MT5 M1 manifest")
    start, end = manifest["start"], manifest["end"]
    if start % 300 or end % 300:
        raise ValueError("Aggregation window must align to UTC M5 boundaries")
    clock = clock or CaptureClock()
    observed, elapsed = clock.sample()
    if observed < manifest["cutoff"]*1000:
        raise ValueError("Aggregation clock precedes the source cutoff")
    # Verify every byte in the frozen manifest and use only its admitted
    # observations, even if a new correction was captured after sealing.
    from .db import one
    from .indicator_history import load_reference
    from .smt_causal import ArchiveRequest
    window = ArchiveRequest(source="MT5_SAVED", symbol=manifest["symbol"], timeframe="M1",
        feed_id=manifest["feed_id"], price_basis=manifest["price_basis"], calendar=manifest["calendar"],
        partitions=manifest["partitions"], arrival_manifest=manifest["id"], start=start, end=end,
        cutoff=manifest["cutoff"], availability_mode=RECORDED_MODE)
    admitted = load_reference(window,start,end,60)
    if admitted["evidence"].get("status"):
        raise ValueError("Source manifest failed recorded verification")
    visible = {bar.time for bar,_ in admitted["rows"]}
    raw = {}
    from .storage import read_partition
    for expected in manifest["partitions"]:
        part = one("SELECT * FROM datasets WHERE id=?",(expected["id"],))
        if not part or part["sha256"] != expected["sha256"]:
            raise ValueError("Source manifest partition changed")
        for row in read_partition(part):
            stamp = row["time"]
            existing = raw.get(stamp)
            if stamp in visible and row["available_at"]<=manifest["cutoff"] and (
                existing is None or (row["available_at"],row["sequence"]) >
                                     (existing["available_at"],existing["sequence"])
            ):
                raw[stamp] = row
    calendar = MarketSchedule.model_validate(manifest["calendar"])
    scope = {"provider":"MT5","symbol":manifest["symbol"],"timeframe":"M5","price_basis":manifest["price_basis"],
             "feed_id":"aggregate-"+fingerprint([AGGREGATION_POLICY,manifest["feed_id"]])[:40]}
    pending=[]
    session=uuid4().hex
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO arrival_sessions VALUES(?,?,?,?,?,?,?)",
                   (session,"MT5",scope["symbol"],"M5",RECORDED_ARRIVAL,observed,canonical(manifest["calendar"])))
        for stamp in range(start,end,300):
            if not all(member in raw for member in range(stamp,stamp+300,60)) or (
                open_intervals(stamp,stamp+300,calendar) != [(stamp,stamp+300)]
            ):
                continue
            members = [raw[member] for member in range(stamp,stamp+300,60)]
            content = {"time":stamp,"open":members[0]["open"],"high":max(row["high"] for row in members),
                       "low":min(row["low"] for row in members),"close":members[-1]["close"],
                       "volume":sum(row["volume"] for row in members),"source_observed_ms":None,
                       "aggregation_policy":AGGREGATION_POLICY,"constituents":[row["fingerprint"] for row in members]}
            digest = fingerprint(content)
            prior = db.execute("""SELECT o.sequence,o.fingerprint,r.persisted_ms FROM arrival_observations o
                LEFT JOIN arrival_receipts r USING(sequence) WHERE provider=? AND feed_id=? AND symbol=?
                AND timeframe=? AND price_basis=? AND candle_open=? ORDER BY sequence DESC LIMIT 1""",
                (*(scope[key] for key in SCOPE_FIELDS),stamp)).fetchone()
            if prior and prior["fingerprint"]==digest:
                if prior["persisted_ms"] is None:
                    pending.append(prior["sequence"])
                continue
            pending.append(db.execute("""INSERT INTO arrival_observations(
                session_id,provider,feed_id,symbol,timeframe,price_basis,candle_open,nominal_close,
                observed_ms,elapsed_ms,content,fingerprint,previous_sequence,recovery)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)""", (session,*(scope[key] for key in SCOPE_FIELDS),
                stamp,stamp+300,observed,elapsed,canonical(content),digest,prior["sequence"] if prior else None)).lastrowid)
    persisted,_ = clock.sample()
    with transaction() as db:
        db.executemany("INSERT OR IGNORE INTO arrival_receipts VALUES(?,?)",[(seq,persisted) for seq in pending])
    return seal(scope,start,end,to_seconds(persisted),calendar)
