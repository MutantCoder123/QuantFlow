// The mock-platform trades from before the current engine: one collapsed
// line, opening to a read-only table under a plain disclaimer. They are
// never part of any figure on this page (the API keeps them separate).

import { html, render } from '../../shared/dom.js';
import { dayMonth, group, hm, inr } from '../../shared/format.js';

/** Pure: a legacy record -> a row. Unknown fields show as a dash. */
export function legacyRow(t) {
  const e = t.entry || {};
  const x = (t.exits || [])[0] || {};
  // a trade the mock platform never closed has no result -- not a ₹0 result
  const closed = t.status === 'CLOSED' && (t.exits || []).length > 0;
  return {
    id: t.trade_id,
    ts: typeof e.timestamp === 'number' ? e.timestamp : 0,
    when: typeof e.timestamp === 'number' ? `${dayMonth(e.timestamp)}, ${hm(e.timestamp)}` : '—',
    symbol: t.symbol || '—',
    side: t.direction || '—',
    qty: typeof e.executed_quantity === 'number' ? group(e.executed_quantity) : '—',
    px: `${typeof e.executed_price === 'number' ? group(e.executed_price, 2) : '—'} → ${
      closed && typeof x.executed_price === 'number' ? group(x.executed_price, 2) : 'not closed'}`,
    pnl: closed && typeof t.realized_pnl === 'number' ? inr(t.realized_pnl) : '—',
    tone: !closed ? 'plain' : t.realized_pnl > 0 ? 'profit' : t.realized_pnl < 0 ? 'loss' : 'plain',
    confidence: typeof e.llm_confidence === 'number' ? `${e.llm_confidence}/10` : '—',
    status: t.status || '—',
    reason: e.llm_reason || '',
  };
}

export function mount(el, store, actions) {
  let open = false;
  let data = null;
  let error = null;

  async function load() {
    const res = await actions.api.legacy();
    data = res.ok ? res.data : null;
    error = res.ok ? null : res.error;
    draw();
  }

  el.addEventListener('click', (e) => {
    if (!e.target.closest('[data-act="legacy"]')) return;
    open = !open;
    draw();
  });

  function draw() {
    const n = data ? data.trades.length : null;
    const rows = data ? data.trades.map(legacyRow).sort((a, b) => b.ts - a.ts) : [];
    render(el, html`
      <section class="pf-legacy" aria-label="Older trades">
        <button type="button" class="pf-legacy-toggle" data-act="legacy" data-key="legacy" aria-expanded="${open}">
          <span class="pf-indigo" aria-hidden="true">${open ? '−' : '+'}</span>
          <span>Older trades from the mock platform${n === null ? '' : ` (${n})`} — not the current engine, not counted in any figure</span>
        </button>
        ${open ? html`<div class="pf-legacy-body">
          <p class="pf-meta">${data ? data.note : error ? `Couldn’t load them: ${error}` : 'Loading…'}</p>
          ${rows.length ? html`<div role="table" aria-label="Older mock-platform trades" class="pf-table pf-legacy-table">
            <div role="row" class="pf-row pf-head"><span role="columnheader">Opened</span><span role="columnheader">Symbol</span>
              <span role="columnheader">Side</span><span role="columnheader" class="pf-num">Qty</span>
              <span role="columnheader">Entry → exit</span><span role="columnheader" class="pf-num">P&amp;L</span>
              <span role="columnheader" class="pf-num">AI confidence</span><span role="columnheader">Why it entered</span></div>
            ${rows.map((t) => html`<div role="row" class="pf-row">
              <span role="cell" class="pf-mute pf-when">${t.when}</span><span role="cell" class="pf-sym">${t.symbol}</span>
              <span role="cell" class="pf-mute">${t.side}</span><span role="cell" class="pf-num">${t.qty}</span>
              <span role="cell" class="pf-mute">${t.px}</span><span role="cell" class="pf-num" data-tone="${t.tone}">${t.pnl}</span>
              <span role="cell" class="pf-num">${t.confidence}</span><span role="cell" class="pf-mute pf-reason" title="${t.reason}">${t.reason}</span>
            </div>`)}
          </div>` : ''}
        </div>` : ''}
      </section>`);
  }

  draw();
  load();
  return { update() {}, destroy() { el.textContent = ''; } };
}
