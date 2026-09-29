// The eight questions the operator asks, each answered from the API's
// breakdowns and diagnostics. Pure: {metrics, breakdowns, diagnostics} ->
// {kind: 'bars' | 'scatter' | 'notYet', bars, points, finding, tunes}.
//
// Honesty rules (PRD §5): a finding never states a rate whose bucket is
// below its minimum sample; small buckets are drawn in amber and never
// ranked; counterfactual figures are labelled as such.

import { inr, pct, plural, r, sentenceText } from './words.js';
import { regimeLabel, sideLabel, exitLabel } from '../../../shared/labels.js';

const val = (m) => (m && m.status === 'ok' ? m.value : null);
const lower = (s) => s.charAt(0).toLowerCase() + s.slice(1);

function minN(ctx, key, fallback) {
  const m = ctx.metrics && ctx.metrics.min_n;
  return (m && m[key]) || fallback;
}

/**
 * Rows -> bars. `value(row)` must return null for a gated value; `fact(row)`
 * says whether the value is a sum (shown even when small, in amber).
 */
export function toBars(rows, { value, fmt, label, min, isFact = false, rank = true }) {
  const items = rows.map((row) => {
    const n = row.n;
    const small = n < min;
    const v = value(row);
    return { key: row.key, label: label(row), n, small, v: small && !isFact ? null : v };
  });
  const ok = items.filter((i) => !i.small && typeof i.v === 'number');
  if (rank) {
    ok.sort((a, b) => b.v - a.v);
  }
  const small = items.filter((i) => i.small);
  const ordered = rank ? [...ok, ...small] : items;
  const scaleOn = (ok.length ? ok : items.filter((i) => typeof i.v === 'number'));
  const max = Math.max(1e-9, ...scaleOn.map((i) => Math.abs(i.v)));
  return ordered.map((i) => ({
    key: i.key,
    label: i.label,
    n: plural(i.n, 'trade'),
    small: i.small,
    text: typeof i.v !== 'number' ? `not yet (${i.n} of ${min})` : fmt(i.v),
    // half-width bars around a centre line: -50..+50 % of the track
    share: typeof i.v === 'number' ? (Math.min(Math.abs(i.v) / max, 1) * 48) : 0,
    negative: typeof i.v === 'number' && i.v < 0,
  }));
}

const smallNote = (bars, min, what) => (bars.some((b) => b.small)
  ? ` ${what} with fewer than ${min} trades are shown in amber and not ranked.` : '');

// ------------------------------------------------------------- 1 conditions
function conditions(ctx) {
  const min = minN(ctx, 'bucket', 10);
  const rows = (ctx.breakdowns.regime_side || []).filter((x) => x.key);
  const label = (row) => {
    const [side, regime] = row.key.split('|');
    return `${side === 'LONG' ? 'Longs' : side === 'SHORT' ? 'Shorts' : sideLabel(side)} in ${regimeLabel(regime).toLowerCase()}`;
  };
  const bars = toBars(rows, { value: (x) => val(x.expectancy_r), fmt: r, label, min });
  const ok = bars.filter((b) => !b.small);
  const byKey = Object.fromEntries(rows.map((x) => [x.key, x]));
  let finding;
  if (ok.length >= 2) {
    const best = byKey[ok[0].key];
    const worst = byKey[ok[ok.length - 1].key];
    const bw = val(best.expectancy_r);
    const ww = val(worst.expectancy_r);
    finding = `${sentenceText(label(best))} average ${r(bw)} across ${best.n} trades; `
      + `${lower(label(worst))} ${ww < 0 ? 'lose' : 'average'} ${r(ww)} across ${worst.n}.`;
  } else if (ok.length === 1) {
    const only = byKey[ok[0].key];
    finding = `Only ${lower(label(only))} have ${min} or more trades so far: ${r(val(only.expectancy_r))} across ${only.n}.`;
  } else {
    const most = rows.reduce((a, x) => Math.max(a, x.n), 0);
    finding = `No combination of side and market condition has ${min} trades yet; the largest has ${most}.`;
  }
  return { kind: 'bars', bars, finding: finding + smallNote(bars, min, 'Combinations'),
    tunes: ['gates.regime_dampening', 'conviction.bias_threshold'] };
}

// ------------------------------------------------------------------ 2 stocks
function stocks(ctx) {
  const min = minN(ctx, 'bucket', 10);
  const rows = (ctx.breakdowns.symbol || []).filter((x) => x.key);
  const bars = toBars(rows, { value: (x) => val(x.net), fmt: (v) => inr(v), label: (x) => x.label, min, isFact: true });
  const ok = bars.filter((b) => !b.small);
  const byKey = Object.fromEntries(rows.map((x) => [x.key, x]));
  let finding;
  const losers = ok.filter((b) => b.negative);
  if (losers.length >= 2) {
    const [a, b] = losers.slice(-2).reverse().map((x) => byKey[x.key]);
    finding = `${a.label} and ${b.label} cost ${inr(Math.abs(val(a.net) + val(b.net)), { signed: false })} together `
      + `across ${a.n + b.n} trades. ${byKey[ok[0].key].label} made the most, ${inr(val(byKey[ok[0].key].net))} across ${byKey[ok[0].key].n}.`;
  } else if (losers.length === 1) {
    const a = byKey[losers[0].key];
    finding = `${a.label} is the only stock with ${min}+ trades that lost money: ${inr(val(a.net))} across ${a.n}.`;
  } else if (ok.length) {
    finding = `No stock with ${min} or more trades has lost money so far.`;
  } else {
    finding = `No stock has ${min} trades yet, so none can be judged on its own.`;
  }
  return { kind: 'bars', bars, finding: finding + smallNote(bars, min, 'Stocks'),
    tunes: ['watchlist_policy.core', 'watchlist_policy.min_adv_crore'] };
}

// ------------------------------------------------------------------- 3 exits
function exits(ctx) {
  const min = minN(ctx, 'bucket', 10);
  const rows = (ctx.diagnostics.exits && ctx.diagnostics.exits.rows) || [];
  const bars = toBars(rows, { value: (x) => val(x.net), fmt: (v) => inr(v), label: (x) => exitLabel(x.key), min, isFact: true });
  const byKey = Object.fromEntries(rows.map((x) => [x.key, x]));
  const stop = byKey.STOP;
  const unplanned = rows.filter((x) => !['STOP', 'TARGET'].includes(x.key) && x.n >= min && val(x.net) < 0)
    .sort((a, b) => val(a.net) - val(b.net));
  const parts = [];
  if (stop) parts.push(`Planned stops cost ${inr(val(stop.net))} across ${plural(stop.n, 'trade')}.`);
  if (unplanned.length) {
    const w = unplanned[0];
    parts.push(`${exitLabel(w.key)} exits are the largest unplanned cost, ${inr(val(w.net))} across ${w.n}.`);
  } else if (rows.some((x) => !['STOP', 'TARGET'].includes(x.key))) {
    parts.push(`No rule-based exit with ${min}+ trades has lost money.`);
  }
  const tunes = [...new Set([...(stop ? stop.tunes : []), ...(unplanned[0] ? unplanned[0].tunes : [])])];
  return { kind: 'bars', bars, finding: (parts.join(' ') || 'No exits yet.') + smallNote(bars, min, 'Exits'),
    tunes: tunes.length ? tunes : ['gates.stop_proximity_pct'] };
}

// ------------------------------------------------------------------- 4 stops
function stops(ctx) {
  const e = ctx.diagnostics.excursions || {};
  const up = e.stops_after_half_r_up_pct;
  const near = e.wins_near_stop_pct;
  const parts = [];
  if (val(up) !== null) {
    parts.push(`${pct(val(up), { dp: 0 })} of the ${up.n} trades that hit their stop were up +0.50 R or more first.`);
  } else if (up) {
    parts.push(`${plural(up.n, 'trade')} hit the stop — too few to say how many were in profit first (needs ${up.min_n}).`);
  }
  if (val(near) !== null) {
    parts.push(`${pct(val(near), { dp: 0 })} of ${near.n} winners came within 0.20 R of the stop before winning`
      + (val(near) < 15 ? ', so winners have rarely needed all of the stop’s room.' : '.'));
  }
  const gb = val(e.avg_given_back_r);
  if (gb !== null) parts.push(`On average a trade gave back ${r(gb).replace('+', '')} from its best point before it closed.`);
  return { kind: 'scatter', points: e.points || [], finding: parts.join(' ') || 'No stopped trades yet.', tunes: e.tunes || [] };
}

// ------------------------------------------------------------------- 5 score
function score(ctx) {
  const min = minN(ctx, 'calibration', 15);
  const c = ctx.diagnostics.calibration || {};
  const rows = (c.buckets || []).filter((x) => x.bucket).map((x) => ({ ...x, key: x.bucket }));
  const bars = toBars(rows, { value: (x) => val(x.expectancy_r), fmt: r, label: (x) => `Score ${x.bucket}`, min, rank: false });
  const ok = rows.filter((x) => x.n >= min && val(x.expectancy_r) !== null);
  let finding;
  if (ok.length >= 2) {
    const lo = ok[0];
    const hi = ok[ok.length - 1];
    finding = `Trades scored ${hi.bucket} average ${r(val(hi.expectancy_r))} across ${hi.n}; those scored ${lo.bucket} average ${r(val(lo.expectancy_r))} across ${lo.n}.`;
    if (c.monotonic === true) finding += ' Win rate rises with every bucket that has enough trades.';
    if (c.monotonic === false) finding += ' Win rate does not rise steadily with the score.';
  } else {
    finding = `A score bucket needs ${min} trades to be compared; ${ok.length ? 'only one has' : 'none has'} that many yet.`;
  }
  return { kind: 'bars', bars, finding: finding + smallNote(bars, min, 'Buckets'), tunes: c.tunes || [] };
}

// ---------------------------------------------------------------------- 6 AI
function ai(ctx) {
  const min = minN(ctx, 'bucket', 10);
  const a = ctx.diagnostics.ai || {};
  const verdicts = (a.by_verdict || []).filter((x) => x.key).map((x) => ({ ...x, label: `Paper trades the AI ${x.key === 'ADJUST' ? 'adjusted' : 'confirmed'}` }));
  const rows = [
    ...verdicts,
    { key: 'taken', label: 'Signals it took (scored)', n: (a.taken || {}).labelled || 0, expectancy_r: (a.taken || {}).avg_math_r },
    { key: 'vetoed', label: 'Signals it turned down (scored)', n: (a.vetoed || {}).labelled || 0, expectancy_r: (a.vetoed || {}).avg_math_r },
  ];
  const bars = toBars(rows, { value: (x) => val(x.expectancy_r), fmt: r, label: (x) => x.label, min, rank: false });
  const v = val((a.vetoed || {}).avg_math_r);
  const t = val((a.taken || {}).avg_math_r);
  let finding;
  if (v !== null && t !== null) {
    finding = `Signals the AI turned down would have averaged ${r(v)} across ${a.vetoed.labelled}; those it took averaged ${r(t)} across ${a.taken.labelled}, scored the same way.`
      + (v < t ? ' So far its vetoes have avoided the weaker trades.' : ' So far its vetoes have not avoided weaker trades.');
  } else {
    finding = `The AI has turned down ${plural((a.vetoed || {}).n || 0, 'signal')}, ${(a.vetoed || {}).labelled || 0} of them scored so far; comparing vetoes with trades needs ${min} scored on each side.`;
  }
  finding += ' Scored = a counterfactual from the fixed-horizon labeller, before costs; paper trades are after costs.';
  return { kind: 'bars', bars, finding, tunes: a.tunes || [] };
}

// ----------------------------------------------------------------- 7 timing
function timing(ctx) {
  const min = minN(ctx, 'bucket', 10);
  const rows = (ctx.breakdowns.hour || []).filter((x) => x.key);
  const span = (k) => `${k}–${String(Number(k.slice(0, 2)) + 1).padStart(2, '0')}:00`;
  const bars = toBars(rows, { value: (x) => val(x.expectancy_r), fmt: r, label: (x) => `Entered ${span(x.key)}`, min, rank: false });
  const ok = rows.filter((x) => x.n >= min && val(x.expectancy_r) !== null).sort((a, b) => val(b.expectancy_r) - val(a.expectancy_r));
  let finding;
  if (ok.length >= 2) {
    const b = ok[0];
    const w = ok[ok.length - 1];
    finding = `Entries between ${span(b.key)} average ${r(val(b.expectancy_r))} across ${b.n} trades; entries between ${span(w.key)} average ${r(val(w.expectancy_r))} across ${w.n}.`;
  } else {
    finding = `An hour needs ${min} entries to be compared; ${ok.length ? 'only one has' : 'none has'} that many yet.`;
  }
  return { kind: 'bars', bars, finding: finding + smallNote(bars, min, 'Hours'),
    tunes: ['horizon.entry_cutoff_ist', 'gates.failure_to_launch_min'] };
}

// ------------------------------------------------------------------ 8 costs
function costs(ctx) {
  const h = ctx.metrics.headline;
  const c = ctx.diagnostics.costs || {};
  const gross = h.gross_pnl.value;
  const cost = h.costs.value;
  const n = h.trades.value;
  const rows = [
    { key: 'gross', label: 'Gross P&L', n, v: gross },
    { key: 'costs', label: 'Costs', n, v: -cost },
    { key: 'net', label: 'Net P&L', n, v: h.net_pnl.value },
  ];
  const bars = toBars(rows, { value: (x) => x.v, fmt: (v) => inr(v), label: (x) => x.label, min: 0, isFact: true, rank: false });
  const parts = [`Costs took ${inr(cost, { signed: false })} across ${plural(n, 'trade')}`];
  const drag = val(h.cost_drag_pct);
  if (drag !== null) parts[0] += `, ${pct(drag)} of the gross winnings`;
  parts[0] += '.';
  const per = val(c.per_trade);
  const perR = val(h.cost_per_trade_r);
  if (per !== null) parts.push(`The average trade paid ${inr(per, { signed: false })}${perR !== null ? ` (${r(perR).replace('+', '')})` : ''}.`);
  const flipped = val(c.flipped_to_loss);
  if (flipped) parts.push(`${plural(flipped, 'trade')} ${flipped === 1 ? 'was' : 'were'} a gross win but a net loss.`);
  return { kind: 'bars', bars, finding: parts.join(' '), tunes: c.tunes || [] };
}

// --------------------------------------------------------------- 9 versions
function versions(ctx) {
  const min = minN(ctx, 'bucket', 10);
  const rows = (ctx.breakdowns.config_version || []).filter((x) => x.key !== null && x.key !== undefined);
  const bars = toBars(rows, { value: (x) => val(x.expectancy_r), fmt: r, label: (x) => `Policy v${x.key}`, min, rank: false });
  const ok = rows.filter((x) => x.n >= min && val(x.expectancy_r) !== null);
  let finding;
  if (ok.length >= 2) {
    const [prev, last] = ok.slice(-2);
    const d = val(last.expectancy_r) - val(prev.expectancy_r);
    finding = `Policy v${last.key} averages ${r(val(last.expectancy_r))} per trade across ${last.n}, against `
      + `${r(val(prev.expectancy_r))} across ${prev.n} for v${prev.key}: ${d > 0 ? 'better' : d < 0 ? 'worse' : 'no different'}`
      + `${d ? ` by ${Math.abs(d).toFixed(2)} R` : ''}.`;
  } else if (rows.length <= 1) {
    finding = 'Only one policy version has traded in this range. The comparison starts after the next change to the decision policy.';
  } else {
    finding = `Each version needs ${min} trades to be compared; ${ok.length ? 'only one has' : 'none has'} that many yet.`;
  }
  return { kind: 'bars', bars, finding, tunes: [] };
}

export const QUESTIONS = [
  ['conditions', 'Which market conditions pay?', conditions],
  ['stocks', 'Which stocks cost us?', stocks],
  ['exits', 'Which exits cost money?', exits],
  ['stops', 'Are stops too tight?', stops],
  ['score', 'Does a higher score win more?', score],
  ['ai', 'Is the AI worth it?', ai],
  ['timing', 'When in the day is the edge?', timing],
  ['costs', 'Are costs eating the edge?', costs],
  ['versions', 'Did my last change help?', versions],
];

/** Pure: the answer to question `id`, or the not-yet state for all of them. */
export function answer(id, ctx) {
  const q = QUESTIONS.find((x) => x[0] === id) || QUESTIONS[0];
  if (!ctx.metrics || !ctx.breakdowns || !ctx.diagnostics) return { kind: 'loading', question: q[1] };
  const n = ctx.metrics.headline.trades.value;
  const need = minN(ctx, 'rates', 20);
  if (n < need) {
    return { kind: 'notYet', question: q[1], text: `Not enough data yet: ${n} of ${need} closed trades. The first answers appear at ${need}.`, tunes: [] };
  }
  return { question: q[1], ...q[2](ctx) };
}
