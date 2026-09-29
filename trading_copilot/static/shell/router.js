// Hash routes. Pure: parse and build only, so it runs under node --test.
//
//   #market #signals #discovery #performance #review   a tab
//   #inspect/SAIL                                       the Inspector, over the current tab
//   #settings, #settings/news                           the Settings sheet, over the current tab
//
// The hashes of the old page (#dashboard, #live-action, #screener) still land
// somewhere sensible, so a bookmark never opens a blank page.

export const TABS = [
  { id: 'market', label: 'Market' },
  { id: 'signals', label: 'Signals' },
  { id: 'discovery', label: 'Discovery' },
  { id: 'performance', label: 'Performance' },
  { id: 'review', label: 'Review' },
];

const TAB_IDS = new Set(TABS.map((t) => t.id));
const OLD = { dashboard: 'market', 'live-action': 'signals', screener: 'discovery' };
export const DEFAULT_TAB = 'market';

/** '#inspect/SAIL' -> {kind: 'inspect', symbol: 'SAIL'}; unknown -> the default tab. */
export function parseHash(hash) {
  const h = String(hash || '').replace(/^#\/?/, '');
  const [head, ...rest] = h.split('/');
  const arg = rest.join('/');
  if (head === 'inspect' && arg) {
    let symbol = arg;
    try { symbol = decodeURIComponent(arg); } catch { /* keep it as typed */ }
    return { kind: 'inspect', symbol: symbol.trim().toUpperCase() };
  }
  if (head === 'settings') return { kind: 'settings', section: arg || null };
  if (TAB_IDS.has(head)) return { kind: 'tab', tab: head };
  if (OLD[head]) return { kind: 'tab', tab: OLD[head] };
  return { kind: 'tab', tab: DEFAULT_TAB };
}

export const hashForTab = (tab) => `#${TAB_IDS.has(tab) ? tab : DEFAULT_TAB}`;
export const hashForInspect = (symbol) => `#inspect/${encodeURIComponent(String(symbol || '').toUpperCase())}`;
export const hashForSettings = (section) => (section ? `#settings/${section}` : '#settings');

/** The old page's tab ids, for the window.switchTab shim. */
export const tabFromOld = (id) => (TAB_IDS.has(id) ? id : OLD[id] || DEFAULT_TAB);
