// The tape: one plate, three bands on one time axis.
//   equity, net of costs (gross dashed)  /  one tick per closed trade,
//   height = R  /  drawdown from the peak, underwater.
// The time axis runs over trading time only (each session day is 09:15 to
// 15:30), so nights and weekends don't stretch the curve.

import { html, render } from '../../shared/dom.js';
import { MINUS, dayMonth, hm, inr, istDay, istMinutes, r as fmtR, trim } from '../../shared/format.js';
import { SETTING, exitLabel, settingValue, sideLabel } from '../../shared/labels.js';
import { f1, hline, linear, stepPath, vline } from '../../shared/charts/svg.js';
import { whenChanged } from '../../shared/store.js';

const OPEN_MIN = 555;        // 09:15
const SESSION_MIN = 375;     // to 15:30

// ts -> 0..1 along the session days (clamped into the trading window)
export function timeAxis(days, now) {
  const idx = new Map(days.map((d, i) => [d, i]));
  const n = Math.max(days.length, 1);
  const x = (ts) => {
    const d = istDay(ts);
    let i = idx.get(d);
    if (i === undefined) i = d < (days[0] || d) ? 0 : n - 1;       // outside: pin to an end
    const frac = Math.min(1, Math.max(0, (istMinutes(ts) - OPEN_MIN) / SESSION_MIN));
    return (i + frac) / n;
  };
  return { x, nowX: now !== undefined && idx.has(istDay(now)) ? x(now) : 1 };
}

function settingText(change) {
  const keys = Object.keys(change.new || {});
  return keys.map((k) => {
    const fmt = (v) => settingValue(k, v, inr, trim);
    return `${SETTING[k] || k} ${fmt((change.old || {})[k])} → ${fmt(change.new[k])}`;
  }).join('; ');
}

/**
 * Pure: the equity API body -> everything the SVG needs.
 * size: {W, eqH, tkH, ddH, gap}
 */
export function tapeGeometry(eq, size, now) {
  const { W, eqH, tkH, ddH, gap = 16 } = size;
  const days = eq.session_days || [];
  const intraday = days.length <= 1;
  const { x: tx, nowX } = timeAxis(days, now);
  const X = (v) => v * W;
  const pts = (eq.realised || []).map((p) => ({ x: p.ts === eq.realised[0].ts ? 0 : tx(p.ts), net: p.equity,
    gross: p.equity - p.pnl + p.gross, dd: p.dd, dd_pct: p.dd_pct, ts: p.ts }));
  const start = pts.length ? pts[0].net : null;
  const base = start ?? eq.start_equity ?? null;
  const last = pts.length ? pts[pts.length - 1] : null;
  if (last) pts.push({ ...last, x: Math.max(last.x, nowX), ts: now });

  // equity band
  const vals = pts.flatMap((p) => [p.net, p.gross]).concat(base === null ? [] : [base]);
  let lo = vals.length ? Math.min(...vals) : 0;
  let hi = vals.length ? Math.max(...vals) : 1;
  const minSpan = Math.max(Math.abs(base || 0) * 0.01, 1000);      // flat books still get a readable band
  if (hi - lo < minSpan) { const mid = (hi + lo) / 2; lo = mid - minSpan / 2; hi = mid + minSpan / 2; }
  const pad = (hi - lo) * 0.12;
  const Y = linear(lo - pad, hi + pad, eqH, 0);
  const net = stepPath(pts.map((p) => [X(p.x), Y(p.net)]));
  const gross = pts.length > 1 ? stepPath(pts.map((p) => [X(p.x), Y(p.gross)])) : '';

  // tick band
  const tkTop = eqH + gap;
  const mid = tkTop + tkH / 2;
  const ticks = (eq.ticks || []).map((t) => {
    const h = Math.max(2, Math.min(Math.abs(t.r_net ?? 0) / 2, 1) * (tkH / 2 - 2));
    const up = (t.net ?? 0) >= 0;
    const xx = X(tx(t.ts));
    return { pos_id: t.pos_id, x: xx, y1: up ? mid - h : mid, y2: up ? mid : mid + h, up,
      title: `${t.symbol} ${sideLabel(t.side)}, ${exitLabel(t.reason)}, ${inr(t.net)} (${fmtR(t.r_net)}) at ${hm(t.ts)}` };
  });

  // drawdown band
  const ddTop = tkTop + tkH + gap;
  const H = ddTop + ddH;
  const dip = pts.length ? Math.min(0, ...pts.map((p) => p.dd)) : 0;
  const DY = linear(0, Math.min(dip, -1), ddTop, ddTop + ddH - 4);
  const ddLine = pts.length ? stepPath(pts.map((p) => [X(p.x), DY(p.dd)])) : '';
  const ddArea = dip < 0 ? `${ddLine}V${f1(ddTop)}H0Z` : '';
  const dipPt = dip < 0 ? pts.find((p) => p.dd === dip) : null;

  // settings changes
  const sets = (eq.settings_changes || []).map((c) => ({ x: X(tx(c.ts)), t: settingText(c) }));

  // labels
  const yLabels = [];
  if (base !== null) yLabels.push({ t: inr(base, { signed: false }), y: Y(base) });
  if (pts.length && Y(hi) < Y(base ?? hi) - 18) yLabels.unshift({ t: inr(hi, { signed: false }), y: Y(hi) });
  if (pts.length && Y(lo) > Y(base ?? lo) + 18) yLabels.push({ t: inr(lo, { signed: false }), y: Y(lo) });
  yLabels.push({ t: '+2 R', y: tkTop + 2 }, { t: `${MINUS}2 R`, y: tkTop + tkH - 2 }, { t: '₹0', y: ddTop });
  if (dip < 0) yLabels.push({ t: inr(dip), y: ddTop + ddH - 4 });

  let xLabels;
  if (intraday) {
    xLabels = [['09:15', 0], ['10:30', 0.2], ['11:45', 0.4], ['13:00', 0.6], ['14:15', 0.8], ['15:30', 1]];
  } else {
    const k = Math.min(5, days.length);
    const picks = Array.from({ length: k }, (_, i) => Math.round((i * (days.length - 1)) / Math.max(k - 1, 1)));
    xLabels = [...new Set(picks)].map((i) => {
      const [y, m, d] = days[i].split('-').map(Number);
      return [dayMonth(Date.UTC(y, m - 1, d, 6, 30) / 1000), i / days.length];
    });
  }

  return {
    W, H, empty: !pts.length, intraday,
    paths: {
      grid: `${hline(mid, 0, W)}${hline(ddTop, 0, W)}`,
      base: base === null ? '' : hline(Y(base), 0, W),
      set: sets.map((s) => vline(s.x, 0, H)).join(''),
      gross, net, ddLine, ddArea,
    },
    ticks, sets, yLabels, xLabels,
    bands: [{ t: 'Equity, net of costs', top: 0 }, { t: 'Closed trades, height = R', top: tkTop },
      { t: 'Drawdown from peak', top: ddTop + 4 }],
    eqH, dip, dipLabel: dipPt ? { t: `deepest ${inr(dip)}`, x: X(dipPt.x), y: DY(dip) } : null,
  };
}

function caption(range, days) {
  if (!range) return '';
  if (!days.length) return 'Waiting for the first trade';
  if (days.length === 1) return `${range.name === 'today' ? 'Today' : 'One session'}, 09:15–15:30`;
  return `${days.length} sessions, trading hours only`;
}

export function mount(el, store, actions) {
  let seen = null;             // tick ids already drawn: new ones get the one orchestrated moment

  el.addEventListener('click', (e) => {
    const t = e.target.closest('[data-tick]');
    if (t) actions.openTrade(t.dataset.tick);
  });

  function size() {
    const w = el.clientWidth || 1200;
    const narrow = w < 640;
    const inner = w - (narrow ? 34 : 50) - (narrow ? 0 : 84);
    return narrow ? { W: Math.max(inner, 200), eqH: 120, tkH: 44, ddH: 40, narrow }
      : { W: Math.max(inner, 400), eqH: 210, tkH: 72, ddH: 64, narrow };
  }

  // redrawn when the curve changes or the plate is resized -- not every
  // second, so a hovered tick keeps its tooltip
  function draw(state) {
    const eq = state.equity;
    if (!eq) {
      render(el, html`<section class="pf-plate pf-tape" aria-label="The tape"><h2 class="pf-h2">The tape</h2>
        <p class="pf-meta">${state.equityError ? `Couldn’t load the curve: ${state.equityError}` : 'Loading…'}</p></section>`);
      return;
    }
    const s = size();
    const g = tapeGeometry(eq, s, state.now);
    const fresh = seen ? new Set(g.ticks.filter((t) => !seen.has(t.pos_id)).map((t) => t.pos_id)) : new Set();
    seen = new Set(g.ticks.map((t) => t.pos_id));
    const tick = (t) => html`<g data-tick="${t.pos_id}" class="pf-tick${fresh.has(t.pos_id) ? ' pf-tick-new' : ''}"
        style="transform-origin:${f1(t.x)}px ${f1(t.up ? t.y2 : t.y1)}px"><title>${t.title}</title>
        <rect x="${f1(t.x - 4)}" y="${f1(t.y1 - 3)}" width="8" height="${f1(t.y2 - t.y1 + 6)}" fill="transparent"></rect>
        <path d="${vline(t.x, t.y1, t.y2)}" class="${t.up ? 'pf-up' : 'pf-down'}"></path></g>`;
    render(el, html`
      <section class="pf-plate pf-tape" aria-label="The tape">
        <div class="pf-tape-head">
          <h2 class="pf-h2">The tape</h2>
          <span class="pf-meta">${caption(eq.range, eq.session_days || [])}</span>
          <span class="pf-grow"></span>
          <div class="pf-legend pf-meta" aria-hidden="true">
            <span><i class="pf-key-net"></i>Net</span><span><i class="pf-key-gross"></i>Gross</span>
            <span><i class="pf-key-up"></i><i class="pf-key-down"></i>Closed trade</span>
            <span><i class="pf-key-set"></i>Settings change</span>
          </div>
        </div>
        <div class="pf-tape-box" style="width:${f1(g.W + (s.narrow ? 0 : 84))}px;height:${f1(g.H + 30)}px">
          <svg width="${f1(g.W)}" height="${f1(g.H)}" aria-hidden="true">
            <path d="${g.paths.grid}" class="pf-grid"></path>
            <path d="${g.paths.base}" class="pf-baseline"></path>
            <path d="${g.paths.set}" class="pf-setline"></path>
            <path d="${g.paths.gross}" class="pf-gross"></path>
            <path d="${g.paths.net}" class="pf-net"></path>
            ${g.ticks.map(tick)}
            <path d="${g.paths.ddArea}" class="pf-dd-area"></path>
            <path d="${g.paths.ddLine}" class="pf-dd-line"></path>
          </svg>
          ${g.bands.map((b) => html`<span class="pf-band" style="top:${f1(b.top)}px">${b.t}</span>`)}
          ${s.narrow ? '' : g.sets.map((c) => html`<span class="pf-setlabel" style="${c.x > g.W - 240
            ? `left:${f1(c.x - 6)}px;transform:translateX(-100%)` : `left:${f1(c.x + 6)}px`};top:${f1(g.eqH - 22)}px">${c.t}</span>`)}
          ${s.narrow ? '' : g.yLabels.map((l) => html`<span class="pf-axis" style="left:${f1(g.W + 10)}px;top:${f1(l.y - 10)}px">${l.t}</span>`)}
          ${g.xLabels.map(([t, x], i, a) => html`<span class="pf-axis" style="left:${f1(x * g.W)}px;top:${f1(g.H + 8)}px;transform:translateX(${i === 0 ? '0' : i === a.length - 1 && x >= 0.99 ? '-100%' : '-50%'})">${t}</span>`)}
          ${g.dipLabel && !s.narrow ? html`<span class="pf-dip" style="left:${f1(g.dipLabel.x + 6)}px;top:${f1(g.dipLabel.y - 2)}px">${g.dipLabel.t}</span>` : ''}
        </div>
        ${g.empty ? html`<p class="pf-meta">A flat line at the starting capital until the first trade closes.</p>` : ''}
        <p class="pf-sr">${g.empty ? 'No closed trades in this range.' : `${g.ticks.length} closed trades; deepest drawdown ${inr(g.dip)}. Every trade is listed under Every trade.`}</p>
      </section>`);
  }

  let pending = null;
  let lastW = el.clientWidth;
  const ro = globalThis.ResizeObserver ? new ResizeObserver(() => {
    if (el.clientWidth === lastW) return;          // our own re-render changes height, not width
    lastW = el.clientWidth;
    cancelAnimationFrame(pending);
    pending = requestAnimationFrame(() => draw(store.get()));
  }) : null;
  ro?.observe(el);
  const update = whenChanged((s) => [s.equity, s.equityError], draw);
  return { update, destroy() { ro?.disconnect(); el.textContent = ''; } };
}
