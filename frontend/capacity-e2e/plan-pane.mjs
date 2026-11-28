import assert from 'node:assert/strict';
import {expect} from '@playwright/test';

// Exercise the decision after closing and reopening it, before any price is placed.
export async function keepManualDecision(page,shot){
  const button=name=>page.getByRole('button',{name,exact:true});
  const pane=page.getByLabel('Inspection pane');
  const time=page.getByLabel('Selected candle',{exact:true}).locator('time').first();
  const state=async()=>({pane:await pane.inputValue(),open:await time.getAttribute('datetime'),
    position:await page.getByLabel('Replay timeline').inputValue()});
  const before=await state(),writes=[];
  assert.deepEqual(before,{pane:shot.decision_replay.view,open:new Date(shot.decision_replay.time*1000).toISOString(),
    position:String(shot.decision_replay.position)});
  const observe=r=>{if(r.method()!=='GET'&&r.url().includes('/research/api/'))writes.push(r.url());};
  page.on('request',observe);
  try{
    await page.keyboard.press('Escape');await button('Record decision').click();
    const draft=page.getByLabel('Reason to pass');
    await draft.fill('Preserve this GBPUSD observation');
    const picker=button('Select plan levels');
    const box=await picker.boundingBox();assert.ok(box);
    await page.mouse.click(box.x+box.width/2,box.y+box.height/2);
    const after=await state();
    console.log('Plan picker',JSON.stringify({capture:shot.id,cutoff:shot.cutoff,before,after}));
    assert.deepEqual(after,before);
    await expect(picker).toBeDisabled();
    await expect(button('Create trade plan')).toHaveAccessibleDescription(/Enter GBPUSD levels in the trade plan/);
    await page.getByRole('button',{name:'Close Record decision'}).focus();
    await page.keyboard.press('Tab');await expect(button('Create trade plan')).toBeFocused();
    await expect(draft).toHaveValue('Preserve this GBPUSD observation');
    await expect(page.getByText('Select entry',{exact:true})).toHaveCount(0);
    assert.deepEqual(writes,[]);
  }finally{page.off('request',observe);}
}
