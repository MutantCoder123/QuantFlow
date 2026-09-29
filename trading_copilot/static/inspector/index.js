// The Inspector: one stock, over any tab (#inspect/SYM). Replaces the old AI
// modal (reasoning, catalyst, provenance, telemetry), the position modal and
// the ledger modal. Tabs: Now (chart, score, states, position), AI, News, Raw.
//
// Keyboard: Esc or the close button returns to the tab beneath (Back works
// too); focus stays inside while open and returns to what opened it.

import { html, render } from '../shared/dom.js';
import { request } from '../shared/api.js';
import { loadPrompt } from '../shared/prompt.js';
import { parsePositionForm, positionFor, savePosition } from '../shared/positions.js';
import { copyText, newsOf, paperPositionFor, paperWords, parseIntent, parseReport, payloadOf, planOf, quote,
  stateStamps, stockOf, waterfall } from './model.js';
import * as V from './views.js';

const REPORT_POLL_MS = 5000;
const BARS_POLL_MS = 60_000;

function fresh(symbol, opts) {
  opts = opts || {};
  return {
    symbol, tab: opts.tab || 'now', bars: null, levels: {}, barsError: null, report: undefined, reportError: null,
    auto: false, interval: 90, busy: null, aiError: null, news: null, newsAt: null, newsError: null,
    editing: false, posError: null, logging: !!opts.log, log: { kind: 'open', direction: 'Long', qty: '', price: '', stop: '', target: '', charges: '0', reason: '' },
    logError: null, intentOpen: false, intent: { action: 'None', type: 'Intraday', quantity: '', price: '', advice: '' },
    copied: null, prefilled: false,
  };
}

async function copy(text) {
  try {
    if (navigator.clipboard && globalThis.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* fall through to the old way */ }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { ok = false; }
  ta.remove();
  return ok;
}

export function mount(host, store, actions) {
  let st = null;              // this stock's view state, null when closed
  let opener = null;
  let models = null;
  let model = null;
  let timers = [];
  let lastLiveDraw = 0;

  const set = (patch) => { Object.assign(st, patch); draw(); };
  const stock = () => stockOf(store.get().live, st.symbol);
  const typing = () => {
    const a = document.activeElement;
    return a && host.contains(a) && (a.tagName === 'INPUT' || a.tagName === 'SELECT' || a.tagName === 'TEXTAREA');
  };

  function positionForChart(s) {
    const paper = paperPositionFor(store.get().live, st.symbol);
    if (paper) return { side: paper.side === 'SHORT' ? 'Short' : 'Long', entry: paper.entry_price, stop: paper.stop, target: paper.target, opened: paper.ts };
    const m = positionFor(st.symbol);
    if (m) return { side: m.direction, entry: m.entry_price, stop: m.stoploss, target: m.target, opened: m.entry_timestamp };
    return null;
  }

  function panel(s) {
    const sp = s && s.structured_payload;
    if (st.tab === 'ai') {
      return V.ai({ plan: planOf(st.report), models, model, auto: st.auto, interval: st.interval, busy: st.busy === 'analyse',
        error: st.aiError, intentOpen: st.intentOpen, intent: st.intent, copied: st.copied });
    }
    if (st.tab === 'news') {
      return V.news({ items: newsOf(s, st.news), busy: st.busy === 'news', error: st.newsError, fetchedAt: st.newsAt });
    }
    if (st.tab === 'raw') {
      const p = payloadOf(s, positionFor(st.symbol));
      return V.rawView({ json: p ? JSON.stringify(p, null, 2) : '', copied: st.copied });
    }
    const paper = paperPositionFor(store.get().live, st.symbol);
    // the drawer's inner width less the price axis (left) and the level labels (right; on a phone they sit on the line)
    const phone = window.innerWidth <= 640;
    const width = Math.min(740, window.innerWidth) - (phone ? 32 + 40 : 48 + 44 + 124);
    return html`
      ${st.bars === null ? html`<p class="in-empty">Loading today’s bars…</p>`
        : st.barsError ? html`<p class="qf-error">Couldn’t load the chart: ${st.barsError}</p>`
          : V.chart({ bars: st.bars, levels: st.levels, position: positionForChart(s), width })}
      ${V.waterfallView(waterfall(sp && sp.math_setup))}
      ${V.stamps(stateStamps(sp))}
      ${paper ? V.paperPosition(paper, store.get().now)
        : V.manualPosition({ manual: positionFor(st.symbol), editing: st.editing, error: st.posError, logging: st.logging,
          log: st.log, logError: st.logError, busy: st.busy === 'pos' })}`;
  }

  // The frame (scrim + drawer) renders once per opening, so its slide-in plays
  // once; live updates redraw only the head and the panel inside it.
  let frame = null;
  function draw() {
    if (!st) { frame = null; render(host, html``); return; }
    if (!frame) {
      render(host, html`
        <div class="qf-scrim" data-act="close"></div>
        <aside class="qf-drawer in-drawer" role="dialog" aria-modal="true" aria-labelledby="in-title">
          <div class="qf-drawer-head"></div>
          <div class="qf-drawer-body" id="in-panel" role="tabpanel"></div>
        </aside>`);
      frame = { head: host.querySelector('.qf-drawer-head'), body: host.querySelector('.qf-drawer-body') };
    }
    const s = stock();
    const paper = paperPositionFor(store.get().live, st.symbol);
    const plan = planOf(st.report);
    const stamp = paper ? { text: paperWords(paper), tone: paper.r_now > 0 ? 'ok' : paper.r_now < 0 ? 'bad' : 'off' }
      : plan && plan.stamp ? plan.stamp : null;
    render(frame.head, V.head({ symbol: st.symbol, quote: quote(s), stamp, tab: st.tab }));
    render(frame.body, html`
      ${!s ? html`<p class="qf-amber">${st.symbol} isn’t streaming on the live watchlist, so only saved analysis shows here.</p>` : ''}
      ${panel(s)}`);
  }

  // --------------------------------------------------------------- loading
  async function loadBars() {
    if (!st) return;
    const sym = st.symbol;
    const res = await request(`/api/stock/${encodeURIComponent(sym)}/bars`);
    if (!st || st.symbol !== sym) return;
    if (res.ok) set({ bars: res.data.bars || [], levels: res.data.levels || {}, barsError: null });
    else set({ bars: st.bars || [], barsError: res.error });
  }
  async function loadReport() {
    if (!st) return;
    const sym = st.symbol;
    const res = await request(`/api/reasoning/report/${encodeURIComponent(sym)}`);
    if (!st || st.symbol !== sym) return;
    if (res.ok) {
      const report = parseReport(res.data.report);
      st.report = report;
      st.auto = !!res.data.is_active;
      if (!st.prefilled && report && !report.text) prefillLog(report);
      if (!typing()) draw();
    }
  }
  async function loadModels() {
    if (models) return;
    const res = await request('/api/ai/models');
    if (res.ok) {
      models = res.data.models || [];
      model = model || res.data.default || (models[0] && models[0].id);
      if (st && st.tab === 'ai' && !typing()) draw();
    }
  }
  function prefillLog(report) {
    const a = String(report.Action || '').toUpperCase();
    const n = (v) => (typeof v === 'number' && v > 0 ? String(v) : '');
    st.prefilled = true;
    st.log = { ...st.log, direction: a === 'SHORT' ? 'Short' : 'Long', kind: a === 'CLOSE' ? 'close' : a === 'HOLD' ? 'hold' : 'open',
      price: n(report.Entry_Target_Price), stop: n(report.Stoploss), target: n(report.Exit_Target_Price),
      reason: report.Reason && !report.math_rejection ? report.Reason : '' };
  }

  function start() {
    stop();
    loadBars();
    loadReport();
    loadModels();
    timers.push(setInterval(() => { if (st && st.tab === 'now') loadBars(); }, BARS_POLL_MS));
    timers.push(setInterval(() => { if (st && (st.auto || st.busy === 'analyse' || st.tab === 'ai')) loadReport(); }, REPORT_POLL_MS));
  }
  function stop() { timers.forEach(clearInterval); timers = []; }

  function open(symbol, opts) {
    const sameStock = st && st.symbol === symbol;
    if (!sameStock) {
      opener = opener || document.activeElement;
      st = fresh(symbol, opts);
      frame = null;
      draw();
      start();
    } else if (opts) {
      set({ tab: opts.tab || st.tab, logging: opts.log || st.logging });
    }
    const close = host.querySelector('[data-key="in-close"]');
    if (close && !host.contains(document.activeElement)) close.focus({ preventScroll: true });
    document.body.style.overflow = 'hidden';
  }
  function closeNow() {
    stop();
    st = null;
    draw();
    document.body.style.overflow = '';
    if (opener && opener.isConnected) opener.focus({ preventScroll: true });
    opener = null;
  }

  // --------------------------------------------------------------- actions
  const act = {
    close: () => actions.closeOverlay(),
    tab: (el) => { set({ tab: el.dataset.tab, copied: null }); if (el.dataset.tab === 'now') loadBars(); },
    async analyse() {
      set({ busy: 'analyse', aiError: null });
      const res = await request(`/api/reasoning/instant/${encodeURIComponent(st.symbol)}`, { method: 'POST',
        body: { model, prompt: loadPrompt(), user_position: positionFor(st.symbol), user_intent: parseIntent(st.intent) } });
      if (!st) return;
      if (res.ok) set({ busy: null, report: parseReport(res.data.report) });
      else set({ busy: null, aiError: `Couldn’t analyse: ${res.error}` });
    },
    async auto() {
      const on = !st.auto;
      const interval = Math.max(10, parseInt(st.interval, 10) || 90);
      const res = on
        ? await request('/api/reasoning/loop/start', { method: 'POST', body: { symbol: st.symbol, interval, model,
          prompt: loadPrompt(), user_position: positionFor(st.symbol) } })
        : await request('/api/reasoning/loop/stop', { method: 'POST', body: { symbol: st.symbol } });
      if (!st) return;
      set(res.ok ? { auto: on, aiError: null } : { aiError: `Couldn’t ${on ? 'start' : 'stop'} auto-analysis: ${res.error}` });
    },
    async 'fetch-news'() {
      set({ busy: 'news', newsError: null });
      const res = await request(`/api/news/fetch/${encodeURIComponent(st.symbol)}`, { method: 'POST', body: { model } });
      if (!st) return;
      if (res.ok && res.data.data) set({ busy: null, news: res.data.data, newsAt: Date.now() / 1000 });
      else set({ busy: null, newsError: `Couldn’t fetch news: ${res.ok ? 'the news service sent nothing' : res.error}` });
    },
    async 'copy-json'() {
      const p = payloadOf(stock(), positionFor(st.symbol));
      if (p && await copy(JSON.stringify(p, null, 2))) flashCopied('json');
    },
    async 'copy-ai'() {
      const p = payloadOf(stock(), positionFor(st.symbol));
      if (!p) return;
      const text = copyText({ prompt: loadPrompt(), payload: p, manual: positionFor(st.symbol), intent: parseIntent(st.intent) });
      if (await copy(text)) flashCopied('ai');
    },
    'pos-edit': () => set({ editing: true, posError: null, logging: false }),
    'pos-cancel': () => set({ editing: false, posError: null }),
    async 'pos-clear'() {
      set({ busy: 'pos' });
      await savePosition(st.symbol, null);
      hotReload(null);
      set({ busy: null, editing: false });
    },
    'log-toggle': () => set({ logging: !st.logging, editing: false, logError: null }),
  };

  function flashCopied(which) {
    set({ copied: which });
    setTimeout(() => { if (st && st.copied === which) set({ copied: null }); }, 2000);
  }

  // The Signals auto-analysis loop carries the position; restart it so it sees the change (as the old modal did).
  function hotReload(position) {
    let on = true;
    let freq = 90;
    try {
      on = localStorage.getItem('global_auto_analyze') !== 'false';
      freq = parseInt(localStorage.getItem('global_analyze_interval'), 10) || 90;
    } catch { /* storage blocked: keep the defaults */ }
    if (on) request('/api/reasoning/loop/start', { method: 'POST', body: { symbol: st.symbol, interval: freq, user_position: position } });
  }

  const formData = (form) => Object.fromEntries(new FormData(form).entries());

  async function submitPosition(form) {
    const parsed = parsePositionForm(formData(form), { existing: positionFor(st.symbol) });
    if (!parsed.ok) { set({ posError: parsed.error }); return; }
    set({ busy: 'pos', posError: null });
    const res = await savePosition(st.symbol, parsed.position);
    hotReload(parsed.position);
    if (!st) return;
    set(res.ok ? { busy: null, editing: false } : { busy: null, posError: `Saved here, but the server didn’t take it: ${res.error}` });
  }

  async function submitLog(form) {
    const f = formData(form);
    const kind = f.kind || st.log.kind;
    const n = (v) => parseFloat(String(v || '').replace(/,/g, ''));
    const report = st.report && !st.report.text ? st.report : {};
    const reason = f.reason || report.Reason || 'Logged from the Inspector.';
    let res;
    set({ busy: 'pos', logError: null });
    if (kind === 'open') {
      const price = n(f.price);
      const qty = parseInt(f.qty, 10);
      if (!(price > 0) || !(qty > 0)) { set({ busy: null, logError: 'Enter the price and a quantity above zero.' }); return; }
      const stopPx = n(f.stop);
      const targetPx = n(f.target);
      res = await request('/api/ledger/open', { method: 'POST', body: {
        symbol: st.symbol, direction: f.direction === 'Short' ? 'Short' : 'Long', entry_price: price, entry_qty: qty,
        confidence: report.Confidence_Score || 5, reason,
        target: targetPx > 0 ? String(targetPx) : null, stoploss: stopPx > 0 ? String(stopPx) : null } });
      if (res.ok) {
        const pos = { mode: 'Intraday', direction: f.direction === 'Short' ? 'Short' : 'Long', quantity: qty, entry_price: price,
          target: targetPx > 0 ? targetPx : null, stoploss: stopPx > 0 ? stopPx : null, entry_timestamp: Math.floor(Date.now() / 1000) };
        await savePosition(st.symbol, pos);
        hotReload(pos);
      }
    } else if (kind === 'close') {
      const price = n(f.price);
      const qty = parseInt(f.qty, 10);
      if (!(price > 0) || !(qty > 0)) { set({ busy: null, logError: 'Enter the exit price and a quantity above zero.' }); return; }
      res = await request('/api/ledger/close', { method: 'POST', body: {
        symbol: st.symbol, exit_price: price, exit_qty: qty, reason, charges: n(f.charges) || 0 } });
      if (res.ok) { await savePosition(st.symbol, null); hotReload(null); }
    } else {
      const stopPx = n(f.stop);
      const targetPx = n(f.target);
      res = await request('/api/ledger/manage', { method: 'POST', body: {
        symbol: st.symbol, action: 'Hold', reason,
        target: targetPx > 0 ? String(targetPx) : null, stoploss: stopPx > 0 ? String(stopPx) : null } });
    }
    if (!st) return;
    set(res.ok ? { busy: null, logging: false } : { busy: null, logError: `Couldn’t log it: ${res.error}` });
  }

  // ---------------------------------------------------------------- events
  host.addEventListener('click', (e) => {
    const el = e.target.closest('[data-act]');
    if (!el || !st || el.tagName === 'SELECT' || el.tagName === 'INPUT' || el.tagName === 'DETAILS') return;
    const fn = act[el.dataset.act];
    if (fn) { e.preventDefault(); fn(el); }
  });
  host.addEventListener('change', (e) => {
    if (!st) return;
    if (e.target.dataset.act === 'model') { model = e.target.value; return; }
    if (e.target.dataset.act === 'interval') { st.interval = e.target.value; return; }
    const form = e.target.form;
    if (form && form.dataset.form === 'log' && e.target.name === 'kind') set({ log: { ...st.log, ...formData(form) } });
  });
  host.addEventListener('input', (e) => {
    if (!st) return;
    const form = e.target.form;
    if (!form) return;
    if (form.dataset.form === 'log') st.log = { ...st.log, ...formData(form) };
    if (form.dataset.form === 'intent') st.intent = { ...st.intent, ...formData(form) };
  });
  host.addEventListener('toggle', (e) => { if (st && e.target.tagName === 'DETAILS') st.intentOpen = e.target.open; }, true);
  host.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!st) return;
    if (e.target.dataset.form === 'pos') submitPosition(e.target);
    if (e.target.dataset.form === 'log') submitLog(e.target);
  });
  document.addEventListener('keydown', (e) => {
    if (!st) return;
    if (e.key === 'Escape') { e.preventDefault(); actions.closeOverlay(); return; }
    if (e.key !== 'Tab') return;
    const f = [...host.querySelectorAll('button, [href], input, select, textarea, summary, [tabindex]:not([tabindex="-1"])')]
      .filter((x) => !x.disabled && x.offsetParent !== null);
    if (!f.length) return;
    const firstEl = f[0];
    const lastEl = f[f.length - 1];
    if (e.shiftKey && document.activeElement === firstEl) { e.preventDefault(); lastEl.focus(); }
    else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); firstEl.focus(); }
  });
  window.addEventListener('resize', () => { if (st && st.tab === 'now') draw(); });

  return {
    update(state, changed) {
      if (changed.includes('inspect')) {
        if (state.inspect) open(state.inspect, state.inspectOpts);
        else if (st) closeNow();
        return;
      }
      if (changed.includes('inspectOpts') && st && state.inspectOpts) { open(st.symbol, state.inspectOpts); return; }
      if (!st) return;
      // the live price moves twice a second; redraw at most once a second, and never under the operator's typing
      if (changed.includes('live') && state.now - lastLiveDraw >= 1 && !typing()) {
        lastLiveDraw = state.now;
        draw();
      }
    },
    destroy() { stop(); },
  };
}
