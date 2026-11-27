import assert from 'node:assert/strict';
import {execFileSync,spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtempSync,readFileSync,realpathSync,rmSync,writeFileSync} from 'node:fs';
import {createServer} from 'node:net';
import {tmpdir} from 'node:os';
import {dirname,join,resolve} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {chromium,expect} from '@playwright/test';

const {values:args}=parseArgs({options:{
  reference:{type:'string'},output:{type:'string'},source:{type:'string',default:'storage/t06-input'},
}});
if(!args.reference||!args.output)throw Error('Supply --reference and --output paths.');

const root=fileURLToPath(new URL('../../',import.meta.url)), frontend=join(root,'frontend');
const python=process.env.TRADEFORGE_E2E_PYTHON??join(root,'.venv','Scripts','python.exe');
const path='/research/api/month-replay/2025-11';
const expected=JSON.parse(readFileSync(resolve(args.reference),'utf8'));

async function listen(requested){
  const server=createServer();
  const port=requested===undefined?0:Number(requested);
  if(!Number.isInteger(port)||port<0||port>65535)throw Error('Invalid private service port.');
  const listening=once(server,'listening');
  server.listen(port,'127.0.0.1');
  await listening;
  return server;
}

async function stop(child){
  if(!child||child.exitCode!==null||child.signalCode!==null)return;
  const exit=once(child,'exit');
  // Windows virtual-environment launchers can delegate to a child Python process.
  execFileSync('taskkill.exe',['/PID',String(child.pid),'/T','/F'],{stdio:'ignore',windowsHide:true});
  await exit;
}

async function ready(url,child){
  for(let attempt=0;attempt<150;attempt++){
    if(child.exitCode!==null)throw Error('Private server exited before listening.');
    try {
      if((await fetch(url,{signal:AbortSignal.timeout(1000)})).ok)return;
    }catch{}
    await delay(100);
  }
  throw Error('Private server did not answer '+url);
}

async function request(origin,route,method='GET'){
  const response=await fetch(origin+route,{method});
  if(!response.ok)throw Error(await response.text());
  return response.json();
}

async function openLab(browser,origin){
  const page=await browser.newPage();
  await page.goto(origin);
  await page.getByRole('button',{name:'Strategy Lab',exact:true}).click();
  const region=page.getByRole('region',{name:'Monthly EURUSD replay'});
  const button=(name)=>region.getByRole('button',{name,exact:true});
  await button('Inspect monthly coverage').click();
  await expect(region).toContainText('2 registered hours');
  return {page,region,button};
}

async function driveUntil({page,button},label,stopAt){
  let halted=false;
  const reached=Promise.withResolvers();
  // Let the real request commit, then block further writes before restarting the service.
  await page.route('**/month-replay/**/advance?*',async route=>{
    if(halted){
      await route.abort().catch(()=>{});
      return;
    }
    try{
      const response=await route.fetch();
      if(!response.ok())throw Error(await response.text());
      const step=await response.json();
      halted=stopAt(step);
      await route.fulfill({response});
      if(halted)reached.resolve({step,index:Number(new URL(route.request().url()).searchParams.get('index'))});
    }catch(error){reached.reject(error)}
  });
  try{
    await button(label).click();
    return await Promise.race([reached.promise,delay(30000,null,{ref:false}).then(()=>{throw Error('Checkpoint wait expired')})]);
  }finally{await page.close()}
}

const boundApi=await listen(process.env.TRADEFORGE_E2E_API_PORT);
let boundUI;
try{boundUI=await listen(process.env.TRADEFORGE_E2E_UI_PORT)}
catch(error){boundApi.close();throw error}
const apiPort=String(boundApi.address().port),uiPort=String(boundUI.address().port);
const apiOrigin='http://127.0.0.1:'+apiPort,uiOrigin='http://127.0.0.1:'+uiPort;
const tempRoot=realpathSync(tmpdir());
const storage=mkdtempSync(join(tempRoot,'tf-month-browser-'));
const env={...process.env,TRADEFORGE_STORAGE:storage,TRADEFORGE_DATA_DIR:join(storage,'research'),
  TRADEFORGE_E2E_API_PORT:apiPort,TRADEFORGE_E2E_UI_PORT:uiPort};
const start=(executable,options,cwd)=>spawn(executable,options,{cwd,env,windowsHide:true,stdio:'ignore'});
const startApi=()=>start(python,['-m','uvicorn','research_backend.tests.recorded_browser_app:app',
  '--host','127.0.0.1','--port',apiPort],root);
const read=(route,method)=>request(apiOrigin,route,method);

let api,ui,browser;
try{
  execFileSync(python,['-m','scripts.month_archive','2025-11','--source',resolve(args.source)],
    {cwd:root,env,windowsHide:true,stdio:['ignore','ignore','inherit']});
  boundApi.close();
  api=startApi();
  await ready(apiOrigin+'/api/health',api);
  boundUI.close();
  ui=start(process.execPath,['node_modules/vite/bin/vite.js','--host','127.0.0.1','--strictPort',
    '--config','e2e/vite.config.ts','--port',uiPort],frontend);
  await ready(uiOrigin,ui);
  browser=await chromium.launch();

  const initial=await read(path);
  assert.equal(initial.session,expected.cold.session);
  assert.equal(initial.checkpoint,null);
  const route=path+'/'+initial.session;
  const inspect=()=>read(path+'?session='+initial.session);
  const checkpoints=[];
  const pids=[api.pid];
  async function restart({step,index}){
    await stop(api);
    api=startApi();
    await ready(apiOrigin+'/api/health',api);
    pids.push(api.pid);
    assert.deepEqual((await inspect()).checkpoint,step);
    const repeat=()=>read(route+'/advance?index='+index,'POST');
    assert.deepEqual(await Promise.all([repeat(),repeat()]),[step,step]);
    assert.deepEqual((await inspect()).checkpoint,step);
    checkpoints.push({index,through_utc:new Date(step.through*1000).toISOString(),
      position:step.state.position,concurrent_duplicate_deliveries:2});
  }

  const entry=await driveUntil(await openLab(browser,uiOrigin),'Run monthly strategy',
    step=>step.events.some(event=>event.kind==='ENTRY'));
  assert.deepEqual(entry.step.state.position.fill,expected.cold.events[1].quote);
  await restart(entry);
  const boundary=await driveUntil(await openLab(browser,uiOrigin),'Resume monthly replay',
    step=>step.through===Date.parse('2025-11-13T14:00:00Z')/1000);
  await restart(boundary);
  assert.equal(new Set(pids).size,3);

  const active=await openLab(browser,uiOrigin);
  await active.button('Resume monthly replay').click();
  await expect(active.region).toContainText('4320/4320 ten-minute intervals',{timeout:30000});
  await expect(active.region).toContainText('Replay ledger complete');
  await expect(active.region).toContainText('2881 recorded open-session gap intervals');
  await expect(active.region).toContainText('Month-level performance metrics are unavailable.');

  const receipts=[];
  for(let after=-1;;){
    const page=await read(route+'/steps?after='+after);
    if(!page.length)break;
    receipts.push(...page);
    after=page.at(-1).index;
  }
  const events=receipts.flatMap(receipt=>receipt.events);
  assert.deepEqual(events,expected.cold.events);
  const ledgerSha256=execFileSync(python,['-c',
    'import hashlib,json,sys; print(hashlib.sha256(json.dumps(json.load(sys.stdin),sort_keys=True).encode()).hexdigest())'],
    {input:JSON.stringify(receipts),encoding:'utf8',windowsHide:true}).trim();
  assert.equal(ledgerSha256,expected.cold.ledger_sha256);
  const quotes=[];
  for(const event of events){
    const summary=active.page.locator('summary',{hasText:new RegExp('^'+event.kind+' \u00b7')});
    const detail=active.region.locator('details').filter({has:summary});
    await detail.locator('summary').click();
    await detail.getByRole('button',{name:'Verify source quote'}).click();
    const output=detail.locator('output');
    await expect(output).toContainText('Verified archive observation');
    const quote=JSON.parse((await output.textContent()).replace('Verified archive observation ',''));
    assert.deepEqual(quote,event.quote);
    quotes.push({kind:event.kind,id:event.id,quote});
  }
  const final=await inspect();
  const report={session:initial.session,distinct_api_processes:pids.length,checkpoints,receipt_count:receipts.length,
    ledger_sha256:ledgerSha256,quote_count:final.checkpoint.quote_count,open_gap_intervals:final.open_gap_intervals,
    month_level_metrics:'UNAVAILABLE',verified_quotes:quotes};
  writeFileSync(resolve(args.output),JSON.stringify(report,null,2)+'\n');
  console.log('Process restart and quote checks passed. Ledger SHA-256 '+ledgerSha256);
}finally{
  boundApi.close();
  boundUI.close();
  if(browser)await browser.close();
  await stop(ui);
  await stop(api);
  if(dirname(realpathSync(storage))===tempRoot)rmSync(storage,{recursive:true,force:true});
}
