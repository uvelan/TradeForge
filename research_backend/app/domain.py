"""Trade planning, audit trail, journal and AI export/import without LLM APIs."""
from __future__ import annotations
from datetime import datetime,timezone
from decimal import Decimal
import csv
import hashlib
import io
import json
import os
import uuid
import zipfile
from pathlib import Path
from fastapi import HTTPException
from .schemas import TradePlan, Outcome, JournalInput, ReviewInput
from .risk import evaluate
from .db import transaction, rows, one, utc_now
from .config import EXPORT_DIR, JOURNAL_DIR, JOURNAL_FILE


def _id(prefix:str)->str: return prefix+'-'+uuid.uuid4().hex[:12].upper()

def _json(value)->str: return json.dumps(value,sort_keys=True,default=str,ensure_ascii=False)


def create_trade(plan:TradePlan)->dict:
    uid=_id('TD');pid=_id('PLAN');eid=_id('EVENT');now=utc_now()
    payload=plan.model_dump(mode='json')
    with transaction() as db:
        db.execute('INSERT INTO trades(id,symbol,direction,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',
            (uid,plan.symbol,plan.direction,'PLANNED',now,now))
        db.execute('INSERT INTO plan_versions VALUES(?,?,?,?,?)',(pid,uid,1,_json(payload),now))
        db.execute('INSERT INTO trade_events VALUES(?,?,?,?,?,?)',
            (eid,uid,1,'PLAN_CREATED',_json({'plan_version':1}),now))
    return get_trade(uid)


def get_trade(uid:str)->dict:
    t=one('SELECT * FROM trades WHERE id=?',(uid,))
    if not t:raise HTTPException(status_code=404,detail='Trade not found')
    versions=rows('SELECT id,version,payload,created_at FROM plan_versions WHERE trade_id=? ORDER BY version DESC',(uid,))
    t['versions']=[{'id':v['id'],'version':v['version'],'plan':json.loads(v['payload']),'created_at':v['created_at']} for v in versions]
    t['plan']=t['versions'][0]['plan']
    t['events']=[dict(e,payload=json.loads(e['payload'])) for e in rows('SELECT * FROM trade_events WHERE trade_id=? ORDER BY sequence',(uid,))]
    t['reviews']=rows('''SELECT r.id,r.package_id,r.assessment,r.verification,r.created_at FROM ai_reviews r JOIN ai_packages p ON r.package_id=p.id WHERE p.trade_id=? ORDER BY r.created_at DESC''',(uid,))
    t['outcome']=one('SELECT * FROM trade_outcomes WHERE trade_id=?',(uid,))
    return t


def revise(uid:str,plan:TradePlan)->dict:
    old=get_trade(uid)
    if old['status'] in ('CLOSED','CANCELLED'):raise HTTPException(409,'Cannot revise closed or cancelled trade')
    version=old['versions'][0]['version']+1
    with transaction() as db:
        now=utc_now()
        db.execute('INSERT INTO plan_versions VALUES(?,?,?,?,?)',(_id('PLAN'),uid,version,_json(plan.model_dump(mode='json')),now))
        db.execute('INSERT INTO trade_events VALUES(?,?,?,?,?,?)',
            (_id('EVENT'),uid,len(old['events'])+1,'PLAN_REVISED',_json({'previous_version':version-1,'new_version':version}),now))
        db.execute('UPDATE trades SET updated_at=?,symbol=?,direction=?,status=? WHERE id=?',
            (now,plan.symbol,plan.direction,'PLANNED',uid))
    return get_trade(uid)


def close_trade(uid:str,outcome:Outcome)->dict:
    t=get_trade(uid)
    if t['status'] in ('CANCELLED','CLOSED'):raise HTTPException(409,'Trade already finalized')
    with transaction() as db:
        now=utc_now()
        db.execute('INSERT INTO trade_outcomes VALUES(?,?,?,?,?)',(uid,str(outcome.pnl_usd),str(outcome.result_r),outcome.notes,now))
        db.execute('UPDATE trades SET status=?,updated_at=? WHERE id=?',('CLOSED',now,uid))
        db.execute('INSERT INTO trade_events VALUES(?,?,?,?,?,?)',(_id('EVENT'),uid,len(t['events'])+1,'TRADE_CLOSED',
            _json(outcome.model_dump(mode='json')),now))
    return get_trade(uid)


def list_trades():
    trades=rows('SELECT * FROM trades ORDER BY created_at DESC LIMIT 200')
    for t in trades:
        p=one('SELECT version,payload FROM plan_versions WHERE trade_id=? ORDER BY version DESC LIMIT 1',(t['id'],))
        t['version']=p['version'];t['plan']=json.loads(p['payload'])
        outcome=one('SELECT pnl_usd,result_r FROM trade_outcomes WHERE trade_id=?',(t['id'],))
        t['outcome']=outcome
    return trades


def analytics():
    all_trades=list_trades();closed=[t for t in all_trades if t['outcome']]
    values=[Decimal(t['outcome']['result_r']) for t in closed]
    wins=[v for v in values if v>0];losses=[v for v in values if v<0]
    avg=sum(values)/len(values) if values else Decimal(0)
    return {'trades':len(all_trades),'active_plans':sum(t['status']=='PLANNED' for t in all_trades),
      'closed':len(closed),'win_rate':round(len(wins)*100/len(values),1) if values else None,
      'expectancy_r':round(float(avg),3) if values else None,
      'net_pnl_usd':str(sum((Decimal(t['outcome']['pnl_usd']) for t in closed),Decimal(0))),
      'profit_factor':round(float(sum(wins)/abs(sum(losses))),2) if losses else None,
      'note':'Manual journal outcomes; not a reconciled broker performance statement'}


def _package_files(t:dict,package_id:str,cutoff:str)->dict[str,bytes]:
    latest=t['versions'][0]
    plan=latest['plan'];risk=evaluate(TradePlan.model_validate(plan))
    events=[e for e in t['events'] if e['at']<=cutoff]
    # The latest plan must have existed at cutoff.
    if latest['created_at']>cutoff:raise HTTPException(422,'Plan was created after knowledge cutoff')
    pieces=[
      '# TRADEFORGE · PRE-TRADE REVIEW',
      f'Package: {package_id} | Trade: {t["id"]} | Plan v{latest["version"]}',
      f'Knowledge cutoff (UTC): {cutoff}',
      '\n## Strict requirements',
      'Evaluate ONLY supplied evidence available at or before cutoff. Challenge assumptions. Do not invent metrics.',
      'Distinguish observation from validated statistical edge. If evidence is insufficient, say INSUFFICIENT_DATA.',
      'Do not provide executable orders. Return JSON matching response/AI_REVIEW_TEMPLATE.json.',
      '\n## Trade plan'
    ]
    for key,value in plan.items():pieces.append(f'- **{key}**: {value}')
    pieces.extend(['\n## Deterministic risk calculation', '```json',json.dumps(risk,indent=2),'```',
      '\n## Event timeline (cutoff filtered)'])
    for event in events:pieces.append(f'- {event["at"]}: {event["kind"]} — {event["payload"]}')
    pieces.extend(['\n## Evidence limitations','- No historical analog dataset is included unless explicitly provided.','- No future price or realized trade outcome is included.',
      '- Broker-specific execution cost and pip value have not been independently verified.'])
    template={'package_id':package_id,'trade_decision_id':t['id'],'plan_version':latest['version'],
      'assessment':'INSUFFICIENT_DATA',
      'findings':[{'text':'State one evidence-linked finding','severity':'MEDIUM','evidence_refs':['PLAN','RISK']}],
      'missing_evidence':[],'limitations':[]}
    manifest={'schema_version':'1.0.0','package_id':package_id,'trade_decision_id':t['id'],
      'plan_version':latest['version'],'review_type':'PRE_TRADE','knowledge_cutoff_utc':cutoff,
      'evidence_refs':['PLAN','RISK','TIMELINE'], 'generated_at':utc_now(),'source':'Local plan only; no verified historical analogs'}
    return {'AI_CONTEXT.md':'\n'.join(pieces).encode(),
        'data/trade_plan.json':json.dumps(plan,indent=2).encode(),
        'data/risk_metrics.json':json.dumps(risk,indent=2).encode(),
        'data/timeline.jsonl':'\n'.join(json.dumps(e) for e in events).encode(),
        'response/AI_REVIEW_TEMPLATE.json':json.dumps(template,indent=2).encode(),
        'manifest.json':json.dumps(manifest,indent=2).encode()}


def export_ai(uid:str)->dict:
    t=get_trade(uid)
    latest=t['versions'][0]
    # Strictly bound to the current time, after latest plan was created.
    cutoff=utc_now()
    package_id=_id('PKG')
    items=_package_files(t,package_id,cutoff)
    path=EXPORT_DIR/f'{package_id}.zip';partial=path.with_suffix('.pending')
    with zipfile.ZipFile(partial,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,content in items.items():z.writestr(name,content)
    os.replace(partial,path)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    with transaction() as db:
        db.execute('INSERT INTO ai_packages VALUES(?,?,?,?,?,?,?)',
          (package_id,uid,latest['version'],cutoff,str(path),digest,utc_now()))
    return {'package_id':package_id,'sha256':digest,'download_url':f'/research/api/ai/packages/{package_id}/download'}


def import_ai(data:ReviewInput)->dict:
    pkg=one('SELECT * FROM ai_packages WHERE id=?',(data.package_id,))
    if not pkg:raise HTTPException(404,'AI package not found')
    if pkg['trade_id']!=data.trade_decision_id or pkg['plan_version']!=data.plan_version:
        raise HTTPException(422,'AI response does not match immutable package snapshot')
    if len(data.findings)>30:raise HTTPException(422,'Too many findings')
    for f in data.findings:
        if not isinstance(f.get('text'),str) or not f['text'].strip() or len(f['text'])>2500:
            raise HTTPException(422,'Finding must include bounded text')
        refs=f.get('evidence_refs')
        if not isinstance(refs,list) or not refs or any(r not in ('PLAN','RISK','TIMELINE') for r in refs):
            raise HTTPException(422,'Finding references unsupported evidence')
        if 'metric_claims' in f:
            raise HTTPException(422,'Unverified numerical metric claims are not permitted')
    serialized=_json(data.model_dump())
    existing=one('SELECT id,assessment FROM ai_reviews WHERE package_id=? AND response=?',(data.package_id,serialized))
    if existing:return {'id':existing['id'],'assessment':existing['assessment'],'verification':'DUPLICATE_IGNORED'}
    uid=_id('REVIEW')
    with transaction() as db:
        db.execute('INSERT INTO ai_reviews VALUES(?,?,?,?,?,?)',
          (uid,data.package_id,data.assessment,serialized,'STRUCTURE_VALID_CONTENT_UNVERIFIED',utc_now()))
    return {'id':uid,'assessment':data.assessment,'verification':'STRUCTURE_VALID_CONTENT_UNVERIFIED',
      'warning':'Free-text findings are not fact-checked by a model or by this importer.'}


def _render_journal(entries:list[dict])->str:
    parts=['# Forex Intraday Trading — Master Learning Journal','',
       '> Append-only logical record in SQLite. This Markdown file is an atomically regenerated materialized view.','']
    for e in entries:
        parts.extend([f'## {e["id"]} · {e["title"]}',f'*{e["created_at"]}*','',e['content'],''])
    return '\n'.join(parts)+'\n'


def journal_add(inp:JournalInput)->dict:
    with transaction() as db:
        n=db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM journal_entries WHERE kind=?',(inp.kind,)).fetchone()[0]
        uid=f'{inp.kind}-{n:05d}';now=utc_now()
        db.execute('INSERT INTO journal_entries VALUES(?,?,?,?,?,?)',(uid,inp.kind,n,inp.title,inp.content,now))
    journal_materialize()
    return {'id':uid,'kind':inp.kind,'title':inp.title,'content':inp.content,'created_at':now}


def journal_materialize():
    contents=_render_journal(rows('SELECT * FROM journal_entries ORDER BY created_at,id'))
    temp=JOURNAL_DIR/'.journal.pending'
    temp.write_text(contents,encoding='utf-8')
    os.replace(temp,JOURNAL_FILE)


def journal_list():return rows('SELECT * FROM journal_entries ORDER BY created_at DESC,id DESC LIMIT 500')
