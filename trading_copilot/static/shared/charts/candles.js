// Intraday candles with volume, cumulative delta and horizontal levels
// (the Inspector's chart). Pure geometry: bars and sizes in, coordinates out.

import { f1, linear } from './svg.js';

const known = (v) => typeof v === 'number' && Number.isFinite(v);

/** A "nice" tick step for a price span: 1, 2 or 5 x 10^k, about `count` ticks. */
export function niceStep(span, count = 4) {
  if (!(span > 0)) return 1;
  const raw = span / count;
  const p = 10 ** Math.floor(Math.log10(raw));
  const m = raw / p;
  return (m < 1.5 ? 1 : m < 3 ? 2 : m < 7 ? 5 : 10) * p;       // the nearest nice step
}

/**
 * bars: [{t, o, h, l, c, v, vwap, cvd}] oldest first.
 * levels: [{key, price, keep}] -- a level with keep:true (the position's stop,
 *   entry, target) widens the price range; the others (value area, POC) are
 *   drawn only when they fall inside it, so a far level never squashes the
 *   candles into a line.
 * Returns null when there are no bars.
 */
export function candleGeometry(bars, { width, height, volHeight = 44, deltaHeight = 50, levels = [], slots } = {}) {
  if (!bars || !bars.length) return null;
  const n = Math.max(bars.length, slots || 0);
  let lo = Math.min(...bars.map((b) => b.l));
  let hi = Math.max(...bars.map((b) => b.h));
  for (const lv of levels) {
    if (lv.keep && known(lv.price)) { lo = Math.min(lo, lv.price); hi = Math.max(hi, lv.price); }
  }
  const pad = (hi - lo) * 0.06 || Math.max(hi * 0.001, 0.05);
  lo -= pad; hi += pad;
  const y = linear(lo, hi, height, 0);
  const slot = width / n;
  const bodyW = Math.max(2, Math.min(10, slot * 0.62));
  const x = (i) => slot * (i + 0.5);

  const candles = bars.map((b, i) => {
    const up = b.c >= b.o;
    const top = y(Math.max(b.o, b.c));
    const bottom = y(Math.min(b.o, b.c));
    return { x: f1(x(i)), w: f1(bodyW), up, wickTop: f1(y(b.h)), wickH: f1(Math.max(1, y(b.l) - y(b.h))),
      bodyTop: f1(top), bodyH: f1(Math.max(1.5, bottom - top)), t: b.t };
  });

  let vwap = '';
  bars.forEach((b, i) => { if (known(b.vwap)) vwap += `${vwap ? 'L' : 'M'}${f1(x(i))} ${f1(y(b.vwap))}`; });

  const maxV = Math.max(...bars.map((b) => (known(b.v) ? b.v : 0)), 0);
  const volume = bars.map((b, i) => ({ x: f1(x(i) - bodyW / 2), w: f1(bodyW), up: b.c >= b.o,
    h: f1(maxV > 0 && known(b.v) ? (b.v / maxV) * (volHeight - 4) : 0) }));

  const deltas = bars.map((b) => b.cvd).filter(known);
  let delta = null;
  if (deltas.length >= 2) {
    const dlo = Math.min(0, ...deltas);
    const dhi = Math.max(0, ...deltas);
    const dy = linear(dlo, dhi, deltaHeight - 6, 6);
    let path = '';
    bars.forEach((b, i) => { if (known(b.cvd)) path += `${path ? 'L' : 'M'}${f1(x(i))} ${f1(dy(b.cvd))}`; });
    const last = deltas[deltas.length - 1];
    delta = { path, zero: f1(dy(0)), rising: last >= deltas[Math.max(0, deltas.length - 4)] };
  }

  const step = niceStep(hi - lo);
  const yTicks = [];
  for (let p = Math.ceil(lo / step) * step; p <= hi; p += step) yTicks.push({ y: f1(y(p)), price: p });

  const lines = levels.filter((lv) => known(lv.price) && lv.price >= lo && lv.price <= hi)
    .map((lv) => ({ ...lv, y: f1(y(lv.price)) }));

  // about four time labels, on whole bars
  const every = Math.max(1, Math.ceil(bars.length / 4));
  const xTicks = bars.map((b, i) => ({ i, t: b.t })).filter(({ i }) => i % every === 0).map(({ i, t }) => ({ x: f1(x(i)), t }));

  return { candles, vwap, volume, delta, yTicks, lines, xTicks, y, x, lo, hi, band: (a, b) => ({ top: f1(y(Math.max(a, b))), h: f1(Math.abs(y(a) - y(b))) }) };
}
