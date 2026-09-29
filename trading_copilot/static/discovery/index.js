// Discovery: what to watch today. The scan runs in the feed process; this tab
// starts it, follows it (a scan still running after a reload is picked up),
// and shows the universe, the picks and the AI's read of the top 10.

import { html, render } from '../shared/dom.js';
import { request } from '../shared/api.js';
import * as V from './views.js';

const RUNNING_MS = 2000;
const IDLE_MS = 30_000;

export function mount(section, store, actions) {
  const root = document.createElement('div');
  root.className = 'qf-page dc-page';
  section.prepend(root);
  render(root, html`<div data-part="head"></div>
    <div class="qf-split dc-split"><div data-part="chart"></div><div data-part="read"></div></div>
    <div data-part="sheet"></div><div data-part="foot"></div>`);
  const parts = Object.fromEntries([...root.querySelectorAll('[data-part]')].map((el) => [el.dataset.part, el]));

  let v = null;
  let feedDown = false;
  let models = null;
  let model = null;
  let selected = null;
  let runError = null;
  let starting = false;
  let adding = null;
  let addMsg = null;
  let timer = null;

  const visible = () => !section.classList.contains('hidden');
  const pick = () => (v && v.picks || []).find((p) => p.symbol === selected) || null;

  function draw() {
    if (!visible()) return;
    render(parts.head, V.head({ v, models, model, feedDown, runError, starting }));
    render(parts.chart, V.universe({ v, selected, width: parts.chart.clientWidth || 860 }));
    render(parts.read, V.read(pick()));
    render(parts.sheet, V.sheet({ picks: v && v.picks, selected, adding }));
    render(parts.foot, V.footer({ notes: v && v.notes, addMsg }));
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(load, v && v.state === 'running' ? RUNNING_MS : IDLE_MS);
  }
  async function load() {
    const res = await request('/api/discovery/view');
    if (res.ok) {
      v = res.data; feedDown = false;
      if (!selected && v.picks && v.picks.length) selected = v.picks[0].symbol;
    } else {
      feedDown = !!(res.data && res.data.feed_down) || /reachable/.test(res.error || '');
      if (!feedDown) runError = `Couldn’t read the scan: ${res.error}`;
    }
    draw();
    schedule();
  }
  async function loadModels() {
    const res = await request('/api/ai/models');
    if (res.ok) {
      models = res.data.models || [];
      model = model || res.data.default || (models[0] && models[0].id);
      draw();
    }
  }

  root.addEventListener('click', async (e) => {
    const open = e.target.closest('[data-open]');
    if (open) { actions.inspect(open.dataset.open); return; }
    const act = e.target.closest('[data-act]');
    if (act && act.dataset.act === 'run') {
      starting = true; runError = null; draw();
      const res = await request('/api/discovery/run', { method: 'POST', body: { model } });
      starting = false;
      if (!res.ok) runError = `Couldn’t start the scan: ${res.error}`;
      load();
      return;
    }
    if (act && act.dataset.act === 'add') {
      const p = (v.picks || []).find((x) => x.symbol === act.dataset.symbol);
      if (!p) return;
      adding = p.symbol; addMsg = null; draw();
      const res = await request('/api/watchlist/add', { method: 'POST', body: { token: p.token, symbol: p.symbol, exchange: p.exchange } });
      adding = null;
      addMsg = res.ok ? { ok: true, text: `Added ${p.symbol} to the watchlist; it streams live now.` }
        : { ok: false, text: `Couldn’t add ${p.symbol}: ${res.error}` };
      load();
      return;
    }
    const pk = e.target.closest('[data-pick]');
    if (pk) { selected = pk.dataset.pick; draw(); }
  });
  root.addEventListener('keydown', (e) => {
    const row = e.target.closest('tr[data-pick]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); selected = row.dataset.pick; draw(); }
  });
  root.addEventListener('change', (e) => { if (e.target.dataset.act === 'model') model = e.target.value; });
  let resizeTimer = null;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(draw, 150); });

  load();
  loadModels();
  return {
    update(state, changed) { if (changed.includes('tab') && state.tab === 'discovery') { draw(); load(); } },
    destroy() { clearTimeout(timer); },
  };
}
