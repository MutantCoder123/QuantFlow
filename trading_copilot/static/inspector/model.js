// The Inspector's data, shaped for display. Pure: no DOM, no fetch.
// Every number here was computed by the engine; this only picks, labels and
// lays out.

import { DASH, MINUS, r as fmtR, tone } from '../shared/format.js';
import { rejectionLabel, sentenceCase, statusLabel, statusTone } from '../shared/labels.js';
import { normalizeSymbol } from '../shared/positions.js';

const known = (v) => typeof v === 'number' && Number.isFinite(v);
const price = (v) => (known(v) && v > 0 ? v : null);

/** The socket's state for one stock, or null. */
export function stockOf(live, symbol) {
  const gs = (live && live.global_state) || {};
  const want = normalizeSymbol(symbol).toUpperCase();
  for (const [k, s] of Object.entries(gs)) {
    if (normalizeSymbol((s && s.symbol) || k).toUpperCase() === want) return s;
  }
  return null;
}

export function paperPositionFor(live, symbol) {
  const open = (live && live.paper && live.paper.open) || [];
  const want = normalizeSymbol(symbol).toUpperCase();
  return open.find((p) => String(p.symbol).toUpperCase() === want) || null;
}

/** "Paper long · +0.55 R". */
export function paperWords(p) {
  if (!p) return '';
  return `Paper ${p.side === 'SHORT' ? 'short' : 'long'} · ${fmtR(p.r_now)}`;
}

/** The latest report text -> an object, or {text} when it isn't JSON (an error or a note). */
export function parseReport(text) {
  if (text === null || text === undefined) return null;
  let t = String(text).trim();
  if (!t || t === 'No report generated yet.') return null;
  t = t.replace(/^```(?:json)?/, '').replace(/```$/, '').trim();
  try {
    const d = JSON.parse(t);
    return d && typeof d === 'object' ? d : { text: String(text) };
  } catch {
    return { text: String(text) };
  }
}

const ACTION = { LONG: 'Long', SHORT: 'Short', WAIT: 'Wait', HOLD: 'Hold', CLOSE: 'Close', NEUTRAL: 'No direction' };
const actionWord = (a) => ACTION[String(a || '').toUpperCase()] || (a ? sentenceCase(a) : '');
const actionTone = (a) => {
  const u = String(a || '').toUpperCase();
  return u === 'LONG' ? 'profit' : u === 'SHORT' ? 'loss' : 'plain';
};

/** What the engine last said about the stock: the stamp, the plan and the reason. */
export function planOf(report) {
  if (!report) return null;
  if (report.text) return { note: report.text };
  const math = report.math_rejection && report.math_rejection !== 'UNKNOWN' ? report.math_rejection : null;
  const status = report.Status_Tag || '';
  let stamp;
  if (status) stamp = { text: statusLabel(status) || sentenceCase(status), tone: statusTone(status) };
  else if (math) stamp = { text: rejectionLabel(math), tone: 'off' };
  else stamp = { text: 'Watching', tone: 'off' };
  const entry = price(report.Entry_Target_Price);
  const stop = price(report.Stoploss);
  const target = price(report.Exit_Target_Price);
  let rr = known(report.Reward_Risk) ? report.Reward_Risk : null;
  if (rr === null && entry && stop && target && entry !== stop) rr = Math.abs(target - entry) / Math.abs(entry - stop);
  let reason = report.Reason || '';
  if (math && (reason === math || reason.startsWith('Math Engine Rejection'))) reason = '';
  const unmeasured = !report.Confidence_Score || String(report.Edge_Status || '').startsWith('unmeasured');
  return {
    action: actionWord(report.Action), actionTone: actionTone(report.Action), stamp,
    unstable: !!report.unstable, time: report.Generated_Time && report.Generated_Time !== 'UNKNOWN' ? report.Generated_Time : '',
    entry, stop, target, rr,
    qty: known(report.Qty) && report.Qty > 0 ? report.Qty : null,
    riskAmount: known(report.Risk_Amount) && report.Risk_Amount > 0 ? report.Risk_Amount : null,
    riskRejection: report.Risk_Rejection ? rejectionLabel(report.Risk_Rejection) : '',
    edge: unmeasured ? 'Unmeasured' : `${report.Confidence_Score} of 10`,
    exitRule: report.Exit_Rule ? rejectionLabel(report.Exit_Rule) : '',
    reason,
  };
}

/** The price line: last, change vs the previous close. */
export function quote(stock) {
  const ltp = price(stock && Number(stock.ltp));
  const prev = price(stock && Number(stock.prev_close));
  const change = ltp && prev ? (ltp - prev) / prev * 100 : null;
  return { ltp, prev, change, tone: tone(change) };
}

// Plain words for the states the scorer reads (semantic_tagger), a handful at most.
const WORDS = {
  order_book_imbalance_state: {
    EXTREME_BID_DOMINANCE: 'Buyers dominate the book', MODERATE_BID: 'Buyers lead the book',
    BALANCED: 'Book balanced', MODERATE_ASK: 'Sellers lead the book', EXTREME_ASK_DOMINANCE: 'Sellers dominate the book',
  },
  flow_divergence_state: {
    HIDDEN_BULLISH_ABSORPTION: 'Buyers absorbing the fall', HIDDEN_BEARISH_DISTRIBUTION: 'Sellers distributing into strength',
    MOMENTUM_CONFIRMED_BULLISH: 'Flow confirms the rise', MOMENTUM_CONFIRMED_BEARISH: 'Flow confirms the fall',
  },
  volume_regime: { TIME_ADJUSTED_SHOCK: 'Volume shock', ELEVATED_ACCUMULATION: 'Heavy volume', SUPPRESSED_FLOW: 'Thin volume' },
  session_cost_basis_state: {
    EXTREME_PREMIUM: 'Far above VWAP', ELEVATED_PREMIUM: 'Above VWAP', AT_EQUILIBRIUM: 'At VWAP',
    ELEVATED_DISCOUNT: 'Below VWAP', EXTREME_DISCOUNT: 'Far below VWAP',
  },
};
const stateOf = (v) => (v && typeof v === 'object' ? v.state : v);

/** A few stamps that say what the stock is doing, in words. */
export function stateStamps(sp) {
  if (!sp) return [];
  const b1 = sp['1_live_microstructure'] || {};
  const b3 = sp['3_local_structural_edge_20d'] || {};
  const out = [];
  for (const key of ['order_book_imbalance_state', 'flow_divergence_state', 'session_cost_basis_state', 'volume_regime']) {
    const w = WORDS[key][stateOf(b1[key])];
    if (w) out.push(w);
  }
  const prox = b3.structural_proximity_state || {};
  if (prox.state === 'TEST_IMMINENT' && prox.nearest_level) {
    const LEVEL = { rolling_20d_value_area_low: '20-day value area low', rolling_20d_value_area_high: '20-day value area high',
      rolling_20d_poc_price: '20-day POC' };
    out.push(`Testing the ${LEVEL[prox.nearest_level] || sentenceCase(prox.nearest_level).toLowerCase()}`);
  }
  const regime = (sp.market_regime || {}).current_regime;
  if (regime) out.push(sentenceCase(regime));
  return out;
}

const PART = { micro: 'Order flow', struct: 'Structure', deriv: 'Options', catalyst: 'News' };

/**
 * The composite score as its signed parts, each a bar from the running sum
 * before it to the running sum after (a waterfall), then the total.
 * left/width are percentages of the plot; zero is where 0 falls.
 */
export function waterfall(math) {
  const contrib = math && math.contributions;
  if (!contrib || !Object.keys(contrib).length) return null;
  const parts = Object.keys(PART).filter((k) => contrib[k]).map((k) => ({ key: k, c: contrib[k] }));
  const sums = [0];
  parts.forEach((p) => sums.push(sums[sums.length - 1] + (known(p.c.contribution) ? p.c.contribution : 0)));
  const total = known(math.composite_score) ? math.composite_score : sums[sums.length - 1];
  const lo = Math.min(0, ...sums, total);
  const hi = Math.max(0, ...sums, total);
  const span = hi - lo || 1;
  const at = (v) => ((v - lo) / span) * 100;
  const rows = parts.map((p, i) => {
    const v = known(p.c.contribution) ? p.c.contribution : 0;
    const a = sums[i];
    const b = sums[i + 1];
    return {
      name: PART[p.key], value: v, text: p.c.dead ? 'no input' : `${v < 0 ? MINUS : '+'}${Math.abs(v).toFixed(2)}`,
      tone: p.c.dead ? 'amber' : v < 0 ? 'loss' : 'profit', dead: !!p.c.dead,
      left: at(Math.min(a, b)), width: Math.max(0.6, Math.abs(at(b) - at(a))),
      signals: p.c.firing_signals || [],
    };
  });
  return {
    rows, zero: at(0),
    total: { name: 'Score', value: total, text: known(total) ? `${total < 0 ? MINUS : ''}${Math.abs(total).toFixed(2)}` : DASH,
      left: at(Math.min(0, total)), width: Math.max(0.6, Math.abs(at(total) - at(0))) },
    bias: math.directional_bias ? sentenceCase(math.directional_bias) : '',
    rejection: math.setup_rejected && math.rejection_reason ? rejectionLabel(math.rejection_reason) : '',
  };
}

/** News for the stock: the cached catalyst, newest fetch first. */
export function newsOf(stock, fetched) {
  const src = fetched || (stock && (stock.latest_catalyst
    || ((stock.structured_payload || {})['4_catalyst_engine']))) || {};
  return (src.raw_news || []).filter((n) => n && (n.headline || n.summary))
    .map((n) => ({ headline: n.headline || '', summary: n.summary || '' }));
}

/** The payload the AI sees, with the operator's manual position folded in (as the old modal did). */
export function payloadOf(stock, manual) {
  if (!stock) return null;
  const copy = JSON.parse(JSON.stringify(stock.structured_payload || stock));
  if (manual) {
    if (copy.user_context) copy.user_context.position = manual;
    else copy.user_position = manual;
  }
  return copy;
}

/** The text "Copy for another AI" puts on the clipboard: position, trade idea, the prompt, the payload. */
export function copyText({ prompt, payload, manual, intent }) {
  let head = '';
  if (manual) {
    head += `My Status: ${manual.direction === 'Long' ? 'Holding' : 'Short'} ${manual.mode}, ${manual.quantity} qty entered at ${manual.entry_price}.\n\n`;
  }
  if (intent) {
    head += `Proposed Trade Intent: ${intent.action} (${intent.type}) | Qty: ${intent.quantity} | Price: ${intent.price}\n`
      + `Advice/Note: ${intent.advice || 'None'}\n\n`;
  }
  return `${head}SYSTEM INSTRUCTION:\n${prompt}\n\nDATA PAYLOAD:\n${JSON.stringify(payload, null, 2)}`;
}

/** The "trade I'm considering" form -> an intent, or null when no action is chosen. */
export function parseIntent(f) {
  if (!f || !f.action || f.action === 'None') return null;
  const n = (v) => { const x = parseFloat(v); return Number.isFinite(x) ? x : 0; };
  return { action: f.action, type: f.type || 'Intraday', quantity: n(f.quantity), price: n(f.price), advice: f.advice || '' };
}

