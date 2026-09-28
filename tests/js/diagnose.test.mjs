import { test } from 'node:test';
import assert from 'node:assert/strict';
import { QUESTIONS, answer, toBars } from '../../trading_copilot/static/performance/components/diagnose/questions.js';
import { scatterGeometry } from '../../trading_copilot/static/performance/components/diagnose/index.js';
import { legacyRow } from '../../trading_copilot/static/performance/components/legacy.js';
import { MINUS } from '../../trading_copilot/static/performance/core/format.js';

const ok = (value, n, min_n = 10) => ({ value, n, min_n, status: 'ok' });
const short = (n, min_n = 10) => ({ value: null, n, min_n, status: 'insufficient' });
const bucket = (key, n, er, net) => ({ key, label: key, n, expectancy_r: n >= 10 ? ok(er, n) : short(n), net: ok(net, n) });

function ctx(n = 48) {
  return {
    metrics: {
      min_n: { rates: 20, bucket: 10, calibration: 15, days: 20 },
      headline: { trades: ok(n, n), gross_pnl: ok(16541, n), costs: ok(3978, n), net_pnl: ok(12563, n),
        cost_drag_pct: ok(24.2, n), cost_per_trade_r: ok(0.02, n) },
    },
    breakdowns: {
      regime_side: [bucket('LONG|TREND_EXPANSION', 14, 0.6, 5000), bucket('SHORT|LUNCH_CHOP', 11, -0.4, -2600),
        bucket('LONG|OPENING_DRIVE', 12, 0.1, 400), bucket('SHORT|MEAN_REVERSION', 4, 1.9, 3000)],
      symbol: [bucket('IDEA', 12, -0.3, -4100), bucket('SAIL', 10, 0.5, 6200), bucket('BHEL', 11, -0.1, -900), bucket('INFY', 3, -2, -7000)],
      hour: [bucket('09:00', 12, 0.4, 1), bucket('10:00', 15, -0.2, 1), bucket('13:00', 2, 1.5, 1)],
    },
    diagnostics: {
      exits: { rows: [
        { key: 'TARGET', n: 14, net: ok(40000, 14), tunes: ['conviction.min_reward_risk'] },
        { key: 'STOP', n: 20, net: ok(-24000, 20), tunes: ['conviction.atr_mult_by_iv_regime'] },
        { key: 'GATEKEEPER_WHALE_FLIP', n: 10, net: ok(-3000, 10), tunes: ['gates.whale_flip_adv_frac'] },
        { key: 'SQUARE_OFF', n: 4, net: ok(-600, 4), tunes: ['horizon.square_off_ist'] },
      ] },
      excursions: { points: [{ mae_r: -0.2, mfe_r: 1.6, win: true }, { mae_r: -1.0, mfe_r: 0.7, win: false }],
        stops_after_half_r_up_pct: ok(43, 28, 20), wins_near_stop_pct: short(19, 20), avg_given_back_r: ok(0.41, 44, 20),
        tunes: ['gates.stop_proximity_pct'] },
      calibration: { buckets: [{ bucket: '0.2-0.3', n: 16, expectancy_r: ok(-0.1, 16, 15) }, { bucket: '0.3-0.4', n: 5, expectancy_r: short(5, 15) },
        { bucket: '0.5+', n: 18, expectancy_r: ok(0.5, 18, 15) }], monotonic: true, tunes: ['conviction.weights'] },
      ai: { by_verdict: [{ key: 'CONFIRM', n: 34, expectancy_r: ok(0.2, 34) }, { key: 'ADJUST', n: 14, expectancy_r: ok(-0.05, 14) }],
        vetoed: { n: 30, labelled: 26, avg_math_r: ok(-0.35, 26) }, taken: { n: 48, labelled: 40, avg_math_r: ok(0.18, 40) },
        tunes: ['paper.judge_model'] },
      costs: { per_trade: ok(82.9, 48, 20), flipped_to_loss: ok(3, 48, 0), tunes: ['paper.fill.slippage_pct'] },
    },
  };
}

test('below 20 trades every question says how far there is to go', () => {
  for (const [id] of QUESTIONS) {
    const a = answer(id, ctx(7));
    assert.equal(a.kind, 'notYet');
    assert.equal(a.text, 'Not enough data yet: 7 of 20 closed trades. The first answers appear at 20.');
  }
});

test('no data yet is loading, not an answer', () => {
  assert.equal(answer('stops', { metrics: null }).kind, 'loading');
});

test('conditions: ranked by R, small combinations amber and unranked', () => {
  const a = answer('conditions', ctx());
  assert.deepEqual(a.bars.map((b) => [b.label, b.text, b.small]), [
    ['Longs in trend expansion', '+0.60 R', false],
    ['Longs in opening drive', '+0.10 R', false],
    ['Shorts in lunch chop', `${MINUS}0.40 R`, false],
    ['Shorts in mean reversion', 'not yet (4 of 10)', true],
  ]);
  assert.equal(a.finding, `Longs in trend expansion average +0.60 R across 14 trades; shorts in lunch chop lose ${MINUS}0.40 R across 11.`
    + ' Combinations with fewer than 10 trades are shown in amber and not ranked.');
  assert.ok(!a.finding.includes('1.90'));            // the small bucket's number is never quoted
});

test('stocks: sums shown, but only big-enough stocks are named', () => {
  const a = answer('stocks', ctx());
  assert.equal(a.bars.find((b) => b.label === 'INFY').small, true);
  assert.ok(a.finding.startsWith(`IDEA and BHEL cost ₹5,000 together across 23 trades. SAIL made the most, +₹6,200 across 10.`));
  assert.ok(!a.finding.includes('INFY'));
  assert.deepEqual(a.tunes, ['watchlist_policy.core', 'watchlist_policy.min_adv_crore']);
});

test('exits: planned stops and the worst unplanned rule, with their knobs', () => {
  const a = answer('exits', ctx());
  assert.ok(a.finding.startsWith(`Planned stops cost ${MINUS}₹24,000 across 20 trades. Whale flip exits are the largest unplanned cost, ${MINUS}₹3,000 across 10.`));
  assert.deepEqual(a.tunes, ['conviction.atr_mult_by_iv_regime', 'gates.whale_flip_adv_frac']);
});

test('stops: states only the gated rates', () => {
  const a = answer('stops', ctx());
  assert.equal(a.kind, 'scatter');
  assert.equal(a.points.length, 2);
  assert.ok(a.finding.startsWith('43% of the 28 trades that hit their stop were up +0.50 R or more first.'));
  assert.ok(!/winners came within/.test(a.finding));   // 19 winners < 20: not stated
  assert.match(a.finding, /gave back 0\.41 R/);
});

test('score: compares the extreme buckets that qualify', () => {
  const a = answer('score', ctx());
  assert.deepEqual(a.bars.map((b) => b.label), ['Score 0.2-0.3', 'Score 0.3-0.4', 'Score 0.5+']);   // in score order
  assert.ok(a.finding.startsWith(`Trades scored 0.5+ average +0.50 R across 18; those scored 0.2-0.3 average ${MINUS}0.10 R across 16. Win rate rises`));
});

test('AI: vetoes against trades, labelled as a counterfactual', () => {
  const a = answer('ai', ctx());
  assert.ok(a.finding.startsWith(`Signals the AI turned down would have averaged ${MINUS}0.35 R across 26; those it took averaged +0.18 R across 40, scored the same way. So far its vetoes have avoided the weaker trades.`));
  assert.match(a.finding, /counterfactual/);
  const c = ctx();
  c.diagnostics.ai.vetoed.avg_math_r = short(6);
  assert.match(answer('ai', c).finding, /comparing vetoes with trades needs 10 scored on each side/);
});

test('timing and costs', () => {
  assert.ok(answer('timing', ctx()).finding.startsWith(`Entries between 09:00–10:00 average +0.40 R across 12 trades; entries between 10:00–11:00 average ${MINUS}0.20 R across 15.`));
  const c = answer('costs', ctx());
  assert.deepEqual(c.bars.map((b) => b.text), ['+₹16,541', `${MINUS}₹3,978`, '+₹12,563']);
  assert.equal(c.finding, 'Costs took ₹3,978 across 48 trades, 24.2% of the gross winnings. The average trade paid ₹83 (0.02 R). 3 trades were a gross win but a net loss.');
});

test('did my last change help: compares the two latest versions that qualify', () => {
  const c = ctx();
  c.breakdowns.config_version = [bucket(1, 30, -0.1, -900), bucket(2, 18, 0.25, 2100)];
  const a = answer('versions', c);
  assert.deepEqual(a.bars.map((b) => b.label), ['Policy v1', 'Policy v2']);
  assert.equal(a.finding, `Policy v2 averages +0.25 R per trade across 18, against ${MINUS}0.10 R across 30 for v1: better by 0.35 R.`);
  c.breakdowns.config_version = [bucket(1, 48, 0.1, 100)];
  assert.match(answer('versions', c).finding, /^Only one policy version has traded in this range/);
});

test('bars scale to the largest qualifying value, not a small bucket', () => {
  const bars = toBars([{ key: 'a', n: 12, v: 1 }, { key: 'b', n: 12, v: -0.5 }, { key: 'c', n: 2, v: 9 }],
    { value: (x) => x.v, fmt: String, label: (x) => x.key, min: 10, isFact: true });
  assert.deepEqual(bars.map((b) => [b.key, b.share, b.negative]), [['a', 48, false], ['b', 24, true], ['c', 48, false]]);
});

test('scatter geometry puts the stop line at −1 R', () => {
  const g = scatterGeometry([{ mae_r: -1, mfe_r: 0.5, win: false }, { mae_r: -0.2, mfe_r: 2, win: true }], 600, 300);
  assert.equal(g.n, 2);
  assert.ok(Math.abs(g.stopX - ((-1 - -1.35) / (0.05 - -1.35)) * 600) < 1e-9);
  assert.match(g.win, /^M/);
  assert.match(g.loss, /^M/);
});

test('legacy rows read the mock-platform record as it is', () => {
  const t1 = { trade_id: 'T1', symbol: 'SAIL', direction: 'Long', status: 'CLOSED', realized_pnl: 59.99,
    entry: { timestamp: 1781159018, executed_price: 180.81, executed_quantity: 300, llm_confidence: 7, llm_reason: 'x' },
    exits: [{ executed_price: 181.01 }] };
  const row = legacyRow(t1);
  assert.deepEqual([row.when, row.qty, row.px, row.pnl, row.confidence, row.tone], ['11 Jun, 11:53', '300', '180.81 → 181.01', '+₹60', '7/10', 'profit']);
  // never closed on the mock platform: no result, rather than a ₹0 one
  const open = legacyRow({ ...t1, status: 'OPEN', realized_pnl: 0, exits: [] });
  assert.deepEqual([open.px, open.pnl, open.tone], ['180.81 → not closed', '—', 'plain']);
  assert.equal(legacyRow({ trade_id: 'T2' }).px, '— → not closed');
});
