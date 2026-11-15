"""Versioned user annotations, scoped to one chart data context."""

import json
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from .db import one, transaction, utc_now
from .market import FRAME_MIN

router = APIRouter(prefix="/api/drawings")
Scope = Annotated[str, Query(pattern=r"^(MT5|MT5_SAVED|DUKASCOPY_SAVED|BINANCE_SPOT|DEMO):[a-zA-Z0-9._#+!-]{1,64}:[A-Z0-9]{2,4}$")]
Tool = Literal["horizontal", "vertical", "trend", "ray", "rectangle", "price-range", "time-range",
               "risk-reward", "entry", "stop", "target", "text", "session", "liquidity", "fvg", "smt", "measure"]
POINTS = {kind: 1 for kind in ("horizontal", "vertical", "entry", "stop", "target", "text")}


class Anchor(BaseModel):
    time: int = Field(ge=0, le=32_503_680_000)
    price: str = Field(pattern=r"^[0-9]{1,12}(\.[0-9]{1,10})?$")


class Drawing(BaseModel):
    id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    kind: Tool
    anchors: list[Anchor] = Field(min_length=1, max_length=3)
    label: str = Field(default="", max_length=120)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    visible: bool = True
    locked: bool = False
    created_at: int = Field(ge=0,le=32_503_680_000)
    updated_at: int = Field(ge=0,le=32_503_680_000)

    @model_validator(mode="after")
    def validate_points(self):
        count = POINTS.get(self.kind, 3 if self.kind == "risk-reward" else 2)
        if len(self.anchors) != count:
            raise ValueError("Drawing anchor count does not match its tool")
        if self.kind == "risk-reward":
            entry, stop, target = (Decimal(point.price) for point in self.anchors)
            if not (stop < entry < target or target < entry < stop):
                raise ValueError("Stop and target must bracket entry")
        if self.updated_at < self.created_at:
            raise ValueError("Drawing update precedes creation")
        return self


class DrawingUpdate(BaseModel):
    schema_version: Literal[1] = 1
    revision: int = Field(ge=0, le=2**31-2)
    items: list[Drawing] = Field(max_length=100)


@router.get("")
def get_drawings(scope: Scope):
    if scope.rsplit(":", 1)[-1] not in FRAME_MIN:
        raise HTTPException(422, "Unknown drawing timeframe")
    row = one("SELECT revision,payload FROM chart_drawing_versions WHERE scope=? ORDER BY revision DESC LIMIT 1", (scope,))
    return {"schema_version": 1, "revision": row["revision"] if row else 0, "items": json.loads(row["payload"]) if row else []}


@router.post("")
def save_drawings(scope: Scope, update: DrawingUpdate):
    if scope.rsplit(":", 1)[-1] not in FRAME_MIN:
        raise HTTPException(422, "Unknown drawing timeframe")
    items = [item.model_dump() for item in update.items]
    if len(set(item["id"] for item in items)) != len(items):
        raise HTTPException(422, "Duplicate drawing identity")
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        revision = db.execute("SELECT COALESCE(MAX(revision),0) FROM chart_drawing_versions WHERE scope=?",
                              (scope,)).fetchone()[0]
        if revision != update.revision:
            raise HTTPException(409, "Drawings changed in another window. Reload the drawing toolbox.")
        db.execute("INSERT INTO chart_drawing_versions VALUES(?,?,?,?)",
                   (scope, revision+1, json.dumps(items, separators=(",",":")), utc_now()))
    return {"schema_version": 1, "revision": revision+1, "items": items}
