// Performance tab: entry point.
//
// Contract with templates/index.html -- the only coupling between the two:
//   - <section id="tab-performance" class="hidden"> is the mount point.
//   - index.html's dashboard socket re-broadcasts every message as a window
//     event `qf:ws` ({detail: payload}); payload.paper is the live block.
//   - switchTab(id) shows `tab-<id>` and emits `qf:tab` ({detail: id}).
//   - Page globals this tab calls: switchTab (for the #performance deep
//     link) and openJsonModal(symbol) (a blotter row opens its analysis).
// Every figure is computed in Python (/api/paper/*). This code formats and
// renders, nothing more. Components: mount(el, store, actions) ->
// {update(state), destroy()}; each owns only its own element.

import { api } from './core/api.js';
import { connectLive } from './core/live.js';
import { createStore } from './core/store.js';
import * as blotterC from './components/blotter.js';
import * as diagnoseC from './components/diagnose/index.js';
import * as legacyC from './components/legacy.js';
import * as figuresC from './components/figures-line.js';
import * as journalC from './components/journal.js';
import * as settingsC from './components/settings-drawer.js';
import * as statementC from './components/statement.js';
import * as statusC from './components/status-line.js';
import * as tapeC from './components/tape.js';

const root = document.getElementById('tab-performance');
const RANGE_KEY = 'qf.performance.range';
const REFRESH_S = 30;

function savedRange() {
  try { return localStorage.getItem(RANGE_KEY) || 'all'; } catch { return 'all'; }
}

const store = createStore({
  range: savedRange(), now: Date.now() / 1000, live: null, liveAt: null,
  metrics: null, metricsError: null, equity: null, equityError: null, trades: null, tradesError: null,
  breakdowns: null, breakdownsError: null, diagnostics: null, diagnosticsError: null,
  actionError: null, drawer: false, openTrade: null, scrollTo: false,
});
connectLive(store);

const visible = () => root && !root.classList.contains('hidden');

// One refresh = the range-scoped reads, applied only if the range hasn't
// changed while they were in flight. A failed read keeps the last good data
// and records why.
async function refresh() {
  const range = store.get().range;
  const [m, e, t, b, d] = await Promise.all([api.metrics(range), api.equity(range), api.trades(range),
    api.breakdowns(range), api.diagnostics(range)]);
  if (store.get().range !== range) return;
  const s = store.get();
  store.set({
    metrics: m.ok ? m.data : s.metrics, metricsError: m.ok ? null : m.error,
    equity: e.ok ? e.data : s.equity, equityError: e.ok ? null : e.error,
    trades: t.ok ? t.data.trades : s.trades, tradesError: t.ok ? null : t.error,
    breakdowns: b.ok ? b.data.breakdowns : s.breakdowns, breakdownsError: b.ok ? null : b.error,
    diagnostics: d.ok ? d.data : s.diagnostics, diagnosticsError: d.ok ? null : d.error,
  });
}

const actions = {
  api,
  refresh,
  setRange(range) {
    try { localStorage.setItem(RANGE_KEY, range); } catch { /* a per-viewer convenience only */ }
    store.set({ range, metrics: null, equity: null, trades: null, breakdowns: null, diagnostics: null,
      metricsError: null, equityError: null, tradesError: null, breakdownsError: null, diagnosticsError: null,
      openTrade: null });
    refresh();
  },
  async pause() {
    const res = await api.pause();
    store.set({ actionError: res.ok ? null : `Couldn’t pause: ${res.error}` });
  },
  async resume() {
    const res = await api.resume();
    store.set({ actionError: res.ok ? null : `Couldn’t resume: ${res.error}` });
  },
  openSettings() { store.set({ drawer: true }); },
  closeSettings() { store.set({ drawer: false }); },
  // from a tape tick (scroll to it) or a journal row (it's already in view)
  openTrade(id, scroll = true) { store.set({ openTrade: id, scrollTo: !!(id && scroll) }); },
};

let mounted = false;
function mountTab() {
  if (mounted || !root) return;
  mounted = true;
  root.textContent = '';
  const parts = [statusC, statementC, tapeC, figuresC, blotterC, diagnoseC, journalC, legacyC, settingsC].map((c) => {
    const el = root.appendChild(document.createElement('div'));
    el.className = 'pf-part';
    return c.mount(el, store, actions);
  });
  const draw = (s) => parts.forEach((p) => p.update(s));
  store.subscribe(draw);
  draw(store.get());
  refresh();

  let lastRealised = null;
  let lastOpen = null;
  store.subscribe((s, changed) => {          // a close changes today's realised P&L or the open set: refetch
    if (!changed.includes('live') || !s.live) return;
    const r = s.live.realized_today;
    const open = (s.live.open || []).map((p) => p.pos_id).join();
    if ((lastRealised !== null && r !== lastRealised) || (lastOpen !== null && open !== lastOpen)) refresh();
    lastRealised = r;
    lastOpen = open;
  });
  setInterval(() => { if (visible()) store.set({ now: Date.now() / 1000 }); }, 1000);
  setInterval(() => { if (visible()) refresh(); }, REFRESH_S * 1000);
}

window.addEventListener('qf:tab', (e) => { if (e.detail === 'performance') mountTab(); });
if (location.hash === '#performance' && typeof window.switchTab === 'function') window.switchTab('performance');
if (visible()) mountTab();
