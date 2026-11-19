"""Optional read-only acceptance against the connected MT5 terminal; uses temporary storage."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time

from research_backend.app import config, db, storage
from research_backend.app.arrival_capture import CaptureConfig, CaptureManager, seal
from research_backend.app.arrival_routes import assess
from research_backend.app.market import mt5_connection
from research_backend.app.smt_causal import FrozenFeed


async def verify():
    if not mt5_connection()["connected"]:
        print("NOT EXECUTED: connected MT5 terminal unavailable",flush=True)
        return
    with TemporaryDirectory(prefix="tradeforge-live-arrival-") as root:
        root = Path(root)
        config.DATABASE = db.DATABASE = root/"isolated.sqlite3"
        storage.MARKET_DIR = root/"market"
        db.initialize()
        manager = CaptureManager()
        try:
            await manager.start(CaptureConfig(timeframe="M1",poll_seconds=2))
            deadline = asyncio.get_running_loop().time()+120
            while asyncio.get_running_loop().time()<deadline:
                status = manager.status()
                scopes = status["scopes"]
                if all(scope.get("last_persisted_ms") for scope in scopes.values()):
                    print("Both EURUSD and GBPUSD produced durable receipts", flush=True)
                    break
                if status["state"]=="FAILED":
                    break
                if all(scope.get("warning")=="PROVIDER_NOMINAL_CLOSE_IN_FUTURE" for scope in scopes.values()):
                    break
                await asyncio.sleep(2)
        finally:
            stopped = await manager.stop()
        print(json.dumps({"state":stopped["state"],"scopes":{symbol:{
            key:value for key,value in item.items() if key not in {"feed_id","session_id"}}
            for symbol,item in stopped["scopes"].items()}},sort_keys=True),flush=True)
        scopes=stopped["scopes"]
        if not all(scope.get("last_persisted_ms") for scope in scopes.values()):
            print("FAIL: terminal was available but a recorded pair did not complete",flush=True)
            return
        cutoff=int(time())
        common = min(scope["last_candle"] for scope in scopes.values())
        print("shared_candle_utc_epoch",common,flush=True)
        for symbol,scope in scopes.items():
            fields={"provider":"MT5","symbol":symbol,"feed_id":scope["feed_id"],
                    "timeframe":"M1","price_basis":scope["price_basis"]}
            sealed=seal(fields,common,common+60,cutoff,manager.worker.config.calendar)
            feed=FrozenFeed(symbol=symbol,feed_id=scope["feed_id"],timeframe="M1",source="MT5_SAVED",
                            price_basis=scope["price_basis"],start=common,end=common+60,
                            calendar=manager.worker.config.calendar,partitions=sealed["partitions"],
                            arrival_manifest=sealed["id"])
            result=assess(feed,cutoff)
            print(symbol,result["status"],result["modes"]["STRICT_RECORDED"]["arrival_delay_seconds"],flush=True)
            assert result["status"]=="READY_RECORDED"
        print("PASS: recorded pair, manifest integrity, and readiness for the one observed shared candle",flush=True)


if __name__=="__main__":
    asyncio.run(verify())
