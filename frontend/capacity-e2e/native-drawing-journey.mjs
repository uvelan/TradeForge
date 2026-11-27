import assert from 'node:assert/strict';
import {expect} from '@playwright/test';

const selector = view => view === 'primary' ? '.primary-chart .chart-input'
  : '[aria-label="Chart view ' + view + '"] .chart-input';
const scopeOf = (details, view) => [
  details.request.source, details.panes[view].symbol, details.panes[view].timeframe,
].join(':');
const endpoint = scope => '/research/api/drawings?' + new URLSearchParams({scope});
const isWrite = res => res.request().method() === 'POST' && new URL(res.url()).pathname.endsWith('/drawings');
async function scene(page, scope) {
  const response = await page.request.get(endpoint(scope));
  assert.equal(response.status(), 200);
  return response.json();
}
async function selectPane(page, view) {
  await page.getByLabel('Inspection pane').selectOption(view);
  await expect(page.locator(selector(view)).locator('xpath=ancestor::*[@data-inspecting="true"]')).toHaveCount(1);
}
async function point(page, view, x, y) {
  const pane = page.locator(selector(view));
  await pane.scrollIntoViewIfNeeded();
  const coords = await pane.evaluate((_, args) => window.workspaceProbe.drawingPoint(...args), [selector(view), x, y]);
  assert(Number.isInteger(coords.time), 'Pointer must target a real plotted candle in ' + view);
  return coords;
}
async function save(page, scope, act) {
  const [res] = await Promise.all([page.waitForResponse(isWrite, {timeout: 8000}), act()]);
  assert.equal(new URL(res.url()).searchParams.get('scope'), scope);
  assert.equal(res.status(), 200, await res.text());
  const saved = await res.json();
  assert.deepEqual(saved, await scene(page, scope));
  return saved;
}
const anchor = ({time, price}) => ({time, price});
async function place(page, details, view, kind, fractions) {
  const scope = scopeOf(details, view);
  const before = await scene(page, scope);
  await selectPane(page, view);
  await page.getByLabel('Drawing tool', {exact: true}).selectOption(kind);
  await page.getByRole('button', {name:'Draw', exact:true}).click();
  const expected = [];
  const after = await save(page, scope, async () => {
    for (const [x,y] of fractions) {
      const coords = await point(page, view, x, y);
      assert(details.panes[view].candles.some(bar => bar.time === coords.time));
      expected.push(anchor(coords));
      await page.mouse.click(coords.x, coords.y);
    }
  });
  assert.equal(after.revision, before.revision + 1);
  assert.deepEqual(after.items.slice(0,-1), before.items);
  const item = after.items.at(-1);
  assert.equal(item.kind, kind);
  assert.deepEqual(item.anchors, expected);
  await expect(page.getByLabel('Select drawing')).toHaveValue(item.id);
  return {view,scope,kind,id:item.id,anchors:expected,revision:after.revision};
}
export async function drawingJourney(page, details) {
  await page.getByText(/^Drawing toolbox/).click();
  const creations = [];
  creations.push(await place(page, details, 'primary', 'horizontal', [[.40,.46]]));
  const view = '2', scope = scopeOf(details, view);
  await selectPane(page, view);
  const start = await point(page, view, .52, .52);
  await page.mouse.move(start.x,start.y);
  await page.mouse.down();
  await page.mouse.move(start.x+45,start.y,{steps:6});
  await page.mouse.up();
  await expect.poll(async () => (await point(page,view,.52,.52)).range).not.toEqual(start.range);
  const afterPan = (await point(page,view,.52,.52)).range;
  await page.mouse.wheel(0,-180);
  await expect.poll(async () => {
    const {range} = await point(page,view,.52,.52);
    return range.to-range.from;
  }).toBeLessThan(afterPan.to-afterPan.from);
  const afterZoom = (await point(page,view,.52,.52)).range;
  const trend = await place(page,details,view,'trend',[[.33,.43],[.68,.61]]);
  creations.push(trend);
  const beforeEdit = await scene(page,scope);
  await page.getByLabel('Select drawing').selectOption('');
  await page.getByLabel('Select drawing').selectOption(trend.id);
  await page.getByRole('button',{name:'Set anchor 2',exact:true}).click();
  const newPoint = await point(page,view,.59,.34);
  const edited = await save(page,scope,() => page.mouse.click(newPoint.x,newPoint.y));
  assert.equal(edited.revision,beforeEdit.revision+1);
  assert.equal(edited.items.at(-1).id,trend.id);
  assert.deepEqual(edited.items.at(-1).anchors,[trend.anchors[0],anchor(newPoint)]);
  const undone = await save(page,scope,() => page.getByRole('button',{name:'Undo drawing edit'}).click());
  assert.equal(undone.revision,edited.revision+1);
  assert.deepEqual(undone.items,beforeEdit.items);
  creations.push(await place(page,details,'3','risk-reward',[[.32,.50],[.49,.68],[.65,.31]]));

  // Hold a real, committed API response while the operator changes the inspected pane.
  const lateView = '4', lateScope = scopeOf(details,lateView);
  await selectPane(page,lateView);
  const beforeLate = await scene(page,lateScope);
  let release, notify;
  const gate = new Promise(resolve => {release=resolve;});
  const received = new Promise(resolve => {notify=resolve;});
  const routePattern = '**/api/drawings?**';
  const hold = async route => {
    if (route.request().method() !== 'POST') return route.continue();
    const response = await route.fetch({timeout:8000});
    notify({response,scope:new URL(route.request().url()).searchParams.get('scope')});
    await gate;
    await route.fulfill({response});
  };
  await page.route(routePattern,hold);
  let late;
  try {
    await page.getByLabel('Drawing tool',{exact:true}).selectOption('vertical');
    await page.getByRole('button',{name:'Draw',exact:true}).click();
    const at = await point(page,lateView,.54,.47);
    await page.mouse.click(at.x,at.y);
    const result = await Promise.race([received,new Promise((_,reject) => {
      const timer=setTimeout(() => reject(Error('Missing delayed drawing POST')),8000);timer.unref();
    })]);
    assert.equal(result.scope,lateScope);
    assert.equal(result.response.status(),200);
    late = await result.response.json();
    const item=late.items.at(-1);
    assert.equal(late.revision,beforeLate.revision+1);
    assert.deepEqual(item.anchors,[anchor(at)]);
    await selectPane(page,view);
    await expect(page.getByLabel('Select drawing').locator('option[value="'+trend.id+'"]')).toHaveCount(1);
    const applied=page.waitForResponse(isWrite);
    release();
    await applied;
    await expect(page.getByLabel('Select drawing').locator('option[value="'+item.id+'"]')).toHaveCount(0);
    assert.deepEqual(await scene(page,scope),undone);
    creations.push({view:lateView,scope:lateScope,kind:item.kind,id:item.id,anchors:item.anchors,revision:late.revision});
  } finally {
    release();
    await page.unroute(routePattern,hold);
  }
  const scenes={};
  for (const entry of creations) scenes[entry.view]=await scene(page,entry.scope);
  assert.deepEqual(scenes[lateView],late);
  return {creations,scenes,view_ranges:{before:start.range,after_pan:afterPan,after_zoom:afterZoom},
    edit:{view,id:trend.id,revision:edited.revision,anchors:edited.items.at(-1).anchors,undo_revision:undone.revision},
    delayed_save:{origin:lateView,active_at_response:view,revision:late.revision}};
}

export async function verifyReopenedDrawings(page,details,journey) {
  await page.getByText(/^Drawing toolbox/).click();
  for (const {view,scope,id} of journey.creations) {
    await selectPane(page,view);
    assert.equal(scope,scopeOf(details,view));
    assert.deepEqual(await scene(page,scope),journey.scenes[view]);
    const picker = page.getByLabel('Select drawing');
    await expect(picker.locator('option[value="'+id+'"]')).toHaveCount(1);
    await picker.selectOption(id);
    await expect(picker).toHaveValue(id);
  }
}
