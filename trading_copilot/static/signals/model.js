// The Signals tab's layout and words. Pure: /api/signals/* resolved every
// stage in Python; this places and names things.

import { f1, linear } from '../shared/charts/svg.js';
import { rejectionLabel } from '../shared/labels.js';

const known = (v) => typeof v === 'number' && Number.isFinite(v);
const IST_OFFSET_S = 19800;
const SESSION_MIN = 375;
const sessionMinute = (ts) => (((ts + IST_OFFSET_S) % 86400) / 60) - (9 * 60 + 15);

export const STAGES = [
  { key: 'found', label: 'Found by the math' },
  { key: 'cleared', label: 'Cleared the gates' },
  { key: 'reviewed', label: 'Reviewed by the AI' },
  { key: 'confirmed', label: 'AI said trade' },
  { key: 'opened', label: 'Paper opened' },
];

/** The stage strip: count, share of the ideas found, and why the rest stopped there. */
export function stageStrip(f) {
  if (!f || !f.stages) return null;
  const found = f.stages.found || 0;
  return STAGES.map((s, i) => {
    const n = f.stages[s.key] || 0;
    const drops = Object.entries((f.drops || {})[s.key] || {}).sort((a, b) => b[1] - a[1])
      .map(([why, c]) => `${s.key === 'opened' ? rejectionLabel(why) || why : why} ${c}`);
    return { key: s.key, label: s.label, n, share: found ? n / found : 0,
      tone: s.key === 'confirmed' || s.key === 'opened' ? 'profit' : s.key === 'reviewed' ? 'ind' : 'paper',
      why: i === 0 ? 'Every stock, every 10 s' : drops.join(' · ') };
  });
}

/** Timeline lanes: marks and hold bars on the 09:15-15:30 axis. */
export function laneGeometry(lanes, { width, labelW = 130, rowH = 38, max = 10 }) {
  const x = linear(0, SESSION_MIN, labelW, width);
  const clampX = (ts) => x(Math.max(0, Math.min(SESSION_MIN, sessionMinute(ts))));
  const shown = (lanes || []).slice(0, max);
  return {
    height: 22 + shown.length * rowH + 10,
    lanes: shown.map((l, i) => {
      const y = 22 + i * rowH;
      return {
        symbol: l.symbol, y,
        marks: l.marks.map((m) => ({ x: f1(clampX(m.ts)), kind: m.kind })),
        holds: l.holds.map((h) => ({ x: f1(clampX(h.start)), w: f1(Math.max(3, clampX(h.end) - clampX(h.start))), kind: h.kind })),
      };
    }),
    // every hour on a wide screen; on a narrow one, every other so the labels don't collide
    hours: (width - labelW < 500 ? [[0, '09:15'], [105, '11:00'], [225, '13:00'], [375, '15:30']]
      : [[0, '09:15'], [45, '10:00'], [105, '11:00'], [165, '12:00'], [225, '13:00'], [285, '14:00'], [345, '15:00'], [375, '15:30']])
      .map(([m, t]) => ({ x: f1(x(m)), t })),
    nowX: (ts) => f1(clampX(ts)),
    more: Math.max(0, (lanes || []).length - shown.length),
  };
}

/** The word beside the pipeline dots: the furthest stage that has something to say. */
export function pipelineWord(pipeline) {
  const said = (pipeline || []).map((s, i) => ({ ...s, i })).filter((s) => s.text);
  if (!said.length) return { text: '', tone: 'off' };
  const last = said[said.length - 1];
  const text = last.i === 1 && last.tone === 'bad' ? rejectionLabel(last.text)
    : last.i === 3 && last.tone === 'bad' ? rejectionLabel(last.text) : last.text;
  return { text, tone: last.tone };
}

/** The stop -> target rail: entry and the price as percentages along it. */
export function rail(stop, target, entry, price) {
  if (![stop, target].every(known) || stop === target) return null;
  const at = (v) => (known(v) ? Math.max(0, Math.min(100, ((v - stop) / (target - stop)) * 100)) : null);
  return { entry: at(entry), price: at(price) };
}

/** A queue row passes a chip filter. */
export function inFilter(row, filter) {
  if (filter === 'action') return !!row.wants_action;
  if (filter === 'blocked') return !!row.blocked;
  return true;
}

/** Risk by sector, one bar each against its limit. */
export function sectorBars(exposure) {
  const d = exposure || {};
  return (d.clusters || []).map((c) => ({
    name: c.name || c.cluster, risk: c.open_risk, limit: c.limit,
    share: known(c.pct_of_limit) ? Math.min(1, c.pct_of_limit) : 0, hot: known(c.pct_of_limit) && c.pct_of_limit >= 0.9,
  }));
}

export { sessionMinute };
