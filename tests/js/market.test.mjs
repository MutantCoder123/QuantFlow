// The Market tab's pure parts: the index line, breadth, flows, tiles, the
// quadrant, sparklines, and a board row.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { boardRow, flowBars, lineGeometry, patternOf, quadrantGeometry, ratioWords, sessionMinute, signedNum, signedPct,
  spark, tileTone, tilt, waffle } from '../../trading_copilot/static/market/model.js';

test('session minutes count from 09:15 IST', () => {
  const t0915 = Date.UTC(2026, 8, 29, 3, 45) / 1000;
  assert.equal(sessionMinute(t0915), 0);
  assert.equal(sessionMinute(t0915 + 147 * 60), 147);
});

test('signed figures carry their sign and a real minus', () => {
  assert.equal(signedPct(-1.1834), '−1.18%');
  assert.equal(signedPct(0.5), '+0.50%');
  assert.equal(signedPct(null), '—');
  assert.equal(signedNum(-5353.22), '−5,353');
  assert.equal(signedNum(4120), '+4,120');
});

test('the index line fits its points and a reference line', () => {
  const g = lineGeometry([{ x: 0, y: 100 }, { x: 150, y: 90 }], { width: 375, height: 100, xMax: 375, refs: [110] });
  assert.ok(g.path.startsWith('M0 '));
  assert.ok(g.hi > 110 && g.lo < 90);
  assert.equal(g.last.x, '150');
  assert.equal(lineGeometry([{ x: 0, y: 1 }], { width: 1, height: 1 }), null);
});

test('breadth: the waffle from counts, words from a ratio', () => {
  const w = waffle({ advances: 7, declines: 42, unchanged: 1 });
  assert.equal(w.length, 50);
  assert.deepEqual([w[0], w[7], w[8]], ['up', 'flat', 'down']);
  assert.equal(waffle({ advances: null, declines: 3 }), null);
  assert.equal(ratioWords(0.16), '16 stocks rise for every 100 that fall');
  assert.equal(ratioWords(null), '');
});

test('flow bars sit either side of one zero line', () => {
  const g = flowBars([{ date: 'a', fii_net: -100, dii_net: 50 }, { date: 'b', fii_net: 20, dii_net: null }], { width: 200, height: 128 });
  assert.equal(g.sessions, 2);
  assert.equal(g.bars[0].fii.top, g.zero);                  // a sale hangs below zero
  assert.equal(Number(g.bars[0].fii.h), 54);                // the biggest fills half the height
  assert.equal(Number(g.bars[0].dii.top) + Number(g.bars[0].dii.h), Number(g.zero));
  assert.equal(g.bars[1].dii, null);
  assert.equal(g.fiiSold, 1);
  assert.equal(flowBars([], { width: 1, height: 1 }), null);
});

test('tile tone is symmetric and saturates at 3 %', () => {
  assert.equal(tileTone(-3).bg, tileTone(-9).bg);
  assert.ok(tileTone(3).bg.startsWith('rgba(69'));
  assert.equal(tileTone(-0.2).fg, 'var(--coral)');
  assert.equal(tileTone(2.5).fg, 'var(--paper)');
  assert.equal(tileTone(null).fg, 'var(--mute)');
});

test('the quadrant labels buyers absorbing a fall first', () => {
  const pts = [{ symbol: 'A', obi: 0.3, change_pct: -1, absorbing: true }, { symbol: 'B', obi: -0.2, change_pct: -3.4, absorbing: false },
    { symbol: 'C', obi: 0.1, change_pct: 0.2, absorbing: false }];
  const g = quadrantGeometry(pts, { width: 420, height: 300, labels: 2 });
  assert.deepEqual(g.points.filter((p) => p.label).map((p) => p.symbol), ['A', 'B']);
  assert.equal(g.oy, 4);
  assert.equal(g.ox, 0.4);
  assert.deepEqual(g.absorbing, ['A']);
  // two points on top of each other: only one gets a label
  const crowd = quadrantGeometry([{ symbol: 'P', obi: 0.2, change_pct: -2, absorbing: true },
    { symbol: 'Q', obi: 0.21, change_pct: -2.05, absorbing: true }], { width: 420, height: 300 });
  assert.deepEqual(crowd.points.filter((p) => p.label).map((p) => p.symbol), ['Q']);   // the bigger move wins the room
  assert.equal(g.points[0].tone, 'ind');
});

test('sparklines, tilt, patterns and a board row', () => {
  assert.equal(spark([1, 2, 3], 72, 22), '0,19 36,11 72,3');
  assert.equal(spark([1]), '');
  assert.deepEqual(tilt(-0.4), { left: 30, width: 20, tone: 'loss' });
  assert.equal(tilt(null), null);
  assert.equal(patternOf({ active_patterns: 'None', bullish_engulfing: true }), 'Bullish engulfing');
  assert.equal(patternOf({ active_patterns: 'DOJI' }), 'Doji');
  assert.equal(patternOf({ active_patterns: 'None' }), '');
  const r = boardRow('SAIL', { ltp: 181.5, change_pct: -1.87, obi: -0.21, cvd: -124560, rsi_5m: 34, stock_pcr: 0.82, ivr: 41,
    max_pain_price: 185, market_state: 'LIVE', data_age_s: 20, candlesticks: {} }, 0, 15);
  assert.equal(r.stale, true);
  assert.equal(r.maxPain, 185);
  assert.equal(boardRow('X', undefined, 0).ltp, null);
});
