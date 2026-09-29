// Review: is the signal engine any good? Replaces the Score reliability and
// Session review panels. The last 20 sessions by default; Today, or any one
// session date. Paper P&L lives on Performance.

import { html, render } from '../shared/dom.js';
import { request } from '../shared/api.js';
import * as V from './views.js';

const REFRESH_MS = 60_000;
const istToday = () => new Date(Date.now() + 19800_000).toISOString().slice(0, 10);

export function mount(section, store) {
  const root = document.createElement('div');
  root.className = 'qf-page rv-page';
  section.prepend(root);
  render(root, html`<div data-part="head"></div>
    <div class="rv-grid"><div data-part="rel"></div><div data-part="trend"></div></div>
    <div class="rv-grid"><div data-part="hist"></div><div data-part="groups"></div></div>
    <div data-part="error"></div>`);
  const parts = Object.fromEntries([...root.querySelectorAll('[data-part]')].map((el) => [el.dataset.part, el]));

  let r = null;
  let error = null;
  let range = 'sessions';
  let date = null;
  let session = '';
  const visible = () => !section.classList.contains('hidden');

  function draw() {
    if (!visible()) return;
    render(parts.head, V.head({ r, range, date, today: istToday(), session }));
    render(parts.rel, V.reliabilityView(r, parts.rel.clientWidth || 560));
    render(parts.trend, V.trendView(r, parts.trend.clientWidth || 716));
    render(parts.hist, V.histView(r, parts.hist.clientWidth || 560));
    render(parts.groups, V.groupsView(r));
    render(parts.error, error ? html`<p class="qf-error">${error}</p>` : html``);
  }

  async function load() {
    const q = range === 'sessions' ? 'sessions=20' : `date=${range === 'today' ? istToday() : date}`;
    if (range === 'date' && !date) { draw(); return; }
    const res = await request(`/api/review?${q}`);
    if (res.ok) { r = res.data; error = null; } else error = `Couldn’t load the review: ${res.error}`;
    session = '';
    if (range !== 'sessions') {
      // the session's policy version and feed staleness (the old Session review line)
      const d = range === 'today' ? istToday() : date;
      const s = await request(`/api/session/review?date=${d}`);
      if (s.ok) {
        const st = (s.data.data || {}).staleness || {};
        session = `policy v${s.data.data.config_version}${st.incidents !== null && st.incidents !== undefined
          ? ` · ${st.incidents} stale-price rejection${st.incidents === 1 ? '' : 's'}` : ''}`;
      }
    }
    draw();
  }

  root.addEventListener('click', (e) => {
    const el = e.target.closest('[data-act="range"]');
    if (!el) return;
    range = el.dataset.range;
    if (range === 'date' && !date) date = istToday();
    load();
  });
  root.addEventListener('change', (e) => {
    if (e.target.dataset.act === 'date' && e.target.value) { date = e.target.value; load(); }
  });
  let resizeTimer = null;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(draw, 150); });

  load();
  const timer = setInterval(() => { if (visible()) load(); }, REFRESH_MS);
  return {
    update(state, changed) { if (changed.includes('tab') && state.tab === 'review') { draw(); load(); } },
    destroy() { clearInterval(timer); },
  };
}
