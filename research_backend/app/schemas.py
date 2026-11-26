from __future__ import annotations
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Symbol = Literal['EURUSD','GBPUSD','USDJPY','XAUUSD']
Direction = Literal['BUY','SELL']

class SignalReference(BaseModel):
    event_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["SMT_CANDIDATE", "SMT_CONFIRMED", "SMT_INVALIDATED", "SMT_SOURCE_CORRECTED"]


SnapshotId = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Tag = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,23}$")]


class PlanReference(BaseModel):
    trade_id: str = Field(pattern=r"^TD-[A-F0-9]{12}$")
    version: int = Field(ge=1)


class TradePlan(BaseModel):
    symbol: Symbol
    direction: Direction
    strategy: str = Field(default='CRT / Market Structure',min_length=2,max_length=120)
    session: Literal['ASIA','LONDON','NEW_YORK','OTHER']='LONDON'
    timeframe: Literal['M1','M3','M5','M15','M30','H1','H4','D1']='M5'
    context: str = Field(min_length=5,max_length=10000)
    observation: str = Field(min_length=5,max_length=10000)
    evidence: str = Field(min_length=5,max_length=10000)
    hypothesis: str = Field(min_length=5,max_length=10000)
    setup: str = Field(min_length=5,max_length=10000)
    trigger: str = Field(min_length=5,max_length=10000)
    invalidation: str = Field(min_length=5,max_length=10000)
    management: str = Field(default='Review at planned levels; no automatic execution.',max_length=10000)
    entry: Decimal = Field(gt=0)
    stop: Decimal = Field(gt=0)
    target: Decimal = Field(gt=0)
    equity_usd: Decimal = Field(gt=0,le=100000000000)
    risk_percent: Decimal = Field(gt=0,le=2)
    pip_value_per_lot_usd: Decimal = Field(gt=0,le=1000000)
    signal_reference: SignalReference | None = None
    snapshot_id: SnapshotId | None = None
    knowledge_cutoff_utc: str | None = None

    @model_validator(mode='after')
    def validate_levels(self):
        if self.direction=='BUY' and not (self.stop < self.entry < self.target):
            raise ValueError('BUY requires stop < entry < target')
        if self.direction=='SELL' and not (self.target < self.entry < self.stop):
            raise ValueError('SELL requires target < entry < stop')
        return self

class Outcome(BaseModel):
    pnl_usd: Decimal
    result_r: Decimal
    notes: str=Field(default='',max_length=5000)

class JournalInput(BaseModel):
    snapshot_id: SnapshotId | None = None
    plan_reference: PlanReference | None = None
    supersedes: str | None = Field(default=None,pattern=r"^(T|Q|CHART|RM|M|R)-[0-9]{5,}$")
    tags: list[Tag] = Field(default_factory=list,max_length=10)
    signal_reference: SignalReference | None = None
    kind: Literal['T','Q','CHART','RM','M','R']
    title: str=Field(min_length=2,max_length=200)
    content: str=Field(min_length=5,max_length=20000)

class CorrectableReview(BaseModel):
    corrects_review_id: str | None = Field(default=None, pattern=r"^REVIEW-[A-Za-z0-9-]+$", max_length=120)


class ReviewInput(CorrectableReview):
    model_config = ConfigDict(extra="forbid")
    package_id: str
    trade_decision_id: str
    plan_version: int
    assessment: Literal['SUPPORTED','CONDITIONAL','REJECTED','INSUFFICIENT_DATA']
    findings: list[dict] = Field(default_factory=list,max_length=30)
    missing_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


Reference = Annotated[str, Field(min_length=1, max_length=160)]
Note = Annotated[str, Field(min_length=1, max_length=4000)]
References = Annotated[list[Reference], Field(max_length=200)]
Notes = Annotated[list[Note], Field(max_length=100)]


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: Note
    evidence_refs: Annotated[References, Field(min_length=1)]
    metric_refs: References
    metric_claims: dict[Reference, Annotated[str, Field(pattern=r"^-?[0-9]+(?:\.[0-9]+)?$", max_length=64)]] = Field(
        default_factory=dict, max_length=100,
        description="Exact numeric claims keyed by exported metric reference, using decimal strings.",
    )


class ReviewV1(CorrectableReview):
    model_config = ConfigDict(extra="forbid", title="TradeForge AI review v1")
    schema_version: Literal[1]
    package_id: str = Field(pattern=r"^PKG-[A-Za-z0-9-]+$", max_length=120)
    trade_decision_id: Reference
    plan_version: StrictInt = Field(ge=1)
    review_type: Literal["PRE_TRADE", "IN_TRADE", "POST_TRADE", "STRATEGY_RESEARCH", "LEARNING_REVIEW"]
    assessment: Literal["SUPPORTED", "CONDITIONAL", "REJECTED", "INSUFFICIENT_DATA"]
    findings: list[ReviewFinding] = Field(max_length=100)
    evidence_refs: References
    missing_evidence: Notes
    contradictions: Notes
    risk_concerns: Notes
    suggested_actions: Notes
    limitations: Notes

class RiskInput(BaseModel):
    plan: TradePlan
