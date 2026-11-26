import {expect,test} from '@playwright/test';

test('reopen a falsely reconciled V1 source and inspect the original verdict',async({page,request})=>{
  const versions=await (await request.get('/research/api/statements')).json() as {version:number;id:string}[];
  const gap=versions.find(v=>v.version===7)!.id;
  const truncated=versions.find(v=>v.version===2)!.id;
  await page.goto('/');
  await page.getByRole('button',{name:'Data Library',exact:true}).click();
  const select=page.getByLabel('Reporting period and revision');
  await select.selectOption(gap);
  await expect(page.getByRole('heading',{name:'UNRESOLVED · USD'})).toBeVisible();
  await expect(page.getByText('EURUSD · MISSING_OR_OVERLAPPING_DATED_SPEC')).toBeVisible();
  await page.getByText('Inspect original V1 verdict · RECONCILED').click();
  await expect(page.locator('details[open] pre')).toContainText('"margin_minor": 1098');
  await select.selectOption(truncated);
  await expect(page.getByRole('alert').filter({hasText:'Current assessment unavailable'})).toContainText('00:00:00.0000001Z');
  await expect(page.getByRole('heading',{name:'RECONCILED · USD'})).toHaveCount(0);
  await page.reload();
  await page.getByRole('button',{name:'Data Library',exact:true}).click();
  await select.selectOption(gap);
  await expect(page.getByRole('heading',{name:'UNRESOLVED · USD'})).toBeVisible();
});
