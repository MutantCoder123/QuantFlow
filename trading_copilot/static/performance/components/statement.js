// The statement: the account's P&L said as one plain sentence, the page's
// hero. Figures are separate spans so each carries its own tone; below the
// minimum sample the sentence says so instead of quoting a rate.

import { html, render } from '../core/dom.js';
import { hms, inr, isoDay, pct, weekdayDayMonth } from '../core/format.js';

const PHRASE = { today: 'today', '5d': 'over the last 5 days', '1m': 'over the last 30 days', all: 'since paper trading began' };

function rangeName(range, now) {
  if (!range) return '';
  if (range.name === 'today') return `Today, ${weekdayDayMonth(now)}`;
  if (range.name === 'all') return 'All time';
  return `${isoDay(range.first_day)} to ${isoDay(range.last_day)}`;
}

const T = (t) => ({ t });
const F = (t, tone = 'plain') => ({ t, fig: true, tone });

// Open-position count, or null when the engine isn't there to say.
function openClause(live) {
  if (!live || live.running === false) return null;
  return (live.open || []).length;
}

/**
 * Pure: the sentence as segments plus the lines around it.
 * metrics: the /api/paper/metrics body; live: the socket's paper block.
 * -> {meta, segs: [{t, fig?, tone?}], sub}
 */
export function statement(metrics, live, now) {
  if (!metrics) {
    return { meta: 'Loading the paper account…', segs: [T('Reading the trade log.')], sub: '' };
  }
  const h = metrics.headline;
  const range = metrics.range || {};
  const n = h.trades.value;
  const net = h.net_pnl.value;
  const costs = h.costs.value;
  const minRates = (metrics.min_n && metrics.min_n.rates) || 20;
  const phrase = PHRASE[range.name] || `from ${isoDay(range.first_day)} to ${isoDay(range.last_day)}`;
  const openK = openClause(live);
  const frozen = live && live.engine_ok === false;
  const meta = `${rangeName(range, now)} · ${frozen && live.failed_at ? `frozen at ${hms(live.failed_at)}` : `updated ${hms(now)}`} IST`;
  const unreal = live && live.running !== false ? live.unrealized : null;
  const openSentence = openK === null ? '' : openK === 0 ? ' Nothing is open.'
    : ` ${openK === 1 ? '1 trade is' : `${openK} trades are`} open${typeof unreal === 'number' ? `, ${inr(unreal)} unrealised` : ''}.`;

  if (n === 0) {
    if (range.name === 'all') {
      return {
        meta,
        segs: [T('No paper trades yet. They’ll appear here when a signal passes every check during market hours ('),
          F('09:15–13:45'), T(' for new entries).')],
        sub: `Starting capital ${inr(h.start_equity.value, { signed: false })}.${openSentence}`,
      };
    }
    return { meta, segs: [T(`No trades closed ${phrase}.`)], sub: openSentence.trim() };
  }

  const tone = net > 0 ? 'profit' : net < 0 ? 'loss' : 'plain';
  if (n < minRates) {
    return {
      meta,
      segs: [F(String(n)), T(` ${n === 1 ? 'trade' : 'trades'} closed ${phrase}. Too few to call a win rate; the first reading comes at `),
        F(String(minRates), 'amber'), T('.')],
      sub: `Net ${inr(net)} so far, after ${inr(costs, { signed: false })} costs.${openSentence}`,
    };
  }

  const dd = h.max_drawdown.value;
  const lead = net > 0 ? 'Up ' : net < 0 ? 'Down ' : 'Flat, ';
  const segs = [T(lead), F(inr(Math.abs(net), { signed: false }), tone), T(` ${phrase} on `), F(String(n)),
    T(' trades. That’s '), F(pct(Math.abs(h.return_pct.value), { dp: 2 }), tone),
    T(` of ${inr(h.start_equity.value, { signed: false })}. `)];
  if (dd < 0) segs.push(T('The deepest dip was '), F(inr(Math.abs(dd), { signed: false }), 'loss'));
  else segs.push(T('It hasn’t fallen below a previous high'));
  if (openK === null) segs.push(T('.'));
  else if (openK === 0) segs.push(T(', and nothing is open now.'));
  else segs.push(T(', and '), F(String(openK)), T(` ${openK === 1 ? 'trade is' : 'trades are'} open now.`));
  const sub = `Net of ${inr(costs, { signed: false })} costs.`
    + (openK && typeof unreal === 'number' ? ` Open positions add ${inr(unreal)} unrealised.` : '');
  return { meta, segs, sub };
}

export function mount(el) {
  function update(state) {
    const s = statement(state.metrics, state.live, state.now);
    render(el, html`
      <section class="pf-statement" aria-label="Statement">
        <p class="pf-meta">${s.meta}</p>
        <p class="pf-sentence">${s.segs.map((g) => (g.fig
          ? html`<span class="pf-fig" data-tone="${g.tone}">${g.t}</span>`
          : g.t))}</p>
        ${s.sub ? html`<p class="pf-sub">${s.sub}</p>` : ''}
        ${state.metricsError ? html`<p class="pf-inline-error" role="alert">Couldn’t load the figures: ${state.metricsError}</p>` : ''}
      </section>`);
  }
  return { update, destroy() { el.textContent = ''; } };
}

