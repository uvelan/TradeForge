import {expect,test} from '@playwright/test';
import {createHash} from 'node:crypto';

test('the genuine pre-fix receipt reopens with its timing discrepancy and original image',async({page,request})=>{
  const id='2ed01141c01ec11c4b56eadd377867b6f806f138b4f8d77bb3f8fa3a36f3f8ef';
  const url='/research/api/chart-snapshots/'+id;
  const response=await request.get(url);
  expect(response.status()).toBe(200);
  const meta=await response.json();
  expect(meta).toMatchObject({id,cutoff:1794541199,signal_transition_at:1794541200,chart_context:'FROZEN'});
  const created=await request.post('/research/api/journal',{data:{
    kind:'CHART',title:'Historical revision timing',content:'Inspect the original frozen receipt.',tags:[],snapshot_id:id,
    signal_reference:meta.signal_reference}});
  expect(created.status(),await created.text()).toBe(201);
  await page.goto('/');
  await page.getByRole('button',{name:'Learning Journal',exact:true}).click();
  const entry=page.locator('.journal-item').filter({hasText:'Historical revision timing'}).first();
  await entry.getByText('Chart capture details',{exact:true}).click();
  await expect(entry.getByRole('alert')).toContainText('This signal revision occurred after the saved chart cutoff');
  await expect(entry.locator('details')).toContainText(new Date(meta.cutoff*1000).toISOString());
  await expect(entry.locator('details')).toContainText(new Date(meta.signal_transition_at*1000).toISOString());
  const link=entry.getByRole('link',{name:'Open chart capture'});
  await expect(link).toHaveAttribute('href',url+'/image');
  const image=await request.get(url+'/image');expect(image.status()).toBe(200);
  expect(createHash('sha256').update(await image.body()).digest('hex')).toBe('eaa4a94ea300e0d2c775968cbe42f0b5b51ceafdeb73d64e9efddf6d4e880865');
  expect(await (await request.get(url)).json()).toEqual(meta);
});
