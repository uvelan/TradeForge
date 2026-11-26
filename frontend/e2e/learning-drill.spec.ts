import { expect, test } from '@playwright/test';

test('a blind SMT answer is revealed after reasoning is saved and linked to the journal', async ({page}) => {
  const openJournal = async () => {
    await page.goto('/');
    await page.getByRole('button', {name:'Learning Journal', exact:true}).click();
  };
  await openJournal();
  await expect(page.getByRole('heading', {name:'Blind SMT drill: when is the divergence known?'})).toBeVisible();
  await expect(page.getByRole('rowheader', {name:'10:05'})).toHaveCount(0);
  const submit = page.getByRole('button', {name:'Submit hypothesis and reveal'});
  await page.getByRole('radio', {name:'Yes, this is confirmed SMT.'}).check();
  await expect(submit).toBeDisabled();
  const hypothesis = 'The EURUSD high exceeded the preceding high.';
  await page.getByRole('textbox', {name:/Your reasoning/}).fill(hypothesis);
  await submit.click();
  await expect(page.getByText('Needs review', {exact:true})).toBeVisible();
  await expect(page.getByRole('rowheader', {name:'10:05'})).toBeVisible();
  const link = page.getByRole('button', {name:/Open journal entry CHART-/});
  const journalLabel = await link.textContent();
  expect(journalLabel).toMatch(/CHART-\d{5}/);
  await openJournal();
  await expect(page.getByText('Your recorded hypothesis: '+hypothesis, {exact:true})).toBeVisible();
  const restoredLink = page.getByRole('button', {name:journalLabel!});
  await restoredLink.click();
  await expect(page.getByRole('heading', {name:/CHART-\d+ · Blind SMT drill/})).toBeVisible();
});
