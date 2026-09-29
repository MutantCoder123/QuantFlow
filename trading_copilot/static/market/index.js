// Market (was "Live Dashboard"): where the market is today and what each of
// your stocks is doing. Desktop: the NIFTY hero, breadth, flows, the AI's
// global read, sectors, the order book against price, the board, the wire.
// Phone (<= 640 px): the NIFTY card with a range control, breadth and flows,
// the watchlist tiles, and what is held or signalling. Any stock opens the
// Inspector.

import { html, render } from '../shared/dom.js';
import { request } from '../shared/api.js';
import { boardRow } from './model.js';
import * as V from './views.js';

const SUMMARY_MS = 5000;
const SLOW_MS = 60_000;
const PHONE = '(max-width: 640px)';

export function mount(section, store, actions) {
  const root = document.createElement('div');
  root.className = 'qf-page mk-page';
  section.prepend(root);

  let summary = null;
  let summaryError = null;
  let sparks = {};
  let niftyBars = [];
  const history = {};                    // range -> index-history body
  let range = null;                      // the phone card's range; unset = 1D once the index trades, else 1M
  let filter = 'all';
  let query = '';
  let newsBusy = false;
  let newsError = null;
  let lastBoard = 0;
  let phone = globalThis.matchMedia ? globalThis.matchMedia(PHONE).matches : false;
  let parts = null;

  const visible = () => !section.classList.contains('hidden');

  function frame() {
    if (phone) {
      render(root, html`
        <div data-part="pnifty"></div><div data-part="pbreadth"></div>
        <div data-part="ptiles"></div><div data-part="held"></div><div data-part="wire"></div>`);
    } else {
      render(root, html`
        <div class="qf-split mk-top"><div data-part="hero"></div>
          <aside class="mk-aside"><div data-part="breadth"></div><div data-part="flows"></div><div data-part="read"></div></aside></div>
        <div class="qf-split"><div data-part="heat"></div><div data-part="quad"></div></div>
        <section class="qf-plate mk-board" aria-label="The board">
          <div class="qf-plate-head" data-part="bhead"></div>
          <div class="qf-scroll-x"><table class="qf-table">
            <thead><tr><th>Stock</th><th class="qf-r">Last</th><th class="qf-r">Change</th><th>Today</th><th class="qf-c">Book</th>
              <th class="qf-r">Delta</th><th>RSI</th><th class="qf-r">PCR</th><th class="qf-r">IV rank</th><th class="qf-r">Max pain</th>
              <th>Pattern</th><th>Engine</th></tr></thead>
            <tbody data-part="bbody"></tbody></table></div>
          <div class="qf-plate-foot" data-part="bfoot"></div>
        </section>
        <div data-part="wire"></div>`);
    }
    parts = Object.fromEntries([...root.querySelectorAll('[data-part]')].map((el) => [el.dataset.part, el]));
  }

  const width = (el, fallback) => Math.max(0, (el && el.clientWidth) || fallback);

  function rows(state) {
    const syms = (summary && summary.symbols) || [];
    const bySym = {};
    for (const [k, s] of Object.entries((state.live && state.live.global_state) || {})) bySym[String((s && s.symbol) || k).toUpperCase()] = s;
    const staleAfter = (state.live && state.live.paper && state.live.paper.stale_price_seconds) || 15;
    return syms.map((sym) => boardRow(sym, bySym[sym.toUpperCase()], state.now, staleAfter));
  }

  function filtered(all) {
    const engine = (summary && summary.engine) || {};
    const q = query.trim().toUpperCase();
    return all.filter((r) => {
      const e = engine[r.symbol];
      if (filter === 'held' && !(e && e.rank === 0)) return false;
      if (filter === 'signals' && !(e && e.rank > 0)) return false;
      return !q || r.symbol.includes(q);
    });
  }

  function drawBoard(state) {
    if (!parts || phone) return;
    const all = rows(state);
    const engine = (summary && summary.engine) || {};
    const counts = { all: all.length, held: all.filter((r) => engine[r.symbol] && engine[r.symbol].rank === 0).length,
      signals: all.filter((r) => engine[r.symbol] && engine[r.symbol].rank > 0).length };
    render(parts.bhead, V.boardHead({ filter, counts, query }));
    render(parts.bbody, V.boardBody({ rows: filtered(all), engine, sparks }));
    const ages = all.map((r) => r.age).filter((a) => typeof a === 'number');
    render(parts.bfoot, html`<span>${all.length ? `${all.length} stocks · a row opens the Inspector` : 'Waiting for prices…'}</span>
      <span>${ages.length ? `Prices ${Math.round(Math.min(...ages))} s old` : ''}</span>`);
  }

  function drawPhoneLive(state) {
    if (!parts || !phone) return;
    const all = rows(state);
    const engine = (summary && summary.engine) || {};
    render(parts.ptiles, V.phoneTiles(all));
    const list = all.filter((r) => engine[r.symbol]).map((r) => ({ ...r, engine: engine[r.symbol] }))
      .sort((a, b) => a.engine.rank - b.engine.rank || Math.abs(b.change || 0) - Math.abs(a.change || 0)).slice(0, 8);
    render(parts.held, V.held(list));
  }

  function draw() {
    if (!visible()) return;
    const state = store.get();
    if (!parts) frame();
    const s = summary || {};
    if (summaryError && !summary) {
      render(root, html`<p class="qf-error">Couldn’t load the market: ${summaryError}</p>`);
      parts = null;
      return;
    }
    if (phone) {
      const r = range || (s.nifty && s.nifty.source === 'feed' ? '1D' : '1M');
      render(parts.pnifty, V.phoneNifty({ nifty: s.nifty, bars: niftyBars, history: history[r], range: r, width: width(parts.pnifty, 358) - 34 }));
      render(parts.pbreadth, V.phoneBreadth(s.breadth, s.flows));
      drawPhoneLive(state);
    } else {
      render(parts.hero, V.hero({ nifty: s.nifty, bars: niftyBars, daily: history['1M'], width: width(parts.hero, 860) - 70 }));
      render(parts.breadth, V.breadthView(s.breadth));
      render(parts.flows, V.flowsView(s.flows, width(parts.flows, 420)));
      render(parts.read, V.globalRead(s.context, state.now));
      render(parts.heat, V.heatmap(s.sectors));
      render(parts.quad, V.quadrantView(s.quadrant, width(parts.quad, 420)));
      drawBoard(state);
    }
    render(parts.wire, V.wire({ items: s.wire, news: s.news, now: state.now, busy: newsBusy, error: newsError }));
  }

  // ------------------------------------------------------------- loading
  async function loadSummary() {
    const res = await request('/api/market/summary');
    if (res.ok) { summary = res.data; summaryError = null; } else summaryError = res.error;
    draw();
  }
  async function loadSlow() {
    const [sp, nb] = await Promise.all([request('/api/market/sparks'), request('/api/stock/NIFTY/bars')]);
    if (sp.ok) sparks = sp.data.sparks || {};
    if (nb.ok) niftyBars = nb.data.bars || [];
    if (!history['1M']) await loadHistory('1M');
    draw();
  }
  async function loadHistory(r) {
    if (r === '1D' || history[r]) return;
    const res = await request(`/api/market/index-history?range=${r}`);
    if (res.ok) history[r] = res.data;
  }

  // -------------------------------------------------------------- events
  root.addEventListener('click', async (e) => {
    const open = e.target.closest('[data-open]');
    if (open) { actions.inspect(open.dataset.open); return; }
    const el = e.target.closest('[data-act]');
    if (!el) return;
    if (el.dataset.act === 'filter') { filter = el.dataset.filter; drawBoard(store.get()); }
    if (el.dataset.act === 'range') { range = el.dataset.range; await loadHistory(range); draw(); }
    if (el.dataset.act === 'fetch-news') {
      newsBusy = true; newsError = null; draw();
      const model = summary && summary.news && summary.news.model;
      const res = await request('/api/news/instant', { method: 'POST', body: model ? { model } : {} });
      newsBusy = false;
      if (!res.ok) newsError = `Couldn’t fetch: ${res.error}`;
      await loadSummary();
    }
  });
  root.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const row = e.target.closest('tr[data-open]');
    if (row) { e.preventDefault(); actions.inspect(row.dataset.open); }
  });
  root.addEventListener('input', (e) => {
    if (e.target.dataset.act === 'query') { query = e.target.value; drawBoard(store.get()); }
  });
  if (globalThis.matchMedia) {
    globalThis.matchMedia(PHONE).addEventListener('change', (m) => { phone = m.matches; parts = null; draw(); });
  }
  let resizeTimer = null;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(draw, 150); });

  loadSummary();
  loadSlow();
  const timers = [setInterval(() => { if (visible()) loadSummary(); }, SUMMARY_MS),
    setInterval(() => { if (visible()) loadSlow(); }, SLOW_MS)];

  return {
    update(state, changed) {
      if (changed.includes('tab') && state.tab === 'market') { draw(); return; }
      if (!visible()) return;
      // the socket ticks twice a second; the board and tiles redraw at most once a second
      if (changed.includes('live') && state.now - lastBoard >= 1) {
        lastBoard = state.now;
        if (phone) drawPhoneLive(state); else drawBoard(state);
      }
      if (changed.includes('now') && parts && parts.read && !phone) render(parts.read, V.globalRead(summary && summary.context, state.now));
    },
    destroy() { timers.forEach(clearInterval); },
  };
}
