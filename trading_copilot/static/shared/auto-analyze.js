// "AI reviews the top 5": the global auto-analysis switch (the old Live
// Action "Global Auto-Analyze"). On, it starts the reasoning loop for every
// streaming stock; off, it stops them. The choice is kept in this browser
// under the old page's keys and re-applied when the page loads, because the
// server forgets its loops on restart.

import { request } from './api.js';
import { loadPositions, normalizeSymbol } from './positions.js';

const ON_KEY = 'global_auto_analyze';
const FREQ_KEY = 'global_analyze_interval';

export function autoState() {
  let on = true;
  let interval = 90;
  try {
    on = globalThis.localStorage.getItem(ON_KEY) !== 'false';
    interval = parseInt(globalThis.localStorage.getItem(FREQ_KEY), 10) || 90;
  } catch { /* storage blocked: the defaults */ }
  return { on, interval: Math.max(10, interval) };
}

/** Turn the loops on or off for these symbols, and remember the choice. */
export async function applyAuto(on, interval, symbols) {
  try {
    globalThis.localStorage.setItem(ON_KEY, String(on));
    globalThis.localStorage.setItem(FREQ_KEY, String(interval));
  } catch { /* remembered for this page only */ }
  const positions = loadPositions();
  const results = await Promise.all((symbols || []).map((s) => {
    const sym = normalizeSymbol(s);
    return on
      ? request('/api/reasoning/loop/start', { method: 'POST', body: { symbol: sym, interval, user_position: positions[sym] || null } })
      : request('/api/reasoning/loop/stop', { method: 'POST', body: { symbol: sym } });
  }));
  const failed = results.filter((r) => !r.ok).length;
  return { ok: failed === 0, failed };
}

/** At load: once the socket has named the stocks, re-apply a saved "on". */
export function bootAuto(store) {
  const { on, interval } = autoState();
  if (!on) return;
  const off = store.subscribe((s, changed) => {
    if (!changed.includes('live')) return;
    const syms = Object.keys((s.live && s.live.global_state) || {});
    if (!syms.length) return;
    off();
    applyAuto(true, interval, syms);
  });
}
