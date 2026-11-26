import {writeFile} from 'node:fs/promises';
import {expect,test,type Locator,type Page,type Route} from '@playwright/test';

const softExpect=expect.configure({soft:true});
const day='2026-11-13';
const epoch=(time:string)=>String(Date.parse(day+'T'+time+':00Z')/1000);
const A='20b2f3eaaaf199141ef695db9a1a53674d31abc1b771ddaed0d014e0467ef940';
const B='5aaf19ac4d08237796dccf3154fc0e0dcc333229e6656ca778424fdbbed54a7b';
const C='81c3aa747549f900f47a3d050fc5facb1ee929560775c55fd525940f93d275d3';
const aConfirmed='c04e4d87c513f365988fa35d9e81f4c67eb80afb603623b550b7d82e012c9ff0';
const bCandidate='cc454f320070e8fa8175503e4a0831f3b1b8496fab0e6ffd5791637e24ab05a2';
const bInvalidated='3366e60877d258ce0291262b38da1ba632854749abd2dbe19718b24380cacc2e';

const region=(page:Page)=>page.getByRole('region',{name:'Recorded SMT at chart cutoff'});
const picker=(page:Page)=>region(page).getByRole('combobox',{name:'Recorded SMT family',exact:true});
const replay=(page:Page)=>page.getByLabel('Historical replay',{exact:true});
const button=(scope:Page|Locator,name:string)=>scope.getByRole('button',{name,exact:true});

type SignalPage={
  items:{event_key:string;status:string;strategy_revision?:{id:string}}[];
  next_cursor:string|null;
};
type ResponseReceipt={
  cutoff:string|null; cursor:string|null; family:string|null; limit:string|null;
  nextCursor:string|null; items:{event:string;state:string;revision:string|null}[];
};
type Delay={delivered:boolean;response?:ResponseReceipt;release:()=>void;gate:Promise<void>};

function delayExactResponse():Delay {
  const gate=Promise.withResolvers<void>();
  return {delivered:false,release:gate.resolve,gate:gate.promise};
}

async function recordResponses(page:Page,responses:ResponseReceipt[],delay:()=>Delay|undefined=()=>undefined){
  await page.route('**/research/api/indicators/signals?*',async(route:Route)=>{
    const url=new URL(route.request().url());
    const {searchParams}=url;
    const family=searchParams.get('event_key');
    // Preserve the actual server cursor, result order, filters and payload.
    if(!family)searchParams.set('limit','2');
    const response=await route.fetch({url:url.href});
    if(!response.ok()){
      await route.fulfill({response});
      return;
    }
    const data:SignalPage=await response.json();
    const receipt={
      cutoff:searchParams.get('as_of'),cursor:searchParams.get('cursor'),family,
      limit:searchParams.get('limit'),nextCursor:data.next_cursor,items:data.items.map(item=>({
        event:item.event_key,state:item.status,revision:item.strategy_revision?.id??null,
      })),
    };
    responses.push(receipt);
    const delayed=family===B&&receipt.cutoff===epoch('13:30')?delay():undefined;
    if(delayed){
      delayed.response=receipt;
      await delayed.gate;
    }
    await route.fulfill({response});
    if(delayed)delayed.delivered=true;
  });
}

async function openArchive(page:Page,primary:'EURUSD'|'GBPUSD',comparison:'EURUSD'|'GBPUSD'){
  await button(page,'Market Terminal').click();
  await page.getByLabel('Market data source',{exact:true}).selectOption('DUKASCOPY_SAVED');
  await page.locator('.market-watchlist button').filter({hasText:primary}).click();
  await page.getByLabel('SMT comparison',{exact:true}).selectOption(comparison);
  await page.getByLabel('From UTC',{exact:true}).fill(day);
  await page.getByLabel('From time (UTC)',{exact:true}).fill('13:00');
  await page.getByLabel('Until UTC (exclusive)',{exact:true}).fill(day);
  await page.getByLabel('Until time (UTC)',{exact:true}).fill('14:00');
  await button(page,'Load saved range').click();
  await expect(button(page,'Bar replay')).toBeEnabled();
}

async function seek(page:Page,time:string){
  await replay(page).getByLabel('Jump to UTC',{exact:true}).fill(day+'T'+time);
  const browse=page.waitForResponse(response=>{
    const {pathname,searchParams}=new URL(response.url());
    return pathname.endsWith('/indicators/signals')&&searchParams.get('as_of')===epoch(time)&&
      !searchParams.has('cursor')&&!searchParams.has('event_key');
  });
  await button(replay(page),'Seek').click();
  await (await browse).finished();
  await expect(replay(page).locator('output')).toContainText(day+'T'+time+':00.000Z');
  await expect(region(page)).toContainText('Chart cutoff '+day+' '+time+':00 UTC');
}

async function observe(page:Page){
  return region(page).evaluate(node=>({
    selected:node.querySelector('select')?.value??null,
    options:Array.from(node.querySelectorAll('select option'),option=>(option as HTMLOptionElement).value),
    receipts:Array.from(node.querySelectorAll('[data-event-key]'),row=>({
      event:row.getAttribute('data-event-key'),revision:row.getAttribute('data-revision-id'),
      state:row.getAttribute('data-status'),visibleId:row.querySelector('p code')?.textContent??null,
    })),
    text:node.innerText,
  }));
}

test('a selected recorded SMT family survives cutoff, paging, stale responses and workspace reopening',async({page,context,browser},testInfo)=>{
  test.setTimeout(90000);
  const responses:ResponseReceipt[]=[];
  const checkpoints:object[]=[];
  let delayed:Delay|undefined;
  await recordResponses(page,responses,()=>delayed);
  const state=(event:string,revision:string,state:string)=>({
    receipts:[{event,revision,state,visibleId:revision}],
  });
  const pinned=(revision:string,status:string)=>({selected:B,...state(B,revision,status)});
  const absent={selected:B,receipts:[],text:expect.stringContaining('No recorded revision for the selected family at this cutoff.')};
  const verify=async(p:Page,step:string,expected:object)=>{
    await softExpect.poll(()=>observe(p),{message:step,timeout:2300}).toMatchObject(expected);
    const observed=await observe(p);
    checkpoints.push({step,observed});
  };

  try{
    await page.goto('/?timezone=UTC');
    await openArchive(page,'EURUSD','GBPUSD');
    const created=page.waitForResponse(r=>r.url().endsWith('/replay/sessions')&&r.request().method()==='POST');
    await button(page,'Bar replay').click();
    const session=await created;
    expect(session.status(),await session.text()).toBe(201);
    const {id}=await session.json();
    await seek(page,'13:25');
    const initial=responses.findLast(r=>r.cutoff===epoch('13:25')&&!r.cursor&&!r.family)!;
    expect(initial.items.map(row=>row.event)).toEqual([A,B]);
    expect(initial.nextCursor).toBeTruthy();
    await verify(page,'13:25 automatic preview displays A',state(A,aConfirmed,'SMT_CONFIRMED'));

    await picker(page).selectOption(B);
    await verify(page,'13:25 B selected once',pinned(bCandidate,'SMT_CANDIDATE'));
    await seek(page,'13:20');
    const early=responses.findLast(r=>r.cutoff===epoch('13:20')&&!r.cursor&&!r.family)!;
    expect(early.items.map(row=>row.event)).toEqual([A,C]);
    await verify(page,'13:20 rewind before B, with A eligible',absent);

    await seek(page,'13:25');
    await verify(page,'13:25 return to the same B candidate revision',pinned(bCandidate,'SMT_CANDIDATE'));
    const restoredReceipt=region(page).locator(`[data-event-key="${B}"]`);
    const archivePins=[
      {label:'EURUSD · chart primary · BID',id:'ca01e9ac45346d7c9fdfdf4263085831a07735bc16158901c9a14187f77aaf4e',
        sha256:'fa2af192aa86dc41bb20df51dffe95c29bc48cdf2fe38b643f699985c6b0e09d'},
      {label:'GBPUSD · chart comparison · BID',id:'1cc6acc1b358b3b043438badc69f72413827a8cd5dad379de8e41dd9aaaecfd0',
        sha256:'aed73f09d78bb3df29d3678e0ace1f00949a673a920ed22dcd6d23cb7a173d02'},
    ];
    const observedPins=[];
    for(const pin of archivePins){
      const summary=restoredReceipt.getByText(pin.label,{exact:true});
      await summary.click();
      const details=summary.locator('..');
      await expect(details.getByText(pin.id,{exact:true})).toBeVisible();
      await expect(details.getByText(pin.sha256,{exact:true})).toBeVisible();
      observedPins.push(await details.innerText());
    }
    checkpoints.push({step:'13:25 restored B displays both immutable archive pins',observedPins});
    await button(region(page),'Older families').click();
    await expect(button(region(page),'First page')).toBeVisible();
    const older=responses.findLast(r=>r.cutoff===epoch('13:25')&&r.cursor&&!r.family)!;
    expect(older.items[0].event).toBe(C);
    expect(older.items.map(row=>row.event)).not.toContain(B);
    await verify(page,'13:25 older page browses C and retains the B receipt',pinned(bCandidate,'SMT_CANDIDATE'));
    await expect(picker(page).locator(`option[value="${C}"]`)).toHaveCount(1);
    await button(region(page),'First page').click();
    await expect(button(region(page),'Older families')).toBeVisible();
    await verify(page,'13:25 first page retains the B receipt',pinned(bCandidate,'SMT_CANDIDATE'));

    await seek(page,'13:30');
    await verify(page,'13:30 advance resolves the B invalidated revision',pinned(bInvalidated,'SMT_INVALIDATED'));
    delayed=delayExactResponse();
    await button(region(page),'Older families').click();
    await softExpect.poll(()=>delayed?.response?.items.map(row=>[row.event,row.revision]),
      {message:'older page requests B by key at 13:30',timeout:2300}).toEqual([[B,bInvalidated]]);

    await seek(page,'13:20');
    await verify(page,'13:20 newer visit resolves before the delayed 13:30 response',absent);
    if(delayed.response){
      const stale=page.waitForResponse(r=>{
        const {searchParams}=new URL(r.url());
        return searchParams.get('as_of')===epoch('13:30')&&searchParams.get('event_key')===B;
      });
      delayed.release();
      await (await stale).finished();
      await expect.poll(()=>delayed?.delivered).toBe(true);
      await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
    }
    await verify(page,'13:20 delayed 13:30 response leaves B unavailable',absent);
    await seek(page,'13:25');
    await verify(page,'13:25 after stale response returns to B candidate',pinned(bCandidate,'SMT_CANDIDATE'));

    await button(replay(page),'Save replay position').click();
    await expect(replay(page).getByText('Replay position saved',{exact:true})).toBeVisible();
    const baseURL=new URL(page.url()).origin;
    const storageState=await context.storageState();
    await page.close();
    const reopened=await browser.newContext({baseURL,storageState});
    try{
      const fresh=await reopened.newPage();
      await recordResponses(fresh,responses);
      await fresh.goto('/?timezone=UTC');
      await openArchive(fresh,'EURUSD','GBPUSD');
      await fresh.getByRole('combobox',{name:'Resume saved replay',exact:true}).selectOption(id,{timeout:5000});
      await expect(replay(fresh).locator('output')).toContainText(day+'T13:25:00.000Z');
      await verify(fresh,'fresh Chromium context reopens B at the saved cutoff',pinned(bCandidate,'SMT_CANDIDATE'));

      await button(replay(fresh),'Exit replay').click();
      await openArchive(fresh,'GBPUSD','EURUSD');
      await button(fresh,'Bar replay').click();
      await seek(fresh,'13:25');
      await verify(fresh,'swapped GBPUSD/EURUSD scope uses the automatic A preview',{selected:'',...state(A,aConfirmed,'SMT_CONFIRMED')});

      await button(replay(fresh),'Exit replay').click();
      await openArchive(fresh,'EURUSD','GBPUSD');
      await fresh.getByRole('combobox',{name:'Resume saved replay',exact:true}).selectOption(id,{timeout:5000});
      await expect(replay(fresh).locator('output')).toContainText(day+'T13:25:00.000Z');
      await verify(fresh,'returning to EURUSD/GBPUSD restores only its B pin',pinned(bCandidate,'SMT_CANDIDATE'));
    }finally{
      await reopened.close();
    }
  }finally{
    delayed?.release();
    const path=testInfo.outputPath('recorded-smt-identity.json');
    await writeFile(path,JSON.stringify({
      checkpoints,responses,delayed:delayed&&{response:delayed.response,delivered:delayed.delivered},
    },null,2)+'\n');
    await testInfo.attach('recorded-smt-identity',{path,contentType:'application/json'});
  }
});
