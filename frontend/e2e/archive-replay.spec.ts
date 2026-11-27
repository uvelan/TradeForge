import {expect, test, type Page} from '@playwright/test';
import type {Candle, ReplaySelection, ReplaySession, Timeframe} from '../src/types';

const start = Date.parse('2026-01-05T06:00:00Z') / 1000;
const partition = {id:'frozen-test-partition',sha256:'f'.repeat(64)};
const stamp = (time: number) => new Date(time * 1000).toISOString();

function capture(rangeStart = start): ReplaySession {
  const bar = (minute: number, release: number, price: number): Candle => ({
    time:rangeStart+minute*60, available_at:rangeStart+release*60,
    open:price,high:price+.001,low:price-.001,close:price+.0001,
    availability_basis:'ARCHIVE_SUPPLIED',source_partition_indexes:[0],
  });
  const metadata = {
    source:'MT5_SAVED' as const, synthetic:false, feed_id:'fixture-feed', price_basis:'BID',
    partitions:[partition], latest_unconfirmed:false,
    coverage:{start:rangeStart,end:rangeStart+900,gaps:[],duplicate_bars:0,truncated:false},
  };
  const candles = [bar(0,15,1.13),bar(5,10,1.14),bar(10,15,1.15)];
  return {
    id:'archive-'+rangeStart,
    request:{start:rangeStart,end:rangeStart+900,window_bars:5000},
    market:{...metadata,candles,replay_cutoffs:[rangeStart+600,rangeStart+900,rangeStart+900]},
    views:{
      '2':{...metadata,symbol:'GBPUSD',timeframe:'M5',candles:[bar(0,5,1.30),bar(10,15,1.31)],
        coverage:{...metadata.coverage,gaps:[{start:rangeStart+300,end:rangeStart+600}]}},
      '3':{symbol:'USDJPY',timeframe:'M5',error:'No verified archive for this pane'},
      '4':{...metadata,symbol:'XAUUSD',timeframe:'M5',candles:[bar(0,5,2013),bar(5,15,2014),bar(10,15,2015)]},
    },
    checkpoint:null,
  };
}

async function openArchive(page: Page) {
  await page.addInitScript(() => {
    for (const [setting,value] of Object.entries({
      source:'MT5_SAVED', timeframe:'M5','chart-views':'4','link-replay':'off',
      'view-2-symbol':'GBPUSD','view-3-symbol':'USDJPY','view-4-symbol':'XAUUSD',
      'secondary-timeframe':'M5','view-3-frame':'M5','view-4-frame':'M5',
    })) localStorage.setItem('tradeforge.workspace.'+setting,value);
  });
  await page.route('**/research/api/market/candles?*', route => route.fulfill({
    status:404,json:{detail:'Archive preview unavailable'},
  }));
  await page.goto('/');
  await page.getByRole('button',{name:'Market Terminal',exact:true}).press('Enter');
}

async function openRange(page:Page, day = '2026-01-05') {
  await page.getByLabel('From UTC',{exact:true}).fill(day);
  await page.getByLabel('Until UTC (exclusive)',{exact:true}).fill(day);
  await page.getByLabel('From time (UTC)',{exact:true}).fill('06:00');
  await page.getByLabel('Until time (UTC)',{exact:true}).fill('06:15');
  await page.getByRole('button',{name:'Load saved range'}).press('Enter');
}

test('inspects and restores the selected frozen candle without exposing out-of-cutoff bars',async({page})=>{
  const session = capture();
  const captured = Promise.withResolvers<void>();
  const respond = Promise.withResolvers<void>();
  await page.route('**/research/api/replay/sessions', async route=>{
    const body = route.request().postDataJSON();
    captured.resolve();
    expect(body).toMatchObject({
      start,end:start+900,window_bars:5000,
      views:[
        {view:'2',symbol:'GBPUSD',timeframe:'M5' satisfies Timeframe},
        {view:'3',symbol:'USDJPY',timeframe:'M5' satisfies Timeframe},
        {view:'4',symbol:'XAUUSD',timeframe:'M5' satisfies Timeframe},
      ],
    });
    await respond.promise;
    await route.fulfill({status:201,json:session});
  });
  await page.route('**/research/api/replay/sessions?*',route=>route.fulfill({
    json:session.checkpoint?[{id:session.id,position:session.checkpoint.position,updated_at:stamp(start)}]:[],
  }));
  await page.route('**/replay/sessions/'+session.id,route=>route.fulfill({json:session}));
  await page.route('**/replay/sessions/'+session.id+'/checkpoint',route=>{
    session.checkpoint=route.request().postDataJSON();
    const expected:{position:number;bookmarks:number[];selected:ReplaySelection}={
      position:0,bookmarks:[],selected:{view:'4',time:start+300},
    };
    expect(session.checkpoint).toEqual(expected);
    return route.fulfill({json:{ok:true}});
  });
  await openArchive(page);
  await openRange(page);
  const freeze=page.getByRole('button',{name:'Freeze range for replay'});
  await expect(freeze).toBeEnabled();
  try {
    await freeze.press('Enter');
    await captured.promise;
    await expect(freeze).toBeDisabled();
    await expect(page.getByText('Freezing selected range…')).toBeVisible();
  } finally { respond.resolve(); }

  const coverage=page.getByRole('region',{name:'Frozen replay coverage'});
  await expect(coverage.getByText('Partial coverage · 1 UTC gap')).toBeVisible();
  await expect(coverage.getByText('No verified archive for this pane')).toBeVisible();
  await expect(page.getByRole('region',{name:'Chart view 3'})).toContainText('No verified archive');
  const picker=page.getByRole('combobox',{name:'Inspection pane'});
  await expect(picker).toBeFocused();
  await picker.press('End');
  await expect(picker).toHaveValue('4');
  const slider=page.getByRole('slider',{name:'Inspect candle'});
  await slider.press('ArrowLeft');
  await expect(slider).toHaveAttribute('aria-valuetext',stamp(start+300));
  const selected=page.getByRole('region',{name:'Selected candle',exact:true});
  await expect(selected.getByLabel('Selected candle OHLC')).toContainText('2014.000');
  await selected.getByText('Selected candle source',{exact:true}).press('Enter');
  await expect(selected.getByText('SHA-256 '+partition.sha256)).toBeVisible();

  const controls=page.getByLabel('Historical replay',{exact:true});
  await controls.getByRole('button',{name:'Restart',exact:true}).press('Enter');
  await expect(picker).toHaveValue('4');
  await expect(selected).toContainText('The selected candle is beyond the replay cutoff.');
  await expect(selected.getByLabel('Selected candle OHLC')).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Primary chart',exact:true}).getByLabel('Chart data')).toContainText('1.14010');
  await controls.getByRole('button',{name:'Save replay position'}).press('Enter');
  await expect(controls.getByText('Replay position saved')).toBeVisible();

  await page.reload();
  await page.getByRole('combobox',{name:'Resume saved replay'}).press('End');
  await expect(picker).toHaveValue('4');
  await expect(selected).toContainText('The selected candle is beyond the replay cutoff.');
  await controls.getByRole('button',{name:'Last candle'}).press('Enter');
  await expect(slider).toHaveAttribute('aria-valuetext',stamp(start+300));
  await expect(selected.getByLabel('Selected candle OHLC')).toContainText('2014.000');
});

test('discards an old capture after the operator leaves and returns to the same scope',async({page})=>{
  const session = capture();
  const requested = Promise.withResolvers<void>();
  const publish = Promise.withResolvers<void>();
  await page.route('**/research/api/replay/sessions',async route=>{
    requested.resolve();
    await publish.promise;
    await route.fulfill({status:201,json:session});
  });
  await openArchive(page);
  await openRange(page);
  const freeze=page.getByRole('button',{name:'Freeze range for replay'});
  try{
    await freeze.press('Enter');
    await requested.promise;
    const layout=page.getByRole('combobox',{name:'Chart layout'});
    await layout.press('Home');
    await expect(layout).toHaveValue('1');
    await layout.press('End');
    await expect(layout).toHaveValue('4');
    await expect(freeze).toBeEnabled();
  }finally {publish.resolve();}
  await page.unrouteAll({behavior:'wait'});
  await expect(page.getByLabel('Frozen replay coverage')).toHaveCount(0);
  await expect(freeze).toBeEnabled();

  await page.route('**/research/api/replay/sessions',route=>route.fulfill({
    status:422,json:{detail:'Select a replay range of at most 5,000 bars per pane.'},
  }));
  await freeze.press('Enter');
  await expect(page.getByRole('alert').filter({hasText:'Select a replay range of at most 5,000 bars per pane.'})).toBeVisible();
  await expect(freeze).toBeEnabled();
});
