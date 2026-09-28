// "Why": the questions the operator asks on the left, the selected one's
// answer on the right -- one chart, a finding written from the data, and
// the config keys it points to. On phones the list becomes a native select.

import { html, render } from '../../core/dom.js';
import { r } from '../../core/format.js';
import { whenChanged } from '../../core/store.js';
import { dot, f1, hline, linear, vline } from '../../charts/svg.js';
import { QUESTIONS, answer } from './questions.js';

const KEY = 'qf.performance.question';

function savedQuestion() {
  try { return localStorage.getItem(KEY) || 'stops'; } catch { return 'stops'; }
}

function barsView(bars) {
  return html`<div class="pf-bars">${bars.map((b) => html`
    <div class="pf-bar-row${b.small ? ' pf-small' : ''}">
      <span class="pf-bar-label">${b.label}</span>
      <span class="pf-bar-track" aria-hidden="true"><span class="pf-bar-zero"></span>
        <span class="pf-bar" data-tone="${b.small ? 'amber' : b.negative ? 'loss' : 'profit'}"
          style="${b.negative ? `right:50%` : 'left:50%'};width:${b.share.toFixed(1)}%"></span></span>
      <span class="pf-bar-value" data-tone="${b.small ? 'amber' : b.negative ? 'loss' : 'profit'}">${b.text}
        ${b.small ? '' : html`<span class="pf-mute">· ${b.n}</span>`}</span>
    </div>`)}</div>`;
}

/** Pure: MAE/MFE points -> scatter geometry. */
export function scatterGeometry(points, w, h) {
  const pts = points.filter((p) => typeof p.mae_r === 'number' && typeof p.mfe_r === 'number');
  const xMin = Math.min(-1.3, ...pts.map((p) => p.mae_r)) - 0.05;
  const yMax = Math.max(2.2, ...pts.map((p) => p.mfe_r)) + 0.05;
  const X = linear(xMin, 0.05, 0, w);
  const Y = linear(0, yMax, h, 0);
  const ticksX = [-1.2, -0.8, -0.4, 0].filter((v) => v >= xMin);
  const ticksY = [0.5, 1, 1.5, 2].filter((v) => v <= yMax);
  return {
    win: pts.filter((p) => p.win).map((p) => dot(X(p.mae_r), Y(Math.max(0, p.mfe_r)))).join(''),
    loss: pts.filter((p) => !p.win).map((p) => dot(X(p.mae_r), Y(Math.max(0, p.mfe_r)))).join(''),
    grid: ticksY.map((v) => hline(Y(v), 0, w)).join(''),
    stop: vline(X(-1), 0, h), half: hline(Y(0.5), 0, w),
    xl: ticksX.map((v) => ({ t: r(v).replace(' R', ''), x: X(v) })),
    yl: ticksY.map((v) => ({ t: r(v).replace(' R', ''), y: Y(v) })),
    stopX: X(-1), halfY: Y(0.5), n: pts.length,
  };
}

function scatterView(points, width) {
  const w = Math.max(260, width - 60);
  const h = width < 500 ? 200 : 300;
  const g = scatterGeometry(points, w, h);
  return html`
    <div class="pf-scatter" style="width:${f1(w)}px;height:${f1(h + 40)}px">
      <svg width="${f1(w)}" height="${f1(h)}" role="img" aria-label="${`Worst point against best point for ${g.n} trades`}">
        <path d="${g.grid}" class="pf-grid"></path>
        <path d="${g.half}" class="pf-sc-half"></path>
        <path d="${g.stop}" class="pf-sc-stop"></path>
        <path d="${g.loss}" class="pf-sc-loss"></path>
        <path d="${g.win}" class="pf-sc-win"></path>
      </svg>
      ${g.xl.map((l) => html`<span class="pf-axis" style="left:${f1(l.x)}px;top:${f1(h + 6)}px;transform:translateX(-50%)">${l.t}</span>`)}
      ${g.yl.map((l) => html`<span class="pf-axis pf-sc-y" style="top:${f1(l.y - 20)}px">${l.t}</span>`)}
      <span class="pf-axis pf-loss" style="left:${f1(g.stopX + 6)}px;top:4px">Stop at −1.00 R</span>
      <span class="pf-axis" style="right:0;top:${f1(g.halfY - 20)}px">Up +0.50 R</span>
      <span class="pf-axis" style="right:0;bottom:0">Worst point in trade (MAE), R</span>
      <span class="pf-axis pf-sc-ytitle">Best point in trade (MFE), R</span>
    </div>
    <div class="pf-legend pf-meta"><span><i class="pf-key-dot" data-tone="profit"></i>Winner</span><span><i class="pf-key-dot" data-tone="loss"></i>Loser</span></div>`;
}

export function mount(el, store) {
  let selected = savedQuestion();
  const pick = (id, focus = false) => {
    selected = id;
    try { localStorage.setItem(KEY, id); } catch { /* per-viewer convenience */ }
    draw(store.get());
    if (focus) el.querySelector(`[data-key="q-${id}"]`)?.focus();
  };
  el.addEventListener('click', (e) => {
    const b = e.target.closest('[data-q]');
    if (b) pick(b.dataset.q);
  });
  el.addEventListener('change', (e) => { if (e.target.dataset.qselect !== undefined) pick(e.target.value); });
  el.addEventListener('keydown', (e) => {               // arrow keys move through the question list
    const b = e.target.closest('[data-q]');
    if (!b || !['ArrowDown', 'ArrowUp'].includes(e.key)) return;
    e.preventDefault();
    const i = QUESTIONS.findIndex((q) => q[0] === b.dataset.q);
    const next = QUESTIONS[(i + (e.key === 'ArrowDown' ? 1 : QUESTIONS.length - 1)) % QUESTIONS.length][0];
    pick(next, true);
  });

  function draw(state) {
    const a = answer(selected, state);
    const width = el.clientWidth ? el.clientWidth - (el.clientWidth < 640 ? 34 : 410) : 800;
    const err = state.diagnosticsError || state.breakdownsError;
    render(el, html`
      <section class="pf-why" aria-label="Why">
        <div class="pf-sec-head pf-wrap">
          <h2 class="pf-h2">Why</h2>
          <span class="pf-grow"></span>
          <span class="pf-meta">Evidence report for this range:
            <a href="/api/paper/report?range=${encodeURIComponent(state.range)}&fmt=md" target="_blank" rel="noopener" data-key="report-view">view</a> ·
            <a href="/api/paper/report?range=${encodeURIComponent(state.range)}&fmt=md&download=1" download data-key="report-dl">download</a></span>
        </div>
        <label class="pf-qselect"><span class="pf-meta">Question</span>
          <select data-qselect data-key="qselect">${QUESTIONS.map(([id, q]) => html`<option value="${id}" ${id === selected ? 'selected' : ''}>${q}</option>`)}</select></label>
        <div class="pf-why-grid">
          <div class="pf-qlist" role="tablist" aria-label="Questions" aria-orientation="vertical">
            ${QUESTIONS.map(([id, q]) => html`<button type="button" role="tab" data-q="${id}" data-key="q-${id}"
                aria-selected="${id === selected}" tabindex="${id === selected ? '0' : '-1'}"><span>${q}</span>
                <span class="pf-indigo" aria-hidden="true">${id === selected ? '→' : ''}</span></button>`)}
          </div>
          <div class="pf-plate pf-answer" role="tabpanel">
            <h3 class="pf-h3">${a.question}</h3>
            ${err ? html`<p class="pf-error">Couldn’t load the diagnostics: ${err}</p>` : ''}
            ${a.kind === 'loading' && !err ? html`<p class="pf-meta">Loading…</p>` : ''}
            ${a.kind === 'notYet' ? html`<div class="pf-notyet">${a.text}</div>` : ''}
            ${a.kind === 'bars' ? barsView(a.bars) : ''}
            ${a.kind === 'scatter' ? scatterView(a.points, width) : ''}
            ${a.finding ? html`<p class="pf-finding">${a.finding}</p>` : ''}
            ${a.tunes && a.tunes.length ? html`<div class="pf-tunes"><span class="pf-meta">Tunes</span>
              ${a.tunes.map((k) => html`<code>${k}</code>`)}</div>` : ''}
          </div>
        </div>
      </section>`);
  }

  const update = whenChanged((s) => [s.metrics, s.breakdowns, s.diagnostics, s.diagnosticsError, s.breakdownsError, s.range], draw);
  return { update, destroy() { el.textContent = ''; } };
}
