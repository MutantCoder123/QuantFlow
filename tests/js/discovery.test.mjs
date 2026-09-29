// Discovery's pure parts: the universe scatter, the stepper, the scan line, the RS bar.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dotSize, rsBar, scanLine, scatterGeometry, stepper } from '../../trading_copilot/static/discovery/model.js';

const picks = [{ symbol: 'TATAPOWER', rank: 1, rs: -4.2, signed: -0.71, adv_crore: 412 }, { symbol: 'MCX', rank: 2, rs: 3.8, signed: 0.66, adv_crore: 1240 }];

test('the universe: picks named and toned, the crowd grey, the cut at the 10th pick', () => {
  const universe = [{ symbol: 'TATAPOWER', rs: -4.2, y: -0.71, adv_crore: 412 }, { symbol: 'MCX', rs: 3.8, y: 0.66, adv_crore: 1240 },
    { symbol: 'X', rs: 11, y: 0.05, adv_crore: 3 }];
  const g = scatterGeometry({ universe, picks, cut: 0.41, selected: 'MCX' }, { width: 860, height: 396 });
  assert.equal(g.full, true);
  assert.equal(g.xr, 12);                                          // widened to fit X at 11 %
  assert.deepEqual(g.crowd.map((c) => c.symbol), ['X']);
  assert.deepEqual(g.picks.map((p) => [p.symbol, p.tone, p.selected]), [['MCX', 'profit', true], ['TATAPOWER', 'loss', false]]);
  assert.equal(g.y0, '198');
  assert.ok(g.picks.every((p) => p.label));                        // far apart: both named
  const crowded = scatterGeometry({ universe: null, picks: [{ symbol: 'A', rank: 1, rs: 3, signed: 0.5 },
    { symbol: 'B', rank: 2, rs: 3.1, signed: 0.51 }, { symbol: 'C', rank: 3, rs: 3.05, signed: 0.5 }], selected: 'C' },
  { width: 860, height: 396 });
  assert.deepEqual(crowded.picks.filter((p) => p.label).map((p) => p.symbol).sort(), ['C']);   // the selected wins the room
  assert.equal(Number(g.cutHi) + Number(g.cutLo), 396);             // the two cut lines mirror each other
  const fallback = scatterGeometry({ universe: null, picks, cut: null }, { width: 400, height: 200 });
  assert.equal(fallback.full, false);
  assert.equal(fallback.cutHi, null);
  assert.equal(scatterGeometry({ universe: [], picks: [] }, { width: 1, height: 1 }), null);
});

test('dot size grows gently with liquidity and has a floor and a ceiling', () => {
  assert.equal(dotSize(0), 5);
  assert.equal(dotSize(null), 5);
  assert.equal(dotSize(100), 7.5);
  assert.equal(dotSize(1e6), 22);
});

test('the stepper follows the scan', () => {
  assert.equal(stepper({ state: 'done' }), null);
  const s1 = stepper({ state: 'running', phase: 'scanning', scanned: 120, total: 214 });
  assert.deepEqual(s1.map((s) => s.state), ['now', 'next', 'next']);
  assert.equal(s1[0].label, 'Scoring 120 of 214');
  assert.deepEqual(stepper({ state: 'running', phase: 'scanning', scanned: 214, total: 214 }).map((s) => s.state), ['done', 'now', 'next']);
  assert.deepEqual(stepper({ state: 'running', phase: 'analyzing', scanned: 214, total: 214 }).map((s) => s.state), ['done', 'done', 'now']);
  assert.equal(stepper({ state: 'running', phase: 'scanning', scanned: 0, total: null })[0].label, 'Loading the F&O list');
});

test('the scan line and the RS bar', () => {
  assert.equal(scanLine({ finished_at: '2026-09-29T08:52:10', duration_s: 130, ai_model: 'ollama:qwen2.5:7b' }),
    'Scanned 08:52 in 2 min · AI read the top 10 with qwen2.5:7b');
  assert.equal(scanLine({ state: 'idle' }), '');
  assert.deepEqual(rsBar(-4), { left: 25, width: 25, tone: 'loss' });
  assert.deepEqual(rsBar(20), { left: 50, width: 50, tone: 'profit' });
  assert.equal(rsBar(null), null);
});
