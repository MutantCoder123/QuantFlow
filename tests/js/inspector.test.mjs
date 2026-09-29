// The Inspector's pure parts, and the shared modules it brought in.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { copyText, newsOf, paperPositionFor, paperWords, parseIntent, parseReport, payloadOf, planOf, quote,
  stateStamps, stockOf, waterfall } from '../../trading_copilot/static/inspector/model.js';
import { candleGeometry, niceStep } from '../../trading_copilot/static/shared/charts/candles.js';
import { normalizeSymbol, parsePositionForm } from '../../trading_copilot/static/shared/positions.js';
import { DEFAULT_PROMPT, isDefault, migrate } from '../../trading_copilot/static/shared/prompt.js';
import { rejectionLabel, statusLabel } from '../../trading_copilot/static/shared/labels.js';

const live = {
  global_state: { SAIL: { symbol: 'SAIL', ltp: 184.3, prev_close: 186, structured_payload: { math_setup: {} } } },
  paper: { open: [{ symbol: 'MCX', side: 'LONG', r_now: 0.5512 }] },
};

test('a stock and its paper position are found by any spelling of the symbol', () => {
  assert.equal(stockOf(live, 'NSE_EQ|SAIL-EQ').ltp, 184.3);
  assert.equal(stockOf(live, 'IDEA'), null);
  assert.equal(paperWords(paperPositionFor(live, 'mcx')), 'Paper long · +0.55 R');
  assert.equal(paperPositionFor(live, 'SAIL'), null);
  const q = quote(live.global_state.SAIL);
  assert.equal(q.tone, 'loss');
  assert.ok(Math.abs(q.change - (-0.914)) < 0.001);
  assert.deepEqual(quote({ ltp: 0 }), { ltp: null, prev: null, change: null, tone: 'flat' });
});

test('reports: JSON cards, plain notes, and nothing yet', () => {
  assert.equal(parseReport('No report generated yet.'), null);
  assert.equal(parseReport(null), null);
  assert.deepEqual(parseReport('Error generating report: timeout'), { text: 'Error generating report: timeout' });
  assert.equal(parseReport('```json\n{"Action": "Long"}\n```').Action, 'Long');
});

test('the plan reads the gatekeeper and AI cards in words', () => {
  const ai = planOf(parseReport(JSON.stringify({ Action: 'Long', Reason: 'Buyers absorbed twice.', Entry_Target_Price: 100,
    Stoploss: 98, Exit_Target_Price: 104, Status_Tag: 'LLM_ANALYZED', Qty: 250, Risk_Amount: 500, Risk_Rejection: null,
    Confidence_Score: 0, Edge_Status: null, Reward_Risk: null, Generated_Time: '12:31 pm' })));
  assert.equal(ai.action, 'Long');
  assert.deepEqual(ai.stamp, { text: 'AI reviewed', tone: 'ok' });
  assert.equal(ai.rr, 2);
  assert.equal(ai.qty, 250);
  assert.equal(ai.edge, 'Unmeasured');
  assert.equal(ai.reason, 'Buyers absorbed twice.');
  const rejected = planOf({ Action: 'SHORT', Reason: 'INSUFFICIENT_REWARD_RISK', math_rejection: 'INSUFFICIENT_REWARD_RISK',
    Entry_Target_Price: 0, Stoploss: 0 });
  assert.deepEqual(rejected.stamp, { text: 'Reward too small for the risk', tone: 'off' });
  assert.equal(rejected.reason, '');
  assert.equal(rejected.entry, null);
  const blocked = planOf({ Action: 'Wait', Status_Tag: 'RANK_GATED', Risk_Rejection: 'CLUSTER_LIMIT_POWER_CAPGOODS' });
  assert.equal(blocked.stamp.text, 'Below the top-5 cut');
  assert.equal(blocked.riskRejection, 'Sector limit (power capgoods)');
  assert.equal(planOf({ text: 'Error' }).note, 'Error');
});

test('rejection and status words, including the codes that carry a value', () => {
  assert.equal(rejectionLabel('STALE_DATA_20s'), 'Price 20 s old');
  assert.equal(rejectionLabel('ENTRY_CUTOFF_1345'), 'After the 13:45 entry cutoff');
  assert.equal(rejectionLabel('REGIME_DAMPENED_LUNCH_CHOP'), 'Held back in lunch chop');
  assert.equal(rejectionLabel('SIZE_ROUNDS_TO_ZERO'), 'Rounds to zero shares');
  assert.equal(rejectionLabel('SOMETHING_NEW'), 'Something new');
  assert.equal(statusLabel('PENDING_LLM'), 'AI reviewing');
  assert.equal(statusLabel('whatever'), '');
});

test('the score waterfall walks from zero to the composite', () => {
  const w = waterfall({ composite_score: 0.09, directional_bias: 'NEUTRAL', setup_rejected: true, rejection_reason: 'NEUTRAL_CONVICTION',
    contributions: { micro: { contribution: -0.188 }, struct: { contribution: 0.196 }, deriv: { contribution: 0.078 },
      catalyst: { contribution: 0, dead: true } } });
  assert.deepEqual(w.rows.map((r) => r.name), ['Order flow', 'Structure', 'Options', 'News']);
  assert.deepEqual(w.rows.map((r) => r.text), ['−0.19', '+0.20', '+0.08', 'no input']);
  assert.equal(w.rows[0].tone, 'loss');
  assert.equal(w.rows[3].tone, 'amber');
  // range is -0.188 (after order flow) .. 0.09 (the total); zero sits at 0.188 / 0.278 = 67.6 %
  assert.ok(Math.abs(w.zero - 67.63) < 0.05);
  assert.ok(Math.abs(w.rows[0].left) < 1e-9);
  assert.equal(w.total.text, '0.09');
  assert.equal(w.rejection, 'No clear direction');
  assert.equal(waterfall({}), null);
  assert.equal(waterfall(null), null);
});

test('state stamps say what the stock is doing, and only what is known', () => {
  const sp = {
    '1_live_microstructure': { order_book_imbalance_state: { state: 'MODERATE_BID' }, flow_divergence_state: { state: 'EQUILIBRIUM_CHOP' },
      session_cost_basis_state: { state: 'ELEVATED_PREMIUM' }, volume_regime: { state: 'NORMAL_DRIFT' } },
    '3_local_structural_edge_20d': { structural_proximity_state: { state: 'TEST_IMMINENT', nearest_level: 'rolling_20d_value_area_low' } },
    market_regime: { current_regime: 'TRANSITIONAL_DRIFT' },
  };
  assert.deepEqual(stateStamps(sp), ['Buyers lead the book', 'Above VWAP', 'Testing the 20-day value area low', 'Transitional drift']);
  assert.deepEqual(stateStamps(null), []);
});

test('news, the payload with a manual position, and the copy-for-another-AI text', () => {
  const s = { latest_catalyst: { raw_news: [{ headline: 'Wins an order', summary: 'Rail supply.' }, { headline: '' }] } };
  assert.deepEqual(newsOf(s), [{ headline: 'Wins an order', summary: 'Rail supply.' }]);
  assert.deepEqual(newsOf(s, { raw_news: [] }), []);
  const stock = { structured_payload: { user_context: {}, ltp: 10 } };
  const manual = { direction: 'Long', mode: 'Intraday', quantity: 100, entry_price: 9.5 };
  const p = payloadOf(stock, manual);
  assert.deepEqual(p.user_context.position, manual);
  assert.equal(stock.structured_payload.user_context.position, undefined);   // not mutated
  const intent = parseIntent({ action: 'Buy Limit', type: 'Intraday', quantity: '50', price: 'x', advice: '' });
  assert.deepEqual(intent, { action: 'Buy Limit', type: 'Intraday', quantity: 50, price: 0, advice: '' });
  assert.equal(parseIntent({ action: 'None' }), null);
  const text = copyText({ prompt: 'P', payload: { a: 1 }, manual, intent });
  assert.ok(text.startsWith('My Status: Holding Intraday, 100 qty entered at 9.5.\n\nProposed Trade Intent: Buy Limit (Intraday) | Qty: 50 | Price: 0\nAdvice/Note: None\n\nSYSTEM INSTRUCTION:\nP\n\nDATA PAYLOAD:\n{'));
});

test('candles: price range, levels, volume and delta', () => {
  const bars = [
    { t: '09:15', ts: 1, o: 100, h: 102, l: 99, c: 101, v: 100, vwap: 100.5, cvd: -50 },
    { t: '09:20', ts: 2, o: 101, h: 103, l: 100, c: 100.5, v: 300, vwap: 101, cvd: 20 },
  ];
  const g = candleGeometry(bars, { width: 200, height: 100,
    levels: [{ key: 'stop', price: 95, keep: true }, { key: 'poc', price: 150 }, { key: 'poc', price: 101 }] });
  assert.ok(g.lo < 95 && g.hi > 103);                     // the kept stop widens the range
  assert.deepEqual(g.lines.map((l) => l.price), [95, 101]);  // a far level is not drawn
  assert.equal(g.candles.length, 2);
  assert.equal(g.candles[0].up, true);
  assert.equal(g.candles[1].up, false);
  assert.equal(Number(g.volume[1].h), 40);                 // the biggest bar fills the strip
  assert.ok(g.vwap.startsWith('M50 '));
  assert.equal(g.delta.rising, true);
  assert.equal(candleGeometry([], { width: 1, height: 1 }), null);
  assert.equal(candleGeometry([{ ...bars[0], cvd: null }], { width: 100, height: 50 }).delta, null);
  assert.equal(niceStep(10), 2);       // 2.5 per tick -> the nearest nice step
  assert.equal(niceStep(22), 5);       // 5.5 per tick
  assert.equal(niceStep(30), 10);      // 7.5 per tick
  assert.equal(niceStep(420), 100);
});

test('the position form: required, positive, stop on the right side', () => {
  assert.equal(normalizeSymbol('NSE_EQ|SAIL-EQ'), 'SAIL');
  assert.deepEqual(parsePositionForm({ quantity: '0', entry_price: '10' }), { ok: false, error: 'Enter a quantity above zero.' });
  assert.equal(parsePositionForm({ quantity: '5', entry_price: '' }).error, 'Enter the entry price.');
  assert.equal(parsePositionForm({ quantity: '5', entry_price: '10', stoploss: '11', direction: 'Long' }).error, 'A long’s stop sits below the entry.');
  const ok = parsePositionForm({ quantity: '1,000', entry_price: '10.5', stoploss: '10', target: '', direction: 'Long', mode: 'Delivery' },
    { now: 1000.7 });
  assert.deepEqual(ok, { ok: true, position: { mode: 'Delivery', direction: 'Long', quantity: 1000, entry_price: 10.5,
    target: null, stoploss: 10, entry_timestamp: 1000 } });
  assert.equal(parsePositionForm({ quantity: '1', entry_price: '5' }, { existing: { entry_timestamp: 7 } }).position.entry_timestamp, 7);
});

test('the stored prompt migrates off the 5-year block and keeps other edits', () => {
  assert.equal(migrate(null), DEFAULT_PROMPT);
  assert.equal(migrate('old ===ADVICE_SPLIT=== format'), DEFAULT_PROMPT);
  const edited = 'MY NOTE\n3. BLOCK 3: MACRO STATISTICAL EDGE (THE CONCRETE WALLS)\n   - Measure LTP against `structural_liquidity.volume_poc_price`. This is an absolute multi-year liquidity wall. Never short directly on top of a 5-year POC floor, and never long directly under a major Value Area High rejection.\n   - Cross-check `regime_confluence.alpha_vs_nifty_5y`. If alpha is highly negative, the stock has persistent secular weakness. Short setups require less volume conviction than long setups.';
  const out = migrate(edited);
  assert.ok(out.startsWith('MY NOTE\n3. BLOCK 3: LOCAL STRUCTURAL EDGE (20-DAY)'));
  assert.ok(!out.includes('5-year'));
  assert.ok(isDefault(`  ${DEFAULT_PROMPT}\n`));
});
