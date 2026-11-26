"""Statement cash in integer minor units; exact rational lots, prices and FX."""
from datetime import datetime, timedelta
from fractions import Fraction as F

from .statement_contract import MINOR


class Missing(ValueError):
    pass


def minor(s, amount):
    return round(F(amount)*10**MINOR[s.currency])


def spec_at(s,symbol,at):
    matches=[v for v in s.specs if v.symbol==symbol and v.start<=at<v.end]
    if len(matches)!=1:
        raise Missing("MISSING_OR_OVERLAPPING_DATED_SPEC")
    return matches[0]


def conversion(s,value,currency,fx,at):
    if currency==s.currency:
        return minor(s,value),None
    rate=next((r for r in s.rates if r.id==fx),None)
    if not rate:
        raise Missing("MISSING_FX")
    age=(datetime.fromisoformat(at)-datetime.fromisoformat(rate.at)).total_seconds()
    if rate.available>at or age<0 or age>s.max_age_seconds:
        raise Missing("STALE_OR_UNAVAILABLE_FX")
    pair=(rate.base,rate.quote)
    if pair==(currency,s.currency):
        result=value*F(rate.rate)
    elif pair==(s.currency,currency):
        result=value/F(rate.rate)
    else:
        raise Missing("FX_PAIR_MISMATCH")
    return minor(s,result),rate.model_dump()


def key(s,symbol,ticket):
    if s.mode=="HEDGING" and not ticket:
        raise Missing("MISSING_POSITION_TICKET")
    return ticket if s.mode=="HEDGING" else symbol


def problem(state,source,reason):
    state["issues"].append({"source":source,"reason":reason})


def initial(s):
    state=dict(positions={},external=0,realized=0,charges=0,cash_known=True,inventory_known=True,issues=[],daily={})
    if not s.complete_history:
        problem(state,"header","INCOMPLETE_HISTORY")
        state.update(cash_known=False,inventory_known=False)
    if s.opening_balance is None:
        problem(state,"opening_balance","MISSING_OPENING_BALANCE")
        state["cash_known"]=False
    if s.opening_positions is None:
        problem(state,"opening_positions","MISSING_OPENING_INVENTORY")
        state.update(cash_known=False,inventory_known=False)
    for p in s.opening_positions or []:
        try:
            k=key(s,p.symbol,p.ticket)
            spec=spec_at(s,p.symbol,s.start)
            if k in state["positions"] or not F(p.lots) or F(p.average)<=0 or F(p.lots)%F(spec.lot_step):
                raise Missing("INVALID_OPENING_POSITION")
            state["positions"][k]=dict(symbol=p.symbol,lots=p.lots,average=p.average,contract=spec.contract_size,
                                       currency=spec.quote_currency)
        except Missing as exc:
            problem(state,"opening_positions/"+p.symbol,str(exc))
            state.update(inventory_known=False,cash_known=False)
    return state


def cash(s,state):
    if state["cash_known"]:
        return minor(s,s.opening_balance)+state["external"]+state["realized"]+state["charges"]


def fill(s,state,row,trace):
    if not state["inventory_known"]:
        raise Missing("PRIOR_INVENTORY_UNKNOWN")
    k=key(s,row.symbol,row.ticket)
    spec=spec_at(s,row.symbol,row.at)
    size,price=F(row.lots),F(row.price)
    if size%F(spec.lot_step) or price%F(spec.tick):
        raise Missing("OFF_GRID_EXECUTION")
    old=state["positions"].get(k)
    if old and (old["symbol"]!=row.symbol or old["currency"]!=spec.quote_currency or F(old["contract"])!=F(spec.contract_size)):
        raise Missing("POSITION_CONTRACT_DISCONTINUITY")
    lots=F(old["lots"]) if old else F(0)
    avg=F(old["average"]) if old else F(0)
    delta=size if row.kind=="BUY" else -size
    closed=min(size,abs(lots)) if lots*delta<0 else F(0)
    opened=size-closed
    effect="INOUT" if closed and opened else "OUT" if closed else "IN"
    if (row.effect and row.effect!=effect) or (s.mode=="HEDGING" and row.effect!=effect) or (s.mode=="HEDGING" and effect=="INOUT"):
        raise Missing("POSITION_EFFECT_MISMATCH")
    native=(price-avg)*closed*F(spec.contract_size)*(1 if lots>0 else -1)
    remaining=lots+delta
    if remaining:
        if not closed:
            avg=(abs(lots)*avg+size*price)/abs(remaining)
        elif opened:
            avg=price
        state["positions"][k]=dict(symbol=row.symbol,lots=str(remaining),average=str(avg),
                                   contract=spec.contract_size,currency=spec.quote_currency)
    else:
        state["positions"].pop(k)
    trace.update(position=k,spec=spec.id,before_lots=str(lots),closed_lots=str(closed),opened_lots=str(opened),
                 after_lots=str(remaining),before_average=old["average"] if old else None,
                 native_realized=str(native),native_currency=spec.quote_currency)
    try:
        amount,rate=conversion(s,native,spec.quote_currency,row.fx,row.at) if closed else (0,None)
        trace.update(realized_minor=amount,fx=rate)
        state["realized"]+=amount
    except Missing as exc:
        problem(state,row.id,str(exc))
        state["cash_known"]=False
        return
    if row.realized is not None:
        try:
            reported,used=conversion(s,F(row.realized.amount),row.realized.currency,row.realized.fx,row.at)
            trace.update(reported_realized_minor=reported,reported_fx=used,realized_difference_minor=amount-reported)
            if amount!=reported:
                problem(state,row.id,"BROKER_REALIZED_MISMATCH")
        except Missing as exc:
            problem(state,row.id,"REPORTED_"+str(exc))


def apply(s,state,row):
    before=cash(s,state)
    prior_income=state["realized"]+state["charges"]
    issue_index=len(state["issues"])
    trace=dict(row=row.model_dump(exclude_none=True),cash_before_minor=before,postings=[])
    if row.kind!="CASH":
        try:
            fill(s,state,row,trace)
        except Missing as exc:
            problem(state,row.id,str(exc))
            state.update(inventory_known=False,cash_known=False)
    for p in row.postings:
        evidence=p.model_dump(exclude_none=True)
        try:
            amount,rate=conversion(s,F(p.amount),p.currency,p.fx,row.at)
            category="external" if p.kind in ("DEPOSIT","WITHDRAWAL") else "charges"
            state[category]+=amount
            evidence.update(account_minor=amount,fx_evidence=rate,category=category)
        except Missing as exc:
            problem(state,row.id+"/"+p.id,str(exc))
            state["cash_known"]=False
        trace["postings"].append(evidence)
    after=cash(s,state)
    trace["cash_after_minor"]=after
    if row.balance_after is not None and after is not None:
        difference=F(after)-F(row.balance_after)*10**MINOR[s.currency]
        if difference:
            trace["balance_difference_minor"]=str(difference)
            problem(state,row.id,"RUNNING_BALANCE_MISMATCH")
    day=row.at[:10]
    state["daily"][day]=state["daily"].get(day,0)+state["realized"]+state["charges"]-prior_income
    trace["issues"]=state["issues"][issue_index:]
    return trace


VALUATION_POLICY = "STATEMENT_CLOSING_VALUATION_V2"


def finalize(s,state):
    expected={}
    if s.closing_positions is None:
        problem(state,"closing_positions","MISSING_CLOSING_INVENTORY")
    else:
        for p in s.closing_positions:
            try:
                k=key(s,p.symbol,p.ticket)
                if k in expected:
                    raise Missing("DUPLICATE_CLOSING_POSITION")
                expected[k]=(p.symbol,F(p.lots),F(p.average))
            except Missing as exc:
                problem(state,"closing_positions/"+p.symbol,str(exc))
        actual={k:(p["symbol"],F(p["lots"]),F(p["average"])) for k,p in state["positions"].items()}
        for k in sorted(actual.keys()|expected.keys()):
            if actual.get(k)!=expected.get(k):
                problem(state,"closing_positions/"+k,"CLOSING_POSITION_MISMATCH")

    cutoff = datetime.fromisoformat(s.end)
    last_period_instant = (cutoff - timedelta(microseconds=1)).isoformat(timespec="microseconds")
    marks={m.symbol:m for m in s.marks}
    unrealized=0
    margin=0
    valued=state["inventory_known"]
    margin_known=valued and s.margin_model=="GROSS_NOTIONAL"
    positions=[]
    for k,p in state["positions"].items():
        result={"position":k,**p}
        try:
            mark=marks.get(p["symbol"])
            if not mark:
                raise Missing("MISSING_CLOSING_MARK")
            result.update(mark=mark.model_dump(), valuation_cutoff=s.end)
            if mark.available > s.end or cutoff - datetime.fromisoformat(mark.as_of) > timedelta(seconds=s.max_age_seconds):
                raise Missing("STALE_OR_UNAVAILABLE_MARK")
            # Closing observations may arrive at cutoff. Contract terms belong
            # to the observed price and cannot come from the following period.
            terms_at = min(mark.as_of, last_period_instant)
            result["terms_at"] = terms_at
            spec=spec_at(s,p["symbol"],terms_at)
            result.update(spec=spec.id, contract_size=spec.contract_size, leverage=spec.leverage,
                          spec_start=spec.start, spec_end=spec.end)
            if F(spec.contract_size)!=F(p["contract"]) or spec.quote_currency!=p["currency"]:
                raise Missing("POSITION_CONTRACT_DISCONTINUITY")
            qty=F(p["lots"])
            price=F(mark.bid if qty>0 else mark.ask)
            value=(price-F(p["average"]))*qty*F(spec.contract_size)
            converted,fx=conversion(s,value,spec.quote_currency,mark.fx,s.end)
            result.update(mark=mark.model_dump(),spec=spec.id,native_unrealized=str(value),unrealized_minor=converted,fx=fx)
            unrealized+=converted
            if s.margin_model=="GROSS_NOTIONAL":
                if spec.leverage is None:
                    margin_known=False
                    problem(state,k,"MISSING_DATED_LEVERAGE")
                else:
                    used,_=conversion(s,abs(qty)*price*F(spec.contract_size)/F(spec.leverage),spec.quote_currency,mark.fx,s.end)
                    result["margin_minor"]=used
                    margin+=used
        except Missing as exc:
            valued=margin_known=False
            problem(state,k,str(exc))
        positions.append(result)

    closing=cash(s,state)
    equity=closing+unrealized if closing is not None and valued else None
    report=dict(valuation_policy=VALUATION_POLICY,valuation_cutoff=s.end,currency=s.currency,minor_decimals=MINOR[s.currency],opening=s.opening_balance,
                external_known_minor=state["external"],realized_known_minor=state["realized"],charges_known_minor=state["charges"],
                closing_minor=closing,broker_closing=s.closing_balance,unrealized_minor=unrealized if valued else None,
                equity_minor=equity,broker_equity=s.closing_equity,positions=positions,margin_minor=margin if margin_known else None,
                broker_margin=s.closing_margin)
    for name,value,reported in (("balance",closing,s.closing_balance),("equity",equity,s.closing_equity),("margin",report["margin_minor"],s.closing_margin)):
        if name=="margin" and reported is None and s.margin_model=="UNAVAILABLE":
            continue
        diff=None if value is None or reported is None else value-minor(s,reported)
        report[name+"_residual_minor"]=diff
        if diff!=0:
            problem(state,"closing_"+name,"MISSING_"+name.upper()+"_EVIDENCE" if diff is None else name.upper()+"_MISMATCH")
    # Send exact decimal strings as well as minor units for JavaScript clients.
    scale = 10**MINOR[s.currency]
    def display(amount):
        if amount is None:
            return None
        whole, fraction = divmod(abs(amount), scale)
        sign = "-" if amount < 0 else ""
        return f"{sign}{whole}.{fraction:0{MINOR[s.currency]}d}" if scale > 1 else f"{sign}{whole}"

    report["amounts"] = {k: display(v) for k,v in report.items() if k.endswith("_minor")}
    report["issues"]=state["issues"]
    report["status"]="RECONCILED" if not state["issues"] else "UNRESOLVED"
    # End-exclusive periods: the last included UTC day determines the risk window.
    last=datetime.fromisoformat(s.end)-timedelta(microseconds=1)
    midnight=last.replace(hour=0,minute=0,second=0,microsecond=0)
    limits={}
    for name,begin,budget in (("daily",midnight,s.daily_loss_limit),("weekly",midnight-timedelta(days=last.weekday()),s.weekly_loss_limit)):
        available=report["status"]=="RECONCILED" and datetime.fromisoformat(s.start)<=begin and budget is not None
        net=sum(amount for day,amount in state["daily"].items() if day>=begin.date().isoformat())
        limits[name]=dict(status="RECONCILED_PERIOD_TO_DATE" if available else "UNAVAILABLE",
                          budget=budget,loss_minor=max(0,-net) if available else None,
                          remaining_minor=minor(s,budget)-max(0,-net) if available else None)
    report["loss_limits"]=limits
    return report
