// The app bar: wordmark, the five tabs, the market clock and feed health in
// words, alerts, and Settings. Rendered once; the clock, health and the
// current tab are updated in place so the 1 s clock never redraws the tray.
// When every price has stopped, a coral line under the bar says so (the
// Performance engine-down pattern) -- the old amber banner's replacement.

import { html, render } from '../shared/dom.js';
import { duration } from '../shared/format.js';
import { icons } from '../shared/icons.js';
import { TABS, hashForSettings, hashForTab } from './router.js';
import { feedHealth, marketClock, marketLive } from './status.js';
import * as alertsC from './alerts.js';

export function mount(el, store, actions, { banner, settings = true } = {}) {
  render(el, html`
    <a class="qf-wordmark" href="${hashForTab('market')}" aria-label="QuantFlow, Market"><i aria-hidden="true"></i>QuantFlow</a>
    <nav class="qf-nav" aria-label="Main">
      ${TABS.map((t) => html`<a href="${hashForTab(t.id)}" data-tab="${t.id}">${t.label}</a>`)}
    </nav>
    <div class="qf-bar-right">
      <span class="qf-clock" data-part="clock"></span>
      <span class="qf-health" data-part="health" role="status"></span>
      <span data-part="alerts"></span>
      ${settings ? html`<a class="qf-iconbtn qf-gear" href="${hashForSettings()}" aria-label="Settings">${icons.gear}</a>` : ''}
    </div>`);
  const clockEl = el.querySelector('[data-part="clock"]');
  const healthEl = el.querySelector('[data-part="health"]');
  const alerts = alertsC.mount(el.querySelector('[data-part="alerts"]'), store, actions);
  const links = [...el.querySelectorAll('[data-tab]')];

  let lastClock = '';
  let lastHealth = '';
  let lastTab = null;
  let lastBanner = '';

  function update(s, changed = []) {
    const open = marketLive(s.live);
    const clock = marketClock(s.now, open);
    const clockText = `${clock.time} · ${clock.words}`;
    if (clockText !== lastClock) {
      lastClock = clockText;
      render(clockEl, html`<b>${clock.time}</b> · ${clock.words}`);
    }
    const staleAfter = (s.live && s.live.paper && s.live.paper.stale_price_seconds) || 15;
    const health = feedHealth(s.live, { connected: s.connected !== false, staleAfter });
    const healthKey = `${health.tone}|${health.words}`;
    if (healthKey !== lastHealth) {
      lastHealth = healthKey;
      render(healthEl, html`<span class="qf-dot" data-tone="${health.tone}" aria-hidden="true"></span>${health.words}`);
    }
    if (s.tab !== lastTab) {
      lastTab = s.tab;
      links.forEach((a) => {
        if (a.dataset.tab === s.tab) a.setAttribute('aria-current', 'page');
        else a.removeAttribute('aria-current');
      });
    }
    if (banner) {
      const text = health.stopped && clock.open
        ? `Prices stopped updating${Number.isFinite(health.age) ? ` ${duration(health.age)} ago` : ''}. The engine does not act on stale prices.`
        : '';
      if (text !== lastBanner) {
        lastBanner = text;
        banner.hidden = !text;
        render(banner, text ? html`<span class="qf-dot" aria-hidden="true"></span><span>${text}</span>` : html``);
      }
    }
    alerts.update(s, changed);
  }
  update(store.get());
  return { update, destroy() { alerts.destroy(); } };
}

/** The phone tab bar: the same five tabs, icon over label. */
export function mountBottom(el, store) {
  render(el, html`${TABS.map((t) => html`
    <a href="${hashForTab(t.id)}" data-tab="${t.id}"><span>${icons[t.id]}</span>${t.label}</a>`)}`);
  const links = [...el.querySelectorAll('[data-tab]')];
  let last = null;
  const update = (s) => {
    if (s.tab === last) return;
    last = s.tab;
    links.forEach((a) => {
      if (a.dataset.tab === s.tab) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
  };
  update(store.get());
  return { update, destroy() {} };
}
