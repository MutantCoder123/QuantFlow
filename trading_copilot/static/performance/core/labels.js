// Engine codes -> the words a trader uses. One place, so a reason reads the
// same in the tape, the blotter, the journal and the exports' viewers.

export const EXIT = {
  TARGET: 'Target',
  STOP: 'Stop',
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
