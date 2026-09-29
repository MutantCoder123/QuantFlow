import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DASH, MINUS, duration, group, hm, hms, inr, isoDay, notYet, pct, r, tone } from '../../trading_copilot/static/shared/format.js';

const at = (h, m, s = 0) => Date.UTC(2026, 8, 28, h, m, s) / 1000 - 5.5 * 3600; // IST wall time

test('Indian digit grouping', () => {
  assert.equal(group(1012480), '10,12,480');
  assert.equal(group(100000), '1,00,000');
  assert.equal(group(999), '999');
  assert.equal(group(1000), '1,000');
  assert.equal(group(123456789), '12,34,56,789');
  assert.equal(group(1412.4, 2), '1,412.40');
});

test('rupees carry their sign; zero has none; unknown is a dash, never 0', () => {
  assert.equal(inr(2100), '+₹2,100');
  assert.equal(inr(-630), `${MINUS}₹630`);
  assert.equal(inr(0), '₹0');
  assert.equal(inr(-0.3), '₹0');
  assert.equal(inr(1000000, { signed: false }), '₹10,00,000');
  assert.equal(inr(-12480, { signed: false }), `${MINUS}₹12,480`);   // a loss is never unsigned
  assert.equal(inr(null), DASH);
  assert.equal(inr(NaN), DASH);
});

test('R-multiples and percentages', () => {
  assert.equal(r(0.62), '+0.62 R');
  assert.equal(r(-0.3), `${MINUS}0.30 R`);
  assert.equal(r(0.001), '0.00 R');
  assert.equal(r(undefined), DASH);
  assert.equal(pct(41.666), '41.7%');
  assert.equal(pct(1.25, { dp: 2, signed: true }), '+1.25%');
  assert.equal(pct(-0.4), `${MINUS}0.4%`);
});

test('times are IST, 24-hour', () => {
  assert.equal(hms(at(14, 6, 12)), '14:06:12');
  assert.equal(hm(at(9, 5)), '09:05');
  assert.equal(isoDay('2026-09-28'), '28 Sep');
  assert.equal(hm(null), DASH);
});

test('durations', () => {
  assert.equal(duration(2.4), '2 s');
  assert.equal(duration(38 * 60), '38 min');
  assert.equal(duration(72 * 60), '1 h 12 min');
  assert.equal(duration(-1), DASH);
});

test('not-yet text states the sample size against its minimum', () => {
  assert.equal(notYet({ value: null, n: 7, min_n: 20, status: 'insufficient' }), 'not yet (7 of 20)');
  assert.equal(notYet({ value: null, n: 30, min_n: 20, status: 'undefined' }), 'no value yet');
});

test('tone', () => {
  assert.equal(tone(5), 'profit');
  assert.equal(tone(-5), 'loss');
  assert.equal(tone(0), 'flat');
  assert.equal(tone(null), 'flat');
});
