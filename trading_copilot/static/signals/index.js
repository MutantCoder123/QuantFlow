// Signals (was "Live Action"): what the engine wants to do now, and what
// happened to each idea today. Where ideas stop (the funnel), today stock by
// stock, the queue with each idea's four stages (math -> risk -> AI -> paper),
// risk by sector, and the decisions log.

import { html, render } from '../shared/dom.js';
import { request } from '../shared/api.js';
import { applyAuto, autoState } from '../shared/auto-analyze.js';
import * as V from './views.js';

const QUEUE_MS = 5000;
const FUNNEL_MS = 20_000;
const SLOW_MS = 15_000;

export function mount(section, store, actions) {
  const root = document.createElement('div');
  root.className = 'qf-page sg-page';
  section.prepend(root);
  render(root, html`
    <div data-part="where"></div>
    <div data-part="timeline"></div>
    <div class="qf-split sg-main">
      <section class="qf-plate" aria-label="The queue">
        <div class="qf-plate-head" data-part="qhead"></div>
        <div class="qf-scroll-x"><table class="qf-table sg-queue">
          <thead><tr><th class="qf-r">#</th><th>Stock</th><th>Idea</th><th>Math · risk · AI · paper</th><th>Stop → target</th>
            <th class="qf-r">R:R</th><th class="qf-r">When</th></tr></thead>
          <tbody data-part="qbody"></tbody></table></div>
      </section>
      <div class="sg-side"><div data-part="risk"></div><div data-part="log"></div></div>
    </div>`);
  const parts = Object.fromEntries([...root.querySelectorAll('[data-part]')].map((el) => [el.dataset.part, el]));

  let queue = null;
  let queueError = null;
  let funnel = null;
  let exposure = null;
  let exposureError = null;
  let alerts = [];
  let calls = 0;
  let filter = 'all';
  let open = null;
  const dismissed = {};
  let auto = autoState();
  let autoBusy = false;
  let autoError = null;

  const visible = () => !section.classList.contains('hidden');
  const prices = () => {
    const out = {};
    for (const [k, s] of Object.entries((store.get().live && store.get().live.global_state) || {})) out[String((s && s.symbol) || k)] = s && s.ltp;
    return out;
  };

  function drawWhere() {
    render(parts.where, V.statement({ funnel, auto: auto.on, interval: auto.interval, calls, busy: autoBusy, error: autoError }));
  }
  function drawQueue() {
    const rows = (queue && queue.rows) || [];
    render(parts.qhead, V.queueHead({ rows, filter }));
    render(parts.qbody, queueError && !queue ? html`<tr><td colspan="7" class="qf-error">Couldn’t load the queue: ${queueError}</td></tr>`
      : V.queueBody({ rows, filter, open, prices: prices(), dismissed, now: store.get().now }));
  }
  function drawTimeline() {
    render(parts.timeline, V.timelineView({ lanes: (funnel && funnel.timeline) || [], width: parts.timeline.clientWidth || 1200,
      now: store.get().now }));
  }
  function draw() {
    if (!visible()) return;
    drawWhere();
    drawTimeline();
    drawQueue();
    render(parts.risk, V.sectorRisk(exposure, exposureError));
    render(parts.log, V.decisions(alerts));
  }

  async function loadQueue() {
    const res = await request('/api/signals/queue');
    if (res.ok) { queue = res.data; queueError = null; calls = res.data.ai_calls || calls; } else queueError = res.error;
    if (visible()) { drawQueue(); drawWhere(); }
  }
  async function loadFunnel() {
    const res = await request('/api/signals/funnel');
    if (res.ok) funnel = res.data;
    if (visible()) { drawWhere(); drawTimeline(); }
  }
  async function loadSlow() {
    const [ex, al] = await Promise.all([request('/api/risk/exposure'), request('/api/alerts/history')]);
    if (ex.ok) { exposure = ex.data.data; exposureError = null; } else exposureError = `Couldn’t load exposure: ${ex.error}`;
    if (al.ok) alerts = al.data.alerts || [];
    if (visible()) { render(parts.risk, V.sectorRisk(exposure, exposureError)); render(parts.log, V.decisions(alerts)); }
  }

  async function setAuto(on) {
    const syms = Object.keys((store.get().live && store.get().live.global_state) || {});
    autoBusy = true; autoError = null; drawWhere();
    const res = await applyAuto(on, auto.interval, syms);
    autoBusy = false;
    auto = { ...auto, on };
    if (!res.ok) autoError = `${res.failed} of ${syms.length} stocks didn’t take the change; try again.`;
    drawWhere();
  }

  root.addEventListener('click', (e) => {
    const openEl = e.target.closest('[data-open]');
    if (openEl) { actions.inspect(openEl.dataset.open); return; }
    const act = e.target.closest('[data-act]');
    if (act && act.tagName !== 'INPUT') {
      const a = act.dataset.act;
      if (a === 'filter') { filter = act.dataset.filter; drawQueue(); }
      if (a === 'auto') setAuto(!auto.on);
      if (a === 'log') actions.inspect(act.dataset.symbol, { log: true });
      if (a === 'dismiss') { dismissed[act.dataset.symbol] = store.get().now; open = null; drawQueue(); }
      return;
    }
    const row = e.target.closest('tr[data-row]');
    if (row) { open = open === row.dataset.row ? null : row.dataset.row; drawQueue(); }
  });
  root.addEventListener('keydown', (e) => {
    const row = e.target.closest('tr[data-row]');
    if (row && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      open = open === row.dataset.row ? null : row.dataset.row;
      drawQueue();
    }
  });
  root.addEventListener('change', (e) => {
    if (e.target.dataset.act !== 'interval') return;
    const v = Math.max(10, parseInt(e.target.value, 10) || 90);
    auto = { ...auto, interval: v };
    if (auto.on) setAuto(true); else drawWhere();
  });
  let resizeTimer = null;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (visible()) drawTimeline(); }, 150); });

  loadQueue(); loadFunnel(); loadSlow();
  const timers = [
    setInterval(() => { if (visible()) loadQueue(); }, QUEUE_MS),
    setInterval(() => { if (visible()) loadFunnel(); }, FUNNEL_MS),
    setInterval(() => { if (visible()) loadSlow(); }, SLOW_MS),
  ];
  return {
    update(state, changed) {
      if (changed.includes('tab') && state.tab === 'signals') { draw(); loadQueue(); loadFunnel(); loadSlow(); }
    },
    destroy() { timers.forEach(clearInterval); },
  };
}
