// Manual positions: the ones the operator holds outside the paper engine.
// Kept in this browser (localStorage 'user_positions', the old page's key and
// shape) and mirrored to the reasoning engine, so the AI manages them.
// Paper positions are not here: they live in the paper engine.

import { request } from './api.js';

const KEY = 'user_positions';

/** 'NSE_EQ|SAIL-EQ' -> 'SAIL' (matches the backend's _normalize_symbol). */
export const normalizeSymbol = (s) => String(s || '').split('|').pop().split('-')[0];

function read() {
  try {
    const raw = JSON.parse(globalThis.localStorage.getItem(KEY) || '{}');
    return raw && typeof raw === 'object' ? raw : {};
  } catch { return {}; }
}
function write(all) {
  try { globalThis.localStorage.setItem(KEY, JSON.stringify(all)); } catch { /* storage blocked */ }
}

export const loadPositions = () => read();
export const positionFor = (symbol) => read()[normalizeSymbol(symbol)] || null;

const num = (v) => {
  const n = typeof v === 'number' ? v : parseFloat(String(v ?? '').replace(/,/g, ''));
  return Number.isFinite(n) ? n : NaN;
};

/**
 * The position form -> {ok, position} or {ok: false, error}. Pure.
 * Quantity and entry are required and positive; target and stop are optional.
 */
export function parsePositionForm(f, { now = Date.now() / 1000, existing = null } = {}) {
  const qty = num(f.quantity);
  const entry = num(f.entry_price);
  if (!(qty > 0)) return { ok: false, error: 'Enter a quantity above zero.' };
  if (!(entry > 0)) return { ok: false, error: 'Enter the entry price.' };
  const target = num(f.target);
  const stop = num(f.stoploss);
  const direction = f.direction === 'Short' ? 'Short' : 'Long';
  if (Number.isFinite(stop) && stop > 0 && (direction === 'Long' ? stop >= entry : stop <= entry)) {
    return { ok: false, error: direction === 'Long' ? 'A long’s stop sits below the entry.' : 'A short’s stop sits above the entry.' };
  }
  return {
    ok: true,
    position: {
      mode: f.mode === 'Delivery' ? 'Delivery' : 'Intraday',
      direction, quantity: qty, entry_price: entry,
      target: Number.isFinite(target) && target > 0 ? target : null,
      stoploss: Number.isFinite(stop) && stop > 0 ? stop : null,
      entry_timestamp: existing && existing.entry_timestamp ? existing.entry_timestamp : Math.floor(now),
    },
  };
}

/** Save (or with null, clear) a manual position here and on the server. */
export async function savePosition(symbol, position) {
  const sym = normalizeSymbol(symbol);
  const all = read();
  if (position) all[sym] = position; else delete all[sym];
  write(all);
  return request('/api/reasoning/position/save', { method: 'POST', body: { symbol: sym, user_position: position || null } });
}

/** On load: normalise stored keys and resend them all (the server forgets on restart). */
export async function syncAll() {
  const all = {};
  for (const [k, v] of Object.entries(read())) all[normalizeSymbol(k)] = v;
  write(all);
  if (!Object.keys(all).length) return { ok: true };
  return request('/api/reasoning/position/sync_all', { method: 'POST', body: { positions: all } });
}
