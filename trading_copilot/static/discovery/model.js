// Discovery's geometry and words. Pure: /api/discovery/view shaped the data.

import { f1, linear } from '../shared/charts/svg.js';
import { duration } from '../shared/format.js';

const known = (v) => typeof v === 'number' && Number.isFinite(v);

/** Dot diameter for a liquidity figure (₹ Cr): area grows with it, gently. */
export const dotSize = (adv, min = 5, max = 22) => (known(adv) && adv > 0 ? Math.min(max, min + Math.sqrt(adv) / 4) : min);

/**
 * The universe: every scored stock by relative strength (x) and signed score
 * (y, long up, short down), the picks labelled, the top-10 cut as two lines.
 * Falls back to the picks alone when the scan kept no universe.
 */
export function scatterGeometry({ universe, picks, cut, selected }, { width, height }) {
  const pts = (universe && universe.length ? universe : (picks || []).map((p) => ({ symbol: p.symbol, rs: p.rs, y: p.signed, adv_crore: p.adv_crore })))
    .filter((p) => known(p.rs) && known(p.y));
  if (!pts.length) return null;
  const pickSet = new Map((picks || []).map((p) => [p.symbol, p]));
  const xr = Math.max(8, Math.ceil(Math.max(...pts.map((p) => Math.abs(p.rs))) / 2) * 2);
  const x = linear(-xr, xr, 0, width);
  const y = linear(-1, 1, height, 0);
  const crowd = [];
  const named = [];
  for (const p of pts) {
    const pk = pickSet.get(p.symbol);
    const d = { symbol: p.symbol, x: f1(x(p.rs)), y: f1(y(p.y)), r: f1(dotSize(p.adv_crore)) };
    if (pk) named.push({ ...d, tone: p.y >= 0 ? 'profit' : 'loss', selected: p.symbol === selected, rank: pk.rank });
    else crowd.push(d);
  }
  // labels: the selected pick always, then by rank wherever a name fits
  const placed = [];
  const byRank = [...named].sort((a, b) => Number(b.selected) - Number(a.selected) || a.rank - b.rank);
  for (const p of byRank) {
    const fits = placed.every((q) => Math.abs(Number(q.x) - Number(p.x)) > 80 || Math.abs(Number(q.y) - Number(p.y)) > 15);
    p.label = p.selected || fits;
    if (p.label) placed.push(p);
  }
  return {
    crowd, picks: named.sort((a, b) => b.rank - a.rank), xr, x0: f1(x(0)), y0: f1(y(0)),
    cutHi: known(cut) ? f1(y(cut)) : null, cutLo: known(cut) ? f1(y(-cut)) : null,
    full: !!(universe && universe.length), n: pts.length,
  };
}

/** The scan's steps while it runs: scoring (with counts), selecting, the AI reading. */
export function stepper(v) {
  if (!v || v.state !== 'running') return null;
  const analysing = v.phase === 'analyzing';
  const scoring = !analysing;
  const done = v.total && v.scanned >= v.total;
  return [
    { label: v.total ? `Scoring ${v.scanned} of ${v.total}` : 'Loading the F&O list', state: scoring && !done ? 'now' : 'done' },
    { label: 'Selecting the watchlist', state: analysing || done ? (analysing ? 'done' : 'now') : 'next' },
    { label: 'AI reading the top 10', state: analysing ? 'now' : 'next' },
  ];
}

/** "Scanned 08:52 in 2 min 10 s · AI read the top 10 · qwen2.5:7b". */
export function scanLine(v) {
  if (!v || !v.finished_at) return '';
  const t = String(v.finished_at).slice(11, 16);
  const parts = [`Scanned ${t}${known(v.duration_s) ? ` in ${duration(v.duration_s)}` : ''}`];
  if (v.ai_model) parts.push(`AI read the top 10 with ${v.ai_model.replace(/^ollama:/, '')}`);
  return parts.join(' · ');
}

/** A diverging bar for relative strength, ±8 % full width, as left/width %. */
export function rsBar(rs, range = 8) {
  if (!known(rs)) return null;
  const v = Math.max(-range, Math.min(range, rs)) / range;
  return v >= 0 ? { left: 50, width: v * 50, tone: 'profit' } : { left: 50 + v * 50, width: -v * 50, tone: 'loss' };
}
