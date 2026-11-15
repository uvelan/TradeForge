from __future__ import annotations
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, Field, model_validator

Symbol = Literal['EURUSD','GBPUSD','USDJPY','XAUUSD']
Direction = Literal['BUY','SELL']

class TradePlan(BaseModel):
    symbol: Symbol
    direction: Direction
    strategy: str = Field(default='CRT / Market Structure',min_length=2,max_length=120)
    session: Literal['ASIA','LONDON','NEW_YORK','OTHER']='LONDON'
    timeframe: Literal['M1','M3','M5','M15','H1','H4','D1']='M5'
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
    kind: Literal['T','Q','CHART','RM','M','R']
    title: str=Field(min_length=2,max_length=200)
    content: str=Field(min_length=5,max_length=20000)

class ReviewInput(BaseModel):
    package_id: str
    trade_decision_id: str
    plan_version: int
    assessment: Literal['SUPPORTED','CONDITIONAL','REJECTED','INSUFFICIENT_DATA']
    findings: list[dict] = Field(default_factory=list,max_length=30)
    missing_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

class RiskInput(BaseModel):
    plan: TradePlan
