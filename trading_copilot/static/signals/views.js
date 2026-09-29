// The Signals tab's markup. Data in, html`` out; clicks carry data-act /
// data-open and are handled in index.js.

import { bool, html, raw } from '../shared/dom.js';
import { DASH, group, hm, inr, num } from '../shared/format.js';
import { directiveLabel, rejectionLabel, sentenceCase, verdictLabel } from '../shared/labels.js';
import { inFilter, laneGeometry, pipelineWord, rail, sectorBars, stageStrip } from './model.js';

const px = (v) => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? group(v, v >= 1000 ? 1 : 2) : DASH);
const STAGE_NAMES = ['math', 'risk', 'AI', 'paper'];

export function statement({ funnel, auto, interval, calls, busy, error }) {
  const strip = stageStrip(funnel);
  return html`
    <section class="qf-section sg-where" aria-label="Where ideas stop today">
      <div class="qf-between sg-where-head">
        <h1 class="qf-statement qf-md">Where ideas stop today</h1>
        <div class="qf-row sg-auto">
          <button class="qf-switch" type="button" role="switch" data-act="auto" data-key="sg-auto" aria-checked="${bool(auto)}"
            aria-label="AI reviews the top five" ${busy ? raw('disabled') : ''}></button>
          <span>AI reviews the top 5 every</span>
          <input class="qf-field sg-interval" data-act="interval" data-key="sg-interval" value="${interval}" inputmode="numeric"
            aria-label="Seconds between reviews"><span>s</span>
          <span class="qf-mute">· ${calls} AI calls</span>
        </div>
      </div>
      ${error ? html`<p class="qf-error" role="alert">${error}</p>` : ''}
      ${strip ? html`<div class="sg-strip">${strip.map((s) => html`
          <div class="sg-stage">
            <span class="qf-eyebrow">${s.label}</span>
            <span class="qf-kpi qf-s" data-tone="${s.tone}">${group(s.n)}</span>
            <div class="qf-bar-track sg-stage-bar"><i data-tone="${s.tone}" style="width:${(s.share * 100).toFixed(1)}%"></i></div>
            <span class="qf-cap">${s.why || DASH}</span>
          </div>`)}</div>`
        : html`<p class="qf-note">Reading today’s logs…</p>`}
    </section>`;
}

const MARK = { taken: 'AI confirmed a trade', rejected: 'AI rejected', deferred: 'AI deferred', other: 'AI passed or managed a position' };

export function timelineView({ lanes, width, now }) {
  const g = laneGeometry(lanes, { width: Math.max(300, width), labelW: width < 500 ? 96 : 130 });
  return html`
    <section class="qf-section" aria-label="Today, stock by stock">
      <div class="qf-between"><h3 class="qf-h3">Today, stock by stock</h3>
        <span class="qf-legend">
          <span><i class="sg-dot" data-kind="taken"></i>AI confirmed</span>
          <span><i class="sg-dot" data-kind="rejected"></i>rejected</span>
          <span><i class="sg-dot" data-kind="deferred"></i>deferred</span>
          <span><i class="sg-dot" data-kind="other"></i>passed</span>
          <span><i class="sg-hold-key"></i>paper position</span>
        </span></div>
      ${!g.lanes.length ? html`<p class="qf-note">The AI hasn’t reviewed an idea yet today.</p>` : html`
        <div class="qf-plot sg-lanes" style="width:100%;height:${g.height + 20}px">
          ${g.hours.map((h) => html`<div class="sg-hour" style="left:${h.x}px"></div><span class="qf-axis sg-hour-label" style="left:${h.x}px;top:${g.height + 2}px">${h.t}</span>`)}
          <div class="sg-now" style="left:${g.nowX(now)}px"></div>
          ${g.lanes.map((l) => html`
            <button type="button" class="qf-sym sg-lane-name" data-open="${l.symbol}" style="top:${l.y}px">${l.symbol}</button>
            <div class="sg-lane-line" style="top:${l.y}px"></div>
            ${l.holds.map((h) => html`<span class="sg-hold" data-kind="${h.kind}" style="left:${h.x}px;width:${h.w}px;top:${l.y + 9}px"
              title="${h.kind === 'open' ? 'Open now' : h.kind === 'win' ? 'Closed with a profit' : 'Closed with a loss'}"></span>`)}
            ${l.marks.map((m) => html`<span class="qf-pt sg-dot" data-kind="${m.kind}" style="left:${m.x}px;top:${l.y}px" title="${MARK[m.kind]}"></span>`)}`)}
        </div>
        ${g.more ? html`<p class="qf-cap">${g.more} more stocks had a review today.</p>` : ''}`}
    </section>`;
}

export const FILTERS = [{ id: 'all', label: 'All' }, { id: 'action', label: 'Wants action' }, { id: 'blocked', label: 'Blocked' }];

export function queueHead({ rows, filter }) {
  const count = (f) => rows.filter((r) => inFilter(r, f)).length;
  return html`
    <div class="qf-row sg-queue-left"><h2 class="qf-h2">The queue</h2>
      <div class="qf-row" role="group" aria-label="Show">
        ${FILTERS.map((f) => html`<button type="button" class="qf-chip" data-act="filter" data-filter="${f.id}" data-key="sg-f-${f.id}"
          aria-pressed="${bool(filter === f.id)}">${f.label} <b>${count(f.id)}</b></button>`)}
      </div></div>
    <span class="qf-cap">by attention</span>`;
}

export function queueBody({ rows, filter, open, prices, dismissed, now }) {
  const shown = rows.filter((r) => inFilter(r, filter) && !(dismissed[r.symbol] && now - dismissed[r.symbol] < 60));
  if (!shown.length) {
    return html`<tr><td colspan="7" class="qf-empty">${rows.length ? 'Nothing here right now.'
      : 'Nothing wants action. The engine checks every stock each 10 s; the AI reviews the top 5.'}</td></tr>`;
  }
  return html`${shown.map((r, i) => {
    const word = pipelineWord(r.pipeline);
    const src = r.paper || r;
    const rl = rail(src.stop, src.target, src.entry, (r.paper && r.paper.last) || prices[r.symbol]);
    const isOpen = open === r.symbol;
    const side = r.bias || (r.paper && r.paper.side);
    return html`
      <tr data-row="${r.symbol}" tabindex="0" aria-expanded="${bool(isOpen)}" class="${isOpen ? 'qf-sel' : ''}">
        <td class="qf-r qf-mute">${i + 1}</td>
        <td><span class="qf-sym">${r.symbol}</span></td>
        <td>${side ? html`<span data-tone="${side === 'LONG' ? 'profit' : 'loss'}">${sentenceCase(side)}</span>` : ''}
          ${r.composite !== null ? html` <span class="qf-mute">${num(Math.abs(r.composite), 2)}</span>` : ''}
          ${r.regime ? html`<small class="sg-regime">${sentenceCase(r.regime)}</small>` : ''}</td>
        <td><span class="qf-row sg-pipe">
          <span class="qf-pdots" aria-label="${r.pipeline.map((s, k) => `${STAGE_NAMES[k]}: ${s.text || 'nothing yet'}`).join(', ')}">
            ${r.pipeline.map((s, k) => html`${k ? html`<b></b>` : ''}<i data-tone="${s.tone}" title="${STAGE_NAMES[k]}: ${s.text || '—'}"></i>`)}</span>
          <span class="${word.tone === 'ok' ? 'qf-up' : word.tone === 'bad' ? 'qf-down' : word.tone === 'wait' ? 'qf-amber' : 'qf-mute'}">${word.text}</span>
        </span></td>
        <td>${rl ? html`<div class="sg-rail" title="stop ${px(src.stop)} → target ${px(src.target)}">
            ${rl.entry !== null ? html`<span class="sg-rail-e" style="left:${rl.entry}%"></span>` : ''}
            ${rl.price !== null ? html`<span class="sg-rail-p" style="left:${rl.price}%"></span>` : ''}</div>` : html`<span class="qf-mute">${DASH}</span>`}</td>
        <td class="qf-r">${r.rr ? num(r.rr, 1) : DASH}</td>
        <td class="qf-r qf-mute">${r.time || (r.paper ? hm(r.paper.opened) : DASH)}</td>
      </tr>
      ${isOpen ? html`
        <tr class="qf-sel sg-detail"><td></td><td colspan="6">
          <div class="sg-detail-body">
            <div class="sg-detail-text">
              ${r.reason ? html`<p class="sg-reason">${r.reason}</p>` : html`<p class="qf-mute">No AI reason yet for this idea.</p>`}
              <p class="qf-cap">${r.pipeline.map((s, k) => `${sentenceCase(STAGE_NAMES[k])}: ${s.text ? (s.tone === 'bad' ? rejectionLabel(s.text) : s.text) : 'nothing yet'}`).join(' · ')}
                ${src.entry ? ` · entry ${px(src.entry)}, stop ${px(src.stop)}, target ${px(src.target)}` : ''}</p>
            </div>
            <div class="qf-row sg-detail-acts">
              <button type="button" class="qf-pill qf-primary" data-act="log" data-symbol="${r.symbol}">Log as manual trade</button>
              <button type="button" class="qf-pill" data-act="dismiss" data-symbol="${r.symbol}">Dismiss for a minute</button>
              <button type="button" class="qf-pill qf-text" data-open="${r.symbol}">Inspect</button>
            </div>
          </div>
        </td></tr>` : ''}`;
  })}`;
}

export function sectorRisk(exposure, error) {
  const bars = sectorBars(exposure);
  const d = exposure || {};
  const limit = bars.length ? bars[0].limit : null;
  const unsized = d.unsizeable_positions || [];
  return html`
    <section class="qf-section sg-risk" aria-label="Risk by sector">
      <div class="qf-between"><h3 class="qf-h3">Risk by sector</h3><span class="qf-cap">${limit ? `limit ${inr(limit, { signed: false })} each` : ''}</span></div>
      ${error ? html`<p class="qf-error">${error}</p>` : ''}
      ${bars.length ? bars.map((b) => html`
        <div class="sg-risk-row">
          <div class="qf-between"><span>${b.name}</span><span class="${b.hot ? 'qf-down' : 'qf-mute'}">${inr(b.risk, { signed: false })}</span></div>
          <div class="qf-bar-track"><i style="width:${(b.share * 100).toFixed(1)}%;background:${b.hot ? 'var(--coral)' : 'var(--indigo-text)'}"></i></div>
        </div>`) : !error ? html`<p class="qf-note">No open risk${unsized.length ? '' : ': fully within limits'}.</p>` : ''}
      ${unsized.length ? html`<p class="qf-cap qf-amber">Risk unknown for ${unsized.join(', ')}: no quantity, entry or stop recorded.</p>` : ''}
    </section>`;
}

export function decisions(alerts) {
  const list = (alerts || []).slice(0, 12);
  return html`
    <section class="qf-section" aria-label="Decisions today">
      <div class="qf-between"><h3 class="qf-h3">Decisions today</h3><span class="qf-cap">acted on</span></div>
      ${list.length ? html`<div class="sg-log">${list.map((a) => html`
        <button type="button" class="sg-log-row" data-open="${String(a.symbol).split('|').pop().split('-')[0]}">
          <span class="qf-mute">${hm(Number(a.timestamp))}</span>
          <span class="qf-sym">${String(a.symbol).split('|').pop().split('-')[0]}</span>
          <span>${verdictLabel(a.verdict)} · ${directiveLabel(a.action)}</span>
          <span class="qf-mute sg-log-why">${a.rationale || ''}</span>
        </button>`)}</div>` : html`<p class="qf-note">No decisions yet today.</p>`}
    </section>`;
}
