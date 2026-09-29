// Review's markup. Data in, html`` out; the range control carries data-act.

import { bool, html, raw } from '../shared/dom.js';
import { isoDay } from '../shared/format.js';
import { countLine, headline, histGeometry, reliabilityGeometry, trendGeometry } from './model.js';

const pctText = (v) => (typeof v === 'number' ? `${Math.round(v)}%` : '—');

export function head({ r, range, date, today, session }) {
  const h = headline(r);
  return html`
    <div class="qf-between rv-head">
      <div class="qf-section rv-title">
        <h1 class="qf-statement qf-md">${h.text}${h.figs ? html` — <span class="qf-fig">${pctText(h.figs[0])}</span> → <span class="qf-fig" data-tone="${h.up ? 'profit' : 'loss'}">${pctText(h.figs[1])}</span>` : ''}</h1>
        <span class="qf-cap">${countLine(r)}${session ? ` · ${session}` : ''}</span>
      </div>
      <div class="qf-row rv-range">
        <div class="qf-seg" role="radiogroup" aria-label="Range">
          <button type="button" role="radio" data-act="range" data-range="sessions" aria-checked="${bool(range === 'sessions')}">20 sessions</button>
          <button type="button" role="radio" data-act="range" data-range="today" aria-checked="${bool(range === 'today')}">Today</button>
          <button type="button" role="radio" data-act="range" data-range="date" aria-checked="${bool(range === 'date')}">Pick a date</button>
        </div>
        ${range === 'date' ? html`<input type="date" class="qf-field" data-act="date" data-key="rv-date" value="${date || ''}" max="${today}" aria-label="Session date">` : ''}
      </div>
    </div>`;
}

export function reliabilityView(r, width) {
  const W = Math.max(280, Math.floor(width) - 48);
  const g = reliabilityGeometry(r && r.reliability, { width: W });
  const rel = (r && r.reliability) || {};
  const H = g.height;
  const buckets = (rel.buckets || []).filter((b) => b.n);
  const label = buckets.length
    ? `Hit rate by score: ${buckets.map((b) => `${b.lo.toFixed(1)}–${b.hi.toFixed(1)} ${b.hit_rate !== null ? `${Math.round(b.hit_rate)}%` : 'too few'} (${b.n})`).join('; ')}.`
    : 'No resolved signals in this range.';
  return html`
    <section class="qf-section" aria-label="Is the score honest?">
      <div class="qf-between"><h3 class="qf-h3">Is the score honest?</h3>
        <span class="qf-legend"><span><i class="qf-swatch rv-band-key"></i>95% range</span><span><i class="rv-coin-key"></i>coin flip</span></span></div>
      <div class="qf-plot rv-rel" style="width:${W}px;height:${H + g.strip + 58}px" role="img" aria-label="${label}">
        ${g.grid.map((l) => html`<div class="qf-gridline" style="top:${l.y}px"></div><span class="qf-axis rv-yl" style="top:${l.y}px">${l.label}</span>`)}
        <svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">
          ${g.diag ? raw(`<line x1="${g.diag.x1}" y1="${g.diag.y1}" x2="${g.diag.x2}" y2="${g.diag.y2}" class="rv-diag"/>`) : ''}
          ${raw(`<line x1="0" x2="${W}" y1="${g.coinY}" y2="${g.coinY}" class="rv-coin"/>`)}
          ${g.band ? raw(`<path d="${g.band}" class="rv-band"/>`) : ''}
          ${g.curve ? raw(`<path d="${g.curve}" class="rv-curve"/>`) : ''}
        </svg>
        ${g.diag ? html`<span class="qf-axis rv-diag-label" style="left:${Number(g.diag.x2) - 150}px;top:${Number(g.diag.y2) + 18}px">if score = probability</span>` : ''}
        ${g.points.map((p) => html`<span class="qf-pt rv-pt" data-good="${bool(p.good)}" style="left:${p.x}px;top:${p.y}px"></span>
          <span class="qf-axis rv-pt-label" data-good="${bool(p.good)}" style="left:${p.x}px;top:${Number(p.y) - 26}px">${pctText(p.rate)}</span>`)}
        ${!g.points.length ? html`<span class="qf-axis rv-none" style="top:${Number(g.coinY) - 30}px">Every bucket has fewer than ${rel.min_n || 10} signals: no hit rate to draw yet.</span>` : ''}
        <div class="rv-strip-line" style="top:${H + 12}px"></div>
        ${g.counts.map((c) => c.n ? html`<span class="rv-count" data-thin="${bool(c.thin)}" style="left:${c.x}px;top:${H + 12 + g.strip - Number(c.h)}px;height:${c.h}px"></span>
          <span class="qf-axis rv-count-n" data-thin="${bool(c.thin)}" style="left:${c.x}px;top:${H + 12 + g.strip - Number(c.h) - 16}px">${c.n}</span>` : '')}
        <span class="qf-axis rv-yl" style="top:${H + 12 + g.strip / 2}px">signals</span>
        ${g.xticks.map((t) => html`<span class="qf-axis rv-xt" style="left:${t.x}px;top:${H + g.strip + 18}px">${t.label}</span>`)}
        <span class="qf-axis rv-xtitle" style="top:${H + g.strip + 36}px">score (|composite|) · amber = under ${rel.min_n || 10} signals</span>
      </div>
    </section>`;
}

export function trendView(r, width) {
  const W = Math.max(280, Math.floor(width) - 48);
  const g = trendGeometry(r && r.trend, { width: W });
  if (!g) return html`<section class="qf-section"><h3 class="qf-h3">Is it getting better?</h3><p class="qf-note">No sessions with resolved signals yet.</p></section>`;
  const days = r.trend;
  return html`
    <section class="qf-section" aria-label="Is it getting better?">
      <div class="qf-between"><h3 class="qf-h3">Is it getting better?</h3>
        <span class="qf-legend"><span><i class="rv-line-key"></i>5-session average</span><span><i class="rv-thin-key"></i>under 15 signals</span></span></div>
      <div class="qf-plot rv-trend" style="width:${W}px;height:${g.height + g.barH + 40}px" role="img"
        aria-label="Daily hit rate over ${days.length} ${days.length === 1 ? 'session' : 'sessions'}; the 5-session average ends at ${pctText(days[days.length - 1].rolling)}.">
        ${g.grid.map((l) => html`<div class="qf-gridline" style="top:${l.y}px;${l.base ? 'background:var(--rule-strong)' : ''}"></div><span class="qf-axis rv-yl" style="top:${l.y}px">${l.label}</span>`)}
        <svg width="${W}" height="${g.height}" viewBox="0 0 ${W} ${g.height}" aria-hidden="true">${g.line ? raw(`<path d="${g.line}" class="rv-line"/>`) : ''}</svg>
        ${g.dots.map((d) => d.y !== null ? html`<span class="qf-pt rv-dot" data-thin="${bool(d.thin)}" style="left:${d.x}px;top:${d.y}px" title="${isoDay(d.date)}: ${pctText(d.rate)} of ${d.n}"></span>` : '')}
        ${g.bars.map((b) => html`<span class="rv-nbar" style="left:${b.x}px;top:${g.height + 20 + g.barH - Number(b.h)}px;height:${b.h}px" title="${b.n} signals"></span>`)}
        <span class="qf-axis" style="left:0;top:${g.height + g.barH + 24}px">${isoDay(days[0].date)}</span>
        <span class="qf-axis" style="right:0;top:${g.height + g.barH + 24}px">${isoDay(days[days.length - 1].date)}</span>
        <span class="qf-axis rv-mid" style="top:${g.height + g.barH + 24}px">signals per session</span>
      </div>
    </section>`;
}

export function histView(r, width) {
  const W = Math.max(280, Math.floor(width) - 48);
  const h = r && r.histogram;
  const g = histGeometry(h, { width: W });
  if (!g) return html`<section class="qf-section"><h3 class="qf-h3">The move 90 min later</h3><p class="qf-note">No resolved signals yet.</p></section>`;
  return html`
    <section class="qf-section" aria-label="What happens after a signal">
      <div class="qf-between"><h3 class="qf-h3">The move 90 min later</h3><span class="qf-cap">in the signal’s direction, before costs</span></div>
      <div class="qf-plot rv-hist" style="width:${W}px;height:${g.height + 24}px" role="img"
        aria-label="Moves 90 minutes after a signal: ${g.rightPct}% land on the right side; median ${h.median > 0 ? '+' : ''}${h.median}%.">
        ${g.bars.map((b) => html`<span class="rv-hbar" data-right="${bool(b.right)}" style="left:${b.x}px;width:${b.w}px;height:${b.h}px;bottom:24px" title="${b.n}"></span>`)}
        <div class="rv-zero" style="left:${g.zero}px"></div>
        ${g.median !== null ? html`<div class="rv-median" style="left:${g.median}px"></div>
          <span class="qf-axis qf-paper" style="left:${Number(g.median) + 6}px;top:0">median ${h.median > 0 ? '+' : ''}${h.median}%</span>` : ''}
        <span class="qf-axis" style="left:0;bottom:0">${h.lo}%</span>
        <span class="qf-axis" style="left:${g.zero}px;bottom:0;transform:translateX(-50%)">0</span>
        <span class="qf-axis" style="right:0;bottom:0">+${h.hi}%</span>
      </div>
      <div class="qf-row rv-sides">
        <span><span class="qf-kpi qf-s qf-up">${g.rightPct}%</span> <span class="qf-cap">right side</span></span>
        <span><span class="qf-kpi qf-s qf-down">${g.wrongPct}%</span> <span class="qf-cap">wrong side</span></span>
      </div>
    </section>`;
}

export function groupsView(r) {
  const panels = (r && r.groups) || [];
  return html`
    <section class="qf-section" aria-label="Where it works">
      <div class="qf-between"><h3 class="qf-h3">Where it works</h3><span class="qf-cap">hit rate · the tick is 50% · <span class="qf-amber">amber</span> = under ${(r && r.min_n) || 10}</span></div>
      ${!r || !r.n ? html`<p class="qf-note">No resolved signals yet.</p>` : html`
        <div class="rv-panels">${panels.map((p) => html`
          <div class="rv-panel"><span class="qf-eyebrow">${p.title}</span>
            ${p.rows.map((row) => html`
              <div class="rv-grow">
                <span>${row.label}</span>
                <span class="rv-gtrack"><i style="width:${row.hit_rate || 0}%" data-tone="${row.thin ? 'amber' : (row.hit_rate || 0) >= 50 ? 'profit' : 'loss'}"></i><b></b></span>
                <span class="${row.thin ? 'qf-amber' : ''} qf-r">${pctText(row.hit_rate)} · ${row.n}</span>
              </div>`)}
          </div>`)}</div>`}
    </section>`;
}
