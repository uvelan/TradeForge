import {expect,test} from '@playwright/test';
import {resolve} from 'node:path';

test('inspect a redacted period, its reversal and corrected source rows after reload',async({page})=>{
  await page.goto('/');
  const open=()=>page.getByRole('button',{name:'Data Library',exact:true}).click();
  await open();
  const input=page.getByLabel('Redacted statement',{exact:true});
  await input.setInputFiles(resolve('public/examples/controlled-statement.json'));
  await page.getByRole('button',{name:'Reconcile next batch'}).click();
  await expect(page.getByRole('heading',{name:'RECONCILED · USD'})).toBeVisible();
  await expect(page.getByRole('row',{name:'Calculated balance 1079.00'})).toBeVisible();
  await page.getByText('F4 · SELL · balance 109975 minor units',{exact:true}).click();
  await expect(page.locator('details[open]')).toContainText('"native_realized": "-2"');
  const selector=page.getByLabel('Reporting period and revision');
  const original=await selector.inputValue();
  await page.reload();await open();
  await selector.selectOption(original);
  await expect(page.getByRole('row',{name:'Calculated equity 1080.00'})).toBeVisible();
  const s=await (await page.request.get('/examples/controlled-statement.json')).json();
  s.closing_balance='1079.01';
  await input.setInputFiles({name:'controlled-correction.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(s))});
  await page.getByRole('button',{name:'Reconcile next batch'}).click();
  await expect(page.getByRole('row',{name:'Balance difference -0.01'})).toBeVisible();
  await expect(page.getByText('closing_balance · BALANCE_MISMATCH')).toBeVisible();
  await page.getByText('Statement fingerprint, source evidence and revision changes').click();
  await expect(page.locator('details[open]')).toContainText('"after": "1079.01"');
});
