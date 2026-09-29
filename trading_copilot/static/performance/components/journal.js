// "Every trade": the journal. Filterable; a row opens in place to tell the
// trade's whole story -- why it entered, how it was sized, its fills and
// costs, its worst and best points, and why it exited. Exports link to the
// API. Nothing here is estimated: a value the trade didn't record is shown
// as unknown.

import { html, render } from '../../shared/dom.js';
import { DASH, dayMonth, duration, group, hm, inr, r, trim } from '../../shared/format.js';
import { exitLabel, exitTone, regimeLabel, sideLabel } from '../../shared/labels.js';
import { whenChanged } from '../../shared/store.js';

const PAGE = 10;
const COSTS = [['brokerage', 'Brokerage'], ['stt', 'STT'], ['exchange', 'Exchange'], ['sebi', 'SEBI'],
  ['stamp', 'Stamp'], ['gst', 'GST']];

const ctx = (t) => t.context || {};

/** Pure: the options each filter offers, from the trades themselves. */
export function filterOptions(trades) {
  const uniq = (f) => [...new Set(trades.map(f).filter(Boolean))].sort();
  return { side: uniq((t) => t.side), exit: uniq((t) => t.reason), regime: uniq((t) => ctx(t).regime) };
}

/** Pure: newest first, filtered. */
export function filterTrades(trades, f = {}) {
  const q = (f.symbol || '').trim().toUpperCase();
  return trades
    .filter((t) => (!q || t.symbol.toUpperCase().includes(q))
      && (!f.side || t.side === f.side)
      && (!f.exit || t.reason === f.exit)
      && (!f.regime || ctx(t).regime === f.regime))
    .sort((a, b) => b.closed_ts - a.closed_ts);
}

/** Pure: why it exited, in a sentence. */
export function exitWhy(t) {
  const at = hm(t.closed_ts);
  const held = duration((t.hold_min ?? 0) * 60);
  const base = {
    TARGET: `Reached the ${group(t.target, 2)} target at ${at}, ${held} in.`,
    STOP: `Hit the ${group(t.stop, 2)} stop at ${at}, ${held} in.`,
    GATEKEEPER_STOP_PROXIMITY: `Price came close to the stop; the gatekeeper closed it at ${at}, ${held} in.`,
    GATEKEEPER_WHALE_FLIP: `Large-order flow turned against the position; closed at ${at}, ${held} in.`,
    LLM_CLOSE: `The AI judge said the setup no longer held; closed at ${at}, ${held} in.`,
    LLM_REVERSE: `The AI judge called a reversal; closed at ${at} (no automatic re-entry).`,
    SQUARE_OFF: `Closed at the ${at} square-off.`,
    RECOVERED_STALE: 'Still open when the engine restarted on a later day; closed at its last known price.',
  }[t.reason] || `Closed at ${at} (${exitLabel(t.reason)}).`;
  const notes = [];
  if (t.touch_check === 'ltp_only') notes.push('Checked against the last price, not bars, so the fill is the price seen.');
  if ((t.flags || []).includes('STALE_EXIT_PRICE')) notes.push('No fresh price at the exit: priced at the last known mark.');
  return [base, ...notes].join(' ');
}

/** Pure: the worst/best/exit bar, positions in % across the scale. */
export function excursionBar(t) {
  const rps = Math.abs(t.entry_price - t.stop);
  const targetR = rps > 0 ? Math.abs(t.target - t.entry_price) / rps : null;
  const pts = { stop: -1, entry: 0, target: targetR, worst: t.mae_r, best: t.mfe_r, exit: t.r_gross };
  const vals = Object.values(pts).filter((v) => typeof v === 'number');
  const lo = Math.min(...vals) - 0.15;
  const hi = Math.max(...vals) + 0.15;
  const at = (v) => (typeof v === 'number' ? ((v - lo) / (hi - lo)) * 100 : null);
  return Object.fromEntries(Object.entries(pts).map(([k, v]) => [k, { r: v, at: at(v) }]));
}

/** Pure: the expanded story of one trade. */
export function story(t) {
  const c = ctx(t);
  const st = t.settings || {};
  const long = t.side === 'LONG';
  const [open, close] = long ? ['Buy', 'Sell'] : ['Sell', 'Buy'];
  const dist = Math.abs(t.entry_price - t.stop);
  const full = st.capital && st.risk_per_trade_pct ? (st.capital * st.risk_per_trade_pct) / 100 : null;
  let sizing = full
    ? `Risk ${inr(full, { signed: false })} (${trim(st.risk_per_trade_pct)}% of ${inr(st.capital, { signed: false })}) ÷ stop distance ₹${group(dist, 2)} = ${group(t.qty)} shares.`
    : `${group(t.qty)} shares at a stop distance of ₹${group(dist, 2)}.`;
  if (full && t.risk_amount < full - dist) {
    sizing += ` Cut to ${inr(t.risk_amount, { signed: false })} at risk by the liquidity or sector cap.`;
  }
  const costs = t.costs || {};
  return {
    entry: [c.composite !== undefined && c.composite !== null ? `Score ${Math.abs(c.composite).toFixed(2)}` : null,
      c.regime ? regimeLabel(c.regime) : null, c.verdict ? `AI ${c.verdict}` : null].filter(Boolean).join(' · ') || 'No entry context recorded',
    reason: c.rationale || '',
    sizing,
    fillIn: `${hm(t.opened_ts)}  ${open} ${group(t.qty)} @ ${group(t.entry_price, 2)}`,
    fillOut: `${hm(t.closed_ts)}  ${close} ${group(t.qty)} @ ${group(t.exit_price, 2)}`,
    slipNote: st.slippage_pct !== undefined ? `Simulated fills with ${trim(st.slippage_pct)}% slippage per leg.` : '',
    costs: COSTS.map(([k, label]) => ({ k: label, v: typeof costs[k] === 'number' ? inr(costs[k], { signed: false, dp: 2 }) : DASH })),
    costTotal: inr(costs.total, { signed: false, dp: 2 }),
    gross: inr(t.gross), net: inr(t.net), netTone: t.net > 0 ? 'profit' : t.net < 0 ? 'loss' : 'plain',
    bar: excursionBar(t),
    exitWhy: exitWhy(t),
    version: t.config_version !== undefined && t.config_version !== null ? `Policy v${t.config_version}` : '',
  };
}

export function journalRow(t) {
  const c = ctx(t);
  return {
    id: t.pos_id, when: `${dayMonth(t.closed_ts)}, ${hm(t.closed_ts)}`, symbol: t.symbol, side: sideLabel(t.side),
    qty: group(t.qty), px: `${group(t.entry_price, 2)} → ${group(t.exit_price, 2)}`,
    net: inr(t.net), r: r(t.r_net), tone: t.net > 0 ? 'profit' : t.net < 0 ? 'loss' : 'plain',
    exit: exitLabel(t.reason), exitTone: exitTone(t.reason), regime: regimeLabel(c.regime),
    score: c.composite === undefined || c.composite === null ? DASH : Math.abs(c.composite).toFixed(2),
  };
}

function select(name, label, values, current, fmt) {
  return html`<label class="pf-filter"><span class="pf-sr">${label}</span>
    <select data-filter="${name}" data-key="filter-${name}">
      <option value="">${label}: all</option>
      ${values.map((v) => html`<option value="${v}" ${v === current ? 'selected' : ''}>${label}: ${fmt(v)}</option>`)}
    </select></label>`;
}

function barMarks(b) {
  const mark = (k, cls, text) => (b[k].at === null ? '' : html`<span class="pf-xb-${cls}" style="left:${b[k].at.toFixed(1)}%" title="${text}"></span>`);
  return html`
    <div class="pf-xb" role="img" aria-label="${`Worst ${r(b.worst.r)}, best ${r(b.best.r)}, exit ${r(b.exit.r)}; stop at −1.00 R${b.target.r !== null ? `, target at ${r(b.target.r)}` : ''}`}">
      <span class="pf-xb-track"></span>
      ${b.worst.at !== null && b.best.at !== null ? html`<span class="pf-xb-range" style="left:${b.worst.at.toFixed(1)}%;width:${(b.best.at - b.worst.at).toFixed(1)}%"></span>` : ''}
      ${mark('stop', 'stop', 'Stop, −1.00 R')}${mark('target', 'target', `Target, ${r(b.target.r)}`)}
      ${mark('entry', 'entry', 'Entry, 0 R')}${mark('exit', 'exit', `Exit, ${r(b.exit.r)}`)}
    </div>
    <div class="pf-xb-legend pf-meta"><span class="pf-loss">worst ${r(b.worst.r)}</span><span class="pf-profit">best ${r(b.best.r)}</span><span>exit ${r(b.exit.r)}</span></div>`;
}

export function mount(el, store, actions) {
  let showAll = false;
  const filters = { symbol: '', side: '', exit: '', regime: '' };

  el.addEventListener('click', (e) => {
    const row = e.target.closest('[data-row]');
    if (row) { actions.openTrade(row.dataset.row === store.get().openTrade ? null : row.dataset.row, false); return; }
    if (e.target.closest('[data-act="all"]')) { e.preventDefault(); showAll = !showAll; draw(store.get()); }
  });
  el.addEventListener('keydown', (e) => {
    const row = e.target.closest('[data-row]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); row.click(); }
  });
  el.addEventListener('change', (e) => {
    const f = e.target.dataset.filter;
    if (f) { filters[f] = e.target.value; draw(store.get()); }
  });
  el.addEventListener('input', (e) => {
    if (e.target.dataset.filter === 'symbol') { filters.symbol = e.target.value; draw(store.get()); }
  });

  function draw(state) {
    const all = state.trades;
    if (!all) {
      render(el, html`<section class="pf-journal" aria-label="Every trade"><h2 class="pf-h2">Every trade</h2>
        <p class="pf-meta">${state.tradesError ? `Couldn’t load the trades: ${state.tradesError}` : 'Loading…'}</p></section>`);
      return;
    }
    const opts = filterOptions(all);
    let rows = filterTrades(all, filters);
    // a trade opened from the tape must be visible, whatever the filters and page
    if (state.openTrade && state.scrollTo && !rows.some((t) => t.pos_id === state.openTrade)
        && all.some((t) => t.pos_id === state.openTrade)) {
      Object.assign(filters, { symbol: '', side: '', exit: '', regime: '' });
      rows = filterTrades(all, filters);
    }
    if (state.openTrade && state.scrollTo && rows.findIndex((t) => t.pos_id === state.openTrade) >= PAGE) showAll = true;
    const shown = showAll ? rows : rows.slice(0, PAGE);
    const range = encodeURIComponent(state.range);
    render(el, html`
      <section class="pf-journal" aria-label="Every trade">
        <div class="pf-sec-head pf-wrap">
          <h2 class="pf-h2">Every trade</h2>
          <span class="pf-meta">${all.length ? `${all.length} closed ${all.length === 1 ? 'trade' : 'trades'}` : 'No closed trades'}</span>
          <span class="pf-grow"></span>
          <div class="pf-filters">
            <label class="pf-search"><span class="pf-sr">Symbol</span><input type="search" placeholder="Symbol"
              data-filter="symbol" data-key="filter-symbol" value="${filters.symbol}" autocomplete="off"></label>
            ${select('side', 'Side', opts.side, filters.side, sideLabel)}
            ${select('exit', 'Exit', opts.exit, filters.exit, exitLabel)}
            ${select('regime', 'Regime', opts.regime, filters.regime, regimeLabel)}
            <span class="pf-divider" aria-hidden="true"></span>
            <a class="pf-pill pf-link" href="/api/paper/export?range=${range}&fmt=csv" download data-key="csv">Export CSV</a>
            <a class="pf-pill pf-link" href="/api/paper/export?range=${range}&fmt=parquet" download data-key="parquet">Export Parquet</a>
          </div>
        </div>
        <div role="table" aria-label="Closed trades" class="pf-table pf-journal-table">
          <div role="row" class="pf-row pf-head">
            <span role="columnheader">Closed</span><span role="columnheader">Symbol</span><span role="columnheader">Side</span>
            <span role="columnheader" class="pf-num">Qty</span><span role="columnheader">Entry → exit</span>
            <span role="columnheader" class="pf-num">Net</span><span role="columnheader" class="pf-num">R</span>
            <span role="columnheader">Exit</span><span role="columnheader">Regime</span>
            <span role="columnheader" class="pf-num">Score</span><span role="columnheader"><span class="pf-sr">Details</span></span>
          </div>
          ${shown.map((t) => {
            const j = journalRow(t);
            const isOpen = state.openTrade === j.id;
            const s = isOpen ? story(t) : null;
            return html`<div class="pf-jrow${isOpen ? ' pf-open' : ''}" id="pf-trade-${j.id}">
              <div role="row" class="pf-row" tabindex="0" data-row="${j.id}" data-key="row-${j.id}" aria-expanded="${isOpen}">
                <span role="cell" class="pf-mute pf-when">${j.when}</span>
                <span role="cell" class="pf-sym">${j.symbol}</span>
                <span role="cell" class="pf-mute pf-side">${j.side}</span>
                <span role="cell" class="pf-num pf-qty">${j.qty}</span>
                <span role="cell" class="pf-mute pf-px">${j.px}</span>
                <span role="cell" class="pf-num pf-net" data-tone="${j.tone}">${j.net}</span>
                <span role="cell" class="pf-num pf-r" data-tone="${j.tone}">${j.r}</span>
                <span role="cell" class="pf-exit-cell"><span class="pf-chip"><i data-tone="${j.exitTone}"></i>${j.exit}</span></span>
                <span role="cell" class="pf-mute pf-regime">${j.regime}</span>
                <span role="cell" class="pf-num pf-score">${j.score}</span>
                <span role="cell" class="pf-chev" aria-hidden="true">${isOpen ? '−' : '+'}</span>
              </div>
              ${s ? html`<div class="pf-story">
                <div><span class="pf-meta">Why it entered</span><strong>${s.entry}</strong>${s.reason ? html`<span class="pf-quote">“${s.reason}”</span>` : ''}${s.version ? html`<span class="pf-meta">${s.version}</span>` : ''}</div>
                <div><span class="pf-meta">How it was sized</span><span>${s.sizing}</span></div>
                <div><span class="pf-meta">Fills and costs</span><span>${s.fillIn}</span><span>${s.fillOut}</span>
                  <div class="pf-costs">${s.costs.map((c) => html`<span>${c.k}</span><span class="pf-num">${c.v}</span>`)}
                    <span class="pf-total">Costs</span><span class="pf-num pf-total">${s.costTotal}</span>
                    <span>Gross → net</span><span class="pf-num">${s.gross} → <span data-tone="${s.netTone}">${s.net}</span></span></div>
                  ${s.slipNote ? html`<span class="pf-meta">${s.slipNote}</span>` : ''}</div>
                <div><span class="pf-meta">Worst, best and exit, in R</span>${barMarks(s.bar)}</div>
                <div><span class="pf-meta">Why it exited</span><span class="pf-chip"><i data-tone="${j.exitTone}"></i>${j.exit}</span><span>${s.exitWhy}</span></div>
              </div>` : ''}
            </div>`;
          })}
          ${!rows.length ? html`<div role="row" class="pf-row pf-empty"><span role="cell">${all.length ? 'No trades match these filters.' : 'No closed trades yet.'}</span></div>` : ''}
        </div>
        ${rows.length > PAGE ? html`<p class="pf-meta pf-foot">${showAll ? `Showing all ${rows.length}` : `Showing ${PAGE} of ${rows.length}`} · <a href="#" data-act="all" data-key="all">${showAll ? 'Show fewer' : 'Show all'}</a></p>` : ''}
      </section>`);
    if (state.scrollTo && state.openTrade) {
      const t = el.querySelector(`#pf-trade-${CSS.escape(state.openTrade)}`);
      if (t) t.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }

  const update = whenChanged((s) => [s.trades, s.tradesError, s.openTrade, s.range], draw);
  return { update, destroy() { el.textContent = ''; } };
}
