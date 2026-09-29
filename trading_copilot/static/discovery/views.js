// Discovery's markup. Data in, html`` out; clicks carry data-act / data-pick /
// data-open and are handled in index.js.

import { bool, html, raw } from '../shared/dom.js';
import { DASH, group, num } from '../shared/format.js';
import { icons } from '../shared/icons.js';
import { rsBar, scanLine, scatterGeometry, stepper } from './model.js';

const pct = (v) => (typeof v === 'number' && Number.isFinite(v) ? `${v < 0 ? '−' : v > 0 ? '+' : ''}${Math.abs(v).toFixed(1)}%` : DASH);
const ALIGN = { SUPPORTS: ['News supports it', 'qf-up'], CONTRADICTS: ['News contradicts it', 'qf-amber'],
  NO_NEWS: ['No news', 'qf-mute'], MIXED: ['News is mixed', 'qf-mute'] };

export function head({ v, models, model, feedDown, runError, starting }) {
  const running = v && v.state === 'running';
  const picks = (v && v.picks) || [];
  const steps = stepper(v);
  const total = v && v.total;
  let title;
  if (feedDown) title = html`Scans run in the feed process, which isn’t running`;
  else if (!v || v.picks === null) title = running ? html`Scanning the F&amp;O universe…` : html`No scan this session`;
  else title = html`<span class="qf-fig">${picks.length}</span> picks from <span class="qf-fig">${total || DASH}</span> — the outliers on both sides`;
  return html`
    <div class="qf-between dc-head">
      <div class="qf-section dc-title">
        <h1 class="qf-statement qf-md">${title}</h1>
        ${steps ? html`<ol class="dc-steps">${steps.map((s) => html`<li data-state="${s.state}">${s.label}</li>`)}</ol>`
          : html`<span class="qf-cap">${scanLine(v) || (feedDown ? 'Start the system to scan.' : 'A scan scores every F&O stock and refreshes the watchlist; it takes a couple of minutes.')}</span>`}
      </div>
      <div class="qf-row dc-run">
        <select class="qf-field" data-act="model" data-key="dc-model" aria-label="AI model for the top 10">
          ${(models || [{ id: model, label: model || 'Default model' }]).map((m) => html`<option value="${m.id}" ${m.id === model ? raw('selected') : ''}>${m.label}</option>`)}
        </select>
        <button type="button" class="qf-pill qf-primary" data-act="run" ${running || starting || feedDown ? raw('disabled') : ''}>
          ${icons.play}${running ? 'Scanning…' : 'Run scan'}</button>
      </div>
    </div>
    ${runError ? html`<p class="qf-error" role="alert">${runError}</p>` : ''}`;
}

export function universe({ v, selected, width }) {
  const W = Math.max(300, Math.floor(width));
  const H = 396;
  const g = v && scatterGeometry({ universe: v.universe, picks: v.picks, cut: v.cut, selected }, { width: W, height: H });
  if (!g) return html`<section class="qf-section"><h3 class="qf-h3">Every F&amp;O stock, scored</h3><p class="qf-note">The chart fills in after a scan.</p></section>`;
  const longs = (v.picks || []).filter((p) => p.side === 'Long').length;
  return html`
    <section class="qf-section" aria-label="The universe">
      <div class="qf-between"><h3 class="qf-h3">${g.full ? html`Every F&amp;O stock, scored` : 'The picks, scored'}</h3>
        <span class="qf-cap">dot size = liquidity${g.full ? '' : ' · the whole universe shows after the next scan'}</span></div>
      <div class="qf-plot dc-scatter" style="width:${W}px;height:${H + 24}px" role="img"
        aria-label="${g.n} stocks by relative strength and score; ${v.picks.length} picks, ${longs} long and ${v.picks.length - longs} short.">
        <div class="qf-gridline" style="top:${g.y0}px;background:var(--rule-strong)"></div>
        <div class="dc-axis-y" style="left:${g.x0}px"></div>
        ${g.cutHi !== null ? html`
          <div class="dc-cut" style="top:${g.cutHi}px"></div><div class="dc-cut" style="top:${g.cutLo}px"></div>
          <span class="qf-axis" style="left:6px;top:${Number(g.cutHi) - 18}px">top-10 cut +${num(v.cut, 2)}</span>
          <span class="qf-axis" style="left:6px;top:${Number(g.cutLo) + 4}px">top-10 cut −${num(v.cut, 2)}</span>` : ''}
        <span class="qf-axis qf-up" style="right:8px;top:6px">Long: strong and rising</span>
        <span class="qf-axis qf-down" style="left:8px;top:${H - 20}px">Short: weak and falling</span>
        ${g.crowd.map((c) => html`<span class="qf-pt dc-crowd" style="left:${c.x}px;top:${c.y}px;width:${c.r}px;height:${c.r}px" title="${c.symbol}"></span>`)}
        ${g.picks.map((p) => html`
          <button type="button" class="qf-pt dc-pick" data-pick="${p.symbol}" data-tone="${p.tone}" aria-pressed="${bool(p.selected)}"
            style="left:${p.x}px;top:${p.y}px;width:${p.r}px;height:${p.r}px" aria-label="${p.symbol}, rank ${p.rank}"></button>
          ${p.label ? html`<span class="qf-ptlabel" data-tone="${p.tone}" style="left:${Number(p.x) + Number(p.r) / 2}px;top:${p.y}px">${p.symbol}</span>` : ''}`)}
        <span class="qf-axis" style="left:0;top:${H + 6}px">−${g.xr}%</span>
        <span class="qf-axis" style="left:${g.x0}px;top:${H + 6}px;transform:translateX(-50%)">relative strength vs NIFTY</span>
        <span class="qf-axis" style="right:0;top:${H + 6}px">+${g.xr}%</span>
      </div>
    </section>`;
}

export function read(p) {
  if (!p) return html`<section class="qf-section dc-read"><p class="qf-note">Pick a stock to read the AI’s view of it.</p></section>`;
  const ai = p.ai;
  const align = ai && ai.news_alignment ? ALIGN[ai.news_alignment] : null;
  return html`
    <section class="qf-section dc-read" aria-label="${p.symbol}">
      <div class="qf-between"><h3 class="qf-h3">${p.symbol}</h3><button type="button" class="qf-pill qf-text" data-open="${p.symbol}">Inspect</button></div>
      <div class="qf-row dc-read-fig"><span class="qf-kpi qf-s" data-tone="${p.side === 'Long' ? 'profit' : 'loss'}">${num((p.score || 0) / 100, 2)}</span>
        <span data-tone="${p.side === 'Long' ? 'profit' : 'loss'}" class="dc-side">${p.side}</span>
        <span class="qf-cap">rank ${p.rank} · RS ${pct(p.rs)} · ₹${group(p.adv_crore || 0, 0)} Cr a day</span></div>
      <div class="qf-hair dc-read-body">
        ${!ai ? html`<p class="qf-mute">The AI reads only the top 10.</p>`
          : ai.state === 'reading' ? html`<p class="qf-amber">The AI is reading this one…</p>`
            : ai.state === 'error' ? html`<p class="qf-amber">The AI read failed for this scan; the score stands.</p>`
              : ai.state === 'missing' ? html`<p class="qf-mute">The AI returned nothing for this stock.</p>` : html`
            ${ai.thesis ? html`<p><span class="qf-eyebrow">Thesis</span>${ai.thesis}</p>` : ''}
            ${ai.watch_for ? html`<p><span class="qf-eyebrow">Watch for</span>${ai.watch_for}</p>` : ''}
            ${ai.risks && ai.risks.length ? html`<p><span class="qf-eyebrow">${ai.risks.length > 1 ? 'Risks' : 'Risk'}</span>${ai.risks.join(' ')}</p>` : ''}
            ${align ? html`<p class="qf-cap"><span class="${align[1]}">${align[0]}</span></p>` : ''}`}
        ${p.news ? html`<p class="qf-cap dc-news">${p.news}${p.news_source === 'fetched' ? html` <span class="qf-mute">(fetched after scoring; not in the score)</span>` : ''}</p>` : ''}
      </div>
    </section>`;
}

export function sheet({ picks, selected, adding }) {
  if (!picks || !picks.length) return html``;
  const max = Math.max(...picks.map((p) => p.score || 0), 1);
  return html`
    <section class="qf-plate" aria-label="The picks">
      <div class="qf-scroll-x"><table class="qf-table dc-sheet">
        <thead><tr><th class="qf-r">#</th><th>Stock</th><th>Side</th><th>Score</th><th>Relative strength</th>
          <th class="qf-r">Liquidity ₹ Cr</th><th>News</th><th class="qf-r"></th></tr></thead>
        <tbody>${picks.map((p) => {
          const b = rsBar(p.rs);
          const tone = p.side === 'Long' ? 'profit' : 'loss';
          return html`
            <tr data-pick="${p.symbol}" tabindex="0" class="${p.symbol === selected ? 'qf-sel' : ''}" aria-selected="${bool(p.symbol === selected)}">
              <td class="qf-r qf-mute">${p.rank}</td>
              <td><span class="qf-sym">${p.symbol}</span></td>
              <td><span data-tone="${tone}">${p.side}</span></td>
              <td><span class="qf-row dc-cell"><span class="qf-bar-track dc-score"><i style="width:${(((p.score || 0) / max) * 100).toFixed(0)}%;background:var(--${tone === 'profit' ? 'jade' : 'coral'})"></i></span>${num((p.score || 0) / 100, 2)}</span></td>
              <td><span class="qf-row dc-cell">${b ? html`<span class="qf-tilt dc-rs"><i style="left:${b.left}%;width:${b.width}%;background:var(--${b.tone === 'profit' ? 'jade' : 'coral'})"></i></span>` : ''}<span data-tone="${tone}">${pct(p.rs)}</span></span></td>
              <td class="qf-r">${p.adv_crore !== null ? group(p.adv_crore, 0) : DASH}</td>
              <td class="qf-mute">${p.news ? (p.news_source === 'fetched' ? 'Fetched' : 'At scan') : DASH}</td>
              <td class="qf-r">${p.added_live ? html`<span class="qf-up">Added</span>` : p.on_watchlist ? html`<span class="qf-mute">On the board</span>`
                : html`<button type="button" class="qf-pill qf-text" data-act="add" data-symbol="${p.symbol}" ${adding === p.symbol ? raw('disabled') : ''}>${adding === p.symbol ? 'Adding…' : 'Add'}</button>`}</td>
            </tr>`;
        })}</tbody></table></div>
    </section>`;
}

export function footer({ notes, addMsg }) {
  return html`
    <div class="dc-foot">
      ${addMsg ? html`<p class="${addMsg.ok ? 'qf-up' : 'qf-error'}" role="status">${addMsg.text}</p>` : ''}
      ${(notes || []).map((n) => html`<p class="qf-note">${n}</p>`)}
      <p class="qf-cap">Scores, not predictions. New picks go live at once; dropped stocks stay monitored until the next restart.</p>
    </div>`;
}
