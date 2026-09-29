// Review's pure parts: the headline, the count line, and the chart geometry.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { countLine, headline, histGeometry, reliabilityGeometry, trendGeometry } from '../../trading_copilot/static/review/model.js';

const bucket = (lo, n, rate, ci) => ({ lo, hi: +(lo + 0.1).toFixed(2), n, thin: n < 10, hit_rate: rate, ci });

test('the headline says whether higher scores win more, or that there are too few', () => {
  assert.deepEqual(headline({ n: 414, reliability: { shown: 5, first: 50, last: 69 } }),
    { text: 'Higher scores win more', figs: [50, 69], up: true });
  assert.equal(headline({ n: 40, reliability: { shown: 2, first: 60, last: 45 } }).text, 'Higher scores win less');
  assert.equal(headline({ n: 37, reliability: { shown: 1 } }).text, 'Too few signals to judge the score yet');
  assert.equal(headline({ n: 0 }).text, 'No signal has resolved in this range yet');
});

test('the count line names what is left out', () => {
  assert.equal(countLine({ n: 37, sessions: 1, pending: 2, legacy: 84 }),
    '37 signals resolved at 90 min over 1 session · 2 still open · 84 graded at an older horizon, left out · paper P&L is on Performance');
});

test('reliability: a band around the curve, the coin flip, counts, thin buckets n only', () => {
  const rel = { min_n: 10, buckets: [bucket(0, 0, null, null), bucket(0.1, 98, 50, 9.9), bucket(0.2, 142, 56, 8.2),
    bucket(0.3, 101, 60, 9.6), bucket(0.4, 49, 65, 13.4), bucket(0.5, 16, 69, 22.7), bucket(0.6, 6, null, null),
    bucket(0.7, 2, null, null), bucket(0.8, 0, null, null), bucket(0.9, 0, null, null)] };
  const g = reliabilityGeometry(rel, { width: 560 });
  assert.equal(g.lo, 30);
  assert.equal(g.hi, 100);                                  // 69 + 22.7 reaches past 80
  assert.equal(g.points.length, 5);
  assert.equal(g.points[3].good, true);
  assert.ok(g.band.endsWith('Z') && g.curve.startsWith('M'));
  assert.equal(g.counts.length, 8);                          // 0 .. 0.8
  assert.deepEqual(g.counts.filter((c) => c.thin).map((c) => c.n), [6, 2]);
  assert.equal(g.counts[0].h, '0');
  assert.ok(g.diag);
  const none = reliabilityGeometry({ buckets: [bucket(0.2, 3, null, null)] }, { width: 400 });
  assert.equal(none.curve, '');
  assert.equal(none.points.length, 0);
});

test('the trend and the histogram', () => {
  const t = trendGeometry([{ date: 'a', n: 10, hit_rate: 40, rolling: 40, thin: true }, { date: 'b', n: 20, hit_rate: 60, rolling: 53.3, thin: false }],
    { width: 300 });
  assert.equal(t.dots.length, 2);
  assert.equal(Number(t.bars[1].h), 60);
  assert.ok(t.line.startsWith('M12 '));
  assert.equal(trendGeometry([], { width: 1 }), null);
  const h = histGeometry({ lo: -2, hi: 2, width: 1, n: 4, right: 3, median: 0.2,
    bins: [{ from: -2, to: -1, n: 1 }, { from: -1, to: 0, n: 0 }, { from: 0, to: 1, n: 2 }, { from: 1, to: 2, n: 1 }] }, { width: 400 });
  assert.equal(h.zero, '200');
  assert.equal(h.rightPct, 75);
  assert.equal(h.bars[2].right, true);
  assert.equal(histGeometry({ n: 0 }, { width: 1 }), null);
});
