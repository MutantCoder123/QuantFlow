// Settings: one sheet from the gear (#settings/<section>). Watchlist, the AI
// master prompt, the news loop, the end-of-day data sync, and a pointer to the
// paper account's own settings on Performance. Replaces the prompt and
// watchlist modals and the news-engine panel.

import { html, render } from '../shared/dom.js';
import { request } from '../shared/api.js';
import { DEFAULT_PROMPT, isDefault, loadPrompt, savePrompt } from '../shared/prompt.js';
import { addItem, removeItem, parseNews, sectionOf, syncSymbols } from './model.js';
import * as V from './views.js';

const SEARCH_DELAY_MS = 300;

export function mount(host, store, actions) {
  let open = false;
  let section = 'watchlist';
  let opener = null;
  // per-section state, kept while the sheet is closed so a half-done edit survives
  const wl = { saved: null, working: null, query: '', results: [], searching: false, busy: false, message: null, error: null };
  const ai = { text: null, confirmReset: false, message: null };
  const nw = { state: null, models: null, form: { interval: '120', model: '' }, busy: false, message: null };
  const dt = { picked: new Set(), busy: false, message: null, lastRun: null };
  let searchTimer = null;

  const panel = () => host.querySelector('[data-part="panel"]');

  function drawPanel() {
    const el = panel();
    if (!el) return;
    if (section === 'watchlist') render(el, V.watchlist(wl));
    else if (section === 'ai') render(el, V.ai({ text: ai.text ?? loadPrompt(), isDefault: isDefault(ai.text ?? loadPrompt()), confirmReset: ai.confirmReset, message: ai.message }));
    else if (section === 'news') render(el, V.news(nw));
    else if (section === 'data') render(el, V.data({ symbols: syncSymbols(wl.saved), ...dt }));
    else render(el, V.paper());
  }
  function draw() {
    if (!open) { render(host, html``); return; }
    render(host, V.frame(section));
    drawPanel();
  }

  // --------------------------------------------------------------- loading
  async function loadWatchlist() {
    const res = await request('/api/watchlist');
    if (res.ok) {
      wl.saved = res.data.data || [];
      if (wl.working === null) wl.working = [...wl.saved];
      wl.error = null;
    } else wl.error = `Couldn’t load the watchlist: ${res.error}`;
    if (open) drawPanel();
  }
  async function loadNews() {
    const [st, md] = await Promise.all([request('/api/news/state'), nw.models ? null : request('/api/ai/models')]);
    nw.state = st.ok ? st.data : { error: st.error };
    if (st.ok) nw.form = { interval: String(st.data.interval || nw.form.interval), model: st.data.model || nw.form.model };
    if (md && md.ok) {
      nw.models = md.data.models || [];
      if (!nw.form.model) nw.form.model = md.data.default;
    }
    if (open && section === 'news') drawPanel();
  }
  function enter(s) {
    if (s === 'watchlist' || s === 'data') loadWatchlist();
    if (s === 'news') loadNews();
  }

  // -------------------------------------------------------------- actions
  async function search(q) {
    wl.searching = true; drawPanel();
    const res = await request(`/api/search-token?q=${encodeURIComponent(q)}`);
    wl.searching = false;
    wl.results = res.ok ? (res.data.data || []).slice(0, 12) : [];
    if (wl.query === q) drawPanel();
  }

  const act = {
    close: () => actions.closeOverlay(),
    add: (el) => {
      const item = wl.results.find((r) => String(r.token) === el.dataset.token);
      wl.working = addItem(wl.working || [], item);
      wl.query = ''; wl.results = []; wl.message = null;
      drawPanel();
    },
    remove: (el) => { wl.working = removeItem(wl.working, el.dataset.token); wl.message = null; drawPanel(); },
    revert: () => { wl.working = [...(wl.saved || [])]; wl.message = null; drawPanel(); },
    async 'save-watchlist'() {
      wl.busy = true; drawPanel();
      const res = await request('/api/watchlist', { method: 'POST', body: { items: wl.working } });
      wl.busy = false;
      wl.message = res.ok ? { ok: true, text: 'Watchlist saved. New stocks are streaming; their history syncs in the background.' }
        : { ok: false, text: `Couldn’t save: ${res.error}` };
      if (res.ok) { wl.saved = [...wl.working]; }
      drawPanel();
    },
    'save-prompt': () => {
      const ok = savePrompt(ai.text ?? loadPrompt());
      ai.message = ok ? { ok: true, text: 'Prompt saved in this browser.' } : { ok: false, text: 'The prompt can’t be empty.' };
      drawPanel();
    },
    reset: () => { ai.confirmReset = true; ai.message = null; drawPanel(); },
    'reset-cancel': () => { ai.confirmReset = false; drawPanel(); },
    'reset-confirm': () => {
      savePrompt(DEFAULT_PROMPT);
      ai.text = DEFAULT_PROMPT; ai.confirmReset = false;
      ai.message = { ok: true, text: 'Prompt reset to the default.' };
      drawPanel();
    },
    async 'news-auto'() {
      const on = !(nw.state && nw.state.is_active);
      const parsed = parseNews(nw.form);
      if (on && !parsed.ok) { nw.message = { ok: false, text: parsed.error }; drawPanel(); return; }
      nw.busy = true; drawPanel();
      const res = on ? await request('/api/news/loop/start', { method: 'POST', body: { interval: parsed.interval, model: parsed.model } })
        : await request('/api/news/loop/stop', { method: 'POST' });
      nw.busy = false;
      nw.message = res.ok ? { ok: true, text: on ? 'Auto fetch is on.' : 'Auto fetch is off.' } : { ok: false, text: `Couldn’t change it: ${res.error}` };
      loadNews();
    },
    async 'news-apply'() {
      const parsed = parseNews(nw.form);
      if (!parsed.ok) { nw.message = { ok: false, text: parsed.error }; drawPanel(); return; }
      nw.busy = true; drawPanel();
      const res = await request('/api/news/loop/start', { method: 'POST', body: { interval: parsed.interval, model: parsed.model } });
      nw.busy = false;
      nw.message = res.ok ? { ok: true, text: `Fetching every ${parsed.interval} s.` } : { ok: false, text: `Couldn’t apply: ${res.error}` };
      loadNews();
    },
    async 'news-now'() {
      nw.busy = true; drawPanel();
      const res = await request('/api/news/instant', { method: 'POST', body: nw.form.model ? { model: nw.form.model } : {} });
      nw.busy = false;
      nw.message = res.ok ? { ok: true, text: 'Fetched.' } : { ok: false, text: `Couldn’t fetch: ${res.error}` };
      loadNews();
    },
    'pick-all': () => {
      const syms = syncSymbols(wl.saved);
      dt.picked = syms.every((s) => dt.picked.has(s)) ? new Set() : new Set(syms);
      drawPanel();
    },
    async sync() {
      dt.busy = true; dt.message = null; drawPanel();
      const res = await request('/api/admin/sync-parquet', { method: 'POST', body: { symbols: [...dt.picked] } });
      dt.busy = false;
      if (res.ok) dt.lastRun = Date.now() / 1000;
      dt.message = res.ok ? { ok: true, text: `Sync started for ${dt.picked.size} ${dt.picked.size === 1 ? 'stock' : 'stocks'}.` }
        : { ok: false, text: `Couldn’t start the sync: ${res.error}` };
      drawPanel();
    },
    'to-performance': () => actions.go('performance'),
  };

  host.addEventListener('click', (e) => {
    const el = e.target.closest('[data-act]');
    if (!el || el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') return;
    const fn = act[el.dataset.act];
    if (fn) { e.preventDefault(); fn(el); }
  });
  host.addEventListener('input', (e) => {
    const a = e.target.dataset.act;
    if (a === 'search') {
      wl.query = e.target.value.trim();
      clearTimeout(searchTimer);
      if (wl.query.length >= 2) searchTimer = setTimeout(() => search(wl.query), SEARCH_DELAY_MS);
      else { wl.results = []; drawPanel(); }
    }
    if (a === 'prompt') { ai.text = e.target.value; ai.message = null; }
    if (a === 'news-interval') nw.form.interval = e.target.value;
  });
  host.addEventListener('change', (e) => {
    const a = e.target.dataset.act;
    if (a === 'news-model') nw.form.model = e.target.value;
    if (a === 'pick') { if (e.target.checked) dt.picked.add(e.target.value); else dt.picked.delete(e.target.value); drawPanel(); }
  });
  document.addEventListener('keydown', (e) => {
    if (!open) return;
    if (e.key === 'Escape') { e.preventDefault(); actions.closeOverlay(); return; }
    if (e.key !== 'Tab') return;
    const f = [...host.querySelectorAll('button, [href], input, select, textarea')].filter((x) => !x.disabled && x.offsetParent !== null);
    if (!f.length) return;
    if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
  });

  return {
    update(state, changed) {
      if (!changed.includes('settings')) return;
      if (state.settings) {
        const next = sectionOf(state.settings);
        const was = open;
        if (!open) opener = document.activeElement;
        open = true;
        section = next;
        document.body.style.overflow = 'hidden';
        draw();
        enter(section);
        if (!was) { const c = host.querySelector('[data-key="st-close"]'); if (c) c.focus({ preventScroll: true }); }
      } else if (open) {
        open = false;
        document.body.style.overflow = '';
        draw();
        if (opener && opener.isConnected) opener.focus({ preventScroll: true });
      }
    },
    destroy() {},
  };
}
