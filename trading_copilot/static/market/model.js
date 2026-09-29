// The Market tab's geometry and words. Pure: the numbers come from
// /api/market/* and the socket; this lays them out.

import { f1, linear } from '../shared/charts/svg.js';
import { DASH, MINUS, group } from '../shared/format.js';
import { sentenceCase } from '../shared/labels.js';

const known = (v) => typeof v === 'number' && Number.isFinite(v);
const SESSION_MIN = 375;                    // 09:15 -> 15:30
const IST_OFFSET_S = 19800;

/** Minutes since 09:15 IST for an epoch second. */
export const sessionMinute = (ts) => (((ts + IST_OFFSET_S) % 86400) / 60) - (9 * 60 + 15);

export const signedPct = (v, dp = 2) => (known(v) ? `${v < 0 ? MINUS : v > 0 ? '+' : ''}${Math.abs(v).toFixed(dp)}%` : DASH);
export const signedNum = (v, dp = 0) => (known(v) ? `${v < 0 ? MINUS : v > 0 ? '+' : ''}${group(Math.abs(v), dp)}` : DASH);
export const toneOf = (v) => (!known(v) || v === 0 ? 'flat' : v > 0 ? 'profit' : 'loss');

/**
 * The index line. points: [{x, y}] where x is a session minute (intraday) or
 * an index (daily). Returns path, area, y() and the last point, or null.
 */
export function lineGeometry(points, { width, height, xMax, refs = [], pad = 0.08 }) {
  const pts = (points || []).filter((p) => known(p.x) && known(p.y));
  if (pts.length < 2) return null;
  const ys = pts.map((p) => p.y).concat(refs.filter(known));
  let lo = Math.min(...ys);
  let hi = Math.max(...ys);
  const span = hi - lo || hi * 0.002 || 1;
  lo -= span * pad; hi += span * pad;
  const x = linear(0, xMax ?? pts[pts.length - 1].x, 0, width);
  const y = linear(lo, hi, height, 0);
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${f1(x(p.x))} ${f1(y(p.y))}`).join('');
  const last = pts[pts.length - 1];
  const area = `${path}L${f1(x(last.x))} ${height}L${f1(x(pts[0].x))} ${height}Z`;
  return { path, area, x, y, lo, hi, last: { x: f1(x(last.x)), y: f1(y(last.y)) } };
}

export const SESSION_TICKS = [[0, '09:15'], [45, '10:00'], [105, '11:00'], [165, '12:00'], [225, '13:00'],
  [285, '14:00'], [345, '15:00'], [375, '15:30']];
export { SESSION_MIN };

/** 50 (or n) squares: advancing, unchanged, declining. */
export function waffle(b) {
  if (!b || !known(b.advances) || !known(b.declines)) return null;
  const un = known(b.unchanged) ? b.unchanged : 0;
  const total = b.advances + b.declines + un;
  if (!total) return null;
  return [...Array(b.advances).fill('up'), ...Array(un).fill('flat'), ...Array(b.declines).fill('down')];
}

/** "16 rise for every 100 that fall" from a ratio (the proxy has no counts). */
export function ratioWords(r) {
  return known(r) ? `${Math.round(r * 100)} stocks rise for every 100 that fall` : '';
}

/** Paired FII/DII bars: each session gets two bars from a shared zero line. */
export function flowBars(history, { width, height }) {
  const rows = (history || []).filter((h) => known(h.fii_net) || known(h.dii_net));
  if (!rows.length) return null;
  const maxAbs = Math.max(1, ...rows.flatMap((h) => [Math.abs(h.fii_net || 0), Math.abs(h.dii_net || 0)]));
  const half = (height - 20) / 2;
  const zero = half + 4;
  const slot = width / rows.length;
  const bw = Math.max(3, Math.min(11, slot * 0.3));
  const bar = (v, x) => {
    if (!known(v)) return null;
    const h = Math.max(1, (Math.abs(v) / maxAbs) * half);
    return { x: f1(x), top: f1(v >= 0 ? zero - h : zero), h: f1(h) };
  };
  const bars = rows.map((r, i) => ({
    date: r.date,
    fii: bar(r.fii_net, slot * i + slot / 2 - bw - 1),
    dii: bar(r.dii_net, slot * i + slot / 2 + 1),
  }));
  return {
    bars, bw: f1(bw), zero: f1(zero), sessions: rows.length,
    fiiSold: rows.filter((r) => known(r.fii_net) && r.fii_net < 0).length,
    diiBought: rows.filter((r) => known(r.dii_net) && r.dii_net > 0).length,
  };
}

/** A tile's colours for a day change (symmetric: ±3 % is full strength). */
export function tileTone(c) {
  if (!known(c)) return { bg: 'var(--raise)', bd: 'var(--rule)', fg: 'var(--mute)' };
  const k = Math.min(Math.abs(c), 3) / 3;
  const rgb = c < 0 ? '238, 111, 106' : '69, 199, 154';
  return { bg: `rgba(${rgb}, ${(0.08 + k * 0.5).toFixed(2)})`, bd: `rgba(${rgb}, ${(0.18 + k * 0.32).toFixed(2)})`,
    fg: k > 0.6 ? 'var(--paper)' : c < 0 ? 'var(--coral)' : 'var(--jade)' };
}

/** The order book against the day's change: points, axes, and which to label. */
export function quadrantGeometry(points, { width, height, labels = 5 }) {
  const pts = (points || []).filter((p) => known(p.obi) && known(p.change_pct));
  if (!pts.length) return null;
  const ox = Math.max(0.4, Math.ceil(Math.max(...pts.map((p) => Math.abs(p.obi))) * 5) / 5);
  const oy = Math.max(2, Math.ceil(Math.max(...pts.map((p) => Math.abs(p.change_pct)))));
  const x = linear(-ox, ox, 14, width - 14);
  const y = linear(-oy, oy, height - 14, 14);
  // label the ones worth reading: buyers absorbing a fall, then the biggest moves
  const ranked = [...pts].sort((a, b) => (Number(b.absorbing) - Number(a.absorbing))
    || Math.abs(b.change_pct) - Math.abs(a.change_pct));
  // ...placed only where a label has room (about a name's width, a line's height)
  const placed = [];
  for (const p of ranked) {
    if (placed.length >= labels) break;
    const px = x(p.obi);
    const py = y(p.change_pct);
    if (placed.every((q) => Math.abs(q.x - px) > 70 || Math.abs(q.y - py) > 15)) placed.push({ symbol: p.symbol, x: px, y: py });
  }
  const named = new Set(placed.map((p) => p.symbol));
  return {
    x0: f1(x(0)), y0: f1(y(0)), ox, oy,
    points: pts.map((p) => ({ symbol: p.symbol, x: f1(x(p.obi)), y: f1(y(p.change_pct)), absorbing: p.absorbing,
      tone: p.absorbing ? 'ind' : p.change_pct < 0 ? 'loss' : 'profit', label: named.has(p.symbol) })),
    absorbing: pts.filter((p) => p.absorbing).map((p) => p.symbol),
  };
}

/** A sparkline's polyline points in a w x h box. */
export function spark(closes, w = 72, h = 22) {
  const c = (closes || []).filter(known);
  if (c.length < 2) return '';
  const lo = Math.min(...c);
  const hi = Math.max(...c);
  const s = hi - lo || 1;
  return c.map((v, i) => `${f1((i * w) / (c.length - 1))},${f1(h - 3 - ((v - lo) / s) * (h - 6))}`).join(' ');
}

/** The candlestick pattern word from a stock's `candlesticks`, or '' (the old matrix column). */
export function patternOf(cdl) {
  if (!cdl || typeof cdl !== 'object') return '';
  const active = cdl.active_patterns;
  if (typeof active === 'string' && active && active !== 'None') return sentenceCase(active);
  if (Array.isArray(active) && active.length) return sentenceCase(active[active.length - 1]);
  const keys = Object.keys(cdl).filter((k) => k !== 'active_patterns' && cdl[k] !== null && cdl[k] !== false);
  return keys.length ? sentenceCase(keys[keys.length - 1]) : '';
}

/** A board row, from the socket's state for one stock. */
export function boardRow(sym, st, now, staleAfter = 15) {
  const s = st || {};
  const ltp = known(s.ltp) && s.ltp > 0 ? s.ltp : null;
  const age = known(s.data_age_s) ? s.data_age_s : null;
  return {
    symbol: sym, ltp, change: known(s.change_pct) ? s.change_pct : null, obi: known(s.obi) ? s.obi : null,
    cvd: known(s.cvd) ? s.cvd : null, rsi: known(s.rsi_5m) ? s.rsi_5m : null, pcr: known(s.stock_pcr) ? s.stock_pcr : null,
    ivr: known(s.ivr) ? s.ivr : null, maxPain: known(s.max_pain_price) && s.max_pain_price > 0 ? s.max_pain_price : null,
    pattern: patternOf(s.candlesticks),
    stale: s.market_state === 'LIVE' && age !== null && age > staleAfter, age,
  };
}

/** The book-tilt bar: a centred diverging bar, as left/width percentages. */
export function tilt(obi) {
  if (!known(obi)) return null;
  const v = Math.max(-1, Math.min(1, obi));
  return v >= 0 ? { left: 50, width: v * 50, tone: 'profit' } : { left: 50 + v * 50, width: -v * 50, tone: 'loss' };
}
