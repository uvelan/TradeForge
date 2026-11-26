import {expect, test} from '@playwright/test';

const month = '2025-11';
const session = 'recorded-review-identity';
const root = '/research/api';
const replay = `${root}/month-replay/${month}`;
const sessionRoute = `${replay}/${session}`;
const time = '2025-11-03T13:00:00.000Z';

function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return {promise, release};
}

const fixture = (suffix: string) => {
  const id = `PKG-${suffix}`;
  const review = `REVIEW-${suffix}`;
  const metric = `metric-${suffix}`;
  const value = suffix === 'A' ? '1.25' : '2.50';
  const unit = 'USD';
  const comment = `Only review ${suffix} contains this comment`;
  const rationale = `Only review ${suffix} contains this decision`;
  const quote = {
    time_ms: Date.parse(time), ordinal: suffix === 'A' ? 1 : 2, source_sha256: 'mock-source',
    partition: {id: 'mock-partition', sha256: 'mock-partition-checksum'}, bid_units: 100, ask_units: 101,
  };
  const template = {package_id: id, claims: [{metric_id: metric, value, unit}]};
  return {
    id, review, value, unit, comment, rationale, template,
    recorded: {
      manifest: {package_id: id, knowledge_cutoff_utc: time},
      template,
      evidence: {
        metrics: {
          [metric]: {metric_id: metric, value, unit, entry_ref: 'entry', exit_ref: 'exit', available_at_ms: quote.time_ms},
        },
        events: [{id: 'entry', kind: 'ENTRY', quote}, {id: 'exit', kind: 'EXIT', quote}],
        unavailable: {},
      },
    },
    decisionLog: {current_sequence: 1, decisions: [{sequence: 1, decision: 'ACCEPTED', rationale}]},
    original: {...template, comment},
  };
};

test('a checksum failure cannot leave the previous package review on screen', async ({page}) => {
  const a = fixture('A'), b = fixture('B');
  const respond = gate();
  let bRequests = 0;
  await page.route(url => url.pathname === replay, route => route.fulfill({json: {
    session, sessions: [session], policy_current: true,
    binding: {month, hours: []},
    checkpoint: {index: 0, next_index: 1, completed: true, through: Date.parse(time) / 1000, quote_count: 2, events: [], coverage: []},
  }}));
  await page.route(`**${sessionRoute}/steps`, route => route.fulfill({json: []}));
  await page.route(`**${sessionRoute}/packages`, route => route.fulfill({json:
    [a, b].map(p => ({id: p.id, knowledge_cutoff: time})),
  }));
  await page.route(`**${root}/ai/reviews`, route => route.fulfill({json:
    [a, b].map(p => ({id: p.review, package_id: p.id, assessment: 'ACCEPTED', original_integrity: 'UNAMBIGUOUS_JSON'})),
  }));
  for (const p of [a, b]) {
    await page.route(`**${root}/ai/packages/${p.id}/recorded`, route => route.fulfill({json: p.recorded}));
    await page.route(`**${root}/ai/reviews/${p.review}/decisions`, route => route.fulfill({json: p.decisionLog}));
    await page.route(`**${root}/ai/reviews/${p.review}/original`, async route => {
      if (p === b && ++bRequests === 1) {
        await respond.promise;
        await route.fulfill({status: 409, json: {detail: 'Original review checksum mismatch'}});
      } else {
        await route.fulfill({json: p.original});
      }
    });
  }

  await page.goto('/');
  await page.getByRole('button', {name: 'Strategy Lab', exact: true}).click();
  await page.getByRole('button', {name: 'Inspect monthly coverage'}).click();
  const pane = page.getByRole('region', {name: 'Recorded replay evidence'});
  const packages = pane.getByRole('combobox', {name: 'Saved evidence package'});
  const reviews = pane.getByRole('combobox', {name: 'Imported review'});
  const aComment = pane.getByText(a.comment, {exact: true});
  const aClaim = pane.getByRole('link', {name: `Trace claim ${a.value} ${a.unit}`});
  const originalLink = pane.getByRole('link', {name: 'Original review bytes'});
  const decisionControl = pane.getByRole('button', {name: 'Record operator decision'});
  await packages.selectOption(a.id);
  await expect(aComment).toBeVisible();
  await expect(aClaim).toBeVisible();
  await expect(pane.getByText(a.rationale, {exact: false})).toBeVisible();

  const request = page.waitForRequest(`**${root}/ai/reviews/${b.review}/original`, {timeout: 5000});
  try {
    await packages.selectOption(b.id);
    await request;
    await expect(packages).toHaveValue(b.id);
    await expect.soft(aComment).toBeHidden({timeout: 1000});
    await expect.soft(aClaim).toBeHidden({timeout: 1000});
  } finally {
    respond.release();
  }

  await expect(pane.getByRole('alert')).toHaveText(/Original review checksum mismatch/);
  await expect(aComment).toBeHidden();
  await expect(aClaim).toBeHidden();
  await expect(originalLink).toBeHidden();
  await expect(decisionControl).toBeHidden();
  await expect(reviews).toHaveValue('');

  await reviews.selectOption(b.review);
  await expect(pane.getByRole('alert')).toBeHidden();
  await expect(pane.getByText(b.comment, {exact: true})).toBeVisible();
  await expect(pane.getByRole('link', {name: `Trace claim ${b.value} ${b.unit}`})).toBeVisible();
  await expect(pane.getByText(b.rationale, {exact: false})).toBeVisible();
  await expect(originalLink).toHaveAttribute('href', `${root}/ai/reviews/${b.review}/original`);
  await expect(aComment).toBeHidden();
  await expect(aClaim).toBeHidden();
});
