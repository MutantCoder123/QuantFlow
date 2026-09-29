// The Settings sheet's markup, one view per section.

import { bool, html, raw } from '../shared/dom.js';
import { hm } from '../shared/format.js';
import { icons } from '../shared/icons.js';
import { SECTIONS, diff } from './model.js';

export function frame(section) {
  return html`
    <div class="qf-scrim" data-act="close"></div>
    <aside class="qf-drawer st-drawer" role="dialog" aria-modal="true" aria-labelledby="st-title">
      <div class="qf-drawer-head st-head"><h2 id="st-title">Settings</h2>
        <button class="qf-iconbtn" type="button" data-act="close" data-key="st-close" aria-label="Close">${icons.close}</button></div>
      <div class="st-body">
        <nav class="st-nav" aria-label="Settings sections">
          ${SECTIONS.map((s) => html`<a href="#settings/${s.id}" data-section="${s.id}" ${s.id === section ? raw('aria-current="page"') : ''}>${s.label}</a>`)}
        </nav>
        <div class="st-panel" data-part="panel"></div>
      </div>
    </aside>`;
}

const msg = (m) => (m ? html`<p class="${m.ok ? 'qf-up' : 'qf-error'}" role="status">${m.text}</p>` : '');

export function watchlist({ saved, working, query, results, searching, busy, message, error }) {
  const d = diff(saved, working);
  const changed = d.added || d.removed;
  return html`
    <div class="st-sec-head"><h3 class="qf-h2">Watchlist</h3>
      <p class="qf-note">${working ? `${working.length} streaming live · Discovery refreshes it each morning` : 'Loading…'}</p></div>
    ${error ? html`<p class="qf-error">${error}</p>` : ''}
    <div class="st-search">
      <label class="qf-search">${icons.search}<input type="search" data-act="search" data-key="st-search" value="${query}"
        placeholder="Add a stock (e.g. SAIL, TCS)" aria-label="Search for a stock to add" autocomplete="off"></label>
      ${query.length >= 2 ? html`<div class="st-results" role="listbox">${searching ? html`<p class="qf-cap">Searching…</p>`
        : results.length ? results.map((r) => html`<button type="button" class="st-result" data-act="add" data-token="${r.token}" role="option">
            <b>${r.symbol}</b><span class="qf-cap">${r.exchange} · ${r.token}</span></button>`)
          : html`<p class="qf-cap">No match.</p>`}</div>` : ''}
    </div>
    ${working ? html`
      <div class="qf-plate st-list"><table class="qf-table">
        <thead><tr><th>Stock</th><th>Exchange</th><th class="qf-r"></th></tr></thead>
        <tbody>${working.map((w) => html`<tr><td><span class="qf-sym">${w.symbol}</span></td><td class="qf-mute">${w.exchange}</td>
          <td class="qf-r"><button type="button" class="qf-pill qf-text" data-act="remove" data-token="${w.token}">Remove</button></td></tr>`)}</tbody>
      </table></div>` : ''}
    <div class="st-foot">
      ${msg(message)}
      <span class="qf-cap">${changed ? `${d.added} to add, ${d.removed} to remove · additions go live without a restart; new stocks get their history synced` : 'No changes'}</span>
      <button type="button" class="qf-pill" data-act="revert" ${changed ? '' : raw('disabled')}>Undo changes</button>
      <button type="button" class="qf-pill qf-primary" data-act="save-watchlist" ${changed && !busy ? '' : raw('disabled')}>${busy ? 'Saving…' : 'Save watchlist'}</button>
    </div>`;
}

export function ai({ text, isDefault, confirmReset, message }) {
  return html`
    <div class="st-sec-head"><h3 class="qf-h2">AI analysis</h3>
      <p class="qf-note">The master prompt for “Analyse now”, auto-analysis and “Copy for another AI”. Saved in this browser.</p></div>
    <textarea class="qf-field qf-code st-prompt" data-act="prompt" data-key="st-prompt" spellcheck="false" aria-label="Master prompt">${text}</textarea>
    <div class="st-foot">
      ${msg(message)}
      <span class="qf-cap">${isDefault ? 'The default prompt' : 'Your edited prompt'}</span>
      ${confirmReset ? html`<span class="qf-amber">Replace your prompt with the default?</span>
          <button type="button" class="qf-pill" data-act="reset-cancel">Keep mine</button>
          <button type="button" class="qf-pill qf-danger" data-act="reset-confirm">Reset to default</button>`
        : html`<button type="button" class="qf-pill" data-act="reset" ${isDefault ? raw('disabled') : ''}>Reset to default</button>
          <button type="button" class="qf-pill qf-primary" data-act="save-prompt">Save prompt</button>`}
    </div>`;
}

export function news({ state, models, form, busy, message }) {
  const s = state || {};
  const reachable = state !== null && !state.error;
  return html`
    <div class="st-sec-head"><h3 class="qf-h2">News</h3>
      <p class="qf-note">${!state ? 'Loading…' : !reachable ? 'The news service isn’t running.'
        : s.is_active ? `Fetching every ${s.interval} s · last ${s.last_fetch_time ? hm(s.last_fetch_time) : 'not yet'}` : 'Auto fetch is off.'}</p></div>
    <div class="st-form">
      <label class="qf-row st-switch-row"><button type="button" class="qf-switch" role="switch" data-act="news-auto" data-key="st-news-auto"
        aria-checked="${bool(s.is_active)}" ${reachable && !busy ? '' : raw('disabled')} aria-label="Fetch news automatically"></button> Fetch news automatically</label>
      <label class="qf-form-row"><span class="qf-label">Every (seconds)</span>
        <input class="qf-field" data-act="news-interval" data-key="st-news-interval" value="${form.interval}" inputmode="numeric"></label>
      <label class="qf-form-row"><span class="qf-label">Model for the global read</span>
        <select class="qf-field" data-act="news-model" data-key="st-news-model">${(models || []).map((m) => html`<option value="${m.id}" ${m.id === form.model ? raw('selected') : ''}>${m.label}</option>`)}</select></label>
    </div>
    <div class="st-foot">
      ${msg(message)}
      <button type="button" class="qf-pill" data-act="news-now" ${reachable && !busy ? '' : raw('disabled')}>Fetch now</button>
      ${s.is_active ? html`<button type="button" class="qf-pill qf-primary" data-act="news-apply" ${reachable && !busy ? '' : raw('disabled')}>Apply interval and model</button>` : ''}
    </div>`;
}

export function data({ symbols, picked, busy, message, lastRun }) {
  const all = symbols.length && symbols.every((s) => picked.has(s));
  return html`
    <div class="st-sec-head"><h3 class="qf-h2">Data</h3>
      <p class="qf-note">End-of-day history: sync the daily bars and option history for these stocks now. The launcher also does this after the close.</p></div>
    <div class="qf-row"><button type="button" class="qf-pill qf-text" data-act="pick-all">${all ? 'Clear all' : 'Select all'}</button>
      <span class="qf-cap">${picked.size} of ${symbols.length} selected</span></div>
    <div class="st-checks">${symbols.map((s) => html`<label class="st-check"><input type="checkbox" data-act="pick" value="${s}" ${picked.has(s) ? raw('checked') : ''}> ${s}</label>`)}</div>
    <div class="st-foot">
      ${msg(message)}
      ${lastRun ? html`<span class="qf-cap">Started ${hm(lastRun)}; it runs in the background (see the terminal log).</span>` : ''}
      <button type="button" class="qf-pill qf-primary" data-act="sync" ${picked.size && !busy ? '' : raw('disabled')}>${busy ? 'Starting…' : 'Sync now'}</button>
    </div>`;
}

export function paper() {
  return html`
    <div class="st-sec-head"><h3 class="qf-h2">Paper account</h3>
      <p class="qf-note">Capital, risk per trade, the daily loss limit and the position caps live with the paper account, on Performance.</p></div>
    <div><a class="qf-pill" href="#performance" data-act="to-performance">Open Performance</a></div>
    <p class="qf-cap">There, “Settings” opens the paper account’s sheet. Its changes apply to the next trade.</p>`;
}
