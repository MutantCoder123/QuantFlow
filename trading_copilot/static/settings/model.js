// Settings' pure parts: watchlist edits and the news form.

export const SECTIONS = [
  { id: 'watchlist', label: 'Watchlist' },
  { id: 'ai', label: 'AI analysis' },
  { id: 'news', label: 'News' },
  { id: 'data', label: 'Data' },
  { id: 'paper', label: 'Paper account' },
];
export const sectionOf = (id) => (SECTIONS.some((s) => s.id === id) ? id : 'watchlist');

export const bare = (s) => String(s || '').split('|').pop().split('-')[0].toUpperCase();

/** Add a search result to the working list, once (by token, then by symbol). */
export function addItem(list, item) {
  if (!item || !item.token) return list;
  if (list.some((x) => String(x.token) === String(item.token) || bare(x.symbol) === bare(item.symbol))) return list;
  return [...list, { token: String(item.token), symbol: item.symbol, exchange: item.exchange || 'NSE' }];
}

export const removeItem = (list, token) => list.filter((x) => String(x.token) !== String(token));

/** What changed against the saved list, for the save button's words. */
export function diff(saved, working) {
  const a = new Set((saved || []).map((x) => String(x.token)));
  const b = new Set((working || []).map((x) => String(x.token)));
  return { added: [...b].filter((t) => !a.has(t)).length, removed: [...a].filter((t) => !b.has(t)).length };
}

/** The news loop form -> {ok, interval, model} or {ok: false, error}. */
export function parseNews(f) {
  const interval = parseInt(String(f.interval || '').trim(), 10);
  if (!Number.isFinite(interval) || interval < 30) return { ok: false, error: 'Fetch at most every 30 seconds.' };
  return { ok: true, interval, model: f.model || null };
}

/** The symbols the end-of-day sync can take: the watchlist's stocks, no index. */
export function syncSymbols(watchlist) {
  const out = [];
  for (const w of watchlist || []) {
    const s = bare(w.symbol);
    if (s && s !== 'NIFTY 50' && !out.includes(s)) out.push(s);
  }
  return out;
}
