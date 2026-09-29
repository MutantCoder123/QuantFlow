// The Market tab's markup (desktop sections, then the phone's). Data in,
// html`` out; clicks carry data-act / data-open and are handled in index.js.

import { bool, html, raw } from '../shared/dom.js';
import { DASH, duration, group, isoDay, num } from '../shared/format.js';
import { icons } from '../shared/icons.js';
import { sentenceCase } from '../shared/labels.js';
import { SESSION_MIN, SESSION_TICKS, flowBars, lineGeometry, quadrantGeometry, ratioWords, sessionMinute, signedNum,
  signedPct, spark, tileTone, tilt, toneOf, waffle } from './model.js';

const px = (v, dp = 2) => (typeof v === 'number' && Number.isFinite(v) ? group(v, dp) : DASH);
const TONE_CLASS = { up: 'qf-up', down: 'qf-down', amber: 'qf-amber', mute: 'qf-mute' };

// ----------------------------------------------------------------- NIFTY
function indexPoints(nifty, bars) {
  const pts = (bars || []).map((b) => ({ x: sessionMinute(b.ts) + 2.5, y: b.c }));
  if (nifty && nifty.ltp && pts.length) pts.push({ x: Math.min(SESSION_MIN, sessionMinute(Date.now() / 1000)), y: nifty.ltp });
  return pts;
}

export function hero({ nifty, bars, daily, width }) {
  const n = nifty || {};
  const W = Math.max(280, Math.floor(width));
  const H = 200;
  const live = n.source === 'feed' && n.ltp;
  const intraday = live ? indexPoints(n, bars) : [];
  const g = intraday.length >= 2
    ? lineGeometry(intraday, { width: W, height: H, xMax: SESSION_MIN, refs: [n.prev_close] })
    : daily && daily.points && daily.points.length >= 2
      ? lineGeometry(daily.points.map((p, i) => ({ x: i, y: p.close })), { width: W, height: H })
      : null;
  const down = live ? (n.change_pct || 0) < 0 : daily ? (daily.change_pct || 0) < 0 : false;
  const stroke = down ? 'var(--coral)' : 'var(--jade)';
  const fill = down ? 'rgba(238, 111, 106, .10)' : 'rgba(69, 199, 154, .10)';
  const prevY = live && g && n.prev_close ? g.y(n.prev_close) : null;
  const label = live
    ? `NIFTY 50 at ${px(n.ltp)}, ${signedPct(n.change_pct)} on the day${n.prev_close ? `, against a previous close of ${px(n.prev_close)}` : ''}.`
    : daily && daily.points ? `NIFTY 50 daily closes over the last month, last ${px(daily.last)} on ${isoDay(daily.points[daily.points.length - 1].date)}.` : 'No index data.';
  return html`
    <section class="qf-section mk-hero" aria-label="NIFTY 50 today">
      <div class="mk-hero-head">
        <div class="mk-hero-fig"><span class="qf-eyebrow">NIFTY 50</span>
          <span class="qf-kpi">${live ? group(n.ltp, 0) : n.last_close ? group(n.last_close, 0) : DASH}</span></div>
        <div class="mk-hero-sub">
          ${live ? html`<span class="mk-hero-chg" data-tone="${toneOf(n.change_pct)}">${signedPct(n.change_pct)}  ${signedNum(n.change)}</span>
            <span class="qf-cap">${n.open ? `Open ${group(n.open, 0)} · high ${group(n.high, 0)} · low ${group(n.low, 0)}` : n.prev_close ? `Previous close ${group(n.prev_close, 0)}` : ''}${n.stale ? html` · <span class="qf-amber">${n.age_s ? `${duration(n.age_s)} old` : 'not updating'}</span>` : ''}</span>`
    : html`<span class="qf-cap">${n.last_close ? `Last close, ${isoDay(n.last_close_date)}` : ''}</span>
            <span class="qf-cap qf-amber">No live index price yet</span>`}
        </div>
      </div>
      ${g ? html`
        <div class="qf-plot mk-line" style="width:${W}px;height:${H + 22}px" role="img" aria-label="${label}">
          <div class="qf-gridline" style="top:${(H * 0.25).toFixed(0)}px"></div>
          <div class="qf-gridline" style="top:${(H * 0.5).toFixed(0)}px"></div>
          <div class="qf-gridline" style="top:${(H * 0.75).toFixed(0)}px"></div>
          <svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">
            ${raw(`<path d="${g.area}" fill="${fill}"/>`)}
            ${prevY !== null ? raw(`<line x1="0" x2="${W}" y1="${prevY.toFixed(1)}" y2="${prevY.toFixed(1)}" class="mk-prev"/>`) : ''}
            ${raw(`<path d="${g.path}" fill="none" stroke="${stroke}" stroke-width="1.75" stroke-linejoin="round"/>`)}
            ${raw(`<circle cx="${g.last.x}" cy="${g.last.y}" r="4" fill="${stroke}" stroke="var(--ink)" stroke-width="2"/>`)}
          </svg>
          ${prevY !== null ? html`<span class="qf-axis mk-prev-label" style="top:${(prevY - 8).toFixed(0)}px">prev ${group(n.prev_close, 0)}</span>` : ''}
          ${intraday.length >= 2
            ? SESSION_TICKS.map(([m, t]) => html`<span class="qf-axis mk-xtick" style="left:${((m / SESSION_MIN) * W).toFixed(0)}px;top:${H + 4}px">${t}</span>`)
            : html`<span class="qf-axis" style="left:0;top:${H + 4}px">${isoDay(daily.points[0].date)}</span>
              <span class="qf-axis" style="right:0;top:${H + 4}px">${isoDay(daily.points[daily.points.length - 1].date)}</span>`}
        </div>` : html`<p class="qf-note">The chart appears once the index trades.</p>`}
    </section>`;
}

// --------------------------------------------------------- breadth, flows
export function breadthView(b) {
  const cells = waffle(b);
  const counts = cells ? html`<span class="qf-up">${b.advances} up</span> · <span class="qf-down">${b.declines} down</span> of ${cells.length}` : '';
  return html`
    <section class="qf-section mk-small" aria-label="Breadth">
      <div class="qf-between"><h3 class="qf-h3">Breadth</h3><span class="qf-cap">${cells ? counts : b && b.source === 'WATCHLIST_PROXY' ? 'your watchlist (NSE breadth is stale)' : 'NIFTY 50'}</span></div>
      ${cells ? html`<div class="mk-waffle" role="img" aria-label="${b.advances} of the NIFTY 50 are up, ${b.declines} are down">
          ${cells.map((c) => html`<i data-tone="${c}"></i>`)}</div>`
        : b && b.ratio !== null ? html`<p class="mk-ratio"><span class="qf-fig" data-tone="${b.ratio >= 1 ? 'profit' : 'loss'}">${num(b.ratio, 2)}</span> <span class="qf-mute">${ratioWords(b.ratio)}</span></p>`
          : html`<p class="qf-note">No breadth reading yet.</p>`}
    </section>`;
}

export function flowsView(f, width) {
  const W = Math.max(240, Math.floor(width));
  const g = flowBars(f && f.history, { width: W, height: 128 });
  const l = (f && f.latest) || {};
  return html`
    <section class="qf-section mk-small" aria-label="Institutional flow">
      <div class="qf-between"><h3 class="qf-h3">FII and DII flow</h3>
        <span class="qf-cap mk-flowkey"><i class="mk-fii"></i>FII <i class="mk-dii"></i>DII · ₹ Cr</span></div>
      ${g ? html`
        <div class="qf-plot" style="width:${W}px;height:128px" role="img"
          aria-label="FIIs sold on ${g.fiiSold} of the last ${g.sessions} sessions and DIIs bought on ${g.diiBought}. Latest, ${isoDay(l.date)}: FII ${signedNum(l.fii_net)}, DII ${signedNum(l.dii_net)} crore.">
          <div class="qf-gridline" style="top:${g.zero}px;background:var(--rule-strong)"></div>
          ${g.bars.map((b) => html`
            ${b.fii ? html`<span class="mk-bar mk-fii" style="left:${b.fii.x}px;top:${b.fii.top}px;width:${g.bw}px;height:${b.fii.h}px" title="${isoDay(b.date)} FII"></span>` : ''}
            ${b.dii ? html`<span class="mk-bar mk-dii" style="left:${b.dii.x}px;top:${b.dii.top}px;width:${g.bw}px;height:${b.dii.h}px" title="${isoDay(b.date)} DII"></span>` : ''}`)}
          <span class="qf-axis" style="left:0;top:112px">${isoDay(g.bars[0].date)}</span>
          <span class="qf-axis" style="right:0;top:112px">${isoDay(l.date)}</span>
        </div>
        <p class="qf-cap mk-flow-latest">${isoDay(l.date)}: FII <span data-tone="${toneOf(l.fii_net)}">${signedNum(l.fii_net)}</span> · DII <span data-tone="${toneOf(l.dii_net)}">${signedNum(l.dii_net)}</span></p>` : l.fii_net !== null && l.fii_net !== undefined ? html`<p class="mk-ratio">FII <b data-tone="${toneOf(l.fii_net)}">₹${signedNum(l.fii_net)} Cr</b> · DII <b data-tone="${toneOf(l.dii_net)}">₹${signedNum(l.dii_net)} Cr</b>${l.date ? html` <span class="qf-cap">on ${isoDay(l.date)}</span>` : ''}</p>`
        : html`<p class="qf-note">No flow figures yet.</p>`}
    </section>`;
}

const SENT = { BULLISH: ['Bullish.', 'qf-up'], BEARISH: ['Bearish.', 'qf-down'], MIXED: ['Mixed.', 'qf-amber'], NEUTRAL: ['Neutral.', 'qf-mute'] };
export function globalRead(ctx, now) {
  const s = ctx && (SENT[String(ctx.sentiment || '').toUpperCase()] || [sentenceCase(ctx.sentiment || '') + '.', 'qf-mute']);
  return html`
    <section class="qf-section mk-small" aria-label="Global read">
      <div class="qf-between"><h3 class="qf-h3">Global read</h3><span class="qf-cap">${ctx && ctx.ts ? `${duration(now - ctx.ts)} ago` : ''}</span></div>
      ${ctx ? html`<p class="mk-read"><span class="${s[1]}">${s[0]}</span> ${ctx.summary}</p>`
        : html`<p class="qf-note">The news service hasn’t sent a read yet.</p>`}
    </section>`;
}

// -------------------------------------------------------------- sectors
export function heatmap(sectors) {
  if (!sectors || !sectors.length) return html`<section class="qf-section"><h3 class="qf-h3">Your watchlist by sector</h3><p class="qf-note">Waiting for prices.</p></section>`;
  const best = sectors.filter((s) => s.name !== 'Others')[0];
  return html`
    <section class="qf-section" aria-label="Sectors today">
      <div class="qf-between"><h3 class="qf-h3">Your watchlist by sector</h3>
        <span class="qf-cap mk-scale"><span>${'−3%'}</span>
          ${[-3, -1.5, -0.5, 0.5, 1.5, 3].map((c) => html`<i style="background:${tileTone(c).bg}"></i>`)}
          <span>+3%</span></span></div>
      <div class="mk-sectors" role="img" aria-label="${best ? `${best.name} is the strongest sector on your watchlist today at ${signedPct(best.avg)}.` : ''}">
        ${sectors.map((g) => html`
          <div class="mk-sector">
            <div class="mk-sector-name"><span>${g.name}</span>
              <span class="qf-cap"><span data-tone="${toneOf(g.avg)}">${signedPct(g.avg)}</span> · ${g.count}</span></div>
            <div class="mk-tiles">${g.tiles.map((t) => {
              const tone = tileTone(t.change_pct);
              return html`<button type="button" class="mk-tile" data-open="${t.symbol}" style="background:${tone.bg};border-color:${tone.bd}">
                <b>${t.symbol}</b><span style="color:${tone.fg}">${signedPct(t.change_pct)}</span></button>`;
            })}</div>
          </div>`)}
      </div>
    </section>`;
}

export function quadrantView(points, width) {
  const W = Math.max(260, Math.floor(width));
  const H = 300;
  const g = quadrantGeometry(points, { width: W, height: H });
  return html`
    <section class="qf-section" aria-label="Order book against price">
      <div class="qf-between"><h3 class="qf-h3">Order book vs price</h3><span class="qf-cap">${points ? points.length : 0} stocks</span></div>
      ${g ? html`
        <div class="qf-plot mk-quad" style="width:${W}px;height:${H}px" role="img"
          aria-label="${g.absorbing.length ? `${g.absorbing.join(', ')} ${g.absorbing.length === 1 ? 'has' : 'have'} buyers leading the book while the price falls.` : 'No stock has buyers leading the book into a fall.'}">
          <div class="mk-q-axis" style="left:${g.x0}px;top:0;bottom:0;width:1px"></div>
          <div class="mk-q-axis" style="top:${g.y0}px;left:0;right:0;height:1px"></div>
          <span class="qf-axis" style="left:12px;top:10px">Sellers into strength</span>
          <span class="qf-axis" style="right:12px;top:10px">Buyers, rising</span>
          <span class="qf-axis" style="left:12px;bottom:10px">Sellers, falling</span>
          <span class="qf-axis qf-ind" style="right:12px;bottom:10px">Buyers absorbing a fall</span>
          ${g.points.map((p) => html`
            <button type="button" class="qf-pt mk-q-pt" data-open="${p.symbol}" data-tone="${p.tone}" style="left:${p.x}px;top:${p.y}px" aria-label="${p.symbol}"></button>
            ${p.label ? html`<span class="qf-ptlabel" data-tone="${p.tone === 'ind' ? 'ind' : 'mute'}" style="left:${p.x}px;top:${p.y}px">${p.symbol}</span>` : ''}`)}
          <span class="qf-axis mk-q-x">book tilt → buyers · ±${num(g.ox, 1)}</span>
          <span class="qf-axis mk-q-y">change today ±${g.oy}%</span>
        </div>` : html`<p class="qf-note">Waiting for order-book readings.</p>`}
    </section>`;
}

// --------------------------------------------------------------- board
export const FILTERS = [{ id: 'all', label: 'All' }, { id: 'signals', label: 'Signals' }, { id: 'held', label: 'Held' }];

export function boardHead({ filter, counts, query }) {
  return html`
    <div class="qf-row mk-board-left"><h2 class="qf-h2">The board</h2>
      <div class="qf-row mk-chips" role="group" aria-label="Show">
        ${FILTERS.map((f) => html`<button type="button" class="qf-chip" data-act="filter" data-filter="${f.id}" data-key="mk-f-${f.id}"
          aria-pressed="${bool(filter === f.id)}">${f.label} <b>${counts[f.id]}</b></button>`)}
      </div></div>
    <label class="qf-search">${icons.search}<input type="search" data-act="query" data-key="mk-query" value="${query}"
      placeholder="Find a stock" aria-label="Find a stock" autocomplete="off"></label>`;
}

export function boardBody({ rows, engine, sparks }) {
  if (!rows.length) return html`<tr><td colspan="12" class="qf-empty">No stock matches.</td></tr>`;
  return html`${rows.map((r) => {
    const e = engine[r.symbol];
    const t = tilt(r.obi);
    const sp = spark(sparks[r.symbol]);
    return html`
      <tr data-open="${r.symbol}" tabindex="0" aria-label="${r.symbol}, open in the Inspector">
        <td><span class="qf-sym">${r.symbol}</span>${r.stale ? html`<small class="qf-amber mk-age">${Math.round(r.age)} s old</small>` : ''}</td>
        <td class="qf-r">${px(r.ltp)}</td>
        <td class="qf-r"><span data-tone="${toneOf(r.change)}">${signedPct(r.change)}</span></td>
        <td>${sp ? raw(`<svg width="72" height="22" viewBox="0 0 72 22" aria-hidden="true"><polyline points="${sp}" fill="none" stroke="${r.change < 0 ? 'var(--coral)' : 'var(--jade)'}" stroke-width="1.5" stroke-linejoin="round"/></svg>`) : html`<span class="qf-mute">${DASH}</span>`}</td>
        <td class="qf-c">${t ? html`<span class="qf-tilt" title="${num(r.obi, 2)}"><i style="left:${t.left}%;width:${t.width}%;background:${t.tone === 'profit' ? 'var(--jade)' : 'var(--coral)'}"></i></span>` : DASH}</td>
        <td class="qf-r"><span data-tone="${toneOf(r.cvd)}">${signedNum(r.cvd)}</span></td>
        <td>${r.rsi !== null ? html`<span class="mk-rsi-v">${Math.round(r.rsi)}</span><span class="mk-rsi"><i style="left:${Math.max(0, Math.min(100, r.rsi))}%"></i></span>` : DASH}</td>
        <td class="qf-r">${num(r.pcr, 2)}</td>
        <td class="qf-r">${r.ivr !== null ? Math.round(r.ivr) : DASH}</td>
        <td class="qf-r">${px(r.maxPain, r.maxPain >= 1000 ? 0 : 2)}</td>
        <td class="qf-mute">${r.pattern || DASH}</td>
        <td>${e ? html`<span class="${TONE_CLASS[e.tone] || 'qf-mute'}">${e.text}</span>` : html`<span class="qf-mute">${DASH}</span>`}</td>
      </tr>`;
  })}`;
}

// ---------------------------------------------------------------- wire
export function wire({ items, news, now, busy, error }) {
  const last = news && news.last_fetch;
  const status = !news || !news.reachable ? 'The news service isn’t reachable.'
    : `${last ? `Fetched ${duration(now - last)} ago` : 'Not fetched yet'}${news.active && news.interval ? ` · every ${duration(news.interval)}` : ' · auto fetch off'}`;
  return html`
    <section class="qf-section" aria-label="The wire">
      <div class="qf-between"><h3 class="qf-h3">The wire</h3>
        <span class="qf-row"><span class="qf-cap">${status}</span>
          <button type="button" class="qf-pill qf-text" data-act="fetch-news" ${busy ? raw('disabled') : ''}>${busy ? 'Fetching…' : 'Fetch now'}</button></span></div>
      ${error ? html`<p class="qf-error">${error}</p>` : ''}
      ${items && items.length ? html`<div class="mk-wire">${items.map((w) => html`
        <button type="button" class="mk-wire-row" data-open="${w.symbol}">
          <span class="qf-sym">${w.symbol}</span><span>${w.headline}</span></button>`)}</div>`
        : html`<p class="qf-note">No headlines for your watchlist yet.</p>`}
    </section>`;
}

// ---------------------------------------------------------------- phone
export const RANGES = ['1D', '1W', '1M', '1Y', '5Y'];

export function phoneNifty({ nifty, bars, history, range, width }) {
  const n = nifty || {};
  const W = Math.max(240, Math.floor(width));
  const H = 150;
  const live = n.source === 'feed' && n.ltp;
  let g = null;
  let first = '';
  let lastLabel = '';
  let chg = null;
  let chgAbs = null;
  if (range === '1D') {
    const pts = live ? indexPoints(n, bars) : [];
    g = pts.length >= 2 ? lineGeometry(pts, { width: W, height: H, xMax: SESSION_MIN, refs: [n.prev_close] }) : null;
    first = '09:15'; lastLabel = '15:30';
    chg = live ? n.change_pct : null; chgAbs = live ? n.change : null;
  } else if (history && history.points && history.points.length >= 2) {
    g = lineGeometry(history.points.map((p, i) => ({ x: i, y: p.close })), { width: W, height: H });
    first = isoDay(history.points[0].date); lastLabel = isoDay(history.points[history.points.length - 1].date);
    chg = history.change_pct; chgAbs = history.last - history.first;
  }
  const value = live ? n.ltp : n.last_close;
  const whole = typeof value === 'number' ? group(Math.floor(value), 0) : DASH;
  const frac = typeof value === 'number' ? (value % 1).toFixed(2).slice(1) : '';
  const down = (chg || 0) < 0;
  const stroke = down ? 'var(--coral)' : 'var(--jade)';
  return html`
    <section class="mk-card mk-pnifty" aria-label="NIFTY 50">
      <div class="qf-between"><span class="qf-eyebrow">NIFTY 50 · NSE</span><span class="qf-cap">${live ? 'Index' : n.last_close ? `Last close ${isoDay(n.last_close_date)}` : ''}</span></div>
      <div class="mk-pfig"><span>${whole}</span><small>${frac}</small></div>
      <div class="qf-row mk-pchg">
        ${chg !== null && chg !== undefined ? html`<span class="mk-pchip" data-tone="${toneOf(chg)}">${signedPct(chg)}</span>
          <span data-tone="${toneOf(chg)}">${signedNum(chgAbs, 2)}</span><span class="qf-cap">${range === '1D' ? 'today' : `over ${range}`}</span>`
          : html`<span class="qf-cap qf-amber">${range === '1D' ? 'No live index price yet' : 'No history'}</span>`}
      </div>
      <div class="mk-range" role="group" aria-label="Chart range">
        ${RANGES.map((r) => html`<button type="button" data-act="range" data-range="${r}" aria-pressed="${bool(r === range)}">${r}</button>`)}
      </div>
      ${g ? html`
        <div class="qf-plot" style="width:${W}px;height:${H}px" role="img" aria-label="NIFTY 50 over ${range}">
          <svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">
            ${raw(`<path d="${g.area}" fill="${down ? 'rgba(238, 111, 106, .09)' : 'rgba(69, 199, 154, .09)'}"/>`)}
            ${range === '1D' && n.prev_close ? raw(`<line x1="0" x2="${W}" y1="${g.y(n.prev_close).toFixed(1)}" y2="${g.y(n.prev_close).toFixed(1)}" class="mk-prev"/>`) : ''}
            ${raw(`<path d="${g.path}" fill="none" stroke="${stroke}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`)}
            ${raw(`<circle cx="${g.last.x}" cy="${g.last.y}" r="3.5" fill="${stroke}" stroke="var(--slate)" stroke-width="1.5"/>`)}
          </svg>
        </div>
        <div class="qf-between qf-cap"><span>${first}</span><span>${lastLabel}</span></div>` : html`<p class="qf-note">${range === '1D' ? 'The intraday line starts when the index trades.' : 'Loading…'}</p>`}
      <div class="mk-pstats">
        <div><span class="qf-cap">Open</span><b>${px(n.open)}</b></div>
        <div><span class="qf-cap">Prev close</span><b>${px(n.prev_close)}</b></div>
        <div><span class="qf-cap">From open</span><b data-tone="${toneOf(live && n.open ? n.ltp - n.open : null)}">${live && n.open ? signedNum(n.ltp - n.open, 2) : DASH}</b></div>
      </div>
    </section>`;
}

export function phoneBreadth(b, f) {
  const l = (f && f.latest) || {};
  const counts = b && typeof b.advances === 'number' && typeof b.declines === 'number';
  return html`
    <section class="mk-card" aria-label="Market breadth and flows">
      <div class="qf-between"><h2 class="qf-h3">Market breadth</h2><span class="qf-cap">${counts ? 'NIFTY 50 constituents' : 'your watchlist'}</span></div>
      ${counts ? html`
        <div class="mk-pbar" role="img" aria-label="${b.advances} of the NIFTY 50 are up, ${b.declines} are down">
          <span class="qf-up-bg" style="flex-grow:${b.advances}"></span><span class="qf-down-bg" style="flex-grow:${b.declines}"></span></div>
        <div class="qf-between"><b class="qf-up">${b.advances} advancing</b><b class="qf-down">${b.declines} declining</b></div>`
        : b && b.ratio !== null ? html`<p class="qf-note">${ratioWords(b.ratio)}.</p>` : html`<p class="qf-note">No reading yet.</p>`}
      <div class="mk-ptiles2">
        <div><span class="qf-cap">FII net${l.date ? ` · ${isoDay(l.date)}` : ''}</span><b data-tone="${toneOf(l.fii_net)}">${l.fii_net !== null && l.fii_net !== undefined ? `₹${signedNum(l.fii_net)} Cr` : DASH}</b></div>
        <div><span class="qf-cap">DII net${l.date ? ` · ${isoDay(l.date)}` : ''}</span><b data-tone="${toneOf(l.dii_net)}">${l.dii_net !== null && l.dii_net !== undefined ? `₹${signedNum(l.dii_net)} Cr` : DASH}</b></div>
      </div>
    </section>`;
}

export function phoneTiles(stocks) {
  const sorted = [...(stocks || [])].filter((s) => s.change !== null).sort((a, b) => b.change - a.change);
  return html`
    <section class="qf-section" aria-label="Your watchlist">
      <div class="qf-between"><h2 class="qf-h2">Your watchlist</h2><span class="qf-cap">${sorted.length} stocks · by day change</span></div>
      <div class="mk-ptiles">${sorted.map((s) => {
        const tone = tileTone(s.change);
        return html`<button type="button" class="mk-tile" data-open="${s.symbol}" style="background:${tone.bg};border-color:${tone.bd}">
          <b>${s.symbol}</b><span style="color:${tone.fg}">${signedPct(s.change)}</span></button>`;
      })}</div>
    </section>`;
}

export function held(list) {
  return html`
    <section class="qf-section" aria-label="Held and signalling">
      <div class="qf-between"><h2 class="qf-h2">Held and signalling</h2><a href="#signals" class="qf-cap qf-ind">All signals</a></div>
      ${list.length ? html`<div class="mk-card mk-held">${list.map((r) => {
        const t = tilt(r.obi);
        return html`
          <button type="button" class="mk-held-row" data-open="${r.symbol}">
            <span class="mk-mono">${r.symbol.slice(0, 2)}</span>
            <span class="mk-held-mid"><b>${r.symbol}</b><span class="qf-stamp" data-tone="${r.engine.tone === 'up' ? 'ok' : r.engine.tone === 'down' ? 'bad' : r.engine.tone === 'amber' ? 'wait' : 'off'}">${r.engine.text}</span></span>
            <span class="mk-held-right"><b>₹${px(r.ltp)}</b><span data-tone="${toneOf(r.change)}">${signedPct(r.change)}</span></span>
            ${t ? html`<span class="mk-held-tilt"><span class="qf-cap">Tilt</span><span class="qf-tilt"><i style="left:${t.left}%;width:${t.width}%;background:${t.tone === 'profit' ? 'var(--jade)' : 'var(--coral)'}"></i></span><span class="qf-cap">${num(r.obi, 2)}</span></span>` : ''}
          </button>`;
      })}</div>` : html`<p class="qf-note">Nothing held, and the engine is quiet.</p>`}
    </section>`;
}

