// The Signals tab's pure parts: the stage strip, the lanes, the pipeline word,
// the rail, filters, and the auto-analysis state.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { inFilter, laneGeometry, pipelineWord, rail, sectorBars, stageStrip } from '../../trading_copilot/static/signals/model.js';
import { autoState } from '../../trading_copilot/static/shared/auto-analyze.js';

test('the stage strip: counts, shares of what the math found, and why the rest stopped', () => {
  const s = stageStrip({ stages: { found: 294, cleared: 144, reviewed: 61, confirmed: 30, opened: 27 },
    drops: { cleared: { 'Held by the gatekeeper': 84, 'Held back by the session phase': 66 }, reviewed: { 'Not sent to the AI': 83 },
      confirmed: { 'AI deferred': 16, 'AI passed': 12 }, opened: { SIZE_ROUNDS_TO_ZERO: 2, ALREADY_OPEN: 1 } } });
  assert.deepEqual(s.map((x) => x.n), [294, 144, 61, 30, 27]);
  assert.equal(s[0].share, 1);
  assert.equal(s[0].why, 'Every stock, every 10 s');
  assert.equal(s[1].why, 'Held by the gatekeeper 84 · Held back by the session phase 66');
  assert.equal(s[4].why, 'Rounds to zero shares 2 · Already open 1');
  assert.equal(stageStrip({ stages: { found: 0 } })[2].share, 0);
  assert.equal(stageStrip(null), null);
});

test('lanes sit on the session axis, clamped to 09:15-15:30', () => {
  const t0915 = Date.UTC(2026, 8, 29, 3, 45) / 1000;
  const g = laneGeometry([{ symbol: 'MCX', marks: [{ ts: t0915, kind: 'taken' }, { ts: t0915 - 3600, kind: 'other' }],
    holds: [{ start: t0915 + 1800, end: t0915 + 1800, kind: 'open' }] }], { width: 505, labelW: 130 });
  assert.equal(g.lanes[0].marks[0].x, '130');
  assert.equal(g.lanes[0].marks[1].x, '130');           // before the open: clamped
  assert.equal(g.lanes[0].holds[0].w, '3');            // an instant hold still shows
  assert.equal(g.hours[g.hours.length - 1].x, '505');
  assert.equal(g.hours.length, 4);                              // narrow: every other hour
  assert.equal(laneGeometry([], { width: 1300 }).hours.length, 8);
  assert.equal(laneGeometry(Array.from({ length: 12 }, (_, i) => ({ symbol: `S${i}`, marks: [], holds: [] })), { width: 400 }).more, 2);
});

test('the pipeline word is the furthest stage with something to say', () => {
  assert.deepEqual(pipelineWord([{ tone: 'ok', text: 'Long 0.36' }, { tone: 'ok', text: 'Sized 465' }, { tone: 'wait', text: 'AI reviewing' },
    { tone: 'off', text: '' }]), { text: 'AI reviewing', tone: 'wait' });
  assert.deepEqual(pipelineWord([{ tone: 'ok', text: 'Short 0.41' }, { tone: 'bad', text: 'CLUSTER_LIMIT_POWER_CAPGOODS' },
    { tone: 'off', text: '' }, { tone: 'off', text: '' }]), { text: 'Sector limit (power capgoods)', tone: 'bad' });
  assert.deepEqual(pipelineWord([{ tone: 'off', text: '' }]), { text: '', tone: 'off' });
});

test('the rail places the entry and the price between stop and target', () => {
  assert.deepEqual(rail(98, 104, 100, 101), { entry: 33.33333333333333, price: 50 });
  assert.deepEqual(rail(104, 98, 101, 90), { entry: 50, price: 100 });      // a short, price past the target: clamped
  assert.equal(rail(null, 104, 100, 101), null);
  assert.equal(rail(100, 100, 100, 100), null);
});

test('filters and the sector bars', () => {
  assert.equal(inFilter({ wants_action: true }, 'action'), true);
  assert.equal(inFilter({ blocked: false }, 'blocked'), false);
  assert.equal(inFilter({}, 'all'), true);
  assert.deepEqual(sectorBars({ clusters: [{ cluster: 'FINANCIALS', name: 'Financials', open_risk: 9500, limit: 10000, pct_of_limit: 0.95 }] }),
    [{ name: 'Financials', risk: 9500, limit: 10000, share: 0.95, hot: true }]);
  assert.deepEqual(sectorBars(null), []);
});

test('auto analysis defaults to on, as the old page did', () => {
  assert.deepEqual(autoState(), { on: true, interval: 90 });   // no localStorage under node
});
