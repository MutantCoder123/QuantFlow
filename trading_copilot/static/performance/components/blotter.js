// "Open now": the live positions as a ledger. Each row has a rail from the
// stop (coral) to the target (jade), a tick at entry and a dot at the last
// price. Rows come from the socket's paper block, so they move at 2 Hz.
// Clicking a row opens the page's existing analysis modal for the symbol.

import { html, render } from '../core/dom.js';
import { MINUS, duration, group, hm, inr, r } from '../core/format.js';
import { sideLabel } from '../core/labels.js';

const WARN_BEFORE_MIN = 10;          // the time cell turns amber this long before the launch check

/** Pure: one open position -> what its row shows. */
export function blotterRow(p, live, now) {
  const long = p.side === 'LONG';
  const last = typeof p.last === 'number' ? p.last : p.entry_price;
  const span = Math.abs(p.target - p.stop) || 1;
  const at = (v) => Math.min(1, Math.max(0, Math.abs(v - p.stop) / span));
  const e = at(p.entry_price);
  const l = at(last);
  const ahead = l >= e;
  const clock = (live && live.clock) || {};
  const ftl = clock.failure_to_launch_min;
  const mins = (now - p.ts) / 60;
  const down = live && live.engine_ok === false;
  const staleAfter = (live && live.stale_price_seconds) || 15;
  const markAge = typeof p.last_mark_ts === 'number' ? Math.max(0, now - p.last_mark_ts) : null;
  const stale = !down && (markAge === null || markAge > staleAfter);

  let timeNote = '';
  let timeAmber = false;
  if (down) {
    timeNote = live.failed_at ? `since ${hm(live.failed_at)}` : 'frozen';
    timeAmber = true;
  } else if (typeof ftl === 'number' && mins >= ftl - WARN_BEFORE_MIN && mins < ftl) {
    const left = Math.max(1, Math.ceil(ftl - mins));
    timeNote = `${left} min to the ${ftl}-min launch check`;
    timeAmber = true;
  }
  const pctWay = Math.round(l * 100);
  return {
    pos_id: p.pos_id, symbol: p.symbol, side: sideLabel(p.side), qty: group(p.qty),
    entry: group(p.entry_price, 2), last: group(last, 2), lastAmber: down || stale,
    priceNote: down ? '' : stale ? (markAge === null ? 'no price yet' : `${duration(markAge)} old`) : '',
    unrealised: inr(p.unrealized), rNow: r(p.r_now), tone: p.unrealized > 0 ? 'profit' : p.unrealized < 0 ? 'loss' : 'plain',
    stop: group(p.stop, 2), target: group(p.target, 2),
    rail: { entry: e * 100, last: l * 100, segL: Math.min(e, l) * 100, segW: Math.abs(l - e) * 100, ahead },
    railText: `${long ? 'Long' : 'Short'}: ${pctWay}% of the way from stop ${group(p.stop, 2)} to target ${group(p.target, 2)}`,
    time: down ? 'frozen' : duration(now - p.ts), timeNote, timeAmber,
  };
}

/** Pure: the header and footer lines. */
export function blotterFrame(live) {
  if (!live || live.running === false) return { risk: '', footer: '' };
  const limit = live.daily_loss_limit;
  const clock = live.clock || {};
  const when = [clock.entry_cutoff && `new entries until ${clock.entry_cutoff}`,
    clock.square_off && `square-off at ${clock.square_off}`].filter(Boolean).join(', ');
  return {
    risk: `At the stops: ${inr(live.open_risk, { signed: false })} of the ${inr(limit, { signed: false })} daily loss limit`,
    footer: `Today: ${inr(live.realized_today)} realised against a ${MINUS}${inr(limit, { signed: false })} limit${when ? `; ${when}` : ''}.`,
  };
}

export function mount(el) {
  const open = (row) => {
    if (row && typeof globalThis.openJsonModal === 'function') globalThis.openJsonModal(row.dataset.symbol);
  };
  el.addEventListener('click', (e) => open(e.target.closest('[data-symbol]')));
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      const row = e.target.closest('[data-symbol]');
      if (row) { e.preventDefault(); open(row); }
    }
  });

  function update(state) {
    const live = state.live;
    const running = live && live.running !== false;
    const rows = running ? (live.open || []).map((p) => blotterRow(p, live, state.now)) : [];
    const f = blotterFrame(live);
    const down = live && live.engine_ok === false;
    render(el, html`
      <section class="pf-blotter" aria-label="Open now">
        <div class="pf-sec-head">
          <h2 class="pf-h2">Open now</h2>
          ${down && live.failed_at ? html`<span class="pf-meta pf-amber">Prices as of ${hm(live.failed_at)}</span>` : ''}
          <span class="pf-grow"></span>
          <span class="pf-meta pf-risk">${f.risk}</span>
        </div>
        <div role="table" aria-label="Open positions" class="pf-table pf-blotter-table">
          <div role="row" class="pf-row pf-head">
            <span role="columnheader">Symbol</span><span role="columnheader">Side</span>
            <span role="columnheader" class="pf-num">Qty</span><span role="columnheader">Entry → last</span>
            <span role="columnheader" class="pf-num">Unrealised</span><span role="columnheader" class="pf-num">Unrealised R</span>
            <span role="columnheader">Stop → target</span><span role="columnheader" class="pf-num">Time in trade</span>
          </div>
          ${rows.map((p) => html`
          <div role="row" class="pf-row pf-pos" tabindex="0" data-symbol="${p.symbol}" data-key="pos-${p.pos_id}" title="Open the analysis for ${p.symbol}">
            <span role="cell" class="pf-sym">${p.symbol}</span>
            <span role="cell" class="pf-mute pf-side">${p.side}<span class="pf-only-narrow"> · ${p.qty}</span></span>
            <span role="cell" class="pf-num pf-qty">${p.qty}</span>
            <span role="cell" class="pf-px"><span class="pf-mute">${p.entry} → </span><span class="${p.lastAmber ? 'pf-amber' : ''}">${p.last}</span>${p.priceNote ? html` <span class="pf-note pf-amber">${p.priceNote}</span>` : ''}</span>
            <span role="cell" class="pf-num pf-unr" data-tone="${p.tone}">${p.unrealised}</span>
            <span role="cell" class="pf-num pf-r" data-tone="${p.tone}">${p.rNow}</span>
            <span role="cell" class="pf-rail-cell">
              <span class="pf-stop-px">${p.stop}</span>
              <span class="pf-rail" role="img" aria-label="${p.railText}">
                <span class="pf-rail-track"></span>
                <span class="pf-rail-seg" data-ahead="${p.rail.ahead}" style="left:${p.rail.segL.toFixed(1)}%;width:${p.rail.segW.toFixed(1)}%"></span>
                <span class="pf-rail-stop"></span><span class="pf-rail-target"></span>
                <span class="pf-rail-entry" style="left:${p.rail.entry.toFixed(1)}%"></span>
                <span class="pf-rail-dot" data-ahead="${p.rail.ahead}" style="left:${p.rail.last.toFixed(1)}%"></span>
              </span>
              <span class="pf-target-px">${p.target}</span>
            </span>
            <span role="cell" class="pf-num pf-time"><span class="${p.timeAmber ? 'pf-amber' : ''}">${p.time}</span>${p.timeNote ? html`<span class="pf-note pf-amber">${p.timeNote}</span>` : ''}</span>
          </div>`)}
          ${!rows.length ? html`<div role="row" class="pf-row pf-empty"><span role="cell">${
            !live ? 'Waiting for the dashboard connection.'
              : live.running === false ? 'The paper engine isn’t running, so nothing can be open.'
                : 'Nothing open.'}</span></div>` : ''}
        </div>
        ${f.footer ? html`<p class="pf-meta pf-foot">${f.footer}</p>` : ''}
      </section>`);
  }
  return { update, destroy() { el.textContent = ''; } };
}
