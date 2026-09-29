// The app shell's pure parts: routes, the market clock and feed health in
// words, and the alert tray's lines.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { hashForInspect, hashForTab, parseHash, tabFromOld } from '../../trading_copilot/static/shell/router.js';
import { feedHealth, marketClock, marketLive, openByClock, span } from '../../trading_copilot/static/shell/status.js';
import { alertLine, badgeText } from '../../trading_copilot/static/shell/alerts.js';
import { bool } from '../../trading_copilot/static/shared/dom.js';

// Tuesday 29 Sep 2026, IST = UTC + 5:30
const ist = (h, m) => Date.UTC(2026, 8, 29, h, m) / 1000 - 19800;
const saturday = (h, m) => Date.UTC(2026, 9, 3, h, m) / 1000 - 19800;     // 3 Oct 2026

test('routes: tabs, the Inspector, Settings, and the old page hashes', () => {
  assert.deepEqual(parseHash('#signals'), { kind: 'tab', tab: 'signals' });
  assert.deepEqual(parseHash(''), { kind: 'tab', tab: 'market' });
  assert.deepEqual(parseHash('#nonsense'), { kind: 'tab', tab: 'market' });
  assert.deepEqual(parseHash('#inspect/sail'), { kind: 'inspect', symbol: 'SAIL' });
  assert.deepEqual(parseHash('#inspect/'), { kind: 'tab', tab: 'market' });
  assert.deepEqual(parseHash('#settings'), { kind: 'settings', section: null });
  assert.deepEqual(parseHash('#settings/news'), { kind: 'settings', section: 'news' });
  assert.deepEqual(parseHash('#dashboard'), { kind: 'tab', tab: 'market' });
  assert.deepEqual(parseHash('#live-action'), { kind: 'tab', tab: 'signals' });
  assert.deepEqual(parseHash('#screener'), { kind: 'tab', tab: 'discovery' });
  assert.equal(hashForInspect('m&m'), '#inspect/M%26M');
  assert.deepEqual(parseHash(hashForInspect('M&M')), { kind: 'inspect', symbol: 'M&M' });
  assert.equal(hashForTab('bogus'), '#market');
  assert.equal(tabFromOld('live-action'), 'signals');
  assert.equal(tabFromOld('performance'), 'performance');
});

test('the market clock reads the session in words', () => {
  assert.deepEqual(marketClock(ist(11, 42)), { time: '11:42', open: true, words: 'closes in 3 h 48 m' });
  assert.equal(marketClock(ist(8, 58)).words, 'opens in 17 min');
  assert.equal(marketClock(ist(15, 30)).words, 'closed');
  assert.equal(marketClock(ist(16, 5)).words, 'closed');
  assert.equal(marketClock(ist(15, 29)).words, 'closes in 1 min');
  assert.equal(span(0.5), 'under a minute');
  // Saturday 3 Oct: closed all day
  assert.equal(marketClock(saturday(10, 0)).words, 'closed');
  assert.equal(openByClock(saturday(10, 0)), false);
  // the socket wins over the clock (a holiday)
  assert.equal(marketClock(ist(11, 42), false).words, 'closed');
  assert.equal(span(60), '1 h');
});

test('market live is known only once the socket has said', () => {
  assert.equal(marketLive(null), undefined);
  assert.equal(marketLive({ global_state: {} }), undefined);
  assert.equal(marketLive({ global_state: { SAIL: { market_state: 'CLOSED' } } }), false);
  assert.equal(marketLive({ global_state: { SAIL: { market_state: 'LIVE' } } }), true);
});

test('feed health: live, some stale, all stopped, reconnecting, idle', () => {
  const s = (ages, state = 'LIVE') => ({ global_state: Object.fromEntries(ages.map((a, i) => [`S${i}`, { market_state: state, data_age_s: a }])) });
  assert.equal(feedHealth(s([1, 2, 0.5])).words, 'Live');
  assert.equal(feedHealth(s([1, 2, 0.5])).tone, 'ok');
  assert.deepEqual([feedHealth(s([1, 20, 30])).tone, feedHealth(s([1, 20, 30])).words], ['amber', 'Live · 2 stale']);
  const stopped = feedHealth(s([40, 60, 90]));
  assert.equal(stopped.tone, 'down');
  assert.equal(stopped.stopped, true);
  assert.equal(stopped.age, 60);
  assert.equal(feedHealth(s([20]), { staleAfter: 30 }).words, 'Live');
  assert.equal(feedHealth(s([1]), { connected: false }).words, 'Reconnecting');
  assert.equal(feedHealth(null).words, 'Connecting');
  assert.equal(feedHealth(s([1], 'CLOSED')).words, 'Idle');
  assert.equal(feedHealth({ global_state: {} }).words, 'Waiting for prices');
});

test('an alert reads as the verdict and the action, in words', () => {
  const line = alertLine({ id: 27, timestamp: ist(12, 16), symbol: 'NSE_EQ|NBCC-EQ', verdict: 'ADJUST',
    action: 'REVERSE_POSITION', rationale: 'Flow turned.', read: false });
  assert.deepEqual(line, { id: 27, symbol: 'NBCC', time: '12:16', words: 'Adjust · Reverse the position',
    rationale: 'Flow turned.', unread: true });
  assert.equal(alertLine({ verdict: 'ABORT', action: 'PASS', read: true }).words, 'Reject · Pass');
  assert.equal(badgeText(0), '');
  assert.equal(badgeText(7), '7');
  assert.equal(badgeText(140), '99+');
});

test('bool writes the ARIA word html`` would otherwise drop', () => {
  assert.equal(bool(false), 'false');
  assert.equal(bool(1), 'true');
});
