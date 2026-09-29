// Review's geometry and words. Pure: /api/review computed every rate, range
// and count; this places them and says what they mean.

import { f1, linear } from '../shared/charts/svg.js';

const known = (v) => typeof v === 'number' && Number.isFinite(v);

/** The headline: does a higher score win more? */
export function headline(r) {
  if (!r || !r.n) return { text: 'No signal has resolved in this range yet', figs: null };
  const rel = r.reliability || {};
  if ((rel.shown || 0) < 2) {
    return { text: `Too few signals to judge the score yet`, figs: null, n: r.n };
  }
  const up = rel.last > rel.first;
  return { text: up ? 'Higher scores win more' : rel.last < rel.first ? 'Higher scores win less' : 'The score doesn’t separate winners',
    figs: [rel.first, rel.last], up };
}

/** The line under the headline: what is counted and what is left out. */
export function countLine(r, horizon = 90) {
  if (!r) return '';
  const parts = [`${r.n} ${r.n === 1 ? 'signal' : 'signals'} resolved at ${horizon} min over ${r.sessions} ${r.sessions === 1 ? 'session' : 'sessions'}`];
  if (r.pending) parts.push(`${r.pending} still open`);
  if (r.legacy) parts.push(`${r.legacy} graded at an older horizon, left out`);
  parts.push('paper P&L is on Performance');
  return parts.join(' · ');
}

/**
 * The reliability chart: hit rate by score bucket, its 95% range as a band,
 * the coin flip, the "if score = probability" diagonal, and n per bucket.
 * Buckets under the minimum count show n only (amber).
 */
export function reliabilityGeometry(rel, { width, height = 280, strip = 44 }) {
  const all = (rel && rel.buckets) || [];
  const used = all.filter((b) => b.n > 0);
  const hiScore = Math.max(0.8, ...used.map((b) => b.hi));
  const buckets = all.filter((b) => b.lo < hiScore);
  const shown = buckets.filter((b) => known(b.hit_rate));
  const rates = shown.flatMap((b) => [b.hit_rate - (b.ci || 0), b.hit_rate + (b.ci || 0)]);
  const lo = Math.max(0, Math.min(30, Math.floor(Math.min(50, ...rates) / 10) * 10));
  const hi = Math.min(100, Math.max(80, Math.ceil(Math.max(50, ...rates) / 10) * 10));
  const x = linear(0, hiScore, 0, width);
  const y = linear(lo, hi, height, 0);
  const clamp = (v) => Math.max(lo, Math.min(hi, v));
  const mid = (b) => (b.lo + b.hi) / 2;
  let curve = '';
  let up = '';
  let dn = '';
  shown.forEach((b, i) => {
    curve += `${i ? 'L' : 'M'}${f1(x(mid(b)))} ${f1(y(b.hit_rate))}`;
    up += `${i ? 'L' : 'M'}${f1(x(mid(b)))} ${f1(y(clamp(b.hit_rate + (b.ci || 0))))}`;
  });
  [...shown].reverse().forEach((b) => { dn += `L${f1(x(mid(b)))} ${f1(y(clamp(b.hit_rate - (b.ci || 0))))}`; });
  const maxN = Math.max(1, ...buckets.map((b) => b.n));
  // the diagonal: where a bucket would sit if the score were its win probability
  const d0 = Math.max(0, lo / 100);
  const d1 = Math.min(hiScore, hi / 100);
  return {
    height, strip, lo, hi,
    band: shown.length >= 2 ? `${up}${dn}Z` : '', curve: shown.length >= 2 ? curve : '',
    points: shown.map((b) => ({ x: f1(x(mid(b))), y: f1(y(b.hit_rate)), rate: b.hit_rate, good: b.hit_rate >= 60, n: b.n })),
    counts: buckets.map((b) => ({ x: f1(x(mid(b))), h: f1(b.n ? Math.max(2, (b.n / maxN) * strip) : 0), n: b.n, thin: b.thin && b.n > 0 })),
    grid: [...Array((hi - lo) / 10 + 1)].map((_, i) => lo + i * 10).map((v) => ({ y: f1(y(v)), label: `${v}%` })),
    xticks: [...Array(Math.round(hiScore * 10) + 1)].map((_, i) => ({ x: f1(x(i / 10)), label: i ? (i / 10).toFixed(1) : '0' })),
    coinY: f1(y(50)),
    diag: d1 > d0 ? { x1: f1(x(d0)), y1: f1(y(d0 * 100)), x2: f1(x(d1)), y2: f1(y(d1 * 100)) } : null,
  };
}

/** The daily trend: hit-rate dots, the rolling line, and signals-per-session bars. */
export function trendGeometry(days, { width, height = 230, barH = 60 }) {
  if (!days || !days.length) return null;
  const rates = days.flatMap((d) => [d.hit_rate, d.rolling]).filter(known);
  const lo = Math.max(0, Math.min(40, Math.floor(Math.min(...rates, 50) / 10) * 10));
  const hi = Math.min(100, Math.max(70, Math.ceil(Math.max(...rates, 50) / 10) * 10));
  const x = days.length === 1 ? () => width / 2 : linear(0, days.length - 1, 12, width - 12);
  const y = linear(lo, hi, height, 10);
  let line = '';
  days.forEach((d, i) => { if (known(d.rolling)) line += `${line ? 'L' : 'M'}${f1(x(i))} ${f1(y(d.rolling))}`; });
  const maxN = Math.max(1, ...days.map((d) => d.n));
  return {
    lo, hi, line, height, barH,
    dots: days.map((d, i) => ({ x: f1(x(i)), y: known(d.hit_rate) ? f1(y(d.hit_rate)) : null, thin: d.thin, date: d.date, rate: d.hit_rate, n: d.n })),
    bars: days.map((d, i) => ({ x: f1(x(i)), h: f1((d.n / maxN) * barH), n: d.n })),
    grid: [lo, 50, hi].filter((v, i, a) => a.indexOf(v) === i).map((v) => ({ y: f1(y(v)), label: `${v}%`, base: v === 50 })),
  };
}

/** The histogram of the move after a signal, with zero and the median marked. */
export function histGeometry(h, { width, height = 190 }) {
  if (!h || !h.n) return null;
  const bins = h.bins || [];
  const maxN = Math.max(1, ...bins.map((b) => b.n));
  const x = linear(h.lo, h.hi, 0, width);
  const bw = width / bins.length;
  return {
    bars: bins.map((b) => ({ x: f1(x(b.from) + 1.5), w: f1(bw - 3), h: f1((b.n / maxN) * height), n: b.n, right: b.from >= 0 })),
    zero: f1(x(0)), median: known(h.median) ? f1(x(Math.max(h.lo, Math.min(h.hi, h.median)))) : null, height,
    rightPct: Math.round((h.right / h.n) * 100), wrongPct: 100 - Math.round((h.right / h.n) * 100),
  };
}
