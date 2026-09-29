import { test } from 'node:test';
import assert from 'node:assert/strict';
import { tapeGeometry, timeAxis } from '../../trading_copilot/static/performance/components/tape.js';
import { figures } from '../../trading_copilot/static/performance/components/figures-line.js';
import { blotterFrame, blotterRow } from '../../trading_copilot/static/performance/components/blotter.js';
import { excursionBar, exitWhy, filterOptions, filterTrades, journalRow, story } from '../../trading_copilot/static/performance/components/journal.js';
import { errorText, parseForm } from '../../trading_copilot/static/performance/components/settings-drawer.js';
import { whenChanged } from '../../trading_copilot/static/performance/core/store.js';
import { MINUS, inr, trim } from '../../trading_copilot/static/performance/core/format.js';
import { settingValue } from '../../trading_copilot/static/performance/core/labels.js';

// IST wall time -> epoch seconds
const at = (day, h, m) => Date.UTC(2026, 8, day, h, m) / 1000 - 19800;
const ok = (value, n, min_n = 20) => ({ value, n, min_n, status: 'ok' });
const short = (n, min_n = 20) => ({ value: null, n, min_n, status: 'insufficient' });

// ------------------------------------------------------------------- tape
test('the time axis runs over trading hours only', () => {
  const { x, nowX } = timeAxis(['2026-09-25', '2026-09-28'], at(28, 12, 0));
  assert.equal(x(at(25, 9, 15)), 0);
  assert.equal(x(at(25, 15, 30)), 0.5);                 // Friday's close ...
  assert.equal(x(at(28, 9, 15)), 0.5);                  // ... meets Monday's open: no weekend gap
  assert.equal(x(at(28, 20, 0)), 1);                    // after hours pins to the close
  assert.ok(Math.abs(nowX - (1 + 165 / 375) / 2) < 1e-9);
  assert.equal(timeAxis(['2026-09-25'], at(28, 12, 0)).nowX, 1);   // now outside the range: the end
});

const equity = {
  range: { name: 'all' }, session_days: ['2026-09-21', '2026-09-22'], start_equity: 100000,
  realised: [
    { ts: at(21, 9, 15), pnl: 0, gross: 0, equity: 100000, dd: 0, dd_pct: 0 },
    { ts: at(21, 10, 0), pnl: 1000, gross: 1100, equity: 101000, dd: 0, dd_pct: 0 },
    { ts: at(21, 10, 15), pnl: 500, gross: 650, equity: 100500, dd: -500, dd_pct: -0.495 },
  ],
  ticks: [
    { ts: at(21, 10, 0), pos_id: 'P1', symbol: 'SAIL', side: 'LONG', net: 1000, r_net: 2.0, reason: 'TARGET' },
    { ts: at(21, 10, 15), pos_id: 'P2', symbol: 'IDEA', side: 'SHORT', net: -500, r_net: -1.0, reason: 'STOP' },
  ],
  settings_changes: [{ ts: at(22, 8, 0), old: { risk_per_trade_pct: 0.6 }, new: { risk_per_trade_pct: 0.5 } }],
};
const SIZE = { W: 1000, eqH: 210, tkH: 72, ddH: 64 };

test('tape geometry: ticks by R, capped at 2 R; drawdown underwater; settings labelled', () => {
  const g = tapeGeometry(equity, SIZE, at(22, 12, 0));
  assert.equal(g.empty, false);
  const [up, down] = g.ticks;
  assert.equal(up.up, true);
  assert.equal(down.up, false);
  const mid = 210 + 16 + 36;
  assert.equal(mid - up.y1, 34);                        // 2 R = the full half-band (36 - 2)
  assert.equal(down.y2 - mid, 17);                      // 1 R = half of it
  assert.match(up.title, /SAIL Long, Target, \+₹1,000 \(\+2\.00 R\)/);
  assert.equal(g.dip, -500);
  assert.match(g.paths.ddArea, /Z$/);
  assert.equal(g.dipLabel.t, `deepest ${MINUS}₹500`);
  assert.equal(g.sets[0].t, 'Risk per trade 0.6% → 0.5%');
  assert.deepEqual(g.xLabels.map((l) => l[0]), ['21 Sep', '22 Sep']);
  assert.ok(g.yLabels.some((l) => l.t === '₹1,00,000'));
});

test('an empty tape is a flat baseline at the starting capital', () => {
  const g = tapeGeometry({ ...equity, realised: [], ticks: [], settings_changes: [], session_days: [] }, SIZE, at(22, 12, 0));
  assert.equal(g.empty, true);
  assert.equal(g.paths.net, '');
  assert.ok(g.paths.base.startsWith('M0 '));
  assert.equal(g.intraday, true);
  assert.equal(g.xLabels[0][0], '09:15');
});

// ---------------------------------------------------------------- figures
test('figures carry their n, and go amber below the minimum', () => {
  const m = { headline: { win_rate: ok(41.66, 48), profit_factor: ok(1.137, 48), expectancy_r: ok(0.05, 48),
    costs: ok(3978, 48), cost_drag_pct: ok(24.2, 48) }, risk_adjusted: { sharpe: short(20) } };
  const f = figures(m);
  assert.deepEqual(f.map((x) => [x.label, x.value, x.n, x.amber]), [
    ['Win rate', '41.7%', '48 trades', false],
    ['Profit factor', '1.14', '48 trades', false],
    ['Average trade', '+0.05 R', '48 trades', false],
    ['Costs', '₹3,978', '24.2% of gross winnings, 48 trades', false],
    ['Sharpe:', 'not yet', '(20 of 20)', true],
  ]);
});

test('a profit factor with no losing trade says so instead of a number', () => {
  const m = { headline: { win_rate: ok(100, 25), profit_factor: { value: null, n: 25, min_n: 20, status: 'undefined' },
    expectancy_r: ok(1, 25), costs: ok(10, 25), cost_drag_pct: short(25) }, risk_adjusted: {} };
  assert.equal(figures(m)[1].value, 'no losing trade yet');
});

// ---------------------------------------------------------------- blotter
const NOW = at(28, 14, 6);
const live = { running: true, engine_ok: true, clock: { failure_to_launch_min: 45, entry_cutoff: '13:45', square_off: '15:20' },
  stale_price_seconds: 15, open_risk: 6200, daily_loss_limit: 20000, realized_today: -2100 };
const pos = (o) => ({ pos_id: 'A', symbol: 'NMDC', side: 'LONG', qty: 2100, entry_price: 71.42, last: 71.80, stop: 69.04,
  target: 76.18, unrealized: 798, r_now: 0.16, ts: NOW - 38 * 60, last_mark_ts: NOW - 2, ...o });

test('the rail places entry and price between stop and target', () => {
  const r = blotterRow(pos({}), live, NOW);
  assert.ok(Math.abs(r.rail.entry - ((71.42 - 69.04) / (76.18 - 69.04)) * 100) < 1e-9);
  assert.equal(r.rail.ahead, true);
  assert.equal(r.railText, 'Long: 39% of the way from stop 69.04 to target 76.18');
  assert.equal(r.tone, 'profit');
  const s = blotterRow(pos({ side: 'SHORT', entry_price: 402.1, last: 405.6, stop: 406.1, target: 394.1, unrealized: -4375, r_now: -0.88 }), live, NOW);
  assert.equal(s.rail.ahead, false);                    // a short moving toward its stop
  assert.ok(s.rail.last < s.rail.entry);
});

test('the time cell warns ten minutes before the launch check', () => {
  const r = blotterRow(pos({}), live, NOW);
  assert.equal(r.time, '38 min');
  assert.equal(r.timeAmber, true);
  assert.equal(r.timeNote, '7 min to the 45-min launch check');
  assert.equal(blotterRow(pos({ ts: NOW - 20 * 60 }), live, NOW).timeAmber, false);
  assert.equal(blotterRow(pos({ ts: NOW - 72 * 60 }), live, NOW).time, '1 h 12 min');
});

test('stale and frozen prices are amber and say why', () => {
  const stale = blotterRow(pos({ last_mark_ts: NOW - 23 }), live, NOW);
  assert.equal(stale.lastAmber, true);
  assert.equal(stale.priceNote, '23 s old');
  const frozen = blotterRow(pos({}), { ...live, engine_ok: false, failed_at: at(28, 11, 42) }, NOW);
  assert.equal(frozen.time, 'frozen');
  assert.equal(frozen.timeNote, 'since 11:42');
});

test('blotter header and footer', () => {
  const f = blotterFrame(live);
  assert.equal(f.risk, 'At the stops: ₹6,200 of the ₹20,000 daily loss limit');
  assert.equal(f.footer, `Today: ${MINUS}₹2,100 realised against a ${MINUS}₹20,000 limit; new entries until 13:45, square-off at 15:20.`);
  assert.deepEqual(blotterFrame({ running: false }), { risk: '', footer: '' });
});

// ---------------------------------------------------------------- journal
const trade = (o) => ({
  pos_id: 'P1', symbol: 'BHEL', side: 'LONG', qty: 2564, entry_price: 228.22, exit_price: 231.73, stop: 226.27, target: 231.73,
  reason: 'TARGET', touch_check: 'bars', flags: [], gross: 9010, net: 8949, r_net: 1.79, r_gross: 1.8, mae_r: -0.26, mfe_r: 2.1,
  risk_amount: 4999.8, hold_min: 82, opened_ts: at(24, 12, 23), closed_ts: at(24, 13, 45), config_version: 1,
  costs: { brokerage: 40, stt: 9.02, exchange: 4.3, sebi: 0.21, stamp: 2.58, gst: 5.37, total: 61.48 },
  settings: { capital: 1000000, risk_per_trade_pct: 0.5, slippage_pct: 0.03 },
  context: { composite: -0.65, regime: 'LUNCH_CHOP', verdict: 'CONFIRM', rationale: 'Fade of a failed VWAP reclaim.' }, ...o,
});

test('journal rows, filters and their options', () => {
  const ts = [trade({}), trade({ pos_id: 'P2', symbol: 'IDEA', side: 'SHORT', reason: 'STOP', closed_ts: at(25, 10, 0), context: { regime: 'TREND_EXPANSION' } })];
  assert.deepEqual(filterTrades(ts).map((t) => t.pos_id), ['P2', 'P1']);           // newest first
  assert.deepEqual(filterTrades(ts, { symbol: 'bh' }).map((t) => t.pos_id), ['P1']);
  assert.deepEqual(filterTrades(ts, { exit: 'STOP' }).map((t) => t.pos_id), ['P2']);
  assert.deepEqual(filterTrades(ts, { regime: 'LUNCH_CHOP', side: 'SHORT' }), []);
  assert.deepEqual(filterOptions(ts), { side: ['LONG', 'SHORT'], exit: ['STOP', 'TARGET'], regime: ['LUNCH_CHOP', 'TREND_EXPANSION'] });
  const j = journalRow(ts[0]);
  assert.deepEqual([j.when, j.side, j.net, j.r, j.exit, j.regime, j.score], ['24 Sep, 13:45', 'Long', '+₹8,949', '+1.79 R', 'Target', 'Lunch chop', '0.65']);
});

test('the story says why it entered, how it was sized, and why it exited', () => {
  const s = story(trade({}));
  assert.equal(s.entry, 'Score 0.65 · Lunch chop · AI CONFIRM');
  assert.equal(s.sizing, 'Risk ₹5,000 (0.5% of ₹10,00,000) ÷ stop distance ₹1.95 = 2,564 shares.');
  assert.equal(s.fillIn, '12:23  Buy 2,564 @ 228.22');
  assert.equal(s.fillOut, '13:45  Sell 2,564 @ 231.73');
  assert.equal(s.costTotal, '₹61.48');
  assert.equal(s.exitWhy, 'Reached the 231.73 target at 13:45, 1 h 22 min in.');
  assert.equal(s.version, 'Policy v1');
  const capped = story(trade({ risk_amount: 3000 }));
  assert.match(capped.sizing, /Cut to ₹3,000 at risk by the liquidity or sector cap\.$/);
  assert.equal(story(trade({ side: 'SHORT' })).fillIn, '12:23  Sell 2,564 @ 228.22');
});

test('exit reasons read plainly, with their caveats', () => {
  assert.equal(exitWhy(trade({ reason: 'SQUARE_OFF', closed_ts: at(24, 15, 20) })), 'Closed at the 15:20 square-off.');
  assert.match(exitWhy(trade({ reason: 'STOP', touch_check: 'ltp_only' })), /Checked against the last price, not bars/);
  assert.match(exitWhy(trade({ reason: 'SQUARE_OFF', flags: ['STALE_EXIT_PRICE'] })), /priced at the last known mark/);
  assert.match(exitWhy(trade({ reason: 'SOMETHING_NEW' })), /\(Something new\)\.$/);
});

test('the excursion bar is drawn from recorded points only', () => {
  const b = excursionBar(trade({}));
  assert.equal(b.stop.r, -1);
  assert.ok(Math.abs(b.target.r - (231.73 - 228.22) / (228.22 - 226.27)) < 1e-9);
  assert.ok(b.stop.at < b.worst.at && b.worst.at < b.entry.at && b.entry.at < b.exit.at && b.exit.at < b.best.at);
  assert.ok(b.best.at <= 100 && b.stop.at >= 0);
});

// ---------------------------------------------------------------- settings
const data = { settings: { capital: 1000000, slippage_pct: 0.03, risk_per_trade_pct: 0.4, max_daily_loss_pct: 2, max_cluster_risk_pct: 1, max_position_value_x: 1, max_open_value_x: 5 },
  sources: { capital: 'risk.yaml', slippage_pct: 'paper.yaml', risk_per_trade_pct: 'override', max_daily_loss_pct: 'risk.yaml', max_cluster_risk_pct: 'risk.yaml', max_position_value_x: 'risk.yaml', max_open_value_x: 'risk.yaml' },
  defaults: { capital: 1000000, slippage_pct: 0.03, risk_per_trade_pct: 0.5, max_daily_loss_pct: 2, max_cluster_risk_pct: 1, max_position_value_x: 1, max_open_value_x: 5 } };
const form = (o) => ({ capital: '1000000', slippage_pct: '0.03', risk_per_trade_pct: '0.4', max_daily_loss_pct: '2', max_cluster_risk_pct: '1', max_position_value_x: '1', max_open_value_x: '5', ...o });

test('the settings form sends only what changed', () => {
  assert.deepEqual(parseForm(form({}), data), { changes: {}, errors: {} });
  assert.deepEqual(parseForm(form({ capital: '5,00,000' }), data).changes, { capital: 500000 });
  assert.deepEqual(parseForm(form({ capital: '' }), data).errors, { capital: 'must be a number' });
  assert.deepEqual(parseForm(form({ slippage_pct: 'abc' }), data).errors, { slippage_pct: 'must be a number' });
  // "use default" sends null, and only for a field that is actually overridden
  assert.deepEqual(parseForm(form({}), data, new Set(['risk_per_trade_pct', 'capital'])).changes, { risk_per_trade_pct: null });
  assert.equal(errorText('capital', 'must be more than ₹0'), 'Capital must be more than ₹0');
  // the position-value caps are editable like the rest
  assert.deepEqual(parseForm(form({ max_open_value_x: '3' }), data).changes, { max_open_value_x: 3 });
  assert.equal(errorText('max_position_value_x', 'must be above 0'), 'Largest position must be above 0');
  assert.equal(settingValue('max_open_value_x', 5, inr, trim), '5× capital');
  assert.equal(settingValue('risk_per_trade_pct', 0.5, inr, trim), '0.5%');
});

test('whenChanged skips updates whose inputs are identical', () => {
  const calls = [];
  const u = whenChanged((s) => [s.a, s.b], (s) => calls.push(s.a));
  const a = {};
  u({ a, b: 1, now: 1 });
  u({ a, b: 1, now: 2 });                 // only an unrelated key moved
  u({ a: {}, b: 1 });
  assert.equal(calls.length, 2);
});

test('a mark a moment "ahead" of the page clock reads as fresh, not unknown', () => {
  const r = blotterRow(pos({ last_mark_ts: NOW + 0.4 }), live, NOW);
  assert.equal(r.lastAmber, false);
  assert.equal(r.priceNote, '');
});
