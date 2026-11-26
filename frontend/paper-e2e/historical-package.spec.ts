import {expect,test} from '@playwright/test';
import {readFileSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {join} from 'node:path';

const dir=process.env.TRADEFORGE_PAPER_STORAGE!;
const receipts=join(dir,'review-receipts.json');
const restart=process.env.TRADEFORGE_PAPER_PHASE==='package-restart';

test('trace a reviewed replay price change to the recorded executable sides',async({page,request})=>{
  test.setTimeout(120000);
  await page.goto('/');
  await page.getByRole('button',{name:'Strategy Lab',exact:true}).click();
  await page.getByRole('button',{name:'Inspect monthly coverage'}).click();
  const lab=page.getByRole('region',{name:'Monthly EURUSD replay'});
  const panel=page.getByRole('region',{name:'Recorded replay evidence'});
  if(!restart){
    await lab.getByRole('button',{name:'Run monthly strategy'}).click();
    await expect(lab).toContainText('4320/4320 ten-minute intervals',{timeout:90000});
    const exported=[];
    for(const iso of ['2025-11-13T13:31:38.102Z','2025-11-13T13:31:38.103Z']){
      await panel.getByLabel('Evidence cutoff').fill(iso);
      await panel.getByRole('button',{name:'Export recorded evidence'}).click();
      const selector=panel.getByLabel('Saved evidence package');
      await expect(selector.locator('option:checked')).toContainText(iso.slice(0,-1));
      const id=await selector.inputValue();
      const content=await (await request.get('/research/api/ai/packages/'+id+'/recorded')).json();
      exported.push(content);
    }
    expect(exported[0].evidence.metrics).toEqual({});
    expect(exported[0].evidence.events.map((e:{kind:string})=>e.kind)).toEqual(['QUALIFIED','ENTRY']);
    const after=exported[1];
    const response={...after.template,assessment:'SUPPORTED',comment:'Checked recorded ASK and BID.'};
    const raw=Buffer.from(JSON.stringify(response,null,4)+'\r\n');
    await panel.getByLabel('Upload original review file').setInputFiles({name:'local-review.json',mimeType:'application/json',buffer:raw});
    await panel.getByLabel('Operator decision').selectOption('ACCEPTED');
    await panel.getByLabel('Decision rationale').fill('Verified source ordinals 6 and 1564');
    await panel.getByRole('button',{name:'Record operator decision'}).click();
    await expect(panel).toContainText('1 · ACCEPTED · Verified source ordinals 6 and 1564');
    const review=await panel.getByLabel('Imported review').inputValue();
    writeFileSync(receipts,JSON.stringify({exported,review,review_sha256:createHash('sha256').update(raw).digest('hex')}));
  }
  const saved=JSON.parse(readFileSync(receipts,'utf8'));
  const id=saved.exported[1].manifest.package_id;
  if(restart)await panel.getByLabel('Saved evidence package').selectOption(id);
  await expect(panel).toContainText('1 · ACCEPTED · Verified source ordinals 6 and 1564');
  await expect(panel).toContainText('-0.00100 USD per EUR');
  await panel.getByRole('link',{name:'Trace claim -0.00100 USD per EUR'}).click();
  for(const [kind,ordinal] of [['ENTRY',6],['STOP',1564]] as const){
    await panel.getByRole('button',{name:'Verify '+kind+' quote '+ordinal}).click();
    await expect(panel.locator('output')).toContainText('"ordinal":'+ordinal);
  }
  expect(await (await request.get('/research/api/ai/packages/'+id+'/recorded')).json()).toEqual(saved.exported[1]);
  const path='/research/api/ai/reviews/'+saved.review;
  const bytes=await (await request.get(path+'/original')).body();
  expect(createHash('sha256').update(bytes).digest('hex')).toBe(saved.review_sha256);
  const retry=await request.post(path+'/decisions',{data:{expected_sequence:0,decision:'ACCEPTED',rationale:'Verified source ordinals 6 and 1564'}});
  expect((await retry.json()).sequence).toBe(1);
  expect((await (await request.get(path+'/decisions')).json()).decisions).toHaveLength(1);
});
