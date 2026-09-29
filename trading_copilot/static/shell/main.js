// QuantFlow shell: boots the app.
//
// Owns the one /ws socket (store.live, re-broadcast as `qf:ws`), the hash
// router, the app bar, and which tab section is visible. Tab modules are
// imported the first time their tab is shown, and each mounts into its own
// <section id="tab-…">. Overlays (the Inspector, Settings) open over the
// current tab from a hash (#inspect/SAIL, #settings) so Back closes them.
//
// Contract with the tabs: `qf:tab` ({detail: tab id}) fires on every switch;
// window.QF = {store, actions} is the one global, for the Performance tab
// and the old inline code that is still being replaced.

import { createStore } from '../shared/store.js';
import { connectSocket } from './socket.js';
import { DEFAULT_TAB, TABS, hashForInspect, hashForSettings, hashForTab, parseHash, tabFromOld } from './router.js';
import * as appBar from './app-bar.js';
import * as prompt from '../shared/prompt.js';
import { syncAll } from '../shared/positions.js';
import { bootAuto } from '../shared/auto-analyze.js';

const SECTIONS = Object.fromEntries(TABS.map((t) => [t.id, `tab-${t.id}`]));
const LABEL = Object.fromEntries(TABS.map((t) => [t.id, t.label]));

// A tab listed here is rendered by its module; the others are still the old markup.
const TAB_MODULES = {
  market: () => import('../market/index.js'),
  signals: () => import('../signals/index.js'),
  discovery: () => import('../discovery/index.js'),
  review: () => import('../review/index.js'),
};
const OVERLAYS = {
  inspect: () => import('../inspector/index.js'),
};

const first = parseHash(location.hash);
const store = createStore({
  tab: first.kind === 'tab' ? first.tab : DEFAULT_TAB,
  inspect: null, inspectOpts: null, settings: null,
  now: Date.now() / 1000, live: null, liveAt: null, connected: null, alertsUnread: 0,
});
connectSocket(store);

// ------------------------------------------------------------------ routing
let lastTab = null;        // the tab under an overlay, to return to on close

const actions = {
  store,
  go(tab) { location.hash = hashForTab(tab); },
  /** Open a stock in the Inspector; opts {tab: 'now'|'ai'|'news'|'raw', log: true} pick where it opens. */
  inspect(symbol, opts = null) {
    if (!symbol) return;
    const sym = String(symbol).split('|').pop().split('-')[0].toUpperCase();
    store.set({ inspectOpts: opts ? { ...opts, at: Date.now() } : null });
    if (store.get().inspect === sym) return;        // already open: the options above re-aim it
    location.hash = hashForInspect(sym);
  },
  openSettings(section) { location.hash = hashForSettings(section); },
  /** Close whatever overlay is open and return to the tab beneath it. */
  closeOverlay() {
    const s = store.get();
    if (!s.inspect && !s.settings) return;
    location.hash = hashForTab(s.tab);
  },
};

function route() {
  const r = parseHash(location.hash);
  if (r.kind === 'tab') store.set({ tab: r.tab, inspect: null, settings: null });
  else if (r.kind === 'inspect') store.set({ inspect: r.symbol, settings: null });
  else store.set({ settings: r.section || 'watchlist', inspect: null });
}
window.addEventListener('hashchange', route);

// ---------------------------------------------------------- tab sections
const mounted = {};
async function showTab(tab) {
  Object.entries(SECTIONS).forEach(([id, sec]) => {
    const el = document.getElementById(sec);
    if (el) el.classList.toggle('hidden', id !== tab);
  });
  document.title = `${LABEL[tab] || 'Market'} · QuantFlow`;
  if (TAB_MODULES[tab] && !mounted[tab]) {
    mounted[tab] = 'loading';
    try {
      const mod = await TAB_MODULES[tab]();
      mounted[tab] = mod.mount(document.getElementById(SECTIONS[tab]), store, actions);
    } catch (e) {
      mounted[tab] = null;
      console.error(`The ${tab} tab failed to load`, e);
    }
  }
  window.dispatchEvent(new CustomEvent('qf:tab', { detail: tab }));
}

const overlayParts = {};
async function showOverlays(s) {
  for (const [key, load] of Object.entries(OVERLAYS)) {
    if (s[key] && !overlayParts[key]) {
      overlayParts[key] = 'loading';
      const mod = await load();
      const host = document.getElementById(`overlay-${key}`);
      overlayParts[key] = mod.mount(host, store, actions);
      overlayParts[key].update(store.get(), [key]);
    }
  }
}

// ---------------------------------------------------------------- the bar
const bar = appBar.mount(document.getElementById('app-bar'), store, actions,
  { banner: document.getElementById('feed-banner'), settings: 'settings' in OVERLAYS });
const bottom = appBar.mountBottom(document.getElementById('bottom-bar'), store);

store.subscribe((s, changed) => {
  bar.update(s, changed);
  bottom.update(s);
  if (changed.includes('tab') && s.tab !== lastTab) {
    lastTab = s.tab;
    showTab(s.tab);
    window.scrollTo({ top: 0 });
  }
  if (changed.includes('inspect') || changed.includes('settings')) showOverlays(s);
  Object.values(mounted).forEach((m) => { if (m && m.update) m.update(s, changed); });
  Object.values(overlayParts).forEach((m) => { if (m && m.update) m.update(s, changed); });
});

setInterval(() => store.set({ now: Date.now() / 1000 }), 1000);

// ------------------------------------------------------------- globals
window.QF = { store, actions, prompt };
prompt.loadPrompt();        // migrates a stored prompt once, as the old page did at load
syncAll();                  // the server forgets manual positions on restart; resend them
bootAuto(store);            // and its auto-analysis loops: re-apply a saved "on"
// The old page's tab switch, for anything that still calls it.
window.switchTab = (id) => actions.go(tabFromOld(id));

lastTab = store.get().tab;
showTab(lastTab);
if (first.kind !== 'tab') route();

export { TAB_MODULES, OVERLAYS };
