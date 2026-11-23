import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .catalog import Catalog, CatalogIntegrityError
from .database import open_database
from .market import MT5Connection, MT5Unavailable, Simulator, Timeframe

logger = logging.getLogger(__name__)


class SimulationRequest(BaseModel):
    timeframe: Timeframe = Timeframe.M15
    start: int = 1_763_942_400
    end: int = 1_764_115_200


class BrokerImport(BaseModel):
    symbol: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._#+-]{0,31}$")
    timeframe: Timeframe
    start: int
    end: int


def create_app(storage: Path | None = None, broker: MT5Connection | None = None) -> FastAPI:
    root = storage or Path(os.environ.get("TRADEFORGE_STORAGE", "storage"))
    mt5 = broker or MT5Connection(os.environ.get("TRADEFORGE_MT5_TERMINAL"))
    research_app = None
    if storage is None:
        from research_backend.app.main import app as research_app

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = open_database(root)
        app.state.catalog = Catalog(root, engine)
        logger.info("event=storage_ready")
        try:
            if research_app is None:
                yield
            else:
                async with research_app.router.lifespan_context(research_app):
                    yield
        finally:
            engine.dispose()

    app = FastAPI(title="TradeForge", lifespan=lifespan)
    if research_app is not None:
        app.mount("/research", research_app)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])

    @app.middleware("http")
    async def check_origin(request: Request, call_next):
        from starlette.responses import JSONResponse

        origin = request.headers.get("origin")
        if origin is not None and origin not in {
            "http://127.0.0.1:5173", "http://localhost:5173",
            "http://127.0.0.1:4180", "http://localhost:4180",
            "http://127.0.0.1:8000", "http://localhost:8000",
        }:
            return JSONResponse({"detail": "Untrusted origin"}, status_code=403)
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"status": "ready", "timezone": "Asia/Kolkata"}

    @app.get("/api/mt5")
    def mt5_status():
        return {"status": "CONNECTED" if mt5.connected() else "DISCONNECTED"}

    @app.get("/api/datasets")
    def datasets():
        return app.state.catalog.list()

    @app.post("/api/datasets/simulation")
    def simulate(body: SimulationRequest):
        try:
            dataset_id = app.state.catalog.ingest(Simulator(), "EURUSD", body.timeframe, body.start, body.end)
        except CatalogIntegrityError as error:
            logger.exception("event=dataset_publication_integrity_failed")
            raise HTTPException(409, "Dataset integrity check failed") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return {"id": dataset_id}

    @app.post("/api/datasets/mt5")
    def import_broker(body: BrokerImport):
        try:
            if body.timeframe.seconds > 1800:
                raise ValueError("MT5 import supports M1 through M30 until broker session boundaries are modeled")
            catalog = app.state.catalog
            catalog.validate_range(body.timeframe, body.start, body.end)
            server, scale, candles = mt5.historical_candles(body.symbol, body.timeframe, body.start, body.end)
            dataset_id = catalog.publish("mt5", server, "bid", scale, body.symbol,
                                         body.timeframe, body.start, body.end, candles)
        except MT5Unavailable as error:
            raise HTTPException(503, str(error)) from error
        except CatalogIntegrityError as error:
            logger.exception("event=dataset_publication_integrity_failed")
            raise HTTPException(409, "Dataset integrity check failed") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return {"id": dataset_id}

    @app.get("/api/datasets/{dataset_id}")
    def read(dataset_id: str, cutoff: int | None = None):
        try:
            return app.state.catalog.read(dataset_id, cutoff)
        except KeyError as error:
            raise HTTPException(404, "Dataset not found") from error
        except (OSError, ValueError) as error:
            logger.exception("event=dataset_read_failed")
            raise HTTPException(409, "Dataset integrity check failed") from error

    return app


app = create_app()
