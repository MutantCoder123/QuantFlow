// Status line: what this account is, whether the engine is healthy, the
// range, and Pause. Engine health is said in words; a stopped engine also
// gets the page's only boxed alert, saying what is frozen and what to do.

import { html, render } from '../core/dom.js';
import { duration, hm } from '../core/format.js';

export const RANGES = [
  ['today', 'Today'], ['5d', '5 days'], ['1m', '30 days'], ['all', 'All'],
];
const SOCKET_STALE_S = 15;

// -> {tone: 'ok'|'warn'|'down'|'mute', text, banner?}
export function engineStatus(live, liveAt, now) {
  if (!live) return { tone: 'mute', text: 'Waiting for the dashboard connection' };
  if (liveAt && now - liveAt > SOCKET_STALE_S) {
    return { tone: 'warn', text: `No update from the web process for ${duration(now - liveAt)}` };
  }
  if (live.running === false) {
    return { tone: 'mute', text: 'Engine not started. It starts with the decision loop.' };
  }
  if (live.engine_ok === false) {
    const at = live.failed_at ? hm(live.failed_at) : null;
    const why = live.last_error ? ` (${live.last_error})` : '';
    const fix = /not writable/.test(live.last_error || '')
      ? 'Check disk space, then restart the web process.'
      : 'Restart the web process; if it stops again, check its log.';
    return {
      tone: 'down',
      text: at ? `Engine stopped at ${at}` : 'Engine stopped',
      banner: {
        what: `The paper engine stopped${at ? ` at ${at}` : ''}${why}.`,
        fix: `Live figures are frozen${at ? ' at that time' : ''}. ${fix}`,
      },
    };
  }
  if (live.enabled === false) {
    return { tone: 'warn', text: 'Paused: no new positions; open ones are still managed' };
  }
  const marks = (live.open || []).map((p) => p.last_mark_ts).filter((t) => typeof t === 'number');
  if (!(live.open || []).length) return { tone: 'ok', text: 'Engine running, nothing open' };
  if (!marks.length) return { tone: 'warn', text: 'Engine running, no price yet for open positions' };
  // the page clock ticks once a second, so a fresh mark can be a hair "in the future"
  const oldest = Math.max(0, now - Math.min(...marks));
  const age = oldest < 1 ? 'under 1 s' : duration(oldest);
  return { tone: oldest > 15 ? 'warn' : 'ok', text: `Engine running, prices ${age} old` };
}

export function mount(el, store, actions) {
  el.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-range], button[data-act]');
    if (!b) return;
    if (b.dataset.range) actions.setRange(b.dataset.range);
    if (b.dataset.act === 'pause') actions.pause();
    if (b.dataset.act === 'resume') actions.resume();
    if (b.dataset.act === 'settings') actions.openSettings(b);
  });

  function update(state) {
    const s = engineStatus(state.live, state.liveAt, state.now);
    const paused = state.live && state.live.enabled === false;
    const canToggle = state.live && state.live.running !== false && state.live.engine_ok !== false;
    render(el, html`
      <header class="pf-status">
        <h1 class="pf-title">Performance</h1>
        <span class="pf-meta pf-paper"><span class="pf-ring" aria-hidden="true"></span>Paper account — simulated fills, no real orders</span>
        <span class="pf-meta pf-engine" data-tone="${s.tone}"><span class="pf-dot" aria-hidden="true"></span>${s.text}</span>
        <span class="pf-grow"></span>
        <div class="pf-seg" role="radiogroup" aria-label="Range">
          ${RANGES.map(([k, label]) => html`<button type="button" role="radio" data-range="${k}" data-key="range-${k}"
              aria-checked="${state.range === k ? 'true' : 'false'}">${label}</button>`)}
        </div>
        <div class="pf-actions">
          <button type="button" class="pf-pill" data-act="settings" data-key="settings" aria-haspopup="dialog">Settings</button>
          ${canToggle ? html`<button type="button" class="pf-pill" data-act="${paused ? 'resume' : 'pause'}" data-key="toggle"
              >${paused ? 'Resume' : 'Pause'}</button>` : ''}
        </div>
      </header>
      ${state.actionError ? html`<p class="pf-inline-error" role="alert">${state.actionError}</p>` : ''}
      ${s.banner ? html`<div class="pf-banner" role="alert"><span class="pf-dot" aria-hidden="true"></span>
          <p><span class="pf-banner-what">${s.banner.what}</span> ${s.banner.fix}</p></div>` : ''}
    `);
  }
  return { update, destroy() { el.textContent = ''; } };
}
