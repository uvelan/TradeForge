"""Redacted V1 interchange for linear, weighted-average broker accounts."""
from datetime import datetime, timezone
from typing import Annotated, Literal
from fractions import Fraction

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, model_validator

ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")]
Number = Annotated[str, Field(pattern=r"^-?(0|[1-9][0-9]{0,15})(\.[0-9]{1,10})?$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
MINOR = {"USD":2,"EUR":2,"GBP":2,"CHF":2,"CAD":2,"AUD":2,"NZD":2,"JPY":0,"KWD":3}


def utc(value):
    if not isinstance(value,str) or "T" not in value:
        raise ValueError("Use an ISO timestamp with explicit offset")
    dt=datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("Timestamp requires a UTC offset")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


Stamp=Annotated[str,AfterValidator(utc)]


class Contract(BaseModel):
    model_config=ConfigDict(extra="forbid")


class Money(Contract):
    amount:Number
    currency:Currency
    fx:ID|None=None


class Posting(Money):
    id:ID
    kind:Literal["DEPOSIT","WITHDRAWAL","COMMISSION","SWAP","FEE"]


class Deal(Contract):
    id:ID
    at:Stamp
    sequence:StrictInt=Field(ge=0)
    kind:Literal["BUY","SELL","CASH"]
    symbol:ID|None=None
    ticket:ID|None=None
    order_id:ID|None=None
    lots:Number|None=None
    price:Number|None=None
    balance_after:Number|None=None
    effect:Literal["IN","OUT","INOUT"]|None=None
    realized:Money|None=None
    fx:ID|None=None
    postings:list[Posting]=Field(max_length=16)


class Spec(Contract):
    id:ID
    symbol:ID
    start:Stamp
    end:Stamp
    contract_size:Number
    tick:Number
    lot_step:Number
    quote_currency:Currency
    leverage:Number|None=None

    @model_validator(mode="after")
    def valid(self):
        if self.start>=self.end or min(Fraction(x) for x in (self.contract_size,self.tick,self.lot_step))<=0:
            raise ValueError("Invalid effective range or contract grid")
        if self.leverage is not None and Fraction(self.leverage)<=0:
            raise ValueError("Invalid leverage")
        return self


class Rate(Contract):
    id:ID
    base:Currency
    quote:Currency
    rate:Number
    at:Stamp
    available:Stamp
    source:ID

    @model_validator(mode="after")
    def valid(self):
        if self.at>self.available or self.base==self.quote or Fraction(self.rate)<=0:
            raise ValueError("Invalid FX observation")
        return self


class Position(Contract):
    symbol:ID
    ticket:ID|None=None
    lots:Number
    average:Number


class Mark(Contract):
    symbol:ID
    bid:Number
    ask:Number
    as_of:Stamp
    available:Stamp
    source:ID
    fx:ID|None=None

    @model_validator(mode="after")
    def valid(self):
        if self.as_of>self.available or not 0<Fraction(self.bid)<=Fraction(self.ask):
            raise ValueError("Invalid closing mark")
        return self


class Statement(Contract):
    format:Literal["REDACTED_STATEMENT_V1"]
    book:ID
    start:Stamp
    end:Stamp
    mode:Literal["HEDGING","NETTING"]
    currency:Currency
    rounding:Literal["HALF_EVEN_PER_POSTING"]
    complete_history:bool=Field(strict=True)
    opening_balance:Number|None
    closing_balance:Number|None
    closing_equity:Number|None
    opening_positions:list[Position]|None=Field(max_length=500)
    closing_positions:list[Position]|None=Field(max_length=500)
    specs:list[Spec]=Field(max_length=1000)
    rates:list[Rate]=Field(max_length=5000)
    marks:list[Mark]=Field(max_length=500)
    max_age_seconds:StrictInt=Field(ge=0,le=86400)
    margin_model:Literal["GROSS_NOTIONAL","UNAVAILABLE"]="UNAVAILABLE"
    closing_margin:Number|None=None
    daily_loss_limit:Number|None=None
    weekly_loss_limit:Number|None=None
    rows:list[Deal]=Field(max_length=5000)

    @model_validator(mode="after")
    def consistent(self):
        if self.currency not in MINOR or self.start>=self.end:
            raise ValueError("Unsupported account currency or invalid period")
        for amount in (self.opening_balance,self.closing_balance,self.closing_equity,self.closing_margin,
                       self.daily_loss_limit,self.weekly_loss_limit):
            if amount is not None and (Fraction(amount)*10**MINOR[self.currency]).denominator!=1:
                raise ValueError("Account totals must be exact at the currency minor unit")
        if any(v is not None and Fraction(v)<=0 for v in (self.daily_loss_limit,self.weekly_loss_limit)):
            raise ValueError("Loss budgets must be positive")
        for values in ([r.id for r in self.rows],[(r.at,r.sequence) for r in self.rows],
                       [p.id for r in self.rows for p in r.postings],[s.id for s in self.specs],
                       [r.id for r in self.rates],[m.symbol for m in self.marks]):
            if len(values)!=len(set(values)):
                raise ValueError("Repeated row, posting, execution priority, or evidence identity")
        for r in self.rows:
            if not self.start<=r.at<self.end:
                raise ValueError(f"Row outside half-open reporting period: {r.id}")
            if r.kind=="CASH":
                if not r.postings or any(x is not None for x in (r.symbol,r.lots,r.price,r.realized,r.effect,r.fx)):
                    raise ValueError(f"Invalid cash row: {r.id}")
            elif not r.symbol or not r.price or not r.lots or min(Fraction(r.price),Fraction(r.lots))<=0:
                raise ValueError(f"Missing or invalid execution fields: {r.id}")
            for p in r.postings:
                if (p.kind=="DEPOSIT" and Fraction(p.amount)<=0) or (p.kind=="WITHDRAWAL" and Fraction(p.amount)>=0):
                    raise ValueError(f"Incorrect transfer sign: {p.id}")
        return self
