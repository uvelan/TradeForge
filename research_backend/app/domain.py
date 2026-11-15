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
from .schemas import SignalReference, TradePlan, Outcome, JournalInput, ReviewInput, ReviewV1
from .risk import evaluate
from .db import transaction, rows, one, utc_now
from .config import EXPORT_DIR, JOURNAL_DIR, JOURNAL_FILE


def _id(prefix:str)->str: return prefix+'-'+uuid.uuid4().hex[:12].upper()

def _json(value)->str: return json.dumps(value,sort_keys=True,default=str,ensure_ascii=False)


def resolve_signal(db, reference: SignalReference | None) -> dict | None:
    if reference is None:
        return None
    if reference.status == "SMT_SOURCE_CORRECTED":
        row = db.execute("SELECT payload FROM smt_source_corrections WHERE event_key=?",
                         (reference.event_key,)).fetchone()
    else:
        row = db.execute("SELECT payload FROM smt_event_history WHERE event_key=? AND status=?",
                         (reference.event_key, reference.status)).fetchone()
    if row is None:
        raise HTTPException(422, "Linked signal revision unavailable")
    signal = json.loads(row["payload"])
    signal.pop("capture", None)
    return signal


def resolve_snapshot(db, snapshot_id:str|None):
    if snapshot_id is None:
        return None
    row=db.execute("SELECT metadata,png,created_at FROM chart_snapshots WHERE id=?",(snapshot_id,)).fetchone()
    if row is None:
        raise HTTPException(422,"Linked chart snapshot unavailable")
    if hashlib.sha256(row["metadata"].encode()+row["png"]).hexdigest()!=snapshot_id:
        raise HTTPException(409,"Chart snapshot checksum mismatch")
    return json.loads(row["metadata"]),row["png"],row["created_at"]


def validate_plan_snapshot(db,plan:TradePlan):
    capture=resolve_snapshot(db,plan.snapshot_id)
    if capture is not None and (capture[0]["symbol"]!=plan.symbol or capture[0]["timeframe"]!=plan.timeframe):
        raise HTTPException(422,"Snapshot symbol or timeframe differs from plan")
    return capture


def linked_signal(db, plan: TradePlan) -> dict | None:
    signal = resolve_signal(db, plan.signal_reference)
    if signal is None:
        return None
    provenance = signal["provenance"]
    if plan.symbol not in {provenance[side]["symbol"] for side in ("primary", "comparison")} or plan.timeframe != provenance["timeframe"]:
        raise HTTPException(422, "Signal instrument or timeframe differs from the plan")
    return signal


def create_trade(plan:TradePlan)->dict:
    uid=_id('TD');pid=_id('PLAN');eid=_id('EVENT');now=utc_now()
    payload=plan.model_dump(mode='json')
    with transaction() as db:
        linked_signal(db, plan)
        validate_plan_snapshot(db, plan)
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


def revise(uid:str,plan:TradePlan,expected_version:int|None=None)->dict:
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        trade=db.execute('SELECT status FROM trades WHERE id=?',(uid,)).fetchone()
        if trade is None:raise HTTPException(404,'Trade not found')
        if trade['status'] in ('CLOSED','CANCELLED'):raise HTTPException(409,'Cannot revise a finalized decision')
        previous=db.execute('SELECT MAX(version) FROM plan_versions WHERE trade_id=?',(uid,)).fetchone()[0]
        if expected_version is not None and expected_version != previous:
            raise HTTPException(409,'Plan changed since this draft was opened')
        sequence=db.execute('SELECT MAX(sequence)+1 FROM trade_events WHERE trade_id=?',(uid,)).fetchone()[0]
        linked_signal(db, plan)
        validate_plan_snapshot(db, plan)
        now=utc_now()
        db.execute('INSERT INTO plan_versions VALUES(?,?,?,?,?)',(_id('PLAN'),uid,previous+1,_json(plan.model_dump(mode='json')),now))
        db.execute('INSERT INTO trade_events VALUES(?,?,?,?,?,?)',
            (_id('EVENT'),uid,sequence,'PLAN_REVISED',_json({'previous_version':previous,'new_version':previous+1}),now))
        db.execute('UPDATE trades SET updated_at=?,symbol=?,direction=?,status=? WHERE id=?',
            (now,plan.symbol,plan.direction,'PLANNED',uid))
    return get_trade(uid)


def decline_trade(uid:str,reason:str)->dict:
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        trade=db.execute('SELECT status FROM trades WHERE id=?',(uid,)).fetchone()
        if trade is None:raise HTTPException(404,'Trade not found')
        if trade['status'] in ('CLOSED','CANCELLED'):raise HTTPException(409,'Decision is already finalized')
        sequence=db.execute('SELECT MAX(sequence)+1 FROM trade_events WHERE trade_id=?',(uid,)).fetchone()[0]
        now=utc_now()
        db.execute("UPDATE trades SET updated_at=?,status='CANCELLED' WHERE id=?",(now,uid))
        db.execute('INSERT INTO trade_events VALUES(?,?,?,?,?,?)',
                   (_id('EVENT'),uid,sequence,'NO_TRADE',_json({'reason':reason}),now))
    return get_trade(uid)


def close_trade(uid:str,outcome:Outcome)->dict:
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        trade=db.execute("SELECT status FROM trades WHERE id=?",(uid,)).fetchone()
        if trade is None:raise HTTPException(404,"Trade not found")
        if trade["status"] in ("CANCELLED","CLOSED"):raise HTTPException(409,"Trade already finalized")
        sequence=db.execute("SELECT MAX(sequence)+1 FROM trade_events WHERE trade_id=?",(uid,)).fetchone()[0]
        now=utc_now()
        db.execute('INSERT INTO trade_outcomes VALUES(?,?,?,?,?)',(uid,str(outcome.pnl_usd),str(outcome.result_r),outcome.notes,now))
        db.execute('UPDATE trades SET status=?,updated_at=? WHERE id=?',('CLOSED',now,uid))
        db.execute('INSERT INTO trade_events VALUES(?,?,?,?,?,?)',(_id('EVENT'),uid,sequence,'TRADE_CLOSED',
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
    with transaction() as db:
        counts = db.execute("SELECT COUNT(*) AS total, COUNT(CASE WHEN status='PLANNED' THEN 1 END) AS active FROM trades").fetchone()
        closed = db.execute("SELECT result_r, pnl_usd FROM trade_outcomes").fetchall()
    values=[Decimal(outcome['result_r']) for outcome in closed]
    wins=[v for v in values if v>0];losses=[v for v in values if v<0]
    avg=sum(values)/len(values) if values else Decimal(0)
    return {'trades':counts['total'],'active_plans':counts['active'],
      'closed':len(closed),'win_rate':round(len(wins)*100/len(values),1) if values else None,
      'expectancy_r':round(float(avg),3) if values else None,
      'net_pnl_usd':str(sum((Decimal(outcome['pnl_usd']) for outcome in closed),Decimal(0))),
      'profit_factor':round(float(sum(wins)/abs(sum(losses))),2) if losses else None,
      'note':'Manual journal outcomes; not a reconciled broker performance statement'}


def _package_files(t:dict,package_id:str,cutoff:str,backtest_evidence:dict|None=None)->dict[str,bytes]:
    latest=t['versions'][0]
    plan=latest['plan']; validated=TradePlan.model_validate(plan);risk=evaluate(validated)
    with transaction() as db:
        signal = linked_signal(db, validated)
        snapshot = validate_plan_snapshot(db,validated)
    evidence_refs = ['PLAN','RISK','TIMELINE']
    if signal is not None:
        cutoff_epoch = datetime.fromisoformat(cutoff).timestamp()
        if signal["transitions"][-1]["time"] > cutoff_epoch:
            raise HTTPException(422, "Linked signal occurred after package cutoff")
        evidence_refs.append("SIGNAL")
    if snapshot is not None:
        if snapshot[2]>cutoff:
            raise HTTPException(422,"Linked chart snapshot occurred after package cutoff")
        evidence_refs.append("CHART_CAPTURE")
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
    template=ReviewV1(schema_version=1, package_id=package_id, trade_decision_id=t['id'],
      plan_version=latest['version'], review_type='PRE_TRADE', assessment='INSUFFICIENT_DATA',
      findings=[{'summary':'State one evidence-linked finding','evidence_refs':['PLAN','RISK'],'metric_refs':[]}],
      evidence_refs=['PLAN','RISK'], missing_evidence=[], contradictions=[], risk_concerns=[],
      suggested_actions=[], limitations=[]).model_dump(mode="json")
    manifest={'schema_version':'1.0.0','package_id':package_id,'trade_decision_id':t['id'],
      'plan_version':latest['version'],'review_type':'PRE_TRADE','knowledge_cutoff_utc':cutoff,
      'evidence_refs':evidence_refs, 'generated_at':utc_now(),'source':'Local plan only; no verified historical analogs'}
    if signal is not None:
        pieces.append("\n## Linked signal\nSee data/signal.json for the recorded revision and source lineage.")
    if snapshot is not None:
        pieces.append("\n## User chart capture\nSee data/chart.png and data/chart_metadata.json.")
    if backtest_evidence is not None:
        manifest["backtest_run"] = backtest_evidence
        pieces.append("\n## Historical replay classification")
        pieces.append(f'- {backtest_evidence["trust"]["status"]}. {backtest_evidence["trust"]["reason"]}')
        pieces.append("- Classification only. This pre-trade package includes no backtest outcomes or performance.")
    return {**({"data/chart.png":snapshot[1],"data/chart_metadata.json":_json(snapshot[0]).encode()}
              if snapshot is not None else {}),
        **({"data/signal.json": _json(signal).encode()} if signal is not None else {}), 'AI_CONTEXT.md':'\n'.join(pieces).encode(),
        'data/trade_plan.json':json.dumps(plan,indent=2).encode(),
        'data/risk_metrics.json':json.dumps(risk,indent=2).encode(),
        'data/timeline.jsonl':'\n'.join(json.dumps(e) for e in events).encode(),
        'response/AI_REVIEW_TEMPLATE.json':json.dumps(template,indent=2).encode(),
        'manifest.json':json.dumps(manifest,indent=2).encode()}


def export_ai(uid:str, plan_version:int|None=None, *, backtest_run_id:str|None=None)->dict:
    t=get_trade(uid)
    if t["outcome"] is not None:
        raise HTTPException(422, "Pre-trade review packages require an open plan")
    latest=t['versions'][0]
    if plan_version is not None and latest["version"] != plan_version:
        raise HTTPException(409, "Plan version changed. Refresh the plan before exporting.")
    # Strictly bound to the current time, after latest plan was created.
    cutoff=utc_now()
    package_id=_id('PKG')
    backtest_evidence = None
    if backtest_run_id is not None:
        from .replay_trust import assess_replay, stored_replay
        row, replay = stored_replay(backtest_run_id)
        if row["created_at"] > cutoff or replay["result"]["symbol"] != latest["plan"]["symbol"]:
            raise HTTPException(422, "Replay must match the plan instrument and exist at the export cutoff")
        backtest_evidence = {"run_id": backtest_run_id, "trust": assess_replay(replay),
                             "performance_included": False}
    items=_package_files(t,package_id,cutoff,backtest_evidence)
    path=EXPORT_DIR/f'{package_id}.zip';partial=path.with_suffix('.pending')
    with zipfile.ZipFile(partial,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,content in items.items():z.writestr(name,content)
    os.replace(partial,path)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    with transaction() as db:
        db.execute('INSERT INTO ai_packages VALUES(?,?,?,?,?,?,?)',
          (package_id,uid,latest['version'],cutoff,str(path),digest,utc_now()))
    return {'package_id':package_id,'sha256':digest,'download_url':f'/research/api/ai/packages/{package_id}/download'}


def import_ai(data: ReviewV1 | ReviewInput, original: bytes) -> dict:
    pkg=one('SELECT * FROM ai_packages WHERE id=?',(data.package_id,))
    if not pkg:raise HTTPException(404,'AI package not found')
    if pkg['trade_id']!=data.trade_decision_id or pkg['plan_version']!=data.plan_version:
        raise HTTPException(422,'AI response does not match immutable package snapshot')
    if isinstance(data, ReviewV1):
        if data.review_type != "PRE_TRADE":
            raise HTTPException(422, "Review type differs from the exported package")
        path = Path(pkg["location"]).resolve()
        if not path.is_relative_to(EXPORT_DIR.resolve()) or not path.is_file():
            raise HTTPException(409, "Package file unavailable")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != pkg["sha256"]:
            raise HTTPException(409, "Package checksum mismatch")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            metrics = json.loads(archive.read("data/risk_metrics.json"))
            manifest = json.loads(archive.read("manifest.json"))
        if set(data.evidence_refs) - set(manifest["evidence_refs"]):
            raise HTTPException(422, "Review references unsupported evidence")
        for finding in data.findings:
            if set(finding.evidence_refs) - set(data.evidence_refs):
                raise HTTPException(422, "Finding references undeclared evidence")
            if set(finding.metric_refs) - set(metrics):
                raise HTTPException(422, "Finding references unsupported metrics")
            if finding.metric_refs and "RISK" not in finding.evidence_refs:
                raise HTTPException(422, "Metric references require RISK evidence")
            for metric, claimed in finding.metric_claims.items():
                if metric not in finding.metric_refs or not isinstance(metrics[metric], str):
                    raise HTTPException(422, "Numeric claims require a referenced numeric metric")
                if Decimal(claimed) != Decimal(metrics[metric]):
                    raise HTTPException(422, f"Metric claim differs from the exported value: {metric}")
    for f in data.findings if isinstance(data, ReviewInput) else ():
        if not isinstance(f.get('text'),str) or not f['text'].strip() or len(f['text'])>2500:
            raise HTTPException(422,'Finding must include bounded text')
        refs=f.get('evidence_refs')
        if not isinstance(refs,list) or not refs or any(r not in ('PLAN','RISK','TIMELINE') for r in refs):
            raise HTTPException(422,'Finding references unsupported evidence')
        if 'metric_claims' in f:
            raise HTTPException(422,'Unverified numerical metric claims are not permitted')
    serialized=_json(data.model_dump())
    digest = hashlib.sha256(original).hexdigest()
    submitted_at = utc_now()
    submission_id = _id("SUBMISSION")
    verification = "STRUCTURE_VALID_CONTENT_UNVERIFIED"
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT id FROM ai_reviews WHERE package_id=? AND response=? ORDER BY created_at,id LIMIT 1",
            (data.package_id, serialized),
        ).fetchone()
        if existing:
            uid = existing["id"]
            exact = db.execute("SELECT 1 FROM ai_review_submissions WHERE review_id=? AND sha256=? AND body=? LIMIT 1",
                               (uid, digest, original)).fetchone()
            classification = "EXACT_DUPLICATE" if exact else "EQUIVALENT_JSON"
            verification = "DUPLICATE_IGNORED"
        else:
            uid = _id("REVIEW")
            classification = "NEW_REVIEW"
            db.execute("INSERT INTO ai_reviews VALUES(?,?,?,?,?,?)",
                       (uid, data.package_id, data.assessment, serialized, verification, submitted_at))
        db.execute("INSERT INTO ai_review_submissions VALUES(?,?,?,?,?,?,?)",
                   (submission_id, uid, original, digest, submitted_at, "ACCEPTED", classification))
    return {"id": uid, "assessment": data.assessment, "verification": verification,
            "submission_id": submission_id, "submission_sha256": digest, "classification": classification,
            "warning": "Free-text findings require manual verification."}


def _render_journal(entries:list[dict])->str:
    parts=['# Forex Intraday Trading — Master Learning Journal','',
       '> Append-only logical record in SQLite. This Markdown file is an atomically regenerated materialized view.','']
    for e in entries:
        parts.extend([f'## {e["id"]} · {e["title"]}',f'*{e["created_at"]}*','',e['content'],''])
        if e.get('signal_reference'):
            link = e['signal_reference']
            parts.extend([f'Signal evidence: {link["event_key"]} ({link["status"]})', ''])
        if e.get('plan_reference'):
            reference=e['plan_reference']
            parts.extend([f"Plan: {reference['trade_id']} v{reference['version']}",''])
        if e.get('snapshot_id'):
            parts.extend([f"Chart capture: {e['snapshot_id']}",''])
        if e.get('supersedes'):
            parts.extend([f"Supersedes: {e['supersedes']}",''])
        if e.get('tags'):
            parts.extend(["Tags: "+", ".join(e['tags']),''])
    return '\n'.join(parts)+'\n'


def journal_add(inp:JournalInput)->dict:
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        resolve_signal(db, inp.signal_reference)
        resolve_snapshot(db, inp.snapshot_id)
        if inp.plan_reference and db.execute("SELECT 1 FROM plan_versions WHERE trade_id=? AND version=?",
                                            (inp.plan_reference.trade_id,inp.plan_reference.version)).fetchone() is None:
            raise HTTPException(422,"Linked plan version unavailable")
        if inp.supersedes:
            if db.execute("SELECT 1 FROM journal_entries WHERE id=?",(inp.supersedes,)).fetchone() is None:
                raise HTTPException(422,"Original journal entry unavailable")
            if db.execute("SELECT 1 FROM journal_entry_context WHERE json_extract(payload,'$.supersedes')=?",
                          (inp.supersedes,)).fetchone():
                raise HTTPException(409,"Journal entry has already been superseded")
        n=db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM journal_entries WHERE kind=?',(inp.kind,)).fetchone()[0]
        uid=f'{inp.kind}-{n:05d}';now=utc_now()
        db.execute('INSERT INTO journal_entries VALUES(?,?,?,?,?,?)',(uid,inp.kind,n,inp.title,inp.content,now))
        if inp.signal_reference:
            db.execute('INSERT INTO journal_signal_links VALUES(?,?,?)',
                       (uid, inp.signal_reference.event_key, inp.signal_reference.status))
        context=inp.model_dump(mode="json",include={"snapshot_id","plan_reference","supersedes","tags"},exclude_none=True)
        context["tags"]=sorted(set(context["tags"]))
        if any(context.values()):
            db.execute("INSERT INTO journal_entry_context VALUES(?,?)",(uid,_json(context)))
    journal_materialize()
    return {'id':uid,'kind':inp.kind,'title':inp.title,'content':inp.content,'created_at':now,
            'signal_reference':inp.signal_reference,**context}


def journal_materialize():
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        contents=_render_journal(journal_records("ORDER BY created_at,id"))
        temp=JOURNAL_DIR/'.journal.pending'
        temp.write_text(contents,encoding='utf-8')
        os.replace(temp,JOURNAL_FILE)


def format_journal_entry(entry:dict)->dict:
    key = entry.pop("event_key")
    status = entry.pop("status")
    context = entry.pop("context")
    if key:
        entry["signal_reference"] = {"event_key": key, "status": status}
    if context:
        entry.update(json.loads(context))
    return entry


JOURNAL_QUERY = """SELECT entry.*, link.event_key, link.status, context.payload AS context FROM journal_entries entry
                   LEFT JOIN journal_signal_links link ON entry.id=link.entry_id
                   LEFT JOIN journal_entry_context context ON entry.id=context.entry_id """


def journal_records(order: str):
    return [format_journal_entry(entry) for entry in rows(JOURNAL_QUERY+order)]


def journal_detail(entry_id:str):
    entry=one(JOURNAL_QUERY+"WHERE entry.id=?",(entry_id,))
    if entry is None:
        raise HTTPException(404,"Journal entry unavailable")
    return format_journal_entry(entry)


def journal_list():
    return journal_records("ORDER BY created_at DESC,id DESC LIMIT 500")
