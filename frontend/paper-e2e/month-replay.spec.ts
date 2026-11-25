import {expect,test} from '@playwright/test';

test('resume a pinned November position in Strategy Lab and inspect its exit quote',async({page})=>{
  await page.goto('/');
  const lab=()=>page.getByRole('button',{name:'Strategy Lab',exact:true}).click();
  await lab();
  const section=page.getByRole('region',{name:'Monthly EURUSD replay'});
  const button=(name:string)=>section.getByRole('button',{name,exact:true});
  await button('Inspect monthly coverage').click();
  await expect(section).toContainText('2 registered hours · 480 missing open hours · 238 scheduled closure hours');
  const filled=page.waitForResponse(async r=>r.url().includes('/advance?index=')&&
    (await r.json()).events?.some((e:{kind:string})=>e.kind==='ENTRY'));
  // Hold the next real request at the browser until the operator has paused.
  let hold=false;
  const gate=Promise.withResolvers<void>();
  await page.route('**/month-replay/**/advance?*',async route=>{
    if(hold)await gate.promise;
    await route.continue();
  });
  await button('Run monthly strategy').click();
  const entry=await (await filled).json();hold=true;
  await button('Pause after batch').click();gate.resolve();
  await expect(button('Resume monthly replay')).toBeEnabled();
  expect(entry.state.position.fill).toMatchObject({ordinal:6,ask_units:116243});
  await page.reload();await lab();await button('Inspect monthly coverage').click();
  await button('Resume monthly replay').click();
  await expect(section).toContainText('4320/4320 ten-minute intervals · 8636 verified observations',{timeout:60000});
  const stop=section.locator('details').filter({has:page.locator('summary',{hasText:/^STOP ·/})});
  await stop.locator('summary').click();
  await expect(stop).toContainText('"price_units": 116143');
  await stop.getByRole('button',{name:'Verify source quote'}).click();
  await expect(stop.locator('output')).toContainText('Verified archive observation');
  await expect(stop).toContainText('"ordinal": 1564');
  await expect(stop).toContainText('2f47bcb14e0a2c4dcd6d65eb819b85f1b5168b656d1f5f67c3dcd2240569f014');
});
