import {expect,test} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';

test('Strategy Lab checkpoints and reopens the recorded EURUSD historical session',async({page,request})=>{
  test.setTimeout(90000);
  const api='/research/api';
  const ids:string[]=[];
  for(const hour of [13,14]){
    const response=await request.post(api+'/market/import/reviewed',{multipart:{file:{
      name:hour+'h_ticks.bi5',mimeType:'application/octet-stream',buffer:readFileSync(resolve('../storage/t06-input/'+hour+'h_ticks.bi5'))}}});
    expect(response.status(),await response.text()).toBe(200);
    const {partitions}=await response.json();
    ids.push(partitions.find((p:{timeframe?:string;price_basis?:string})=>p.timeframe==='M5'&&p.price_basis==='BID').id);
  }
  const saved=await request.post(api+'/strategies/v1',{data:JSON.parse(readFileSync(resolve('../docs/implementation/plan-integration-evidence/REPLAY-FVG-DEFINITION.json'),'utf8'))});
  expect(saved.status(),await saved.text()).toBe(201);
  const version=await saved.json();
  async function lab(){
    await page.goto('/');await page.getByRole('button',{name:'Strategy Lab',exact:true}).click();
    return page.getByRole('region',{name:'Historical strategy evaluation'});
  }
  const section=await lab();
  await section.getByLabel('Strategy version').selectOption(version.version_id);
  await section.getByLabel('Frozen source').selectOption('WORKSPACE');
  await section.getByLabel('Workspace partition IDs').fill(ids.join(' '));
  await section.getByLabel('Warm-up start (UTC)').fill('2025-11-13T13:00:00');
  await section.getByLabel('Evaluation start (UTC)').fill('2025-11-13T14:00:00');
  await section.getByLabel('Evaluation end (UTC)').fill('2025-11-13T15:00:00');
  await section.getByRole('button',{name:'Queue historical evaluation'}).click();
  const closing=section.getByRole('region',{name:'Recorded quote replay'});
  await closing.getByLabel('Replay through (UTC)').fill('2025-11-13T14:30:00');
  await closing.getByRole('button',{name:'Save replay checkpoint'}).click();
  await expect(closing).toContainText('Saved through 2025-11-13T14:30:00.000Z');
  const id=await section.getByLabel('Saved historical run').inputValue();
  const again=await lab();
  await again.getByLabel('Saved historical run').selectOption(id);
  await expect(closing).toContainText('Saved through 2025-11-13T14:30:00.000Z');
  await closing.getByRole('button',{name:'Resume recorded replay'}).click();
  await expect(closing).toContainText('8636 quotes · 0 qualified intents');
  await again.getByRole('button',{name:'Inspect historical decision'}).click();
  await expect(again).toContainText('invalidated');
});
