import { test } from 'node:test';
import assert from 'node:assert/strict';
import { statement } from '../../trading_copilot/static/performance/components/statement.js';
import { engineStatus } from '../../trading_copilot/static/performance/components/status-line.js';

const NOW = Date.UTC(2026, 8, 28, 8, 36, 12) / 1000;        // 14:06:12 IST
const f = (value, n = 48, min_n = 0) => ({ value, n, min_n, status: 'ok' });
const text = (s) => s.segs.map((g) => g.t).join('');

function metrics({ n, net, costs = 3978, ret = 1.248, dd = -8900, name = '1m', start = 1000000 }) {
  return {
    range: { name, first_day: '2026-08-29', last_day: '2026-09-28', session_days: 20 },
    min_n: { rates: 20 },
    headline: { trades: f(n, n), net_pnl: f(net, n), costs: f(costs, n), return_pct: f(ret, n),
      max_drawdown: f(dd, n), start_equity: f(start, n) },
  };
}
const live = (open = 3, unrealized = 1431) => ({ running: true, engine_ok: true, enabled: true, unrealized,
  open: Array.from({ length: open }, () => ({ last_mark_ts: NOW - 2 })) });

test('a busy month reads as one plain sentence', () => {
  const s = statement(metrics({ n: 48, net: 12480 }), live(), NOW);
  assert.equal(text(s), 'Up ₹12,480 over the last 30 days on 48 trades. That’s 1.25% of ₹10,00,000. '
    + 'The deepest dip was ₹8,900, and 3 trades are open now.');
  assert.deepEqual(s.segs.filter((g) => g.fig).map((g) => g.tone), ['profit', 'plain', 'profit', 'loss', 'plain']);
  assert.equal(s.sub, 'Net of ₹3,978 costs. Open positions add +₹1,431 unrealised.');
  assert.equal(s.meta, '29 Aug to 28 Sep · updated 14:06:12 IST');
});

test('a losing range says Down, in coral', () => {
  const s = statement(metrics({ n: 30, net: -5200, ret: -0.52, name: 'all' }), live(0), NOW);
  assert.ok(text(s).startsWith('Down ₹5,200 since paper trading began on 30 trades.'));
  assert.ok(text(s).endsWith(', and nothing is open now.'));
  assert.equal(s.segs.find((g) => g.fig).tone, 'loss');
});

test('below the minimum it refuses to call a rate', () => {
  const s = statement(metrics({ n: 4, net: 1840, costs: 310, name: 'today' }), live(1, 798), NOW);
  assert.equal(text(s), '4 trades closed today. Too few to call a win rate; the first reading comes at 20.');
  assert.equal(s.segs.at(-2).tone, 'amber');
  assert.equal(s.sub, 'Net +₹1,840 so far, after ₹310 costs. 1 trade is open, +₹798 unrealised.');
  assert.equal(s.meta, 'Today, Mon 28 Sep · updated 14:06:12 IST');
});

test('no trades at all: the empty state directs the next step', () => {
  const s = statement(metrics({ n: 0, net: 0, costs: 0, name: 'all' }), live(0), NOW);
  assert.equal(text(s), 'No paper trades yet. They’ll appear here when a signal passes every check '
    + 'during market hours (09:15–13:45 for new entries).');
  assert.equal(s.sub, 'Starting capital ₹10,00,000. Nothing is open.');
});

test('without the engine it never claims an open count', () => {
  const s = statement(metrics({ n: 48, net: 12480 }), { running: false, engine_ok: false }, NOW);
  assert.ok(text(s).endsWith('The deepest dip was ₹8,900.'));
  assert.ok(!/open/.test(text(s)));
  assert.equal(s.sub, 'Net of ₹3,978 costs.');
});

test('a frozen engine freezes the meta time', () => {
  const s = statement(metrics({ n: 48, net: 12480 }), { running: true, engine_ok: false, failed_at: NOW - 8644, open: [] }, NOW);
  assert.match(s.meta, /frozen at 11:42:08 IST$/);
});

test('no drawdown yet is said plainly', () => {
  const s = statement(metrics({ n: 25, net: 900, dd: 0 }), live(0), NOW);
  assert.match(text(s), /It hasn’t fallen below a previous high, and nothing is open now\.$/);
});

test('engine status in words', () => {
  assert.equal(engineStatus(null, null, NOW).text, 'Waiting for the dashboard connection');
  assert.deepEqual(engineStatus(live(3), NOW, NOW), { tone: 'ok', text: 'Engine running, prices 2 s old' });
  assert.equal(engineStatus(live(0), NOW, NOW).text, 'Engine running, nothing open');
  assert.equal(engineStatus({ ...live(1), open: [{ last_mark_ts: NOW - 40 }] }, NOW, NOW).tone, 'warn');
  assert.equal(engineStatus({ ...live(0), enabled: false }, NOW, NOW).tone, 'warn');
  assert.match(engineStatus({ running: false }, NOW, NOW).text, /not started/);
  assert.match(engineStatus(live(0), NOW - 40, NOW).text, /No update from the web process for 40 s/);
  const down = engineStatus({ running: true, engine_ok: false, failed_at: NOW - 8644,
    last_error: 'event log not writable: disk full' }, NOW, NOW);
  assert.equal(down.tone, 'down');
  assert.equal(down.text, 'Engine stopped at 11:42');
  assert.equal(down.banner.what, 'The paper engine stopped at 11:42 (event log not writable: disk full).');
  assert.equal(down.banner.fix, 'Live figures are frozen at that time. Check disk space, then restart the web process.');
});
