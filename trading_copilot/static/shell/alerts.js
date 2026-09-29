// The alerts bell and its tray (/api/alerts/*). An alert is an AI verdict the
// engine acted on; opening one marks it read and opens that stock.

import { html, render } from '../shared/dom.js';
import { hm } from '../shared/format.js';
import { icons } from '../shared/icons.js';
import { directiveLabel, verdictLabel } from '../shared/labels.js';
import { request } from '../shared/api.js';

const POLL_MS = 10_000;

/** One tray line's content, from an /api/alerts/history row. Pure. */
export function alertLine(a) {
  return {
    id: a.id,
    symbol: String(a.symbol || '').split('|').pop().split('-')[0],
    time: hm(Number(a.timestamp)),
    words: `${verdictLabel(a.verdict)} · ${directiveLabel(a.action)}`,
    rationale: a.rationale || '',
    unread: !a.read,
  };
}

export function badgeText(n) {
  if (!n) return '';
  return n > 99 ? '99+' : String(n);
}

export function mount(el, store, actions) {
  let open = false;
  let list = null;
  let error = null;

  const refreshCount = async () => {
    const res = await request('/api/alerts/unread');
    if (res.ok) store.set({ alertsUnread: Number(res.data.count) || 0 });
  };
  const loadList = async () => {
    const res = await request('/api/alerts/history');
    list = res.ok ? (res.data.alerts || []).map(alertLine) : list;
    error = res.ok ? null : res.error;
    draw();
  };

  const draw = () => {
    const n = store.get().alertsUnread || 0;
    render(el, html`
      <div class="qf-alerts">
        <button class="qf-iconbtn" type="button" data-key="alerts-bell" aria-haspopup="true" aria-expanded="${open ? 'true' : 'false'}"
          aria-label="${n ? `Alerts, ${n} unread` : 'Alerts'}">${icons.bell}${n ? html`<span class="qf-badge">${badgeText(n)}</span>` : ''}</button>
        ${open ? html`
          <div class="qf-tray" role="dialog" aria-label="Alerts">
            <div class="qf-tray-head"><h2>Alerts</h2><span class="qf-cap">AI verdicts the engine acted on</span></div>
            ${error ? html`<p class="qf-tray-empty">Couldn’t load alerts: ${error}</p>` : ''}
            ${list === null ? html`<p class="qf-tray-empty">Loading…</p>`
              : !list.length ? html`<p class="qf-tray-empty">No alerts yet today.</p>`
                : list.map((a) => html`
                  <button type="button" class="qf-alert" data-alert="${a.id}" data-symbol="${a.symbol}" data-unread="${a.unread ? 'true' : 'false'}">
                    <i aria-hidden="true"></i>
                    <span><b class="qf-sym">${a.symbol}</b> <span class="qf-mute">${a.words}</span></span>
                    <span class="qf-cap">${a.time}</span>
                    ${a.rationale ? html`<p>${a.rationale}</p>` : ''}
                  </button>`)}
          </div>` : ''}
      </div>`);
  };

  const close = () => { if (open) { open = false; draw(); } };
  let inside = null;       // a click handled here re-renders and detaches its target
  el.addEventListener('click', async (e) => {
    inside = e;
    if (e.target.closest('[data-key="alerts-bell"]')) {
      open = !open;
      draw();
      if (open) loadList();
      return;
    }
    const row = e.target.closest('[data-alert]');
    if (row) {
      open = false;
      draw();
      await request(`/api/alerts/mark-read/${encodeURIComponent(row.dataset.alert)}`, { method: 'POST' });
      refreshCount();
      actions.inspect(row.dataset.symbol);
    }
  });
  document.addEventListener('click', (e) => { if (e !== inside && !el.contains(e.target)) close(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });

  refreshCount();
  const timer = setInterval(refreshCount, POLL_MS);
  draw();
  return {
    update(state, changed) { if (changed && changed.includes('alertsUnread')) draw(); },
    destroy() { clearInterval(timer); },
  };
}
