// Performance tab: entry point.
//
// Contract with templates/index.html -- the only coupling between the two:
//   - <section id="tab-performance" class="hidden"> is the mount point.
//   - index.html's dashboard socket re-broadcasts every message as a window
//     event `qf:ws` ({detail: payload}); payload.paper is the live block.
//   - switchTab(id) shows `tab-<id>` and emits `qf:tab` ({detail: id}).
//   - Page globals this tab may call: switchTab (for the #performance deep
//     link) and, from Phase 5, openJsonModal(symbol) for a position's analysis.
// Every figure is computed in Python (/api/paper/*). This code formats and
// renders, nothing more.

import { api } from './core/api.js';
import { connectLive } from './core/live.js';
import { createStore } from './core/store.js';
import * as statementC from './components/statement.js';
import * as statusC from './components/status-line.js';

const root = document.getElementById('tab-performance');
const RANGE_KEY = 'qf.performance.range';
const REFRESH_S = 30;

function savedRange() {
  try { return localStorage.getItem(RANGE_KEY) || 'all'; } catch { return 'all'; }
}

const store = createStore({ range: savedRange(), now: Date.now() / 1000, live: null, liveAt: null,
  metrics: null, metricsError: null, actionError: null });
connectLive(store);

const visible = () => root && !root.classList.contains('hidden');

async function loadMetrics() {
  const range = store.get().range;
  const res = await api.metrics(range);
  if (store.get().range !== range) return;            // a newer range was picked meanwhile
  store.set(res.ok ? { metrics: res.data, metricsError: null } : { metricsError: res.error });
}

const actions = {
  setRange(range) {
    try { localStorage.setItem(RANGE_KEY, range); } catch { /* per-viewer convenience only */ }
    store.set({ range, metrics: null, metricsError: null });
    loadMetrics();
  },
  async pause() {
    const res = await api.pause();
    store.set({ actionError: res.ok ? null : `Couldn’t pause: ${res.error}` });
  },
  async resume() {
    const res = await api.resume();
    store.set({ actionError: res.ok ? null : `Couldn’t resume: ${res.error}` });
  },
};

let mounted = false;
function mountTab() {
  if (mounted || !root) return;
  mounted = true;
  root.textContent = '';
  const parts = [
    [statusC, 'div'],
    [statementC, 'div'],
  ].map(([c, tag]) => {
    const el = root.appendChild(document.createElement(tag));
    return c.mount(el, store, actions);
  });
  const draw = (s) => parts.forEach((p) => p.update(s));
  store.subscribe(draw);
  draw(store.get());
  loadMetrics();

  let lastRealised = null;
  store.subscribe((s, changed) => {           // a close changes today's realised P&L: refetch
    if (!changed.includes('live') || !s.live) return;
    const r = s.live.realized_today;
    if (lastRealised !== null && r !== lastRealised) loadMetrics();
    lastRealised = r;
  });
  setInterval(() => { if (visible()) store.set({ now: Date.now() / 1000 }); }, 1000);
  setInterval(() => { if (visible()) loadMetrics(); }, REFRESH_S * 1000);
}

window.addEventListener('qf:tab', (e) => { if (e.detail === 'performance') mountTab(); });
if (location.hash === '#performance' && typeof window.switchTab === 'function') window.switchTab('performance');
if (visible()) mountTab();
