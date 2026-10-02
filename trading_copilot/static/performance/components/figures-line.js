// The figures line: the secondary numbers as one quiet line of prose, each
// with its sample size. Below its minimum a figure shows its early estimate
// and likely range, in amber.

import { html, render } from '../../shared/dom.js';
import { inr, notYet, num, pct, plural, r } from '../../shared/format.js';

const UNDEFINED = {
  profit_factor: 'no losing trade yet',
  sharpe: 'no day-to-day variation yet',
};

const known = (v) => typeof v === 'number' && Number.isFinite(v);

/** Before the minimum sample, the early estimate with its likely range
 *  (performance/small_sample.py), marked early -- not a blank. */
function earlyItem(label, e, fmt, level) {
  if (!e || !known(e.value)) return null;
  const range = known(e.low) && known(e.high) ? ` (likely ${fmt(e.low)} to ${fmt(e.high)})` : '';
  return { label, value: `about ${fmt(e.value)}${range}`,
    n: `${plural(e.n, 'trade')}, early · ${Math.round((level || 0.9) * 100)}% range`, amber: true };
}

function item(label, m, fmt, nText, key, e, level) {
  if (!m) return null;
  if (m.status === 'ok') return { label, value: fmt(m.value), n: nText(m), amber: false };
  if (m.status === 'undefined') return { label, value: UNDEFINED[key] || 'no value yet', n: nText(m), amber: false };
  return earlyItem(label, e, fmt, level)
    || { label: `${label}:`, value: 'not yet', n: notYet(m).replace(/^not yet /, ''), amber: true };
}

/** Pure: metrics body -> [{label, value, n, amber}] */
export function figures(metrics) {
  if (!metrics) return [];
  const h = metrics.headline;
  const ra = metrics.risk_adjusted || {};
  const trades = (m) => plural(m.n, 'trade');
  const e = metrics.early || {};
  const out = [
    item('Win rate', h.win_rate, (v) => pct(v), trades, 'win_rate', e.win_rate, e.level),
    item('Profit factor', h.profit_factor, (v) => num(v, 2), trades, 'profit_factor', e.profit_factor, e.level),
    item('Average trade', h.expectancy_r, r, trades, 'expectancy_r', e.expectancy_r, e.level),
  ];
  // How likely the average trade is really above zero: meaningful from 2 trades.
  if (e.p_edge && known(e.p_edge.value)) {
    out.push({ label: 'Chance the edge is real', value: pct(e.p_edge.value),
      n: plural(e.p_edge.n, 'trade'), amber: e.p_edge.n < ((metrics.min_n && metrics.min_n.rates) || 20) });
  }
  const drag = h.cost_drag_pct && h.cost_drag_pct.status === 'ok'
    ? `${pct(h.cost_drag_pct.value)} of gross winnings, ` : '';
  out.push({ label: 'Costs', value: inr(h.costs.value, { signed: false }),
    n: `${drag}${plural(h.costs.n, 'trade')}`, amber: false });
  out.push(item('Sharpe', ra.sharpe, (v) => num(v, 2), (m) => plural(m.n, 'session'), 'sharpe'));
  return out.filter(Boolean);
}

export function mount(el) {
  function update(state) {
    const f = figures(state.metrics);
    if (!f.length) { el.textContent = ''; return; }
    render(el, html`<section class="pf-figures" aria-label="Figures">
      ${f.map((x) => html`<span class="pf-figure${x.amber ? ' pf-amber' : ''}">
        <span class="pf-figure-label">${x.label}</span>
        <span class="pf-figure-value">${x.value}</span>
        <span class="pf-figure-n">${x.n}</span></span>`)}
    </section>`);
  }
  return { update, destroy() { el.textContent = ''; } };
}
