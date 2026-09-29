// The Inspector's markup. Each view is html`` from data the controller
// (index.js) already has; clicks carry data-act and are handled there.

import { bool, html, raw } from '../shared/dom.js';
import { DASH, group, hm, inr, num, pct, r as fmtR } from '../shared/format.js';
import { icons } from '../shared/icons.js';
import { candleGeometry } from '../shared/charts/candles.js';

export const TABS = [
  { id: 'now', label: 'Now' }, { id: 'ai', label: 'AI' }, { id: 'news', label: 'News' }, { id: 'raw', label: 'Raw' },
];

const px = (v, dp = 2) => (typeof v === 'number' && Number.isFinite(v) ? group(v, dp) : DASH);

export function head({ symbol, quote, stamp, tab }) {
  return html`
    <div class="qf-between">
      <div class="in-title">
        <h2 id="in-title">${symbol}</h2>
        <span class="in-last">${px(quote.ltp)}</span>
        <span data-tone="${quote.tone}" class="in-chg">${pct(quote.change, { dp: 2, signed: true })}</span>
        ${stamp ? html`<span class="qf-stamp" data-tone="${stamp.tone}">${stamp.text}</span>` : ''}
      </div>
      <button class="qf-iconbtn" type="button" data-act="close" data-key="in-close" aria-label="Close">${icons.close}</button>
    </div>
    <div class="qf-seg" role="tablist" aria-label="View">
      ${TABS.map((t) => html`<button type="button" role="tab" data-act="tab" data-tab="${t.id}" data-key="in-tab-${t.id}"
        aria-selected="${bool(t.id === tab)}" aria-controls="in-panel">${t.label}</button>`)}
    </div>`;
}

// ------------------------------------------------------------------- now
const LEVEL = {
  target: { cls: 'qf-up', label: 'Target' }, entry: { cls: 'qf-mute', label: 'Entry' }, stop: { cls: 'qf-down', label: 'Stop' },
  poc: { cls: 'qf-mute', label: 'POC' },
};

export function chart({ bars, levels, position, width }) {
  const W = Math.max(240, Math.floor(width));
  const H = 230;
  const lv = [];
  if (position) {
    lv.push({ key: 'target', price: position.target, keep: true }, { key: 'entry', price: position.entry, keep: true },
      { key: 'stop', price: position.stop, keep: true });
  }
  if (levels.poc) lv.push({ key: 'poc', price: levels.poc });
  const g = candleGeometry(bars, { width: W, height: H, levels: lv });
  if (!g) return html`<p class="in-empty">No bars yet today. The chart fills in as the feed builds five-minute bars.</p>`;
  const va = levels.va_high && levels.va_low && levels.va_low < g.hi && levels.va_high > g.lo
    ? g.band(Math.min(levels.va_high, g.hi), Math.max(levels.va_low, g.lo)) : null;
  const last = bars[bars.length - 1];
  const summary = `${bars.length} five-minute bars from ${bars[0].t} to ${last.t}; last ${px(last.c)}`
    + (position ? `; ${position.side} from ${px(position.entry)}, stop ${px(position.stop)}, target ${px(position.target)}` : '');
  // the bar the position opened in, marked under it (long) or over it (short)
  const entryIdx = position && position.opened ? bars.findIndex((b) => b.ts + 300 > position.opened) : -1;
  let mark = null;
  if (entryIdx >= 0) {
    const c = g.candles[entryIdx];
    const long = position.side === 'Long';
    mark = { x: c.x, side: position.side, top: long ? Number(c.wickTop) + Number(c.wickH) + 4 : Number(c.wickTop) - 13 };
  }
  return html`
    <div class="in-chart" style="--w:${W}px">
      <div class="qf-between in-chart-key">
        <span class="qf-legend">
          <span><i class="in-key-vwap"></i>VWAP</span>
          ${levels.poc ? html`<span><i class="in-key-poc"></i>POC</span>` : ''}
          ${va ? html`<span><i class="in-key-va"></i>20-day value area</span>` : ''}
        </span>
        <span class="qf-cap">5 min · ${bars[0].t} – ${last.t}</span>
      </div>
      <div class="qf-plot" style="width:${W}px;height:${H}px" role="img" aria-label="${summary}">
        ${va ? html`<div class="in-va" style="top:${va.top}px;height:${va.h}px"></div>` : ''}
        ${g.yTicks.map((t) => html`<div class="qf-gridline in-grid" style="top:${t.y}px"></div>
          <span class="qf-axis in-yaxis" style="top:${t.y}px">${group(t.price, t.price >= 1000 ? 0 : 2)}</span>`)}
        <svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">
          ${g.vwap ? raw(`<path d="${g.vwap}" class="in-vwap"/>`) : ''}
          ${raw(g.candles.map((c) => `<g class="${c.up ? 'in-up' : 'in-down'}"><rect x="${(Number(c.x) - 0.5).toFixed(1)}" y="${c.wickTop}" width="1" height="${c.wickH}"/>`
            + `<rect x="${(Number(c.x) - Number(c.w) / 2).toFixed(1)}" y="${c.bodyTop}" width="${c.w}" height="${c.bodyH}" rx="1"/></g>`).join(''))}
        </svg>
        ${g.lines.map((l) => html`<div class="in-lvl in-lvl-${l.key}" style="top:${l.y}px"><span class="${LEVEL[l.key].cls}">${LEVEL[l.key].label} ${px(l.price)}</span></div>`)}
        ${mark ? html`<span class="in-entry-mark" data-side="${mark.side}" style="left:${mark.x}px;top:${mark.top}px" aria-hidden="true"></span>` : ''}
      </div>
      <div class="qf-plot in-vol" style="width:${W}px;height:44px" role="img" aria-label="Volume per five minutes">
        ${g.volume.map((v) => html`<span class="${v.up ? 'in-vol-up' : 'in-vol-down'}" style="left:${v.x}px;width:${v.w}px;height:${v.h}px"></span>`)}
        <span class="qf-axis in-yaxis" style="top:22px">vol</span>
      </div>
      ${g.delta ? html`
        <div class="qf-plot in-delta" style="width:${W}px;height:50px" role="img" aria-label="Cumulative delta is ${g.delta.rising ? 'rising' : 'falling'}">
          <div class="qf-gridline in-zero" style="top:${g.delta.zero}px"></div>
          <svg width="${W}" height="50" viewBox="0 0 ${W} 50" aria-hidden="true">${raw(`<path d="${g.delta.path}" class="${g.delta.rising ? 'in-delta-up' : 'in-delta-down'}"/>`)}</svg>
          <span class="qf-axis in-yaxis" style="top:25px">delta</span>
        </div>` : ''}
      <div class="in-xaxis" style="width:${W}px">${g.xTicks.map((t) => html`<span class="qf-axis" style="left:${t.x}px">${t.t}</span>`)}</div>
    </div>`;
}

export function waterfallView(w) {
  if (!w) return html`<p class="in-empty">No score breakdown yet: the math has not scored this stock this session.</p>`;
  return html`
    <section class="in-fall" aria-label="Why the score is ${w.total.text}">
      <div class="qf-between"><h3 class="qf-h3">Score ${w.total.text}${w.bias ? html` <span class="qf-mute">· ${w.bias}</span>` : ''}</h3>
        <span class="qf-cap">${w.rejection ? html`<span class="qf-amber">${w.rejection}</span>` : 'its parts, in order'}</span></div>
      ${w.rows.map((r) => html`
        <div class="in-fall-row" title="${r.signals.join(' · ')}">
          <span>${r.name}</span>
          <span class="in-fall-track"><i class="in-fall-zero" style="left:${w.zero}%"></i><i class="in-fall-bar" data-tone="${r.tone}" style="left:${r.left}%;width:${r.width}%"></i></span>
          <span data-tone="${r.tone}" class="qf-r">${r.text}</span>
        </div>`)}
      <div class="in-fall-row in-fall-total">
        <span>${w.total.name}</span>
        <span class="in-fall-track"><i class="in-fall-zero" style="left:${w.zero}%"></i><i class="in-fall-bar in-fall-sum" style="left:${w.total.left}%;width:${w.total.width}%"></i></span>
        <span class="qf-r">${w.total.text}</span>
      </div>
    </section>`;
}

export function stamps(list) {
  if (!list.length) return '';
  return html`<div class="in-stamps">${list.map((s) => html`<span class="qf-stamp">${s}</span>`)}</div>`;
}

export function paperPosition(p, now) {
  return html`
    <section class="in-pos" aria-label="Paper position">
      <div class="qf-between"><h3 class="qf-h3">Paper position</h3><a class="qf-pill qf-text" href="#performance">On Performance</a></div>
      <div class="qf-kv">
        <div><span class="qf-label">${p.side === 'SHORT' ? 'Short' : 'Long'}</span><b>${group(p.qty)}</b></div>
        <div><span class="qf-label">Entry</span><b>${px(p.entry_price)}</b></div>
        <div><span class="qf-label">Unrealised</span><b data-tone="${p.unrealized > 0 ? 'profit' : p.unrealized < 0 ? 'loss' : 'flat'}">${inr(p.unrealized)}</b></div>
        <div><span class="qf-label">Opened</span><b>${hm(p.ts)}</b></div>
        <div><span class="qf-label">Stop</span><b class="qf-down">${px(p.stop)}</b></div>
        <div><span class="qf-label">Target</span><b class="qf-up">${px(p.target)}</b></div>
        <div><span class="qf-label">Now</span><b>${fmtR(p.r_now)}</b></div>
        <div><span class="qf-label">Price age</span><b>${Number.isFinite(p.last_mark_ts) ? `${Math.max(0, Math.round(now - p.last_mark_ts))} s` : DASH}</b></div>
      </div>
      <p class="qf-cap">The paper engine manages this position; a near-stop warning waits for the real stop.</p>
    </section>`;
}

const field = (label, name, value, attrs = {}) => html`
  <label class="qf-form-row"><span class="qf-label">${label}</span>
    <input class="qf-field" name="${name}" data-key="in-f-${name}" value="${value ?? ''}" inputmode="${attrs.inputmode || 'decimal'}"
      ${attrs.placeholder ? raw(`placeholder="${attrs.placeholder}"`) : ''} autocomplete="off"></label>`;
const select = (label, name, value, options) => html`
  <label class="qf-form-row"><span class="qf-label">${label}</span>
    <select class="qf-field" name="${name}" data-key="in-f-${name}">${options.map(([v, t]) => html`<option value="${v}" ${v === value ? raw('selected') : ''}>${t}</option>`)}</select></label>`;

export function manualPosition({ manual, editing, error, logging, log, logError, busy }) {
  const m = manual || {};
  return html`
    <section class="in-pos" aria-label="Your position">
      <div class="qf-between"><h3 class="qf-h3">Your position</h3>
        <span class="qf-row">
          ${manual && !editing ? html`<button class="qf-pill qf-text" type="button" data-act="pos-edit">Edit</button>
            <button class="qf-pill qf-text" type="button" data-act="pos-clear">Clear</button>` : ''}
          ${!manual && !editing ? html`<button class="qf-pill" type="button" data-act="pos-edit">Record a position</button>` : ''}
          <button class="qf-pill qf-text" type="button" data-act="log-toggle" aria-expanded="${bool(logging)}">Log a trade</button>
        </span></div>
      ${manual && !editing ? html`
        <div class="qf-kv">
          <div><span class="qf-label">${m.direction}</span><b>${group(m.quantity)}</b></div>
          <div><span class="qf-label">Entry</span><b>${px(m.entry_price)}</b></div>
          <div><span class="qf-label">Stop</span><b class="qf-down">${px(m.stoploss)}</b></div>
          <div><span class="qf-label">Target</span><b class="qf-up">${px(m.target)}</b></div>
        </div>
        <p class="qf-cap">${m.mode} · held outside the paper engine; the AI manages it as yours.</p>` : ''}
      ${!manual && !editing ? html`<p class="qf-cap">Nothing held here. Record a position you took elsewhere so the AI manages it.</p>` : ''}
      ${editing ? html`
        <form class="in-form" data-form="pos">
          ${select('Direction', 'direction', m.direction || 'Long', [['Long', 'Long'], ['Short', 'Short']])}
          ${select('Mode', 'mode', m.mode || 'Intraday', [['Intraday', 'Intraday'], ['Delivery', 'Delivery / swing']])}
          ${field('Quantity', 'quantity', m.quantity, { inputmode: 'numeric' })}
          ${field('Entry price', 'entry_price', m.entry_price)}
          ${field('Stop (optional)', 'stoploss', m.stoploss)}
          ${field('Target (optional)', 'target', m.target)}
          <div class="in-form-foot">
            ${error ? html`<span class="qf-error" role="alert">${error}</span>` : ''}
            <button class="qf-pill" type="button" data-act="pos-cancel">Cancel</button>
            <button class="qf-pill qf-primary" type="submit" ${busy ? raw('disabled') : ''}>Save position</button>
          </div>
        </form>` : ''}
      ${logging ? html`
        <form class="in-form" data-form="log">
          ${select('What happened', 'kind', log.kind, [['open', 'I opened a trade'], ['close', 'I closed it'], ['hold', 'I’m holding']])}
          ${log.kind === 'open' ? select('Side', 'direction', log.direction, [['Long', 'Long'], ['Short', 'Short']]) : ''}
          ${log.kind !== 'hold' ? field('Price', 'price', log.price) : ''}
          ${log.kind !== 'hold' ? field('Quantity', 'qty', log.qty, { inputmode: 'numeric' }) : ''}
          ${log.kind === 'close' ? field('Charges ₹', 'charges', log.charges) : ''}
          ${log.kind !== 'close' ? field('Stop', 'stop', log.stop) : ''}
          ${log.kind !== 'close' ? field('Target', 'target', log.target) : ''}
          <label class="qf-form-row in-wide"><span class="qf-label">Why</span>
            <input class="qf-field" name="reason" data-key="in-f-reason" value="${log.reason || ''}" autocomplete="off"></label>
          <div class="in-form-foot">
            ${logError ? html`<span class="qf-error" role="alert">${logError}</span>` : ''}
            <span class="qf-cap">Goes to the trade journal${log.kind === 'open' ? ' and records the position' : log.kind === 'close' ? ' and clears the position' : ''}.</span>
            <button class="qf-pill qf-primary" type="submit" ${busy ? raw('disabled') : ''}>Log it</button>
          </div>
        </form>` : ''}
    </section>`;
}

// -------------------------------------------------------------------- ai
export function ai({ plan, models, model, auto, interval, busy, error, intentOpen, intent, copied }) {
  return html`
    <div class="in-ai-controls">
      <button class="qf-pill qf-primary" type="button" data-act="analyse" ${busy ? raw('disabled') : ''}>${busy ? 'Analysing…' : 'Analyse now'}</button>
      <label class="qf-row in-model"><span class="qf-label">Model</span>
        <select class="qf-field" data-act="model" data-key="in-model">${(models || [{ id: model, label: model }]).map((m) => html`<option value="${m.id}" ${m.id === model ? raw('selected') : ''}>${m.label}</option>`)}</select></label>
      <span class="qf-row">
        <button class="qf-switch" type="button" role="switch" data-act="auto" data-key="in-auto" aria-checked="${bool(auto)}" aria-label="Re-analyse automatically"></button>
        <span>Every</span>
        <input class="qf-field in-interval" data-act="interval" data-key="in-interval" value="${interval}" inputmode="numeric" aria-label="Seconds between analyses"> s
      </span>
    </div>
    ${error ? html`<p class="qf-error" role="alert">${error}</p>` : ''}
    ${!plan ? html`<p class="in-empty">No AI read yet. “Analyse now” asks the model with your master prompt.</p>` : plan.note ? html`<p class="in-note">${plan.note}</p>` : html`
      <div class="in-plan">
        <div class="qf-row">
          ${plan.action ? html`<span class="in-action" data-tone="${plan.actionTone}">${plan.action}</span>` : ''}
          <span class="qf-stamp" data-tone="${plan.stamp.tone}">${plan.stamp.text}</span>
          ${plan.unstable ? html`<span class="qf-cap qf-amber">still settling</span>` : ''}
          ${plan.time ? html`<span class="qf-cap">${plan.time}</span>` : ''}
        </div>
        ${plan.entry || plan.stop || plan.target ? html`
          <div class="qf-kv">
            <div><span class="qf-label">Entry</span><b>${px(plan.entry)}</b></div>
            <div><span class="qf-label">Stop</span><b class="qf-down">${px(plan.stop)}</b></div>
            <div><span class="qf-label">Target</span><b class="qf-up">${px(plan.target)}</b></div>
            <div><span class="qf-label">Reward : risk</span><b>${plan.rr ? `${num(plan.rr, 2)} : 1` : DASH}</b></div>
          </div>` : ''}
        ${plan.riskRejection ? html`<p class="qf-cap"><span class="qf-down">Risk layer: ${plan.riskRejection}.</span></p>`
          : plan.qty ? html`<p class="qf-cap">Sized at ${group(plan.qty)} shares, ${inr(plan.riskAmount, { signed: false })} at risk.</p>` : ''}
        ${plan.exitRule ? html`<p class="qf-cap">Exit rule: ${plan.exitRule}.</p>` : ''}
        ${plan.reason ? html`<p class="in-reason">${plan.reason}</p>` : ''}
        <p class="qf-cap">Edge: ${plan.edge === 'Unmeasured' ? 'not measured yet, so the AI is judged on reward : risk alone' : plan.edge}.</p>
      </div>`}
    <details class="in-intent" ${intentOpen ? raw('open') : ''} data-act="intent-toggle">
      <summary>Ask about a trade I’m considering</summary>
      <form class="in-form" data-form="intent">
        ${select('Action', 'action', intent.action, [['None', 'None'], ['Buy Market', 'Buy at market'], ['Buy Limit', 'Buy with a limit'], ['Sell Market', 'Sell at market'], ['Sell Limit', 'Sell with a limit']])}
        ${select('Type', 'type', intent.type, [['Intraday', 'Intraday'], ['Delivery', 'Delivery']])}
        ${field('Quantity', 'quantity', intent.quantity, { inputmode: 'numeric' })}
        ${field('Price', 'price', intent.price)}
        <label class="qf-form-row in-wide"><span class="qf-label">Note to the AI</span>
          <input class="qf-field" name="advice" data-key="in-f-advice" value="${intent.advice || ''}" placeholder="e.g. scaling in slowly on dips" autocomplete="off"></label>
        <div class="in-form-foot"><span class="qf-cap">Sent with “Analyse now”, and included when you copy.</span>
          <button class="qf-pill" type="button" data-act="copy-ai">${copied === 'ai' ? 'Copied' : 'Copy for another AI'}</button></div>
      </form>
    </details>`;
}

// ------------------------------------------------------------------ news
export function news({ items, busy, error, fetchedAt }) {
  return html`
    <div class="qf-between"><h3 class="qf-h3">News for this stock</h3>
      <button class="qf-pill" type="button" data-act="fetch-news" ${busy ? raw('disabled') : ''}>${icons.refresh}${busy ? 'Fetching…' : 'Fetch news'}</button></div>
    ${error ? html`<p class="qf-error" role="alert">${error}</p>` : ''}
    ${fetchedAt ? html`<p class="qf-cap">Fetched at ${hm(fetchedAt)}.</p>` : ''}
    ${!items.length ? html`<p class="in-empty">No news cached for this stock. “Fetch news” asks the news service now.</p>`
      : html`<ol class="in-news">${items.map((n) => html`<li><b>${n.headline}</b>${n.summary ? html`<p>${n.summary}</p>` : ''}</li>`)}</ol>`}`;
}

// ------------------------------------------------------------------- raw
export function rawView({ json, copied }) {
  return html`
    <div class="qf-between"><h3 class="qf-h3">What the AI sees</h3>
      <span class="qf-row">
        <button class="qf-pill" type="button" data-act="copy-json">${icons.copy}${copied === 'json' ? 'Copied' : 'Copy JSON'}</button>
        <button class="qf-pill" type="button" data-act="copy-ai">${copied === 'ai' ? 'Copied' : 'Copy for another AI'}</button>
      </span></div>
    ${json ? html`<pre class="in-json" tabindex="0">${json}</pre>` : html`<p class="in-empty">No live data for this stock.</p>`}`;
}
