import {expect,test} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';

test('inspect an ambiguous upload and attach a clean review to the original receipt',async({page})=>{
  const old=JSON.parse(readFileSync(join(process.env.TRADEFORGE_PAPER_STORAGE!,'old-reviews.json'),'utf8'));
  await page.goto('/');
  await page.getByRole('button',{name:'Strategy Lab',exact:true}).click();
  await page.getByRole('button',{name:'Inspect monthly coverage'}).click();
  const panel=page.getByRole('region',{name:'Recorded replay evidence'});
  await panel.getByLabel('Saved evidence package').selectOption(old.review.package_id);
  await panel.getByLabel('Imported review').selectOption(old.affected);
  await expect(panel.getByRole('alert')).toContainText('duplicate JSON key');
  await expect(panel.getByRole('link',{name:/Trace claim/})).toHaveCount(0);
  await expect(panel).toContainText('1 · NEEDS_FOLLOW_UP · Prior operator decision');
  await panel.getByRole('button',{name:'Prepare linked correction'}).click();
  const input=panel.getByLabel('Local review JSON');
  const fresh=JSON.parse(await input.inputValue());
  expect(fresh.corrects_review_id).toBe(old.affected);
  fresh.comment=old.review.comment;
  await input.fill(JSON.stringify(fresh));
  await panel.getByRole('button',{name:'Import recorded review'}).click();
  await expect(panel).toContainText('Corrects original '+old.affected);
  await expect(panel.getByRole('link',{name:'Trace claim -0.00100 USD per EUR'})).toBeVisible();
  expect(await panel.getByLabel('Imported review').inputValue()).not.toBe(old.affected);
  await panel.getByLabel('Imported review').selectOption(old.clean);
  await expect(panel.getByRole('alert')).toHaveCount(0);
  await expect(panel).toContainText('1 · NEEDS_FOLLOW_UP · Prior operator decision');
});
