import {expect, test} from '@playwright/test';

test('data library controls capture and labels frozen recorded readiness', async ({page},info) => {
  let state = 'STOPPED';
  let blocked = false;
  let stale = false;
  const start = 1735689600;
  const available = {
    state:'RUNNING',timeframe:'M5',clock_policy:'LOCAL_UTC_MONOTONIC_GUARD_V1',
    scopes:{EURUSD:{state:'RUNNING',feed_id:'capture-test',price_basis:'BID',timestamp_policy:'UTC_EPOCH_VERIFIED_V1',
      coverage_start:start,coverage_end:start+300,last_candle:start,last_persisted_ms:(start+301)*1000,recorded:1}},
  };
  const blockedCapture = {
    state:'BLOCKED',timeframe:'M5',scopes:{EURUSD:{state:'BLOCKED',timestamp_policy:'UNVERIFIED_TIMESTAMP_DOMAIN'}},
  };
  const manifest = {id:'frozen-recorded',symbol:'EURUSD',provider:'MT5',feed_id:'capture-test',
    price_basis:'BID',timeframe:'M5',calendar:{kind:'forex_weekly'},start,end:start+300,cutoff:start+302,
    partitions:[{id:'partition-recorded',sha256:'a'.repeat(64)}],availability_policy:'SERVER_RECORDED_ARRIVAL_V2'};
  const minute = {...manifest,id:'source-m1',timeframe:'M1'};
  const aggregated = {...manifest,id:'derived-m5',aggregation_policy:'RECORDED_M1_TO_M5_V2',
    aggregation_sources:[{id:minute.id,cutoff:minute.cutoff}],data_quality:{corrections:1}};
  let frozen = false;
  await page.route('**/research/api/market/arrival/manifests/source-m1/aggregate-m5',route=>
    stale ? route.fulfill({status:409,json:{detail:'STALE_OR_INCOMPARABLE_AGGREGATE_SOURCE'}})
          : route.fulfill({json:aggregated}));
  await page.route('**/research/api/market/arrival/capture',route=>
    route.fulfill({json:state==='STOPPED'?{state,scopes:{}}:blocked?blockedCapture:available}));
  await page.route('**/research/api/market/arrival/capture/start',route=>{
    expect(route.request().postDataJSON()).toMatchObject({symbols:['EURUSD','GBPUSD'],timeframe:'M5'});
    state='RUNNING';
    return route.fulfill({json:blocked?blockedCapture:available});
  });
  await page.route('**/research/api/market/arrival/capture/stop',route=>{
    state='STOPPED';
    return route.fulfill({json:{state,scopes:{}}});
  });
  await page.route('**/research/api/market/arrival/manifests',route=>{
    if(route.request().method()==='POST'){
      expect(route.request().postDataJSON()).toMatchObject({symbol:'EURUSD',start,end:start+300});
      frozen=true;
      return route.fulfill({json:manifest});
    }
    return route.fulfill({json:frozen?[manifest,minute]:[minute]});
  });
  await page.route('**/research/api/market/arrival/readiness',route=>{
    const body = route.request().postDataJSON();
    expect(body.primary.arrival_manifest).toBe('frozen-recorded');
    expect(body.primary).not.toHaveProperty('availability_policy');
    expect(body.cutoff).toBe(start+302);
    return route.fulfill({json:{primary:{symbol:'EURUSD',status:'READY_RECORDED',modes:{
      STRICT_RECORDED:{status:'COMPLETE',eligible_candles:1,missing_slots:0,blocking_reason:null},
    }}}});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Data Library',exact:true}).click();
  await expect(page.getByRole('combobox',{name:'Availability mode'})).toHaveValue('LEGACY_RESEARCH');
  await page.getByRole('button',{name:'Start read-only candle capture'}).click();
  await expect(page.getByText('Receipt committed by',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Freeze EURUSD coverage'}).click();
  await expect(page.getByRole('combobox',{name:'Availability mode'})).toHaveValue('STRICT_RECORDED');
  await page.getByRole('button',{name:'Assess data readiness'}).click();
  await expect(page.getByText('EURUSD · READY_RECORDED')).toBeVisible();
  await expect(page.getByText(/LOCAL_UTC_MONOTONIC_GUARD_V1/)).toBeVisible();
  await expect(page.getByText(/Verified provider time UTC_EPOCH_VERIFIED_V1/)).toBeVisible();
  await page.screenshot({path:info.outputPath('arrival-readiness.png')});
  await page.getByLabel('Primary frozen evidence').selectOption(minute.id);
  await page.getByRole('button',{name:'Publish recorded M5'}).click();
  await expect(page.getByLabel('Primary frozen evidence')).toHaveValue(aggregated.id);
  await page.getByText('Aggregate revision lineage').click();
  await expect(page.getByText(/source-m1 · source cutoff/)).toBeVisible();
  await page.screenshot({path:info.outputPath('aggregate-lineage.png')});
  stale = true;
  await page.getByLabel('Primary frozen evidence').selectOption(minute.id);
  await page.getByRole('button',{name:'Publish recorded M5'}).click();
  await expect(page.getByRole('alert')).toContainText('stale or incomparable revisions');
  await page.getByRole('button',{name:'Stop candle capture'}).click();
  await expect(page.getByRole('button',{name:'Start read-only candle capture'})).toBeVisible();
  await page.getByRole('combobox',{name:'Availability mode'}).selectOption('HISTORICAL_SIMULATION');
  await expect(page.getByLabel('Primary modeled delay (seconds)')).toBeVisible();
  await expect(page.getByText('EURUSD · READY_RECORDED')).toHaveCount(0);
  blocked = true;
  await page.getByRole('button',{name:'Start read-only candle capture'}).click();
  await expect(page.getByText('EURUSD · BLOCKED')).toBeVisible();
  await expect(page.getByText(/broker timestamp domain needs verification/)).toBeVisible();
  await expect(page.getByRole('button',{name:'Freeze EURUSD coverage'})).toHaveCount(0);
  await page.screenshot({path:info.outputPath('arrival-blocked.png')});
});
