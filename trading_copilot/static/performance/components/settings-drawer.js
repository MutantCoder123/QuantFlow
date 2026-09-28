// Settings drawer: the paper account's editable settings, grouped by what
// they protect. Each field says where its value comes from. The server
// validates (the rules live in paper/settings.py); this form only parses
// numbers and shows the server's words. Changes apply to new positions.

import { html, render } from '../core/dom.js';
import { inr, trim } from '../core/format.js';
import { SETTING } from '../core/labels.js';

export const FIELDS = [
  { key: 'capital', group: 'money', unit: '₹', prefix: true },
  { key: 'slippage_pct', group: 'money', unit: '%' },
  { key: 'risk_per_trade_pct', group: 'risk', unit: '% of capital' },
  { key: 'max_daily_loss_pct', group: 'risk', unit: '% of capital' },
  { key: 'max_cluster_risk_pct', group: 'risk', unit: '% of capital' },
];
const GROUPS = [
  ['money', 'Money', 'What the account holds and pays to trade'],
  ['risk', 'Risk', 'How much one trade, one day, or one sector can lose'],
];

const show = (key, v) => (typeof v !== 'number' ? '' : key === 'capital' ? String(Math.round(v)) : trim(v, 3));
const defaultText = (key, v) => (key === 'capital' ? inr(v, { signed: false }) : `${trim(v, 3)}%`);

/**
 * Pure: form strings -> {changes, errors}. `resets` are fields sent back to
 * their default (null). Unchanged fields are not sent.
 */
export function parseForm(values, data, resets = new Set()) {
  const changes = {};
  const errors = {};
  for (const { key } of FIELDS) {
    if (resets.has(key)) {
      if (data.sources[key] === 'override') changes[key] = null;
      continue;
    }
    const raw = String(values[key] ?? '').replace(/[,₹%\s]/g, '');
    const n = raw === '' ? NaN : Number(raw);
    if (!Number.isFinite(n)) { errors[key] = 'must be a number'; continue; }
    if (Math.abs(n - data.settings[key]) > 1e-9) changes[key] = n;
  }
  return { changes, errors };
}

export const errorText = (key, rule) => `${SETTING[key] || key} ${rule}`;

export function mount(el, store, actions) {
  let data = null;           // GET /api/paper/settings body
  let loadError = null;
  let errors = {};
  let message = '';
  let busy = false;
  const resets = new Set();
  let opener = null;
  let wasOpen = false;

  const values = () => Object.fromEntries(FIELDS.map(({ key }) => [key, el.querySelector(`[name="${key}"]`)?.value]));

  async function load() {
    const res = await actions.api.settings();
    data = res.ok ? res.data : null;
    loadError = res.ok ? null : res.error;
    draw();
    el.querySelector('input')?.focus();
  }

  async function save() {
    if (!data || busy) return;
    const current = values();
    const { changes, errors: local } = parseForm(current, data, resets);
    errors = local;
    if (Object.keys(errors).length) { message = ''; draw(current); return; }
    if (!Object.keys(changes).length) { message = 'Nothing has changed.'; draw(current); return; }
    busy = true;
    draw(current);
    const res = await actions.api.saveSettings(changes);
    busy = false;
    if (res.ok) {
      data = res.data;
      resets.clear();
      errors = {};
      message = 'Settings saved';
      draw();
      actions.refresh();                 // the change shows on the tape as a marker
    } else {
      errors = (res.data && res.data.errors) || {};
      message = Object.keys(errors).length ? '' : `Couldn’t save: ${res.error}`;
      draw(current);
    }
  }

  function close() {
    actions.closeSettings();
    if (opener && opener.isConnected) opener.focus();
    else document.querySelector('#tab-performance [data-key="settings"]')?.focus();
  }

  el.addEventListener('click', (e) => {
    if (e.target.closest('[data-act="close"]') || e.target.classList.contains('pf-scrim')) close();
    const reset = e.target.closest('[data-reset]');
    if (reset) {
      e.preventDefault();
      const key = reset.dataset.reset;
      resets.add(key);
      const current = values();
      current[key] = show(key, data.defaults[key]);
      delete errors[key];
      message = '';
      draw(current);
    }
    if (e.target.closest('[data-act="save"]')) save();
  });
  el.addEventListener('submit', (e) => { e.preventDefault(); save(); });
  el.addEventListener('input', (e) => { if (e.target.name) resets.delete(e.target.name); });
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    if (e.key === 'Tab') {                          // keep focus inside the open dialog
      const f = [...el.querySelectorAll('button, input, a[href]')].filter((x) => !x.disabled);
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    }
  });

  function field(fd, current) {
    const key = fd.key;
    const src = data.sources[key];
    const pendingReset = resets.has(key);
    const changed = src === 'override' && !pendingReset;
    const err = errors[key];
    const v = current && current[key] !== undefined ? current[key] : show(key, data.settings[key]);
    return html`<label class="pf-field">
      <span class="pf-field-label">${SETTING[key]}</span>
      <span class="pf-input${err ? ' pf-invalid' : ''}">
        ${fd.prefix ? html`<span class="pf-mute">${fd.unit}</span>` : ''}
        <input name="${key}" inputmode="decimal" autocomplete="off" value="${v}" aria-invalid="${err ? 'true' : 'false'}"
          ${err ? html`aria-describedby="pf-err-${key}"` : ''}>
        ${fd.prefix ? '' : html`<span class="pf-mute">${fd.unit}</span>`}
      </span>
      ${err ? html`<span class="pf-error" id="pf-err-${key}">${errorText(key, err)}</span>` : ''}
      <span class="pf-source">${pendingReset ? html`<span class="pf-indigo">will use the default (${defaultText(key, data.defaults[key])})</span>`
        : changed ? html`<span class="pf-indigo">changed here</span><a href="#" data-reset="${key}">use default (${defaultText(key, data.defaults[key])})</a>`
          : html`<span class="pf-mute">from ${src}</span>`}</span>
    </label>`;
  }

  function draw(current) {
    const open = store.get().drawer;
    if (!open) { el.textContent = ''; return; }
    const blocking = Object.keys(errors);
    const status = busy ? 'Saving…' : blocking.length ? `Fix ${blocking.map((k) => (SETTING[k] || k).toLowerCase()).join(' and ')} to save`
      : message;
    render(el, html`
      <div class="pf-scrim"></div>
      <aside class="pf-drawer" role="dialog" aria-modal="true" aria-labelledby="pf-drawer-title">
        <div class="pf-drawer-head">
          <h2 class="pf-h2 pf-drawer-title" id="pf-drawer-title">Settings</h2>
          <button type="button" class="pf-close" data-act="close" aria-label="Close settings">×</button>
        </div>
        <form class="pf-drawer-body" novalidate>
          ${!data ? html`<p class="${loadError ? 'pf-error' : 'pf-meta'}">${loadError || 'Loading…'}</p>`
            : GROUPS.map(([g, title, blurb]) => html`<fieldset class="pf-group"><legend>
                <span class="pf-group-title">${title}</span><span class="pf-meta">${blurb}</span></legend>
                ${FIELDS.filter((f) => f.group === g).map((f) => field(f, current))}</fieldset>`)}
          <button type="submit" hidden></button>
        </form>
        <div class="pf-drawer-foot">
          <p class="pf-meta">Changes apply to new positions. Open positions keep the settings they opened with.</p>
          <div class="pf-save-row">
            <button type="button" class="pf-primary" data-act="save" ${!data || busy ? 'disabled' : ''}>Save settings</button>
            <span class="${blocking.length ? 'pf-error' : message === 'Settings saved' ? 'pf-profit' : 'pf-meta'}" role="status">${status}</span>
          </div>
        </div>
      </aside>`);
  }

  function update(state) {
    if (state.drawer && !wasOpen) {
      wasOpen = true;
      opener = document.activeElement;
      errors = {}; message = ''; resets.clear(); data = null; loadError = null;
      draw();
      load();
    } else if (!state.drawer && wasOpen) {
      wasOpen = false;
      el.textContent = '';
    }
  }
  return { update, destroy() { el.textContent = ''; } };
}
