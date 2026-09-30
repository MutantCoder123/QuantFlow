// Engine codes -> the words a trader uses. One place, so a reason reads the
// same in the tape, the blotter, the journal and the exports' viewers.

export const EXIT = {
  TARGET: 'Target',
  STOP: 'Stop',
  TRAIL_STOP: 'Trailed stop',
  GATEKEEPER_STOP_PROXIMITY: 'Stop proximity',
  GATEKEEPER_WHALE_FLIP: 'Whale flip',
  GATEKEEPER_UNSPECIFIED: 'Gatekeeper exit',
  LLM_CLOSE: 'AI close',
  LLM_REVERSE: 'AI reverse',
  SQUARE_OFF: 'Square-off',
  RECOVERED_STALE: 'Closed after restart',
};

// planned exits read as outcomes (jade/coral); rule exits stay neutral
export function exitTone(reason) {
  if (reason === 'TARGET') return 'profit';
  if (reason === 'STOP') return 'loss';
  return 'plain';
}

export const exitLabel = (reason) => EXIT[reason] || (reason ? sentenceCase(reason) : 'Unknown');

export function sentenceCase(code) {
  if (!code) return 'Unknown';
  const s = String(code).replace(/_/g, ' ').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export const regimeLabel = (r) => (r ? sentenceCase(r) : 'Unknown');
export const sideLabel = (s) => (s === 'LONG' ? 'Long' : s === 'SHORT' ? 'Short' : 'Unknown');

// The AI judge's verdicts and directives (reasoning_engine execution_ticket).
export const VERDICT = { CONFIRM: 'Confirm', ADJUST: 'Adjust', DEFER: 'Defer', ABORT: 'Reject' };
export const DIRECTIVE = {
  EXECUTE_LONG: 'Long', EXECUTE_SHORT: 'Short', PASS: 'Pass', CLOSE_EXISTING: 'Close the position',
  REVERSE_POSITION: 'Reverse the position', NONE_NO_POSITION: 'Nothing to close',
};
export const verdictLabel = (v) => VERDICT[v] || sentenceCase(v);
export const directiveLabel = (d) => DIRECTIVE[d] || sentenceCase(d);
export function verdictTone(v) {
  if (v === 'CONFIRM' || v === 'ADJUST') return 'ok';
  if (v === 'ABORT') return 'bad';
  return 'wait';
}
export const directiveTone = (d) => (d === 'EXECUTE_LONG' ? 'profit' : d === 'EXECUTE_SHORT' ? 'loss' : 'plain');

// Why the math or a gate stopped an idea (math_rejection, Risk_Rejection,
// paper REJECT reasons, core/risk.py). Codes carrying a value (STALE_DATA_20s,
// ENTRY_CUTOFF_1345, CLUSTER_LIMIT_POWER_CAPGOODS, REGIME_DAMPENED_LUNCH_CHOP)
// are read by their prefix.
const REJECTION = {
  NEUTRAL_CONVICTION: 'No clear direction',
  INSUFFICIENT_REWARD_RISK: 'Reward too small for the risk',
  NO_GEOMETRY: 'No entry, stop and target',
  NO_FRESH_PRICE: 'No fresh price',
  PRICE_BEYOND_GEOMETRY: 'Price already past the plan',
  NO_ADV: 'No liquidity figure',
  ALREADY_OPEN: 'Already open',
  MANUAL_POSITION_HELD: 'You hold it manually',
  AFTER_ENTRY_CUTOFF: 'After the entry cutoff',
  ENGINE_PAUSED: 'Paper engine paused',
  NOT_AN_ENTRY: 'Not an entry',
  DEGENERATE_STOP: 'Stop at the entry',
  DAILY_LOSS_LIMIT: 'Daily loss limit',
  SIZE_ROUNDS_TO_ZERO: 'Rounds to zero shares',
  OPEN_VALUE_LIMIT: 'Open positions at their cap',
  SIZE_TOO_SMALL: 'Too small to be worth the charges',
  REVERSAL_COOLDOWN: 'Just stopped out the other way',
  SIZING_ERROR: 'Sizing failed',
  UNKNOWN: 'Unknown reason',
};
export function rejectionLabel(code) {
  if (!code) return '';
  const c = String(code);
  if (REJECTION[c]) return REJECTION[c];
  let m = c.match(/^STALE_DATA_(\d+)s$/);
  if (m) return `Price ${m[1]} s old`;
  m = c.match(/^ENTRY_CUTOFF_(\d{2})(\d{2})$/);
  if (m) return `After the ${m[1]}:${m[2]} entry cutoff`;
  m = c.match(/^CLUSTER_LIMIT_(.+)$/);
  if (m) return `Sector limit (${sentenceCase(m[1]).toLowerCase()})`;
  m = c.match(/^REGIME_DAMPENED_(.+)$/);
  if (m) return `Held back in ${sentenceCase(m[1]).toLowerCase()}`;
  return sentenceCase(c);
}

// latest_reports' Status_Tag: where the idea is with the AI.
const STATUS = {
  LLM_ANALYZED: ['AI reviewed', 'ok'],
  PENDING_LLM: ['AI reviewing', 'wait'],
  STABILIZING: ['Settling', 'wait'],
  RANK_GATED: ['Below the top-5 cut', 'off'],
  'REQUIRED LLM ANALYZE': ['AI review off', 'off'],
};
export const statusLabel = (tag) => (STATUS[tag] ? STATUS[tag][0] : '');
export const statusTone = (tag) => (STATUS[tag] ? STATUS[tag][1] : 'off');

export const SETTING = {
  capital: 'Capital',
  slippage_pct: 'Slippage per leg',
  risk_per_trade_pct: 'Risk per trade',
  max_daily_loss_pct: 'Daily loss limit',
  max_cluster_risk_pct: 'Per-sector limit',
  max_position_value_x: 'Largest position',
  max_open_value_x: 'All open positions',
};

/** A setting's value in words: ₹ for capital, "× capital" for value caps, % otherwise. */
export const settingValue = (key, v, inr, trim) => (
  key === 'capital' ? inr(v, { signed: false })
    : key.endsWith('_x') ? `${trim(v, 3)}× capital`
      : `${trim(v, 3)}%`);
